"""Current UI titles, derived from authorized durable Yjs state without writes.

Canonical revision snapshots/receipts keep their original title and checksum.
This projection is for current UI metadata, not a replacement history snapshot.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

from .canonical_authority import AuthorityUnavailable
from .durable_transport import YJS_UPDATE_ENCODING

CONTRACT = "code_assist.notion.title_projection.v1"
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
BATCH_BYTES = 8 * 1024 * 1024
_cache: OrderedDict[str, str | None] = OrderedDict()
_cache_lock = threading.Lock()


class TitleProjectionChanged(RuntimeError):
    """The caller must collect its source metadata again before returning it."""


def retry_title_projection(function):
    @wraps(function)
    def current(*args, **kwargs):
        for _ in range(3):
            try:
                return function(*args, **kwargs)
            except TitleProjectionChanged:
                continue
        raise AuthorityUnavailable("document changed while reading its current title; retry")
    return current


def _materialize_batch(documents: list[list[bytes]]) -> list[str | None]:
    node = shutil.which("node")
    if not node:
        raise AuthorityUnavailable("the pinned Yjs title projection requires Node.js")
    request = {"contract": CONTRACT, "documents": [
        [base64.b64encode(frame).decode("ascii") for frame in frames]
        for frames in documents
    ]}
    try:
        process = subprocess.run(
            [node, "--max-old-space-size=256", str(Path(__file__).with_suffix(".cjs"))],
            input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if process.returncode:
            raise ValueError("title runtime failed")
        response = json.loads(process.stdout)
        titles = response["titles"]
        if (response.get("contract") != CONTRACT or not isinstance(titles, list)
                or len(titles) != len(documents)
                or any(value is not None and not isinstance(value, str) for value in titles)):
            raise ValueError("invalid title response")
        return titles
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        raise AuthorityUnavailable("current collaborative title is unavailable") from exc


def _materialize(documents: list[list[bytes]]) -> list[str | None]:
    """Cache only pure decoding results. Authorization is never cached."""
    keys = []
    missing = {}
    decoded = {}
    for frames in documents:
        digest = hashlib.sha256(CONTRACT.encode())
        for frame in frames:
            digest.update(len(frame).to_bytes(8, "big"))
            digest.update(frame)
        key = digest.hexdigest()
        keys.append(key)
        with _cache_lock:
            if key in _cache:
                decoded[key] = _cache[key]
                _cache.move_to_end(key)
            else:
                missing[key] = frames
    batch, size = [], 0

    def flush():
        nonlocal batch, size
        if not batch:
            return
        values = _materialize_batch([frames for _, frames in batch])
        for (key, _), value in zip(batch, values):
            decoded[key] = value
            if value is None or len(value) <= 16384:
                with _cache_lock:
                    _cache[key] = value
                    _cache.move_to_end(key)
                    while len(_cache) > 256:
                        _cache.popitem(last=False)
        batch, size = [], 0

    for key, frames in missing.items():
        amount = sum(map(len, frames))
        if amount > MAX_DOCUMENT_BYTES:
            raise AuthorityUnavailable("current title replay requires compaction")
        if batch and (size + amount > BATCH_BYTES or len(batch) >= 32):
            flush()
        batch.append((key, frames))
        size += amount
    flush()
    return [decoded[key] for key in keys]


def _generation(con, document_id):
    row = con.execute(
        "SELECT generation FROM workspace_collaboration_crdt_generations "
        "WHERE document_id=? AND state='current'", (document_id,),
    ).fetchone()
    if row is None:
        raise AuthorityUnavailable("current CRDT generation is unavailable")
    return int(row[0])


def _frames(con, document_id, generation):
    snapshot = con.execute(
        "SELECT s.* FROM workspace_collaboration_crdt_snapshots s "
        "JOIN workspace_collaboration_crdt_snapshot_bindings b ON b.snapshot_id=s.id "
        "WHERE b.document_id=? AND b.generation=? "
        "ORDER BY s.through_update_sequence DESC LIMIT 1", (document_id, generation),
    ).fetchone()
    after = 0 if snapshot is None else int(snapshot["through_update_sequence"])
    frames = []

    def checked(payload, checksum):
        value = bytes(payload)
        if "sha256:" + hashlib.sha256(value).hexdigest() != checksum:
            raise AuthorityUnavailable("current title replay checksum mismatch")
        frames.append(value)

    if snapshot is not None:
        checked(snapshot["snapshot"], snapshot["snapshot_sha256"])
    for row in con.execute(
        "SELECT u.payload,u.payload_sha256,u.payload_encoding FROM workspace_collaboration_crdt_updates u "
        "JOIN workspace_collaboration_crdt_update_bindings b ON b.update_id=u.id "
        "WHERE b.document_id=? AND b.generation=? AND u.update_sequence>? ORDER BY u.update_sequence",
        (document_id, generation, after),
    ):
        if row["payload_encoding"] != YJS_UPDATE_ENCODING:
            raise AuthorityUnavailable("unsupported current title encoding")
        checked(row["payload"], row["payload_sha256"])
    return frames


def read_current_titles(authority, actor: str, workspace_id: str, items: list[dict]) -> dict:
    """Read only the caller's bound page set; reject mixed or retired snapshots.

    Caller metadata and CRDT bytes must describe the same document sequence and
    access epochs. Materialization happens after the read transaction is closed.
    A second current permission/version check also covers cached decoder results.
    """
    from .iam_authority import CollaborativeIAMAuthority

    actor = authority._require_active(actor)
    sources = {}
    with authority._reader() as con:
        con.execute("BEGIN")
        workspace = authority._workspace(con, workspace_id)
        for item in items:
            document_id = item["id"]
            row = con.execute(
                "SELECT * FROM workspace_collaboration_documents WHERE id=? AND workspace_id=? AND archived=0",
                (document_id, workspace_id),
            ).fetchone()
            if row is None:
                continue
            role, _ = CollaborativeIAMAuthority._resolution_on_connection(con, actor, workspace_id, document_id)
            if role is None:
                continue
            expected = (int(row["document_sequence"]), int(workspace["security_epoch"]), int(row["access_epoch"]))
            for key, value in zip(("document_sequence", "workspace_epoch", "document_epoch"), expected):
                if key in item and int(item[key]) != value:
                    raise TitleProjectionChanged()
            generation = _generation(con, document_id)
            sources[document_id] = {
                "title": str(row["title"]), "generation": generation,
                "stamp": (*expected, int(row["row_version"]), generation),
                "frames": _frames(con, document_id, generation),
            }
    ids = [key for key, value in sources.items() if value["frames"]]
    titles = _materialize([sources[key]["frames"] for key in ids]) if ids else []
    for key, title in zip(ids, titles):
        # Explicit empty Y.Text is different from an absent title leaf.
        if title is not None:
            sources[key]["title"] = title
    authority._require_active(actor)
    result = {}
    with authority._reader() as con:
        con.execute("BEGIN")
        workspace = authority._workspace(con, workspace_id)
        for document_id, source in sources.items():
            row = con.execute(
                "SELECT * FROM workspace_collaboration_documents WHERE id=? AND workspace_id=? AND archived=0",
                (document_id, workspace_id),
            ).fetchone()
            role, _ = CollaborativeIAMAuthority._resolution_on_connection(con, actor, workspace_id, document_id)
            if row is None or role is None:
                continue
            stamp = (int(row["document_sequence"]), int(workspace["security_epoch"]), int(row["access_epoch"]),
                     int(row["row_version"]), _generation(con, document_id))
            if stamp != source["stamp"]:
                raise TitleProjectionChanged()
            result[document_id] = {
                "title": source["title"],
                "title_projection": {"contract": CONTRACT, "generation": stamp[-1],
                                     "document_sequence": stamp[0]},
            }
    return result
