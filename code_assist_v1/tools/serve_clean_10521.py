"""Run the isolated 10521 Second Brain web and durable workers.

No migration, account seed or activation is performed at service startup.
This defaults to loopback 10521 and the sibling clean data root. A separately
pinned V71/V72/V73 full-restored validation copy may use loopback 10531, never an
environment-only port override or a historical writer at a new endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from code_assist_v1.document_pdf_geometry import PDF_ENGINE_VERSION
from code_assist_v1.tools.clean_10521_runtime import (
    ACTIVE_REGISTRY_NAME, DATA_ROOT, MODEL_SWITCH_ACTIVATION_NAME,
    catalog_for, selection_for,
)
from code_assist_v1.tools.recovered_preview_binding import load_binding, _CLEAN_RECOVERY_KIND


def _require_pdf_worker_runtime(python_executable: str) -> None:
    """Fail startup before accepting uploads if the pinned PDF worker is unusable."""
    probe = (
        "from importlib.metadata import version; "
        "import pdfplumber, pypdfium2, PIL; "
        "from pdfplumber.utils import extract_text; "
        "print('|'.join(version(name) for name in "
        "('pdfplumber', 'pdfminer.six', 'pypdfium2', 'Pillow')))"
    )
    expected = f"{PDF_ENGINE_VERSION}|20251230|5.13.0|12.3.0"
    environment = os.environ.copy()
    environment.pop("CODE_ASSIST_10521_PDF_OWNER_CREDENTIALS_JSON", None)
    try:
        result = subprocess.run(
            [python_executable, "-I", "-B", "-c", probe],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("The 10521 PDF worker dependency check failed.") from exc
    if result.returncode != 0 or result.stdout.strip() != expected:
        raise RuntimeError("The 10521 PDF worker dependencies are missing or differ from the runtime lock.")


def _require_local_office_validation_runtime() -> None:
    if (os.environ.get("CODE_ASSIST_10521_LOCAL_OFFICE_COM_VALIDATION") != "1"
            or sys.platform != "win32"):
        raise RuntimeError("The v58 local Office COM validation mode is not explicitly enabled.")
    try:
        import pythoncom  # noqa: F401
        import pywintypes  # noqa: F401
        import win32api  # noqa: F401
        import win32con  # noqa: F401
        import win32com.client  # noqa: F401
        import win32job  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("The v58 Office helper dependencies are unavailable.") from exc


def compose(*, answer_request_policy=None, codex_available: bool = False):
    if any(name in sys.modules for name in (
        "code_assist_v1.config", "code_assist_v1.auth", "code_assist_v1.app_code",
    )):
        raise RuntimeError("The 10521 host requires a fresh process before application configuration.")
    root = DATA_ROOT.resolve(strict=True)
    selected = selection_for(root)
    if selected is None:
        raise RuntimeError("The clean 10521 runtime is not activated.")
    binding = load_binding(root)
    port = 10521 if binding is None else binding.port
    if port != 10521 and (port not in (10531, 10541) or binding.kind != _CLEAN_RECOVERY_KIND
            or catalog_for(root).latest_version not in (71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98)):
        raise RuntimeError("Only a separately bound V71/V72/V73 recovery copy may use port 10531.")
    if catalog_for(root).latest_version == 96 and (port != 10541
            or binding.current_marker_sha256 != selected.activation_sha256
            or not selected.forward_partition):
        raise RuntimeError("V96 requires its independently bound A10541 forward selection.")
    if catalog_for(root).latest_version == 97 and (port != 10541
            or binding.current_marker_sha256 != selected.activation_sha256
            or not selected.forward_partition or not selected.forward_continuation):
        raise RuntimeError("V97 requires its independently bound A10541 continuation selection.")
    if catalog_for(root).latest_version == 98:
        if (binding is None or port != 10541
                or binding.current_marker_sha256 != selected.activation_sha256
                or selected.forward_partition is not True
                or selected.forward_continuation is not True):
            raise RuntimeError("V98 requires its independently bound A10541 retained-result selection.")
        from code_assist_v1.tools.pdf_terminal_result_reuse_activation import (
            require_terminal_result_reuse_binding,
        )
        require_terminal_result_reuse_binding(root, binding=binding)
    _require_pdf_worker_runtime(selected.python_executable)
    if catalog_for(root).latest_version in (58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69) or port == 10541:
        _require_local_office_validation_runtime()
    from code_assist_v1.tools.full_second_brain_preview import (
        _canonical_factory, _configure_environment, build_preview_app,
    )

    active_registry = (root / ".runtime" / ACTIVE_REGISTRY_NAME
        if (root / ".runtime" / MODEL_SWITCH_ACTIVATION_NAME).is_file() else None)
    _configure_environment(root, port=port, codex=selected.provider == "codex",
        api_config_path=active_registry)
    if codex_available:
        # Registry visibility is distinct from the immutable Qwen worker lane.
        # The canonical Wiki turn remains on the selected runtime provider;
        # Codex is available to the ordinary model-selectable chat route.
        os.environ["CODE_ASSIST_ENABLE_CODEX_BRIDGE"] = "1"
        if "--codex" not in sys.argv:
            sys.argv.append("--codex")
    factory = _canonical_factory(root)
    marker = {
        "data_root": str(root),
        "marker_sha256": selected.activation_sha256,
        "clone_port": port,
        "clean_schema_version": factory.migration_catalog.latest_version,
    }
    from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V34, VERSION_V35, VERSION_V36
    if getattr(selected, 'policy_version', None) in (VERSION_V34, VERSION_V35, VERSION_V36):
        marker['chart_preparation'] = json.loads(selected.upload_policy().encoded())['configuration']['chart_preparation']
    app = build_preview_app(
        factory, marker, answer_request_policy=answer_request_policy,
    )
    app.config["CLEAN_10521_SCHEMA_VERSION"] = factory.migration_catalog.latest_version
    app.config["CLEAN_10521_BOUND_PORT"] = port
    if getattr(selected, 'policy_version', None) in (VERSION_V34, VERSION_V35, VERSION_V36):
        app.config['PDF_CHART_PREPARATION'] = marker['chart_preparation']
    return app, marker, selected


def _smoke(app, password: str) -> dict[str, object]:
    if not password:
        raise ValueError("CODE_ASSIST_10521_SMOKE_PASSWORD is required for login smoke.")
    client = app.test_client()
    origin = "http://127.0.0.1:" + str(app.config.get("CLEAN_10521_BOUND_PORT", 10521))
    login = client.post(
        "/api/code/auth/login",
        json={"user_id": "admin", "password": password},
        headers={"Origin": origin},
        base_url=origin,
    )
    if login.status_code != 200:
        raise RuntimeError(f"10521 login smoke failed with HTTP {login.status_code}.")
    endpoints = {
        "models": "/api/code/models",
        "knowledge": "/api/code/knowledge/list",
        "organization": "/api/admin/v1/org/tree",
    }
    observed = {
        name: client.get(path, base_url=origin).status_code
        for name, path in endpoints.items()
    }
    if any(status != 200 for status in observed.values()):
        raise RuntimeError("10521 authenticated route smoke failed: " + repr(observed))
    return {"status": "pass", "http_status": observed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--codex", action="store_true",
        help="Also expose Codex in the selectable chat model list.")
    args = parser.parse_args(argv)
    app, marker, selected = compose(codex_available=args.codex)
    identity = {
        "status": "composition_ready",
        "url": "http://127.0.0.1:" + str(marker["clone_port"]),
        "data_root": marker["data_root"],
        "schema_version": app.config["CLEAN_10521_SCHEMA_VERSION"],
        "activation_sha256": marker["marker_sha256"],
        "provider": selected.provider,
        "model_id": selected.model_id,
    }
    if args.check:
        print(json.dumps(identity, sort_keys=True), flush=True)
        return 0
    if args.smoke:
        print(json.dumps({**identity, "smoke": _smoke(
            app, os.environ.get("CODE_ASSIST_10521_SMOKE_PASSWORD", ""),
        )}, sort_keys=True), flush=True)
        return 0
    from code_assist_v1.tools.full_second_brain_preview_workers import PreviewWorkerSupervisor
    from waitress import serve

    supervisor = PreviewWorkerSupervisor(
        DATA_ROOT.resolve(strict=True),
        marker_sha256=marker["marker_sha256"],
        codex_validation=selected.provider == "codex",
    )
    supervisor.start()
    app.extensions["full_second_brain_preview_worker_supervisors"].append(supervisor)
    print(json.dumps({**identity, "status": "ready", "workers": supervisor.status()}, sort_keys=True), flush=True)
    try:
        serve(app, host="127.0.0.1", port=marker["clone_port"], threads=8)
    finally:
        supervisor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
