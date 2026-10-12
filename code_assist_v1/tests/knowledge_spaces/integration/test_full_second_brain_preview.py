from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import zipfile

import pytest
from werkzeug.security import generate_password_hash

from code_assist_v1.tools import full_second_brain_preview as preview


pytestmark = pytest.mark.integration


class _TestSemanticJudge:
    """Deterministic infrastructure seam; production preview never imports it."""

    def __call__(self, request):
        if not request.candidates:
            return {"schema_version": 1, "selections": []}
        candidate = request.candidates[0]
        source_evidence = request.source_content_md[:64]
        target_evidence = candidate.content_md[:64]
        if not source_evidence or not target_evidence:
            return {"schema_version": 1, "selections": []}
        return {
            "schema_version": 1,
            "selections": [
                {
                    "candidate_item_id": candidate.candidate_item_id,
                    "direction": (
                        "symmetric"
                        if "symmetric" in request.allowed_directions
                        else request.allowed_directions[0]
                    ),
                    "edge_type": (
                        "related"
                        if "related" in request.allowed_relation_types
                        else request.allowed_relation_types[0]
                    ),
                    "model_grade": 3,
                    "source_evidence": source_evidence,
                    "target_evidence": target_evidence,
                }
            ],
        }


def _source_tree(root: Path) -> None:
    for name in ("knowledge", "user_data", "sessions", "workspace"):
        (root / name).mkdir(parents=True)
    wiki = root / "knowledge" / "admin" / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "sources" / "note.md").write_text("# Preview note\n", encoding="utf-8")
    (root / "user_data" / "admin").mkdir()
    runtime = root / ".runtime"
    runtime.mkdir()
    (runtime / "users.json").write_text(
        json.dumps(
            {
                "users": {
                    "admin": {
                        "active": True,
                        "created_at": 1_780_000_000,
                        "display_name": "Admin",
                        "must_change_password": False,
                        "password_hash": generate_password_hash("admin1234"),
                        "role": "admin",
                    },
                    "user01": {
                        "active": True,
                        "created_at": 1_780_000_001,
                        "display_name": "User 01",
                        "must_change_password": False,
                        "password_hash": generate_password_hash("user1234"),
                        "role": "user",
                    },
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "api_config.json").write_text(
        json.dumps(
            {
                "gguf": {"autoload_on_startup": True},
                "models": {
                    "gemma": {"env_id": "gemma", "model": "gemma", "url": "http://invalid.local"},
                    "qwen": {"env_id": "qwen", "model": "qwen", "url": "http://invalid.local"},
                    "glm": {"env_id": "glm", "model": "glm", "url": "http://invalid.local"},
                    "local": {"env_id": "local", "model": "local", "runtime": "gguf"},
                },
                "default_model_priority": ["gemma", "qwen", "glm", "local"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_full_preview_isolated_copy_model_order_and_canonical_admin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source_tree(source)
    target = tmp_path / "preview"
    monkeypatch.setattr(preview, "_package_root", lambda: source)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    for name in (
        "CODE_ASSIST_TEST_MODE",
        "CODE_ASSIST_TEST_DATA_ROOT",
        "CODE_ASSIST_DATA_ROOT",
        "CODE_ASSIST_STANDALONE",
        "CODE_ASSIST_PROCESS_ROLE",
        "CODE_ASSIST_API_CONFIG",
        "CODE_ASSIST_PORT",
        "CODE_ASSIST_AUTOLOAD_GGUF",
        "CODE_ASSIST_DOCUMENT_CONVERTERS",
        "CODE_ASSIST_ENABLE_CODEX_BRIDGE",
        "CODE_ASSIST_ALLOWED_ORIGINS",
        "CODE_ASSIST_CODEX_CWD",
        "CODE_ASSIST_CODEX_SANDBOX",
        "CODE_ASSIST_CODEX_EPHEMERAL",
        "CODE_ASSIST_CODEX_CLI_MODEL",
        "CODE_ASSIST_CODEX_REASONING_EFFORT",
        "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION",
        "CODE_ASSIST_SESSION_COOKIE_SECURE",
    ):
        # ``_configure_environment`` writes through ``os.environ`` directly.
        # Make even initially absent keys visible to monkeypatch so teardown
        # removes them instead of leaking preview policy into later tests.
        if name not in os.environ:
            monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    preview._configure_environment(target, port=10111, codex=True)

    marker = preview.prepare_preview_root(target, codex=True)
    assert marker["validation_seed_mode"] == "fixture"
    with pytest.raises(preview.FullPreviewError, match="validation-seed mode"):
        preview.prepare_preview_root(
            target,
            codex=True,
            clean_validation_root=True,
        )
    preview.assert_original_inputs_unchanged(marker)
    from code_assist_v1 import auth

    monkeypatch.setattr(auth, "AUTH_USERS_FILE", str(target / ".runtime" / "users.json"))
    monkeypatch.setattr(auth, "AUTH_AUDIT_FILE", str(target / ".runtime" / "audit.jsonl"))
    model_config = json.loads(
        (target / ".runtime" / "api_config.json").read_text(encoding="utf-8")
    )
    assert model_config["gguf"]["autoload_on_startup"] is False
    assert model_config["default_model_priority"] == [
        "gemma",
        "qwen",
        "glm",
        "local",
        "codex-local",
    ]
    assert model_config["models"]["codex-local"]["runtime"] == "codex"
    assert model_config["models"]["codex-local"]["codex_cli_model"] == "gpt-5.6-luna"
    assert (target / "knowledge" / "admin" / "wiki" / "sources" / "note.md").is_file()

    factory = preview._canonical_factory(target)
    ids = preview.seed_canonical_administration(factory, target)
    clean_projection = preview.seed_canonical_preview_knowledge(
        factory,
        target,
        ids,
        include_fixture_documents=False,
    )
    assert clean_projection == {
        "part": 0,
        "personal": 1,
        "responsibility": 0,
        "team": 0,
    }
    projection = preview.seed_canonical_preview_knowledge(factory, target, ids)
    assert projection == {
        "part": 3,
        "personal": 1,
        "responsibility": 1,
        "team": 1,
    }
    test_judge = _TestSemanticJudge()
    successor = preview.seed_canonical_successor_preview(
        factory,
        ids,
        semantic_judge=test_judge,
        semantic_model_provider="test-injected",
        semantic_model_name="deterministic-infrastructure-seam",
    )
    assert successor["aggregate_auto_edges"] >= 1
    assert successor["local_auto_edges"] >= 1
    assert successor["explicit_references"] == 1
    assert successor["read_only_distributions"] == 1
    restarted_ids = preview.seed_canonical_administration(factory, target)
    assert restarted_ids == ids
    assert preview.seed_canonical_preview_knowledge(factory, target, restarted_ids) == projection
    from code_assist_v1.knowledge_core.worker import DurableWorkKernel

    def reject_restart_claim(*_args, **_kwargs):
        pytest.fail("a preview restart must not claim post-baseline semantic jobs")

    with monkeypatch.context() as restart_patch:
        restart_patch.setattr(DurableWorkKernel, "claim_next", reject_restart_claim)
        assert preview.seed_canonical_successor_preview(
            factory,
            restarted_ids,
            semantic_judge=test_judge,
            semantic_model_provider="test-injected",
            semantic_model_name="deterministic-infrastructure-seam",
        ) == successor
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM app_user").fetchone()[0] == 4
        assert connection.execute("SELECT current_part_id FROM app_user WHERE id='admin'").fetchone()[0] == ids["part"]
        assert connection.execute("SELECT current_part_id FROM app_user WHERE id='user01'").fetchone()[0] == ids["part_b"]
        assert connection.execute("SELECT count(*) FROM knowledge_space").fetchone()[0] == 8
        assert connection.execute(
            "SELECT current_part_id FROM app_user WHERE id='preview-team-leader'"
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT current_part_id FROM app_user "
            "WHERE id='preview-responsibility-executive'"
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT home_kind FROM org_home_affiliation "
            "WHERE user_id='preview-team-leader' AND valid_to IS NULL"
        ).fetchone()[0] == "team"
        assert connection.execute(
            "SELECT home_kind FROM org_home_affiliation "
            "WHERE user_id='preview-responsibility-executive' AND valid_to IS NULL"
        ).fetchone()[0] == "responsibility"
        scope_epoch = connection.execute(
            "SELECT scope_epoch FROM app_user WHERE id='admin'"
        ).fetchone()[0]
    finally:
        connection.close()

    app = preview.build_preview_app(factory, marker)
    from code_assist_v1.knowledge_core.services.scope_selection import (
        ScopeSelectionRequest,
    )

    runtime = app.extensions["full_second_brain_preview_runtime"]
    assert runtime.route_bindings.turn_executor.provider_order == ("codex",)
    assert runtime.route_bindings.turn_executor.model_ids == ("codex-local",)
    scope_snapshot = runtime.services.require("scope_selection").read(
        ScopeSelectionRequest("admin", scope_epoch)
    )
    assert [row.kind for row in scope_snapshot.spaces] == [
        "personal",
        "part",
        "team",
        "responsibility",
    ]
    client = app.test_client()
    origin = "http://127.0.0.1:10111"
    login = client.post(
        "/api/code/auth/login",
        json={"user_id": "admin", "password": "admin1234"},
        headers={"Origin": origin},
        base_url=origin,
    )
    assert login.status_code == 200
    csrf_token = login.get_json()["csrf_token"]
    with client.session_transaction(base_url=origin) as session:
        session["code_assist_user"] = {
            "display_name": "Admin",
            "id": "admin",
            "is_admin": True,
            "login_at": 1_780_000_000,
            "must_change_password": False,
            "role": "admin",
        }
    homepage = client.get("/", base_url=origin)
    assert b'data-keep-legacy-graph="true"' in homepage.data
    homepage_text = homepage.get_data(as_text=True)
    assert 'id="composerAddMenu"' in homepage_text
    assert 'data-composer-add="upload"' in homepage_text
    assert 'data-composer-add="promote"' in homepage_text
    assert 'data-composer-add="attach"' in homepage_text
    assert 'id="kbPromoteChatBtn"' not in homepage_text
    assert 'id="kbUploadSourceBtn"' not in homepage_text
    knowledge_source = (Path(preview.__file__).resolve().parents[1] / "static" / "knowledge.js").read_text(encoding="utf-8")
    browser_source = (Path(preview.__file__).resolve().parents[1] / "static" / "knowledge_core_browser.js").read_text(encoding="utf-8")
    preview_source = Path(preview.__file__).read_text(encoding="utf-8")
    assert "def preview_judge" not in preview_source
    assert 'model_provider="local-deterministic-preview"' not in preview_source
    assert "await window.KnowledgeCoreBrowser.renderManagement(query);" in knowledge_source
    assert "deferLegacyPersonal" not in browser_source
    assert "SecondBrainGraph.useProvider(" in browser_source
    assert "readOnly: true" in browser_source
    assert 'fileInput.accept = ".docx,.pptx,.xlsx,.pdf,.md,.markdown' in knowledge_source
    assert 'title: "지식에 추가"' in knowledge_source
    assert 'makeSourceTab("upload", "자료 업로드")' in knowledge_source
    assert 'makeSourceTab("chat", "대화 지식화")' in knowledge_source
    assert 'targetTitle.textContent = "게시 위치"' in knowledge_source
    assert 'preferredTargetMode: selectedTarget' in knowledge_source
    request_headers = {"X-Request-ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
    spaces = client.get(
        "/api/knowledge/v1/spaces", base_url=origin, headers=request_headers
    )
    assert spaces.status_code == 200, spaces.get_json()
    rows = spaces.get_json()["items"]
    assert [row["kind"] for row in rows] == [
        "personal",
        "part",
        "team",
        "responsibility",
    ]
    assert rows[1]["org_path"] == [
        "AMHS담당",
        "Second Brain Preview Team",
        "Second Brain Preview Part",
    ]
    assert rows[2]["org_path"] == ["AMHS담당", "Second Brain Preview Team"]
    assert rows[3]["org_path"] == ["AMHS담당"]
    team_role = client.get(
        f"/api/admin/v2/org/teams/{ids['team']}/leader",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000020"},
        base_url=origin,
    )
    assert team_role.status_code == 200, team_role.get_json()
    assert team_role.get_json()["assignee_user_id"] == "preview-team-leader"
    assert team_role.get_json()["space_id"] == ids["team_space"]
    assert team_role.get_json()["write_enabled"] is True
    responsibility_role = client.get(
        f"/api/admin/v2/org/responsibilities/{ids['responsibility']}/executive",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000021"},
        base_url=origin,
    )
    assert responsibility_role.status_code == 200, responsibility_role.get_json()
    assert (
        responsibility_role.get_json()["assignee_user_id"]
        == "preview-responsibility-executive"
    )
    assert responsibility_role.get_json()["space_id"] == ids["responsibility_space"]
    assert responsibility_role.get_json()["write_enabled"] is True
    leader_client = app.test_client()
    leader_login = leader_client.post(
        "/api/code/auth/login",
        json={"user_id": "preview-team-leader", "password": "admin1234"},
        headers={"Origin": origin},
        base_url=origin,
    )
    assert leader_login.status_code == 200, leader_login.get_json()
    distribution_targets = leader_client.get(
        f"/api/knowledge/v2/spaces/{ids['team_space']}/distribution-targets",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000022"},
        base_url=origin,
    )
    assert distribution_targets.status_code == 200, distribution_targets.get_json()
    assert {item["org_unit_id"] for item in distribution_targets.get_json()["items"]} == {
        ids["part"],
        ids["part_b"],
    }
    distributions = leader_client.get(
        f"/api/knowledge/v2/spaces/{ids['team_space']}/distributions?limit=100",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000023"},
        base_url=origin,
    )
    assert distributions.status_code == 200, distributions.get_json()
    active_distributions = [
        item for item in distributions.get_json()["items"] if item["revoked_at"] is None
    ]
    assert len(active_distributions) == 1
    assert active_distributions[0]["target_org_unit_id"] == ids["part"]
    team_item_id = preview._uuid("preview-item:team:native-incident-coordination")
    team_item = client.get(
        f"/api/knowledge/v1/spaces/{ids['team_space']}/items/{team_item_id}",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000024"},
        base_url=origin,
    )
    assert team_item.status_code == 200, team_item.get_json()
    assert team_item.get_json()["selected_view_space_id"] == ids["team_space"]
    assert team_item.get_json()["source_space_id"] == ids["team_space"]
    assert len(team_item.get_json()["references"]) == 1
    explicit_reference = team_item.get_json()["references"][0]
    assert explicit_reference["availability_status"] == "available"
    assert explicit_reference["target"]["item_id"] == preview._uuid(
        "preview-item:part:shared-space-guide"
    )
    assert explicit_reference["target"]["newer_revision_available"] is False
    temporal = client.get(
        f"/api/knowledge/v2/spaces/{ids['team_space']}/items/{team_item_id}/temporal",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000025"},
        base_url=origin,
    )
    assert temporal.status_code == 200, temporal.get_json()
    assert temporal.get_json()["revision_id"] == preview._uuid(
        "preview-revision:team:native-incident-coordination"
    )
    assert temporal.get_json()["temporal_summary_sha256"]
    item_titles: dict[str, set[str]] = {}
    for row in rows:
        response = client.get(
            f"/api/knowledge/v1/spaces/{row['id']}/items",
            base_url=origin,
            headers=request_headers,
        )
        assert response.status_code == 200
        item_titles[row["kind"]] = {
            item["title"] for item in response.get_json()["items"]
        }
    assert item_titles == {
        "personal": {"Preview note"},
        "part": {
            "Part shared incident knowledge",
            "Part incident recovery runbook",
            "Team native incident coordination",
        },
        "team": {
            "Part shared incident knowledge",
            "Part incident recovery runbook",
            "Research Part recovery evidence",
            "Team native incident coordination",
        },
        "responsibility": {
            "Part shared incident knowledge",
            "Part incident recovery runbook",
            "Research Part recovery evidence",
            "Team native incident coordination",
            "Responsibility native operating policy",
        },
    }
    mutation_headers = {
        "Origin": origin,
        "X-CSRF-Token": csrf_token,
        "X-Request-ID": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    }
    wiki_page = client.get(
        "/api/knowledge/v2/personal-wikis?lifecycle_status=active&limit=50",
        base_url=origin,
    )
    assert wiki_page.status_code == 200, wiki_page.get_json()
    default_profile = next(
        item for item in wiki_page.get_json()["items"] if item["is_default"]
    )
    additional_wiki = client.post(
        "/api/knowledge/v2/personal-wikis",
        json={
            "display_name": "Exact project Wiki",
            "expected_row_version": default_profile["row_version"],
            "operation_id": "bbbbbbbb-0000-4000-8000-000000000001",
        },
        headers=mutation_headers,
        base_url=origin,
    )
    assert additional_wiki.status_code == 201, additional_wiki.get_json()
    additional_wiki_id = additional_wiki.get_json()["resource_id"]
    exact_wiki_spaces = client.get(
        "/api/knowledge/v1/spaces",
        query_string={"selected_space_id": additional_wiki_id},
        headers={"X-Request-ID": "bbbbbbbb-0000-4000-8000-000000000002"},
        base_url=origin,
    )
    assert exact_wiki_spaces.status_code == 200, exact_wiki_spaces.get_json()
    exact_rows = exact_wiki_spaces.get_json()["items"]
    assert len(exact_rows) == 5
    assert [row["name"] for row in exact_rows if row["id"] == additional_wiki_id] == [
        "Exact project Wiki"
    ]
    assert sum(row["kind"] == "personal" for row in exact_rows) == 2
    created = client.post(
        "/api/knowledge/v1/publication-previews",
        data={
            "operation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "source_type": "upload",
            "title": "Inherited Part note",
            "file": (
                io.BytesIO(b"# Inherited Part note\n\nVisible at every higher level.\n"),
                "inherited-part-note.md",
            ),
        },
        content_type="multipart/form-data",
        headers=mutation_headers,
        base_url=origin,
    )
    assert created.status_code == 202, created.get_json()
    preview_id = created.get_json()["preview_id"]
    ready = client.get(
        f"/api/knowledge/v1/publication-previews/{preview_id}",
        headers={"X-Request-ID": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"},
        base_url=origin,
    )
    assert ready.status_code == 200, ready.get_json()
    ready_body = ready.get_json()
    assert ready_body["status"] == "ready"
    assert ready_body["available_target_modes"] == ["personal", "part", "both"]
    committed = client.post(
        "/api/knowledge/v1/publications",
        json={
            "operation_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "preview_id": preview_id,
            "source_snapshot_sha256": ready_body["source_snapshot_sha256"],
            "target_mode": "part",
        },
        headers={
            "Origin": origin,
            "X-CSRF-Token": csrf_token,
            "X-Request-ID": "ffffffff-ffff-4fff-8fff-ffffffffffff",
        },
        base_url=origin,
    )
    assert committed.status_code == 201, committed.get_json()
    assert committed.get_json()["results"][0]["space_id"] == ids["part_space"]
    both_preview = client.post(
        "/api/knowledge/v1/publication-previews",
        data={
            "operation_id": "55555555-5555-4555-8555-555555555555",
            "source_type": "upload",
            "title": "Independent personal and Part copies",
            "file": (
                io.BytesIO(b"# Independent copies\n\nEach target owns its revision.\n"),
                "independent-copies.md",
            ),
        },
        content_type="multipart/form-data",
        headers={
            "Origin": origin,
            "X-CSRF-Token": csrf_token,
            "X-Request-ID": "66666666-6666-4666-8666-666666666666",
        },
        base_url=origin,
    )
    assert both_preview.status_code == 202, both_preview.get_json()
    both_preview_id = both_preview.get_json()["preview_id"]
    both_ready = client.get(
        f"/api/knowledge/v1/publication-previews/{both_preview_id}",
        headers={"X-Request-ID": "77777777-7777-4777-8777-777777777777"},
        base_url=origin,
    )
    assert both_ready.status_code == 200, both_ready.get_json()
    both_ready_body = both_ready.get_json()
    assert both_ready_body["status"] == "ready"
    both_commit = client.post(
        "/api/knowledge/v1/publications",
        json={
            "operation_id": "88888888-8888-4888-8888-888888888888",
            "personal_space_id": additional_wiki_id,
            "preview_id": both_preview_id,
            "source_snapshot_sha256": both_ready_body["source_snapshot_sha256"],
            "target_mode": "both",
        },
        headers={
            "Origin": origin,
            "X-CSRF-Token": csrf_token,
            "X-Request-ID": "99999999-9999-4999-8999-999999999999",
        },
        base_url=origin,
    )
    assert both_commit.status_code == 201, both_commit.get_json()
    both_targets = both_commit.get_json()["results"]
    assert {target["space_id"] for target in both_targets} == {
        additional_wiki_id,
        ids["part_space"],
    }
    assert len({target["item_id"] for target in both_targets}) == 2
    exact_wiki_items = client.get(
        f"/api/knowledge/v1/spaces/{additional_wiki_id}/items",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000010"},
        base_url=origin,
    )
    assert exact_wiki_items.status_code == 200, exact_wiki_items.get_json()
    assert "Independent personal and Part copies" in {
        item["title"] for item in exact_wiki_items.get_json()["items"]
    }
    default_personal_id = next(
        row["id"] for row in rows if row["kind"] == "personal"
    )
    default_wiki_items = client.get(
        f"/api/knowledge/v1/spaces/{default_personal_id}/items",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000011"},
        base_url=origin,
    )
    assert default_wiki_items.status_code == 200, default_wiki_items.get_json()
    assert "Independent personal and Part copies" not in {
        item["title"] for item in default_wiki_items.get_json()["items"]
    }
    docx_bytes = io.BytesIO()
    with zipfile.ZipFile(docx_bytes, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Canonical DOCX body</w:t></w:r></w:p></w:body>
</w:document>""",
        )
    docx_bytes.seek(0)
    docx_preview = client.post(
        "/api/knowledge/v1/publication-previews",
        data={
            "operation_id": "aaaaaaaa-0000-4000-8000-000000000001",
            "source_type": "upload",
            "title": "Canonical DOCX preview",
            "file": (
                docx_bytes,
                "canonical-preview.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        },
        content_type="multipart/form-data",
        headers={
            "Origin": origin,
            "X-CSRF-Token": csrf_token,
            "X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000002",
        },
        base_url=origin,
    )
    assert docx_preview.status_code == 202, docx_preview.get_json()
    docx_preview_id = docx_preview.get_json()["preview_id"]
    docx_ready = client.get(
        f"/api/knowledge/v1/publication-previews/{docx_preview_id}",
        headers={"X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000003"},
        base_url=origin,
    )
    assert docx_ready.status_code == 200, docx_ready.get_json()
    assert docx_ready.get_json()["status"] == "ready"
    docx_commit = client.post(
        "/api/knowledge/v1/publications",
        json={
            "operation_id": "aaaaaaaa-0000-4000-8000-000000000004",
            "preview_id": docx_preview_id,
            "source_snapshot_sha256": docx_ready.get_json()["source_snapshot_sha256"],
            "target_mode": "part",
        },
        headers={
            "Origin": origin,
            "X-CSRF-Token": csrf_token,
            "X-Request-ID": "aaaaaaaa-0000-4000-8000-000000000005",
        },
        base_url=origin,
    )
    assert docx_commit.status_code == 201, docx_commit.get_json()
    for index, row in enumerate(rows[1:], start=1):
        inherited_items = client.get(
            f"/api/knowledge/v1/spaces/{row['id']}/items",
            headers={
                "X-Request-ID": f"11111111-1111-4111-8111-{index:012d}"
            },
            base_url=origin,
        )
        assert inherited_items.status_code == 200, inherited_items.get_json()
        inherited_body = inherited_items.get_json()
        assert inherited_body["items"]
        assert all(
            item["selected_view_space_id"] == row["id"]
            for item in inherited_body["items"]
        )
        assert all(
            item["space_id"] == item["source_space_id"]
            for item in inherited_body["items"]
        )
        assert "Inherited Part note" in {
            item["title"] for item in inherited_body["items"]
        }
        assert "Canonical DOCX preview" in {
            item["title"] for item in inherited_body["items"]
        }
        graph = client.get(
            f"/api/knowledge/v1/spaces/{row['id']}/graph",
            headers={
                "X-Request-ID": f"22222222-2222-4222-8222-{index:012d}"
            },
            base_url=origin,
        )
        assert graph.status_code == 200, graph.get_json()
        graph_body = graph.get_json()
        assert graph_body["space_id"] == row["id"]
        assert "Inherited Part note" in {
            node["title"] for node in graph_body["nodes"]
        }
        aggregate_edges = [
            edge for edge in graph_body["edges"] if edge["edge_scope"] == "aggregate"
        ]
        if row["kind"] == "part":
            assert aggregate_edges == []
        else:
            assert aggregate_edges
            assert any(
                edge["model_provider"] == "test-injected"
                and edge["model_name"] == "deterministic-infrastructure-seam"
                and edge["source_evidence_span_sha256"]
                and edge["target_evidence_span_sha256"]
                for edge in aggregate_edges
            ), aggregate_edges
        activity = client.get(
            f"/api/knowledge/v1/spaces/{row['id']}/activity",
            headers={
                "X-Request-ID": f"33333333-3333-4333-8333-{index:012d}"
            },
            base_url=origin,
        )
        assert activity.status_code == 200, activity.get_json()
        activity_body = activity.get_json()
        assert activity_body["items"]
        assert all(
            event["selected_view_space_id"] == row["id"]
            for event in activity_body["items"]
        )
        graph_activity = [
            event
            for event in activity_body["items"]
            if event["action"]
            in {"aggregate_edge.create", "semantic_edge.automatic_activate"}
        ]
        assert graph_activity
        assert all(
            event["item_id"] is None
            and event["base_revision_id"] is None
            and event["new_revision_id"] is None
            for event in graph_activity
        )
        findings = client.get(
            f"/api/knowledge/v1/spaces/{row['id']}/curation/findings",
            headers={
                "X-Request-ID": f"44444444-4444-4444-8444-{index:012d}"
            },
            base_url=origin,
        )
        assert findings.status_code == 200, findings.get_json()
        findings_body = findings.get_json()
        assert all(
            finding["selected_view_space_id"] == row["id"]
            for finding in findings_body["items"]
        )
    for path in (
        "/api/code/knowledge/graph",
        "/api/code/knowledge/system_doc/index",
        "/api/code/knowledge/system_doc/log",
    ):
        response = client.get(path, base_url=origin)
        assert response.status_code == 200, (path, response.get_json(silent=True))
