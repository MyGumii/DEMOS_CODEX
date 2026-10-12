"""Separate machine and browser authority; bearer grants never alter sessions."""
from functools import wraps
import json
import os
from uuid import uuid4

from flask import g, jsonify, request, redirect, url_for

from . import API_PREFIX, CONTRACT_VERSION
from .contracts import IntegrationError, object_shape, text, uid, integer, SET_FIELDS, encoded


def error_response(exc):
    status = getattr(exc, "http_status", 500)
    if type(status) is not int or not 400 <= status <= 599:
        status = 500
    message = str(exc) if status < 500 or isinstance(exc, IntegrationError) else "연동 작업을 완료하지 못했습니다. 실행 이력을 확인해 주세요."
    response = jsonify(error=dict(code=getattr(exc, "code", "INTEGRATION_FAILED"), message=message))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response, status


def bearer_auth_gate(app):
    """Called by the existing auth gate *after* trusted-Host validation.

    Return (handled, response). Only exact registered agent endpoints may opt
    out of cookie CSRF. Cookie/Origin-bearing requests may never use this lane.
    Invalid Authorization never falls back to the browser's ambient account.
    """
    extension = app.extensions.get("agent_integrations")
    if extension is None or request.endpoint not in extension["bearer_endpoints"] or not request.headers.get("Authorization"):
        return False, None
    try:
        if request.headers.get("Cookie") or request.headers.get("Origin"):
            raise IntegrationError("에이전트 인증과 브라우저 인증을 함께 사용할 수 없습니다.", 400, "MIXED_AUTHENTICATION")
        header = request.headers["Authorization"]
        if not header.startswith("Bearer "):
            raise IntegrationError("Bearer 토큰이 필요합니다.", 401, "AUTHENTICATION_REQUIRED")
        g.agent_integration = extension["service"].authenticate(header[7:])
        return True, None
    except Exception as exc:
        return True, error_response(exc)


def register(app, service, principal_resolver):
    extension = dict(service=service, bearer_endpoints=set())
    app.extensions["agent_integrations"] = extension

    def route(path, methods, *, scope=None, browser_only=False):
        def decorate(fn):
            endpoint = "agent_integration_"+fn.__name__
            @wraps(fn)
            def wrapped(**kwargs):
                try:
                    delegate = getattr(g, "agent_integration", None)
                    if delegate:
                        if browser_only:
                            raise IntegrationError("사용자 확인이 필요한 작업입니다.", 403, "FORBIDDEN")
                        owner, cid = delegate["owner_id"], delegate["connection_id"]
                        service.authorize_connection(owner, cid, scope)
                        if "connection_id" in kwargs and kwargs["connection_id"] != cid:
                            raise IntegrationError("연결을 찾을 수 없습니다.", 404, "NOT_FOUND")
                    else:
                        # Browser-only operations reject even a valid bearer header.
                        if request.headers.get("Authorization"):
                            raise IntegrationError("사용자 로그인으로 실행해 주세요.", 403, "FORBIDDEN")
                        principal = principal_resolver()
                        if not isinstance(principal, dict):
                            raise IntegrationError("로그인이 필요합니다.", 401, "AUTHENTICATION_REQUIRED")
                        owner, cid = principal["id"], None
                        service.actor(owner)
                    result = fn(owner, cid, **kwargs)
                    response = app.make_response(result if isinstance(result, tuple) else jsonify(result))
                    if delegate and 200 <= response.status_code < 300:
                        expires = service.store.renew_after_use(owner, cid,
                            lambda: service.authorize_connection(owner, cid, scope))
                        response.headers["X-Agent-Token-Expires-At"] = str(expires)
                    response.headers["Cache-Control"] = "private, no-store"
                    response.headers["X-Content-Type-Options"] = "nosniff"
                    return response
                except Exception as exc:
                    return error_response(exc)
            app.add_url_rule(API_PREFIX+path, endpoint, wrapped, methods=methods)
            if not browser_only:
                extension["bearer_endpoints"].add(endpoint)
            return wrapped
        return decorate

    def body(required=(), optional=()):
        if request.mimetype != "application/json" or (request.content_length or 0) > 550000:
            raise IntegrationError("550KB 이하 JSON 요청이 필요합니다.", 413)
        raw = request.stream.read(550001)
        if len(raw)>550000:
            raise IntegrationError("요청이 너무 큽니다.", 413)
        def pairs(values):
            obj = {}
            for key, value in values:
                if key in obj:
                    raise ValueError("duplicate")
                obj[key] = value
            return obj
        try:
            value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise IntegrationError("JSON 요청이 올바르지 않습니다.") from None
        return object_shape(value, required, optional)

    def selected(owner, cid, value=None, scope=None):
        chosen = cid or uid(value, "connection_id")
        actor, config = service.authorize_connection(owner, chosen, scope)
        return chosen, actor, config

    @route("/status", ["GET"])
    def status(owner, cid):
        return dict(contract_version=CONTRACT_VERSION, enabled=True, notion_configured=service.notion is not None,
                    connection_token_reveal=True,
                    knowledge_sets_version=2 if hasattr(service.bridge, "view") else 1,
                    knowledge_sets=service.knowledge_description(owner,cid) if cid else None,
                    organization_views=(service.bridge.organization_views(service.actor(owner))
                        if not cid and hasattr(service.bridge, "organization_views") else []),
                    notion_url=os.environ.get("CODE_ASSIST_AGENT_NOTION_URL", "") if service.notion else "",
                    connection_id=cid, authoring_policy="knowledge-return-authoring-v1",
                    scopes=service.authorize_connection(owner,cid)[1]["scopes"] if cid else [],
                    mail_connector_enabled=service.outlook is not None,
                    background_refinement=bool(extension.get("worker") and extension["worker"].thread.is_alive()))

    def outlook():
        if service.outlook is None:
            raise IntegrationError("이 서버의 Outlook 연결 기능이 비활성 상태입니다.", 503, "MAIL_NOT_CONFIGURED")
        return service.outlook.graph

    @route("/outlook/status", ["GET"], browser_only=True)
    def outlook_status(owner, cid):
        if service.outlook is None:
            return dict(enabled=False, authenticated=False)
        result = outlook().connection_status(owner, probe=False)
        expected = url_for("agent_integration_outlook_callback", _external=True)
        return dict(result, enabled=True, expected_redirect_uri=expected,
                    redirect_matches=result.get("configuration", {}).get("redirect_uri") == expected)

    @route("/outlook/connect", ["POST"], browser_only=True)
    def outlook_connect(owner, cid):
        body()
        graph = outlook()
        expected = url_for("agent_integration_outlook_callback", _external=True)
        config = graph.public_configuration()
        if not config["configured"]:
            raise IntegrationError("Microsoft 앱의 서버 설정을 먼저 완료해 주세요.", 503, "OUTLOOK_SETUP_REQUIRED")
        if config["redirect_uri"] != expected:
            raise IntegrationError("Microsoft 앱의 리디렉션 주소를 이 서버의 콜백 주소로 설정해 주세요.", 503, "OUTLOOK_REDIRECT_MISMATCH")
        # OAuth is a short-lived, single-use state flow, not a replayable ledger mutation.
        return dict(authorization_url=graph.begin_authorization(owner))

    @route("/outlook/callback", ["GET"], browser_only=True)
    def outlook_callback(owner, cid):
        from ..outlook_graph import GraphOAuthError
        try:
            outlook().complete_authorization(owner, request.args.to_dict(flat=True))
        except GraphOAuthError:
            raise IntegrationError("Microsoft 로그인이 취소되었거나 만료되었습니다. 연결을 다시 시작해 주세요.",
                                   400, "OUTLOOK_OAUTH_FAILED") from None
        response = redirect("/?agent_outlook=connected", code=303)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response, 303

    @route("/outlook/disconnect", ["POST"], browser_only=True)
    def outlook_disconnect(owner, cid):
        body()
        outlook().disconnect(owner)
        return dict(disconnected=True)

    @route("/mail/inbox", ["POST"], scope="mail:read")
    def mail_inbox(owner, cid):
        data = body(("since_utc",), ("connection_id","limit","cursor_url"))
        connection_id, _, _ = selected(owner,cid,data.get("connection_id"),"mail:read")
        if service.outlook is None:
            raise IntegrationError("Outlook 연결이 설정되지 않았습니다.",503,"MAIL_NOT_CONFIGURED")
        result = service.outlook.inbox(owner,data["since_utc"],data.get("limit",50),data.get("cursor_url",""))
        service.authorize_connection(owner,connection_id,"mail:read")
        return result

    @route("/runs/<run_id>/observe-outlook-reply", ["POST"], scope="mail:read")
    def observe_outlook_reply(owner,cid,run_id):
        data = body(("operation_id","action_id","candidate_message_id","expected_version"))
        uid(data["operation_id"])
        run = service.store.run(owner,run_id,cid=cid)
        return service.observe_reply(owner,run["connection_id"],run_id,data)

    @route("/wikis", ["GET"], browser_only=True)
    def wikis(owner, cid):
        return service.bridge.wikis(service.actor(owner), request.args.get("cursor"))

    @route("/notion/targets", ["GET"], browser_only=True)
    def notion_targets(owner, cid):
        return {"items": []} if service.notion is None else service.notion.targets(service.actor(owner))

    @route("/connections", ["GET"], browser_only=True)
    def connections(owner, cid):
        return {"items": service.store.list_connections(owner)}

    @route("/connections", ["POST"], browser_only=True)
    def create_connection(owner, cid):
        data = body(("operation_id", "name", "read_space_id", "write_space_id", "scopes"), ("expires_days", "auto_renew", "auto_publish_sources", "notion_parent_id", *SET_FIELDS))
        return service.create_connection(owner, data)

    @route("/connections/<connection_id>/revoke", ["POST"], browser_only=True)
    def revoke(owner, cid, connection_id):
        data = body(("operation_id",))
        return service.store.revoke(owner, connection_id, data["operation_id"], lambda: service.actor(owner))

    @route("/connections/<connection_id>/renewal", ["POST"], browser_only=True)
    def renewal(owner, cid, connection_id):
        data = body(("operation_id", "expected_version", "auto_renew"))
        return service.store.set_renewal(owner, connection_id, data["operation_id"],
            data["expected_version"], data["auto_renew"], lambda: service.authorize_connection(owner, connection_id))

    @route("/connections/<connection_id>/token", ["POST"], browser_only=True)
    def reveal_token(owner, cid, connection_id):
        data = body(("operation_id",))
        result = service.store.reveal_token(owner, connection_id, data["operation_id"],
            lambda: service.authorize_connection(owner, connection_id))
        response = jsonify(result)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response, 200

    @route("/knowledge/search", ["POST"], scope="knowledge:read")
    def search(owner, cid):
        data = body(("query",), ("connection_id", "read_mode", "personal_space_ids", "max_bytes"))
        chosen, _, _ = selected(owner, cid, data.get("connection_id"), "knowledge:read")
        result = service.knowledge_search(owner, chosen, data["query"], mode=data.get("read_mode"),
            personal_space_ids=data.get("personal_space_ids"), max_bytes=data.get("max_bytes", 16384))
        # Preserve the exact budgeted UTF-8 JSON bytes even when a host's Flask
        # provider escapes non-ASCII text or enables debug pretty-printing.
        if "max_bytes" in result:
            return app.response_class(encoded(result), mimetype="application/json"), 200
        return result

    @route("/knowledge/items/<item_id>", ["GET"], scope="knowledge:read")
    def get_item(owner, cid, item_id):
        object_shape(request.args.to_dict(), (), ("connection_id", "view_space_id", "read_mode"))
        chosen, _, _ = selected(owner, cid, request.args.get("connection_id"), "knowledge:read")
        return service.knowledge_read(owner, chosen, item_id, view_space_id=request.args.get("view_space_id"),
            mode=request.args.get("read_mode"))

    @route("/knowledge/ask", ["POST"], scope="knowledge:ask")
    def ask(owner, cid):
        data = body(("operation_id", "message"), ("connection_id", "read_mode", "personal_space_ids"))
        chosen, _, _ = selected(owner, cid, data.get("connection_id"), "knowledge:ask")
        return service.knowledge_ask(owner, chosen, data["operation_id"], data["message"],
            mode=data.get("read_mode"), personal_space_ids=data.get("personal_space_ids"))

    @route("/connections/<connection_id>/runs", ["POST"], scope="results:write")
    def submit(owner, cid, connection_id):
        data = body(("operation_id", "kind", "title", "sources"), ("result", "actions", "external_key", "followup_of", "read_mode", "publication_target"))
        return jsonify(service.submit(owner, connection_id, data)), 202

    @route("/runs", ["GET"], scope="results:write")
    def runs(owner, cid):
        return service.store.list_runs(owner, cid=cid, cursor=request.args.get("cursor"))

    @route("/runs/<run_id>", ["GET"], scope="results:write")
    def get_run(owner, cid, run_id):
        return service.store.run(owner, run_id, cid=cid)

    @route("/runs/<run_id>/process", ["POST"], scope="results:write")
    def process(owner, cid, run_id):
        data = body(("operation_id",))
        run = service.store.run(owner, run_id, cid=cid)
        return service.effect(owner,run["connection_id"],data["operation_id"],"process",run_id)

    @route("/connections/<connection_id>/process-next", ["POST"], scope="results:write")
    def process_next(owner, cid, connection_id):
        data = body(("operation_id",))
        return service.effect(owner,connection_id,data["operation_id"],"process-next")

    @route("/runs/<run_id>/evidence", ["POST"], browser_only=True)
    def evidence(owner, cid, run_id):
        data = body(("operation_id", "expected_version", "action_id", "kind", "body"))
        return service.store.evidence(owner, run_id, data["operation_id"], data["action_id"], data["kind"], data["body"],
                                      data["expected_version"], lambda: service.actor(owner))

    @route("/runs/<run_id>/review", ["POST"], browser_only=True)
    def review(owner, cid, run_id):
        data = body(("operation_id", "expected_version", "draft_hash", "decision"))
        return service.store.review(owner, run_id, data["operation_id"], integer(data["expected_version"], "expected_version"),
                                    text(data["draft_hash"], "draft_hash", 64), data["decision"], lambda: service.actor(owner))

    @route("/runs/<run_id>/publish", ["POST"], scope="results:write")
    def publish(owner, cid, run_id):
        data = body(("operation_id",))
        run = service.store.run(owner, run_id, cid=cid)
        return service.effect(owner,run["connection_id"],data["operation_id"],"publish",run_id,automatic=cid is not None)

    @route("/runs/<run_id>/notion", ["POST"], scope="notion:write")
    def notion(owner, cid, run_id):
        data = body(("operation_id",))
        run = service.store.run(owner, run_id, cid=cid)
        return service.effect(owner,run["connection_id"],data["operation_id"],"notion",run_id)


def register_from_core(app, core):
    """Same composition hook in development and production app_code."""
    if app.extensions.get("agent_integrations"):
        return
    enabled = os.environ.get("CODE_ASSIST_AGENT_INTEGRATIONS", "1")
    if enabled not in {"0", "1"}:
        raise ValueError("CODE_ASSIST_AGENT_INTEGRATIONS must be 0 or 1")
    if enabled == "0" or core.services.source_artifacts is None or core.services.personal_wikis is None:
        return
    from .bridge import KnowledgeBridge
    from .store import IntegrationStore
    from .service import IntegrationService
    bridge = KnowledgeBridge(core.services, core.canonical_route_bindings)
    store = IntegrationStore(bridge.factory.database_path.parent / "agent_integrations.db", app.secret_key)
    store.initialize()
    from .notion import configured_connector
    from .outlook import OutlookConnector
    service = IntegrationService(store, bridge, notion=configured_connector(),
        outlook=OutlookConnector() if os.environ.get("CODE_ASSIST_AGENT_OUTLOOK","0")=="1" else None,
        provider_order=getattr(core.canonical_route_bindings.turn_executor, "provider_order", None),
        model_ids=getattr(core.canonical_route_bindings.turn_executor,"model_ids",None))
    register(app, service, core.canonical_route_bindings.principal_resolver)
    worker_enabled = os.environ.get("CODE_ASSIST_AGENT_WORKER","0")
    if worker_enabled not in {"0","1"}:
        raise ValueError("CODE_ASSIST_AGENT_WORKER must be 0 or 1")
    if worker_enabled == "1":
        from .worker import IntegrationWorker
        worker = IntegrationWorker(service)
        app.extensions["agent_integrations"]["worker"] = worker
        worker.start()
