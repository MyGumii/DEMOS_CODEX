from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from flask import Flask
import pytest

from code_assist_v1.knowledge_core.canonical_routes import _Handlers
from code_assist_v1.knowledge_core.repositories.source_artifacts import (
    ItemSourceArtifactRecord,
)
from code_assist_v1.knowledge_core.services.source_artifacts import (
    ItemSourceArtifactDownload,
    ItemSourceArtifactPage,
)
from code_assist_v1.tools.contract_audit import ContractAuditor


pytestmark = pytest.mark.unit

SPACE_ID = "10000000-0000-4000-8000-000000000001"
ITEM_ID = "10000000-0000-4000-8000-000000000002"
REVISION_ID = "10000000-0000-4000-8000-000000000003"
ARTIFACT_ID = "10000000-0000-4000-8000-000000000004"
FILE_CONTENT = b"%PDF"
FILE_SHA256 = hashlib.sha256(FILE_CONTENT).hexdigest()
DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs" / "knowledge_spaces"
SCHEMA_PATH = DOCS_ROOT / "contracts" / "schemas" / "knowledge.schema.json"


class _SourceArtifacts:
    def list_for_item(self, request):
        assert request.selected_space_id == SPACE_ID
        assert request.item_id == ITEM_ID
        return ItemSourceArtifactPage(
            selected_space_id=SPACE_ID,
            item_id=ITEM_ID,
            corpus_closure_sha256="a" * 64,
            items=(
                ItemSourceArtifactRecord(
                    artifact_id=ARTIFACT_ID,
                    item_id=ITEM_ID,
                    source_revision_id=REVISION_ID,
                    origin_type="upload",
                    original_filename="근거 자료.pdf",
                    media_type="application/pdf",
                    size_bytes=4,
                    content_sha256=FILE_SHA256,
                    created_at_ms=1_700_000_000_000,
                ),
            ),
        )

    def download(self, request):
        assert request.artifact_id == ARTIFACT_ID
        return ItemSourceArtifactDownload(
            selected_space_id=SPACE_ID,
            item_id=ITEM_ID,
            artifact_id=ARTIFACT_ID,
            original_filename="근거 자료.pdf",
            media_type="application/pdf",
            size_bytes=4,
            content_sha256=FILE_SHA256,
            content=FILE_CONTENT,
        )


def _handlers() -> _Handlers:
    return _Handlers(
        scope_selection=None,
        conversations=None,
        retrieval=None,
        management_reads=None,
        agent_context=None,
        turn_executor=None,
        conversation_summarizer=None,
        principal_resolver=lambda: {"id": "local-admin", "scope_epoch": 1},
        source_artifacts=_SourceArtifacts(),
    )


def test_source_artifact_list_emits_exact_same_origin_download_url() -> None:
    app = Flask(__name__)
    with app.test_request_context("/"):
        response, status = _handlers().list_item_source_artifacts(SPACE_ID, ITEM_ID)
    assert status == 200
    payload = response.get_json()
    assert set(payload) == {"request_id", "corpus_closure_sha256", "item_id", "selected_view_space_id", "items"}
    assert set(payload["items"][0]) == {
        "artifact_id", "created_at", "download_url", "media_type", "origin_type",
        "original_filename", "size_bytes", "source_revision_id",
    }
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert ContractAuditor(DOCS_ROOT).validate_instance(
        payload, schema["$defs"]["ItemSourceArtifactListResponse"], SCHEMA_PATH,
    ) == []
    assert payload["items"][0]["original_filename"] == "근거 자료.pdf"
    assert payload["items"][0]["download_url"] == (
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/"
        f"source-artifacts/{ARTIFACT_ID}/download"
    )


def test_source_artifact_download_is_attachment_and_no_store() -> None:
    app = Flask(__name__)
    with app.test_request_context("/"):
        response = _handlers().download_item_source_artifact(
            SPACE_ID, ITEM_ID, ARTIFACT_ID
        )
    assert response.status_code == 200
    assert response.get_data() == b"%PDF"
    assert response.headers["Content-Type"].startswith("application/pdf")
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Original-Content-SHA256"] == hashlib.sha256(response.get_data()).hexdigest()
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Request-ID"]
    assert int(response.headers["Content-Length"]) == len(response.get_data()) == 4


@pytest.mark.parametrize("field,value", [
    ("source_revision_id", None), ("artifact_id", "not-a-uuid"),
    ("origin_type", "unregistered"), ("size_bytes", -1), ("size_bytes", 1.5),
    ("created_at", "not-a-date"), ("media_type", None),
    ("original_filename", "x" * 241),
    ("download_url", "https://foreign.invalid/source"),
    ("content_sha256", "a" * 64),
])
def test_sbeops_001_source_artifact_row_schema_rejects_drift(field: str, value: object) -> None:
    app = Flask(__name__)
    with app.test_request_context("/"):
        response, _ = _handlers().list_item_source_artifacts(SPACE_ID, ITEM_ID)
    row = copy.deepcopy(response.get_json()["items"][0])
    row[field] = value
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert ContractAuditor(DOCS_ROOT).validate_instance(row, schema["$defs"]["ItemSourceArtifact"], SCHEMA_PATH)


@pytest.mark.parametrize("valid_id", [SPACE_ID, ITEM_ID, ARTIFACT_ID])
@pytest.mark.parametrize("invalid_id", ["-" * 36, "100000000-000-4000-8000-000000000001"])
def test_sbeops_001_download_url_requires_each_canonical_uuid(valid_id: str, invalid_id: str) -> None:
    app = Flask(__name__)
    with app.test_request_context("/"):
        response, _ = _handlers().list_item_source_artifacts(SPACE_ID, ITEM_ID)
    row = response.get_json()["items"][0]
    row["download_url"] = row["download_url"].replace(valid_id, invalid_id)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert ContractAuditor(DOCS_ROOT).validate_instance(row, schema["$defs"]["ItemSourceArtifact"], SCHEMA_PATH)


@pytest.mark.parametrize("field", ["request_id", "corpus_closure_sha256", "item_id", "selected_view_space_id", "items"])
def test_sbeops_001_source_artifact_list_schema_requires_full_envelope(field: str) -> None:
    app = Flask(__name__)
    with app.test_request_context("/"):
        response, _ = _handlers().list_item_source_artifacts(SPACE_ID, ITEM_ID)
    payload = response.get_json()
    del payload[field]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert ContractAuditor(DOCS_ROOT).validate_instance(
        payload, schema["$defs"]["ItemSourceArtifactListResponse"], SCHEMA_PATH,
    )


def test_sbeops_001_source_artifact_list_allows_empty_results_without_pagination() -> None:
    app = Flask(__name__)
    with app.test_request_context("/"):
        response, _ = _handlers().list_item_source_artifacts(SPACE_ID, ITEM_ID)
    payload = response.get_json()
    payload["items"] = []
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    auditor = ContractAuditor(DOCS_ROOT)
    assert auditor.validate_instance(payload, schema["$defs"]["ItemSourceArtifactListResponse"], SCHEMA_PATH) == []
    payload["next_cursor"] = None
    assert auditor.validate_instance(payload, schema["$defs"]["ItemSourceArtifactListResponse"], SCHEMA_PATH)


@pytest.mark.parametrize("download", [False, True])
def test_sbeops_001_source_artifact_routes_reject_query_parameters(download: bool) -> None:
    app = Flask(__name__)
    with app.test_request_context("/?cursor=not-supported"):
        handlers = _handlers()
        response, status = (
            handlers.download_item_source_artifact(SPACE_ID, ITEM_ID, ARTIFACT_ID)
            if download else handlers.list_item_source_artifacts(SPACE_ID, ITEM_ID)
        )
    assert status == 400
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"
