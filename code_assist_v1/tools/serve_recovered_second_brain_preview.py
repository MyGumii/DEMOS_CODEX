"""Start an explicitly bound recovered preview; never seed, migrate, or reset users.

This loopback recovery entry point cannot occupy retained service ports. It is
not the production WinSW/TLS rollout entry point. Supply the operator binding
and its externally retained SHA256 before importing application configuration.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

from code_assist_v1.tools.recovered_preview_binding import (
    PATH_ENV, SHA_ENV, _CLEAN_RECOVERY_KIND, RecoveryBindingError, load_binding,
)


def _bind_environment(values):
    for name, value in values.items():
        if os.environ.get(name) not in (None, value):
            raise RecoveryBindingError("An incompatible process environment is already bound: " + name)
    os.environ.update(values)


def compose(binding_path, binding_sha256, root, *, answer_request_policy=None,
            codex_available=False):
    """Validate all immutable startup inputs before opening any mutable store."""
    from code_assist_v1.knowledge_core.answer_request import AnswerRequestPolicy
    if answer_request_policy is not None and type(answer_request_policy) is not AnswerRequestPolicy:
        raise TypeError("answer_request_policy must be an AnswerRequestPolicy or None.")
    if any(name in sys.modules for name in ("code_assist_v1.config", "code_assist_v1.auth", "code_assist_v1.app_code")):
        raise RecoveryBindingError("Recovery startup requires a fresh application process.")
    _bind_environment({PATH_ENV: str(binding_path), SHA_ENV: binding_sha256})
    binding = load_binding(root)
    if binding is None:
        raise RecoveryBindingError("Recovery startup requires an explicit binding.")
    if binding.kind == _CLEAN_RECOVERY_KIND:
        from code_assist_v1.tools.clean_10521_runtime import DATA_ROOT, catalog_for
        if (binding.data_root != DATA_ROOT.resolve(strict=True)
                or binding.port not in (10521, 10531, 10541)
                or (binding.port in (10531, 10541) and catalog_for(binding.data_root).latest_version not in (71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98))
                or (catalog_for(binding.data_root).latest_version == 97 and binding.port != 10541)):
            raise RecoveryBindingError("The clean recovery target is not the isolated 10521 root.")
        if catalog_for(binding.data_root).latest_version == 98:
            from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import (
                PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG,
            )
            from code_assist_v1.tools.pdf_terminal_result_reuse_activation import (
                require_terminal_result_reuse_binding,
            )
            if catalog_for(binding.data_root) is not PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG:
                raise RecoveryBindingError("V98 recovery requires its exact compiled catalog.")
            # Reject other ports/providers/origins before application composition.
            # The normal schema4 pin also validates its unchanged V97 predecessor.
            require_terminal_result_reuse_binding(binding.data_root, binding=binding)
        _bind_environment({
            "CODE_ASSIST_AUTH_USERS_FILE": str(binding.data_root / ".runtime/users.json"),
            "CODE_ASSIST_AUTH_SECRET_FILE": str(binding.data_root / ".runtime/secret.key"),
            "CODE_ASSIST_AGENT_WORKER": "0", "CODE_ASSIST_AGENT_OUTLOOK": "0",
            "CODE_ASSIST_AUTOLOAD_GGUF": "0",
        })
        if any(os.environ.get(name) for name in (
                "CODE_ASSIST_AGENT_NOTION_MODE",
                "CODE_ASSIST_AGENT_NOTION_USER_DATA_ROOT",
                "CODE_ASSIST_AGENT_NOTION_URL")):
            raise RecoveryBindingError(
                "External integration bindings require a separate recovery decision."
            )
        from code_assist_v1.tools.serve_clean_10521 import compose as compose_clean
        app, marker, selected = compose_clean(
            answer_request_policy=answer_request_policy,
            codex_available=codex_available,
        )
        if binding.provider_mode != (
                "codex-validation" if selected.provider == "codex"
                else "openai-compatible"):
            raise RecoveryBindingError("The clean recovery provider differs from its binding.")
        app.config["SESSION_COOKIE_NAME"] = "sb_recovery_" + binding.document_sha256[:16]
        app.config["RECOVERED_SCHEMA_VERSION"] = catalog_for(binding.data_root).latest_version
        return app, marker, binding
    from code_assist_v1.tools.full_second_brain_preview import (
        _configure_environment, _load_marker, build_preview_app,
    )
    from code_assist_v1.tools.native_publication_preview_runtime import native_publication_catalog_for
    from code_assist_v1.tools.preservation_preview_runtime import factory_for
    from code_assist_v1.knowledge_core.migrations.legacy_authoring_retention_catalog import (
        LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG,
    )
    from code_assist_v1.knowledge_core.migrations.pdf_publication_catalog import PDF_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_visual_publication_catalog import PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_visual_writing_policy_catalog import PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_evidence_units_catalog import PDF_EVIDENCE_UNITS_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.native_semantic_targets_catalog import NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
    marker = _load_marker(binding.data_root)
    catalog = native_publication_catalog_for(binding.data_root)
    if catalog not in (LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG, PDF_PUBLICATION_MIGRATION_CATALOG,
            PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG, PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG,
            PDF_EVIDENCE_UNITS_MIGRATION_CATALOG, NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG,
            SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG):
        raise RecoveryBindingError("Recovery startup requires a complete activated v38, v41, v45, v46, v47, v48 or v49 catalog.")
    from code_assist_v1.tools.windows_volume import probe_path
    for relative in (".", "core", "core/knowledge_core.db", "blobs", "projections", ".runtime"):
        path = binding.data_root / relative
        facts = probe_path(path)
        if (facts.get("eligible") is not True or facts.get("exists") is not True
                or (facts.get("reparse") or {}).get("present") is not False
                or path.resolve(strict=True) != path.absolute()):
            raise RecoveryBindingError("Recovery storage must remain on ordinary local NTFS paths.")
    codex = binding.provider_mode == "codex-validation"
    _configure_environment(binding.data_root, port=binding.port, codex=codex)
    _bind_environment({
        "CODE_ASSIST_AUTH_USERS_FILE": str(binding.data_root / ".runtime/users.json"),
        "CODE_ASSIST_AUTH_SECRET_FILE": str(binding.data_root / ".runtime/secret.key"),
        "CODE_ASSIST_AGENT_WORKER": "0", "CODE_ASSIST_AGENT_OUTLOOK": "0",
        "CODE_ASSIST_AUTOLOAD_GGUF": "0",
    })
    if any(os.environ.get(name) for name in ("CODE_ASSIST_AGENT_NOTION_MODE",
            "CODE_ASSIST_AGENT_NOTION_USER_DATA_ROOT", "CODE_ASSIST_AGENT_NOTION_URL")):
        raise RecoveryBindingError("External integration bindings require a separate recovery decision.")
    factory = factory_for(binding.data_root)  # normal strict ConnectionFactory, never migrates
    app = build_preview_app(factory, marker, answer_request_policy=answer_request_policy)
    app.config["SESSION_COOKIE_NAME"] = "sb_recovery_" + binding.document_sha256[:16]
    app.config["RECOVERED_SCHEMA_VERSION"] = catalog.latest_version
    return app, marker, binding


def main(argv=None):
    from code_assist_v1.tools.full_second_brain_preview import (
        _add_answer_request_arguments, _answer_request_policy_from_args)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--binding-sha256", required=True)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--codex", action="store_true",
        help="Expose Codex alongside the bound provider in the chat selector.")
    parser.add_argument("--stop-file", type=Path)
    _add_answer_request_arguments(parser)
    args = parser.parse_args(argv)
    policy = _answer_request_policy_from_args(args)
    # The application has its own import-time argument parser.
    sys.argv = [sys.argv[0]]
    app, marker, binding = compose(args.binding, args.binding_sha256, args.data_root,
                                  answer_request_policy=policy,
                                  codex_available=args.codex)
    identity = dict(data_root=str(binding.data_root), port=binding.port,
        code_root=str(Path(__file__).resolve().parents[1]),
        recovery_binding_sha256=binding.document_sha256, schema_version=app.config["RECOVERED_SCHEMA_VERSION"],
        provider_mode=binding.provider_mode, seeded=False, migrated=False,
        restored_source_available_required=False, company_api_verified=False,
        answer_request_policy=None if policy is None else policy.receipt())
    if args.check:
        print(json.dumps(dict(identity, status="composition_ready")), flush=True)
        return 0
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    watcher = None
    if args.stop_file is not None:
        stop_file = args.stop_file.absolute()
        if (not stop_file.is_relative_to(binding.data_root / ".runtime")
                or stop_file.exists() or stop_file.parent.resolve(strict=True) != stop_file.parent):
            raise RecoveryBindingError("Invalid recovery stop file.")
        def watch():
            while not stop.wait(0.25):
                if stop_file.exists():
                    stop.set()
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
    from code_assist_v1.tools.full_second_brain_preview_workers import PreviewWorkerSupervisor
    from code_assist_v1.knowledge_core.production_wsgi import ProductionWSGIConfig, ProductionWSGIHost
    supervisor = PreviewWorkerSupervisor(binding.data_root, marker_sha256=marker["marker_sha256"],
        codex_validation=binding.provider_mode == "codex-validation")
    try:
        supervisor.start()
        app.extensions["full_second_brain_preview_worker_supervisors"].append(supervisor)
        host = ProductionWSGIHost(app, ProductionWSGIConfig(
            service_id="second-brain-recovery", host="127.0.0.1", port=binding.port, threads=4))
        print(json.dumps(dict(identity, status="ready", workers=supervisor.status())), flush=True)
        host.serve(stop)
    finally:
        stop.set()
        supervisor.stop()
        if watcher is not None:
            watcher.join(timeout=2)
    print(json.dumps(dict(identity, status="stopped")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


