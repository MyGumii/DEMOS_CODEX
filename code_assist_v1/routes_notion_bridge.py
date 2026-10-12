"""
Bridge routes exposed by the 10011 Second Brain Agent for Notion workspace data.
"""
from __future__ import annotations

from typing import Any

from flask import jsonify, request

from code_assist_v1 import notion_http
from code_assist_v1 import notion_publication
from code_assist_v1 import notion_store as ns
from code_assist_v1 import second_brain_store as sb
from code_assist_v1.auth import current_user_id
from code_assist_v1.concurrency import user_write_lock
from code_assist_v1.internal_llm import run_internal_text


def _uid() -> str | None:
    return current_user_id()


def _json_data() -> dict:
    return notion_http.json_object_request()


def _error_response(exc: Exception):
    return notion_http.error_response(exc)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "input_text"}:
                    parts.append(str(item.get("text") or ""))
                elif item.get("text"):
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") in {"image", "image_url", "input_image"}:
                    parts.append("[image]")
            elif item is not None:
                parts.append(str(item))
        return "\n".join(part for part in parts if part.strip())
    return str(content or "")


def _conversation_markdown(messages: list[dict], *, max_chars: int = 80000) -> tuple[str, bool]:
    lines: list[str] = []
    for idx, msg in enumerate(messages or [], start=1):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "message")
        content = _message_text(msg.get("content")).strip()
        if not content:
            continue
        lines.extend([f"## {idx}. {role}", "", content, ""])
    text = "\n".join(lines).strip()
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "\n\n...(conversation truncated for summarization)", True


def _summary_system_prompt(mode: str) -> str:
    if mode == "decisions":
        return (
            "You convert a Second Brain Agent chat transcript into a Notion-ready Korean Markdown note. "
            "Focus on decisions, action items, unresolved questions, owners if mentioned, and reusable rules. "
            "Do not include chain-of-thought. Return Markdown only."
        )
    return (
        "You convert a Second Brain Agent chat transcript into a concise Notion-ready Korean Markdown note. "
        "Extract the durable knowledge, user preferences, technical decisions, implementation context, and next actions. "
        "Do not include chain-of-thought. Return Markdown only."
    )


def _summary_user_prompt(title: str, mode: str, transcript: str, truncated: bool) -> str:
    if mode == "decisions":
        format_hint = """
Use this Markdown structure:

# {title}

## 결정사항

## 할 일

## 열린 질문

## 관련 맥락

## 원문에서 보존할 포인트
""".strip()
    else:
        format_hint = """
Use this Markdown structure:

# {title}

## 핵심 요약

## 중요한 맥락

## 결정/합의

## 후속 작업

## 참고할 원문 포인트
""".strip()
    return (
        f"{format_hint.format(title=title or 'Chat Summary')}\n\n"
        f"Transcript truncated: {'yes' if truncated else 'no'}\n\n"
        "=== CHAT TRANSCRIPT ===\n"
        f"{transcript}"
    )


def _summarize_chat_for_notion(messages: list[dict], *, title: str, mode: str) -> dict:
    transcript, truncated = _conversation_markdown(messages)
    prompt_messages = [
        {"role": "system", "content": _summary_system_prompt(mode)},
        {"role": "user", "content": _summary_user_prompt(title, mode, transcript, truncated)},
    ]
    text, meta = run_internal_text(
        "chat_to_notion_summary",
        {"title": title, "mode": mode, "truncated": truncated},
        messages=prompt_messages,
        max_tokens=4096,
        temperature=0.15,
    )
    if not text:
        raise RuntimeError(meta.get("error") or "LLM summary failed")
    return {
        "markdown": _ensure_markdown_title(text, title),
        "model_kind": meta.get("model_kind") or "",
        "model_used": meta.get("model_used") or "",
        "fallback_errors": notion_http.public_fallback_errors(meta),
    }


def _ensure_markdown_title(markdown: str, title: str) -> str:
    text = str(markdown or "").strip()
    if not text:
        return f"# {title or 'Chat Summary'}\n"
    if text.lstrip().startswith("# "):
        return text + "\n"
    return f"# {title or 'Chat Summary'}\n\n{text}\n"


def _append_chat_source_footer(markdown: str, *, session_id: str, mode: str, model_kind: str = "", model_used: str = "") -> str:
    lines = [
        str(markdown or "").rstrip(),
        "",
        "---",
        "Source: Second Brain Agent chat session",
        f"Session: {session_id or 'current'}",
        f"Save mode: {mode}",
    ]
    if model_kind or model_used:
        lines.append(f"Summary model: {model_kind or '-'} / {model_used or '-'}")
    return "\n".join(lines).rstrip() + "\n"


def register_notion_bridge_routes(app):
    notion_http.register_error_handlers(app)

    @app.route("/api/code/notion/pages", methods=["GET"])
    def api_code_notion_pages():
        try:
            items = ns.list_pages(_uid(), query=request.args.get("q", ""))
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/code/notion/templates/second_brain", methods=["POST"])
    def api_code_notion_second_brain_template():
        uid = _uid()
        try:
            with user_write_lock("notion_template", uid):
                result = ns.create_second_brain_template(uid)
        except Exception as e:
            return _error_response(e)
        return jsonify(result)

    @app.route("/api/code/notion/pages/<path:page_id>/export", methods=["GET"])
    def api_code_notion_export(page_id):
        try:
            result = ns.export_page_markdown(_uid(), page_id, include_assets=False)
        except Exception as e:
            return _error_response(e)
        return jsonify(result)

    @app.route("/api/code/notion/pages/<path:page_id>/publish_second_brain", methods=["POST"])
    def api_code_notion_publish(page_id):
        uid = _uid()
        data = _json_data()
        try:
            _snapshot, result = notion_publication.publish_page_to_legacy_second_brain(
                uid,
                page_id,
                source_store=ns,
                target_store=sb,
                write_lock=user_write_lock,
                expected_source_snapshot_sha256=data.get(
                    "expected_source_snapshot_sha256"
                ) or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "published", **result})

    @app.route("/api/code/notion/session_attach", methods=["POST"])
    def api_code_notion_session_attach():
        data = _json_data()
        try:
            item = ns.attach_page_to_session(
                _uid(),
                data.get("page_id") or "",
                data.get("session_id") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "attached", **item})

    @app.route("/api/code/notion/session_detach", methods=["POST"])
    def api_code_notion_session_detach():
        data = _json_data()
        try:
            item = ns.detach_page_from_session(
                _uid(),
                data.get("page_id") or "",
                data.get("session_id") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "detached", **item})

    @app.route("/api/code/notion/session_pages", methods=["GET"])
    def api_code_notion_session_pages():
        try:
            items = ns.list_session_pages(_uid(), request.args.get("session_id") or "")
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/code/notion/save_chat_summary", methods=["POST"])
    def api_code_notion_save_chat_summary():
        data = _json_data()
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        if not messages:
            return notion_http.validation_response("messages required")
        title = data.get("title") or "Chat Summary"
        mode_data = dict(data)
        if "mode" not in mode_data and "save_mode" in mode_data:
            mode_data["mode"] = mode_data.get("save_mode")
        mode = notion_http.enum_field(
            mode_data,
            "mode",
            {"full", "summary", "decisions"},
            default="full",
        )
        session_id = data.get("session_id") or ""
        model_kind = ""
        model_used = ""
        fallback_errors: list[str] = []
        if mode == "full":
            try:
                markdown = ns.chat_messages_to_markdown(
                    messages,
                    title=title,
                    session_id=session_id,
                )
            except Exception as e:
                return _error_response(e)
        else:
            try:
                summarized = _summarize_chat_for_notion(messages, title=title, mode=mode)
            except Exception as e:
                return _error_response(e)
            markdown = summarized["markdown"]
            model_kind = summarized.get("model_kind") or ""
            model_used = summarized.get("model_used") or ""
            fallback_errors = summarized.get("fallback_errors") or []
            markdown = _append_chat_source_footer(
                markdown,
                session_id=session_id,
                mode=mode,
                model_kind=model_kind,
                model_used=model_used,
            )
        try:
            page = ns.create_page_from_markdown(
                _uid(),
                title=title,
                markdown=markdown,
                parent_id=data.get("parent_id") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({
            "status": "saved",
            "save_mode": mode,
            "model_kind": model_kind,
            "model_used": model_used,
            "fallback_errors": fallback_errors,
            **page,
        })

    @app.route("/api/code/notion/import_thought", methods=["POST"])
    def api_code_notion_import_thought():
        uid = _uid()
        data = _json_data()
        thought_id = str(data.get("thought_id") or "").strip()
        meta = {}
        content = str(data.get("content") or "")
        if thought_id:
            try:
                thought = sb.get_thought(thought_id, user_id=uid)
            except Exception as e:
                return _error_response(e)
            if not thought:
                return _error_response(FileNotFoundError("thought not found"))
            meta = thought.get("meta") or {}
            content = thought.get("content") or content
        title = str(data.get("title") or meta.get("title") or thought_id or "Second Brain Note").strip()
        if not content.strip():
            return notion_http.validation_response("content required")
        topics = meta.get("topics") if isinstance(meta.get("topics"), list) else []
        source_lines = [
            "",
            "---",
            "Imported from Second Brain",
            f"- thought_id: {thought_id or meta.get('id') or '-'}",
        ]
        if topics:
            source_lines.append(f"- topics: {', '.join(str(t) for t in topics)}")
        markdown = content.rstrip() + "\n" + "\n".join(source_lines) + "\n"
        try:
            with user_write_lock("notion_import", uid):
                page = ns.create_page_from_markdown(
                    uid,
                    title=title or "Second Brain Note",
                    markdown=markdown,
                    parent_id=data.get("parent_id") or "",
                )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "imported", **page})
