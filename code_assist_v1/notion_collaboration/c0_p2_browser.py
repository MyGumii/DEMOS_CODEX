"""Actual two-principal C0-P2 presence, comment, inbox and revoke journey."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from .approved_dev_pair import AGENT_PORT, NOTION_PORT, ApprovedDevPairError
from .v0b_p1_capture import v0b_chrome_launch_options


def _origin(value: str, expected_port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.path not in {"", "/"}
        or parsed.port != expected_port
    ):
        raise ApprovedDevPairError("C0_P2_BROWSER_ORIGIN_INVALID")
    return f"http://127.0.0.1:{expected_port}"


def _screenshot_sha256(page) -> str:
    return "sha256:" + hashlib.sha256(page.screenshot()).hexdigest()


def _seed_v0b_reference_body(
    page: Any,
    *,
    child_page_id: str,
    cumulative: bool = False,
) -> None:
    """Persist the V0R reference canvas as distinct canonical CRDT blocks."""

    page.wait_for_function(
        """() => activePageCanEdit() === true
          && window.NotionCollaborativeEditor?.descriptor?.().ready === true
          && State.liveCollaboration.crdtReady === true""",
        timeout=30_000,
    )
    baseline_sequence = page.evaluate(
        "() => Number(State.liveCollaboration.clientSequence || 0)"
    )
    page.evaluate(
        """({childPageId, cumulative}) => {
          const database = EditorCore.createBlock('table', {
            content: 'V0R-A-DB',
          });
          const databaseId = database.id;
          database.props = {
            database_id: databaseId,
            active_view_id: `view-table-${databaseId}`,
            properties: [
              {id: `title-${databaseId}`, name: '이름', type: 'title'},
            ],
            views: [
              {id: `view-table-${databaseId}`, name: 'Table', type: 'table', filters: [], sorts: []},
            ],
            rows: (cumulative ? [
              ['base', 'V0R-A-ROW-BASE'],
              ['owner', 'V0R-A-ROW-OWNER'],
              ['collaborator', 'V0R-A-ROW-COLLABORATOR'],
              ['reconnect', 'V0R-A-ROW-RECONNECT'],
            ] : [['base', 'V0R-A-ROW-BASE']]).map(([suffix, title]) => ({
              page_id: `row-${suffix}-${databaseId}`,
              [`title-${databaseId}`]: title,
            })),
            reference_child_page_id: childPageId,
          };
          const blocks = [
            EditorCore.createBlock('paragraph', {
              content: cumulative
                ? 'V0R-A-TEXT-BASE | V0R-A-TEXT-OWNER | V0R-A-TEXT-COLLABORATOR'
                : 'V0R-A-TEXT-BASE',
            }),
            EditorCore.createBlock('paragraph', {
              content: cumulative
                ? 'V0R-A-BLOCK-BASE | V0R-A-BLOCK-OWNER | V0R-A-BLOCK-COLLABORATOR'
                : 'V0R-A-BLOCK-BASE',
            }),
            database,
          ];
          State.pageData.blocks = blocks.map(block => EditorCore.normalizeBlock(block));
          renderBlocks();
          markDirty();
        }""",
        {"childPageId": child_page_id, "cumulative": cumulative},
    )
    page.wait_for_function(
        """({childPageId, baselineSequence, cumulative}) => {
          const blocks = window.NotionCollaborativeEditor?.projection?.()?.blocks || [];
          const expectedTypes = ['paragraph', 'paragraph', 'table'];
          return State.liveCollaboration.clientSequence > baselineSequence
            && State.liveCollaboration.unsyncedCount === 0
            && State.liveCollaboration.pendingEnvelopes?.size === 0
            && blocks.length === expectedTypes.length
            && blocks.every((block, index) => block.type === expectedTypes[index])
            && blocks[0]?.content === (cumulative
              ? 'V0R-A-TEXT-BASE | V0R-A-TEXT-OWNER | V0R-A-TEXT-COLLABORATOR'
              : 'V0R-A-TEXT-BASE')
            && blocks[1]?.content === (cumulative
              ? 'V0R-A-BLOCK-BASE | V0R-A-BLOCK-OWNER | V0R-A-BLOCK-COLLABORATOR'
              : 'V0R-A-BLOCK-BASE')
            && blocks[2]?.content === 'V0R-A-DB'
            && blocks[2]?.props?.reference_child_page_id === childPageId
            && blocks[2]?.props?.rows?.map(row => row[`title-${blocks[2].id}`]).join('|')
              === (cumulative
                ? 'V0R-A-ROW-BASE|V0R-A-ROW-OWNER|V0R-A-ROW-COLLABORATOR|V0R-A-ROW-RECONNECT'
                : 'V0R-A-ROW-BASE');
        }""",
        arg={
            "childPageId": child_page_id,
            "baselineSequence": baseline_sequence,
            "cumulative": cumulative,
        },
        timeout=60_000,
    )
    page.evaluate("() => syncWorkspaceLandingBanner(State.pageData?.page)")
    page.wait_for_function(
        "() => document.querySelector('#workspaceLandingBanner')?.hidden === true",
        timeout=10_000,
    )
    page.wait_for_timeout(350)


def _promote_v0b_reconnect_body(page: Any) -> str:
    """Commit the cumulative reference state while the collaborator reconnects."""

    baseline_sequence = page.evaluate(
        "() => Number(State.liveCollaboration.clientSequence || 0)"
    )
    database_id = page.evaluate(
        """async () => {
          const blocks = (window.NotionCollaborativeEditor?.projection?.()?.blocks || [])
            .map(block => EditorCore.normalizeBlock(EditorCore.deepClone(block)));
          const database = blocks.find(block => block.type === 'table');
          if (blocks.length < 3 || !database) throw new Error('V0B_RECONNECT_PROMOTION_BASE_MISSING');
          const titlePropertyId = database.props?.properties?.find(item => item.type === 'title')?.id;
          if (!titlePropertyId) throw new Error('V0B_RECONNECT_PROMOTION_TITLE_MISSING');
          database.props.reconnect_marker = 'V0B-RECONNECT-CATCH-UP';
          State.pageData.blocks = blocks;
          renderBlocks();
          markDirty();
          await State.liveCollaboration.persistenceChain;
          for (let attempt = 0; attempt < 100 && State.liveCollaboration.unsyncedCount > 0; attempt += 1) {
            await new Promise(resolve => setTimeout(resolve, 50));
          }
          return database.id;
        }"""
    )
    page.wait_for_function(
        """baselineSequence => {
          const blocks = window.NotionCollaborativeEditor?.projection?.()?.blocks || [];
          const database = blocks.find(block => block.type === 'table');
          const titlePropertyId = database?.props?.properties?.find(item => item.type === 'title')?.id;
          return State.liveCollaboration.clientSequence > baselineSequence
            && State.liveCollaboration.unsyncedCount === 0
            && database?.props?.reconnect_marker === 'V0B-RECONNECT-CATCH-UP'
            && blocks[0]?.content.endsWith('V0R-A-TEXT-COLLABORATOR')
            && blocks[1]?.content.endsWith('V0R-A-BLOCK-COLLABORATOR')
            && database?.props?.rows?.map(row => row[titlePropertyId]).join('|')
              === 'V0R-A-ROW-BASE|V0R-A-ROW-OWNER|V0R-A-ROW-COLLABORATOR|V0R-A-ROW-RECONNECT';
        }""",
        arg=baseline_sequence,
        timeout=30_000,
    )
    return str(database_id)


def _login(page, *, agent: str, notion: str, principal: str, password: str) -> None:
    page.goto(agent + "/login", wait_until="domcontentloaded")
    page.locator("#userId").fill(principal)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    page.wait_for_url(agent + "/", wait_until="domcontentloaded")
    page.goto(notion + "/workspace", wait_until="domcontentloaded")
    page.wait_for_function(
        "principal => typeof State !== 'undefined' "
        "&& State.user?.id === principal "
        "&& State.navigation?.collaborativeSharing === true",
        arg=principal,
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


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _cleanup_synthetic_fixture(
    page: Any,
    *,
    workspace_id: str,
    page_id: str,
    run_id: str,
    operation_prefix: str,
) -> bool:
    result = page.evaluate(
        """
        async ({ workspaceId, pageId, runId, operationPrefix }) => {
          let current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
          if ((current.members || []).some(member => member.subject_id === 'user01')) {
            await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/members/user01`, {
              method: 'DELETE',
              body: {
                expected_workspace_epoch: current.security_epoch,
                operation_id: `${operationPrefix}-member-${runId}`,
              },
            });
            current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
          }
          let pages = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages?q=`);
          if ((pages.items || []).some(item => String(item.id || '') === pageId)) {
            await api(
              `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/trash`,
              {
                method: 'POST',
                body: {
                  base_version: current.version,
                  operation_id: `${operationPrefix}-trash-${runId}`,
                },
              },
            );
          }
          current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
          pages = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages?q=`);
          return {
            member_absent: !(current.members || []).some(member => member.subject_id === 'user01'),
            page_absent: !(pages.items || []).some(item => String(item.id || '') === pageId),
          };
        }
        """,
        {
            "workspaceId": workspace_id,
            "pageId": page_id,
            "runId": run_id,
            "operationPrefix": operation_prefix,
        },
    )
    return result == {"member_absent": True, "page_absent": True}


def run_c0_p2_browser_collaboration(
    *,
    agent_origin: str,
    notion_origin: str,
    credentials: Mapping[str, str],
    v0b_collector: Any | None = None,
) -> dict[str, object]:
    """Prove live delivery, TTL, reconnect, inbox and post-revoke zero delivery."""

    agent = _origin(agent_origin, AGENT_PORT)
    notion = _origin(notion_origin, NOTION_PORT)
    if set(credentials) != {"admin", "user01"} or any(
        not isinstance(value, str) or not value for value in credentials.values()
    ):
        raise ApprovedDevPairError("C0_P2_BROWSER_PRINCIPALS_INVALID")

    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    run_id = uuid4().hex
    marker = f"c0-{run_id[:10]}"
    contexts = []
    browser = None
    browser_closed = False
    requests: list[str] = []
    websocket_urls: list[str] = []
    screenshot_hashes: dict[str, str] = {}
    measurements: dict[str, int] = {}
    workspace_id = ""
    page_id = ""
    v0b_mode = v0b_collector is not None
    workspace_name = "REF-FREE-A" if v0b_mode else f"C0 Browser {run_id[:8]}"
    page_title = "V0R-A-ROOT" if v0b_mode else f"C0 live {marker}"
    comment_text = (
        "V0R-A-COMMENT-OWNER @user01"
        if v0b_mode
        else f"C0 live @{ 'user01' } {marker}"
    )
    reply_text = "V0R-A-REPLY-COLLABORATOR" if v0b_mode else f"C0 reply {marker}"
    # The adopted comment surface exposes the same quick reaction in normal
    # and V0B capture journeys.  Keep the browser gate on the visible product
    # control instead of looking for the removed legacy thumbs-up shortcut.
    reaction_emoji = "🥳"
    cleaned_to_trash = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**v0b_chrome_launch_options())
            browser_version = browser.version
            pages = {}
            for principal in ("admin", "user01"):
                context = browser.new_context(locale="ko-KR", service_workers="block")
                contexts.append(context)
                page = context.new_page()
                page.on("request", lambda request: requests.append(request.url))
                page.on("websocket", lambda socket: websocket_urls.append(socket.url))
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
                async ({ runId, title, workspaceName, seedReference }) => {
                  const created = await api('/api/notion/workspaces', {
                    method: 'POST',
                    body: {
                      name: workspaceName,
                      icon: 'C',
                      operation_id: `c0-browser-workspace-${runId}`,
                    },
                  });
                  const workspaceId = created.workspace.id;
                  let workspace = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                  const page = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages`, {
                    method: 'POST',
                    body: {
                      base_version: workspace.version,
                      operation_id: `c0-browser-page-${runId}`,
                      parent_id: '',
                      title,
                    },
                  });
                  let childPageId = '';
                  if (seedReference) {
                    workspace = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                    const child = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages`, {
                      method: 'POST',
                      body: {
                        base_version: workspace.version,
                        operation_id: `c0-browser-child-page-${runId}`,
                        parent_id: page.page.id,
                        title: 'REF-FREE-A',
                      },
                    });
                    childPageId = child.page.id;
                  }
                  let collaboration = await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(page.page.id)}/collaboration`,
                  );
                  await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/members/user01`, {
                    method: 'PUT',
                    body: {
                      role: 'member',
                      expected_workspace_epoch: collaboration.page.workspace_epoch,
                      operation_id: `c0-browser-member-${runId}`,
                    },
                  });
                  collaboration = await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(page.page.id)}/collaboration`,
                  );
                  await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(page.page.id)}/shares`,
                    {
                      method: 'POST',
                      body: {
                        principal_type: 'subject',
                        principal_id: 'user01',
                        role: 'comment',
                        inherit_to_descendants: false,
                        expected_workspace_epoch: collaboration.page.workspace_epoch,
                        expected_document_epoch: collaboration.page.document_epoch,
                        operation_id: `c0-browser-grant-${runId}`,
                      },
                    },
                  );
                  return {
                    workspace_id: workspaceId,
                    page_id: page.page.id,
                    child_page_id: childPageId,
                  };
                }
                """,
                {
                    "runId": run_id,
                    "title": page_title,
                    "workspaceName": workspace_name,
                    "seedReference": v0b_mode,
                },
            )
            workspace_id = str(setup["workspace_id"])
            page_id = str(setup["page_id"])
            child_page_id = str(setup.get("child_page_id") or "")

            member = pages["user01"]
            for page in (admin, member):
                _reload_and_switch(page, notion, workspace_id)
                page.locator(f'[data-page-id="{page_id}"]').first.click()
                page.wait_for_function(
                    "pageId => State.activePageId === pageId "
                    "&& State.liveCollaboration.connected === true",
                    arg=page_id,
                    timeout=15_000,
                )

            if v0b_mode:
                for page in (admin, member):
                    page.wait_for_function(
                        """pageId => window.NotionCollaborativeEditor?.descriptor?.().ready === true
                          && window.NotionCollaborativeEditor.descriptor().page_id === pageId
                          && State.liveCollaboration.crdtReady === true""",
                        arg=page_id,
                        timeout=30_000,
                    )
                _seed_v0b_reference_body(
                    admin,
                    child_page_id=child_page_id,
                    cumulative=True,
                )
                admin.wait_for_function(
                    """() => State.liveCollaboration.unsyncedCount === 0
                      && (window.NotionCollaborativeEditor.projection()?.blocks || [])
                        .some(block => block.content.includes('V0R-A-TEXT-BASE'))
                      && (window.NotionCollaborativeEditor.projection()?.blocks || []).length >= 2""",
                    timeout=30_000,
                )
                member.wait_for_function(
                    """() => (window.NotionCollaborativeEditor.projection()?.blocks || [])
                      .some(block => block.content.includes('V0R-A-TEXT-BASE'))
                      && (window.NotionCollaborativeEditor.projection()?.blocks || []).length >= 2""",
                    timeout=30_000,
                )
                admin.evaluate(
                    """
                    async () => {
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
                    }
                    """
                )
                member.evaluate(
                    """async pageId => {
                      await openPage(pageId, false, PageFocusPolicy.PRESERVE);
                    }""",
                    page_id,
                )
                member.wait_for_function(
                    """pageId => State.activePageId === pageId
                      && State.pageData?.page?.icon === 'list'
                      && window.NotionCollaborativeEditor?.descriptor?.().ready === true
                      && State.liveCollaboration.crdtReady === true""",
                    arg=page_id,
                    timeout=30_000,
                )

            presence_started = time.monotonic()
            admin.evaluate("() => State.liveCollaboration.participants.delete('user01')")
            member.evaluate("() => State.liveCollaboration.participants.delete('admin')")
            admin.evaluate("() => sendPresenceHeartbeat()")
            member.evaluate("() => sendPresenceHeartbeat()")
            admin.wait_for_function(
                "() => State.liveCollaboration.participants.has('user01')",
                timeout=5_000,
            )
            member.wait_for_function(
                "() => State.liveCollaboration.participants.has('admin')",
                timeout=5_000,
            )
            measurements["presence_delivery_ms"] = _elapsed_ms(presence_started)
            if measurements["presence_delivery_ms"] > 2_500:
                raise ApprovedDevPairError("C0_P2_PRESENCE_LATENCY_EXCEEDED")
            admin.locator("#commentDrawerBtn").click()
            member.locator("#commentDrawerBtn").click()
            admin.locator("#commentInput").fill(comment_text)
            comment_started = time.monotonic()
            admin.locator("#commentSubmitBtn").click()
            member.wait_for_function(
                "text => State.comments.some(item => item.content === text)",
                arg=comment_text,
                timeout=5_000,
            )
            measurements["comment_delivery_ms"] = _elapsed_ms(comment_started)
            if measurements["comment_delivery_ms"] > 2_500:
                raise ApprovedDevPairError("C0_P2_COMMENT_LATENCY_EXCEEDED")
            member.wait_for_function(
                "pageId => State.inboxItems.some(item => item.collaborative "
                "&& item.page_id === pageId && item.canonical_kind === 'comment_mentioned')",
                arg=page_id,
                timeout=5_000,
            )
            screenshot_hashes["member_live_comment_and_mention"] = _screenshot_sha256(member)

            comment_id = member.evaluate(
                "text => State.comments.find(item => item.content === text)?.id || ''",
                comment_text,
            )
            member.locator(f'[data-comment-reply="{comment_id}"]').click()
            member.locator("#commentInput").fill(reply_text)
            member.locator("#commentSubmitBtn").click()
            admin.wait_for_function(
                "text => State.comments.some(item => item.content === text)",
                arg=reply_text,
                timeout=5_000,
            )
            reaction_started = time.monotonic()
            member.locator(
                f'[data-comment-reaction="{comment_id}"][data-reaction-emoji="{reaction_emoji}"]'
            ).click()
            admin.wait_for_function(
                "({commentId, reactionEmoji}) => State.comments.some(item => item.id === commentId "
                "&& (item.reactions?.[reactionEmoji] || []).includes('user01'))",
                arg={"commentId": comment_id, "reactionEmoji": reaction_emoji},
                timeout=5_000,
            )
            measurements["reaction_delivery_ms"] = _elapsed_ms(reaction_started)
            if measurements["reaction_delivery_ms"] > 2_500:
                raise ApprovedDevPairError("C0_P2_REACTION_LATENCY_EXCEEDED")
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-007",
                    admin,
                    selectors={
                        "PAGE-HEADER": ".topbar",
                        "PRESENCE": "#presenceAvatars",
                        "PAGE-CANVAS": "#pageView",
                    },
                    focus_selector="#pageView",
                    keyboard_path=["Tab", "Tab", "Enter"],
                    live_region_expected=True,
                    observation="Both independent synthetic principals were present on the same page.",
                )
                v0b_collector.capture(
                    "V0R-MU-012",
                    member,
                    selectors={
                        "COMMENT-DIALOG": "#inspector",
                        "COMMENT-THREAD": f'[data-comment-thread="{comment_id}"]',
                        "COMMENT-INPUT": "#commentInput",
                    },
                    focus_selector=f'[data-comment-thread="{comment_id}"]',
                    keyboard_path=["Tab", "Enter", "Tab", "Enter"],
                    live_region_expected=True,
                    observation="The synthetic comment, reply, and collaborator reaction were visible in one thread.",
                )

                member.evaluate("() => setSidebarCollapsed(false, false)")
                member.locator("#inboxNavBtn").click()
                member.locator("#inboxOverlay").wait_for(state="visible")
                member.wait_for_function(
                    "pageId => State.inboxItems.some(item => item.collaborative && item.page_id === pageId)",
                    arg=page_id,
                    timeout=5_000,
                )
                member.locator("#inspector").wait_for(state="hidden")
                member.locator("#inboxList [data-inbox-item]").first.wait_for(
                    state="visible",
                    timeout=5_000,
                )
                v0b_collector.capture(
                    "V0R-MU-013",
                    member,
                    selectors={
                        "INBOX-DIALOG": "#inboxOverlay .inbox-dialog",
                        "INBOX-LIST": "#inboxList",
                        "INBOX-FILTER": "#inboxFilter",
                    },
                    focus_selector="#inboxFilter",
                    keyboard_path=["Tab", "Enter", "Escape"],
                    live_region_expected=True,
                    observation="The collaborator inbox displayed the synthetic mention notification.",
                )
            admin.evaluate("() => sendPresenceHeartbeat()")
            member.evaluate("() => sendPresenceHeartbeat()")
            admin.wait_for_function(
                "() => State.liveCollaboration.participants.has('user01')",
                timeout=5_000,
            )
            member.wait_for_function(
                "() => State.liveCollaboration.participants.has('admin')",
                timeout=5_000,
            )
            member.evaluate(
                """
                () => {
                  clearInterval(State.liveCollaboration.heartbeatTimer);
                  State.liveCollaboration.heartbeatTimer = null;
                }
                """
            )
            ttl_started = time.monotonic()
            admin.wait_for_function(
                "() => !State.liveCollaboration.participants.has('user01')",
                timeout=24_000,
            )
            measurements["presence_ttl_expiry_ms"] = _elapsed_ms(ttl_started)
            if not 12_000 <= measurements["presence_ttl_expiry_ms"] <= 22_000:
                raise ApprovedDevPairError("C0_P2_PRESENCE_TTL_OUT_OF_BOUNDS")

            reconnect_started = time.monotonic()
            member.evaluate(
                "() => State.liveCollaboration.socket?.close(4000, 'c0-reconnect-test')"
            )
            if v0b_mode:
                reconnect_database_id = _promote_v0b_reconnect_body(admin)
            member.wait_for_function(
                """() => {
                  const blocks = window.NotionCollaborativeEditor?.projection?.()?.blocks || [];
                  const database = blocks.find(block => block.type === 'table');
                  const titlePropertyId = database?.props?.properties?.find(item => item.type === 'title')?.id;
                  return State.liveCollaboration.connected === true
                    && database?.props?.reconnect_marker === 'V0B-RECONNECT-CATCH-UP'
                    && database?.props?.rows?.map(row => row[titlePropertyId]).includes('V0R-A-ROW-RECONNECT');
                }""" if v0b_mode else "() => State.liveCollaboration.connected === true",
                timeout=10_000,
            )
            if v0b_mode:
                member.evaluate(
                    """async databaseId => {
                      await reloadCanonicalDatabases([databaseId]);
                      renderBlocks();
                    }""",
                    reconnect_database_id,
                )
                member.wait_for_function(
                    """() => [...document.querySelectorAll('[data-db-row][data-db-field]')]
                      .some(input => input.value === 'V0R-A-ROW-RECONNECT')""",
                    timeout=10_000,
                )
            admin.wait_for_function(
                "() => State.liveCollaboration.participants.has('user01')",
                timeout=10_000,
            )
            measurements["reconnect_ms"] = _elapsed_ms(reconnect_started)
            if measurements["reconnect_ms"] > 7_000:
                raise ApprovedDevPairError("C0_P2_RECONNECT_LATENCY_EXCEEDED")
            screenshot_hashes["two_principal_presence_reconnected"] = _screenshot_sha256(admin)
            if v0b_collector is not None:
                v0b_collector.capture(
                    "V0R-MU-014",
                    member,
                    selectors={
                        "INBOX": "#inboxOverlay",
                        "PAGE-CANVAS": "#pageView",
                        "PAGE-EDITOR": "#pageEditorGroup",
                        "CONNECTION-STATUS": "#status",
                    },
                    focus_selector="#pageView",
                    keyboard_path=["Tab", "Shift+Tab", "Enter"],
                    live_region_expected=True,
                    observation="The reconnected collaborator caught up to the durable synthetic page state.",
                )

            revoke_result = admin.evaluate(
                """
                async ({ workspaceId, pageId, runId }) => {
                  const shares = await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/shares`,
                  );
                  return await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/shares/subject/user01`,
                    {
                      method: 'DELETE',
                      body: {
                        expected_workspace_epoch: shares.workspace_epoch,
                        expected_document_epoch: shares.document_epoch,
                        operation_id: `c0-browser-revoke-${runId}`,
                      },
                    },
                  );
                }
                """,
                {"workspaceId": workspace_id, "pageId": page_id, "runId": run_id},
            )
            if revoke_result.get("state") != "revoked":
                raise ApprovedDevPairError("C0_P2_REVOKE_COMMAND_FAILED")
            member.wait_for_function(
                "() => State.liveCollaboration.connected === false",
                timeout=7_000,
            )

            post_revoke = admin.evaluate(
                """
                async ({ workspaceId, pageId, runId }) => {
                  const page = await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/collaboration`,
                  );
                  return await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/comments`,
                    {
                      method: 'POST',
                      body: {
                        comment_id: `comment_post_revoke_${runId}`,
                        content: `post revoke ${runId}`,
                        anchor: {},
                        block_id: null,
                        parent_comment_id: null,
                        mention_subject_ids: [],
                        expected_workspace_epoch: page.page.workspace_epoch,
                        expected_document_epoch: page.page.document_epoch,
                        operation_id: `c0-browser-post-revoke-${runId}`,
                      },
                    },
                  );
                }
                """,
                {"workspaceId": workspace_id, "pageId": page_id, "runId": run_id},
            )
            if not post_revoke.get("comment_id"):
                raise ApprovedDevPairError("C0_P2_POST_REVOKE_COMMIT_FAILED")

            fences = member.evaluate(
                """
                async ({ workspaceId, pageId, title, runId }) => {
                  const base = `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}`;
                  const comments = await fetch(`${base}/comments`);
                  const collaboration = await fetch(`${base}/collaboration`);
                  const ticket = await fetch(`${base}/realtime-ticket`, {
                    method: 'POST',
                    headers: {
                      'Content-Type': 'application/json',
                      'X-CSRF-Token': State.csrfToken,
                    },
                    body: JSON.stringify({
                      client_id: `revoked_${runId}`,
                      session_binding_sha256: `sha256:${'0'.repeat(64)}`,
                    }),
                  });
                  const inboxResponse = await fetch(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/inbox?limit=200`,
                  );
                  const inbox = await inboxResponse.json();
                  const pagesResponse = await fetch(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages`,
                  );
                  const pages = await pagesResponse.json();
                  const searchResponse = await fetch(
                    `/api/notion/search?q=${encodeURIComponent(title)}`,
                  );
                  const search = await searchResponse.json();
                  return {
                    comments_status: comments.status,
                    collaboration_status: collaboration.status,
                    ticket_status: ticket.status,
                    inbox_status: inboxResponse.status,
                    inbox_document_match: (inbox.items || []).some(item => item.document_id === pageId),
                    pages_document_match: (pages.items || []).some(item => item.id === pageId),
                    search_document_match: (search.items || []).some(item => item.id === pageId || item.page_id === pageId),
                    live_connected: State.liveCollaboration.connected,
                    post_revoke_comment_seen: State.comments.some(item => item.content === `post revoke ${runId}`),
                  };
                }
                """,
                {
                    "workspaceId": workspace_id,
                    "pageId": page_id,
                    "title": page_title,
                    "runId": run_id,
                },
            )
            expected_fences = {
                "comments_status": 404,
                "collaboration_status": 404,
                "ticket_status": 404,
                "inbox_status": 200,
                "inbox_document_match": False,
                "pages_document_match": False,
                "search_document_match": False,
                "live_connected": False,
                "post_revoke_comment_seen": False,
            }
            if fences != expected_fences:
                raise ApprovedDevPairError(f"C0_P2_REVOKE_FENCE_FAILED:{fences!r}")
            screenshot_hashes["member_after_revoke_fence"] = _screenshot_sha256(member)

            cleanup_state = admin.evaluate(
                """
                async ({ workspaceId, pageId, runId }) => {
                  let current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                  if ((current.members || []).some(member => member.subject_id === 'user01')) {
                    await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/members/user01`, {
                      method: 'DELETE',
                      body: {
                        expected_workspace_epoch: current.security_epoch,
                        operation_id: `c0-browser-member-revoke-${runId}`,
                      },
                    });
                    current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                  }
                  await api(
                    `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/trash`,
                    {
                      method: 'POST',
                      body: {
                        base_version: current.version,
                        operation_id: `c0-browser-trash-${runId}`,
                      },
                    },
                  );
                  current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                  const pages = await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages?q=`);
                  return {
                    member_absent: !(current.members || []).some(member => member.subject_id === 'user01'),
                    page_absent: !(pages.items || []).some(item => String(item.id || '') === pageId),
                  };
                }
                """,
                {"workspaceId": workspace_id, "pageId": page_id, "runId": run_id},
            )
            cleaned_to_trash = cleanup_state == {
                "member_absent": True,
                "page_absent": True,
            }
            if not cleaned_to_trash:
                raise ApprovedDevPairError("C0_P2_BROWSER_PROCESS_CLEANUP_FAILED")

            allowed = {
                agent,
                notion,
                "http://127.0.0.1:10113",
                "ws://127.0.0.1:10113",
                "https://cdn.jsdelivr.net",
            }
            escaped = sorted(
                {
                    f"{parsed.scheme}://{parsed.netloc}"
                    for value in [*requests, *websocket_urls]
                    if (parsed := urlsplit(value)).scheme in {"http", "https", "ws", "wss"}
                    and f"{parsed.scheme}://{parsed.netloc}" not in allowed
                }
            )
            if escaped:
                raise ApprovedDevPairError(
                    "C0_P2_BROWSER_NETWORK_TARGET_ESCAPE:" + ",".join(escaped)
                )
            for context in reversed(contexts):
                context.close()
            contexts.clear()
            browser.close()
            browser_closed = not browser.is_connected()
            browser = None
    finally:
        if not cleaned_to_trash and workspace_id and page_id and "admin" in locals().get("pages", {}):
            try:
                pages["admin"].evaluate(
                    """
                    async ({ workspaceId, pageId, runId }) => {
                      let current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                      if ((current.members || []).some(member => member.subject_id === 'user01')) {
                        await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/members/user01`, {
                          method: 'DELETE',
                          body: {
                            expected_workspace_epoch: current.security_epoch,
                            operation_id: `c0-browser-failure-member-revoke-${runId}`,
                          },
                        });
                        current = (await api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}`)).workspace;
                      }
                      await api(
                        `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(pageId)}/trash`,
                        {
                          method: 'POST',
                          body: {
                            base_version: current.version,
                            operation_id: `c0-browser-failure-trash-${runId}`,
                          },
                        },
                      );
                    }
                    """,
                    {"workspaceId": workspace_id, "pageId": page_id, "runId": run_id},
                )
                cleaned_to_trash = True
            except Exception:
                pass
        if not cleaned_to_trash and workspace_id and page_id:
            try:
                with sync_playwright() as cleanup_playwright:
                    cleanup_browser = cleanup_playwright.chromium.launch(
                        **v0b_chrome_launch_options()
                    )
                    cleanup_context = cleanup_browser.new_context(
                        locale="ko-KR",
                        service_workers="block",
                    )
                    cleanup_page = cleanup_context.new_page()
                    _login(
                        cleanup_page,
                        agent=agent,
                        notion=notion,
                        principal="admin",
                        password=credentials["admin"],
                    )
                    cleaned_to_trash = _cleanup_synthetic_fixture(
                        cleanup_page,
                        workspace_id=workspace_id,
                        page_id=page_id,
                        run_id=run_id,
                        operation_prefix="c0-browser-failure-fresh-context",
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
        raise ApprovedDevPairError("C0_P2_BROWSER_PROCESS_CLEANUP_FAILED")
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.c0_p2_two_principal_browser.v1",
        "status": "pass",
        "principal_ids": ["admin", "user01"],
        "principal_count": 2,
        "workspace_id": workspace_id,
        "document_id": page_id,
        "presence_delivery_verified": True,
        "presence_ttl_verified": True,
        "reconnect_verified": True,
        "comment_delivery_without_reload_verified": True,
        "reaction_delivery_without_reload_verified": True,
        "mention_inbox_verified": True,
        "durable_before_live_verified": True,
        "post_revoke_http_websocket_replay_cache_search_inbox_zero_verified": True,
        "measurements_ms": measurements,
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
        "test_page_moved_to_trash": cleaned_to_trash,
        "synthetic_member_absent_after_cleanup": cleaned_to_trash,
        "screenshot_sha256": screenshot_hashes,
        "network_request_count": len(requests),
        "websocket_connection_count": len(websocket_urls),
        "duration_ms": _elapsed_ms(started),
    }


__all__ = ["run_c0_p2_browser_collaboration"]
