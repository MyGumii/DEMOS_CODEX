"""Restart an existing development preview without seeding or migrating data."""
import argparse
import json
from pathlib import Path
import os


def main(argv=None):
    from code_assist_v1.tools.full_second_brain_preview import (
        _add_answer_request_arguments, _answer_request_policy_from_args)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root",type=Path,required=True)
    parser.add_argument("--port",type=int,choices=[10221,10223],default=10221)
    parser.add_argument("--agent-worker",action="store_true")
    parser.add_argument("--check",action="store_true")
    _add_answer_request_arguments(parser)
    args=parser.parse_args(argv)
    policy=_answer_request_policy_from_args(args)
    root=args.data_root.resolve(strict=True)
    marker=json.loads((root/".runtime/full-second-brain-preview.json").read_text(encoding="utf-8"))
    if Path(marker["data_root"]).resolve(strict=True)!=root or marker.get("schema_version")!=3:
        raise RuntimeError("Existing preview marker does not match the chosen root")
    from code_assist_v1.tools.full_second_brain_preview import _configure_environment,_canonical_factory,build_preview_app
    _configure_environment(root,port=args.port,codex=True)
    # Persist only this existing preview's optional host bindings across restarts.
    host_config = root/".runtime/agent-integration-host.json"
    if host_config.is_file():
        values = json.loads(host_config.read_text(encoding="utf-8"))
        allowed = {"notion_user_data_root", "notion_url", "outlook_enabled"}
        if not isinstance(values, dict) or set(values)-allowed:
            raise ValueError("Invalid agent integration host configuration")
        if "notion_user_data_root" in values:
            target = Path(values["notion_user_data_root"])
            if not target.is_absolute() or not target.resolve(strict=True).is_dir():
                raise ValueError("An existing absolute Notion user data directory is required")
            os.environ["CODE_ASSIST_AGENT_NOTION_MODE"] = "legacy"
            os.environ["CODE_ASSIST_AGENT_NOTION_USER_DATA_ROOT"] = str(target.resolve(strict=True))
        if "notion_url" in values:
            from urllib.parse import urlsplit
            url = urlsplit(values["notion_url"])
            if url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1"} or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
                raise ValueError("A loopback Notion origin is required")
            os.environ["CODE_ASSIST_AGENT_NOTION_URL"] = values["notion_url"].rstrip("/")
        if "outlook_enabled" in values:
            if type(values["outlook_enabled"]) is not bool:
                raise ValueError("outlook_enabled must be a Boolean")
            os.environ["CODE_ASSIST_AGENT_OUTLOOK"] = "1" if values["outlook_enabled"] else "0"
    os.environ["CODE_ASSIST_AGENT_WORKER"]="1" if args.agent_worker and not args.check else "0"
    factory=_canonical_factory(root)
    app=build_preview_app(factory,marker,answer_request_policy=policy)
    extension=app.extensions.get("agent_integrations")
    if extension is None:
        raise RuntimeError("Integration composition failed")
    if args.check:
        print(json.dumps(dict(status="composition_ready",port=args.port,
            integration_routes=len(extension["bearer_endpoints"]),marker_sha256=marker["marker_sha256"],
            answer_request_policy=None if policy is None else policy.receipt())))
        return
    from waitress import serve
    from code_assist_v1.tools.full_second_brain_preview_workers import PreviewWorkerSupervisor
    supervisor=PreviewWorkerSupervisor(root,marker_sha256=marker["marker_sha256"],codex_validation=True)
    supervisor.start()
    app.extensions["full_second_brain_preview_worker_supervisors"].append(supervisor)
    print(json.dumps(dict(status="ready",port=args.port,marker_sha256=marker["marker_sha256"],workers=supervisor.status(),
        answer_request_policy=None if policy is None else policy.receipt())),flush=True)
    try:
        serve(app,host="127.0.0.1",port=args.port,threads=8)
    finally:
        worker=extension.get("worker")
        if worker:worker.stop()
        supervisor.stop()


if __name__=="__main__":
    main()
