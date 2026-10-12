"""Authenticated API for the independent AI home; no automatic page writes."""
from functools import wraps
import json
from threading import BoundedSemaphore

from flask import Response, current_app, jsonify, request, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge

from code_assist_v1 import notion_http
from code_assist_v1.auth import current_user_id
from code_assist_v1 import notion_ai_chat_artifacts as artifacts
from code_assist_v1 import notion_meeting_providers as providers
from code_assist_v1.notion_ai_chat import ChatService, ChatGenerationFailed
from code_assist_v1.notion_ai_chat_store import ChatConflict
from code_assist_v1.routes_notion_document_ai import field, AiUnavailable, DocumentChanged
from code_assist_v1.notion_ai_notes import NotesUnavailable

_slots = BoundedSemaphore(2)
SERVICE_KEY = "notion-ai-home-v1"


def register_notion_ai_chat_routes(app):
    app.extensions[SERVICE_KEY] = ChatService(app)
    app.extensions[SERVICE_KEY].research_jobs.start()

    def api(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            uid = current_user_id()
            if not uid:
                response = jsonify(message="로그인이 필요합니다.")
                response.headers["Cache-Control"] = "no-store"
                return response, 401
            try:
                request.max_content_length = 96000
                result = function(str(uid), current_app.extensions[SERVICE_KEY], *args, **kwargs)
                response = result if isinstance(result, Response) else jsonify(result)
                response.headers["Cache-Control"] = "no-store"
                return response
            except (ChatConflict, DocumentChanged) as exc:
                response, status = jsonify(message=str(exc), code="chat_conflict"), 409
            except ChatGenerationFailed as exc:
                response, status = jsonify(message=str(exc), code=exc.code), 503
            except (AiUnavailable, NotesUnavailable):
                response, status = jsonify(message="AI 또는 참고 자료 연결을 사용할 수 없습니다. 호스트 설정을 확인하고 다시 요청하세요."), 503
            except RequestEntityTooLarge:
                response, status = jsonify(message="요청이 너무 큽니다."), 413
            except Exception as exc:
                response, status = notion_http.error_response(exc)
            response.headers["Cache-Control"] = "no-store"
            return response, status
        return wrapped

    @app.get("/api/notion/ai-home/capabilities")
    @api
    def ai_home_capabilities(uid, service):
        models = [{"id": mid, "name": str(cfg.get("name") or mid), "provider": lane} for mid, lane, cfg in providers.text_models()]
        try:
            scopes = service.reader.scopes(uid)
            knowledge = {"enabled": True, **scopes}
        except Exception:
            knowledge = {"enabled": False, "items": [], "next_cursor": None}
        from code_assist_v1.notion_collaboration import iam_runtime
        web = service.web_reader.capability()
        return {"models": models, "knowledge": knowledge, "notion": service.workspace_reader.capability(uid), "modes": artifacts.MODES,
                "exports": {"slides": "html", "spreadsheet": "csv", "visualization": "svg", "research": "md"},
                "web_search": web["enabled"], "web": web, "research": {"multi_step": True, "durable": True,
                "depth": "deep", "resume": True, "pdf_sources": True,
                "database_queries": not iam_runtime.runtime_requested(), "save_page": not iam_runtime.runtime_requested()},
                "streaming": True, "max_pages": 4, "max_excerpt_bytes": 98304}

    @app.post("/api/notion/ai-home/conversations/<cid>/save-report")
    @api
    def ai_home_save_report(uid, service, cid):
        return service.save_report(uid, cid, notion_http.json_object_request())

    @app.get("/api/notion/ai-home/wikis")
    @api
    def ai_home_wikis(uid, service):
        return service.reader.scopes(uid, request.args.get("cursor"))

    @app.route("/api/notion/ai-home/conversations", methods=["GET", "POST"])
    @api
    def ai_home_conversations(uid, service):
        if request.method == "POST":
            return service.create(uid, notion_http.json_object_request())
        cursor = request.args.get("cursor", "0")
        if not cursor.isdigit() or int(cursor) > 500:
            raise ValueError("invalid cursor")
        return service.list(uid, request.args.get("workspace_id", ""), request.args.get("archived") == "1", int(cursor), request.args.get("q", ""))

    @app.get("/api/notion/ai-home/conversations/<cid>")
    @api
    def ai_home_conversation(uid, service, cid):
        return service.get(uid, cid)

    @app.post("/api/notion/ai-home/conversations/<cid>/messages")
    @api
    def ai_home_messages(uid, service, cid):
        data = notion_http.json_object_request()
        if data.get("mode") == "research":
            return service.research_jobs.submit(uid, cid, data)
        if not _slots.acquire(blocking=False):
            response = jsonify(message="AI 채팅이 처리 중입니다. 잠시 후 다시 보내세요.")
            response.status_code = 429
            return response
        try:
            return service.ask(uid, cid, data)
        finally:
            _slots.release()

    @app.post("/api/notion/ai-home/conversations/<cid>/messages/stream")
    @api
    def ai_home_messages_stream(uid, service, cid):
        data = notion_http.json_object_request()
        if data.get("mode") == "research":
            result = service.research_jobs.submit(uid, cid, data)
            return Response("data: " + json.dumps({"type": "done", "conversation": result}, ensure_ascii=True) + "\n\n",
                            content_type="text/event-stream; charset=utf-8")
        if not _slots.acquire(blocking=False):
            response = jsonify(message="AI 채팅이 처리 중입니다. 잠시 후 다시 보내세요.")
            response.status_code = 429
            return response
        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                _slots.release()

        @stream_with_context
        def events():
            iterator = service.ask_events(uid, cid, data)
            try:
                for event in iterator:
                    yield "data: " + json.dumps(event, ensure_ascii=True) + "\n\n"
            except (ChatConflict, DocumentChanged) as exc:
                yield frame_error(str(exc), "chat_conflict", 409)
            except ChatGenerationFailed as exc:
                yield frame_error(str(exc), exc.code, 503)
            except (AiUnavailable, NotesUnavailable):
                yield frame_error("AI 또는 참고 자료 연결을 사용할 수 없습니다. 호스트 설정을 확인하고 다시 요청하세요.", "ai_unavailable", 503)
            except Exception as exc:
                error = notion_http.classify_error(exc)
                yield frame_error(error.payload["message"], error.payload["code"], error.status)
            finally:
                try:
                    iterator.close()
                finally:
                    release()

        response = Response(events(), content_type="text/event-stream; charset=utf-8")
        response.headers["X-Accel-Buffering"] = "no"
        response.call_on_close(release)
        return response

    @app.post("/api/notion/ai-home/conversations/<cid>/research")
    @api
    def ai_home_research(uid, service, cid):
        data = notion_http.json_object_request()
        if data.get("action"):
            return service.research_jobs.control(uid, cid, data)
        return service.research_jobs.submit(uid, cid, data)

    @app.post("/api/notion/ai-home/conversations/<cid>/archive")
    @api
    def ai_home_archive(uid, service, cid):
        data = notion_http.json_object_request()
        # An owner may archive an inaccessible conversation without exposing its body.
        service.store(uid).archive(cid, field(data, "operation_id", 36, True), data.get("archived"))
        return {"id": cid, "archived": data["archived"]}

    @app.get("/api/notion/ai-home/conversations/<cid>/messages/<op>/download/<kind>")
    @api
    def ai_home_download(uid, service, cid, op, kind):
        record = service.get(uid, cid)
        message = next((m for m in record["messages"] if m["role"] == "assistant" and m["operation_id"] == op), None)
        if not message:
            raise FileNotFoundError("생성 결과를 찾을 수 없습니다.")
        content, mime, filename = artifacts.export(message, kind)
        response = Response(content, content_type=mime)
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        return response


def frame_error(message, code, status):
    return "data: " + json.dumps({"type": "error", "message": message,
                                 "code": code, "status": status}, ensure_ascii=True) + "\n\n"
