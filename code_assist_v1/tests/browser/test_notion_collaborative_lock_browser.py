"""UI-PAGE-SETTINGS: current-tab unlock and remote relock in an isolated full app."""
from __future__ import annotations

from contextlib import closing, contextmanager
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Iterator
import urllib.request

import pytest

from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as database_upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore, collaborative_page_payload
from code_assist_v1.notion_collaboration.file_authority import CollaborativeFileAuthority
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import _environment
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import (
    _free_port, _seed_synthetic_auth_registry,
)
from code_assist_v1.tools.notion_parity.ui_local_fixture import (
    _prepare_isolated_runtime_layout, build_fixture, isolated_environment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CHILD_FLAGS = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
               if sys.platform == "win32" else 0)


def _spawn(command: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    with log_path.open("wb") as stream:
        return subprocess.Popen(command, cwd=PROJECT_ROOT, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, creationflags=CHILD_FLAGS)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _settings_request(authority, page_id: str, updates: dict, operation_id: str) -> dict:
    page = authority.read_document("owner", page_id)
    return {"updates": updates, "operation_id": operation_id,
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"]}


@contextmanager
def _isolated_collaborative_app(tmp_path: Path, *, locked: bool = True,
                                gateway_enabled: bool = True,
                                navigation_database_favorites: bool = False,
                                owned_database_copy: bool = False,
                                synced_detach: bool = False,
                                legacy_adoption: bool = False,
                                page_copy: bool = False,
                                owned_database_move: bool = False,
                                database_activity: bool = False,
                                owned_database_trash: bool = False,
                                relation_pair: bool = False,
                                row_page_placement: bool = False,
                                custom_emoji: bool = False,
                                item_page_layout: bool = False,
                                template_default: bool = False,
                                navigation_ui: bool = False,
                                staged_database_sharing: bool = False,
                                direct_database_favorites: bool = False) -> Iterator[tuple[str, str, object, str, str]]:
    if staged_database_sharing and not template_default:
        raise ValueError("staged database sharing requires the 2427 fixture")
    if synced_detach and not navigation_database_favorites:
        raise ValueError("staged synced detach requires the native file-copy fixture")
    if legacy_adoption and not navigation_database_favorites:
        raise ValueError("staged legacy adoption requires the native file-copy fixture")
    if page_copy and not navigation_database_favorites:
        raise ValueError("staged page copy requires the native file-copy fixture")
    if direct_database_favorites and not staged_database_sharing:
        raise ValueError("direct database favorites require the staged database fixture")
    _authority, _transport, catalog_root, catalog, workspace_id = _environment(tmp_path)
    migrations = ("second_brain_publication", "database_features", "file_authority",
                  "history_authority", "search_authority", "icon_image",
                  "guest_navigation", "page_archive", "native_page")
    if navigation_database_favorites or owned_database_copy:
        migrations += ("member_navigation", "file_copy", "native_team", "row_body", "row_appearance")
    if owned_database_copy:
        migrations += ("same_document_file_copy",)
    if database_activity:
        if not owned_database_copy:
            raise ValueError("database activity requires the 2420 fixture")
        migrations += ("database_activity",)
    if owned_database_trash:
        if not database_activity:
            raise ValueError("database Trash requires the 2421 fixture")
        migrations += ("database_trash",)
    if relation_pair:
        if not owned_database_trash:
            raise ValueError("relation pair requires the 2422 fixture")
        migrations += ("database_row_trash", "database_row_page_placement")
    if row_page_placement and not relation_pair:
        raise ValueError("row/page placement requires the 2424 fixture")
    if custom_emoji:
        if not relation_pair:
            raise ValueError("custom emoji requires the 2424 fixture")
        migrations += ("custom_emoji",)
    if item_page_layout:
        if not custom_emoji:
            raise ValueError("item-page layout requires the 2425 fixture")
        migrations += ("database_item_page_layout",)
    if template_default:
        if not item_page_layout:
            raise ValueError("template default requires the 2426 fixture")
        migrations += ("database_template_default",)
    for version, name in enumerate(migrations, start=2406):
        migration = importlib.import_module(f"code_assist_v1.notion_workspace_{name}_migration")
        _apply(migration, catalog, catalog_root, f"before-browser-{version}.db")
        if name == "file_authority":
            CollaborativeFileAuthority.open(catalog, catalog_root, InMemoryIdentityDirectory(["owner"]),
                                            initialize_storage=True)
    store = CollaborativeWorkspaceStore(catalog, user_data_root=catalog_root,
                                        identity_directory=InMemoryIdentityDirectory(["owner", "content-editor", "schema-editor"]))
    created = create_page(store.iam, "owner", workspace_id, title="Collaborative browser lock",
                          parent_id="", base_version=store.get_workspace("owner", workspace_id)["version"],
                          operation_id="browser-native-page-create")
    page_id = created["page"]["id"]
    authority = store.iam
    for subject, role in (("content-editor", "edit_content"), ("schema-editor", "edit")):
        page = authority.read_document("owner", page_id)
        authority.set_page_grant("owner", page_id, "subject", subject, role=role,
                                 inherit_to_descendants=False,
                                 expected_workspace_epoch=page["workspace_epoch"],
                                 expected_document_epoch=page["document_epoch"],
                                 operation_id=f"browser-grant-{subject}")
    if locked:
        update_page_settings(authority, "owner", workspace_id, page_id,
                             **_settings_request(authority, page_id, {"locked": True}, "browser-first-lock"))
    staged_base_capabilities = store.capabilities() if staged_database_sharing else None
    if staged_database_sharing:
        _apply(database_sharing_migration, catalog, catalog_root,
               "before-browser-database-sharing.db")
        _apply(database_upload_scope_migration, catalog, catalog_root,
               "before-browser-database-upload-scope.db")
        if direct_database_favorites:
            _apply(direct_favorite_migration, catalog, catalog_root,
                   "before-browser-direct-database-favorites.db")
        store.identity_directory.replace(["owner", "content-editor", "schema-editor",
                                          "db-viewer"])
        database = database_access.resolve(authority, "owner", workspace_id, "database-e1")
        database_access.change_grant(
            authority, "owner", workspace_id, "database-e1", "subject", "db-viewer",
            role="comment", expected_workspace_epoch=database.workspace_epoch,
            expected_document_epoch=database.document_epoch,
            expected_database_epoch=database.database_epoch,
            operation_id="browser-staged-database-viewer-grant",
        )
    app_root = tmp_path / "app"
    password = "synthetic-browser-password"
    _seed_synthetic_auth_registry(app_root, user_id="owner", password=password)
    registry_path = app_root / ".runtime" / "users.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    subjects = ("content-editor", "schema-editor") + (("db-viewer",) if staged_database_sharing else ())
    for subject in subjects:
        registry["users"][subject] = {**registry["users"]["owner"], "id": subject,
                                      "display_name": subject}
    registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    app_port, gateway_port = _free_port(), _free_port()
    while gateway_port == app_port:
        gateway_port = _free_port()
    fixture = build_fixture(data_root=app_root, repository_root=PROJECT_ROOT, port=app_port)
    _prepare_isolated_runtime_layout(fixture)
    env = isolated_environment(fixture)
    selected_packages = os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES", "")
    if selected_packages:
        package_root = Path(selected_packages).resolve(strict=True)
        if not package_root.is_dir():
            raise RuntimeError("selected gateway package directory is unavailable")
        env["PYTHONPATH"] = os.fspath(package_root) + os.pathsep + env["PYTHONPATH"]
    env.update({
        "CODE_ASSIST_I0_COLLABORATIVE_SHARING": "1",
        "CODE_ASSIST_I0_COLLABORATIVE_SHARING_KILL_SWITCH": "0",
        "CODE_ASSIST_COLLABORATION_CATALOG": os.fspath(catalog),
        "CODE_ASSIST_COLLABORATION_USER_DATA_ROOT": os.fspath(catalog_root),
        "CODE_ASSIST_COLLABORATION_GATEWAY_URL": f"ws://127.0.0.1:{gateway_port}/realtime",
        "CODE_ASSIST_NP2B_NAVIGATION_UI": "1",
        "CODE_ASSIST_SESSION_COOKIE_SECURE": "0",
    })
    if staged_database_sharing:
        env["CODE_ASSIST_TEST_STAGED_BASE_CAPABILITIES"] = json.dumps(
            staged_base_capabilities)
        if direct_database_favorites:
            env["CODE_ASSIST_TEST_STAGED_DIRECT_DATABASE_FAVORITES"] = "1"
    if navigation_ui:
        env.update({
            "CODE_ASSIST_NOTION_CORE_UI_PILOT": "1",
            "CODE_ASSIST_NOTION_CORE_UI_PILOT_SUBJECTS": (
                "owner,db-viewer" if staged_database_sharing else "owner"),
            "CODE_ASSIST_NOTION_CORE_UI_PILOT_WORKSPACES": workspace_id,
        })
    gateway_log, app_log = tmp_path / "gateway.log", tmp_path / "app.log"
    gateway_command = [sys.executable]
    if staged_database_sharing:
        gateway_command += ["-c", (
            "from code_assist_v1.tests.browser.staged_database_runtime "
            "import install_staged_gateway_runtime; "
            "install_staged_gateway_runtime(); "
            "from code_assist_v1.notion_collaboration.gateway_service "
            "import main; raise SystemExit(main())")]
    else:
        gateway_command += ["-m", "code_assist_v1.notion_collaboration.gateway_service"]
    gateway_command += [
        "--serve", "--catalog", os.fspath(catalog), "--user-data-root", os.fspath(catalog_root),
        "--port", str(gateway_port), "--aggregate-enabled", "--realtime-enabled", "--compactor-ready"]
    gateway = _spawn(gateway_command, env, gateway_log) if gateway_enabled else None
    app_script = ""
    if staged_database_sharing:
        app_script = ("from code_assist_v1.tests.browser.staged_database_runtime "
                      "import install_staged_database_runtime; "
                      "install_staged_database_runtime(); ")
    feature_overrides = {}
    if synced_detach:
        feature_overrides['collaborative_synced_detach'] = True
    if legacy_adoption:
        feature_overrides['collaborative_synced_legacy_adoption'] = True
    if page_copy:
        feature_overrides['collaborative_page_copy'] = True
    if owned_database_copy:
        feature_overrides['collaborative_owned_database_copy'] = True
    if owned_database_move:
        feature_overrides['collaborative_owned_database_move'] = True
    if owned_database_trash:
        feature_overrides['collaborative_owned_database_trash'] = True
    if relation_pair:
        feature_overrides['collaborative_database_relation_pair'] = True
    if row_page_placement:
        feature_overrides['collaborative_database_row_to_page_batch'] = True
        feature_overrides['collaborative_page_to_database_row'] = True
    if feature_overrides:
        app_script += ("from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore; "
            "_capabilities = CollaborativeWorkspaceStore.capabilities; "
            "CollaborativeWorkspaceStore.capabilities = lambda self: "
            f"{{**_capabilities(self), **{feature_overrides!r}}}; ")
        if staged_database_sharing:
            # The staged adapter overrides capabilities itself, so explicit
            # fixture flags must also bind that test-only subclass.
            app_script += ("from code_assist_v1.tests.browser.staged_database_runtime import StagedDatabaseWorkspaceStore; "
                "_staged_capabilities = StagedDatabaseWorkspaceStore.capabilities; "
                "StagedDatabaseWorkspaceStore.capabilities = lambda self: "
                f"{{**_staged_capabilities(self), **{feature_overrides!r}}}; ")
    app_script += ("from code_assist_v1.app_notion import create_app; "
        f"create_app().run(host='127.0.0.1', port={app_port}, debug=False, use_reloader=False)")
    app = _spawn([sys.executable, "-c", app_script], env, app_log)
    origin = fixture.origin
    try:
        for _ in range(160):
            if app.poll() is not None or gateway is not None and gateway.poll() is not None:
                raise RuntimeError(f"isolated app/gateway stopped: app={app.poll()} gateway={gateway.poll() if gateway else 'disabled'}\n"
                    f"app log: {app_log.read_text(encoding='utf-8', errors='replace')[-2000:]}\n"
                    f"gateway log: {gateway_log.read_text(encoding='utf-8', errors='replace')[-2000:]}")
            try:
                with urllib.request.urlopen(origin + "/healthz", timeout=1) as response:
                    app_ready = response.status == 200
                if gateway is not None:
                    with urllib.request.urlopen(f"http://127.0.0.1:{gateway_port}/health", timeout=1) as response:
                        gateway_health = json.load(response)
                        gateway_ready = gateway_health.get("ready_for_admission") is True
                else:
                    gateway_ready = True
                if app_ready and gateway_ready:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError(f"isolated collaborative app health timed out: gateway={locals().get('gateway_health')}\n"
                f"app log: {app_log.read_text(encoding='utf-8', errors='replace')[-2000:]}\n"
                f"gateway log: {gateway_log.read_text(encoding='utf-8', errors='replace')[-2000:]}")
        yield origin, password, authority, workspace_id, page_id
    finally:
        _stop(app)
        if gateway is not None:
            _stop(gateway)


@pytest.mark.integration
def test_ui_page_settings_current_tab_unlock_remote_relock_full_browser(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright
    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        try:
            available = metadata.version("uvicorn") == "0.52.4" and metadata.version("websockets") == "17.0.1"
        except metadata.PackageNotFoundError:
            available = False
        if not available:
            pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path) as (origin, password, authority, workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                title = page.locator("#pageTitle")
                expect(title).to_be_visible(timeout=30_000)
                expect(title).to_be_disabled(timeout=30_000)
                page.locator("#pageLockBtn").click()
                unlock = page.locator('[data-page-action="temporary_unlock"]')
                expect(unlock).to_be_visible()
                unlock.click()
                try:
                    expect(title).to_be_enabled(timeout=12_000)
                except AssertionError as exc:
                    state = page.evaluate("""() => ({pageId:State.activePageId,
                      locked:State.pageData?.page?.settings?.locked,
                      generation:State.pageData?.page?.lock_generation,
                      hasGrant:TemporaryUnlockGrants.has(State.activePageId),
                      writeAuthorized:State.liveCollaboration.writeAuthorized,
                      crdtReady:State.liveCollaboration.crdtReady,
                      recoveryOnly:State.liveCollaboration.recoveryOnly,
                      status:document.body.innerText.slice(-300)})""")
                    raise AssertionError(f"current-tab unlock did not activate: {state}; errors={errors}") from exc
                assert page.evaluate("State.liveCollaboration.writeAuthorized") is True
                editor = page.locator(".block-editor[contenteditable='true']").first
                expect(editor).to_be_visible()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    with closing(sqlite3.connect(authority.catalog_path)) as connection:
                        before_updates = connection.execute(
                            "SELECT COUNT(*) FROM workspace_collaboration_crdt_updates WHERE document_id=?",
                            (page_id,),
                        ).fetchone()[0]
                    if before_updates > 0 and page.evaluate("State.liveCollaboration.unsyncedCount") == 0:
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError("the canonical first Yjs state was not synchronized")
                assert collaborative_page_payload(authority, "owner", page_id)["page"]["title"] == "Collaborative browser lock"
                editor.fill("현재 탭에서 저장한 본문")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    with closing(sqlite3.connect(authority.catalog_path)) as connection:
                        update_count = connection.execute(
                            "SELECT COUNT(*) FROM workspace_collaboration_crdt_updates WHERE document_id=?",
                            (page_id,),
                        ).fetchone()[0]
                    if update_count > before_updates and page.evaluate("State.liveCollaboration.unsyncedCount") == 0:
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError("current-tab body edit was not durably synchronized")
                original = page.evaluate("JSON.stringify(State.pageData.blocks)")
                before_title_updates = update_count
                title.fill("현재 탭에서 저장한 제목")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    with closing(sqlite3.connect(authority.catalog_path)) as connection:
                        update_count = connection.execute(
                            "SELECT COUNT(*) FROM workspace_collaboration_crdt_updates WHERE document_id=?",
                            (page_id,),
                        ).fetchone()[0]
                    if update_count > before_title_updates and page.evaluate("State.liveCollaboration.unsyncedCount") == 0:
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError("current-tab title edit was not durably synchronized")
                assert collaborative_page_payload(authority, "owner", page_id)["page"]["title"] == "현재 탭에서 저장한 제목"
                other_tab = context.new_page()
                other_tab.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                other_title = other_tab.locator("#pageTitle")
                expect(other_title).to_be_visible(timeout=30_000)
                expect(other_title).to_be_disabled(timeout=30_000)
                expect(other_title).to_have_value("현재 탭에서 저장한 제목")
                assert other_tab.evaluate("id => TemporaryUnlockGrants.has(id)", page_id) is False
                page.locator("#pageLockBtn").click()
                relock = page.locator('[data-page-action="relock_local"]')
                expect(relock).to_be_visible()
                relock.click()
                expect(title).to_be_disabled(timeout=30_000)
                assert page.evaluate("id => TemporaryUnlockGrants.has(id)", page_id) is False
                page.locator("#pageLockBtn").click()
                expect(unlock).to_be_visible()
                unlock.click()
                expect(title).to_be_enabled(timeout=30_000)

                update_page_settings(authority, "owner", workspace_id, page_id,
                                     **_settings_request(authority, page_id, {"locked": False}, "browser-global-unlock"))
                update_page_settings(authority, "owner", workspace_id, page_id,
                                     **_settings_request(authority, page_id, {"locked": True}, "browser-global-relock"))
                expect(title).to_be_disabled(timeout=30_000)
                assert page.evaluate("State.liveCollaboration.writeAuthorized") is False
                assert page.evaluate("id => TemporaryUnlockGrants.has(id)", page_id) is False
                assert page.evaluate("JSON.stringify(State.pageData.blocks)") == original
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#pageTitle")).to_be_disabled(timeout=30_000)
                expect(page.locator("#pageTitle")).to_have_value("현재 탭에서 저장한 제목")
                expect(page.locator(".block-editor").first).to_contain_text("현재 탭에서 저장한 본문", timeout=30_000)
                assert not errors, errors
                for subject, can_unlock in (("content-editor", False), ("schema-editor", True)):
                    role_context = browser.new_context(locale="ko-KR")
                    try:
                        role_page = role_context.new_page()
                        role_page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                        role_page.locator("#userId").fill(subject)
                        role_page.locator("#password").fill(password)
                        role_page.locator("#submitBtn").click()
                        role_page.wait_for_function(
                            "subject => typeof State !== 'undefined' && State.user?.id === subject && State.navigation.enabled",
                            arg=subject,
                            timeout=30_000,
                        )
                        role_page.evaluate(
                            "id => localStorage.setItem(navigationSelectionKey(), id)",
                            workspace_id,
                        )
                        role_page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                        expect(role_page.locator("#workspaceTitle")).to_have_text("E1 workspace", timeout=30_000)
                        role_title = role_page.locator("#pageTitle")
                        expect(role_title).to_be_visible(timeout=30_000)
                        expect(role_title).to_be_disabled(timeout=30_000)
                        role_lock = role_page.locator("#pageLockBtn")
                        if can_unlock:
                            expect(role_lock).to_be_enabled()
                            role_lock.click()
                            role_page.locator('[data-page-action="temporary_unlock"]').click()
                            expect(role_title).to_be_enabled(timeout=30_000)
                        else:
                            expect(role_lock).to_be_disabled()
                            assert role_page.evaluate("id => TemporaryUnlockGrants.has(id)", page_id) is False
                    finally:
                        role_context.close()
                other_tab.close()
                context.close()
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_comment_composer_tracks_remote_role_change_without_losing_draft(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, authority, workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR")
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("content-editor")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.user?.id === 'content-editor' && State.navigation.enabled",
                    timeout=30_000,
                )
                page.evaluate("id => localStorage.setItem(navigationSelectionKey(), id)", workspace_id)
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                expect(page.locator("#pageTitle")).to_be_visible(timeout=30_000)
                page.wait_for_function("() => State.pageData?.page?.iam_role === 'edit_content'", timeout=30_000)
                page.locator("#addCommentBtn").click()
                expect(page.locator("#pageInlineDiscussion")).to_be_visible()
                comment = page.locator("#commentInput")
                comment.fill("권한 변경 중 보존할 댓글 초안")
                expect(page.locator("#commentSubmitBtn")).to_be_enabled()

                for role, enabled in (("view", False), ("comment", True)):
                    current = authority.read_document("owner", page_id)
                    authority.set_page_grant(
                        "owner", page_id, "subject", "content-editor", role=role,
                        inherit_to_descendants=False,
                        expected_workspace_epoch=current["workspace_epoch"],
                        expected_document_epoch=current["document_epoch"],
                        operation_id=f"browser-comment-role-{role}",
                    )
                    assert collaborative_page_payload(authority, "content-editor", page_id)["page"]["iam_role"] == role
                    page.wait_for_function(
                        "role => State.pageData?.page?.iam_role === role", arg=role, timeout=15_000,
                    )
                    expect(comment).to_have_value("권한 변경 중 보존할 댓글 초안")
                    if enabled:
                        expect(comment).to_be_enabled()
                        expect(page.locator("#commentSubmitBtn")).to_be_enabled()
                    else:
                        expect(comment).to_be_disabled()
                        expect(page.locator("#commentSubmitBtn")).to_be_disabled()
                assert not errors, errors
                context.close()
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_stays_safe_after_remote_access_revoke(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, authority, workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR")
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("content-editor")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.user?.id === 'content-editor' && State.navigation.enabled",
                    timeout=30_000,
                )
                page.evaluate("id => localStorage.setItem(navigationSelectionKey(), id)", workspace_id)
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                editor = page.locator("#blocks .block-editor[contenteditable='true']").first
                expect(editor).to_be_visible(timeout=30_000)
                with closing(sqlite3.connect(authority.catalog_path)) as connection:
                    before_updates = connection.execute(
                        "SELECT COUNT(*) FROM workspace_collaboration_crdt_updates WHERE document_id=?",
                        (page_id,),
                    ).fetchone()[0]
                editor.fill("접근 권한 회수 뒤에도 보존할 본문입니다.")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    with closing(sqlite3.connect(authority.catalog_path)) as connection:
                        update_count = connection.execute(
                            "SELECT COUNT(*) FROM workspace_collaboration_crdt_updates WHERE document_id=?",
                            (page_id,),
                        ).fetchone()[0]
                    if update_count > before_updates and page.evaluate("State.liveCollaboration.unsyncedCount") == 0:
                        break
                    page.wait_for_timeout(100)
                else:
                    raise AssertionError("initial selected-comment paragraph was not synchronized")
                page.evaluate("""() => {
                  const editor = document.querySelector('#blocks .block-editor');
                  const phrase = '보존할 본문';
                  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
                  let text = null, offset = -1;
                  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                    const found = node.nodeValue.indexOf(phrase);
                    if (found >= 0) { text = node; offset = found; break; }
                  }
                  if (!text) throw Error('missing quote');
                  const range = document.createRange();
                  range.setStart(text, offset);
                  range.setEnd(text, offset + phrase.length);
                  editor.focus();
                  const selection = window.getSelection();
                  selection.removeAllRanges(); selection.addRange(range);
                  document.dispatchEvent(new Event('selectionchange'));
                }""")
                comment_action = page.locator('#inlineToolbar [data-inline-format="comment"]')
                expect(comment_action).to_be_visible()
                comment_action.click()
                comment = page.locator("#commentInput")
                comment.fill("회수 뒤에도 남길 선택 댓글 초안")
                expect(page.locator("#commentSubmitBtn")).to_be_enabled()

                current = authority.read_document("owner", page_id)
                authority.revoke_page_grant(
                    "owner", page_id, "subject", "content-editor",
                    expected_workspace_epoch=current["workspace_epoch"],
                    expected_document_epoch=current["document_epoch"],
                    operation_id="browser-comment-access-revoke",
                )
                page.wait_for_function("() => State.liveCollaboration.recoveryOnly", timeout=20_000)
                assert page.evaluate("State.activePageId") == page_id
                expect(page.locator("#collaborationRecoveryBanner")).to_be_visible()
                expect(comment).to_have_value("회수 뒤에도 남길 선택 댓글 초안")
                expect(comment).to_be_disabled()
                expect(page.locator("#commentSubmitBtn")).to_be_disabled()
                marker = page.locator(".page-inline-comment-marker.pending")
                expect(marker).to_be_visible()
                geometry = page.evaluate("""() => ({
                  popupTop: document.querySelector('#inspector').getBoundingClientRect().top,
                  markerTop: document.querySelector('.page-inline-comment-marker.pending').getBoundingClientRect().top,
                  rangeConnected: PendingSelectedCommentRange?.startContainer?.isConnected,
                })""")
                assert geometry["rangeConnected"] is True, geometry
                assert abs(geometry["popupTop"] - geometry["markerTop"]) < 220, geometry

                current = authority.read_document("owner", page_id)
                authority.set_page_grant(
                    "owner", page_id, "subject", "content-editor", role="comment",
                    inherit_to_descendants=False,
                    expected_workspace_epoch=current["workspace_epoch"],
                    expected_document_epoch=current["document_epoch"],
                    operation_id="browser-comment-access-regrant",
                )
                other_tab = context.new_page()
                other_tab.goto("about:blank")
                page.bring_to_front()
                # Headless Chrome does not reliably dispatch a native focus event.
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "() => State.pageData?.page?.iam_role === 'comment' && !State.liveCollaboration.recoveryOnly",
                    timeout=20_000,
                )
                expect(comment).to_have_value("회수 뒤에도 남길 선택 댓글 초안")
                expect(comment).to_be_enabled()
                expect(page.locator("#commentSubmitBtn")).to_be_enabled()
                expect(page.locator(".page-inline-comment-marker.pending")).to_be_visible()
                other_tab.close()
                assert not errors, errors
                context.close()
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_access_regrant_keeps_unsent_body_in_recovery_without_reapplying(tmp_path):
    """A new grant must not silently submit a draft made under an older access epoch."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, authority, workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR")
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("content-editor")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.user?.id === 'content-editor' && State.navigation.enabled",
                    timeout=30_000,
                )
                page.evaluate("id => localStorage.setItem(navigationSelectionKey(), id)", workspace_id)
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                editor = page.locator("#blocks .block-editor[contenteditable='true']").first
                expect(editor).to_be_visible(timeout=30_000)
                page.wait_for_function("() => State.liveCollaboration.connected && State.liveCollaboration.crdtReady", timeout=20_000)
                initial = authority.read_document("owner", page_id)
                page.evaluate("""() => {
                  const send = WebSocket.prototype.send;
                  WebSocket.prototype.send = function(payload) {
                    if (typeof payload === 'string' && JSON.parse(payload).op === 'submit') return;
                    return send.call(this, payload);
                  };
                }""")
                editor.fill("전송되지 않은 본문 초안")
                page.wait_for_function("() => State.liveCollaboration.unsyncedCount > 0", timeout=20_000)
                assert authority.read_document("owner", page_id)["blocks"] == initial["blocks"]

                current = authority.read_document("owner", page_id)
                authority.revoke_page_grant(
                    "owner", page_id, "subject", "content-editor",
                    expected_workspace_epoch=current["workspace_epoch"],
                    expected_document_epoch=current["document_epoch"],
                    operation_id="browser-unsent-access-revoke",
                )
                page.wait_for_function("() => State.liveCollaboration.recoveryOnly", timeout=20_000)
                expect(page.locator("#collaborationRecoveryBanner")).to_be_visible()
                expect(page.locator("#blocks .block-editor").first).to_contain_text("전송되지 않은 본문 초안")
                expect(page.locator("#collaborationRecoveryBanner button")).to_have_text("로컬 편집 내보내기")

                current = authority.read_document("owner", page_id)
                authority.set_page_grant(
                    "owner", page_id, "subject", "content-editor", role="edit_content",
                    inherit_to_descendants=False,
                    expected_workspace_epoch=current["workspace_epoch"],
                    expected_document_epoch=current["document_epoch"],
                    operation_id="browser-unsent-access-regrant",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function("() => State.pageData?.page?.can_edit === true", timeout=20_000)
                recovery = page.evaluate("""() => ({
                  recovery: State.liveCollaboration.recoveryOnly,
                  pending: State.liveCollaboration.unsyncedCount,
                  accessRecoveryPending: State.liveCollaboration.accessRecoveryPending,
                })""")
                assert recovery["recovery"] is True and recovery["pending"] > 0
                assert recovery["accessRecoveryPending"] is True
                expect(page.locator("#blocks .block-editor").first).to_contain_text("전송되지 않은 본문 초안")
                expect(page.locator("#collaborationRecoveryBanner")).to_be_visible()
                expect(page.locator("#collaborationRecoveryBanner")).to_contain_text("미전송 편집은 자동 저장하지 않습니다")
                expect(page.locator("#blocks .block-editor").first).not_to_have_attribute("contenteditable", "true")
                assert authority.read_document("owner", page_id)["blocks"] == initial["blocks"]
                assert not errors, errors
                context.close()
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_stays_on_page_after_remote_quote_removal(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, _authority, workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                def open_page(subject):
                    context = browser.new_context(locale="ko-KR", viewport={"width":1280,"height":800})
                    page = context.new_page()
                    page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                    page.locator("#userId").fill(subject)
                    page.locator("#password").fill(password)
                    page.locator("#submitBtn").click()
                    page.wait_for_function(
                        "subject => typeof State !== 'undefined' && State.user?.id === subject && State.navigation.enabled",
                        arg=subject, timeout=30_000,
                    )
                    page.evaluate("id => localStorage.setItem(navigationSelectionKey(), id)", workspace_id)
                    page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                    expect(page.locator("#pageTitle")).to_be_visible(timeout=30_000)
                    return context, page

                writer_context, writer = open_page("content-editor")
                writer_errors: list[str] = []
                writer.on("pageerror", lambda error: writer_errors.append(str(error)))
                writer_editor = writer.locator("#blocks .block-editor[contenteditable='true']").first
                expect(writer_editor).to_be_visible(timeout=30_000)
                writer_editor.fill("앞부분 보존할 본문 뒤부분")
                writer.wait_for_function("() => State.liveCollaboration.unsyncedCount === 0", timeout=20_000)

                owner_context, owner = open_page("owner")
                owner_errors: list[str] = []
                owner.on("pageerror", lambda error: owner_errors.append(str(error)))
                owner_editor = owner.locator("#blocks .block-editor[contenteditable='true']").first
                expect(owner_editor).to_contain_text("보존할 본문", timeout=30_000)
                writer.evaluate("""() => {
                  const editor = document.querySelector('#blocks .block-editor');
                  const phrase = '보존할 본문';
                  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
                  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                    const offset = node.nodeValue.indexOf(phrase);
                    if (offset < 0) continue;
                    const range = document.createRange();
                    range.setStart(node, offset); range.setEnd(node, offset + phrase.length);
                    editor.focus();
                    const selection = window.getSelection();
                    selection.removeAllRanges(); selection.addRange(range);
                    document.dispatchEvent(new Event('selectionchange'));
                    return;
                  }
                  throw Error('missing quote');
                }""")
                writer.locator('#inlineToolbar [data-inline-format="comment"]').click()
                writer.locator("#commentInput").fill("원문이 바뀌어도 남길 초안")
                expect(writer.locator(".page-inline-comment-marker.pending")).to_be_visible()

                owner_editor.fill("앞부분 다른 문장 뒤부분")
                owner.wait_for_function("() => State.liveCollaboration.unsyncedCount === 0", timeout=20_000)
                expect(writer.locator("#blocks .block-editor").first).to_contain_text("다른 문장", timeout=30_000)
                expect(writer.locator("#commentInput")).to_have_value("원문이 바뀌어도 남길 초안")
                expect(writer.locator(".page-inline-comment-marker.pending")).to_be_visible()
                geometry = writer.evaluate("""() => ({
                  popupTop: document.querySelector('#inspector').getBoundingClientRect().top,
                  markerTop: document.querySelector('.page-inline-comment-marker.pending').getBoundingClientRect().top,
                  highlighted: Boolean(window.CSS?.highlights?.has('pending-selected-comment')),
                })""")
                assert abs(geometry["popupTop"] - geometry["markerTop"]) < 220, geometry
                assert geometry["highlighted"] is False, geometry
                assert not writer_errors and not owner_errors, (writer_errors, owner_errors)
                writer_context.close()
                owner_context.close()
            finally:
                browser.close()
