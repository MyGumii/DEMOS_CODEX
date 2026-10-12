from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.app_notion import _is_nonadopted_internal_surface


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = PROJECT_ROOT / "notion_static"
PRODUCT_DECISION = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_product_decision.v1.json"
)


@pytest.mark.unit
def test_f0_p5_excluded_route_surface_is_exact_and_fail_closed() -> None:
    for path in (
        "/api/notion/ai/ask",
        "/api/notion/outlook/status",
        "/api/notion/outlook/oauth/start",
        "/public/notion/token-a",
        "/api/notion/pages/page-a/public_link",
        "/api/notion/pages/page-a/forms/form-a/submit",
    ):
        assert _is_nonadopted_internal_surface(path), path

    for path in (
        "/",
        "/api/notion/workspaces/workspace-a/pages/page-a",
        "/api/notion/workspaces/workspace-a/search",
        "/api/notion/workspaces/workspace-a/pages/page-a/databases/database-a",
    ):
        assert not _is_nonadopted_internal_surface(path), path


@pytest.mark.unit
def test_f0_p5_nonadopted_controls_are_hidden_or_unreachable_in_internal_ui() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "notion.js").read_text(encoding="utf-8")

    assert 'id="notionAiNavBtn"' in html
    assert 'id="notionAiNavBtn" title="Notion AI" aria-label="Notion AI" hidden aria-hidden="true"' in html
    assert 'id="aiPanel" class="panel" hidden aria-hidden="true"' in html
    assert 'id="publicShareSection" hidden aria-hidden="true"' in html
    assert 'if (mode === "ai") return;' in script
    assert 'if (mode === "agent") mode = "pages";' in script
    assert 'const hidden = State.navigation.authoritative === true;' in script
    assert 'item.kind !== "outlook_digest"' in script

    lowered = (html + script).lower()
    for forbidden_control in (
        "upgrade plan",
        "billing portal",
        "pricing page",
        "업그레이드하기",
        "요금제 변경",
    ):
        assert forbidden_control not in lowered


@pytest.mark.unit
def test_f0_p5_uniform_capability_and_adopted_database_denominator() -> None:
    decision = json.loads(PRODUCT_DECISION.read_text(encoding="utf-8"))
    features = decision["decisions"]["adopted_feature_denominator"]
    scale = decision["decisions"]["target_scale_and_deployment_topology"]
    bridge = (STATIC_ROOT / "notion_database_bridge.js").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "notion.js").read_text(encoding="utf-8")

    assert scale["product_quota_mode"] == "no_plan_or_user_feature_storage_quota"
    assert all(
        scale[key]["product_limit"] is None
        for key in ("pages_per_workspace", "blocks_per_page", "rows_per_database")
    )
    assert "pricing_billing_and_plan_entitlements" in features["excluded_and_hidden"]
    assert "external_calendar_and_mail_integrations" in features["deferred_and_hidden"]
    assert '"table", "board", "list", "gallery", "calendar", "timeline"' in bridge
    assert 'new Set(["table", "board", "calendar", "list", "gallery", "timeline"])' in script
    assert 'new Set(["chart", "dashboard", "form", "feed", "map"])' in script
    assert "DatabaseBridge.applyCanonicalDatabases(\n        projectedBlocks," in script
    assert "Array.from(databaseIds || [], value => String(value || \"\"))" in script
    assert "tasks.push(reloadCanonicalDatabases(databaseIds, { applyToBlocks }).then(loaded =>" in script
    assert "collaborativeDatabaseSyncPending()" in script
    assert "preservePendingDatabaseBlocks(projectedBlocks)" in script
    assert "reloadCanonicalDatabases(databaseIds, { applyToBlocks })" in script
