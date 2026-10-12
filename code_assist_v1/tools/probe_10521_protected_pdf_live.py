"""One-shot 10521 HTTP upload of the synthetic owner-authorized PDF.

Never uses company documents.  The admin test password is supplied by the
operator environment and is not printed or persisted by this probe.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import requests


BASE = "http://127.0.0.1:10521"
ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data-root"
SOURCE = Path(__file__).resolve().parents[1] / "output/pdf/synthetic-amhs-encrypted-surrogate.pdf"
CANARIES = ("C-17", "CV-04", "S-42", "09:35", "09:42", "7.4", "1.6")


def _payload(response: requests.Response, *accepted: int) -> dict:
    value = response.json()
    if response.status_code not in accepted:
        raise AssertionError(f"Unexpected HTTP {response.status_code}: {value}")
    return value


def _headers(csrf: str) -> dict[str, str]:
    return {"Origin": BASE, "X-CSRF-Token": csrf, "X-Request-ID": str(uuid4())}


def _published_by_source(digest: str) -> list[tuple[str, str, str, str, str]]:
    db = sqlite3.connect(
        (DATA_ROOT / "core/knowledge_core.db").as_uri() + "?mode=ro", uri=True
    )
    try:
        return db.execute(
            """SELECT selection.kind,t.item_id,t.resolved_revision_id,
                      body.content_md,extraction.extractor_name
               FROM source_artifact artifact
               JOIN publication_operation operation ON operation.artifact_id=artifact.id
               JOIN publication_target t ON t.publication_operation_id=operation.id
               JOIN publication_operation_body selection
                 ON selection.publication_operation_id=operation.id
               JOIN knowledge_revision_body body ON body.revision_id=t.resolved_revision_id
               JOIN extraction_snapshot extraction
                 ON extraction.id=operation.extraction_snapshot_id
               WHERE artifact.raw_sha256=? ORDER BY operation.created_at,t.item_id""",
            (digest,),
        ).fetchall()
    finally:
        db.close()


def _verify_published(digest: str) -> None:
    rows = _published_by_source(digest)
    if len(rows) != 1:
        raise AssertionError(f"Expected one published Wiki for the synthetic PDF; found {len(rows)}.")
    kind, item_id, revision_id, body, extractor = rows[0]
    if kind != "preserved_wiki" or extractor != "canonical-approved-owner-pdf":
        raise AssertionError(f"Protected source selected wrong Wiki lane: {kind}/{extractor}")
    found = {value: value in body for value in CANARIES}
    print(f"published_item={item_id}; revision={revision_id}; kind={kind}; canaries={found}", flush=True)
    if not all(found.values()):
        raise AssertionError("The protected-source Wiki omitted synthetic preservation facts.")


def main() -> None:
    raw = SOURCE.read_bytes()
    digest = sha256(raw).hexdigest()
    db = sqlite3.connect(
        (DATA_ROOT / "core/knowledge_core.db").as_uri() + "?mode=ro", uri=True
    )
    try:
        already_uploaded = db.execute(
            "SELECT 1 FROM source_artifact WHERE raw_sha256=? LIMIT 1", (digest,)
        ).fetchone() is not None
    finally:
        db.close()
    if already_uploaded:
        # A rerun is read-only.  It can verify the previous result but must
        # never create a second preview or publication.
        _verify_published(digest)
        return

    session = requests.Session()
    login = _payload(session.post(
        BASE + "/api/code/auth/login",
        json={"user_id": "admin", "password": os.environ["SB10521_ADMIN_PASSWORD"]},
        headers={"Origin": BASE}, timeout=15,
    ), 200)
    csrf = login["csrf_token"]
    created = _payload(session.post(
        BASE + "/api/knowledge/v1/publication-previews",
        data={"operation_id": str(uuid4()), "source_type": "upload",
              "title": "10521 synthetic owner-authorized AMHS PDF"},
        files={"file": (SOURCE.name, raw, "application/pdf")},
        headers=_headers(csrf), timeout=30,
    ), 202)
    preview_id = created["preview_id"]
    print(f"preview_id={preview_id}; original_sha256={digest}", flush=True)
    for _ in range(240):
        current = _payload(session.get(
            BASE + f"/api/knowledge/v1/publication-previews/{preview_id}", timeout=15,
        ), 200)
        if current["status"] in {"failed", "cancelled", "expired"}:
            raise AssertionError(f"Protected preview did not succeed: {current}")
        if current["status"] == "ready" and current.get("publication_ready"):
            break
        time.sleep(2)
    else:
        raise AssertionError(f"Protected Wiki authoring did not become ready: {current}")
    print(f"preview_ready={current['status']}; generation={current.get('wiki_generation')}", flush=True)
    committed = _payload(session.post(
        BASE + "/api/knowledge/v1/publications",
        json={"operation_id": str(uuid4()), "preview_id": preview_id,
              "source_snapshot_sha256": current["source_snapshot_sha256"],
              "target_mode": "part"},
        headers=_headers(csrf), timeout=30,
    ), 201)
    if len(committed["results"]) != 1:
        raise AssertionError("The protected source produced an unexpected number of targets.")
    _verify_published(digest)


if __name__ == "__main__":
    main()
