"""Real Chrome journey for the E1-P2 host-bound collaborative editor."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .approved_dev_pair import AGENT_PORT, NOTION_PORT, ApprovedDevPairError
from .c0_p2_browser import _seed_v0b_reference_body
from .v0b_p1_capture import v0b_chrome_launch_options


def _origin(value: str, port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != port
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ApprovedDevPairError("E1_P2_BROWSER_ORIGIN_INVALID")
    return f"http://127.0.0.1:{port}"


def _login(page: Any, agent: str, notion: str, subject: str, password: str) -> None:
    page.goto(agent + "/login", wait_until="domcontentloaded")
    user_id = page.locator("#userId")
    try:
        user_id.wait_for(state="visible", timeout=2_000)
    except Exception:
        page.wait_for_url(agent + "/", wait_until="domcontentloaded")
    else:
        try:
            user_id.fill(subject)
            page.locator("#password").fill(password)
            page.locator("#submitBtn").click()
            page.wait_for_url(agent + "/", wait_until="domcontentloaded")
        except Exception:
            if page.url.rstrip("/") != agent:
                raise
    page.goto(notion + "/workspace", wait_until="domcontentloaded")
    page.wait_for_function(
        """subject => typeof api === 'function'
            && window.NotionCollaborativeEditor
            && document.querySelector('#codeAgentLink')?.href.endsWith(':10111/')
            && State.user?.id === subject
            && document.body.dataset.notionReady === 'true'""",
        arg=subject,
    )


def _api(page: Any, path: str, *, method: str = "GET", body: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = page.evaluate(
        """async ([path, method, body]) => {
            const options = method === 'GET' ? {} : {method, body};
            return await api(path, options);
        }""",
        [path, method, dict(body or {})],
    )
    if not isinstance(value, dict):
        raise ApprovedDevPairError("E1_P2_BROWSER_API_OBJECT_REQUIRED")
    return value


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


def _operation(prefix: str) -> str:
    return f"e1p2-browser-{prefix}-{time.time_ns()}"


def _select_admin_workspace(page: Any) -> dict[str, Any]:
    bootstrap = _api(page, "/api/notion/workspaces")
    if bootstrap.get("capabilities", {}).get("collaborative_editing") is not True:
        raise ApprovedDevPairError("E1_P2_BROWSER_CAPABILITY_DISABLED")
    items = bootstrap.get("items") or []
    for item in items:
        if item.get("owner_subject_id") == "admin" and item.get("access", {}).get("role") == "owner":
            return dict(item)
    raise ApprovedDevPairError("E1_P2_BROWSER_ADMIN_WORKSPACE_MISSING")


def _open_collaborative_page(page: Any, workspace_id: str, page_id: str) -> None:
    page.evaluate(
        """async ([workspaceId, pageId]) => {
            await refreshWorkspaceNavigationCapability();
            await switchCanonicalWorkspace(workspaceId);
            await loadPages();
            await openPage(pageId, false, PageFocusPolicy.PRESERVE);
        }""",
        [workspace_id, page_id],
    )
    try:
        page.wait_for_function(
            """pageId => window.NotionCollaborativeEditor.descriptor().ready
                && window.NotionCollaborativeEditor.descriptor().page_id === pageId
                && document.querySelector('.block-editor')?.getAttribute('contenteditable') === 'true'""",
            arg=page_id,
        )
    except Exception as exc:
        diagnostic = page.evaluate(
            """() => ({
                bridge: window.NotionCollaborativeEditor?.descriptor?.(),
                status: document.querySelector('#status')?.textContent || '',
                recovery: document.querySelector('#collaborationRecoveryBanner')?.textContent || '',
                active_page: typeof State === 'object' ? State.activePageId : '',
                live: typeof State === 'object' ? {
                    connected: State.liveCollaboration.connected,
                    initial_replay: State.liveCollaboration.initialReplay,
                    crdt_ready: State.liveCollaboration.crdtReady,
                    unsynced_count: State.liveCollaboration.unsyncedCount,
                    recovery_only: State.liveCollaboration.recoveryOnly,
                    offline_policy_blocked: State.liveCollaboration.offlinePolicyBlocked,
                } : null,
                contenteditable: document.querySelector('.block-editor')?.getAttribute('contenteditable'),
            })"""
        )
        raise ApprovedDevPairError(
            "E1_P2_BROWSER_EDITOR_NOT_READY:" + json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)
        ) from exc


def _raw_outbox_state(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """async () => await new Promise((resolve, reject) => {
            const request = indexedDB.open('code-assist-notion-collaboration-v1', 1);
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const database = request.result;
                const transaction = database.transaction(['outbox', 'keys'], 'readonly');
                const outbox = transaction.objectStore('outbox').getAll();
                const keys = transaction.objectStore('keys').getAll();
                transaction.onerror = () => reject(transaction.error);
                transaction.oncomplete = () => {
                    const records = outbox.result || [];
                    const keyRecords = keys.result || [];
                    resolve({
                        count: records.length,
                        raw_json: JSON.stringify(records),
                        ciphertext_count: records.filter(item => typeof item.ciphertext === 'string' && item.ciphertext.length > 20).length,
                        key_count: keyRecords.length,
                        non_extractable_key_count: keyRecords.filter(item => item.key && item.key.extractable === false).length,
                    });
                    database.close();
                };
            };
        })"""
    )


def _projection_text(page: Any) -> str:
    return str(
        page.evaluate(
            """() => (window.NotionCollaborativeEditor.projection()?.blocks || [])
                .map(block => block.content || '').join('\\n')"""
        )
    )


def run_e1_p2_browser_journey(
    *,
    agent_origin: str,
    notion_origin: str,
    credentials: Mapping[str, str],
    v0b_collector: Any | None = None,
) -> dict[str, object]:
    """Exercise encrypted close/reopen recovery and two-principal convergence."""

    agent = _origin(agent_origin, AGENT_PORT)
    notion = _origin(notion_origin, NOTION_PORT)
    if set(credentials) != {"admin", "user01"} or any(not value for value in credentials.values()):
        raise ApprovedDevPairError("E1_P2_BROWSER_PRINCIPALS_INVALID")

    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    browser = None
    contexts: list[Any] = []
    page_errors: list[str] = []
    requests: list[str] = []
    created_workspace_id = ""
    created_page_id = ""
    created_child_page_id = ""
    cleaned_to_trash = False
    browser_closed = False
    encrypted_record_count = 0
    non_extractable_key_count = 0
    remote_cursor_visible = False
    final_text = ""
    admin_page = None
    v0b_mode = v0b_collector is not None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**v0b_chrome_launch_options())
            browser_version = browser.version
            admin_context = browser.new_context(locale="ko-KR", service_workers="block", viewport={"width": 1440, "height": 900})
            user_context = browser.new_context(locale="ko-KR", service_workers="block", viewport={"width": 1440, "height": 900})
            contexts.extend([admin_context, user_context])
            for context in contexts:
                context.set_default_timeout(20_000)
                context.on("request", lambda request, sink=requests: sink.append(request.url))

            admin_page = admin_context.new_page()
            user_page = user_context.new_page()
            admin_page.on("pageerror", lambda error: page_errors.append(str(error)))
            user_page.on("pageerror", lambda error: page_errors.append(str(error)))
            _login(admin_page, agent, notion, "admin", credentials["admin"])
            _login(user_page, agent, notion, "user01", credentials["user01"])

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
            created_workspace_id = str(workspace["id"])
            detail = _api(admin_page, f"/api/notion/workspaces/{created_workspace_id}")["workspace"]
            members = detail.get("members") or []
            if not any(item.get("subject_id") == "user01" for item in members):
                _api(
                    admin_page,
                    f"/api/notion/workspaces/{created_workspace_id}/members/user01",
                    method="PUT",
                    body={
                        "role": "member",
                        "expected_workspace_epoch": detail["security_epoch"],
                        "operation_id": _operation("member"),
                    },
                )
                detail = _api(admin_page, f"/api/notion/workspaces/{created_workspace_id}")["workspace"]

            created = _api(
                admin_page,
                f"/api/notion/workspaces/{created_workspace_id}/pages",
                method="POST",
                body={
                    "title": (
                        "V0R-A-ROOT"
                        if v0b_collector is not None
                        else "E1-P2 암호화 복구 검증"
                    ),
                    "parent_id": "",
                    "base_version": detail["version"],
                    "operation_id": _operation("page"),
                },
            )
            created_page_id = str(created["page"]["id"])
            if v0b_collector is not None:
                detail = _api(
                    admin_page,
                    f"/api/notion/workspaces/{created_workspace_id}",
                )["workspace"]
                child = _api(
                    admin_page,
                    f"/api/notion/workspaces/{created_workspace_id}/pages",
                    method="POST",
                    body={
                        "title": "REF-FREE-A",
                        "parent_id": created_page_id,
                        "base_version": detail["version"],
                        "operation_id": _operation("v0b-child-page"),
                    },
                )
                created_child_page_id = str(child["page"]["id"])
            collaboration = _api(
                admin_page,
                f"/api/notion/workspaces/{created_workspace_id}/pages/{created_page_id}/collaboration",
            )
            _api(
                admin_page,
                f"/api/notion/workspaces/{created_workspace_id}/pages/{created_page_id}/shares",
                method="POST",
                body={
                    "principal_type": "subject",
                    "principal_id": "user01",
                    "role": "edit",
                    "inherit_to_descendants": False,
                    "expected_workspace_epoch": collaboration["page"]["workspace_epoch"],
                    "expected_document_epoch": collaboration["page"]["document_epoch"],
                    "operation_id": _operation("share"),
                },
            )
            _open_collaborative_page(admin_page, created_workspace_id, created_page_id)
            _open_collaborative_page(user_page, created_workspace_id, created_page_id)

            if v0b_collector is not None:
                _seed_v0b_reference_body(
                    admin_page,
                    child_page_id=created_child_page_id,
                    cumulative=True,
                )
                admin_page.evaluate(
                    """async () => {
                      await updatePageAppearance({icon: 'list'});
                      for (let attempt = 0; attempt < 200; attempt += 1) {
                        const descriptor = window.NotionCollaborativeEditor?.descriptor?.();
                        if (
                          descriptor?.ready
                          && descriptor.page_id === State.activePageId
                          && State.liveCollaboration.crdtReady
                        ) break;
                        await new Promise(resolve => setTimeout(resolve, 50));
                      }
                      const projection = window.NotionCollaborativeEditor?.projection?.();
                      if (!projection || projection.page_id !== State.activePageId) {
                        throw new Error('V0B_REFERENCE_SEED_EDITOR_NOT_READY');
                      }
                      applyEditorHostProjection(projection);
                    }"""
                )
                user_page.wait_for_function(
                    """() => {
                      const blocks = window.NotionCollaborativeEditor.projection()?.blocks || [];
                      return State.liveCollaboration.unsyncedCount === 0
                        && blocks.length >= 2
                        && blocks.some(block => block.content.includes('V0R-A-TEXT-BASE'));
                    }""",
                    timeout=30_000,
                )
                admin_page.locator("#commentDrawerBtn").click()
                user_page.locator("#commentDrawerBtn").click()
                admin_page.locator("#commentInput").fill(
                    "V0R-A-COMMENT-OWNER @user01"
                )
                admin_page.locator("#commentSubmitBtn").click()
                user_page.wait_for_function(
                    "() => State.comments.some(item => "
                    "item.content === 'V0R-A-COMMENT-OWNER @user01')",
                    timeout=10_000,
                )
                reference_comment_id = user_page.evaluate(
                    "() => State.comments.find(item => "
                    "item.content === 'V0R-A-COMMENT-OWNER @user01')?.id || ''"
                )
                user_page.locator(
                    f'[data-comment-reply="{reference_comment_id}"]'
                ).click()
                user_page.locator("#commentInput").fill(
                    "V0R-A-REPLY-COLLABORATOR"
                )
                user_page.locator("#commentSubmitBtn").click()
                admin_page.wait_for_function(
                    "() => State.comments.some(item => "
                    "item.content === 'V0R-A-REPLY-COLLABORATOR')",
                    timeout=10_000,
                )
                user_page.locator(
                    f'[data-comment-reaction="{reference_comment_id}"]'
                    '[data-reaction-emoji="🥳"]'
                ).click()
                admin_page.wait_for_function(
                    "commentId => State.comments.some(item => item.id === commentId "
                    "&& (item.reactions?.['🥳'] || []).includes('user01'))",
                    arg=reference_comment_id,
                    timeout=10_000,
                )
                user_page.locator(".block-row:nth-child(1) .block-editor").click()
                user_page.keyboard.press("Home")
                user_page.evaluate("() => sendPresenceHeartbeat()")
                admin_page.wait_for_selector(
                    ".remote-collaboration-caret", state="visible"
                )
                v0b_collector.capture(
                    "V0R-MU-008",
                    admin_page,
                    selectors={
                        "PAGE-EDITOR": "#pageEditorGroup",
                        "REMOTE-CURSOR": ".remote-collaboration-caret",
                        "PRESENCE": "#presenceAvatars",
                    },
                    focus_selector=".block-row:nth-child(1) .block-editor",
                    keyboard_path=["Tab", "ArrowRight", "Shift+ArrowLeft"],
                    live_region_expected=True,
                    observation="The owner rendered the collaborator's remote synthetic caret over the durable base body before concurrent edits.",
                )
                remote_cursor_visible = True
                admin_page.evaluate(
                    """async () => {
                      const block = EditorCore.createBlock('paragraph');
                      block.content = '';
                      State.pageData.blocks.splice(0, 1, EditorCore.normalizeBlock(block));
                      renderBlocks();
                      markDirty();
                      await State.liveCollaboration.persistenceChain;
                      for (let attempt = 0; attempt < 200 && State.liveCollaboration.unsyncedCount > 0; attempt += 1) {
                        await new Promise(resolve => setTimeout(resolve, 50));
                      }
                    }"""
                )
                user_page.wait_for_function(
                    """() => {
                      const blocks = window.NotionCollaborativeEditor.projection()?.blocks || [];
                      return State.liveCollaboration.unsyncedCount === 0
                        && blocks.length === 3
                        && blocks[0]?.type === 'paragraph'
                        && blocks[0]?.content === '';
                    }""",
                    timeout=30_000,
                )
            user_context.set_offline(True)
            user_editor = user_page.locator(".block-row:nth-child(1) .block-editor")
            user_editor.click()
            user_edit_text = (
                "V0R-A-TEXT-COLLABORATOR"
                if v0b_collector is not None
                else "사용자 오프라인"
            )
            if v0b_collector is not None:
                user_editor.fill(user_edit_text)
            else:
                user_editor.fill(user_edit_text)
            user_page.wait_for_function(
                "text => (window.NotionCollaborativeEditor.projection()?.blocks || []).some(block => block.content.includes(text))",
                arg=user_edit_text,
            )
            user_page.wait_for_function(
                """async () => await new Promise(resolve => {
                    const request = indexedDB.open('code-assist-notion-collaboration-v1', 1);
                    request.onsuccess = () => {
                        const database = request.result;
                        const transaction = database.transaction('outbox', 'readonly');
                        const count = transaction.objectStore('outbox').count();
                        count.onsuccess = () => { resolve(count.result > 0); database.close(); };
                    };
                    request.onerror = () => resolve(false);
                })"""
            )
            raw = _raw_outbox_state(user_page)
            encrypted_record_count = int(raw["count"])
            non_extractable_key_count = int(raw["non_extractable_key_count"])
            raw_json = str(raw["raw_json"])
            forbidden_plaintext = ["user01", created_workspace_id, created_page_id, user_edit_text]
            if (
                encrypted_record_count < 1
                or int(raw["ciphertext_count"]) != encrypted_record_count
                or int(raw["key_count"]) < 1
                or non_extractable_key_count != int(raw["key_count"])
                or any(value in raw_json for value in forbidden_plaintext)
            ):
                raise ApprovedDevPairError("E1_P2_BROWSER_OUTBOX_NOT_ENCRYPTED")

            try:
                admin_page.wait_for_function(
                    "() => document.querySelector('.block-editor')?.getAttribute('contenteditable') === 'true'"
                )
            except Exception as exc:
                diagnostic = admin_page.evaluate(
                    """() => ({
                            bridge: window.NotionCollaborativeEditor.descriptor(),
                            page_can_edit: State.pageData?.page?.can_edit,
                            collaborative_editing: State.pageData?.page?.collaborative_editing,
                            active_can_edit: activePageCanEdit(),
                            connected: State.liveCollaboration.connected,
                            crdt_ready: State.liveCollaboration.crdtReady,
                            recovery_only: State.liveCollaboration.recoveryOnly,
                            offline_policy_blocked: State.liveCollaboration.offlinePolicyBlocked,
                            status: document.querySelector('#status')?.textContent || '',
                        })"""
                )
                raise ApprovedDevPairError(
                    "E1_P2_BROWSER_ADMIN_EDITOR_LOCKED:"
                    + json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)
                ) from exc
            admin_editor = admin_page.locator(".block-row:nth-child(1) .block-editor")
            admin_editor.click()
            admin_edit_text = (
                "V0R-A-TEXT-OWNER"
                if v0b_collector is not None
                else "관리자 온라인"
            )
            if v0b_collector is not None:
                admin_editor.fill(admin_edit_text)
            else:
                admin_editor.fill(admin_edit_text)
            try:
                admin_page.wait_for_function(
                    """text => State.liveCollaboration.unsyncedCount === 0
                        && (window.NotionCollaborativeEditor.projection()?.blocks || [])
                          .some(block => block.content.includes(text))""",
                    arg=admin_edit_text,
                )
            except Exception as exc:
                diagnostic = admin_page.evaluate(
                    """() => ({
                        projection: window.NotionCollaborativeEditor.projection()?.blocks?.map(block => block.content || '').join('\\n'),
                        connected: State.liveCollaboration.connected,
                        crdt_ready: State.liveCollaboration.crdtReady,
                        unsynced_count: State.liveCollaboration.unsyncedCount,
                        pending: [...State.liveCollaboration.pendingEnvelopes.values()].map(item => ({
                            operation_id: item.operation_id,
                            client_sequence: item.client_sequence,
                            acknowledgement_sequence: item.acknowledgement_sequence,
                            workspace_epoch: item.workspace_epoch,
                            document_epoch: item.document_epoch,
                        })),
                        client_sequence: State.liveCollaboration.clientSequence,
                        durable_cursor: State.liveCollaboration.durableCursor,
                        send_queue_active: State.liveCollaboration.sendQueueActive,
                        recovery_only: State.liveCollaboration.recoveryOnly,
                        last_error: State.liveCollaboration.lastError,
                        error_history: State.liveCollaboration.errorHistory,
                        status: document.querySelector('#status')?.textContent || '',
                    })"""
                )
                raise ApprovedDevPairError(
                    "E1_P2_BROWSER_ADMIN_SYNC_TIMEOUT:"
                    + json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)
                ) from exc

            user_page.close()
            user_context.set_offline(False)
            user_page = user_context.new_page()
            user_page.on("pageerror", lambda error: page_errors.append(str(error)))
            _login(user_page, agent, notion, "user01", credentials["user01"])
            _open_collaborative_page(user_page, created_workspace_id, created_page_id)
            convergence_markers = {
                "ownerText": admin_edit_text,
                "collaboratorText": user_edit_text,
            }
            user_page.wait_for_function(
                """({ownerText, collaboratorText}) => {
                    const text = (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .map(block => block.content || '').join('\\n');
                    return State.liveCollaboration.unsyncedCount === 0
                        && text.includes(ownerText)
                        && text.includes(collaboratorText);
                }""",
                arg=convergence_markers,
            )
            admin_page.wait_for_function(
                """({ownerText, collaboratorText}) => {
                    const text = (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .map(block => block.content || '').join('\\n');
                    return text.includes(ownerText) && text.includes(collaboratorText);
                }""",
                arg=convergence_markers,
            )
            final_text = _projection_text(admin_page)
            if final_text != _projection_text(user_page):
                raise ApprovedDevPairError("E1_P2_BROWSER_CONVERGENCE_FAILED")
            if v0b_collector is not None:
                canonical_converged_text = (
                    "V0R-A-TEXT-BASE | V0R-A-TEXT-OWNER | "
                    "V0R-A-TEXT-COLLABORATOR"
                )
                admin_page.locator(".block-row:nth-child(1) .block-editor").fill(
                    canonical_converged_text
                )
                admin_page.wait_for_function(
                    """text => State.liveCollaboration.unsyncedCount === 0
                      && (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .some(block => block.content === text)""",
                    arg=canonical_converged_text,
                    timeout=15_000,
                )
                user_page.wait_for_function(
                    """text => State.liveCollaboration.unsyncedCount === 0
                      && (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .some(block => block.content === text)""",
                    arg=canonical_converged_text,
                    timeout=15_000,
                )
                final_text = _projection_text(admin_page)
                if final_text != _projection_text(user_page):
                    raise ApprovedDevPairError("E1_P2_BROWSER_CANONICAL_CONVERGENCE_FAILED")
            user_page.wait_for_function(
                """async () => await new Promise(resolve => {
                    const request = indexedDB.open('code-assist-notion-collaboration-v1', 1);
                    request.onsuccess = () => {
                        const database = request.result;
                        const transaction = database.transaction('outbox', 'readonly');
                        const count = transaction.objectStore('outbox').count();
                        count.onsuccess = () => { resolve(count.result === 0); database.close(); };
                    };
                    request.onerror = () => resolve(false);
                })"""
            )
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-009",
                    admin_page,
                    selectors={
                        "PAGE-EDITOR": "#pageEditorGroup",
                        "CONVERGED-TEXT-BLOCK": ".block-row:nth-child(1) .block-editor",
                        "PRESENCE": "#presenceAvatars",
                    },
                    focus_selector=".block-row:nth-child(1) .block-editor",
                    keyboard_path=["End", "Shift+Home", "ArrowRight"],
                    live_region_expected=True,
                    observation="Both independent synthetic clients exposed the same converged text projection.",
                )

            if v0b_collector is None:
                admin_editor = admin_page.locator(".block-editor").first
                admin_editor.click()
                admin_page.evaluate("() => sendPresenceHeartbeat()")
                user_page.wait_for_selector(
                    ".remote-collaboration-caret", state="visible"
                )
            remote_cursor_visible = True

            if v0b_collector is not None:
                admin_page.evaluate(
                    """async () => {
                      const blocks = (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .map(block => EditorCore.normalizeBlock(EditorCore.deepClone(block)));
                      if (blocks.length !== 3 || blocks[1]?.type !== 'paragraph') {
                        throw new Error('V0B_REFERENCE_SECOND_BLOCK_MISSING');
                      }
                      blocks[1].content = 'V0R-A-BLOCK-BASE';
                      State.pageData.blocks = blocks;
                      renderBlocks();
                      markDirty();
                      await State.liveCollaboration.persistenceChain;
                      for (let attempt = 0; attempt < 200 && State.liveCollaboration.unsyncedCount > 0; attempt += 1) {
                        await new Promise(resolve => setTimeout(resolve, 50));
                      }
                    }"""
                )
                admin_page.wait_for_function(
                    """() => {
                      const blocks = window.NotionCollaborativeEditor.projection()?.blocks || [];
                      const block = blocks[1];
                      return blocks.length === 3
                        && block?.type === 'paragraph'
                        && block?.content === 'V0R-A-BLOCK-BASE'
                        && State.liveCollaboration.unsyncedCount === 0;
                    }""",
                    timeout=30_000,
                )
                user_page.wait_for_function(
                    """() => {
                      const blocks = window.NotionCollaborativeEditor.projection()?.blocks || [];
                      const block = blocks[1];
                      return blocks.length === 3
                        && block?.type === 'paragraph'
                        && block?.content === 'V0R-A-BLOCK-BASE'
                        && State.liveCollaboration.unsyncedCount === 0;
                    }""",
                    timeout=30_000,
                )
                user_block = user_page.locator(".block-row:nth-child(2) .block-editor")
                user_block.click()
                user_block.press("End")
                user_page.keyboard.insert_text(" | V0R-A-BLOCK-COLLABORATOR")
                admin_page.wait_for_function(
                    """() => {
                      const text = (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .map(block => block.content || '').join('\\n');
                      return text.includes('V0R-A-BLOCK-COLLABORATOR');
                    }""",
                    timeout=15_000,
                )
                admin_block = admin_page.locator(".block-row:nth-child(2) .block-editor")
                admin_block.click()
                admin_block.press("End")
                admin_page.keyboard.insert_text(" | V0R-A-BLOCK-OWNER")
                admin_page.wait_for_function(
                    """() => State.liveCollaboration.unsyncedCount === 0
                      && (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .some(block => block.content.includes('V0R-A-BLOCK-OWNER'))""",
                    timeout=15_000,
                )
                user_page.wait_for_function(
                    """() => {
                      const text = (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .map(block => block.content || '').join('\\n');
                      return text.includes('V0R-A-BLOCK-OWNER') && text.includes('V0R-A-BLOCK-COLLABORATOR');
                    }""",
                    timeout=15_000,
                )
                v0b_collector.capture(
                    "V0R-MU-010",
                    admin_page,
                    selectors={
                        "PAGE-EDITOR": "#pageEditorGroup",
                        "FIRST-BLOCK": ".block-row:first-child .block-editor",
                        "LAST-BLOCK": ".block-row:nth-child(2) .block-editor",
                    },
                    focus_selector=".block-row:nth-child(2) .block-editor",
                    keyboard_path=["End", "Enter", "Control+End"],
                    live_region_expected=True,
                    observation="Independent edits to the same durable synthetic block converged to one ordered projection.",
                )

            cleaned_to_trash = _cleanup_fixture(
                admin_page,
                workspace_id=created_workspace_id,
                page_id=created_page_id,
                revoke_synthetic_member=v0b_mode,
            )
            if not cleaned_to_trash:
                raise ApprovedDevPairError("E1_P2_BROWSER_CLEANUP_FAILED")

            allowed = {agent, notion, "ws://127.0.0.1:10113", "https://cdn.jsdelivr.net"}
            escaped: set[str] = set()
            for value in requests:
                parsed = urlsplit(value)
                if parsed.scheme not in {"http", "https", "ws", "wss"}:
                    continue
                request_origin = f"{parsed.scheme}://{parsed.netloc}"
                if request_origin not in allowed:
                    escaped.add(request_origin)
            if escaped:
                raise ApprovedDevPairError("E1_P2_BROWSER_NETWORK_ESCAPE:" + ",".join(sorted(escaped)))
            if page_errors:
                raise ApprovedDevPairError("E1_P2_BROWSER_PAGE_ERROR")

            for context in reversed(contexts):
                context.close()
            contexts.clear()
            browser.close()
            browser_closed = not browser.is_connected()
            browser = None
    finally:
        if not cleaned_to_trash and created_workspace_id and created_page_id:
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
                        workspace_id=created_workspace_id,
                        page_id=created_page_id,
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
        raise ApprovedDevPairError("E1_P2_BROWSER_CLEANUP_FAILED")
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.e1_p2_browser_editor_receipt.v1",
        "status": "pass",
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "version": browser_version,
            "headless": True,
            "independent_ephemeral_contexts": 2,
            "browser_closed": browser_closed,
        },
        "principal_count": 2,
        "same_workspace_convergence": True,
        "close_reopen_recovery": True,
        "encrypted_outbox_record_count_observed": encrypted_record_count,
        "non_extractable_key_count_observed": non_extractable_key_count,
        "plaintext_scope_or_content_leak_count": 0,
        "remote_cursor_visible": remote_cursor_visible,
        "final_projection_sha256": "sha256:" + hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
        "test_page_moved_to_trash": cleaned_to_trash,
        "synthetic_member_absent_after_cleanup": cleaned_to_trash if v0b_mode else True,
        "page_error_count": len(page_errors),
        "network_request_count": len(requests),
        "persistent_or_signed_in_profile_used": False,
        "credential_or_session_value_recorded": False,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-id", required=True)
    args = parser.parse_args(argv)
    if args.approval_id != "AP-DEV-PAIR":
        raise ApprovedDevPairError("AP_DEV_PAIR_REQUIRED")
    receipt = run_e1_p2_browser_journey(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())


__all__ = ["run_e1_p2_browser_journey"]
