"""Authenticated, edit-authorized AI drafts; saves use existing page contracts."""
from __future__ import annotations

from threading import BoundedSemaphore

from flask import current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from code_assist_v1 import notion_ai_notes as notes
from code_assist_v1 import notion_http, notion_store
from code_assist_v1 import notion_audio
from code_assist_v1 import notion_notes_translation as translation
from code_assist_v1 import internal_llm
from code_assist_v1.auth import current_user_id

_slots = BoundedSemaphore(2)
_audio_slots = BoundedSemaphore(2)
_translation_slots = BoundedSemaphore(2)


def require_edit(user_id: str, page_id: str, workspace_id: str = "") -> None:
    if not user_id:
        raise PermissionError("로그인이 필요합니다.")
    if not page_id:
        raise ValueError("page_id required")
    from code_assist_v1.notion_collaboration import iam_runtime

    if iam_runtime.runtime_requested():
        from code_assist_v1.routes_notion_workspace import _store

        authority = _store().iam
        payload = iam_runtime.collaborative_page_payload(authority, user_id, page_id)
        page = payload["page"]
        if not workspace_id or page.get("workspace_id") != workspace_id:
            raise FileNotFoundError("not found or permission denied")
    else:
        page = notion_store.get_page(user_id, page_id)["page"]
    if not page.get("can_edit") or page.get("settings", {}).get("locked"):
        raise PermissionError("AI 노트를 생성하려면 잠기지 않은 페이지의 편집 권한이 필요합니다.")


def _json(payload: dict):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


def register_notion_ai_notes_routes(app):
    from code_assist_v1 import notion_notes_comparison as comparisons
    comparisons.register(app, require_edit)

    def comparison_service():
        service = current_app.extensions.get(comparisons.VERSION)
        if not service:
            raise notes.NotesUnavailable("호스트의 비공개 비교 수집이 아직 설정되지 않았습니다.")
        return service

    @app.get("/api/notion/ai-notes/capabilities")
    def api_notion_ai_notes_capabilities():
        if not current_user_id():
            return notion_http.validation_response("로그인이 필요합니다.", status=401)
        audio = notion_audio.capabilities()
        models = notes.available_models()
        return _json({
            "models": models,
            "codex_bridge": {"enabled": internal_llm.codex_internal_enabled(),
                             "connected": any(model["provider"] == "codex" for model in models)},
            "translation": {"enabled": bool(models), "max_text_chars": translation.MAX_TEXT,
                            "default_model_id": ""},
            "max_transcript_chars": notes.MAX_SOURCE_CHARS,
            "max_notes_chars": notes.MAX_NOTES_CHARS,
            "audio_transcription": audio["enabled"],
            "audio": audio,
            "comparison": {"enabled": comparisons.VERSION in current_app.extensions,
                           "notice_version": comparisons.VERSION, "results_visible": False},
        })

    @app.get("/api/notion/ai-notes/comparison-sources")
    def api_notion_ai_notes_comparison_sources():
        if not current_user_id():
            return notion_http.validation_response("로그인이 필요합니다.", status=401)
        try:
            return _json(comparison_service().reader.scopes(str(current_user_id()), request.args.get("cursor")))
        except notes.NotesUnavailable as exc:
            return notion_http.validation_response(str(exc), status=503)
        except Exception as exc:
            return notion_http.error_response(exc)

    @app.post("/api/notion/ai-notes/comparisons")
    def api_notion_ai_notes_comparison_submit():
        if not current_user_id():
            return notion_http.validation_response("로그인이 필요합니다.", status=401)
        try:
            request.max_content_length = 512000
            data = notion_http.json_object_request()
            return _json(comparison_service().submit(str(current_user_id()), data)), 202
        except notes.NotesUnavailable as exc:
            return notion_http.validation_response(str(exc), status=503)
        except Exception as exc:
            return notion_http.error_response(exc)

    @app.get("/api/notion/ai-notes/comparisons/<run_id>")
    def api_notion_ai_notes_comparison_status(run_id):
        if not current_user_id():
            return notion_http.validation_response("로그인이 필요합니다.", status=401)
        try:
            return _json(comparison_service().store.status(str(current_user_id()), run_id))
        except notes.NotesUnavailable as exc:
            return notion_http.validation_response(str(exc), status=503)
        except Exception as exc:
            return notion_http.error_response(exc)

    @app.post("/api/notion/ai-notes/transcribe")
    def api_notion_ai_notes_transcribe():
        if not current_user_id():
            return notion_http.validation_response("로그인이 필요합니다.", status=401)
        try:
            request.max_content_length = notion_audio.MAX_AUDIO_BYTES + 65536
            request.max_form_memory_size = 131072
            request.max_form_parts = 8
            if request.mimetype != "multipart/form-data":
                raise ValueError("multipart 오디오 파일을 보내주세요.")
            fields = request.form.to_dict()
            uid = str(current_user_id())
            page_id = notes.text_field(fields, "page_id", 160)
            workspace_id = notes.text_field(fields, "workspace_id", 160)
            segment_id = notes.text_field(fields, "segment_id", 100)
            language = notes.text_field(fields, "language", 2)
            if not segment_id:
                raise ValueError("segment_id required")
            if fields.get("consent") != "true":
                raise ValueError("음성 입력 시작을 확인해 주세요.")

            def guard():
                require_edit(uid, page_id, workspace_id)

            guard()
            upload = request.files.get("file")
            if upload is None or len(list(request.files.items(multi=True))) != 1:
                raise ValueError("오디오 파일 한 개를 선택하세요.")
            if not _audio_slots.acquire(blocking=False):
                return notion_http.validation_response("음성 인식이 처리 중입니다. 잠시 후 재시도하세요.", status=429)
            try:
                audio = upload.read(notion_audio.MAX_AUDIO_BYTES + 1)
                result = notion_audio.transcribe(audio, language=language, guard=guard)
                guard()
                return _json({"status": "transcribed", "segment_id": segment_id, **result})
            finally:
                upload.close()
                _audio_slots.release()
        except RequestEntityTooLarge:
            return notion_http.validation_response("오디오 요청은 20 MiB 이하여야 합니다.", status=413)
        except notes.NotesUnavailable as exc:
            return notion_http.validation_response(str(exc), code="stt_unavailable", status=503)
        except Exception as exc:
            return notion_http.error_response(exc)

    @app.post("/api/notion/ai-notes/translate")
    def api_notion_ai_notes_translate():
        if not current_user_id():
            return notion_http.validation_response("로그인이 필요합니다.", status=401)
        try:
            request.max_content_length = 64000
            data = notion_http.json_object_request()
            uid = str(current_user_id())
            page_id = notes.text_field(data, "page_id", 160)
            workspace_id = notes.text_field(data, "workspace_id", 160)

            def guard():
                require_edit(uid, page_id, workspace_id)

            guard()
            if not _translation_slots.acquire(blocking=False):
                return notion_http.validation_response("번역 요청이 처리 중입니다. 잠시 후 재시도하세요.", status=429)
            try:
                result = translation.translate(data, guard=guard)
                guard()
                return _json({"status": "translated", **result})
            finally:
                _translation_slots.release()
        except RequestEntityTooLarge:
            return notion_http.validation_response("번역 요청은 64,000바이트 이하여야 합니다.", status=413)
        except notes.NotesUnavailable as exc:
            return notion_http.validation_response(str(exc), code="translation_unavailable", status=503)
        except Exception as exc:
            return notion_http.error_response(exc)

    @app.post("/api/notion/ai-notes/summarize")
    def api_notion_ai_notes_summarize():
        if not current_user_id():
            return notion_http.validation_response("로그인이 필요합니다.", status=401)
        if request.content_length and request.content_length > 512000:
            return notion_http.validation_response("요청 크기가 너무 큽니다.", status=413)
        try:
            data = notion_http.json_object_request()
            if len(request.get_data()) > 512000:
                return notion_http.validation_response("요청 크기가 너무 큽니다.", status=413)
            uid = str(current_user_id())
            page_id = notes.text_field(data, "page_id", 160)
            workspace_id = notes.text_field(data, "workspace_id", 160)

            def guard():
                require_edit(uid, page_id, workspace_id)

            guard()
            if not _slots.acquire(blocking=False):
                return notion_http.validation_response("AI 노트를 처리 중입니다. 잠시 후 다시 시도하세요.", status=429)
            try:
                result = notes.summarize(data, guard=guard)
                guard()
                return _json({"status": "draft", **result})
            finally:
                _slots.release()
        except notes.NotesUnavailable as exc:
            return notion_http.validation_response(str(exc), code="ai_notes_unavailable", status=503)
        except Exception as exc:
            return notion_http.error_response(exc)
