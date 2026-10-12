"""Run the marker-bound full Second Brain account/scope validation matrix.

This is an isolated preview verifier, not a production maintenance command.  It
uses the same HTTP publication, search, conversation, and turn-stream routes as
the browser.  Results are resumable and contain no password or session cookie.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5

import requests


_SCHEMA = "code-assist.full-second-brain-account-matrix"
_SCHEMA_VERSION = 1
_NAMESPACE = UUID("18ea963c-17cc-4c8a-8e4c-a8156dadfa6f")
_ORIGIN_HEADER = "Origin"
_REQUEST_HEADER = "X-Request-ID"
_OPERATION_HEADER = "X-Operation-ID"


class MatrixError(RuntimeError):
    """A stable validation failure."""


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MatrixError(f"{label} did not return a JSON object.")
    return value


def _items(value: Mapping[str, Any], label: str) -> list[dict[str, Any]]:
    rows = value.get("items")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MatrixError(f"{label} returned an invalid item page.")
    return rows


@dataclass(frozen=True, slots=True)
class PublishedItem:
    item_id: str
    space_id: str


class ApiSession:
    def __init__(self, base_url: str, user_id: str, password: str, batch: str):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.batch = batch
        self.session = requests.Session()
        response = self.session.post(
            self.base_url + "/api/code/auth/login",
            json={"password": password, "user_id": user_id},
            headers={_ORIGIN_HEADER: self.base_url},
            timeout=30,
        )
        self._require(response, (200,), "login")
        self.csrf = str(_mapping(response.json(), "login").get("csrf_token") or "")
        if not self.csrf:
            raise MatrixError(f"{user_id}: login returned no CSRF token.")

    def _headers(self, label: str, operation_id: str | None = None) -> dict[str, str]:
        headers = {
            _ORIGIN_HEADER: self.base_url,
            _REQUEST_HEADER: _uuid(f"{self.batch}:request:{self.user_id}:{label}"),
            "X-CSRF-Token": self.csrf,
        }
        if operation_id is not None:
            headers[_OPERATION_HEADER] = operation_id
            headers["Idempotency-Key"] = operation_id
        return headers

    def _require(
        self,
        response: requests.Response,
        expected: Iterable[int],
        label: str,
    ) -> requests.Response:
        allowed = tuple(expected)
        if response.status_code not in allowed:
            text = response.text[:2000]
            raise MatrixError(
                f"{self.user_id}: {label} returned HTTP {response.status_code}, "
                f"expected {allowed}: {text}"
            )
        return response

    def get(self, path: str, label: str) -> dict[str, Any]:
        response = self.session.get(
            self.base_url + path,
            headers=self._headers(label),
            timeout=60,
        )
        self._require(response, (200,), label)
        return _mapping(response.json(), label)

    def post_json(
        self,
        path: str,
        body: Mapping[str, object],
        label: str,
        *,
        expected: tuple[int, ...] = (200, 201),
        timeout: int = 60,
    ) -> dict[str, Any]:
        operation_id = body.get("operation_id")
        response = self.session.post(
            self.base_url + path,
            json=dict(body),
            headers=self._headers(
                label,
                str(operation_id) if isinstance(operation_id, str) else None,
            ),
            timeout=timeout,
        )
        self._require(response, expected, label)
        return _mapping(response.json(), label)

    def spaces(self, selected_space_id: str | None = None) -> list[dict[str, Any]]:
        suffix = "" if selected_space_id is None else f"?selected_space_id={selected_space_id}"
        return _items(self.get("/api/knowledge/v1/spaces" + suffix, "spaces"), "spaces")

    def personal_wikis(self) -> list[dict[str, Any]]:
        page = self.get(
            "/api/knowledge/v2/personal-wikis?lifecycle_status=active&limit=100",
            "personal-wikis",
        )
        return _items(page, "personal-wikis")

    def ensure_wiki(self, display_name: str) -> str:
        wikis = self.personal_wikis()
        existing = next((row for row in wikis if row.get("display_name") == display_name), None)
        if existing is not None:
            return str(existing["space_id"])
        default = next((row for row in wikis if row.get("is_default") is True), None)
        if default is None:
            raise MatrixError(f"{self.user_id}: no default personal Wiki exists.")
        operation_id = _uuid(f"{self.batch}:wiki-create:{self.user_id}")
        created = self.post_json(
            "/api/knowledge/v2/personal-wikis",
            {
                "display_name": display_name,
                "expected_row_version": default["row_version"],
                "operation_id": operation_id,
            },
            "wiki-create",
            expected=(201,),
        )
        return str(created["resource_id"])

    def upload(
        self,
        *,
        action: str,
        filename: str,
        markdown: str | None = None,
        raw: bytes | None = None,
        content_type: str | None = None,
        title: str,
        target_mode: str,
        personal_space_id: str | None = None,
        target: Mapping[str, object] | None = None,
    ) -> tuple[PublishedItem, ...]:
        if (markdown is None) == (raw is None):
            raise ValueError("Supply exactly one of markdown or raw upload content.")
        upload_bytes = markdown.encode("utf-8") if markdown is not None else raw
        assert upload_bytes is not None
        upload_type = content_type or (
            "text/markdown" if markdown is not None else "application/octet-stream"
        )
        preview_operation = _uuid(f"{self.batch}:preview:{self.user_id}:{action}")
        data: dict[str, str] = {
            "operation_id": preview_operation,
            "source_type": "upload",
            "title": title,
        }
        if target is not None:
            data["target"] = json.dumps(
                dict(target), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        response = self.session.post(
            self.base_url + "/api/knowledge/v1/publication-previews",
            data=data,
            files={"file": (filename, upload_bytes, upload_type)},
            headers=self._headers(f"preview:{action}", preview_operation),
            timeout=120,
        )
        self._require(response, (200, 202), f"preview:{action}")
        preview = _mapping(response.json(), f"preview:{action}")
        preview_id = str(preview.get("preview_id") or "")
        for poll in range(180):
            if preview.get("status") not in {"queued", "extracting"}:
                break
            time.sleep(1)
            preview = self.get(
                f"/api/knowledge/v1/publication-previews/{preview_id}",
                f"preview-status:{action}:{poll}",
            )
        if preview.get("status") not in {"ready", "consumed"}:
            raise MatrixError(
                f"{self.user_id}: preview {action} ended as {preview.get('status')}."
            )
        commit_operation = _uuid(f"{self.batch}:commit:{self.user_id}:{action}")
        body: dict[str, object] = {
            "operation_id": commit_operation,
            "preview_id": preview_id,
            "source_snapshot_sha256": preview["source_snapshot_sha256"],
            "target_mode": target_mode,
        }
        if personal_space_id is not None:
            body["personal_space_id"] = personal_space_id
        if target is not None:
            body["target"] = dict(target)
        committed = self.post_json(
            "/api/knowledge/v1/publications",
            body,
            f"commit:{action}",
            expected=(200, 201),
            timeout=120,
        )
        results = committed.get("results")
        if not isinstance(results, list) or not results:
            raise MatrixError(f"{self.user_id}: publication {action} returned no target.")
        return tuple(
            PublishedItem(item_id=str(row["item_id"]), space_id=str(row["space_id"]))
            for row in results
            if isinstance(row, dict)
        )

    def checkpoint(
        self,
        space_id: str,
        label: str,
        *,
        timeout_seconds: int = 60,
    ) -> dict[str, object]:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 3_600
        ):
            raise ValueError("timeout_seconds must be between 1 and 3600.")
        item_page = self.get(
            f"/api/knowledge/v1/spaces/{space_id}/items?limit=100",
            f"{label}:items",
        )
        graph = self.get(
            f"/api/knowledge/v1/spaces/{space_id}/graph",
            f"{label}:graph",
        )
        activity = self.get(
            f"/api/knowledge/v1/spaces/{space_id}/activity?limit=100",
            f"{label}:activity",
        )
        projection: dict[str, Any] = {}
        for projection_attempt in range(timeout_seconds):
            projection = self.get(
                f"/api/knowledge/v1/spaces/{space_id}/projection-status",
                f"{label}:projection:{projection_attempt}",
            )
            if (
                projection.get("status") == "ready"
                and projection.get("built_generation")
                == projection.get("desired_generation")
            ):
                break
            time.sleep(1)
        else:
            raise MatrixError(
                f"{self.user_id}: {label} projection did not become ready: "
                f"{projection}."
            )
        rows = _items(item_page, f"{label}:items")
        nodes = graph.get("nodes")
        edges = graph.get("edges")
        events = _items(activity, f"{label}:activity")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise MatrixError(f"{self.user_id}: {label} graph shape is invalid.")
        return {
            "activity_count": len(events),
            "checked_at": _now(),
            "graph_edge_count": len(edges),
            "graph_node_count": len(nodes),
            "index_item_count": len(rows),
            "projection": {
                key: projection.get(key)
                for key in ("built_generation", "desired_generation", "status")
            },
            "space_id": space_id,
        }

    def item_detail(self, space_id: str, item_id: str, label: str) -> dict[str, Any]:
        return self.get(
            f"/api/knowledge/v1/spaces/{space_id}/items/{item_id}",
            f"item:{label}",
        )

    def edit_item(
        self,
        *,
        action: str,
        space_id: str,
        item_id: str,
        markdown: str,
    ) -> dict[str, object]:
        detail = self.item_detail(space_id, item_id, f"{action}:before")
        item = _mapping(detail.get("item"), f"{action}:item")
        expected_revision = str(item["current_revision_id"])
        expected_row_version = int(item["row_version"])
        if str(detail.get("markdown") or "") == markdown:
            return {
                "base_revision_id": expected_revision,
                "new_revision_id": expected_revision,
                "new_row_version": expected_row_version,
                "status": "already_updated",
            }
        editor_session_id = _uuid(
            f"{self.batch}:editor-session:{self.user_id}:{action}"
        )
        raw_token = secrets.token_urlsafe(32)
        acquire_operation = _uuid(
            f"{self.batch}:lease-acquire:{self.user_id}:{action}"
        )
        acquire_headers = {
            **self._headers(f"lease-acquire:{action}", acquire_operation),
            "X-Knowledge-Lease-Token": raw_token,
        }
        acquired_response = self.session.post(
            self.base_url
            + f"/api/knowledge/v1/spaces/{space_id}/items/{item_id}/lease/acquire",
            json={
                "editor_session_id": editor_session_id,
                "operation_id": acquire_operation,
            },
            headers=acquire_headers,
            timeout=60,
        )
        self._require(acquired_response, (200, 201), f"lease-acquire:{action}")
        acquired = _mapping(acquired_response.json(), f"lease-acquire:{action}")
        lease_id = str(acquired["lease_id"])
        fencing_generation = int(acquired["fencing_generation"])
        update_operation = _uuid(f"{self.batch}:edit:{self.user_id}:{action}")
        update_headers = {
            **self._headers(f"edit:{action}", update_operation),
            "If-Match": f'"{expected_revision}"',
            "X-Knowledge-Fence": str(fencing_generation),
            "X-Knowledge-Lease-Token": raw_token,
        }
        updated_response = self.session.put(
            self.base_url + f"/api/knowledge/v1/spaces/{space_id}/items/{item_id}",
            json={
                "editor_session_id": editor_session_id,
                "expected_revision_id": expected_revision,
                "expected_row_version": expected_row_version,
                "lease_id": lease_id,
                "markdown": markdown,
                "operation_id": update_operation,
                "reason": "Clarify the exact relationship during the exhaustive localhost matrix.",
            },
            headers=update_headers,
            timeout=120,
        )
        self._require(updated_response, (200,), f"edit:{action}")
        updated = _mapping(updated_response.json(), f"edit:{action}")
        release_operation = _uuid(
            f"{self.batch}:lease-release:{self.user_id}:{action}"
        )
        release_headers = {
            **self._headers(f"lease-release:{action}", release_operation),
            "X-Knowledge-Fence": str(fencing_generation),
            "X-Knowledge-Lease-Token": raw_token,
        }
        released_response = self.session.post(
            self.base_url
            + f"/api/knowledge/v1/spaces/{space_id}/items/{item_id}/lease/release",
            json={
                "editor_session_id": editor_session_id,
                "lease_id": lease_id,
                "operation_id": release_operation,
            },
            headers=release_headers,
            timeout=60,
        )
        self._require(released_response, (200, 423), f"lease-release:{action}")
        release_status = "released"
        if released_response.status_code == 423:
            release_error = _mapping(
                released_response.json(), f"lease-release:{action}:error"
            ).get("error")
            if not isinstance(release_error, dict) or release_error.get("code") != "LEASE_LOST":
                raise MatrixError(
                    f"{self.user_id}: {action} lease ended with an unexpected 423."
                )
            release_status = "consumed_by_update"
        return {
            "base_revision_id": expected_revision,
            "new_revision_id": str(updated["revision_id"]),
            "new_row_version": int(updated["row_version"]),
            "release_status": release_status,
            "status": "updated",
        }

    def search(
        self,
        *,
        space_id: str,
        query: str,
        expected_markers: tuple[str, ...],
        expected_source_space_ids: set[str],
        label: str,
    ) -> dict[str, object]:
        page = self.post_json(
            f"/api/knowledge/v1/spaces/{space_id}/search",
            {"limit": 10, "query": query},
            f"search:{label}",
        )
        hits = _items(page, f"search:{label}")
        serialized = json.dumps(hits, ensure_ascii=False)
        matched_markers = tuple(
            marker for marker in expected_markers if marker in serialized
        )
        if not matched_markers:
            raise MatrixError(
                f"{self.user_id}: {label} search did not return any acceptable marker "
                f"from {expected_markers}."
            )
        matched_sources = {
            str(row.get("source_space_id"))
            for row in hits
            if any(
                marker in json.dumps(row, ensure_ascii=False)
                for marker in matched_markers
            )
        }
        if not matched_sources.intersection(expected_source_space_ids):
            raise MatrixError(
                f"{self.user_id}: {label} search returned the marker from the wrong source."
            )
        return {
            "hit_count": len(hits),
            "matched_markers": list(matched_markers),
            "matched_sources": sorted(matched_sources),
        }

    def turn(
        self,
        *,
        label: str,
        space_id: str,
        question: str,
        expected_marker_groups: tuple[tuple[str, ...], ...],
    ) -> dict[str, object]:
        # A terminal failed turn is immutable and intentionally replays when
        # its operation ID is reused.  A validation rerun must exercise a new
        # canonical conversation/turn rather than replaying an earlier
        # transient provider failure forever.
        attempt_identity = secrets.token_hex(16)
        command_identity = f"{label}:{question}:{attempt_identity}"
        conversation_operation = _uuid(
            f"{self.batch}:conversation:{self.user_id}:{command_identity}"
        )
        conversation = self.post_json(
            "/api/knowledge/v1/conversations",
            {
                "operation_id": conversation_operation,
                "space_id": space_id,
                "title": f"Matrix {label}",
            },
            f"conversation:{label}",
            expected=(200, 201),
        )
        conversation_id = str(conversation["resource_id"])
        turn_operation = _uuid(
            f"{self.batch}:turn:{self.user_id}:{command_identity}"
        )
        started = time.monotonic()
        response = self.session.post(
            self.base_url
            + f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
            json={"message": question, "operation_id": turn_operation},
            headers={
                **self._headers(f"turn:{label}", turn_operation),
                "Accept": "text/event-stream",
            },
            timeout=900,
        )
        # Canonical SSE JSON and message text are UTF-8.  ``requests`` may
        # otherwise apply the HTTP text/* ISO-8859-1 fallback when a proxy or
        # framework omits an explicit charset, corrupting valid Korean output.
        response.encoding = "utf-8"
        self._require(response, (200,), f"turn:{label}")
        if "event: failed" in response.text:
            raise MatrixError(f"{self.user_id}: {label} Codex turn failed: {response.text[-2000:]}")
        detail = self.get(
            f"/api/knowledge/v1/conversations/{conversation_id}",
            f"conversation-detail:{label}",
        )
        conversation_space = (detail.get("conversation") or {}).get("space") or {}
        if conversation_space.get("id") != space_id:
            raise MatrixError(f"{self.user_id}: {label} conversation lost its Wiki binding.")
        messages = detail.get("messages")
        if not isinstance(messages, list):
            raise MatrixError(f"{self.user_id}: {label} conversation messages are invalid.")
        assistants = [row for row in messages if isinstance(row, dict) and row.get("role") == "assistant"]
        if not assistants:
            raise MatrixError(f"{self.user_id}: {label} has no assistant response.")
        content = assistants[-1].get("content") or {}
        answer = str(((content.get("content") or {}).get("text")) or "")
        missing_groups = [
            group
            for group in expected_marker_groups
            if not any(marker in answer for marker in group)
        ]
        if missing_groups:
            raise MatrixError(
                f"{self.user_id}: {label} answer missed acceptable evidence groups "
                f"{missing_groups}: {answer[:1000]}"
            )
        return {
            "answer": answer[:2000],
            "conversation_id": conversation_id,
            "conversation_operation_id": conversation_operation,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "expected_marker_groups": [
                list(group) for group in expected_marker_groups
            ],
            "space_id": space_id,
            "turn_operation_id": turn_operation,
            "turn_event_terminal": "event: completed" in response.text,
        }


def _markdown(
    title: str,
    *,
    marker: str,
    body: str,
    clusters: tuple[str, ...] = (),
) -> str:
    cluster_lines = "\n".join(f"- semantic cluster: {value}" for value in clusters)
    return (
        f"# {title}\n\n"
        f"{body}\n\n"
        f"Record identifier: {marker}\n\n"
        + (f"## Relationship vocabulary\n\n{cluster_lines}\n\n" if cluster_lines else "")
        + "This page is an isolated localhost validation fixture.\n"
    )


def _default_report(batch: str, base_url: str, data_root: Path) -> dict[str, Any]:
    return {
        "accounts": {},
        "base_url": base_url,
        "batch": batch,
        "company_api_called": False,
        "created_at": _now(),
        "data_root": str(data_root),
        "failures": [],
        "higher_publications": {},
        "query_results": {},
        "schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "status": "running",
        "updated_at": _now(),
    }


def _load_report(path: Path, batch: str, base_url: str, data_root: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_report(batch, base_url, data_root)
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema") != _SCHEMA
        or value.get("batch") != batch
        or value.get("base_url") != base_url
        or value.get("data_root") != str(data_root)
    ):
        raise MatrixError("The existing matrix report belongs to a different run.")
    return value


def _save_report(path: Path, report: dict[str, Any], lock: threading.Lock) -> None:
    with lock:
        report["updated_at"] = _now()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def _space_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {str(row.get("kind")): dict(row) for row in rows}
    if set(result) != {"personal", "part", "team", "responsibility"}:
        raise MatrixError(f"The account scope shape is invalid: {sorted(result)}")
    return result


def _record_publication(
    target: dict[str, Any],
    key: str,
    rows: tuple[PublishedItem, ...],
) -> None:
    target[key] = [
        {"item_id": row.item_id, "space_id": row.space_id} for row in rows
    ]


def run_uploads(args: argparse.Namespace, report: dict[str, Any], lock: threading.Lock) -> None:
    unrelated_topics = (
        "Renaissance harpsichord comma tuning",
        "deep sea siphonophore zooid anatomy",
        "medieval celadon glaze mineral chemistry",
        "Andean quipu cord taxonomy",
        "Arctic tern magnetic navigation",
        "Baroque bassoon reed scraping",
        "Cambrian trilobite eye morphology",
        "Sumerian cylinder seal iconography",
        "volcanic glass hydration dating",
        "Mesoamerican cacao fermentation",
        "Victorian postage watermark cataloging",
        "orbital resonance of shepherd moons",
        "Roman concrete pozzolan mineralogy",
        "Polynesian star compass nomenclature",
    )
    selected_users = [f"sbv-p{part:02d}-u{user:02d}" for part in range(1, 8) for user in (1, 2)]
    for ordinal, user_id in enumerate(selected_users):
        print(f"UPLOAD account {ordinal + 1}/{len(selected_users)} {user_id}", flush=True)
        api = ApiSession(args.base_url, user_id, args.password, args.batch)
        spaces = _space_map(api.spaces())
        wikis = api.personal_wikis()
        default = next(row for row in wikis if row.get("is_default") is True)
        validation_wiki = api.ensure_wiki(f"Matrix {args.batch} {user_id}")
        exact_spaces = api.spaces(validation_wiki)
        if len([row for row in exact_spaces if row.get("kind") == "personal"]) < 2:
            raise MatrixError(f"{user_id}: additional Wiki was not selectable.")
        part_number = int(user_id[5:7])
        user_number = int(user_id[-2:])
        personal_cluster = f"personal-governor-{user_id}"
        part_cluster = f"part-recovery-P{part_number:02d}"
        team_cluster = f"team-continuity-{spaces['team']['id']}"
        responsibility_cluster = "responsibility-amhs-continuity-matrix"
        markers = {
            "default": f"MATRIX-{user_id.upper()}-DEFAULT",
            "new_related": f"MATRIX-{user_id.upper()}-NEW-RELATED",
            "both": f"MATRIX-{user_id.upper()}-BOTH",
            "new_isolated": f"MATRIX-{user_id.upper()}-NEW-ISOLATED",
            "part_related": f"MATRIX-{user_id.upper()}-PART-RELATED",
            "part_isolated": f"MATRIX-{user_id.upper()}-PART-ISOLATED",
        }
        facts = {
            "default": f"The private latch is cobalt-{part_number}{user_number} and must be checked before standby.",
            "new_related": f"The flux governor reserve threshold is {40 + part_number + user_number} percent.",
            "both": f"The paired flux governor uses the same reserve family and Part recovery lane P{part_number:02d}.",
            "part_related": f"Part P{part_number:02d} recovery order is isolate-segment, drain-queue, resume-at-{20 + part_number}-percent.",
        }
        entry = report["accounts"].setdefault(user_id, {})
        entry.update(
            {
                "default_wiki_id": str(default["space_id"]),
                "markers": markers,
                "part_id": spaces["part"]["id"],
                "responsibility_id": spaces["responsibility"]["id"],
                "team_id": spaces["team"]["id"],
                "validation_wiki_id": validation_wiki,
            }
        )
        publications = entry.setdefault("publications", {})
        checkpoints = entry.setdefault("checkpoints", [])

        actions = (
            (
                "default",
                "personal",
                str(default["space_id"]),
                _markdown(
                    f"Private cobalt latch {user_id}",
                    marker=markers["default"],
                    body=facts["default"],
                    clusters=(f"default-latch-{user_id}",),
                ),
                None,
            ),
            (
                "new_related",
                "personal",
                validation_wiki,
                _markdown(
                    f"Flux governor reserve {user_id}",
                    marker=markers["new_related"],
                    body=facts["new_related"],
                    clusters=(personal_cluster,),
                ),
                None,
            ),
            (
                "both",
                "both",
                validation_wiki,
                _markdown(
                    f"Paired flux and recovery lane {user_id}",
                    marker=markers["both"],
                    body=facts["both"],
                    clusters=(
                        personal_cluster,
                        part_cluster,
                        team_cluster,
                        responsibility_cluster,
                    ),
                ),
                None,
            ),
            (
                "new_isolated",
                "personal",
                validation_wiki,
                _markdown(
                    f"{unrelated_topics[ordinal]} {user_id}",
                    marker=markers["new_isolated"],
                    body=f"This note concerns only {unrelated_topics[ordinal]} and contains no AMHS operating rule.",
                ),
                None,
            ),
            (
                "part_related",
                "part",
                None,
                _markdown(
                    f"Part recovery sequence {user_id}",
                    marker=markers["part_related"],
                    body=facts["part_related"],
                    clusters=(part_cluster, team_cluster, responsibility_cluster),
                ),
                None,
            ),
            (
                "part_isolated",
                "part",
                None,
                _markdown(
                    f"Independent cultural note {user_id}",
                    marker=markers["part_isolated"],
                    body=f"A standalone catalog entry about {unrelated_topics[ordinal]} with no operational dependency.",
                ),
                None,
            ),
        )
        for action, target_mode, personal_space_id, markdown, explicit_target in actions:
            rows = api.upload(
                action=action,
                filename=f"{args.batch.lower()}-{user_id}-{action}.md",
                markdown=markdown,
                title=markdown.splitlines()[0][2:],
                target_mode=target_mode,
                personal_space_id=personal_space_id,
                target=explicit_target,
            )
            _record_publication(publications, action, rows)
            for row in rows:
                checkpoints.append(api.checkpoint(row.space_id, f"after-{action}"))
            _save_report(args.report, report, lock)

    # Leaders are intentionally selected from the same accounts already tested
    # above, so their personal-Wiki evidence and native-authority evidence join.
    leader_candidates: dict[str, tuple[str, dict[str, Any]]] = {}
    responsibility_candidate: tuple[str, dict[str, Any]] | None = None
    for user_id in selected_users:
        api = ApiSession(args.base_url, user_id, args.password, args.batch)
        for row in api.spaces():
            authority = row.get("native_authority")
            if not isinstance(authority, dict):
                continue
            target = authority.get("publication_target")
            if not isinstance(target, dict):
                continue
            if row.get("kind") == "team":
                leader_candidates[str(row["id"])] = (user_id, dict(target))
            elif row.get("kind") == "responsibility":
                responsibility_candidate = (user_id, dict(target))
    if len(leader_candidates) != 3 or responsibility_candidate is None:
        raise MatrixError("The three Team leaders or Responsibility executive are missing.")

    for team_id, (user_id, target) in sorted(leader_candidates.items()):
        print(f"UPLOAD Team native {team_id} by {user_id}", flush=True)
        api = ApiSession(args.base_url, user_id, args.password, args.batch)
        marker = f"MATRIX-TEAM-{team_id.upper()}-NATIVE"
        isolated_marker = f"MATRIX-TEAM-{team_id.upper()}-ISOLATED"
        related = api.upload(
            action=f"team-native-related:{team_id}",
            filename=f"{args.batch.lower()}-team-related-{team_id}.md",
            markdown=_markdown(
                f"Team escalation policy {team_id}",
                marker=marker,
                body="The Team escalation window is exactly twelve minutes before the coordinated queue drain begins.",
                clusters=(
                    f"team-continuity-{team_id}",
                    "responsibility-amhs-continuity-matrix",
                ),
            ),
            title=f"Team escalation policy {team_id}",
            target_mode="explicit",
            target=target,
        )
        isolated = api.upload(
            action=f"team-native-isolated:{team_id}",
            filename=f"{args.batch.lower()}-team-isolated-{team_id}.md",
            markdown=_markdown(
                f"Team detached astronomy note {team_id}",
                marker=isolated_marker,
                body="This detached note records the albedo taxonomy of distant Kuiper belt objects only.",
            ),
            title=f"Team detached astronomy note {team_id}",
            target_mode="explicit",
            target=target,
        )
        record = {
            "isolated_marker": isolated_marker,
            "leader_user_id": user_id,
            "marker": marker,
            "space_id": team_id,
        }
        _record_publication(record, "related", related)
        _record_publication(record, "isolated", isolated)
        record["checkpoint"] = api.checkpoint(team_id, "after-team-native")
        report["higher_publications"][f"team:{team_id}"] = record
        _save_report(args.report, report, lock)

    executive_user, responsibility_target = responsibility_candidate
    responsibility_id = str(responsibility_target["space_id"])
    print(f"UPLOAD Responsibility native {responsibility_id} by {executive_user}", flush=True)
    api = ApiSession(args.base_url, executive_user, args.password, args.batch)
    marker = "MATRIX-RESPONSIBILITY-NATIVE"
    isolated_marker = "MATRIX-RESPONSIBILITY-ISOLATED"
    related = api.upload(
        action="responsibility-native-related",
        filename=f"{args.batch.lower()}-responsibility-related.md",
        markdown=_markdown(
            "Responsibility continuity policy",
            marker=marker,
            body="The 담당 continuity checkpoint is exactly twenty-seven minutes after the first Team escalation.",
            clusters=("responsibility-amhs-continuity-matrix",),
        ),
        title="Responsibility continuity policy",
        target_mode="explicit",
        target=responsibility_target,
    )
    isolated = api.upload(
        action="responsibility-native-isolated",
        filename=f"{args.batch.lower()}-responsibility-isolated.md",
        markdown=_markdown(
            "Responsibility detached textile note",
            marker=isolated_marker,
            body="This detached note classifies eighteenth-century brocade loom patterns only.",
        ),
        title="Responsibility detached textile note",
        target_mode="explicit",
        target=responsibility_target,
    )
    record = {
        "executive_user_id": executive_user,
        "isolated_marker": isolated_marker,
        "marker": marker,
        "space_id": responsibility_id,
    }
    _record_publication(record, "related", related)
    _record_publication(record, "isolated", isolated)
    record["checkpoint"] = api.checkpoint(
        responsibility_id, "after-responsibility-native"
    )
    report["higher_publications"]["responsibility"] = record
    report["upload_stage"] = {"completed_at": _now(), "status": "pass"}
    _save_report(args.report, report, lock)


def _pending_jobs(data_root: Path) -> dict[str, int]:
    database = data_root / "core" / "knowledge_core.db"
    connection = sqlite3.connect(
        "file:" + database.as_posix() + "?mode=ro", isolation_level=None, uri=True
    )
    try:
        return {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """SELECT state,count(*) FROM durable_job
                     WHERE state IN ('queued','claimed','running','retry_wait')
                     GROUP BY state"""
            )
        }
    finally:
        connection.close()


def run_repairs(args: argparse.Namespace, report: dict[str, Any], lock: threading.Lock) -> None:
    repairs = report.setdefault("relationship_clarifications", {})
    part_entries: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for user_id, entry in sorted(report["accounts"].items()):
        part_entries.setdefault(str(entry["part_id"]), []).append((user_id, entry))
        api = ApiSession(args.base_url, user_id, args.password, args.batch)
        wiki_id = str(entry["validation_wiki_id"])
        graph = api.get(
            f"/api/knowledge/v1/spaces/{wiki_id}/graph", f"repair-graph:{user_id}"
        )
        edges = _edges(graph)
        related_id = _published_id(entry, "new_related", wiki_id)
        both_id = _published_id(entry, "both", wiki_id)
        if any(
            {str(edge.get("source_item_id")), str(edge.get("target_item_id"))}
            == {related_id, both_id}
            for edge in edges
        ):
            continue
        if user_id in repairs:
            continue
        part_id = str(entry["part_id"])
        part_both_id = _published_id(entry, "both", part_id)
        part_before = api.item_detail(part_id, part_both_id, f"repair-part-before:{user_id}")
        part_before_item = _mapping(
            part_before.get("item"), f"repair-part-before:{user_id}:item"
        )
        part_before_revision = str(part_before_item["current_revision_id"])
        part_before_markdown = str(part_before.get("markdown") or "")
        part_number = int(user_id[5:7])
        user_number = int(user_id[-2:])
        threshold = 40 + part_number + user_number
        marker = str(entry["markers"]["both"])
        clarified = _markdown(
            f"Paired flux and recovery lane {user_id}",
            marker=marker,
            body=(
                f"The flux governor reserve threshold is exactly {threshold} percent. "
                "This page directly extends the matching flux governor reserve rule. "
                f"Part recovery lane P{part_number:02d} uses this same reserve threshold."
            ),
            clusters=(
                f"personal-governor-{user_id}",
                f"part-recovery-P{part_number:02d}",
                f"team-continuity-{entry['team_id']}",
                "responsibility-amhs-continuity-matrix",
            ),
        )
        edit = api.edit_item(
            action="clarify-both-personal-copy",
            space_id=wiki_id,
            item_id=both_id,
            markdown=clarified,
        )
        part_after = api.item_detail(part_id, part_both_id, f"repair-part-after:{user_id}")
        part_after_item = _mapping(
            part_after.get("item"), f"repair-part-after:{user_id}:item"
        )
        part_unchanged = (
            str(part_after_item["current_revision_id"]) == part_before_revision
            and str(part_after.get("markdown") or "") == part_before_markdown
        )
        if not part_unchanged:
            raise MatrixError(
                f"{user_id}: editing the personal both-copy changed the Part copy."
            )
        repairs[user_id] = {
            **edit,
            "checked_at": _now(),
            "item_id": both_id,
            "part_copy_item_id": part_both_id,
            "part_copy_revision_id": part_before_revision,
            "part_copy_unchanged": True,
            "space_id": wiki_id,
        }
        _save_report(args.report, report, lock)
        print(f"REPAIR PASS {user_id} personal edit / Part copy unchanged", flush=True)

    for part_id, members in sorted(part_entries.items()):
        repair_key = f"part:{part_id}"
        api = ApiSession(args.base_url, members[0][0], args.password, args.batch)
        graph = api.get(
            f"/api/knowledge/v1/spaces/{part_id}/graph", f"repair-part-graph:{part_id}"
        )
        edges = _edges(graph)
        related_ids = {
            _published_id(entry, action, part_id)
            for _, entry in members
            for action in ("both", "part_related")
        }
        linked_ids = {
            item_id
            for edge in edges
            if {
                str(edge.get("source_item_id")),
                str(edge.get("target_item_id")),
            }
            <= related_ids
            for item_id in (
                str(edge.get("source_item_id")),
                str(edge.get("target_item_id")),
            )
        }
        if linked_ids == related_ids or repair_key in repairs:
            continue

        part_number = int(members[0][0][5:7])
        resume_percent = 20 + part_number
        edits: list[dict[str, object]] = []
        for user_id, entry in members:
            member_api = ApiSession(args.base_url, user_id, args.password, args.batch)
            for action in ("both", "part_related"):
                item_id = _published_id(entry, action, part_id)
                marker = str(entry["markers"][action])
                personal_before_revision: str | None = None
                personal_before_markdown: str | None = None
                personal_item_id: str | None = None
                if action == "both":
                    personal_space_id = str(entry["validation_wiki_id"])
                    personal_item_id = _published_id(entry, action, personal_space_id)
                    personal_before = member_api.item_detail(
                        personal_space_id,
                        personal_item_id,
                        f"repair-personal-before:{user_id}:{action}",
                    )
                    personal_before_item = _mapping(
                        personal_before.get("item"),
                        f"repair-personal-before:{user_id}:{action}:item",
                    )
                    personal_before_revision = str(
                        personal_before_item["current_revision_id"]
                    )
                    personal_before_markdown = str(personal_before.get("markdown") or "")

                clarified = _markdown(
                    f"Part P{part_number:02d} shared recovery protocol {user_id} {action}",
                    marker=marker,
                    body=(
                        f"This is a related contribution to the same Part P{part_number:02d} "
                        "flux governor recovery protocol. The invariant recovery sequence is "
                        f"isolate-segment, drain-queue, resume-at-{resume_percent}-percent. "
                        f"Contributor {user_id} records the {action} view of that shared procedure."
                    ),
                    clusters=(
                        f"part-recovery-P{part_number:02d}",
                        f"team-continuity-{entry['team_id']}",
                        "responsibility-amhs-continuity-matrix",
                    ),
                )
                edit = member_api.edit_item(
                    action=f"clarify-part-{part_number:02d}-{user_id}-{action}",
                    space_id=part_id,
                    item_id=item_id,
                    markdown=clarified,
                )

                personal_unchanged: bool | None = None
                if action == "both":
                    personal_after = member_api.item_detail(
                        str(entry["validation_wiki_id"]),
                        str(personal_item_id),
                        f"repair-personal-after:{user_id}:{action}",
                    )
                    personal_after_item = _mapping(
                        personal_after.get("item"),
                        f"repair-personal-after:{user_id}:{action}:item",
                    )
                    personal_unchanged = (
                        str(personal_after_item["current_revision_id"])
                        == personal_before_revision
                        and str(personal_after.get("markdown") or "")
                        == personal_before_markdown
                    )
                    if not personal_unchanged:
                        raise MatrixError(
                            f"{user_id}: editing the Part both-copy changed the personal copy."
                        )
                edits.append(
                    {
                        **edit,
                        "action": action,
                        "item_id": item_id,
                        "marker": marker,
                        "personal_copy_item_id": personal_item_id,
                        "personal_copy_unchanged": personal_unchanged,
                        "user_id": user_id,
                    }
                )

        repairs[repair_key] = {
            "checked_at": _now(),
            "edits": edits,
            "part_id": part_id,
            "personal_copies_unchanged": all(
                row["personal_copy_unchanged"] is not False for row in edits
            ),
        }
        _save_report(args.report, report, lock)
        print(
            f"REPAIR PASS Part P{part_number:02d} related documents / personal copies unchanged",
            flush=True,
        )

    team_records = {
        key.split(":", 1)[1]: value
        for key, value in report["higher_publications"].items()
        if key.startswith("team:")
    }
    team_protocol_names = ("Oriole Delta", "Juniper Echo", "Harbor Quartz")
    for team_ordinal, (team_id, record) in enumerate(sorted(team_records.items())):
        repair_key = f"team:{team_id}"
        user_id = str(record["leader_user_id"])
        api = ApiSession(args.base_url, user_id, args.password, args.batch)
        graph = api.get(
            f"/api/knowledge/v1/spaces/{team_id}/graph",
            f"repair-team-graph:{team_id}",
        )
        edges = _edges(graph)
        nodes = _nodes(graph)
        native_id = str(record["related"][0]["item_id"])
        native_connected = any(
            _touches(edge, native_id)
            and any(
                nodes.get(endpoint, {}).get("source_space_id") != team_id
                for endpoint in (
                    str(edge.get("source_item_id")),
                    str(edge.get("target_item_id")),
                )
                if endpoint != native_id
            )
            for edge in edges
        )
        descendant_repair_key = f"team-descendant:{team_id}"
        if (
            not native_connected
            and repair_key in repairs
            and descendant_repair_key not in repairs
        ):
            descendant_candidates = [
                (account_id, entry)
                for account_id, entry in sorted(report["accounts"].items())
                if str(entry["team_id"]) == team_id
            ]
            descendant_user_id, descendant_entry = descendant_candidates[1]
            descendant_api = ApiSession(
                args.base_url, descendant_user_id, args.password, args.batch
            )
            descendant_part_id = str(descendant_entry["part_id"])
            descendant_item_id = _published_id(
                descendant_entry, "part_related", descendant_part_id
            )
            part_number = int(descendant_user_id[5:7])
            marker = str(descendant_entry["markers"]["part_related"])
            clarified = _markdown(
                f"Part P{part_number:02d} recovery governed by Team {team_id}",
                marker=marker,
                body=(
                    f"This related Part P{part_number:02d} flux governor procedure implements "
                    f"the Team shared recovery and escalation policy {team_id}. Its invariant "
                    "sequence is isolate-segment, drain-queue, then resume at the Part-specific "
                    f"threshold of {20 + part_number} percent. The governing Team escalation "
                    "window is exactly twelve minutes before coordinated queue drain."
                ),
                clusters=(
                    f"part-recovery-P{part_number:02d}",
                    f"team-continuity-{team_id}",
                    "responsibility-amhs-continuity-matrix",
                ),
            )
            edit = descendant_api.edit_item(
                action=f"clarify-team-descendant-{team_id}",
                space_id=descendant_part_id,
                item_id=descendant_item_id,
                markdown=clarified,
            )
            repairs[descendant_repair_key] = {
                **edit,
                "checked_at": _now(),
                "item_id": descendant_item_id,
                "part_space_id": descendant_part_id,
                "team_space_id": team_id,
                "user_id": descendant_user_id,
            }
            _save_report(args.report, report, lock)
            print(
                f"REPAIR PASS Part P{part_number:02d} descendant now references Team {team_id}",
                flush=True,
            )
            continue
        binding_repair_key = f"team-binding:{team_id}"
        if (
            not native_connected
            and descendant_repair_key in repairs
            and binding_repair_key not in repairs
        ):
            protocol_name = team_protocol_names[team_ordinal]
            marker = str(record["marker"])
            team_markdown = _markdown(
                f"Team {protocol_name} continuity protocol",
                marker=marker,
                body=(
                    f"The {protocol_name} continuity protocol is this Team's governing recovery "
                    "policy. At the twelve-minute escalation window it sets an amber coordination "
                    "beacon, freezes the east queue, and records a continuity checksum before "
                    "coordinated drain. It aggregates descendant Part isolate-segment and "
                    "drain-queue procedures and reports to the Responsibility continuity policy, "
                    "whose checkpoint is twenty-seven minutes after first Team escalation."
                ),
                clusters=(
                    f"team-continuity-{team_id}",
                    f"team-protocol-{protocol_name.lower().replace(' ', '-')}",
                    "responsibility-amhs-continuity-matrix",
                ),
            )
            team_edit = api.edit_item(
                action=f"bind-team-native-{team_id}",
                space_id=team_id,
                item_id=native_id,
                markdown=team_markdown,
            )
            descendant_candidates = [
                (account_id, entry)
                for account_id, entry in sorted(report["accounts"].items())
                if str(entry["team_id"]) == team_id
            ]
            descendant_user_id, descendant_entry = descendant_candidates[-1]
            descendant_api = ApiSession(
                args.base_url, descendant_user_id, args.password, args.batch
            )
            descendant_part_id = str(descendant_entry["part_id"])
            part_number = int(descendant_user_id[5:7])
            binding_marker = (
                f"MATRIX-TEAM-{team_id.upper()}-PART-BINDING-{part_number:02d}"
            )
            publication = descendant_api.upload(
                action=f"team-binding-{team_id}",
                filename=f"{args.batch.lower()}-team-binding-{team_id}.md",
                markdown=_markdown(
                    f"Part P{part_number:02d} implementation of Team {protocol_name}",
                    marker=binding_marker,
                    body=(
                        f"This Part P{part_number:02d} implementation follows the Team "
                        f"{protocol_name} continuity protocol. At the same twelve-minute "
                        "escalation window it sets the amber coordination beacon, freezes the "
                        "east queue, records the continuity checksum, and then performs the "
                        "Part isolate-segment and drain-queue recovery sequence."
                    ),
                    clusters=(
                        f"part-recovery-P{part_number:02d}",
                        f"team-continuity-{team_id}",
                        f"team-protocol-{protocol_name.lower().replace(' ', '-')}",
                    ),
                ),
                title=f"Part P{part_number:02d} implementation of Team {protocol_name}",
                target_mode="part",
            )
            if len(publication) != 1 or publication[0].space_id != descendant_part_id:
                raise MatrixError(
                    f"Team {team_id}: binding publication did not land in the expected Part."
                )
            repairs[binding_repair_key] = {
                "binding_marker": binding_marker,
                "checked_at": _now(),
                "part_item_id": publication[0].item_id,
                "part_space_id": descendant_part_id,
                "protocol_name": protocol_name,
                "team_edit": team_edit,
                "team_item_id": native_id,
                "team_space_id": team_id,
                "user_id": descendant_user_id,
            }
            _save_report(args.report, report, lock)
            print(
                f"REPAIR PASS Team {team_id} / Part P{part_number:02d} protocol binding uploaded",
                flush=True,
            )
            continue
        if native_connected or repair_key in repairs:
            continue
        part_numbers = sorted(
            {
                int(account_id[5:7])
                for account_id, entry in report["accounts"].items()
                if str(entry["team_id"]) == team_id
            }
        )
        part_labels = ", ".join(f"P{number:02d}" for number in part_numbers)
        marker = str(record["marker"])
        clarified = _markdown(
            f"Team shared recovery and escalation policy {team_id}",
            marker=marker,
            body=(
                f"This Team policy aggregates the descendant Part {part_labels} flux governor "
                "recovery protocols. Every related Part procedure begins with isolate-segment "
                "and drain-queue, while its resume threshold stays Part-specific. The Team "
                "escalation window is exactly twelve minutes before the coordinated queue drain."
            ),
            clusters=(
                f"team-continuity-{team_id}",
                *(f"part-recovery-P{number:02d}" for number in part_numbers),
                "responsibility-amhs-continuity-matrix",
            ),
        )
        edit = api.edit_item(
            action=f"clarify-team-native-{team_id}",
            space_id=team_id,
            item_id=native_id,
            markdown=clarified,
        )
        repairs[repair_key] = {
            **edit,
            "checked_at": _now(),
            "descendant_parts": part_labels,
            "item_id": native_id,
            "space_id": team_id,
        }
        _save_report(args.report, report, lock)
        print(f"REPAIR PASS Team {team_id} native policy clarified", flush=True)

    responsibility = report["higher_publications"]["responsibility"]
    responsibility_id = str(responsibility["space_id"])
    repair_key = f"responsibility:{responsibility_id}"
    api = ApiSession(
        args.base_url,
        str(responsibility["executive_user_id"]),
        args.password,
        args.batch,
    )
    graph = api.get(
        f"/api/knowledge/v1/spaces/{responsibility_id}/graph",
        "repair-responsibility-graph",
    )
    edges = _edges(graph)
    nodes = _nodes(graph)
    native_id = str(responsibility["related"][0]["item_id"])
    native_connected = any(
        _touches(edge, native_id)
        and any(
            nodes.get(endpoint, {}).get("source_space_id") != responsibility_id
            for endpoint in (
                str(edge.get("source_item_id")),
                str(edge.get("target_item_id")),
            )
            if endpoint != native_id
        )
        for edge in edges
    )
    if not native_connected and repair_key not in repairs:
        marker = str(responsibility["marker"])
        clarified = _markdown(
            "Responsibility continuity aggregation policy",
            marker=marker,
            body=(
                "This Responsibility policy aggregates every Team escalation policy and the "
                "descendant Part flux governor recovery procedures. Each related Team uses a "
                "twelve-minute escalation window before coordinated queue drain; Part procedures "
                "begin isolate-segment and drain-queue. The Responsibility continuity checkpoint "
                "is exactly twenty-seven minutes after the first Team escalation."
            ),
            clusters=(
                "responsibility-amhs-continuity-matrix",
                *(f"team-continuity-{team_id}" for team_id in sorted(team_records)),
            ),
        )
        edit = api.edit_item(
            action="clarify-responsibility-native",
            space_id=responsibility_id,
            item_id=native_id,
            markdown=clarified,
        )
        repairs[repair_key] = {
            **edit,
            "checked_at": _now(),
            "item_id": native_id,
            "space_id": responsibility_id,
        }
        _save_report(args.report, report, lock)
        print("REPAIR PASS Responsibility native policy clarified", flush=True)
    descendant_repair_key = f"responsibility-descendant:{responsibility_id}"
    if (
        not native_connected
        and repair_key in repairs
        and descendant_repair_key not in repairs
    ):
        team_id, team_record = sorted(team_records.items())[0]
        team_api = ApiSession(
            args.base_url,
            str(team_record["leader_user_id"]),
            args.password,
            args.batch,
        )
        team_item_id = str(team_record["related"][0]["item_id"])
        team_marker = str(team_record["marker"])
        descendant_parts = sorted(
            {
                int(account_id[5:7])
                for account_id, entry in report["accounts"].items()
                if str(entry["team_id"]) == team_id
            }
        )
        part_labels = ", ".join(f"P{number:02d}" for number in descendant_parts)
        clarified = _markdown(
            f"Team {team_id} continuity policy governed by Responsibility",
            marker=team_marker,
            body=(
                f"This Team policy aggregates descendant Parts {part_labels} and implements the "
                "Responsibility continuity aggregation policy. Part recovery begins with "
                "isolate-segment and drain-queue. The Team escalation window is exactly twelve "
                "minutes before coordinated queue drain, and the governing Responsibility "
                "checkpoint is exactly twenty-seven minutes after the first Team escalation."
            ),
            clusters=(
                f"team-continuity-{team_id}",
                *(f"part-recovery-P{number:02d}" for number in descendant_parts),
                "responsibility-amhs-continuity-matrix",
            ),
        )
        edit = team_api.edit_item(
            action="clarify-responsibility-descendant-team",
            space_id=team_id,
            item_id=team_item_id,
            markdown=clarified,
        )
        repairs[descendant_repair_key] = {
            **edit,
            "checked_at": _now(),
            "item_id": team_item_id,
            "responsibility_space_id": responsibility_id,
            "team_space_id": team_id,
        }
        _save_report(args.report, report, lock)
        print(
            f"REPAIR PASS Team {team_id} descendant now references Responsibility",
            flush=True,
        )
    report["relationship_clarification_stage"] = {
        "clarified_accounts": len(repairs),
        "completed_at": _now(),
        "status": "pass",
    }
    _save_report(args.report, report, lock)


def _edges(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = graph.get("edges")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MatrixError("Graph edge payload is invalid.")
    return rows


def _nodes(graph: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = graph.get("nodes")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MatrixError("Graph node payload is invalid.")
    return {str(row["id"]): row for row in rows}


def _touches(edge: Mapping[str, Any], item_id: str) -> bool:
    return item_id in {str(edge.get("source_item_id")), str(edge.get("target_item_id"))}


def _published_id(entry: Mapping[str, Any], action: str, space_id: str) -> str:
    rows = (entry.get("publications") or {}).get(action) or []
    match = next((row for row in rows if row.get("space_id") == space_id), None)
    if match is None:
        raise MatrixError(f"Publication {action} has no target in {space_id}.")
    return str(match["item_id"])


def run_graph_audit(args: argparse.Namespace, report: dict[str, Any], lock: threading.Lock) -> None:
    deadline = time.monotonic() + args.job_timeout
    while True:
        pending = _pending_jobs(args.data_root)
        if not pending:
            break
        if time.monotonic() >= deadline:
            raise MatrixError(f"Durable jobs did not drain: {pending}")
        print(f"WAIT durable jobs {pending}", flush=True)
        time.sleep(5)
    audit: dict[str, Any] = {"accounts": {}, "parts": {}, "teams": {}}
    report["graph_audit"] = audit
    part_entries: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for user_id, entry in sorted(report["accounts"].items()):
        part_entries.setdefault(str(entry["part_id"]), []).append((user_id, entry))
        api = ApiSession(args.base_url, user_id, args.password, args.batch)
        wiki_id = str(entry["validation_wiki_id"])
        graph = api.get(f"/api/knowledge/v1/spaces/{wiki_id}/graph", f"audit-personal:{user_id}")
        edges = _edges(graph)
        related = {
            _published_id(entry, "new_related", wiki_id),
            _published_id(entry, "both", wiki_id),
        }
        isolated = _published_id(entry, "new_isolated", wiki_id)
        linked = any(
            {str(edge.get("source_item_id")), str(edge.get("target_item_id"))} == related
            for edge in edges
        )
        detached = not any(_touches(edge, isolated) for edge in edges)
        if not linked or not detached:
            raise MatrixError(
                f"{user_id}: personal related={linked}, isolated={detached}."
            )
        final_checkpoint = api.checkpoint(wiki_id, "final-personal")
        audit["accounts"][user_id] = {
            "final_checkpoint": final_checkpoint,
            "isolated_node_detached": detached,
            "related_pair_linked": linked,
        }
        _save_report(args.report, report, lock)

    for part_id, members in sorted(part_entries.items()):
        api = ApiSession(args.base_url, members[0][0], args.password, args.batch)
        graph = api.get(f"/api/knowledge/v1/spaces/{part_id}/graph", f"audit-part:{part_id}")
        edges = _edges(graph)
        related_ids: set[str] = set()
        isolated_ids: set[str] = set()
        for _, entry in members:
            related_ids.add(_published_id(entry, "both", part_id))
            related_ids.add(_published_id(entry, "part_related", part_id))
            isolated_ids.add(_published_id(entry, "part_isolated", part_id))
        related_edge_endpoints = [
            {
                str(edge.get("source_item_id")),
                str(edge.get("target_item_id")),
            }
            for edge in edges
            if len(
                {
                    str(edge.get("source_item_id")),
                    str(edge.get("target_item_id")),
                }
                & related_ids
            )
            == 2
        ]
        linked_ids = set().union(*related_edge_endpoints) if related_edge_endpoints else set()
        detached = {item_id for item_id in isolated_ids if not any(_touches(edge, item_id) for edge in edges)}
        aggregate_edges = [edge for edge in edges if edge.get("edge_scope") == "aggregate"]
        if linked_ids != related_ids or detached != isolated_ids or aggregate_edges:
            raise MatrixError(
                f"Part {part_id}: related {len(linked_ids)}/{len(related_ids)}, "
                f"isolated {len(detached)}/{len(isolated_ids)}, aggregate {len(aggregate_edges)}."
            )
        audit["parts"][part_id] = {
            "final_checkpoint": api.checkpoint(part_id, "final-part"),
            "isolated_nodes_detached": len(detached),
            "related_nodes_linked": len(linked_ids),
        }

    team_records = {
        key.split(":", 1)[1]: value
        for key, value in report["higher_publications"].items()
        if key.startswith("team:")
    }
    for team_id, record in sorted(team_records.items()):
        user_id = str(record["leader_user_id"])
        api = ApiSession(args.base_url, user_id, args.password, args.batch)
        graph = api.get(f"/api/knowledge/v1/spaces/{team_id}/graph", f"audit-team:{team_id}")
        edges = _edges(graph)
        nodes = _nodes(graph)
        native_id = str(record["related"][0]["item_id"])
        isolated_id = str(record["isolated"][0]["item_id"])
        native_connected = any(
            _touches(edge, native_id)
            and any(
                nodes.get(endpoint, {}).get("source_space_id") != team_id
                for endpoint in (str(edge.get("source_item_id")), str(edge.get("target_item_id")))
                if endpoint != native_id
            )
            for edge in edges
        )
        descendant_part_spaces = {
            str(entry["part_id"])
            for entry in report["accounts"].values()
            if str(entry["team_id"]) == team_id
        }
        cross_part = any(
            edge.get("edge_scope") == "aggregate"
            and {
                nodes.get(str(edge.get("source_item_id")), {}).get("source_space_id"),
                nodes.get(str(edge.get("target_item_id")), {}).get("source_space_id"),
            }.issubset(descendant_part_spaces)
            and nodes.get(str(edge.get("source_item_id")), {}).get("source_space_id")
            != nodes.get(str(edge.get("target_item_id")), {}).get("source_space_id")
            for edge in edges
        )
        isolated = not any(_touches(edge, isolated_id) for edge in edges)
        if not native_connected or not cross_part or not isolated:
            raise MatrixError(
                f"Team {team_id}: native_connected={native_connected}, "
                f"cross_part={cross_part}, isolated={isolated}."
            )
        audit["teams"][team_id] = {
            "cross_part_edge_visible": cross_part,
            "final_checkpoint": api.checkpoint(team_id, "final-team"),
            "isolated_native_detached": isolated,
            "native_to_descendant_connected": native_connected,
        }

    responsibility = report["higher_publications"]["responsibility"]
    responsibility_id = str(responsibility["space_id"])
    api = ApiSession(
        args.base_url, str(responsibility["executive_user_id"]), args.password, args.batch
    )
    graph = api.get(
        f"/api/knowledge/v1/spaces/{responsibility_id}/graph", "audit-responsibility"
    )
    edges = _edges(graph)
    nodes = _nodes(graph)
    native_id = str(responsibility["related"][0]["item_id"])
    isolated_id = str(responsibility["isolated"][0]["item_id"])
    native_connected = any(
        _touches(edge, native_id)
        and any(
            nodes.get(endpoint, {}).get("source_space_id") != responsibility_id
            for endpoint in (str(edge.get("source_item_id")), str(edge.get("target_item_id")))
            if endpoint != native_id
        )
        for edge in edges
    )
    source_to_team: dict[str, str] = {}
    for entry in report["accounts"].values():
        source_to_team[str(entry["part_id"])] = str(entry["team_id"])
        source_to_team[str(entry["team_id"])] = str(entry["team_id"])
    cross_source = any(
        edge.get("edge_scope") == "aggregate"
        and source_to_team.get(
            str(nodes.get(str(edge.get("source_item_id")), {}).get("source_space_id"))
        )
        is not None
        and source_to_team.get(
            str(nodes.get(str(edge.get("target_item_id")), {}).get("source_space_id"))
        )
        is not None
        and source_to_team[
            str(nodes.get(str(edge.get("source_item_id")), {}).get("source_space_id"))
        ]
        != source_to_team[
            str(nodes.get(str(edge.get("target_item_id")), {}).get("source_space_id"))
        ]
        for edge in edges
    )
    isolated = not any(_touches(edge, isolated_id) for edge in edges)
    if not native_connected or not cross_source or not isolated:
        raise MatrixError(
            "Responsibility graph failed: "
            f"native_connected={native_connected}, cross_source={cross_source}, isolated={isolated}."
        )
    audit["responsibility"] = {
        "cross_team_or_source_edge_visible": cross_source,
        "final_checkpoint": api.checkpoint(responsibility_id, "final-responsibility"),
        "isolated_native_detached": isolated,
        "native_to_inherited_connected": native_connected,
    }
    audit["completed_at"] = _now()
    audit["status"] = "pass"
    _save_report(args.report, report, lock)


def _query_specifications(report: Mapping[str, Any]) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    team_records = {
        key.split(":", 1)[1]: value
        for key, value in report["higher_publications"].items()
        if key.startswith("team:")
    }
    responsibility = report["higher_publications"]["responsibility"]
    responsibility_marker = str(responsibility["marker"])
    account_entries: Mapping[str, Mapping[str, Any]] = report["accounts"]
    for user_id, entry in sorted(account_entries.items()):
        markers = entry["markers"]
        specs.extend(
            (
                {
                    "expected_groups": ((str(markers["default"]),),),
                    "label": "personal-default",
                    "question": "What private latch color and number does my default personal runbook require? Cite its record identifier.",
                    "search_query": "private latch cobalt standby",
                    "search_source_ids": {str(entry["default_wiki_id"])},
                    "space_id": str(entry["default_wiki_id"]),
                    "user_id": user_id,
                },
                {
                    "expected_groups": ((str(markers["new_related"]),),),
                    "label": "personal-additional",
                    "question": "What reserve threshold does the flux governor page in this Wiki specify? Cite its record identifier.",
                    "search_query": "flux governor reserve threshold",
                    "search_source_ids": {str(entry["validation_wiki_id"])},
                    "space_id": str(entry["validation_wiki_id"]),
                    "user_id": user_id,
                },
            )
        )

    # Two author accounts plus a third reader in every Part exercise all three
    # organization views.  Questions require both native and inherited records
    # at higher levels without exposing either record ID in the prompt.
    for part in range(1, 8):
        author = account_entries[f"sbv-p{part:02d}-u01"]
        coauthor = account_entries[f"sbv-p{part:02d}-u02"]
        part_markers = [
            str(author["markers"]["part_related"]),
            str(coauthor["markers"]["part_related"]),
        ]
        part_repair_key = f"part:{author['part_id']}"
        if part_repair_key in report.get("relationship_clarifications", {}):
            part_markers.extend(
                (
                    str(author["markers"]["both"]),
                    str(coauthor["markers"]["both"]),
                )
            )
        part_marker_group = tuple(part_markers)
        team_id = str(author["team_id"])
        team_marker = str(team_records[team_id]["marker"])
        for user in (1, 2, 3):
            user_id = f"sbv-p{part:02d}-u{user:02d}"
            specs.extend(
                (
                    {
                        "expected_groups": (part_marker_group,),
                        "label": "part",
                        "question": f"For Part P{part:02d}, what is the recovery order and resume percentage? Cite the record identifier.",
                        "search_query": f"Part P{part:02d} recovery order isolate segment drain queue",
                        "search_source_ids": {str(author["part_id"])},
                        "space_id": str(author["part_id"]),
                        "user_id": user_id,
                    },
                    {
                        "expected_groups": ((team_marker,), part_marker_group),
                        "label": "team",
                        "question": f"What Team escalation window is currently specified, and what inherited recovery order applies to Part P{part:02d}? Cite both record identifiers.",
                        "search_query": "Team escalation window twelve minutes coordinated queue drain",
                        "search_source_ids": {team_id},
                        "space_id": team_id,
                        "user_id": user_id,
                    },
                    {
                        "expected_groups": (
                            (responsibility_marker,),
                            (team_marker,),
                        ),
                        "label": "responsibility",
                        "question": f"What Responsibility continuity checkpoint interval and the escalation window for the Team containing Part P{part:02d} are specified? Cite both record identifiers.",
                        "search_query": f"continuity checkpoint twenty-seven minutes Team {team_id} escalation twelve minutes",
                        "search_source_ids": {str(responsibility["space_id"])},
                        "space_id": str(responsibility["space_id"]),
                        "user_id": user_id,
                    },
                )
            )
    return specs


def _run_query(args: argparse.Namespace, spec: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    user_id = str(spec["user_id"])
    label = str(spec["label"])
    api = ApiSession(args.base_url, user_id, args.password, args.batch)
    expected_groups = tuple(
        tuple(str(value) for value in group)
        for group in spec["expected_groups"]
    )
    # Search uses the first requested record as the grounding probe.  A higher
    # turn can require a second inherited record and the conversation retrieval
    # path must supply it independently.
    search = api.search(
        space_id=str(spec["space_id"]),
        query=str(spec["search_query"]),
        expected_markers=expected_groups[0],
        expected_source_space_ids=set(spec["search_source_ids"]),
        label=f"{user_id}:{label}",
    )
    turn = api.turn(
        label=label,
        space_id=str(spec["space_id"]),
        question=str(spec["question"]),
        expected_marker_groups=expected_groups,
    )
    key = f"{user_id}:{label}"
    return key, {"search": search, "turn": turn, "user_id": user_id}


def run_queries(args: argparse.Namespace, report: dict[str, Any], lock: threading.Lock) -> None:
    specifications = _query_specifications(report)
    selected_specifications = [
        spec
        for spec in specifications
        if args.query_scope == "all"
        or (
            args.query_scope == "personal"
            and str(spec["label"]).startswith("personal-")
        )
        or (
            args.query_scope == "organization"
            and not str(spec["label"]).startswith("personal-")
        )
    ]
    existing = report.setdefault("query_results", {})
    pending = [
        spec
        for spec in selected_specifications
        if f"{spec['user_id']}:{spec['label']}" not in existing
    ]
    print(
        f"QUERY scope={args.query_scope} selected={len(selected_specifications)} "
        f"matrix_total={len(specifications)} pending={len(pending)} "
        f"workers={args.query_workers}",
        flush=True,
    )
    failures: list[str] = []
    completed = len(selected_specifications) - len(pending)
    with ThreadPoolExecutor(max_workers=args.query_workers) as executor:
        futures = {executor.submit(_run_query, args, spec): spec for spec in pending}
        for future in as_completed(futures):
            spec = futures[future]
            key = f"{spec['user_id']}:{spec['label']}"
            try:
                result_key, result = future.result()
                existing[result_key] = result
                completed += 1
                print(
                    f"QUERY {completed}/{len(selected_specifications)} PASS {result_key}",
                    flush=True,
                )
            except Exception as exc:  # collect the whole matrix, then fail
                message = f"{key}: {exc}"
                failures.append(message)
                report["failures"].append(
                    {"at": _now(), "message": message, "stage": "queries"}
                )
                print(f"QUERY FAIL {message}", flush=True)
            _save_report(args.report, report, lock)
    if failures:
        raise MatrixError(f"{len(failures)} query checks failed; see the report.")
    expected_keys = {
        f"{spec['user_id']}:{spec['label']}" for spec in specifications
    }
    complete = expected_keys.issubset(existing)
    report["query_stage"] = {
        "codex_turns": len(expected_keys.intersection(existing)),
        "expected_codex_turns": len(expected_keys),
        "status": "pass" if complete else "in_progress",
    }
    if complete:
        report["query_stage"]["completed_at"] = _now()
    _save_report(args.report, report, lock)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:10211")
    parser.add_argument("--batch", default="E2E-20260826-V1")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--query-workers", type=int, default=3)
    parser.add_argument(
        "--query-scope",
        choices=("all", "personal", "organization"),
        default="all",
    )
    parser.add_argument("--job-timeout", type=int, default=3600)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stage",
        choices=("uploads", "repairs", "graphs", "queries", "all"),
        default="all",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.data_root = args.data_root.resolve(strict=True)
    if args.report is None:
        args.report = args.data_root / ".runtime" / f"account-matrix-{args.batch}.json"
    else:
        args.report = args.report.resolve(strict=False)
    if not 1 <= args.query_workers <= 4:
        raise MatrixError("--query-workers must be between one and four.")
    report = _load_report(args.report, args.batch, args.base_url, args.data_root)
    lock = threading.Lock()
    try:
        if args.stage in {"uploads", "all"}:
            run_uploads(args, report, lock)
        if args.stage in {"repairs", "all"}:
            run_repairs(args, report, lock)
        if args.stage in {"graphs", "all"}:
            run_graph_audit(args, report, lock)
        if args.stage in {"queries", "all"}:
            run_queries(args, report, lock)
        complete = all(
            isinstance(report.get(stage), dict)
            and report[stage].get("status") == "pass"
            for stage in ("upload_stage", "graph_audit", "query_stage")
        )
        report["status"] = "pass" if complete else "in_progress"
        if complete:
            report["completed_at"] = _now()
        _save_report(args.report, report, lock)
        print(
            json.dumps({"report": str(args.report), "status": report["status"]}),
            flush=True,
        )
        return 0
    except Exception as exc:
        report["status"] = "failed"
        report["failures"].append(
            {"at": _now(), "message": str(exc), "stage": args.stage}
        )
        _save_report(args.report, report, lock)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
