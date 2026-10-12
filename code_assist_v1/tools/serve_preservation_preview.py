"""Serve only the separately cloned 10421 development environment.

The durable upload/publication policy is selected by the clone's explicit
activation receipt. Authoring-only mode never claims semantic completeness.
Existing 10221/10321 roots, workers, cookies and integration bindings are unused.
"""
import argparse
import json
import os
from pathlib import Path


def compose(root, *, answer_request_policy=None):
    from code_assist_v1.knowledge_core.answer_request import AnswerRequestPolicy
    if answer_request_policy is not None and type(answer_request_policy) is not AnswerRequestPolicy:
        raise TypeError("answer_request_policy must be an AnswerRequestPolicy or None.")
    root = root.resolve(strict=True)
    if root != Path("D:/CodexTestTemp/SecondBrain-10421-20260911").resolve(strict=True):
        raise ValueError("Only the registered 10421 clone may be served.")
    from code_assist_v1.tools.full_second_brain_preview import (
        _configure_environment, _load_marker, _canonical_factory, build_preview_app)
    marker = _load_marker(root)
    receipt = json.loads((root / ".runtime/clone-receipt.json").read_text(encoding="utf-8"))
    if (marker.get("clone_port") != 10421 or receipt.get("port") != 10421
            or Path(receipt["target_root"]).resolve(strict=True) != root
            or receipt.get("source_mutated_by_clone") is not False):
        raise ValueError("10421 clone evidence mismatch.")
    _configure_environment(root, port=10421, codex=True)
    # A data clone must not resume writes to external Notion/Outlook services.
    os.environ["CODE_ASSIST_AGENT_WORKER"] = "0"
    os.environ["CODE_ASSIST_AGENT_OUTLOOK"] = "0"
    os.environ.pop("CODE_ASSIST_AGENT_NOTION_MODE", None)
    os.environ.pop("CODE_ASSIST_AGENT_NOTION_USER_DATA_ROOT", None)
    os.environ.pop("CODE_ASSIST_AGENT_NOTION_URL", None)
    from code_assist_v1.tools.preservation_preview_runtime import enabled, factory_for, authoring_only_enabled, structured_authoring_enabled, full_label_authoring_enabled
    active = enabled(root)
    authoring_only = active and authoring_only_enabled(root)
    structured = active and structured_authoring_enabled(root)
    full_labels = active and full_label_authoring_enabled(root)
    from code_assist_v1.tools.native_publication_preview_runtime import native_publication_enabled
    native = native_publication_enabled(root)
    factory = factory_for(root) if active else _canonical_factory(root)
    app = build_preview_app(factory, marker, answer_request_policy=answer_request_policy)
    # Development-only isolation; Flask evaluates the name per request. The
    # production cookie policy remains untouched. The clone has its own secret.
    app.config["SESSION_COOKIE_NAME"] = "second_brain_10421"

    @app.after_request
    def preview_identity(response):
        response.headers["X-Second-Brain-Preview"] = "10421"
        return response

    @app.get("/api/code/development-status")
    def development_status():
        from flask import jsonify
        return jsonify(port=10421, isolated_code=True, isolated_data=True,
            source_ports_modified=False, preservation="upload_integrated" if active else "candidate_not_integrated",
            preservation_complete=False, company_api_verified=False,
            semantic_completeness_proven=False,
            wiki_authoring_mode="authoring_only" if authoring_only else "reviewed",
            structured_authoring_enabled=structured,
            full_label_authoring_enabled=full_labels,
            native_publication_enabled=native,
            source_extractor_version=("format-routed" if native else "rich-source-v3-bounded-tables" if structured else "rich-source-v2" if active else "rich-v1"),
            source_extractor_versions=({"docx": "rich-source-v4-native-tables", "pptx": "rich-source-v4-native-tables",
                "other_supported": "rich-source-v3-bounded-tables"} if native else None),
            llm_semantic_review_enabled=active and not authoring_only)

    return app, marker


def main(argv=None):
    from code_assist_v1.tools.full_second_brain_preview import (
        _add_answer_request_arguments, _answer_request_policy_from_args)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    _add_answer_request_arguments(parser)
    args = parser.parse_args(argv)
    policy = _answer_request_policy_from_args(args)
    app, marker = compose(args.data_root, answer_request_policy=policy)
    policy_receipt = None if policy is None else policy.receipt()
    if args.check:
        from code_assist_v1.tools.preservation_preview_runtime import enabled
        print(json.dumps({"status": "composition_ready", "port": 10421,
            "cookie_name": app.config["SESSION_COOKIE_NAME"], "answer_request_policy": policy_receipt,
            "preservation": "upload_integrated" if enabled(args.data_root) else "candidate_not_integrated"}), flush=True)
        return
    from code_assist_v1.tools.full_second_brain_preview_workers import PreviewWorkerSupervisor
    from waitress import serve
    supervisor = PreviewWorkerSupervisor(args.data_root.resolve(strict=True),
        marker_sha256=marker["marker_sha256"], codex_validation=True)
    supervisor.start()
    app.extensions["full_second_brain_preview_worker_supervisors"].append(supervisor)
    from code_assist_v1.tools.preservation_preview_runtime import enabled
    print(json.dumps({"status": "ready", "port": 10421, "pid": os.getpid(), "answer_request_policy": policy_receipt,
        "preservation": "upload_integrated" if enabled(args.data_root) else "candidate_not_integrated", "workers": supervisor.status()}), flush=True)
    try:
        serve(app, host="127.0.0.1", port=10421, threads=8)
    finally:
        supervisor.stop()


if __name__ == "__main__":
    main()
