"""Fail-closed launcher for the isolated Core Free UI browser fixture.

Importing this module is side-effect free.  Filesystem creation and application
imports happen only after an explicit CLI command has validated a trusted test
root outside the repository and enabled the existing test-mode guard.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence

from code_assist_v1.test_safety import (
    UnsafeTestDataPathError,
    assert_isolated_test_path,
    create_isolated_test_layout,
)
from code_assist_v1.notion_core_ui_pilot import (
    PILOT_ENABLE_ENV,
    PILOT_KILL_SWITCH_ENV,
    PILOT_SUBJECTS_ENV,
    PILOT_WORKSPACES_ENV,
)


CONTRACT = "notion_core_free_ui_local_fixture_v1"
PROFILE_ID = "core_free_owner_chrome_desktop_ko_kr"
LOCAL_ORIGIN_ALIAS = "LOCAL-UI1"
_SURFACES = frozenset({"notion", "second_brain"})

_SENSITIVE_ENV = frozenset(
    {
        "CODE_ASSIST_API_KEY",
        "HYNIX_API_KEY",
        "HCP_API_KEY",
        "CODE_ASSIST_CODEX_TOKEN",
        "CODEX_API_KEY",
        "CODEX_TOKEN",
        "CODEX_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODE_ASSIST_MS_TENANT_ID",
        "CODE_ASSIST_MS_CLIENT_ID",
        "CODE_ASSIST_MS_CLIENT_SECRET",
        "CODE_ASSIST_MS_REDIRECT_URI",
        "CODE_ASSIST_MS_TOKEN_KEY",
        "CODE_ASSIST_DOCUMENT_CONVERTERS",
        "CODE_ASSIST_DOCUMENT_EXTRACTOR_CMD",
        "CODE_ASSIST_DEFAULT_MODEL",
    }
)

_RUNTIME_PATH_KEYS = (
    "AUTH_SECRET_FILE",
    "AUTH_USERS_FILE",
    "DATA_ROOT",
    "KNOWLEDGE_DIR",
    "RUNTIME_DIR",
    "SESSIONS_DIR",
    "TOKEN_FILE",
    "USER_DATA_DIR",
    "WORKSPACE_DIR",
)

_UI_GATE_ENV_KEYS = (
    "CODE_ASSIST_NP2B_NAVIGATION_UI",
    "CODE_ASSIST_NP2B_NAVIGATION_UI_KILL_SWITCH",
    PILOT_ENABLE_ENV,
    PILOT_KILL_SWITCH_ENV,
    PILOT_SUBJECTS_ENV,
    PILOT_WORKSPACES_ENV,
)


@dataclass(frozen=True, slots=True)
class LocalUiFixture:
    root: Path
    repository_root: Path
    port: int
    surface: str
    seed_navigation_workspaces: bool = False
    seed_navigation_tree: bool = False

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def python_executable(self) -> Path:
        return self.repository_root / "venv" / "Scripts" / "python.exe"

    def descriptor(self) -> dict[str, object]:
        """Return a secret- and path-free description suitable for evidence."""

        return {
            "contract": CONTRACT,
            "profile_id": PROFILE_ID,
            "origin_alias": LOCAL_ORIGIN_ALIAS,
            "surface": self.surface,
            "isolated_data_root": True,
            "synthetic_only": True,
            "test_mode": True,
            "live_local_data_accessed": False,
            "token_available": False,
        }


def _normalize_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("port must be an integer")
    if value < 1024 or value > 65535:
        raise ValueError("port must be between 1024 and 65535")
    return value


def build_fixture(
    *,
    data_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    port: int,
    surface: str = "notion",
    seed_navigation_workspaces: bool = False,
    seed_navigation_tree: bool = False,
) -> LocalUiFixture:
    """Validate exact roots without creating files or importing the app."""

    normalized_port = _normalize_port(port)
    if surface not in _SURFACES:
        raise ValueError("surface must be notion or second_brain")
    if not isinstance(seed_navigation_workspaces, bool):
        raise ValueError("seed_navigation_workspaces must be a boolean")
    if not isinstance(seed_navigation_tree, bool):
        raise ValueError("seed_navigation_tree must be a boolean")
    if seed_navigation_workspaces and seed_navigation_tree:
        raise ValueError("navigation workspace and tree seeds are mutually exclusive")
    root = assert_isolated_test_path(data_root, test_root=data_root)
    repository = Path(repository_root).resolve(strict=True)
    if not repository.is_dir():
        raise ValueError("repository_root must be a directory")
    if not (repository / "code_assist_v1" / "app_code.py").is_file():
        raise ValueError("repository_root does not contain code_assist_v1/app_code.py")
    python_executable = repository / "venv" / "Scripts" / "python.exe"
    if not python_executable.is_file():
        raise ValueError("repository venv Python is unavailable")
    return LocalUiFixture(
        root=root,
        repository_root=repository,
        port=normalized_port,
        surface=surface,
        seed_navigation_workspaces=seed_navigation_workspaces,
        seed_navigation_tree=seed_navigation_tree,
    )


def isolated_environment(
    fixture: LocalUiFixture,
    *,
    base_environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the complete child environment without mutating ``os.environ``."""

    root = assert_isolated_test_path(fixture.root, test_root=fixture.root)
    environment = dict(os.environ if base_environ is None else base_environ)
    for key in _SENSITIVE_ENV:
        environment.pop(key, None)
    for key in _UI_GATE_ENV_KEYS:
        environment.pop(key, None)

    runtime = root / ".runtime"
    environment.update(
        {
            "CODE_ASSIST_TEST_MODE": "1",
            "CODE_ASSIST_TEST_DATA_ROOT": os.fspath(root),
            "CODE_ASSIST_DATA_ROOT": os.fspath(root),
            "CODE_ASSIST_STANDALONE": "1",
            "CODE_ASSIST_ENABLE_CODEX_BRIDGE": "0",
            "CODE_ASSIST_AUTOLOAD_GGUF": "0",
            "CODE_ASSIST_ALLOW_SIGNUP": "0",
            "CODE_ASSIST_OFFICE_VISIBLE": "0",
            "CODE_ASSIST_RENDER_PPTX_SLIDES": "0",
            "CODE_ASSIST_RENDER_PDF_PAGES": "off",
            "CODE_ASSIST_JOB_WORKERS": "1",
            "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION": "0",
            "CODE_ASSIST_ALLOWED_ORIGINS": ",".join(
                (fixture.origin, f"http://localhost:{fixture.port}")
            ),
            "CODE_ASSIST_PORT": str(fixture.port),
            "CODE_ASSIST_NOTION_PORT": str(fixture.port),
            "CODE_ASSIST_TOKEN_FILE": os.fspath(runtime / "test-token.txt"),
            "CODE_ASSIST_API_CONFIG": os.fspath(runtime / "api-config.json"),
            "CODE_ASSIST_MS_TOKEN_KEY_FILE": os.fspath(runtime / "ms-token.key"),
            "CODE_ASSIST_MS_CLIENT_SECRET_FILE": os.fspath(
                runtime / "ms-client-secret.txt"
            ),
            "CODE_ASSIST_CODEX_CWD": os.fspath(root / "workspace"),
            "CODE_ASSIST_CODEX_BIN": os.fspath(
                root / "bin" / "codex-disabled.exe"
            ),
            "TEMP": os.fspath(root),
            "TMP": os.fspath(root),
            "TMPDIR": os.fspath(root),
            "PYTHONPATH": os.fspath(fixture.repository_root),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    if fixture.seed_navigation_workspaces or fixture.seed_navigation_tree:
        # This is a test-fixture-only enablement.  Production remains
        # default-off and no live catalog is migrated or opened.
        environment["CODE_ASSIST_NP2B_NAVIGATION_UI"] = "1"
        environment.pop("CODE_ASSIST_NP2B_NAVIGATION_UI_KILL_SWITCH", None)
        environment[PILOT_ENABLE_ENV] = "1"
        environment[PILOT_SUBJECTS_ENV] = "ui1-owner"
    return environment


def _prepare_isolated_runtime_layout(fixture: LocalUiFixture):
    """Create the guarded fixture layout and its explicit empty model registry."""

    layout = create_isolated_test_layout(fixture.root, user_id="ui1-owner")
    api_config_path = assert_isolated_test_path(
        layout.runtime_dir / "api-config.json",
        test_root=fixture.root,
    )
    expected = "{}\n"
    if api_config_path.exists():
        if not api_config_path.is_file() or api_config_path.read_text(encoding="utf-8") != expected:
            raise UnsafeTestDataPathError(
                "isolated API model registry must be the exact empty fixture"
            )
    else:
        api_config_path.write_text(expected, encoding="utf-8")
    return layout


def inspect_runtime_paths(fixture: LocalUiFixture) -> dict[str, object]:
    """Import configuration in a child and prove every mutable path is isolated."""

    _prepare_isolated_runtime_layout(fixture)

    script = r'''
import json
import os
from pathlib import Path
from code_assist_v1 import config

root = Path(os.environ["CODE_ASSIST_DATA_ROOT"]).resolve(strict=True)
path_keys = json.loads(os.environ["CODE_ASSIST_UI_PATH_KEYS"])
for key in path_keys:
    candidate = Path(getattr(config, key)).resolve(strict=False)
    candidate.relative_to(root)
print(json.dumps({
    "contract": "notion_core_free_ui_local_path_proof_v1",
    "isolated": True,
    "path_keys": sorted(path_keys),
    "token_available": bool(config.API_TOKEN),
}, sort_keys=True))
'''
    environment = isolated_environment(fixture)
    environment["CODE_ASSIST_UI_PATH_KEYS"] = json.dumps(_RUNTIME_PATH_KEYS)
    completed = subprocess.run(
        [os.fspath(fixture.python_executable), "-c", script],
        cwd=fixture.repository_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("isolated application path proof failed")
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise RuntimeError("isolated application path proof was not canonical")
    report = json.loads(lines[0])
    expected = {
        "contract": "notion_core_free_ui_local_path_proof_v1",
        "isolated": True,
        "path_keys": sorted(_RUNTIME_PATH_KEYS),
        "token_available": False,
    }
    if report != expected:
        raise RuntimeError("isolated application path proof did not match")
    return report


def _canonical_publication_dependencies(layout):
    """Build the isolated H1 canonical Notion-publication surface.

    This is deliberately test-fixture composition, not a production rollout.
    Every durable target is below the already validated isolated data root and
    the normal standalone application remains default-off when it is launched
    without this fixture.
    """

    from code_assist_v1.auth import current_user_id
    from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
    from code_assist_v1.knowledge_core.domain import FeatureFlags
    from code_assist_v1.knowledge_core.legacy.blob_stager import (
        FilesystemLegacyBlobStager,
    )
    from code_assist_v1.knowledge_core.migrations import (
        CANONICAL_MIGRATION_CATALOG,
        MigrationStorageLayout,
        SingleMigrator,
    )
    from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
    from code_assist_v1.knowledge_core.repositories.users import (
        LegacyUserRecord,
        UserRepository,
    )
    from code_assist_v1.knowledge_core.scope import ScopeResolver
    from code_assist_v1.knowledge_core.services.publication import (
        AuthorizedNotionPageSnapshot,
        PublicationApiService,
        PublicationPreviewService,
        PublicationService,
    )
    from code_assist_v1.knowledge_core.services.scope_selection import (
        ScopeSelectionService,
    )
    from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner
    from code_assist_v1 import notion_publication, notion_store

    actor = "ui1-owner"
    migration_layout = MigrationStorageLayout(
        data_root=layout.root,
        database=layout.core_db_path,
        blob_root=layout.blobs_dir,
        projection_root=layout.projections_dir,
    )
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="ui1-free-desktop-golden-journey",
        storage_layout=migration_layout,
    ).run()

    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id=actor,
                    display_name="UI1 Owner",
                    password_hash="scrypt:ui1:synthetic",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            spaces = SpaceRepository()
            personal = spaces.ensure_personal(
                connection,
                space_id=space_id_for_owner("personal", actor),
                user_id=actor,
                created_at_ms=100,
            )
            spaces.update_state(
                connection,
                space_id=personal.space.id,
                expected_row_version=personal.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=101,
            )
    finally:
        connection.close()

    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
    )
    scope = ScopeResolver(flags)

    class _NotionSnapshots:
        def capture(self, *, actor_user_id: str, page_id: str):
            snapshot = notion_publication.capture_page_source_snapshot(
                actor_user_id,
                page_id,
                source_store=notion_store,
            )
            if not snapshot.can_edit:
                raise PermissionError("page edit permission required")
            return AuthorizedNotionPageSnapshot(
                actor_user_id=snapshot.actor_user_id,
                page_id=snapshot.page_id,
                owner_user_id=snapshot.owner_user_id,
                title=snapshot.title,
                revision=snapshot.revision,
                updated_at=snapshot.updated_at,
                markdown=snapshot.markdown,
                content_sha256=snapshot.content_sha256,
                asset_manifest_sha256=snapshot.asset_manifest_sha256,
                source_snapshot_sha256=snapshot.source_snapshot_sha256,
            )

    preview_service = PublicationPreviewService(
        factory,
        scope,
        FilesystemLegacyBlobStager(migration_layout),
        notion_snapshots=_NotionSnapshots(),
    )
    publication_service = PublicationApiService(
        preview_service,
        PublicationService(factory, scope),
    )

    def principal_resolver() -> object:
        user_id = current_user_id()
        if user_id != actor:
            return {}
        principal_connection = factory.open(read_only=True)
        try:
            user = UserRepository().require(principal_connection, actor)
            return {"id": user.id, "scope_epoch": user.scope_epoch}
        finally:
            principal_connection.close()

    return (
        ScopeSelectionService(factory, scope_resolver=scope),
        publication_service,
        principal_resolver,
    )


def _seed_synthetic_navigation_workspaces(layout) -> tuple[str, ...]:
    """Create two isolated canonical workspaces with one bound page each."""

    from code_assist_v1 import config, notion_store
    from code_assist_v1.notion_workspace_projection import project_exact_owner_page
    from code_assist_v1.notion_workspace_store import (
        WorkspaceStore,
        catalog_path_for_user_data_root,
    )

    actor = "ui1-owner"
    catalog = catalog_path_for_user_data_root(config.USER_DATA_DIR)
    store = WorkspaceStore(catalog)
    workspaces = []
    for suffix in ("A", "B"):
        workspace = store.create_workspace(
            actor,
            name=f"Synthetic Workspace {suffix}",
            operation_id=f"ui-shell-001-create-workspace-{suffix.lower()}",
        )["workspace"]
        page = notion_store.create_page(
            actor,
            title=f"Synthetic Page {suffix}",
            markdown=f"Synthetic workspace {suffix} content",
        )["page"]
        proof = notion_store.capture_legacy_page_source_proof(
            actor,
            actor,
            page["id"],
        )
        bound = store.bind_page_source(
            actor,
            workspace["id"],
            proof,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            base_version=workspace["version"],
            operation_id=f"ui-shell-001-bind-page-{suffix.lower()}",
        )
        workspaces.append((suffix, workspace, page, bound))

    source = Path(config.USER_DATA_DIR) / actor / "notion" / "notion.db"
    for suffix, workspace, page, bound in workspaces:
        project_exact_owner_page(
            user_data_root=config.USER_DATA_DIR,
            source_owner_id=actor,
            actor_subject_id=actor,
            workspace_id=workspace["id"],
            source_page_id=page["id"],
            expected_source_db_path=source,
            expected_catalog_db_path=catalog,
            expected_binding_version=int(bound["binding"]["version"]),
            operation_id=f"ui-shell-001-project-page-{suffix.lower()}",
            source_quiesced_confirmed=True,
        )
    return tuple(str(workspace["id"]) for _suffix, workspace, _page, _bound in workspaces)


def _seed_synthetic_navigation_tree(layout) -> tuple[str, ...]:
    """Extend the isolated A/B workspace fixture with one nested A child."""

    from code_assist_v1 import config, notion_store
    from code_assist_v1.notion_workspace_projection import project_exact_owner_page
    from code_assist_v1.notion_workspace_store import (
        WorkspaceStore,
        catalog_path_for_user_data_root,
    )

    workspace_ids = _seed_synthetic_navigation_workspaces(layout)
    actor = "ui1-owner"
    catalog = catalog_path_for_user_data_root(config.USER_DATA_DIR)
    store = WorkspaceStore(catalog)
    workspaces = store.list_workspaces(actor)
    workspace = next(
        item for item in workspaces if item["name"] == "Synthetic Workspace A"
    )
    source = Path(config.USER_DATA_DIR) / actor / "notion" / "notion.db"
    roots = notion_store.list_pages_from_owner_by_ids(
        actor,
        actor,
        {
            binding["source_page_id"]
            for binding in store.list_page_source_bindings(actor, workspace["id"])
        },
    )
    root_page = next(item for item in roots if item["title"] == "Synthetic Page A")
    child = notion_store.create_page(
        actor,
        title="Synthetic Child A",
        parent_id=root_page["id"],
        markdown="Synthetic child content",
    )["page"]
    proof = notion_store.capture_legacy_page_source_proof(
        actor,
        actor,
        child["id"],
    )
    current = store.get_workspace(actor, workspace["id"])
    bound = store.bind_page_source(
        actor,
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=current["version"],
        operation_id="ui-tree-001-bind-child-a",
    )
    project_exact_owner_page(
        user_data_root=config.USER_DATA_DIR,
        source_owner_id=actor,
        actor_subject_id=actor,
        workspace_id=workspace["id"],
        source_page_id=child["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=int(bound["binding"]["version"]),
        operation_id="ui-tree-001-project-child-a",
        source_quiesced_confirmed=True,
    )
    return workspace_ids


def _serve(fixture: LocalUiFixture) -> int:
    environment = isolated_environment(fixture)
    layout = _prepare_isolated_runtime_layout(fixture)
    os.environ.clear()
    os.environ.update(environment)

    # Import only after the complete isolated environment is active.
    if fixture.surface == "notion":
        from code_assist_v1.app_notion import create_app
        from code_assist_v1.knowledge_core.canonical_routes import (
            register_publication_routes,
        )

        scope_selection, publication_service, principal_resolver = (
            _canonical_publication_dependencies(layout)
        )
        workspace_ids: tuple[str, ...] = ()
        if fixture.seed_navigation_tree:
            workspace_ids = _seed_synthetic_navigation_tree(layout)
        elif fixture.seed_navigation_workspaces:
            workspace_ids = _seed_synthetic_navigation_workspaces(layout)
        if workspace_ids:
            os.environ[PILOT_WORKSPACES_ENV] = ",".join(workspace_ids)
        app = create_app()
        register_publication_routes(
            app,
            scope_selection_service=scope_selection,
            publication_service=publication_service,
            principal_resolver=principal_resolver,
        )
    else:
        from code_assist_v1.app_code import create_app
        app = create_app()
    app.run(
        host="127.0.0.1",
        port=fixture.port,
        debug=False,
        use_reloader=False,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "serve"))
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--surface", choices=sorted(_SURFACES), default="notion")
    parser.add_argument(
        "--repository-root",
        default=os.fspath(Path(__file__).resolve().parents[3]),
    )
    parser.add_argument("--test-mode-confirmed", action="store_true")
    parser.add_argument("--seed-navigation-workspaces", action="store_true")
    parser.add_argument("--seed-navigation-tree", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.test_mode_confirmed:
        raise UnsafeTestDataPathError("--test-mode-confirmed is required")
    os.environ["CODE_ASSIST_TEST_MODE"] = "1"
    os.environ["CODE_ASSIST_TEST_DATA_ROOT"] = args.data_root
    fixture = build_fixture(
        data_root=args.data_root,
        repository_root=args.repository_root,
        port=args.port,
        surface=args.surface,
        seed_navigation_workspaces=args.seed_navigation_workspaces,
        seed_navigation_tree=args.seed_navigation_tree,
    )
    if args.command == "check":
        report = {
            "status": "pass",
            "fixture": fixture.descriptor(),
            "runtime": inspect_runtime_paths(fixture),
        }
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    return _serve(fixture)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONTRACT",
    "LOCAL_ORIGIN_ALIAS",
    "LocalUiFixture",
    "PROFILE_ID",
    "build_fixture",
    "inspect_runtime_paths",
    "isolated_environment",
    "main",
]
