"""One synthetic encrypted-PDF HTTP probe of the active v50 evidence path.

This intentionally publishes a second copy only into the isolated admin
personal Wiki. It never reads company documents or prints the test password.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

import requests


BASE = "http://127.0.0.1:10521"
ROOT = Path(__file__).resolve().parents[2] / "data-root"
SOURCE = Path(__file__).resolve().parents[1] / "output/pdf/synthetic-amhs-encrypted-surrogate.pdf"
TITLE = "10521 v50 protected provenance smoke"
CANARIES = ("C-17", "CV-04", "S-42", "09:35", "09:42", "7.4", "1.6")


def _payload(response, *accepted):
    value = response.json()
    if response.status_code not in accepted:
        raise AssertionError(f"Unexpected HTTP {response.status_code}: {value}")
    return value


def _evidence_rows():
    path = ROOT / "core/knowledge_core.db"
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """SELECT p.*,a.raw_sha256,e.extractor_name,b.content_md,
                      target.resolved_revision_id
               FROM protected_extraction_provenance p
               JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
               JOIN source_artifact a ON a.id=p.artifact_id
               JOIN publication_preview preview ON preview.artifact_id=a.id
               JOIN publication_operation operation ON operation.artifact_id=a.id
               JOIN publication_target target ON target.publication_operation_id=operation.id
               JOIN knowledge_revision_body b ON b.revision_id=target.resolved_revision_id
               WHERE a.original_filename=?
                 AND json_extract(preview.ready_result_json,'$.title')=?""",
            (SOURCE.name, TITLE),
        )]
    finally:
        connection.close()


def _verify(digest):
    rows = _evidence_rows()
    if len(rows) != 1:
        raise AssertionError(f"Expected one v50 protected source, found {len(rows)}.")
    row = rows[0]
    if (row["original_sha256"] != row["raw_sha256"]
            or row["original_sha256"] != digest
            or row["derivative_sha256"] == digest
            or row["source_kind"] != "pdf_standard_encryption"
            or row["opening_method"] != "standard_pdf_owner_password_v1"
            or row["expected_locations"] != 1
            or row["visual_coverage"] != "not_proven"
            or row["extractor_name"] != "canonical-approved-owner-pdf"):
        raise AssertionError("The retained v50 provenance differs from the opened source.")
    coverage = json.loads(row["coverage_json"])
    if coverage != [{"ordinal": 1, "status": "complete",
                    "visual_present": False, "visual_examined": False}]:
        raise AssertionError("The retained page coverage differs.")
    if not all(fact in row["content_md"] for fact in CANARIES):
        raise AssertionError("The published Wiki omitted source facts.")
    print(json.dumps({
        "status": "pass", "artifact_id": row["artifact_id"],
        "revision_id": row["resolved_revision_id"],
        "original_sha256": digest,
        "derivative_sha256": row["derivative_sha256"],
        "coverage": coverage, "wiki_canaries_retained": len(CANARIES),
    }, sort_keys=True))


def main():
    raw = SOURCE.read_bytes()
    digest = sha256(raw).hexdigest()
    if _evidence_rows():
        _verify(digest)
        return
    session = requests.Session()
    password = os.environ.get("SB10521_ADMIN_PASSWORD") or sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise ValueError("The isolated admin test credential is required.")
    login = _payload(session.post(
        BASE + "/api/code/auth/login",
        json={"user_id": "admin", "password": password},
        headers={"Origin": BASE}, timeout=15,
    ), 200)
    csrf = login["csrf_token"]
    def headers():
        return {"Origin": BASE, "X-CSRF-Token": csrf, "X-Request-ID": str(uuid4())}
    created = _payload(session.post(
        BASE + "/api/knowledge/v1/publication-previews",
        data={"operation_id": str(uuid4()), "source_type": "upload", "title": TITLE},
        files={"file": (SOURCE.name, raw, "application/pdf")},
        headers=headers(), timeout=30,
    ), 202)
    preview_id = created["preview_id"]
    for _ in range(240):
        current = _payload(session.get(
            BASE + f"/api/knowledge/v1/publication-previews/{preview_id}", timeout=15,
        ), 200)
        if current["status"] in {"failed", "cancelled", "expired"}:
            raise AssertionError(f"The protected preview failed: {current}")
        if current["status"] == "ready" and current.get("publication_ready"):
            break
        time.sleep(2)
    else:
        raise AssertionError("The protected preview did not become publishable.")
    _payload(session.post(
        BASE + "/api/knowledge/v1/publications",
        json={"operation_id": str(uuid4()), "preview_id": preview_id,
              "source_snapshot_sha256": current["source_snapshot_sha256"],
              "target_mode": "personal"},
        headers=headers(), timeout=30,
    ), 201)
    _verify(digest)


if __name__ == "__main__":
    main()
