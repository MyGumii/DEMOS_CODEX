"""Bounded, pure current-body decoder, called outside database transactions."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

from .canonical_authority import AuthorityUnavailable
from .title_projection import BATCH_BYTES, MAX_DOCUMENT_BYTES

CONTRACT = "code_assist.notion.search_projection.v1"


class SearchProjectionChanged(RuntimeError):
    """Source changed after preparation; discard and collect again."""


def _batch(documents, *, rich=False, identities=False):
    node = shutil.which("node")
    if not node:
        raise AuthorityUnavailable("current search content requires the pinned Node.js runtime")
    request = {"contract": CONTRACT, "documents": [
        [base64.b64encode(frame).decode("ascii") for frame in frames] for frames in documents]}
    if rich:
        request["include_rich_text"] = True
    if identities:
        request["include_block_identities"] = True
    try:
        process = subprocess.run(
            [node, "--max-old-space-size=256", str(Path(__file__).with_suffix(".cjs"))],
            input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if process.returncode:
            raise ValueError("search decoder failed")
        response = json.loads(process.stdout)
        values = response["documents"]
        if response.get("contract") != CONTRACT or not isinstance(values, list) or len(values) != len(documents):
            raise ValueError("invalid search projection response")
        for value in values:
            if (not isinstance(value, dict) or value.get("title") is not None and not isinstance(value["title"], str)
                    or value.get("blocks") is not None and not isinstance(value["blocks"], list)):
                raise ValueError("invalid search projection document")
            if rich and (not isinstance(value.get("rich_text"), dict)
                    or not isinstance(value["rich_text"].get("blocks"), dict)
                    or value["rich_text"].get("title") is not None and not isinstance(value["rich_text"]["title"], list)):
                raise ValueError("invalid rich projection document")
            if rich and set(value["rich_text"]["blocks"]) != {block["id"] for block in value["blocks"] or []}:
                raise ValueError("incomplete attributed block projection")
            if identities:
                identity_map, vector = value.get("block_identities"), value.get("state_vector")
                if (not isinstance(identity_map, dict) or not isinstance(vector, dict)
                        or set(identity_map) != {block["id"] for block in value["blocks"] or []}
                        or any(not isinstance(key, str) or not key.isdigit() or type(clock) is not int or clock < 0
                            for key, clock in vector.items())
                        or any(not isinstance(item, dict) or set(item) != {"client", "clock"}
                            or any(type(item[key]) is not int or item[key] < 0 for key in ("client", "clock"))
                            for item in identity_map.values())):
                    raise ValueError("invalid structural identity projection")
        return values
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        raise AuthorityUnavailable("current collaborative search content is unavailable") from exc


def materialize(documents, *, rich=False, identities=False):
    result, batch, size = [], [], 0
    for frames in documents:
        amount = sum(map(len, frames))
        if amount > MAX_DOCUMENT_BYTES:
            raise AuthorityUnavailable("current search replay requires compaction")
        if batch and (size + amount > BATCH_BYTES or len(batch) >= 32):
            result.extend(_batch(batch, rich=rich, identities=identities) if rich or identities else _batch(batch))
            batch, size = [], 0
        batch.append(frames)
        size += amount
    if batch:
        result.extend(_batch(batch, rich=rich, identities=identities) if rich or identities else _batch(batch))
    return result
