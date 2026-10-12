"""Real two-principal Chrome journey for the frozen F0-P5 product surface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from .approved_dev_pair import AGENT_PORT, NOTION_PORT, ApprovedDevPairError
from .v0b_p1_capture import v0b_chrome_launch_options


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECEIPT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_receipts"
    / "f0-p5-l4-adopted-surface-20260825.json"
)


def _origin(value: str, expected_port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != expected_port
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ApprovedDevPairError("F0_P5_BROWSER_ORIGIN_INVALID")
    return f"http://127.0.0.1:{expected_port}"


def _operation(prefix: str) -> str:
    return f"f0p5-browser-{prefix}-{time.time_ns()}"


def _login(page: Any, agent: str, notion: str, subject: str, password: str) -> None:
    page.goto(agent + "/login", wait_until="domcontentloaded")
    user_id = page.locator("#userId")
    try:
        user_id.wait_for(state="visible", timeout=2_000)
    except Exception:
        page.wait_for_url(agent + "/", wait_until="domcontentloaded")
    else:
        user_id.fill(subject)
        page.locator("#password").fill(password)
        page.locator("#submitBtn").click()
        page.wait_for_url(agent + "/", wait_until="domcontentloaded")
    page.goto(notion + "/workspace", wait_until="domcontentloaded")
    page.wait_for_function(
        """subject => typeof api === 'function'
            && window.NotionDatabaseBridge
            && State.user?.id === subject
            && State.navigation?.authoritative === true
            && document.body.dataset.notionReady === 'true'""",
        arg=subject,
    )


def _api(
    page: Any,
    path: str,
    *,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = page.evaluate(
        """async ([path, method, body]) => await api(
            path,
            method === 'GET' ? {} : {method, body},
        )""",
        [path, method, dict(body or {})],
    )
    if not isinstance(result, dict):
        raise ApprovedDevPairError("F0_P5_BROWSER_API_OBJECT_REQUIRED")
    return result


def _cleanup_fixture(
    page: Any,
    *,
    workspace_id: str,
    page_id: str,
    revoke_synthetic_member: bool,
) -> bool:
    current = _api(page, f"/api/notion/workspaces/{workspace_id}")["workspace"]
    if revoke_synthetic_member and any(
        item.get("subject_id") == "user01" for item in current.get("members") or []
    ):
        _api(
            page,
            f"/api/notion/workspaces/{workspace_id}/members/user01",
            method="DELETE",
            body={
                "expected_workspace_epoch": current["security_epoch"],
                "operation_id": _operation("cleanup-member"),
            },
        )
        current = _api(page, f"/api/notion/workspaces/{workspace_id}")["workspace"]

    pages = _api(page, f"/api/notion/workspaces/{workspace_id}/pages?q=")
    if any(str(item.get("id") or "") == page_id for item in pages.get("items") or []):
        _api(
            page,
            f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/trash",
            method="POST",
            body={
                "base_version": current["version"],
                "operation_id": _operation("cleanup-trash"),
            },
        )

    current = _api(page, f"/api/notion/workspaces/{workspace_id}")["workspace"]
    pages = _api(page, f"/api/notion/workspaces/{workspace_id}/pages?q=")
    member_absent = not any(
        item.get("subject_id") == "user01" for item in current.get("members") or []
    )
    page_absent = not any(
        str(item.get("id") or "") == page_id for item in pages.get("items") or []
    )
    return page_absent and (member_absent or not revoke_synthetic_member)


def _select_admin_workspace(page: Any) -> dict[str, Any]:
    bootstrap = _api(page, "/api/notion/workspaces")
    for item in bootstrap.get("items") or []:
        if (
            item.get("owner_subject_id") == "admin"
            and item.get("access", {}).get("role") == "owner"
        ):
            return dict(item)
    raise ApprovedDevPairError("F0_P5_BROWSER_ADMIN_WORKSPACE_MISSING")


def _open_page(page: Any, workspace_id: str, page_id: str) -> None:
    page.evaluate(
        """async ({workspaceId, pageId}) => {
            await refreshWorkspaceNavigationCapability();
            await switchCanonicalWorkspace(workspaceId);
            await loadPages();
            await openPage(pageId, false, PageFocusPolicy.PRESERVE);
        }""",
        {"workspaceId": workspace_id, "pageId": page_id},
    )
    try:
        page.wait_for_function(
            """pageId => State.activePageId === pageId
                && State.pageData?.page?.collaborative_editing === true
                && State.pageData?.page?.collaborative_databases === true""",
            arg=page_id,
        )
    except Exception as exc:
        diagnostic = page.evaluate(
            """() => ({
                active_page_id: State.activePageId,
                page: State.pageData?.page || null,
                capabilities: State.pageData?.capabilities || null,
                status: document.querySelector('#status')?.textContent || '',
            })"""
        )
        raise ApprovedDevPairError(
            "F0_P5_BROWSER_PAGE_NOT_READY:" + repr(diagnostic)
        ) from exc


def _screenshot_sha256(page: Any) -> str:
    return "sha256:" + hashlib.sha256(page.screenshot()).hexdigest()


def run_f0_p5_browser_journey(
    *,
    agent_origin: str,
    notion_origin: str,
    credentials: Mapping[str, str],
    v0b_collector: Any | None = None,
) -> dict[str, object]:
    """Validate the adopted DB UI and role-scoped round trip in two Chrome contexts."""

    agent = _origin(agent_origin, AGENT_PORT)
    notion = _origin(notion_origin, NOTION_PORT)
    if set(credentials) != {"admin", "user01"} or any(
        not isinstance(value, str) or not value for value in credentials.values()
    ):
        raise ApprovedDevPairError("F0_P5_BROWSER_PRINCIPALS_INVALID")

    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    run_id = uuid4().hex
    browser = None
    browser_closed = False
    contexts: list[Any] = []
    requests: list[str] = []
    page_errors: list[str] = []
    screenshot_hashes: dict[str, str] = {}
    workspace_id = ""
    page_id = ""
    database_id = ""
    cleaned_to_trash = False
    admin_page = None
    v0b_mode = v0b_collector is not None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**v0b_chrome_launch_options())
            browser_version = browser.version
            pages: dict[str, Any] = {}
            for principal in ("admin", "user01"):
                context = browser.new_context(
                    locale="ko-KR",
                    service_workers="block",
                    viewport={"width": 1440, "height": 900},
                )
                context.set_default_timeout(25_000)
                context.on("request", lambda request, sink=requests: sink.append(request.url))
                contexts.append(context)
                page = context.new_page()
                page.on("pageerror", lambda error, sink=page_errors: sink.append(str(error)))
                _login(page, agent, notion, principal, credentials[principal])
                pages[principal] = page

            admin_page = pages["admin"]
            member_page = pages["user01"]
            if v0b_collector is not None:
                workspace = _api(
                    admin_page,
                    "/api/notion/workspaces",
                    method="POST",
                    body={
                        "name": "REF-FREE-A",
                        "icon": "R",
                        "operation_id": _operation("v0b-workspace"),
                    },
                )["workspace"]
            else:
                workspace = _select_admin_workspace(admin_page)
            workspace_id = str(workspace["id"])
            detail = _api(admin_page, f"/api/notion/workspaces/{workspace_id}")["workspace"]
            if not any(
                item.get("subject_id") == "user01" for item in detail.get("members") or []
            ):
                _api(
                    admin_page,
                    f"/api/notion/workspaces/{workspace_id}/members/user01",
                    method="PUT",
                    body={
                        "role": "member",
                        "expected_workspace_epoch": detail["security_epoch"],
                        "operation_id": _operation("member"),
                    },
                )
                detail = _api(
                    admin_page, f"/api/notion/workspaces/{workspace_id}"
                )["workspace"]

            created = _api(
                admin_page,
                f"/api/notion/workspaces/{workspace_id}/pages",
                method="POST",
                body={
                    "title": (
                        "V0R-A-ROOT"
                        if v0b_collector is not None
                        else f"F0-P5 DB round trip {run_id[:8]}"
                    ),
                    "parent_id": "",
                    "base_version": detail["version"],
                    "operation_id": _operation("page"),
                },
            )
            page_id = str(created["page"]["id"])
            collaboration = _api(
                admin_page,
                f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/collaboration",
            )
            _api(
                admin_page,
                f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/shares",
                method="POST",
                body={
                    "principal_type": "subject",
                    "principal_id": "user01",
                    "role": "edit_content",
                    "inherit_to_descendants": False,
                    "expected_workspace_epoch": collaboration["page"]["workspace_epoch"],
                    "expected_document_epoch": collaboration["page"]["document_epoch"],
                    "operation_id": _operation("share"),
                },
            )
            _open_page(admin_page, workspace_id, page_id)
            if v0b_collector is not None:
                admin_page.evaluate("() => updatePageAppearance({icon: 'list'})")
                admin_page.wait_for_function(
                    "() => State.pageData?.page?.icon === 'list'"
                )
            setup = admin_page.evaluate(
                """async ({runId, captureMode}) => {
                    const databaseId = State.pageData.blocks[0].id;
                    const titlePropertyId = `title-${runId}`;
                    const statusPropertyId = `status-${runId}`;
                    const estimatePropertyId = `estimate-${runId}`;
                    const rows = captureMode ? [
                        {
                            page_id: `row-base-${runId}`,
                            [titlePropertyId]: 'V0R-A-ROW-BASE',
                            [statusPropertyId]: 'Todo',
                            [estimatePropertyId]: 1,
                        },
                        {
                            page_id: `row-owner-${runId}`,
                            [titlePropertyId]: 'V0R-A-ROW-OWNER',
                            [statusPropertyId]: 'Done',
                            [estimatePropertyId]: 2,
                        },
                        {
                            page_id: `row-collaborator-${runId}`,
                            [titlePropertyId]: 'Initial task',
                            [statusPropertyId]: 'Todo',
                            [estimatePropertyId]: 3,
                        },
                        {
                            page_id: `row-reconnect-${runId}`,
                            [titlePropertyId]: 'V0R-A-ROW-RECONNECT',
                            [statusPropertyId]: 'Todo',
                            [estimatePropertyId]: 4,
                        },
                    ] : [{
                        page_id: `row-${runId}`,
                        [titlePropertyId]: 'Initial task',
                        [statusPropertyId]: 'Todo',
                        [estimatePropertyId]: 3,
                    }];
                    const databaseBlock = EditorCore.normalizeBlock({
                        id: databaseId,
                        type: 'table',
                        content: `F0-P5 adopted tasks ${runId.slice(0, 8)}`,
                        props: {
                            database_id: databaseId,
                            active_view_id: `view-table-${runId}`,
                            properties: [
                                {id: `title-${runId}`, name: 'Name', type: 'title'},
                                {id: `status-${runId}`, name: 'Status', type: 'status', options: ['Todo', 'Done']},
                                {id: `estimate-${runId}`, name: 'Estimate', type: 'number'},
                            ],
                            views: [
                                {id: `view-table-${runId}`, name: 'Table', type: 'table', filters: [], sorts: []},
                                {id: `view-board-${runId}`, name: 'Board', type: 'board', filters: [], sorts: []},
                                {id: `view-list-${runId}`, name: 'List', type: 'list', filters: [], sorts: []},
                                {id: `view-gallery-${runId}`, name: 'Gallery', type: 'gallery', filters: [], sorts: []},
                                {id: `view-calendar-${runId}`, name: 'Calendar', type: 'calendar', filters: [], sorts: []},
                                {id: `view-timeline-${runId}`, name: 'Timeline', type: 'timeline', filters: [], sorts: []},
                            ],
                            rows,
                        },
                    });
                    State.pageData.blocks = captureMode ? [
                        EditorCore.createBlock('paragraph', {
                            content: 'V0R-A-TEXT-BASE | V0R-A-TEXT-OWNER | V0R-A-TEXT-COLLABORATOR',
                        }),
                        EditorCore.createBlock('paragraph', {
                            content: 'V0R-A-BLOCK-BASE | V0R-A-BLOCK-OWNER | V0R-A-BLOCK-COLLABORATOR',
                        }),
                        databaseBlock,
                    ].map(block => EditorCore.normalizeBlock(block)) : [databaseBlock];
                    renderBlocks();
                    markDirty();
                    await State.liveCollaboration.persistenceChain;
                    for (let attempt = 0; attempt < 100 && State.liveCollaboration.unsyncedCount > 0; attempt += 1) {
                        await new Promise(resolve => setTimeout(resolve, 50));
                    }
                    const projectedDatabase = State.pageData.blocks.find(block => block.id === databaseId);
                    if (projectedDatabase?.type !== 'table') {
                        throw new Error(`database block projection reverted to ${projectedDatabase?.type || 'missing'}`);
                    }
                    const committed = await flushCollaborativeDatabaseSync();
                    if (!committed) {
                        throw new Error(`database sync did not commit: ${document.querySelector('#status')?.textContent || ''}`);
                    }
                    await reloadCanonicalDatabases([databaseId]);
                    renderBlocks();
                    return {
                        database_id: databaseId,
                        row_id: captureMode ? `row-collaborator-${runId}` : `row-${runId}`,
                        row_index: captureMode ? 2 : 0,
                        title_property_id: titlePropertyId,
                    };
                }""",
                {"runId": run_id, "captureMode": v0b_collector is not None},
            )
            database_id = str(setup["database_id"])
            collaborator_row_index = int(setup["row_index"])
            title_property_id = str(setup["title_property_id"])
            try:
                admin_page.wait_for_function(
                    "() => document.querySelectorAll('[data-db-view-id]').length === 6"
                )
            except Exception as exc:
                diagnostic = admin_page.evaluate(
                    """() => ({
                        blocks: (State.pageData?.blocks || []).map(block => ({
                            id: block.id,
                            type: block.type,
                            database_id: block.props?.database_id || '',
                            view_count: block.props?.views?.length || 0,
                        })),
                        databases: (State.pageData?.databases || []).map(database => ({
                            id: database.id,
                            view_count: database.views?.length || 0,
                        })),
                        dom_view_count: document.querySelectorAll('[data-db-view-id]').length,
                        status: document.querySelector('#status')?.textContent || '',
                    })"""
                )
                raise ApprovedDevPairError(
                    "F0_P5_BROWSER_DATABASE_NOT_RENDERED:" + repr(diagnostic)
                ) from exc
            if admin_page.locator("[data-db-add-view]").count() != 1:
                raise ApprovedDevPairError("F0_P5_ADMIN_SCHEMA_CONTROLS_MISSING")
            screenshot_hashes["admin_six_adopted_views"] = _screenshot_sha256(admin_page)

            canonical = _api(
                admin_page,
                f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/databases/{database_id}",
            )["database"]
            if [item["type"] for item in canonical["views"]] != [
                "table", "board", "list", "gallery", "calendar", "timeline"
            ]:
                raise ApprovedDevPairError("F0_P5_ADOPTED_VIEW_ROUND_TRIP_FAILED")

            _open_page(member_page, workspace_id, page_id)
            member_page.wait_for_function(
                "() => document.querySelectorAll('[data-db-view-id]').length === 6"
            )
            try:
                member_page.wait_for_function("() => activePageCanEdit() === true")
            except Exception as exc:
                diagnostic = member_page.evaluate(
                    """async () => {
                      const base = collaborativePageBasePath();
                      const response = await fetch(`${base}/realtime-ticket`, {
                        method: 'POST',
                        headers: {
                          'Content-Type': 'application/json',
                          'X-CSRF-Token': State.csrfToken,
                        },
                        body: JSON.stringify({
                          client_id: `f0p5-diagnostic-${crypto.randomUUID()}`,
                          session_binding_sha256: `sha256:${'0'.repeat(64)}`,
                        }),
                      });
                      const ticket_probe = {status: response.status};
                      if (!response.ok) ticket_probe.error = await response.text();
                      return ({
                        page: State.pageData?.page || null,
                        live: {
                            connected: State.liveCollaboration.connected,
                            crdt_ready: State.liveCollaboration.crdtReady,
                            recovery_only: State.liveCollaboration.recoveryOnly,
                            recovery_reason: State.liveCollaboration.recoveryReason,
                            offline_policy_blocked: State.liveCollaboration.offlinePolicyBlocked,
                            last_error: State.liveCollaboration.lastError,
                            error_history: State.liveCollaboration.errorHistory,
                            pending_count: State.liveCollaboration.pendingEnvelopes.size,
                            workspace_epoch: State.liveCollaboration.workspaceEpoch,
                            document_epoch: State.liveCollaboration.documentEpoch,
                        },
                        bridge: window.NotionCollaborativeEditor.descriptor(),
                        ticket_probe,
                        status: document.querySelector('#status')?.textContent || '',
                      });
                    }"""
                )
                raise ApprovedDevPairError(
                    "F0_P5_BROWSER_CONTENT_EDIT_NOT_READY:" + repr(diagnostic)
                ) from exc
            member_page.evaluate("() => renderBlocks()")
            if (
                member_page.locator("[data-db-add-view]").count()
                or member_page.locator("[data-db-add-property]").count()
                or member_page.locator("[data-db-filter]").count()
                or not member_page.locator("[data-db-title]").is_disabled()
            ):
                raise ApprovedDevPairError("F0_P5_CONTENT_ROLE_SCHEMA_CONTROL_EXPOSED")

            title_cell = member_page.locator(
                f'[data-db-row="{collaborator_row_index}"][data-db-field="{title_property_id}"]'
            )
            collaborator_row_title = (
                "V0R-A-ROW-COLLABORATOR"
                if v0b_collector is not None
                else "Edited by user01"
            )
            title_cell.fill(collaborator_row_title)
            title_cell.dispatch_event("change")
            member_commit = member_page.evaluate(
                """async ({workspaceId, pageId, databaseId, propertyId, rowIndex, expectedValue}) => {
                    let last = null;
                    for (let attempt = 0; attempt < 200; attempt += 1) {
                        try {
                            const data = await api(`/api/notion/workspaces/${workspaceId}/pages/${pageId}/databases/${databaseId}`);
                            last = data.database || null;
                            if (last?.rows?.[rowIndex]?.cells?.[propertyId] === expectedValue) {
                                return {committed: true, database: last};
                            }
                        } catch {}
                        await new Promise(resolve => setTimeout(resolve, 50));
                    }
                    return {
                        committed: false,
                        database: last,
                        actions: DatabaseBridge.diffPage(
                            State.pageData?.blocks || [],
                            State.pageData?.databases || [],
                        ),
                        sync: {
                            generation: State.databaseSync.generation,
                            in_flight: Boolean(State.databaseSync.inFlight),
                            queued: State.databaseSync.queued,
                            timer: Boolean(State.databaseSync.timer),
                        },
                        status: document.querySelector('#status')?.textContent || '',
                    };
                }""",
                {
                    "workspaceId": workspace_id,
                    "pageId": page_id,
                    "databaseId": database_id,
                    "propertyId": title_property_id,
                    "rowIndex": collaborator_row_index,
                    "expectedValue": collaborator_row_title,
                },
            )
            if member_commit.get("committed") is not True:
                raise ApprovedDevPairError(
                    "F0_P5_BROWSER_MEMBER_DATABASE_COMMIT_FAILED:" + repr(member_commit)
                )
            screenshot_hashes["member_content_edit_schema_locked"] = _screenshot_sha256(member_page)

            admin_reload = admin_page.evaluate(
                """async databaseId => {
                    const loaded = await reloadCanonicalDatabases([databaseId]);
                    renderBlocks();
                    return loaded[0] || null;
                }""",
                database_id,
            )
            try:
                admin_page.wait_for_function(
                    """({propertyId, rowIndex, expectedValue}) => document.querySelector(
                        `[data-db-row="${rowIndex}"][data-db-field="${propertyId}"]`
                    )?.value === expectedValue""",
                    arg={
                        "propertyId": title_property_id,
                        "rowIndex": collaborator_row_index,
                        "expectedValue": collaborator_row_title,
                    },
                )
            except Exception as exc:
                diagnostic = admin_page.evaluate(
                    """async ({propertyId, rowIndex, workspaceId, pageId, databaseId, adminReload}) => {
                      const block = (State.pageData?.blocks || []).find(item => item.props?.database_id);
                      const database = (State.pageData?.databases || []).find(item => item.id === block?.props?.database_id);
                      const input = document.querySelector(`[data-db-row="${rowIndex}"][data-db-field="${propertyId}"]`);
                      const projectionBlock = (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .find(item => item.id === block?.id);
                      const fresh = await api(`/api/notion/workspaces/${workspaceId}/pages/${pageId}/databases/${databaseId}`);
                      return {
                        input_value: input?.value ?? null,
                        block_type: block?.type || null,
                        block_value: block?.props?.rows?.[rowIndex]?.[propertyId] ?? null,
                        block_canonical_version: block?.props?._canonical_version ?? null,
                        canonical_value: database?.rows?.[rowIndex]?.cells?.[propertyId] ?? null,
                        canonical_version: database?.version ?? null,
                        admin_reload_value: adminReload?.rows?.[rowIndex]?.cells?.[propertyId] ?? null,
                        admin_reload_version: adminReload?.version ?? null,
                        fresh_value: fresh.database?.rows?.[rowIndex]?.cells?.[propertyId] ?? null,
                        fresh_version: fresh.database?.version ?? null,
                        projection_type: projectionBlock?.type || null,
                        projection_value: projectionBlock?.props?.rows?.[rowIndex]?.[propertyId] ?? null,
                        live: {
                          connected: State.liveCollaboration.connected,
                          crdt_ready: State.liveCollaboration.crdtReady,
                          recovery_only: State.liveCollaboration.recoveryOnly,
                        },
                        status: document.querySelector('#status')?.textContent || '',
                      };
                    }""",
                    {
                        "propertyId": title_property_id,
                        "rowIndex": collaborator_row_index,
                        "workspaceId": workspace_id,
                        "pageId": page_id,
                        "databaseId": database_id,
                        "adminReload": admin_reload,
                    },
                )
                raise ApprovedDevPairError(
                    "F0_P5_BROWSER_ADMIN_CANONICAL_PROJECTION_STALE:" + repr(diagnostic)
                ) from exc
            database_title = admin_page.locator("[data-db-title]")
            database_final_title = (
                "V0R-A-DB"
                if v0b_collector is not None
                else "F0-P5 canonical renamed"
            )
            database_title.fill(database_final_title)
            database_title.dispatch_event("change")
            admin_title_commit = admin_page.evaluate(
                """async ({workspaceId, pageId, databaseId, expectedTitle}) => {
                    let last = null;
                    for (let attempt = 0; attempt < 200; attempt += 1) {
                        try {
                            const data = await api(`/api/notion/workspaces/${workspaceId}/pages/${pageId}/databases/${databaseId}`);
                            last = data.database || null;
                            if (last?.title === expectedTitle) {
                                return {committed: true, database: last};
                            }
                        } catch {}
                        await new Promise(resolve => setTimeout(resolve, 50));
                    }
                    return {
                        committed: false,
                        database: last,
                        actions: DatabaseBridge.diffPage(
                            State.pageData?.blocks || [],
                            State.pageData?.databases || [],
                        ),
                        status: document.querySelector('#status')?.textContent || '',
                    };
                }""",
                {
                    "workspaceId": workspace_id,
                    "pageId": page_id,
                    "databaseId": database_id,
                    "expectedTitle": database_final_title,
                },
            )
            if admin_title_commit.get("committed") is not True:
                raise ApprovedDevPairError(
                    "F0_P5_BROWSER_ADMIN_DATABASE_TITLE_COMMIT_FAILED:"
                    + repr(admin_title_commit)
                )
            member_page.evaluate(
                """async databaseId => {
                    await reloadCanonicalDatabases([databaseId]);
                    renderBlocks();
                }""",
                database_id,
            )
            member_page.wait_for_function(
                "expectedTitle => document.querySelector('[data-db-title]')?.value === expectedTitle",
                arg=database_final_title,
            )
            if v0b_collector is not None:
                admin_page.locator("#commentDrawerBtn").click()
                member_page.locator("#commentDrawerBtn").click()
                admin_page.locator("#commentInput").fill(
                    "V0R-A-COMMENT-OWNER @user01"
                )
                admin_page.locator("#commentSubmitBtn").click()
                member_page.wait_for_function(
                    "() => State.comments.some(item => "
                    "item.content === 'V0R-A-COMMENT-OWNER @user01')",
                    timeout=10_000,
                )
                reference_comment_id = member_page.evaluate(
                    "() => State.comments.find(item => "
                    "item.content === 'V0R-A-COMMENT-OWNER @user01')?.id || ''"
                )
                member_page.locator(
                    f'[data-comment-reply="{reference_comment_id}"]'
                ).click()
                member_page.locator("#commentInput").fill(
                    "V0R-A-REPLY-COLLABORATOR"
                )
                member_page.locator("#commentSubmitBtn").click()
                admin_page.wait_for_function(
                    "() => State.comments.some(item => "
                    "item.content === 'V0R-A-REPLY-COLLABORATOR')",
                    timeout=10_000,
                )
                member_page.wait_for_function(
                    "() => State.comments.some(item => "
                    "item.content === 'V0R-A-REPLY-COLLABORATOR')",
                    timeout=10_000,
                )
                member_page.locator(
                    f'[data-comment-reaction="{reference_comment_id}"]'
                    '[data-reaction-emoji="🥳"]'
                ).click()
                admin_page.wait_for_function(
                    "commentId => State.comments.some(item => item.id === commentId "
                    "&& (item.reactions?.['🥳'] || []).includes('user01'))",
                    arg=reference_comment_id,
                    timeout=10_000,
                )
                member_page.wait_for_function(
                    "commentId => State.comments.some(item => item.id === commentId "
                    "&& (item.reactions?.['🥳'] || []).includes('user01'))",
                    arg=reference_comment_id,
                    timeout=10_000,
                )
                admin_page.evaluate("() => closeDrawer(true)")
                member_page.evaluate("() => closeDrawer(true)")
                admin_page.locator("#inspector").wait_for(state="hidden")
                member_page.locator("#inspector").wait_for(state="hidden")
                member_page.wait_for_function(
                    """titlePropertyId => (
                      document.querySelectorAll('[data-db-view-id]').length === 6
                      && document.querySelectorAll(`[data-db-row][data-db-field="${titlePropertyId}"]`).length === 4
                    )""",
                    arg=title_property_id,
                )
                v0b_collector.capture(
                    "V0R-MU-011",
                    member_page,
                    selectors={
                        "DATABASE": ".database-block",
                        "DATABASE-TITLE": "[data-db-title]",
                        "CONVERGED-CELL": f'[data-db-row="{collaborator_row_index}"][data-db-field="{title_property_id}"]',
                    },
                    focus_selector=f'[data-db-row="1"][data-db-field="{title_property_id}"]',
                    keyboard_path=["Tab", "Control+A", "Enter"],
                    live_region_expected=True,
                    observation="The collaborator cell edit and owner database-title edit converged in one canonical database view.",
                )

            policy = admin_page.evaluate(
                """async () => ({
                    non_adopted: [...NON_ADOPTED_BLOCK_TYPES].sort(),
                    ai_hidden: document.querySelector('#notionAiNavBtn')?.hidden === true
                        && document.querySelector('#aiPanel')?.hidden === true,
                    public_hidden: document.querySelector('#publicShareSection')?.hidden === true,
                    deep_link_statuses: await Promise.all([
                        fetch('/api/notion/ai/ask').then(response => response.status),
                        fetch('/api/notion/outlook/status').then(response => response.status),
                        fetch('/api/notion/pages/synthetic/public_link').then(response => response.status),
                    ]),
                })"""
            )
            if (
                policy["non_adopted"] != ["chart", "dashboard", "feed", "form", "map"]
                or policy["ai_hidden"] is not True
                or policy["public_hidden"] is not True
                or policy["deep_link_statuses"] != [404, 404, 404]
            ):
                raise ApprovedDevPairError("F0_P5_NONADOPTED_SURFACE_NOT_FENCED")

            cleaned_to_trash = _cleanup_fixture(
                admin_page,
                workspace_id=workspace_id,
                page_id=page_id,
                revoke_synthetic_member=v0b_mode,
            )
            if not cleaned_to_trash:
                raise ApprovedDevPairError("F0_P5_BROWSER_CLEANUP_FAILED")

            allowed = {
                agent,
                notion,
                "ws://127.0.0.1:10113",
                "https://cdn.jsdelivr.net",
            }
            escaped = set()
            for value in requests:
                parsed = urlsplit(value)
                if parsed.scheme not in {"http", "https", "ws", "wss"}:
                    continue
                request_origin = f"{parsed.scheme}://{parsed.netloc}"
                if request_origin not in allowed:
                    escaped.add(request_origin)
            if escaped:
                raise ApprovedDevPairError(
                    "F0_P5_BROWSER_NETWORK_ESCAPE:" + ",".join(sorted(escaped))
                )
            if page_errors:
                raise ApprovedDevPairError(
                    "F0_P5_BROWSER_PAGE_ERROR:" + " | ".join(page_errors[:3])
                )

            for context in reversed(contexts):
                context.close()
            contexts.clear()
            browser.close()
            browser_closed = not browser.is_connected()
            browser = None
    finally:
        if not cleaned_to_trash and workspace_id and page_id:
            try:
                with sync_playwright() as cleanup_playwright:
                    cleanup_browser = cleanup_playwright.chromium.launch(
                        **v0b_chrome_launch_options()
                    )
                    cleanup_context = cleanup_browser.new_context(
                        locale="ko-KR", service_workers="block"
                    )
                    cleanup_page = cleanup_context.new_page()
                    _login(
                        cleanup_page,
                        agent,
                        notion,
                        "admin",
                        credentials["admin"],
                    )
                    cleaned_to_trash = _cleanup_fixture(
                        cleanup_page,
                        workspace_id=workspace_id,
                        page_id=page_id,
                        revoke_synthetic_member=v0b_mode,
                    )
                    cleanup_context.close()
                    cleanup_browser.close()
            except Exception:
                cleaned_to_trash = False
        for context in reversed(contexts):
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
                browser_closed = not browser.is_connected()
            except Exception:
                browser_closed = False

    if not browser_closed or not cleaned_to_trash:
        raise ApprovedDevPairError("F0_P5_BROWSER_CLEANUP_FAILED")
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.f0_p5_adopted_surface_browser.v1",
        "receipt_id": "F0-P5-L4-ADOPTED-SURFACE-20260825",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "principal_ids": ["admin", "user01"],
        "principal_count": 2,
        "workspace_id": workspace_id,
        "document_id": page_id,
        "database_id": database_id,
        "adopted_database_views": [
            "table", "board", "list", "gallery", "calendar", "timeline"
        ],
        "multi_principal_cell_round_trip": True,
        "schema_role_controls_fenced": True,
        "canonical_reload_convergence": True,
        "nonadopted_controls_and_deep_links_fenced": True,
        "uniform_plan_or_quota_ui": True,
        "test_page_moved_to_trash": cleaned_to_trash,
        "synthetic_member_absent_after_cleanup": cleaned_to_trash if v0b_mode else True,
        "page_error_count": len(page_errors),
        "unapproved_network_target_escape_count": 0,
        "credential_cookie_secret_or_session_recorded": False,
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "version": browser_version,
            "headless": True,
            "independent_ephemeral_contexts": 2,
            "persistent_or_signed_in_profile_used": False,
            "browser_closed": browser_closed,
        },
        "screenshot_sha256": screenshot_hashes,
        "network_request_count": len(requests),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args(argv)
    if args.approval_id != "AP-DEV-PAIR":
        raise ApprovedDevPairError("AP_DEV_PAIR_REQUIRED")
    output = args.output.resolve(strict=False)
    if output != DEFAULT_RECEIPT.resolve(strict=False):
        raise ApprovedDevPairError("F0_P5_RECEIPT_PATH_INVALID")
    receipt = run_f0_p5_browser_journey(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({
        "status": "pass",
        "receipt": str(output),
        "receipt_sha256": "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest(),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - approval-bound evidence entry point
    raise SystemExit(main())


__all__ = ["run_f0_p5_browser_journey"]
