"""
Second Brain / LLM Wiki routes for code_assist_v1.
"""
from __future__ import annotations

from flask import jsonify, request

from code_assist_v1 import second_brain_store as sb
from code_assist_v1.auth import current_user_id
from code_assist_v1.concurrency import user_write_lock
from code_assist_v1.config import MAX_UPLOAD_MB
from code_assist_v1.job_manager import manager as job_manager


def _uid() -> str | None:
    auth_uid = current_user_id()
    if auth_uid:
        return auth_uid
    if request.method == "GET":
        value = (request.args.get("user_id") or "").strip()
        return value or None
    if request.form:
        value = (request.form.get("user_id") or "").strip()
        if value:
            return value
    data = request.get_json(silent=True) or {}
    value = (data.get("user_id") or "").strip()
    return value or None


def _async_requested() -> bool:
    value = (
        request.form.get("async")
        or request.form.get("async_mode")
        or request.args.get("async")
        or request.args.get("async_mode")
        or ""
    )
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _uploaded_files() -> list:
    files = []
    for key in ("files", "file"):
        files.extend(request.files.getlist(key))
    return [f for f in files if f and f.filename]


def _read_upload_payloads() -> list[dict]:
    payloads = []
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    for f in _uploaded_files():
        raw = f.read()
        if len(raw) > max_bytes:
            raise ValueError(f"{f.filename}: file size exceeds limit ({MAX_UPLOAD_MB}MB)")
        payloads.append({"filename": f.filename, "raw": raw})
    return payloads


def register_second_brain_routes(app):
    @app.route("/api/code/knowledge/source/import", methods=["POST"])
    def api_second_brain_source_import():
        if "file" not in request.files:
            return jsonify({"error": "file required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename required"}), 400
        raw = f.read()
        uid = _uid()
        filename = f.filename
        domain = (request.form.get("domain") or "").strip()
        service = (request.form.get("service") or "").strip()
        auto_candidates = request.form.get("auto_candidates", "1") != "0"
        auto_triage = request.form.get("auto_triage", "1") != "0"

        def _run_import(ctx):
            ctx.update(progress=5, message="extracting source")
            ctx.raise_if_cancelled()
            with user_write_lock("second_brain", uid):
                ctx.update(progress=25, message="updating source markdown and graph")
                result = sb.import_source_bytes(
                    filename=filename,
                    raw=raw,
                    user_id=uid,
                    domain=domain,
                    service=service,
                    auto_candidates=auto_candidates,
                    auto_triage=auto_triage,
                    progress_callback=lambda progress, message: ctx.update(progress=progress, message=message),
                )
            ctx.update(progress=95, message="import complete")
            return {"status": "imported", **result}

        if _async_requested():
            job = job_manager.submit(
                user_id=uid or "",
                kind="second_brain.import_source",
                target=_run_import,
                metadata={"filename": filename, "domain": domain, "service": service},
            )
            return jsonify({"status": "queued", "job": job, "job_id": job["job_id"]}), 202
        try:
            with user_write_lock("second_brain", uid):
                result = sb.import_source_bytes(
                    filename=filename,
                    raw=raw,
                    user_id=uid,
                    domain=domain,
                    service=service,
                    auto_candidates=auto_candidates,
                    auto_triage=auto_triage,
                )
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "imported", **result})

    @app.route("/api/code/knowledge/source/import_batch", methods=["POST"])
    def api_second_brain_source_import_batch():
        try:
            uploads = _read_upload_payloads()
        except ValueError as e:
            return jsonify({"error": str(e)}), 413
        if not uploads:
            return jsonify({"error": "files required"}), 400
        uid = _uid()
        domain = (request.form.get("domain") or "").strip()
        service = (request.form.get("service") or "").strip()
        auto_candidates = request.form.get("auto_candidates", "1") != "0"
        auto_triage = request.form.get("auto_triage", "1") != "0"

        def _import_many(ctx=None):
            items = []
            imported = 0
            failed = 0
            graph_edges = 0
            candidate_count = 0
            converted = 0
            assets = 0
            total = max(1, len(uploads))
            with user_write_lock("second_brain", uid):
                for idx, upload in enumerate(uploads):
                    filename = upload["filename"]
                    base_progress = int((idx / total) * 100)

                    def _progress(progress, message):
                        if ctx:
                            mapped = base_progress + int((max(0, min(100, int(progress))) / total))
                            ctx.update(
                                progress=max(1, min(99, mapped)),
                                message=f"{idx + 1}/{total} {filename}: {message}",
                            )

                    try:
                        if ctx:
                            ctx.raise_if_cancelled()
                            ctx.update(progress=base_progress, message=f"{idx + 1}/{total} {filename}: queued")
                        result = sb.import_source_bytes(
                            filename=filename,
                            raw=upload["raw"],
                            user_id=uid,
                            domain=domain,
                            service=service,
                            auto_candidates=auto_candidates,
                            auto_triage=auto_triage,
                            progress_callback=_progress,
                        )
                        imported += 1
                        graph_edges += int(result.get("graph_edges_added") or 0)
                        candidate_count += int(result.get("candidate_count") or 0)
                        converted += 1 if result.get("converted_to_wiki") else 0
                        assets += int(result.get("asset_count") or result.get("image_count") or 0)
                        items.append({"filename": filename, "status": "imported", **result})
                    except Exception as e:
                        failed += 1
                        items.append({"filename": filename, "status": "failed", "error": str(e)})
            return {
                "status": "imported" if failed == 0 else ("partial" if imported else "failed"),
                "count": len(uploads),
                "imported": imported,
                "failed": failed,
                "items": items,
                "graph_edges_added": graph_edges,
                "candidate_count": candidate_count,
                "pending_candidate_count": candidate_count,
                "converted_count": converted,
                "asset_count": assets,
                "auto_triage": {"pending": candidate_count, "failed": failed},
            }

        if _async_requested():
            job = job_manager.submit(
                user_id=uid or "",
                kind="second_brain.import_source_batch",
                target=_import_many,
                metadata={"filenames": [u["filename"] for u in uploads], "domain": domain, "service": service},
            )
            return jsonify({"status": "queued", "job": job, "job_id": job["job_id"]}), 202

        result = _import_many()
        code = 400 if result["status"] == "failed" else 200
        return jsonify(result), code

    @app.route("/api/code/knowledge/chat/promote", methods=["POST"])
    def api_second_brain_chat_promote():
        data = request.get_json(force=True, silent=True) or {}
        messages = data.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return jsonify({"error": "messages required"}), 400
        uid = _uid()
        try:
            with user_write_lock("second_brain", uid):
                result = sb.promote_conversation_source(
                    messages=messages,
                    user_id=uid,
                    session_id=(data.get("session_id") or "").strip(),
                    title=(data.get("title") or "").strip(),
                    domain=(data.get("domain") or "").strip(),
                    service=(data.get("service") or "").strip(),
                )
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "promoted", **result})

    @app.route("/api/code/knowledge/inbox/list")
    def api_second_brain_inbox_list():
        status = (request.args.get("status") or "").strip() or None
        q = request.args.get("q", "")
        items = sb.list_candidates(user_id=_uid(), status=status, q=q)
        return jsonify({
            "items": items,
            "count": len(items),
            "counts": sb.candidate_counts(user_id=_uid()),
            "user_id": _uid(),
        })

    @app.route("/api/code/knowledge/inbox/<path:candidate_id>")
    def api_second_brain_inbox_get(candidate_id):
        item = sb.get_candidate(candidate_id, user_id=_uid())
        if not item:
            return jsonify({"error": "candidate not found"}), 404
        return jsonify(item)

    @app.route("/api/code/knowledge/inbox/review", methods=["POST"])
    def api_second_brain_inbox_review():
        data = request.get_json(force=True, silent=True) or {}
        candidate_id = data.get("id") or data.get("candidate_id") or ""
        if not candidate_id:
            return jsonify({"error": "id required"}), 400
        uid = _uid()
        try:
            with user_write_lock("second_brain", uid):
                result = sb.curation_review_candidate(
                    candidate_id=candidate_id,
                    action=data.get("action") or "review",
                    target_id=data.get("target_id") or data.get("merge_target") or "",
                    user_id=uid,
                    persist=bool(data.get("persist", True)),
                )
        except FileNotFoundError:
            return jsonify({"error": "candidate or target thought not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "ok", **result})

    @app.route("/api/code/knowledge/title/generate", methods=["POST"])
    def api_second_brain_title_generate():
        data = request.get_json(force=True, silent=True) or {}
        uid = _uid()
        try:
            result = sb.curation_generate_title(
                candidate_id=data.get("candidate_id") or data.get("id") or "",
                thought_id=data.get("thought_id") or "",
                user_id=uid,
            )
        except FileNotFoundError:
            return jsonify({"error": "candidate or thought not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "ok", "title": result})

    @app.route("/api/code/knowledge/inbox/action", methods=["POST"])
    def api_second_brain_inbox_action():
        data = request.get_json(force=True, silent=True) or {}
        candidate_id = data.get("id") or ""
        action = data.get("action") or ""
        if not candidate_id or not action:
            return jsonify({"error": "id and action required"}), 400
        try:
            uid = _uid()
            with user_write_lock("second_brain", uid):
                item = sb.apply_candidate_action(
                    candidate_id=candidate_id,
                    action=action,
                    user_id=uid,
                    updates=data.get("updates") or {},
                    merge_target=data.get("merge_target") or None,
                    skip_reason=data.get("skip_reason") or "",
                    use_llm=bool(data.get("use_llm", False)),
                )
        except FileExistsError:
            return jsonify({"error": "thought already exists; use Merge or refine the title"}), 409
        except FileNotFoundError as e:
            return jsonify({"error": f"not found: {e}"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "ok", "candidate": item})

    @app.route("/api/code/knowledge/inbox/batch_action", methods=["POST"])
    def api_second_brain_inbox_batch_action():
        data = request.get_json(force=True, silent=True) or {}
        ids = data.get("ids") or []
        action = data.get("action") or ""
        if isinstance(ids, str):
            ids = [ids]
        ids = [str(x).strip() for x in ids if str(x).strip()]
        if not ids or not action:
            return jsonify({"error": "ids and action required"}), 400
        if action not in {"approve", "skip", "delete", "restore", "purge"}:
            return jsonify({"error": f"unsupported batch action: {action}"}), 400

        results = []
        errors = []
        uid = _uid()
        with user_write_lock("second_brain", uid):
            for candidate_id in ids:
                try:
                    item = sb.apply_candidate_action(
                        candidate_id=candidate_id,
                        action=action,
                        user_id=uid,
                        updates={},
                        merge_target=None,
                        skip_reason=data.get("skip_reason") or "",
                    )
                    results.append({"id": candidate_id, "status": "ok", "candidate": item})
                except FileExistsError:
                    errors.append({
                        "id": candidate_id,
                        "error": "thought already exists; use Merge or refine the title",
                    })
                except FileNotFoundError as e:
                    errors.append({"id": candidate_id, "error": f"not found: {e}"})
                except Exception as e:
                    errors.append({"id": candidate_id, "error": str(e)})
        return jsonify({
            "status": "ok" if not errors else "partial",
            "action": action,
            "requested": len(ids),
            "succeeded": len(results),
            "failed": len(errors),
            "results": results,
            "errors": errors,
        })

    @app.route("/api/code/knowledge/thought/list")
    def api_second_brain_thought_list():
        items = sb.list_thoughts(user_id=_uid())
        cleaned = []
        for item in items:
            cleaned.append({k: v for k, v in item.items() if not k.startswith("_")})
        return jsonify({"items": cleaned, "count": len(cleaned), "user_id": _uid()})

    @app.route("/api/code/knowledge/graph")
    def api_second_brain_graph():
        depth_raw = (request.args.get("depth") or "").strip()
        depth = None
        if depth_raw not in ("", "all"):
            try:
                depth = max(0, min(2, int(depth_raw)))
            except ValueError:
                depth = None
        data = sb.graph_data(
            user_id=_uid(),
            q=request.args.get("q", ""),
            focus=request.args.get("focus", ""),
            depth=depth,
            memory_type=request.args.get("memory_type", ""),
            service=request.args.get("service", ""),
            topic=request.args.get("topic", ""),
            edge_type=request.args.get("edge_type", ""),
            include_legacy=(request.args.get("include_legacy", "1") != "0"),
            view=(request.args.get("view") or "wiki").strip() or "wiki",
        )
        return jsonify(data)

    @app.route("/api/code/knowledge/thought/<path:thought_id>")
    def api_second_brain_thought(thought_id):
        thought = sb.get_thought(thought_id, user_id=_uid())
        if not thought:
            return jsonify({"error": "thought not found"}), 404
        return jsonify(thought)

    @app.route("/api/code/knowledge/system_doc/<name>")
    def api_second_brain_system_doc(name):
        doc = sb.get_system_doc(name, user_id=_uid())
        if not doc:
            return jsonify({"error": "system document not found"}), 404
        return jsonify(doc)

    @app.route("/api/code/knowledge/thought/create", methods=["POST"])
    def api_second_brain_thought_create():
        data = request.get_json(force=True, silent=True) or {}
        content = data.get("content") or data.get("body") or ""
        meta = {
            "id": data.get("id") or data.get("title"),
            "title": data.get("title") or data.get("id") or "Untitled thought",
            "memory_type": data.get("memory_type") or "semantic",
            "domain": data.get("domain") or "",
            "service": data.get("service") or "",
            "topics": data.get("topics") or [],
            "sources": data.get("sources") or [],
            "meaning_version": data.get("meaning_version") or 1,
        }
        try:
            uid = _uid()
            with user_write_lock("second_brain", uid):
                saved = sb.save_thought(meta, content, user_id=uid, overwrite=False)
        except FileExistsError:
            return jsonify({"error": "thought id already exists"}), 409
        return jsonify({"status": "created", **saved})

    @app.route("/api/code/knowledge/thought/update", methods=["POST"])
    def api_second_brain_thought_update():
        data = request.get_json(force=True, silent=True) or {}
        thought_id = data.get("id") or ""
        if not thought_id:
            return jsonify({"error": "id required"}), 400
        uid = _uid()
        with user_write_lock("second_brain", uid):
            old = sb.get_thought(thought_id, user_id=uid)
            if not old:
                return jsonify({"error": "thought not found"}), 404
            old_meta = old["meta"]
            meta = {
                "id": thought_id,
                "title": data.get("title") or old_meta.get("title") or thought_id,
                "memory_type": data.get("memory_type") or old_meta.get("memory_type") or "semantic",
                "domain": data.get("domain") if data.get("domain") is not None else old_meta.get("domain", ""),
                "service": data.get("service") if data.get("service") is not None else old_meta.get("service", ""),
                "topics": data.get("topics") if data.get("topics") is not None else old_meta.get("topics", []),
                "sources": data.get("sources") if data.get("sources") is not None else old_meta.get("sources", []),
                "status": data.get("status") if data.get("status") is not None else old_meta.get("status", "active"),
                "superseded_by": old_meta.get("superseded_by", ""),
                "deprecated_at": old_meta.get("deprecated_at", ""),
                "history": old_meta.get("history", []),
                "meaning_version": data.get("meaning_version") or old_meta.get("meaning_version", 1),
            }
            content = data.get("content") if data.get("content") is not None else old["content"]
            saved = sb.save_thought(meta, content, user_id=uid, overwrite=True)
        return jsonify({"status": "updated", **saved})

    @app.route("/api/code/knowledge/thought/delete", methods=["POST"])
    def api_second_brain_thought_delete():
        data = request.get_json(force=True, silent=True) or {}
        thought_id = data.get("id") or ""
        if not thought_id:
            return jsonify({"error": "id required"}), 400
        uid = _uid()
        with user_write_lock("second_brain", uid):
            deleted = sb.delete_thought(thought_id, user_id=uid)
        if not deleted:
            return jsonify({"error": "thought not found"}), 404
        return jsonify({"status": "deleted", "id": thought_id})

    @app.route("/api/code/knowledge/edge/create", methods=["POST"])
    def api_second_brain_edge_create():
        data = request.get_json(force=True, silent=True) or {}
        source = data.get("source") or ""
        target = data.get("target") or ""
        if not source or not target:
            return jsonify({"error": "source and target required"}), 400
        uid = _uid()
        with user_write_lock("second_brain", uid):
            edge = sb.add_edge(
                source=source,
                target=target,
                edge_type=data.get("edge_type") or "related",
                user_id=uid,
                weight=float(data.get("weight") or 1.0),
                metadata=data.get("metadata") or None,
            )
        return jsonify({"status": "created", "edge": edge})

    @app.route("/api/code/knowledge/search_thoughts", methods=["POST"])
    def api_second_brain_search():
        data = request.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "query required"}), 400
        results = sb.search_thoughts(
            query=query,
            user_id=_uid(),
            max_results=int(data.get("max_results") or 8),
            include_legacy=bool(data.get("include_legacy", True)),
        )
        return jsonify({"query": query, "results": results, "count": len(results)})

    @app.route("/api/code/knowledge/convert", methods=["POST"])
    def api_second_brain_convert():
        data = request.get_json(force=True, silent=True) or {}
        uid = _uid()
        with user_write_lock("second_brain", uid):
            result = sb.convert_legacy_docs(
                user_id=uid,
                filename=(data.get("filename") or "").strip() or None,
                max_sections=int(data.get("max_sections") or 40),
            )
        return jsonify({"status": "converted", **result})
