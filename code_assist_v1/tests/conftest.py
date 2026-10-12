from __future__ import annotations

import gc
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

import pytest


_SAFE_EMPTY_ENV = (
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
)
_SAFE_PATH_ENV = (
    "CODE_ASSIST_TOKEN_FILE",
    "CODE_ASSIST_API_CONFIG",
    "CODE_ASSIST_MS_TOKEN_KEY_FILE",
    "CODE_ASSIST_MS_CLIENT_SECRET_FILE",
    "CODE_ASSIST_CODEX_CWD",
    "CODE_ASSIST_CODEX_BIN",
)
_SAFE_FIXED_ENV = {
    "CODE_ASSIST_TEST_MODE": "1",
    "CODE_ASSIST_STANDALONE": "1",
    "CODE_ASSIST_ENABLE_CODEX_BRIDGE": "0",
    "CODE_ASSIST_AUTOLOAD_GGUF": "0",
    "CODE_ASSIST_ALLOW_SIGNUP": "0",
    "CODE_ASSIST_OFFICE_VISIBLE": "0",
    "CODE_ASSIST_RENDER_PPTX_SLIDES": "0",
    "CODE_ASSIST_RENDER_PDF_PAGES": "off",
    "CODE_ASSIST_JOB_WORKERS": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}
_ENV_OVERRIDES = (
    "CODE_ASSIST_TEST_MODE",
    "CODE_ASSIST_TEST_DATA_ROOT",
    "CODE_ASSIST_DATA_ROOT",
    "CODE_ASSIST_STANDALONE",
    "CODE_ASSIST_ENABLE_CODEX_BRIDGE",
    "CODE_ASSIST_AUTOLOAD_GGUF",
    "CODE_ASSIST_ALLOW_SIGNUP",
    "CODE_ASSIST_OFFICE_VISIBLE",
    "CODE_ASSIST_RENDER_PPTX_SLIDES",
    "CODE_ASSIST_RENDER_PDF_PAGES",
    "CODE_ASSIST_JOB_WORKERS",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "TEMP",
    "TMP",
    "TMPDIR",
    "PYTHONPATH",
) + _SAFE_EMPTY_ENV + _SAFE_PATH_ENV
_CRITICAL_PREIMPORT_MODULES = (
    "code_assist_v1.config",
    "code_assist_v1.auth",
    "code_assist_v1.job_manager",
    "code_assist_v1.knowledge_store",
    "code_assist_v1.second_brain_store",
    "code_assist_v1.notion_store",
    "code_assist_v1.outlook_graph",
    "code_assist_v1.outlook_integration",
    "code_assist_v1.routes_notion",
    "code_assist_v1.routes_sessions",
    "code_assist_v1.routes_workspace",
    "code_assist_v1.skill_filter",
    "code_assist_v1.codex_bridge",
)
_STATE_ATTRIBUTE = "_code_assist_test_isolation"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _restore_environment(previous: dict[str, tuple[bool, str | None]]) -> None:
    for name, (was_present, value) in previous.items():
        if was_present:
            assert value is not None
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


def _cleanup_temporary_directory(temp_dir: tempfile.TemporaryDirectory[str]) -> None:
    """Retry cleanup for short-lived Windows handles before failing the run."""

    for attempt in range(3):
        gc.collect()
        try:
            temp_dir.cleanup()
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))


def _trusted_temp_parent() -> Path:
    """Select an existing OS temp parent without probing it by writing a file."""

    from code_assist_v1.test_safety import assert_isolated_test_path

    configured: list[tuple[str, Path]] = []
    for name in ("TEMP", "TMP", "TMPDIR"):
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise pytest.UsageError(f"inherited {name} must be absolute")
        configured.append(
            (name, assert_isolated_test_path(candidate, test_root=candidate))
        )

    if tempfile.tempdir is not None:
        cached = Path(os.fsdecode(tempfile.tempdir))
        if not cached.is_absolute():
            raise pytest.UsageError("inherited tempfile.tempdir must be absolute")
        assert_isolated_test_path(cached, test_root=cached)

    if configured:
        parent = configured[0][1]
    elif sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise pytest.UsageError(
                "no trusted TEMP/TMP/TMPDIR or LOCALAPPDATA temp parent is available"
            )
        parent = assert_isolated_test_path(
            Path(local_app_data) / "Temp",
            test_root=Path(local_app_data) / "Temp",
        )
    else:
        parent = assert_isolated_test_path(Path("/tmp"), test_root=Path("/tmp"))

    if not parent.is_dir():
        raise pytest.UsageError(f"trusted temp parent does not exist: {parent}")
    return parent


def _assert_effective_isolation(root: Path) -> None:
    """Fail closed if environment, config, or loaded stores escaped the test root."""

    from code_assist_v1.test_safety import assert_isolated_test_path

    root = assert_isolated_test_path(root, test_root=root)
    for name in ("CODE_ASSIST_TEST_DATA_ROOT", "CODE_ASSIST_DATA_ROOT"):
        candidate = assert_isolated_test_path(os.environ.get(name, ""), test_root=root)
        if candidate != root:
            raise pytest.UsageError(f"{name} must equal the pytest isolation root")
    for name in _SAFE_PATH_ENV:
        assert_isolated_test_path(os.environ.get(name, ""), test_root=root)
    for name in _SAFE_EMPTY_ENV:
        if os.environ.get(name) != "":
            raise pytest.UsageError(f"{name} must remain empty during pytest")
    for name, expected in _SAFE_FIXED_ENV.items():
        if os.environ.get(name) != expected:
            raise pytest.UsageError(
                f"{name} must remain {expected!r} during pytest"
            )
    for name in ("TEMP", "TMP", "TMPDIR"):
        if Path(os.environ.get(name, "")).resolve(strict=False) != root:
            raise pytest.UsageError(f"{name} escaped the pytest isolation root")
    if Path(tempfile.gettempdir()).resolve(strict=False) != root:
        raise pytest.UsageError("tempfile.tempdir escaped the pytest isolation root")
    pythonpath_entries = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    if not pythonpath_entries or Path(pythonpath_entries[0]).resolve() != _REPOSITORY_ROOT:
        raise pytest.UsageError("PYTHONPATH must begin with the repository root")

    from code_assist_v1 import config as app_config

    if not app_config.DATA_ROOT_OVERRIDE_ACTIVE:
        raise pytest.UsageError("pytest requires CODE_ASSIST_DATA_ROOT isolation")
    if Path(app_config.DATA_ROOT or "").resolve(strict=False) != root:
        raise pytest.UsageError("effective config DATA_ROOT differs from pytest root")

    config_paths = (
        app_config.RUNTIME_DIR,
        app_config.KNOWLEDGE_DIR,
        app_config.SESSIONS_DIR,
        app_config.WORKSPACE_DIR,
        app_config.USER_DATA_DIR,
        app_config.AUTH_USERS_FILE,
        app_config.AUTH_SECRET_FILE,
        app_config.TOKEN_FILE,
    )
    for candidate in config_paths:
        assert_isolated_test_path(candidate, test_root=root)

    loaded_module_paths = {
        "code_assist_v1.auth": (
            "AUTH_AUDIT_FILE",
            "AUTH_SECRET_FILE",
            "AUTH_USERS_FILE",
            "KNOWLEDGE_DIR",
            "USER_DATA_DIR",
        ),
        "code_assist_v1.job_manager": ("USER_DATA_DIR",),
        "code_assist_v1.knowledge_store": ("KNOWLEDGE_DIR",),
        "code_assist_v1.second_brain_store": ("KNOWLEDGE_DIR",),
        "code_assist_v1.notion_store": ("USER_DATA_DIR",),
        "code_assist_v1.outlook_graph": ("TOKEN_KEY_FILE", "USER_DATA_DIR"),
        "code_assist_v1.outlook_integration": ("USER_DATA_DIR",),
        "code_assist_v1.routes_notion": ("USER_DATA_DIR",),
        "code_assist_v1.routes_sessions": ("SESSIONS_DIR",),
        "code_assist_v1.routes_workspace": ("WORKSPACE_DIR",),
        "code_assist_v1.skill_filter": ("USER_DATA_DIR",),
        "code_assist_v1.codex_bridge": ("WORKSPACE_DIR",),
    }
    for module_name, attributes in loaded_module_paths.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attribute in attributes:
            assert_isolated_test_path(getattr(module, attribute), test_root=root)

    models_module = sys.modules.get("code_assist_v1.models")
    if models_module is not None:
        assert_isolated_test_path(models_module.API_CONFIG_PATH, test_root=root)
    outlook_module = sys.modules.get("code_assist_v1.outlook_graph")
    if outlook_module is not None:
        assert_isolated_test_path(outlook_module._key_file_path(), test_root=root)
    codex_module = sys.modules.get("code_assist_v1.codex_bridge")
    if codex_module is not None:
        assert_isolated_test_path(
            codex_module.codex_model_config()["codex_cwd"],
            test_root=root,
        )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    # Pytest 9.0.3's unittest adapter still reads the debugging plugin's
    # options even when that plugin is intentionally disabled by the validation
    # ladder. Keep the frozen ``-p no:debugging`` command usable without
    # enabling interactive tracing or post-mortem debugging.
    for option_name in ("trace", "usepdb"):
        if not hasattr(config.option, option_name):
            setattr(config.option, option_name, False)

    if hasattr(config, _STATE_ATTRIBUTE):
        return

    preloaded = [name for name in _CRITICAL_PREIMPORT_MODULES if name in sys.modules]
    if preloaded:
        raise pytest.UsageError(
            "mutable-path modules were imported before pytest isolation: "
            + ", ".join(preloaded)
        )

    previous = {
        name: (name in os.environ, os.environ.get(name))
        for name in _ENV_OVERRIDES
    }
    previous_tempfile_tempdir = tempfile.tempdir
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    os.environ["CODE_ASSIST_TEST_MODE"] = "1"
    try:
        temp_parent = _trusted_temp_parent()
        temp_dir = tempfile.TemporaryDirectory(
            prefix="code-assist-pytest-",
            dir=temp_parent,
        )
        root = Path(temp_dir.name).resolve(strict=True)
        token_file = root / ".runtime" / "test-token.txt"
        api_config_file = root / ".runtime" / "api-config.json"
        ms_token_key_file = root / ".runtime" / "ms-token.key"
        ms_secret_file = root / ".runtime" / "ms-client-secret.txt"
        codex_cwd = root / "workspace"
        disabled_codex_bin = root / "bin" / "codex-disabled.exe"
        managed_basetemp = root / "pytest-tmp"
        # Cacheprovider reads this ini value in its own pytest_configure hook.
        config._inicache["cache_dir"] = os.fspath(root / "pytest-cache")
        requested_basetemp = getattr(config.option, "basetemp", None)
        if requested_basetemp is not None:
            resolved_basetemp = Path(requested_basetemp).resolve(strict=False)
            try:
                resolved_basetemp.relative_to(root)
            except ValueError:
                raise pytest.UsageError(
                    "--basetemp must be a strict descendant of the pytest session "
                    "isolation root; omit --basetemp to use the managed safe path"
                ) from None
            if resolved_basetemp == root:
                raise pytest.UsageError(
                    "--basetemp cannot be the pytest session isolation root itself"
                )
            managed_basetemp = resolved_basetemp
        inherited_pythonpath = os.environ.get("PYTHONPATH", "")
        subprocess_pythonpath = os.fspath(_REPOSITORY_ROOT)
        if inherited_pythonpath:
            subprocess_pythonpath = os.pathsep.join(
                (subprocess_pythonpath, inherited_pythonpath)
            )

        token_file.parent.mkdir(parents=True, exist_ok=True)
        codex_cwd.mkdir(parents=True, exist_ok=True)
        api_config_file.write_text("{}\n", encoding="utf-8")
        config.option.basetemp = os.fspath(managed_basetemp)
        safe_environment = {
            "CODE_ASSIST_TEST_DATA_ROOT": os.fspath(root),
            "CODE_ASSIST_DATA_ROOT": os.fspath(root),
            "CODE_ASSIST_TOKEN_FILE": os.fspath(token_file),
            "CODE_ASSIST_API_CONFIG": os.fspath(api_config_file),
            "CODE_ASSIST_MS_TOKEN_KEY_FILE": os.fspath(ms_token_key_file),
            "CODE_ASSIST_MS_CLIENT_SECRET_FILE": os.fspath(ms_secret_file),
            "CODE_ASSIST_CODEX_CWD": os.fspath(codex_cwd),
            "CODE_ASSIST_CODEX_BIN": os.fspath(disabled_codex_bin),
            "TEMP": os.fspath(root),
            "TMP": os.fspath(root),
            "TMPDIR": os.fspath(root),
            "PYTHONPATH": subprocess_pythonpath,
        }
        safe_environment.update(_SAFE_FIXED_ENV)
        safe_environment.update({name: "" for name in _SAFE_EMPTY_ENV})
        os.environ.update(safe_environment)
        tempfile.tempdir = os.fspath(root)
        setattr(
            config,
            _STATE_ATTRIBUTE,
            {
                "previous_environment": previous,
                "root": root,
                "temporary_directory": temp_dir,
                "previous_tempfile_tempdir": previous_tempfile_tempdir,
            },
        )
        _assert_effective_isolation(root)
    except BaseException:
        tempfile.tempdir = previous_tempfile_tempdir
        _restore_environment(previous)
        if temp_dir is not None:
            _cleanup_temporary_directory(temp_dir)
        raise


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    state: dict[str, Any] | None = getattr(config, _STATE_ATTRIBUTE, None)
    if state is None:
        return

    delattr(config, _STATE_ATTRIBUTE)
    tempfile.tempdir = state["previous_tempfile_tempdir"]
    _restore_environment(state["previous_environment"])
    _cleanup_temporary_directory(state["temporary_directory"])


@pytest.fixture(scope="session")
def isolated_data_root(pytestconfig: pytest.Config) -> Path:
    state: dict[str, Any] = getattr(pytestconfig, _STATE_ATTRIBUTE)
    return state["root"]


@pytest.fixture
def isolated_knowledge_layout(isolated_data_root: Path):
    from code_assist_v1.test_safety import create_isolated_test_layout

    case_root = Path(
        tempfile.mkdtemp(prefix="knowledge-layout-", dir=isolated_data_root)
    ).resolve(strict=True)
    return create_isolated_test_layout(case_root, user_id="fixture-user")


@pytest.fixture
def isolated_sqlite_db(isolated_knowledge_layout) -> Path:
    import sqlite3

    database_path = isolated_knowledge_layout.core_db_path
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("VACUUM")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    assert quick_check == ("ok",)
    assert database_path.is_file()
    return database_path


@pytest.fixture
def isolated_blob_dir(isolated_knowledge_layout) -> Path:
    return isolated_knowledge_layout.blobs_dir


@pytest.fixture
def isolated_projection_dir(isolated_knowledge_layout) -> Path:
    return isolated_knowledge_layout.projections_dir


@pytest.fixture
def isolated_notion_dir(isolated_knowledge_layout) -> Path:
    return isolated_knowledge_layout.notion_dir


@pytest.fixture(autouse=True)
def _guard_effective_test_roots(isolated_data_root: Path):
    _assert_effective_isolation(isolated_data_root)
    yield
    _assert_effective_isolation(isolated_data_root)
