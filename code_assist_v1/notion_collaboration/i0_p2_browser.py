"""Actual two-principal Chrome journey for I0-P2 sharing and revocation."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from .approved_dev_pair import AGENT_PORT, NOTION_PORT, ApprovedDevPairError


def _origin(value: str, expected_port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.path not in {"", "/"}
        or parsed.port != expected_port
    ):
        raise ApprovedDevPairError("I0_P2_BROWSER_ORIGIN_INVALID")
    return f"http://127.0.0.1:{expected_port}"


def _screenshot_sha256(page) -> str:
    return "sha256:" + hashlib.sha256(page.screenshot()).hexdigest()


def _login(page, *, agent: str, notion: str, principal: str, password: str) -> None:
    page.goto(agent + "/login", wait_until="domcontentloaded")
    page.locator("#userId").fill(principal)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    page.wait_for_url(agent + "/", wait_until="domcontentloaded")
    page.goto(notion + "/workspace", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => typeof State !== 'undefined' "
        "&& State.user?.id === '" + principal + "' "
        "&& State.navigation?.collaborativeSharing === true"
    )


def _switch_workspace(page, workspace_id: str) -> None:
    page.locator("#workspaceName").click()
    target = page.locator(f'[data-navigation-workspace="{workspace_id}"]')
    target.wait_for(state="visible")
    target.click()
    page.wait_for_function(
        "workspaceId => State.navigation.workspaceId === workspaceId",
        arg=workspace_id,
    )


def _reload_and_switch(page, notion: str, workspace_id: str) -> None:
    page.goto(notion + "/workspace", wait_until="domcontentloaded")
    page.wait_for_function(
        "workspaceId => typeof State !== 'undefined' "
        "&& State.navigation?.workspaces?.some(item => item.id === workspaceId)",
        arg=workspace_id,
    )
    _switch_workspace(page, workspace_id)


def _share_subject_from_visible_surface(
    page,
    *,
    principal_id: str,
    role: str,
    inherit_to_descendants: bool,
) -> dict[str, str]:
    """Submit through whichever canonical share form is visibly adopted."""

    try:
        page.wait_for_function(
            "() => document.querySelector('#inspector')?.dataset.shareLoading !== 'true' "
            "&& ['#pageInheritedShareInviteBtn', '#shareBtn'].some(selector => { "
            "const element = document.querySelector(selector); "
            "return element && element.getClientRects().length > 0; })"
        )
    except Exception as exc:
        state = page.evaluate(
            """() => {
              const inspector = document.querySelector('#inspector');
              const prompt = document.querySelector('#pageInheritedSharePrompt');
              const adopted = document.querySelector('#pageInheritedShareInviteBtn');
              const legacy = document.querySelector('#shareBtn');
              return {
                drawer_mode: State.drawerMode,
                inspector_hidden: inspector?.hidden ?? null,
                inspector_dataset: {...(inspector?.dataset || {})},
                prompt_hidden: prompt?.hidden ?? null,
                prompt_rects: prompt?.getClientRects().length ?? null,
                adopted_hidden: adopted?.hidden ?? null,
                adopted_rects: adopted?.getClientRects().length ?? null,
                legacy_hidden: legacy?.hidden ?? null,
                legacy_rects: legacy?.getClientRects().length ?? null,
                effective_sources: State.shareAccess?.effective_access?.sources || [],
                direct_shares: State.shareAccess?.items || [],
              };
            }"""
        )
        raise ApprovedDevPairError(
            "I0_P2_SHARE_SURFACE_NOT_INTERACTIVE:"
            + json.dumps(state, sort_keys=True, separators=(",", ":"))
        ) from exc
    adopted_button = page.locator("#pageInheritedShareInviteBtn")
    if adopted_button.is_visible():
        page.locator("#pageInheritedShareUser").select_option(principal_id)
        page.locator("#pageInheritedShareRole").select_option(role)
        page.wait_for_function(
            "() => document.querySelector('#pageInheritedShareInviteBtn')?.disabled === false"
        )
        adopted_button.click()
        return {
            "PAGE-SHARE-LIST": "#pageInheritedSharePeople",
            "PAGE-SHARE-ROLE": "#pageInheritedShareRole",
        }

    page.locator("#shareBtn").wait_for(state="visible")
    page.locator("#sharePrincipalType").select_option("subject")
    page.locator("#shareUser").select_option(principal_id)
    page.locator("#shareRole").select_option(role)
    if inherit_to_descendants:
        page.locator("#shareInherit").check()
    else:
        page.locator("#shareInherit").uncheck()
    page.locator("#shareBtn").click()
    return {
        "PAGE-SHARE-LIST": "#shareList",
        "PAGE-SHARE-ROLE": "#shareRole",
    }


def _open_visible_inherited_explanation(page) -> str:
    """Open and return the explanation exposed by the active share surface."""

    page.wait_for_function(
        "() => State.shareAccess?.effective_access?.sources?.some(item => item.inherited === true)"
    )
    advanced = page.locator("#pageInheritedShareAdvancedBtn")
    if advanced.is_visible():
        details = page.locator("#pageInheritedShareDetails")
        if details.is_hidden():
            advanced.click()
        details.wait_for(state="visible")
        return "#pageInheritedShareDetails"
    page.locator("#shareAccessExplanation").wait_for(state="visible")
    return "#shareAccessExplanation"


def run_i0_p2_browser_sharing(
    *,
    agent_origin: str,
    notion_origin: str,
    credentials: Mapping[str, str],
    v0b_collector: Any | None = None,
) -> dict[str, object]:
    """Exercise invite, inherited access, role change, and both revoke fences."""

    agent = _origin(agent_origin, AGENT_PORT)
    notion = _origin(notion_origin, NOTION_PORT)
    if set(credentials) != {"admin", "user01"} or any(
        not isinstance(value, str) or not value for value in credentials.values()
    ):
        raise ApprovedDevPairError("I0_P2_BROWSER_PRINCIPALS_INVALID")

    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    run_id = uuid4().hex
    requests: list[str] = []
    screenshot_hashes: dict[str, str] = {}
    contexts = []
    browser = None
    browser_closed = False
    workspace_id = ""
    parent_id = ""
    child_id = ""
    cleaned_to_trash = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            browser_version = browser.version
            pages = {}
            for principal in ("admin", "user01"):
                context = browser.new_context(locale="ko-KR", service_workers="block")
                contexts.append(context)
                page = context.new_page()
                page.on("request", lambda request: requests.append(request.url))
                _login(
                    page,
                    agent=agent,
                    notion=notion,
                    principal=principal,
                    password=credentials[principal],
                )
                pages[principal] = page

            admin = pages["admin"]
            setup = admin.evaluate(
                """
                async ({ runId }) => {
                  const previous = await api('/api/notion/workspaces');
                  for (const item of previous.items || []) {
                    if (item.access?.role !== 'owner' || !String(item.name || '').startsWith('I0-P2 Browser ')) continue;
                    const detail = (await api(`/api/notion/workspaces/${encodeURIComponent(item.id)}`)).workspace;
                    if ((detail.members || []).some(member => member.subject_id === 'user01')) {
                      await api(`/api/notion/workspaces/${encodeURIComponent(item.id)}/members/user01`, {
                        method: 'DELETE',
                        body: {
                          expected_workspace_epoch: detail.security_epoch,
                          operation_id: `i0-p2-browser-preflight-revoke-${runId}-${item.id}`,
                        },
                      });
                    }
                  }
                  const createdWorkspace = await api('/api/notion/workspaces', {
                    method: 'POST',
                    body: {
                      name: `I0-P2 Browser ${runId.slice(0, 8)}`,
                      icon: 'I',
                      operation_id: `i0-p2-browser-workspace-${runId}`,
                    },
                  });
                  const workspaceId = createdWorkspace.workspace.id;
                  let workspace = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                  const parent = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages`, {
                    method: 'POST',
                    body: {
                      base_version: workspace.version,
                      operation_id: `i0-p2-browser-parent-${runId}`,
                      parent_id: '',
                      title: 'I0-P2 inherited parent',
                    },
                  });
                  workspace = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                  const child = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages`, {
                    method: 'POST',
                    body: {
                      base_version: workspace.version,
                      operation_id: `i0-p2-browser-child-${runId}`,
                      parent_id: parent.page.id,
                      title: 'I0-P2 inherited child',
                    },
                  });
                  const payload = await api('/api/notion/workspaces');
                  const bootstrap = normalizeWorkspaceNavigationBootstrap(payload, workspaceId);
                  State.navigation.workspaces = bootstrap.workspaces;
                  State.navigation.collaborativeSharing = bootstrap.collaborativeSharing;
                  await switchCanonicalWorkspace(workspaceId);
                  await openPage(parent.page.id);
                  return {
                    workspace_id: workspaceId,
                    parent_id: parent.page.id,
                    child_id: child.page.id,
                  };
                }
                """,
                {"runId": run_id},
            )
            workspace_id = str(setup["workspace_id"])
            parent_id = str(setup["parent_id"])
            child_id = str(setup["child_id"])
            admin.wait_for_function(
                "pageId => State.activePageId === pageId", arg=parent_id
            )
            # Workspace membership moved to its own sidebar entry; the page
            # Share button intentionally opens the page-only popover and hides
            # workspace membership controls.
            admin.locator("#sidebarMemberInviteBtn").click()
            admin.locator(
                '#inspector[data-share-surface="workspace-invite"]'
            ).wait_for(state="visible")
            admin.locator("#workspaceMemberSection").wait_for(state="visible")
            # openDrawer loads the canonical workspace/share state
            # asynchronously. Wait for that render to settle so it cannot
            # clear the selection between select_option and click.
            admin.wait_for_function(
                "() => Boolean(State.shareAccess?.workspace_epoch) "
                "&& Boolean(document.querySelector("
                "'#workspaceMemberUser option[value=\"user01\"]')) "
                "&& document.querySelector('#inspector')?.dataset.shareLoading !== 'true'"
            )
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-001",
                    admin,
                    selectors={
                        "WORKSPACE-SHARE-DIALOG": "#inspector",
                        "WORKSPACE-MEMBER-SELECT": "#workspaceMemberUser",
                        "WORKSPACE-MEMBER-LIST": "#workspaceMemberList",
                    },
                    focus_selector="#workspaceMemberUser",
                    keyboard_path=["Tab", "Enter", "Shift+Tab"],
                    live_region_expected=True,
                    observation="Workspace invite controls were visible before the synthetic invite.",
                )
            admin.locator("#workspaceMemberUser").select_option("user01")
            admin.locator("#workspaceMemberRole").select_option("member")
            admin.wait_for_function(
                "() => document.querySelector('#workspaceMemberUser')?.value === 'user01' "
                "&& document.querySelector('#workspaceMemberBtn')?.disabled === false"
            )
            admin.locator("#workspaceMemberBtn").click()
            admin.wait_for_function(
                "() => State.workspaceAccess?.members?.some(item => "
                "item.subject_id === 'user01' && item.role === 'member')"
            )
            admin.wait_for_function(
                "() => document.querySelector('#inspector')?.dataset.shareSurface "
                "=== 'workspace-members' "
                "&& document.querySelector('#inspector')?.dataset.shareSurfaceLock "
                "=== 'workspace-members' "
                "&& document.querySelector('#inspector')?.dataset.shareLoading !== 'true'"
            )
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-002",
                    admin,
                    selectors={
                        "WORKSPACE-SHARE-DIALOG": "#inspector",
                        "WORKSPACE-MEMBER-LIST": "#workspaceMemberList",
                        "WORKSPACE-MEMBER-SELECT": "#workspaceMemberUser",
                    },
                    focus_selector="#workspaceMemberUser",
                    keyboard_path=["Tab", "ArrowDown", "Enter", "Tab", "Enter"],
                    live_region_expected=True,
                    observation="The synthetic collaborator appeared in the active workspace member list.",
                )

            if admin.locator("#inspector").is_visible():
                # Escape is the common keyboard dismissal path for both the
                # workspace invite dialog and the compact page popover.
                admin.keyboard.press("Escape")
                admin.locator("#inspector").wait_for(state="hidden")
            admin.locator("#shareDrawerBtn").click()
            admin.locator(
                '#inspector[data-share-surface="page-popover"]'
            ).wait_for(state="visible")
            share_surface_selectors = _share_subject_from_visible_surface(
                admin,
                principal_id="user01",
                role="edit_content",
                inherit_to_descendants=True,
            )
            admin.wait_for_function(
                "() => State.shareAccess?.items?.some(item => "
                "item.principal_type === 'subject' "
                "&& item.principal_id === 'user01' "
                "&& item.role === 'edit_content' "
                "&& item.inherit_to_descendants === true)"
            )
            screenshot_hashes["admin_invite_and_inherited_share"] = _screenshot_sha256(admin)
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-004",
                    admin,
                    selectors={
                        "PAGE-SHARE-DIALOG": "#inspector",
                        **share_surface_selectors,
                    },
                    focus_selector=share_surface_selectors["PAGE-SHARE-ROLE"],
                    keyboard_path=["Tab", "ArrowDown", "Enter", "Tab", "Space", "Tab", "Enter"],
                    live_region_expected=True,
                    observation="Inherited synthetic page edit access was active in the page share surface.",
                )

            member = pages["user01"]
            _reload_and_switch(member, notion, workspace_id)
            child_row = member.locator(f'[data-page-id="{child_id}"]').first
            child_row.wait_for(state="visible")
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-003",
                    member,
                    selectors={
                        "WORKSPACE-SHELL": "#workspaceMain",
                        "WORKSPACE-SWITCHER": "#workspaceName",
                        "SHARED-PAGE-ROW": f'[data-page-id="{child_id}"]',
                    },
                    focus_selector="#workspaceName",
                    keyboard_path=["Enter", "ArrowDown", "Enter"],
                    live_region_expected=False,
                    observation="The invited synthetic workspace and its shared page were visible to the collaborator.",
            )
            child_row.click()
            member.wait_for_function(
                "pageId => State.activePageId === pageId "
                "&& State.pageData?.page?.iam_role === 'edit_content'",
                arg=child_id,
            )
            member.locator("#shareDrawerBtn").click()
            member.locator(
                '#inspector[data-share-surface="page-popover"]'
            ).wait_for(state="visible")
            inherited_explanation_selector = _open_visible_inherited_explanation(member)
            inherited_explanation = (
                member.locator(inherited_explanation_selector).inner_text().strip()
            )
            if not inherited_explanation:
                raise ApprovedDevPairError("I0_P2_INHERITED_EXPLANATION_MISSING")
            member.wait_for_function(
                "() => document.querySelector('#pageTitle')?.disabled === false",
                timeout=15_000,
            )
            if member.locator("#pageTitle").is_disabled():
                raise ApprovedDevPairError("I0_P2_EDIT_ROLE_NOT_APPLIED")
            if v0b_collector is not None:
                member.locator("#drawerCloseBtn").click()
                member.locator("#inspector").wait_for(state="hidden")
                v0b_collector.capture(
                    "V0R-MU-005",
                    member,
                    selectors={
                        "PAGE-CANVAS": "#pageView",
                        "PAGE-TITLE": "#pageTitle",
                        "PAGE-EDITOR": "#pageEditorGroup",
                    },
                    focus_selector="#pageEditorGroup",
                    keyboard_path=["Tab", "Enter"],
                    live_region_expected=False,
                    observation="The collaborator could focus and edit the inherited synthetic page canvas.",
                )
                member.locator("#shareDrawerBtn").click()
                inherited_explanation_selector = _open_visible_inherited_explanation(member)
                v0b_collector.capture(
                    "V0R-MU-006",
                    member,
                    selectors={
                        "PAGE-SHARE-DIALOG": "#inspector",
                        "ACCESS-EXPLANATION": inherited_explanation_selector,
                        "PAGE-SHARE-LIST": (
                            "#pageInheritedSharePeople"
                            if member.locator("#pageInheritedSharePeople").is_visible()
                            else "#shareList"
                        ),
                    },
                    focus_selector=inherited_explanation_selector,
                    keyboard_path=["Shift+Tab", "Enter", "Tab"],
                    live_region_expected=True,
                    observation="The effective inherited workspace source was explained in the page share surface.",
                )
            screenshot_hashes["member_inherited_edit"] = _screenshot_sha256(member)

            _share_subject_from_visible_surface(
                admin,
                principal_id="user01",
                role="comment",
                inherit_to_descendants=True,
            )
            admin.wait_for_function(
                "() => State.shareAccess?.items?.some(item => "
                "item.principal_id === 'user01' && item.role === 'comment')"
            )
            _reload_and_switch(member, notion, workspace_id)
            member.locator(f'[data-page-id="{child_id}"]').first.click()
            member.wait_for_function(
                "pageId => State.activePageId === pageId "
                "&& State.pageData?.page?.iam_role === 'comment'",
                arg=child_id,
            )
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-015",
                    member,
                    selectors={
                        "PAGE-CANVAS": "#pageView",
                        "PAGE-TITLE": "#pageTitle",
                        "COMMENT-TRIGGER": "#commentDrawerBtn",
                    },
                    focus_selector="#commentDrawerBtn",
                    keyboard_path=["Tab", "Enter", "Escape"],
                    live_region_expected=False,
                    observation="The collaborator retained comment access while the page title remained non-editable.",
                )

            _share_subject_from_visible_surface(
                admin,
                principal_id="user01",
                role="view",
                inherit_to_descendants=True,
            )
            admin.wait_for_function(
                "() => State.shareAccess?.items?.some(item => "
                "item.principal_id === 'user01' && item.role === 'view')"
            )
            _reload_and_switch(member, notion, workspace_id)
            member.locator(f'[data-page-id="{child_id}"]').first.click()
            member.wait_for_function(
                "pageId => State.activePageId === pageId "
                "&& State.pageData?.page?.iam_role === 'view'",
                arg=child_id,
            )
            if not member.locator("#pageTitle").is_disabled():
                raise ApprovedDevPairError("I0_P2_VIEW_ROLE_NOT_APPLIED")
            screenshot_hashes["member_inherited_view"] = _screenshot_sha256(member)
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-016",
                    member,
                    selectors={
                        "PAGE-CANVAS": "#pageView",
                        "PAGE-TITLE": "#pageTitle",
                        "PAGE-EDITOR": "#pageEditorGroup",
                    },
                    focus_selector="#pageView",
                    keyboard_path=["Tab", "Shift+Tab"],
                    live_region_expected=False,
                    observation="The collaborator saw the synthetic page in a read-only state.",
                )

            adopted_revoke = admin.locator('[data-page-share-revoke="user01"]')
            revoke = (
                adopted_revoke
                if adopted_revoke.is_visible()
                else admin.locator("#shareList .mini-row")
                .filter(has_text="user01")
                .locator("button.danger")
            )
            revoke.click()
            admin.wait_for_function(
                "() => !State.shareAccess?.items?.some(item => item.principal_id === 'user01')"
            )
            _reload_and_switch(member, notion, workspace_id)
            if member.locator(f'[data-page-id="{child_id}"]').count() != 0:
                raise ApprovedDevPairError("I0_P2_PAGE_VISIBLE_AFTER_REVOKE")
            revoked_status = member.evaluate(
                """
                async ({ workspaceId, pageId }) => (
                  await fetch(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/collaboration`)
                ).status
                """,
                {"workspaceId": workspace_id, "pageId": child_id},
            )
            if revoked_status != 404:
                raise ApprovedDevPairError("I0_P2_PAGE_REVOKE_NOT_FENCED")
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-017",
                    member,
                    selectors={
                        "WORKSPACE-SHELL": "#workspaceMain",
                        "WORKSPACE-SWITCHER": "#workspaceName",
                        "PAGE-TREE": "#navigationSections",
                    },
                    focus_selector="#workspaceMain",
                    keyboard_path=["Alt+Left", "Tab"],
                    live_region_expected=False,
                    observation="The revoked synthetic page was absent and its collaboration endpoint returned the non-oracle boundary.",
                )

            if admin.locator("#inspector").is_visible():
                # The compact page popover intentionally hides the drawer
                # chrome; Escape is its visible keyboard dismissal path.
                admin.keyboard.press("Escape")
                admin.locator("#inspector").wait_for(state="hidden")
            admin.locator("#workspaceName").click()
            admin.locator('[data-workspace-action="people"]').click()
            admin.locator(
                '#inspector[data-share-surface="workspace-members"]'
            ).wait_for(state="visible")
            member_revoke = admin.locator("#workspaceMemberList .mini-row").filter(
                has_text="user01"
            ).locator("[data-member-revoke]")
            member_revoke.click()
            admin.wait_for_function(
                "() => !State.workspaceAccess?.members?.some(item => item.subject_id === 'user01')"
            )
            member.goto(notion + "/workspace", wait_until="domcontentloaded")
            member.wait_for_function(
                "() => typeof State !== 'undefined' "
                "&& State.navigation?.collaborativeSharing === true"
            )
            if member.locator(
                f'[data-navigation-workspace="{workspace_id}"]'
            ).count() != 0:
                member.locator("#workspaceName").click()
                if member.locator(
                    f'[data-navigation-workspace="{workspace_id}"]'
                ).count() != 0:
                    raise ApprovedDevPairError("I0_P2_WORKSPACE_VISIBLE_AFTER_REVOKE")
            screenshot_hashes["member_after_workspace_revoke"] = _screenshot_sha256(member)
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-018",
                    member,
                    selectors={
                        "WORKSPACE-SHELL": "#workspaceMain",
                        "WORKSPACE-SWITCHER": "#workspaceName",
                        "HOME": "#homeNavBtn",
                    },
                    focus_selector="#workspaceMain",
                    keyboard_path=["Alt+Left", "Tab", "Enter"],
                    live_region_expected=False,
                    observation="The revoked synthetic workspace was absent from the collaborator switcher without disclosing its content.",
                )

            admin.evaluate(
                """
                async ({ workspaceId, pageId, runId }) => {
                  const current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                  await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/trash`,
                    {
                      method: 'POST',
                      body: {
                        base_version: current.version,
                        operation_id: `i0-p2-browser-trash-${runId}`,
                      },
                    },
                  );
                }
                """,
                {"workspaceId": workspace_id, "pageId": parent_id, "runId": run_id},
            )
            cleaned_to_trash = True

            allowed_origins = {agent, notion, "https://cdn.jsdelivr.net"}
            escaped = sorted(
                {
                    f"{parsed.scheme}://{parsed.netloc}"
                    for value in requests
                    if (parsed := urlsplit(value)).scheme in {"http", "https"}
                    and f"{parsed.scheme}://{parsed.netloc}" not in allowed_origins
                }
            )
            if escaped:
                raise ApprovedDevPairError(
                    "I0_P2_BROWSER_NETWORK_TARGET_ESCAPE:" + ",".join(escaped)
                )
            for context in reversed(contexts):
                context.close()
            contexts.clear()
            browser.close()
            browser_closed = not browser.is_connected()
            browser = None
    finally:
        if not cleaned_to_trash and workspace_id and parent_id and "admin" in locals().get("pages", {}):
            try:
                pages["admin"].evaluate(
                    """
                    async ({ workspaceId, pageId, runId }) => {
                      const current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                      await api(
                        `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/trash`,
                        {
                          method: 'POST',
                          body: {
                            base_version: current.version,
                            operation_id: `i0-p2-browser-failure-trash-${runId}`,
                          },
                        },
                      );
                    }
                    """,
                    {"workspaceId": workspace_id, "pageId": parent_id, "runId": run_id},
                )
                cleaned_to_trash = True
            except Exception:
                pass
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
        raise ApprovedDevPairError("I0_P2_BROWSER_PROCESS_CLEANUP_FAILED")
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.i0_p2_two_principal_browser.v1",
        "status": "pass",
        "principal_ids": ["admin", "user01"],
        "principal_count": 2,
        "workspace_id": workspace_id,
        "parent_document_id": parent_id,
        "child_document_id": child_id,
        "workspace_invite_verified": True,
        "inherited_edit_verified": True,
        "inherited_access_explanation_verified": True,
        "role_change_to_view_verified": True,
        "page_revoke_http_404_verified": True,
        "workspace_revoke_visibility_fence_verified": True,
        "cross_context_subject_crossover_count": 0,
        "unapproved_network_target_escape_count": 0,
        "browser_cookie_or_storage_value_recorded": False,
        "plaintext_password_hash_secret_or_session_recorded": False,
        "browser": {
            "channel": "chrome",
            "version": browser_version,
            "headless": True,
            "independent_ephemeral_contexts": 2,
            "browser_closed": browser_closed,
        },
        "test_root_moved_to_trash": cleaned_to_trash,
        "screenshot_sha256": screenshot_hashes,
        "network_request_count": len(requests),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


__all__ = ["run_i0_p2_browser_sharing"]
