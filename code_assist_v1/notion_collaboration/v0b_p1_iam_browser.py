"""V0B-P1 two-workspace IAM journey matching the adopted V0R fixture lineage."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from .approved_dev_pair import AGENT_PORT, NOTION_PORT, ApprovedDevPairError
from .c0_p2_browser import _seed_v0b_reference_body
from .v0b_p1_capture import v0b_chrome_launch_options


V0B_WORKSPACE_REALM_ROTATION_THRESHOLD = 64


def _origin(value: str, expected_port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.path not in {"", "/"}
        or parsed.port != expected_port
    ):
        raise ApprovedDevPairError("V0B_IAM_BROWSER_ORIGIN_INVALID")
    return f"http://127.0.0.1:{expected_port}"


def _login(page: Any, *, agent: str, notion: str, principal: str, password: str) -> None:
    page.goto(agent + "/login", wait_until="domcontentloaded")
    user_id = page.locator("#userId")
    try:
        user_id.wait_for(state="visible", timeout=2_000)
    except Exception:
        page.wait_for_url(agent + "/", wait_until="domcontentloaded")
    else:
        try:
            user_id.fill(principal)
            page.locator("#password").fill(password)
            page.locator("#submitBtn").click()
            page.wait_for_url(agent + "/", wait_until="domcontentloaded")
        except Exception:
            if page.url.rstrip("/") != agent:
                raise
    page.goto(notion + "/workspace", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => typeof State !== 'undefined' "
        f"&& State.user?.id === '{principal}' "
        "&& State.navigation?.collaborativeSharing === true"
    )


def _open_workspace_page(page: Any, workspace_id: str, page_id: str) -> None:
    page.evaluate(
        """async ({workspaceId, pageId}) => {
          await refreshWorkspaceNavigationCapability();
          await switchCanonicalWorkspace(workspaceId);
          await loadPages();
          await openPage(pageId, false, PageFocusPolicy.PRESERVE);
        }""",
        {"workspaceId": workspace_id, "pageId": page_id},
    )
    page.wait_for_function(
        "pageId => State.activePageId === pageId && Boolean(State.pageData?.page)",
        arg=page_id,
    )


def _reload_and_open(page: Any, notion: str, workspace_id: str, page_id: str) -> None:
    page.goto(notion + "/workspace", wait_until="domcontentloaded")
    page.wait_for_function(
        "workspaceId => typeof State !== 'undefined' "
        "&& State.navigation?.workspaces?.some(item => item.id === workspaceId)",
        arg=workspace_id,
    )
    _open_workspace_page(page, workspace_id, page_id)


def _set_first_block(page: Any, value: str) -> None:
    page.wait_for_function(
        """() => activePageCanEdit() === true
          && document.querySelector('.block-editor')?.getAttribute('contenteditable') === 'true'""",
        timeout=20_000,
    )
    editor = page.locator(".block-editor").first
    editor.wait_for(state="visible")
    baseline_sequence = page.evaluate(
        "() => Number(State.liveCollaboration.clientSequence || 0)"
    )
    editor.fill(value)
    page.wait_for_function(
        """({value, baselineSequence}) =>
          window.NotionCollaborativeEditor.projection()?.blocks?.[0]?.content === value
          && State.liveCollaboration.clientSequence > baselineSequence
          && State.liveCollaboration.unsyncedCount === 0""",
        arg={"value": value, "baselineSequence": baseline_sequence},
        timeout=20_000,
    )


def _set_reference_onboarding_blocks(page: Any, workspace_label: str) -> None:
    """Seed the deterministic first-workspace canvas captured by V0R-P1."""

    page.wait_for_function(
        """() => activePageCanEdit() === true
          && window.NotionCollaborativeEditor?.descriptor()?.ready === true""",
        timeout=20_000,
    )
    baseline_sequence = page.evaluate(
        "() => Number(State.liveCollaboration.clientSequence || 0)"
    )
    page.evaluate(
        """workspaceLabel => {
          const currentPageId = String(State.activePageId || '');
          const blocks = [
            EditorCore.createBlock('todo', {
              content: 'Notion 계정을 만들어 보세요.',
              props: {checked: true},
            }),
            EditorCore.createBlock('todo', {
              content: '아무 곳이나 클릭해 / 를 입력하고 제목, 표, 할 일 목록 등 추가할 수 있는 항목을 확인해 보세요.',
              props: {
                checked: false,
                rich_text_html: '아무 곳이나 클릭해 <code>/</code> <strong>를 입력하고</strong> 제목, 표, 할 일 목록 등 추가할 수 있는 항목을 확인해 보세요.',
              },
            }),
            EditorCore.createBlock('todo', {
              content: '/페이지 를 입력해 새 페이지를 추가하고 하위 페이지를 자유롭게 만들어 보세요.',
              props: {
                checked: false,
                indent: 1,
                rich_text_html: '<code>/페이지</code> <strong>를 입력해 새 페이지를</strong> 추가하고 하위 페이지를 자유롭게 만들어 보세요.',
              },
            }),
            EditorCore.createBlock('todo', {
              content: '왼쪽 사이드바에서 새 페이지를 찾고, 정리하고, 추가하세요 👈',
              props: {
                checked: false,
                rich_text_html: '왼쪽 사이드바에서 <strong>새 페이지를 찾고, 정리하고, 추가하세요</strong> 👈',
              },
            }),
            EditorCore.createBlock('toggle', {
              content: '이것은 토글 블록입니다. 작은 삼각형을 클릭하면 유용한 팁을 더 확인할 수 있어요!',
              props: {open: false},
            }),
          ];
          if (workspaceLabel === 'REF-FREE-A') blocks.push(
            EditorCore.createBlock('child_page', {
              content: workspaceLabel,
              props: {page_id: currentPageId, title: workspaceLabel},
            }),
          );
          State.pageData.blocks = blocks;
          renderBlocks();
          markDirty();
        }""",
        workspace_label,
    )
    page.wait_for_function(
        """({workspaceLabel, baselineSequence}) => {
          const projection = window.NotionCollaborativeEditor.projection();
          const blocks = projection?.blocks || [];
          const expectedCount = workspaceLabel === 'REF-FREE-A' ? 6 : 5;
          return blocks.length === expectedCount
            && blocks[0]?.type === 'todo'
            && blocks[0]?.props?.checked === true
            && (workspaceLabel !== 'REF-FREE-A'
              || (blocks[5]?.type === 'child_page' && blocks[5]?.content === workspaceLabel))
            && State.liveCollaboration.clientSequence > baselineSequence
            && State.liveCollaboration.unsyncedCount === 0
            && State.liveCollaboration.pendingEnvelopes?.size === 0;
        }""",
        arg={"workspaceLabel": workspace_label, "baselineSequence": baseline_sequence},
        timeout=20_000,
    )


def _set_reference_b_page(page: Any) -> None:
    """Match the persisted REF-FREE-B starter page used by the V0R reference."""

    expected_title = "V0R-B-ROOTV0R-B-ROOT"
    page.wait_for_function(
        "() => activePageCanEdit() === true && State.liveCollaboration?.crdtReady === true",
        timeout=20_000,
    )
    _set_reference_onboarding_blocks(page, "REF-FREE-B")
    page.evaluate("() => updatePageAppearance({icon: '👋'})")
    page.wait_for_function(
        """expectedTitle => State.pageData?.page?.title === expectedTitle
          && State.pageData?.page?.icon === '👋'
          && State.liveCollaboration.unsyncedCount === 0
          && State.liveCollaboration.pendingEnvelopes?.size === 0""",
        arg=expected_title,
        timeout=60_000,
    )


def _screenshot_sha256(page: Any) -> str:
    return "sha256:" + hashlib.sha256(page.screenshot()).hexdigest()


def _require_workspace_realm_budget(page: Any) -> int:
    payload = page.evaluate("async () => await api('/api/notion/workspaces')")
    if not isinstance(payload, Mapping):
        raise ApprovedDevPairError("V0B_WORKSPACE_REALM_COUNT_INVALID")
    items = payload.get("items")
    count = payload.get("count")
    if (
        not isinstance(items, list)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(items)
        or count < 0
    ):
        raise ApprovedDevPairError("V0B_WORKSPACE_REALM_COUNT_INVALID")
    if count > V0B_WORKSPACE_REALM_ROTATION_THRESHOLD:
        raise ApprovedDevPairError(
            "V0B_WORKSPACE_REALM_ROTATION_REQUIRED:"
            f"{count}>{V0B_WORKSPACE_REALM_ROTATION_THRESHOLD}"
        )
    return count


def _cleanup_iam_fixtures(
    page: Any,
    *,
    fixtures: list[list[str]],
    run_id: str,
) -> bool:
    results = page.evaluate(
        """async ({fixtures, runId}) => {
          const results = [];
          for (const [workspaceId, pageId, suffix] of fixtures) {
            const result = {workspace_id: workspaceId, page_id: pageId, ok: false};
            try {
              const workspacePath = `/api/notion/workspaces/${encodeURIComponent(workspaceId)}`;
              const pagePath = `${workspacePath}/pages/${encodeURIComponent(pageId)}`;
              let detail = (await api(workspacePath)).workspace;
              if ((detail.members || []).some(item => item.subject_id === 'user01')) {
                await api(`${workspacePath}/members/user01`, {
                  method: 'DELETE',
                  body: {
                    expected_workspace_epoch: detail.security_epoch,
                    operation_id: `v0b-iam-cleanup-member-${suffix}-${runId}`,
                  },
                });
              }

              let pages = await api(`${workspacePath}/pages?q=`);
              let pageActive = (pages.items || []).some(item => String(item.id || '') === pageId);
              if (pageActive) {
                detail = (await api(workspacePath)).workspace;
                await api(`${pagePath}/trash`, {
                  method: 'POST',
                  body: {
                    base_version: detail.version,
                    operation_id: `v0b-iam-cleanup-trash-${suffix}-${runId}`,
                  },
                });
              }

              detail = (await api(workspacePath)).workspace;
              if ((detail.guests || []).some(item => item.subject_id === 'user01')) {
                await api(`${workspacePath}/guests/user01`, {
                  method: 'DELETE',
                  body: {
                    base_version: detail.version,
                    operation_id: `v0b-iam-cleanup-guest-${suffix}-${runId}`,
                  },
                });
              }

              detail = (await api(workspacePath)).workspace;
              pages = await api(`${workspacePath}/pages?q=`);
              pageActive = (pages.items || []).some(item => String(item.id || '') === pageId);
              result.member_absent = !(detail.members || []).some(
                item => item.subject_id === 'user01'
              );
              result.guest_absent = !(detail.guests || []).some(
                item => item.subject_id === 'user01'
              );
              result.page_absent = !pageActive;
              result.ok = result.member_absent && result.guest_absent && result.page_absent;
            } catch (error) {
              result.error = String(error && error.message || error);
            }
            results.push(result);
          }
          return results;
        }""",
        {"fixtures": fixtures, "runId": run_id},
    )
    return (
        isinstance(results, list)
        and len(results) == len(fixtures)
        and all(isinstance(item, dict) and item.get("ok") is True for item in results)
    )


def run_v0b_p1_iam_journey(
    *,
    agent_origin: str,
    notion_origin: str,
    credentials: Mapping[str, str],
    v0b_collector: Any,
) -> dict[str, object]:
    """Capture A workspace inheritance and B direct-page roles without lineage mixing."""

    agent = _origin(agent_origin, AGENT_PORT)
    notion = _origin(notion_origin, NOTION_PORT)
    if set(credentials) != {"admin", "user01"} or any(
        not isinstance(value, str) or not value for value in credentials.values()
    ):
        raise ApprovedDevPairError("V0B_IAM_BROWSER_PRINCIPALS_INVALID")
    if v0b_collector is None:
        raise ApprovedDevPairError("V0B_IAM_COLLECTOR_REQUIRED")

    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    run_id = uuid4().hex
    requests: list[str] = []
    contexts: list[Any] = []
    browser = None
    browser_closed = False
    cleaned_to_trash = False
    a_workspace_id = ""
    a_page_id = ""
    b_workspace_id = ""
    b_page_id = ""
    screenshot_hashes: dict[str, str] = {}
    pages: dict[str, Any] = {}
    initial_workspace_count = -1
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**v0b_chrome_launch_options())
            browser_version = browser.version
            for principal in ("admin", "user01"):
                context = browser.new_context(
                    locale="ko-KR",
                    service_workers="block",
                    viewport={"width": 1440, "height": 900},
                )
                context.set_default_timeout(25_000)
                contexts.append(context)
                page = context.new_page()
                page.on("request", lambda request, sink=requests: sink.append(request.url))
                _login(
                    page,
                    agent=agent,
                    notion=notion,
                    principal=principal,
                    password=credentials[principal],
                )
                pages[principal] = page

            admin = pages["admin"]
            member = pages["user01"]
            initial_workspace_count = _require_workspace_realm_budget(admin)
            setup = admin.evaluate(
                """async runId => {
                  const createFixture = async (workspaceName, pageTitle, suffix) => {
                    const created = await api('/api/notion/workspaces', {
                      method: 'POST',
                      body: {
                        name: workspaceName,
                        icon: 'R',
                        operation_id: `v0b-iam-workspace-${suffix}-${runId}`,
                      },
                    });
                    const workspaceId = created.workspace.id;
                    const workspace = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                    const page = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages`, {
                      method: 'POST',
                      body: {
                        base_version: workspace.version,
                        operation_id: `v0b-iam-page-${suffix}-${runId}`,
                        parent_id: '',
                        title: pageTitle,
                      },
                    });
                    return {workspace_id: workspaceId, page_id: page.page.id};
                  };
                  return {
                    a: await createFixture('REF-FREE-A', 'V0R-A-ROOT', 'a'),
                    b: await createFixture(
                      'REF-FREE-B',
                      'V0R-B-ROOTV0R-B-ROOT',
                      'b',
                    ),
                  };
                }""",
                run_id,
            )
            a_workspace_id = str(setup["a"]["workspace_id"])
            a_page_id = str(setup["a"]["page_id"])
            b_workspace_id = str(setup["b"]["workspace_id"])
            b_page_id = str(setup["b"]["page_id"])

            _reload_and_open(admin, notion, a_workspace_id, a_page_id)
            admin.evaluate("() => updatePageAppearance({icon: 'list'})")
            admin.wait_for_function("() => State.pageData?.page?.icon === 'list'")
            _seed_v0b_reference_body(admin, child_page_id="", cumulative=True)
            admin.locator("#sidebarMemberInviteBtn").click()
            admin.wait_for_function(
                "() => document.querySelector('#inspector')?.dataset.shareSurface === 'workspace-invite'"
            )
            admin.locator("#workspaceMemberSection").wait_for(state="visible")
            admin.locator("#workspaceMemberUser").select_option("user01")
            admin.locator("#workspaceMemberRole").select_option("owner")
            v0b_collector.capture(
                "V0R-MU-001",
                admin,
                selectors={
                    "WORKSPACE-SHARE-DIALOG": "#inspector",
                    "WORKSPACE-MEMBER-SELECT": "#workspaceMemberUser",
                    "WORKSPACE-MEMBER-ROLE": "#workspaceMemberRole",
                },
                focus_selector="#workspaceMemberUser",
                keyboard_path=["Tab", "Enter", "Shift+Tab"],
                live_region_expected=True,
                observation="The REF-FREE-A temporary-owner invite controls were ready.",
            )
            admin.locator("#workspaceMemberUser").select_option("user01")
            admin.locator("#workspaceMemberRole").select_option("owner")
            admin.locator("#workspaceMemberBtn").click()
            admin.wait_for_function(
                "() => State.workspaceAccess?.members?.some(item => "
                "item.subject_id === 'user01' && item.role === 'owner')"
            )
            admin.wait_for_function(
                "() => document.querySelector('#inspector')?.dataset.shareSurface === 'workspace-members'"
            )
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
                observation="The collaborator was active as the temporary REF-FREE-A owner.",
            )
            admin.evaluate("() => closeDrawer(false)")
            _seed_v0b_reference_body(admin, child_page_id="", cumulative=True)
            _reload_and_open(admin, notion, a_workspace_id, a_page_id)

            member.goto(notion + "/workspace", wait_until="domcontentloaded")
            member.wait_for_function(
                "workspaceId => State.navigation?.workspaces?.some(item => item.id === workspaceId)",
                arg=a_workspace_id,
            )
            member.evaluate(
                "async workspaceId => { await switchCanonicalWorkspace(workspaceId); await loadPages(); }",
                a_workspace_id,
            )
            a_row = member.locator(f'[data-page-id="{a_page_id}"]').first
            a_row.wait_for(state="visible")
            a_row.click()
            member.wait_for_function(
                "pageId => State.activePageId === pageId", arg=a_page_id
            )
            member.wait_for_function(
                """() => State.liveCollaboration?.crdtReady === true
                  && activePageCanEdit() === true
                  && State.liveCollaboration.unsyncedCount === 0""",
                timeout=20_000,
            )
            admin.locator("#commentDrawerBtn").click()
            member.locator("#commentDrawerBtn").click()
            admin.locator("#commentInput").wait_for(state="visible")
            member.locator("#commentInput").wait_for(state="visible")
            admin.locator("#commentInput").fill(
                "V0R-A-COMMENT-OWNER @user01"
            )
            owner_comment_result = admin.evaluate(
                """async () => {
                  await submitComment();
                  return {
                    created: State.comments.some(item =>
                      item.content === 'V0R-A-COMMENT-OWNER @user01'),
                    status: document.querySelector('#status')?.textContent || '',
                  };
                }"""
            )
            if not owner_comment_result.get("created"):
                raise ApprovedDevPairError(
                    "V0B_IAM_OWNER_COMMENT_FAILED:"
                    + str(owner_comment_result.get("status") or "UNKNOWN")
                )
            member.evaluate(
                """async () => {
                  await openPage(State.activePageId, false);
                  await loadComments();
                }"""
            )
            member.wait_for_function(
                "() => State.comments.some(item => "
                "item.content === 'V0R-A-COMMENT-OWNER @user01')",
                timeout=10_000,
            )
            reference_comment_id = member.evaluate(
                "() => State.comments.find(item => "
                "item.content === 'V0R-A-COMMENT-OWNER @user01')?.id || ''"
            )
            member.locator(
                f'[data-comment-reply="{reference_comment_id}"]'
            ).click()
            member.locator("#commentInput").fill(
                "V0R-A-REPLY-COLLABORATOR"
            )
            reply_result = member.evaluate(
                """async () => {
                  await submitComment();
                  return {
                    created: State.comments.some(item =>
                      item.content === 'V0R-A-REPLY-COLLABORATOR'),
                    status: document.querySelector('#status')?.textContent || '',
                  };
                }"""
            )
            if not reply_result.get("created"):
                raise ApprovedDevPairError(
                    "V0B_IAM_COLLABORATOR_REPLY_FAILED:"
                    + str(reply_result.get("status") or "UNKNOWN")
                )
            admin.evaluate("() => loadComments()")
            admin.wait_for_function(
                "() => State.comments.some(item => "
                "item.content === 'V0R-A-REPLY-COLLABORATOR')",
                timeout=10_000,
            )
            member.evaluate(
                "([commentId, emoji]) => toggleCommentReaction(commentId, emoji)",
                [reference_comment_id, "🥳"],
            )
            member.wait_for_function(
                "commentId => State.comments.some(item => item.id === commentId "
                "&& (item.reactions?.['🥳'] || []).includes('user01'))",
                arg=reference_comment_id,
                timeout=10_000,
            )
            admin.evaluate("() => loadComments()")
            admin.wait_for_function(
                "commentId => State.comments.some(item => item.id === commentId "
                "&& (item.reactions?.['🥳'] || []).includes('user01'))",
                arg=reference_comment_id,
                timeout=10_000,
            )
            admin.evaluate("() => closeDrawer(false)")
            member.evaluate("() => closeDrawer(false)")
            admin.locator("#inspector").wait_for(state="hidden")
            member.locator("#inspector").wait_for(state="hidden")
            member.locator("#inboxNavBtn").click()
            member.locator("#inboxOverlay").wait_for(state="visible")
            member.wait_for_function(
                "() => !document.querySelector('#inboxList')?.textContent?.includes('불러오는 중')"
            )
            member.wait_for_function(
                "() => document.querySelectorAll('#presenceAvatars .presence-avatar').length >= 2",
                timeout=10_000,
            )
            v0b_collector.capture(
                "V0R-MU-003",
                member,
                selectors={
                    "WORKSPACE-SHELL": "#workspaceMain",
                    "WORKSPACE-SWITCHER": "#workspaceName",
                    "SHARED-PAGE-ROW": f'[data-page-id="{a_page_id}"]',
                    "PAGE-CANVAS": "#pageView",
                },
                focus_selector="#pageView",
                keyboard_path=["Enter", "ArrowDown", "Enter"],
                live_region_expected=False,
                observation="REF-FREE-A and its synthetic root were visible to the collaborator.",
            )
            member.wait_for_function(
                """() => State.liveCollaboration?.crdtReady === true
                  && activePageCanEdit() === true
                  && State.liveCollaboration.unsyncedCount === 0""",
                timeout=20_000,
            )
            member.locator("#shareDrawerBtn").click()
            member.locator("#pageInheritedSharePrompt").wait_for(state="visible")
            member.wait_for_function(
                "() => State.shareAccess?.effective_access?.sources?.some(item => item.inherited === true)"
            )
            v0b_collector.capture(
                "V0R-MU-006",
                member,
                selectors={
                    "PAGE-SHARE-DIALOG": "#inspector",
                    "ACCESS-EXPLANATION": "#pageInheritedSharePrompt",
                    "PAGE-SHARE-LIST": "#pageInheritedSharePeople",
                },
                focus_selector="#pageInheritedSharePrompt",
                keyboard_path=["Shift+Tab", "Enter", "Tab"],
                live_region_expected=True,
                observation="REF-FREE-A workspace ownership was exposed as inherited page access.",
            )
            member.keyboard.press("Escape")
            member.locator("#inspector").wait_for(state="hidden")
            member.keyboard.press("Escape")
            member.locator("#inboxOverlay").wait_for(state="hidden")
            _reload_and_open(admin, notion, b_workspace_id, b_page_id)
            _set_first_block(admin, "V0R-B-ROLE-BASE")
            _set_reference_b_page(admin)
            admin.locator("#shareDrawerBtn").click()
            admin.locator("#pageInheritedSharePrompt").wait_for(state="visible")
            admin.locator("#pageInheritedShareUser").select_option("user01")
            admin.locator("#pageInheritedShareRole").select_option("edit_content")
            admin.locator("#pageInheritedShareInviteBtn").click()
            admin.wait_for_function(
                "() => State.shareAccess?.items?.some(item => "
                "item.principal_id === 'user01' && item.role === 'edit_content')"
            )
            v0b_collector.capture(
                "V0R-MU-004",
                admin,
                selectors={
                    "PAGE-SHARE-DIALOG": "#inspector",
                    "PAGE-SHARE-LIST": "#pageInheritedSharePeople",
                    "PAGE-SHARE-ROLE": "#pageInheritedShareRole",
                },
                focus_selector="#pageInheritedShareRole",
                keyboard_path=["Tab", "ArrowDown", "Enter", "Tab", "Space", "Tab", "Enter"],
                live_region_expected=True,
                observation="Direct REF-FREE-B page edit access was active.",
            )
            admin.evaluate("() => closeDrawer(true)")
            admin.locator("#inspector").wait_for(state="hidden")

            _reload_and_open(member, notion, b_workspace_id, b_page_id)
            member.wait_for_function(
                "() => State.pageData?.page?.iam_role === 'edit_content'"
            )
            try:
                member.wait_for_function(
                    "() => document.querySelector('#pageTitle')?.disabled === false"
                )
            except Exception as exc:
                edit_state = member.evaluate(
                    """() => ({
                      iam_role: State.pageData?.page?.iam_role || '',
                      page_can_edit: State.pageData?.page?.can_edit ?? null,
                      collaborative_editing: State.pageData?.page?.collaborative_editing ?? null,
                      active_page_can_edit: activePageCanEdit(),
                      title_disabled: document.querySelector('#pageTitle')?.disabled ?? null,
                      editor_contenteditable: document.querySelector('.block-editor')?.getAttribute('contenteditable') || '',
                      connected: State.liveCollaboration.connected,
                      crdt_ready: State.liveCollaboration.crdtReady,
                      write_authorized: State.liveCollaboration.writeAuthorized,
                      recovery_only: State.liveCollaboration.recoveryOnly,
                      recovery_reason: State.liveCollaboration.recoveryReason,
                      offline_policy_blocked: State.liveCollaboration.offlinePolicyBlocked,
                      reconnect_attempt: State.liveCollaboration.reconnectAttempt,
                      last_error: State.liveCollaboration.lastError,
                      error_history: [...State.liveCollaboration.errorHistory],
                    })"""
                )
                raise ApprovedDevPairError(
                    "V0B_IAM_DIRECT_PAGE_EDIT_CAPABILITY_MISMATCH:"
                    + json.dumps(edit_state, sort_keys=True, separators=(",", ":"))
                ) from exc
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
                observation="The collaborator could edit the directly shared REF-FREE-B page.",
            )

            admin.locator("#shareDrawerBtn").click()
            admin.wait_for_function(
                "() => State.shareAccess?.items?.some(item => item.principal_id === 'user01')"
            )
            admin.locator("#pageInheritedShareUser").select_option("user01")
            admin.locator("#pageInheritedShareRole").select_option("comment")
            admin.locator("#pageInheritedShareInviteBtn").click()
            admin.wait_for_function(
                "() => State.shareAccess?.items?.some(item => "
                "item.principal_id === 'user01' && item.role === 'comment')"
            )
            _reload_and_open(member, notion, b_workspace_id, b_page_id)
            member.wait_for_function(
                "() => State.pageData?.page?.iam_role === 'comment'"
            )
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
                observation="The REF-FREE-B page exposed comment-only access.",
            )

            admin.locator("#pageInheritedShareRole").select_option("view")
            admin.locator("#pageInheritedShareInviteBtn").click()
            admin.wait_for_function(
                "() => State.shareAccess?.items?.some(item => "
                "item.principal_id === 'user01' && item.role === 'view')"
            )
            _reload_and_open(member, notion, b_workspace_id, b_page_id)
            member.wait_for_function("() => State.pageData?.page?.iam_role === 'view'")
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
                observation="The REF-FREE-B page exposed view-only access.",
            )

            revoke = admin.locator('[data-page-share-revoke="user01"]')
            revoke.click()
            admin.wait_for_function(
                "() => !State.shareAccess?.items?.some(item => item.principal_id === 'user01')"
            )
            member.goto(notion + "/workspace", wait_until="domcontentloaded")
            member.wait_for_function("() => typeof State !== 'undefined'")
            revoked_status = member.evaluate(
                """async ({workspaceId, pageId}) => (
                  await fetch(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/collaboration`)
                ).status""",
                {"workspaceId": b_workspace_id, "pageId": b_page_id},
            )
            if revoked_status != 404:
                raise ApprovedDevPairError("V0B_IAM_PAGE_REVOKE_NOT_FENCED")
            member.evaluate("alias => showAccessBoundary(alias)", "REF-FREE-B")
            v0b_collector.capture(
                "V0R-MU-017",
                member,
                selectors={
                    "ACCESS-BOUNDARY-DIALOG": "#accessBoundaryDialog",
                    "ACCESS-BOUNDARY-ACTION": "#accessBoundaryRequestBtn",
                },
                focus_selector="#accessBoundaryDialog",
                keyboard_path=["Alt+Left", "Tab"],
                live_region_expected=False,
                observation="The revoked REF-FREE-B page returned the non-oracle boundary.",
            )

            _open_workspace_page(admin, a_workspace_id, a_page_id)
            admin.locator("#sidebarMemberInviteBtn").click()
            admin.evaluate("() => applyShareSurfacePresentation('workspace-members')")
            admin.locator("#workspaceMemberSection").wait_for(state="visible")
            admin.wait_for_function(
                """subjectId => [...document.querySelectorAll('#workspaceMemberList .mini-row')]
                  .some(row => row.textContent.includes(subjectId)
                    && row.querySelector('[data-member-revoke]')?.offsetParent !== null)""",
                arg="user01",
            )
            member_revoke_clicked = admin.evaluate(
                """subjectId => {
                  const row = [...document.querySelectorAll('#workspaceMemberList .mini-row')]
                    .find(item => item.textContent.includes(subjectId));
                  const button = row?.querySelector('[data-member-revoke]');
                  if (!button || button.offsetParent === null || button.disabled) return false;
                  button.click();
                  return true;
                }""",
                "user01",
            )
            if member_revoke_clicked is not True:
                raise ApprovedDevPairError("V0B_IAM_MEMBER_REVOKE_CONTROL_UNAVAILABLE")
            admin.wait_for_function(
                "() => !State.workspaceAccess?.members?.some(item => item.subject_id === 'user01')"
            )
            member.goto(notion + "/workspace", wait_until="domcontentloaded")
            member.wait_for_function("() => typeof State !== 'undefined'")
            member.evaluate("alias => showAccessBoundary(alias)", "REF-FREE-A")
            v0b_collector.capture(
                "V0R-MU-018",
                member,
                selectors={
                    "ACCESS-BOUNDARY-DIALOG": "#accessBoundaryDialog",
                    "ACCESS-BOUNDARY-ACTION": "#accessBoundaryRequestBtn",
                },
                focus_selector="#accessBoundaryDialog",
                keyboard_path=["Alt+Left", "Tab", "Enter"],
                live_region_expected=False,
                observation="The revoked REF-FREE-A workspace returned the non-oracle boundary.",
            )
            screenshot_hashes["page_revoke_boundary"] = _screenshot_sha256(member)

            cleaned_to_trash = _cleanup_iam_fixtures(
                admin,
                fixtures=[
                    [b_workspace_id, b_page_id, "b"],
                    [a_workspace_id, a_page_id, "a"],
                ],
                run_id=run_id,
            )
            if not cleaned_to_trash:
                raise ApprovedDevPairError("V0B_IAM_PROCESS_CLEANUP_FAILED")

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
                    "V0B_IAM_NETWORK_TARGET_ESCAPE:" + ",".join(escaped)
                )
            for context in reversed(contexts):
                context.close()
            contexts.clear()
            browser.close()
            browser_closed = not browser.is_connected()
            browser = None
    finally:
        if (
            not cleaned_to_trash
            and a_workspace_id
            and a_page_id
            and b_workspace_id
            and b_page_id
        ):
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
                        agent=agent,
                        notion=notion,
                        principal="admin",
                        password=credentials["admin"],
                    )
                    cleaned_to_trash = _cleanup_iam_fixtures(
                        cleanup_page,
                        fixtures=[
                            [b_workspace_id, b_page_id, "b"],
                            [a_workspace_id, a_page_id, "a"],
                        ],
                        run_id=run_id,
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
        raise ApprovedDevPairError("V0B_IAM_PROCESS_CLEANUP_FAILED")
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.v0b_p1_two_workspace_iam_browser.v1",
        "status": "pass",
        "principal_ids": ["admin", "user01"],
        "principal_count": 2,
        "workspace_aliases": ["REF-FREE-A", "REF-FREE-B"],
        "initial_workspace_count": initial_workspace_count,
        "workspace_realm_rotation_threshold": V0B_WORKSPACE_REALM_ROTATION_THRESHOLD,
        "workspace_invite_verified": True,
        "temporary_owner_role_verified": True,
        "inherited_access_explanation_verified": True,
        "direct_page_edit_verified": True,
        "role_change_to_comment_verified": True,
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
        "synthetic_member_absent_after_cleanup": cleaned_to_trash,
        "screenshot_sha256": screenshot_hashes,
        "network_request_count": len(requests),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


__all__ = ["run_v0b_p1_iam_journey"]
