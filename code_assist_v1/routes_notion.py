"""
Routes for the Notion-like workspace app on port 10012.
"""
from __future__ import annotations

import html as html_lib
import csv
import io
import json
import os
import re
import time
from urllib.parse import quote_plus

from flask import Response, current_app, jsonify, redirect, request, send_file

from code_assist_v1 import notion_http
from code_assist_v1 import notion_publication
from code_assist_v1 import notion_store as ns
from code_assist_v1 import second_brain_store as sb
from code_assist_v1.auth import current_user_id, list_public_users
from code_assist_v1.concurrency import user_write_lock
from code_assist_v1.config import USER_DATA_DIR
from code_assist_v1.internal_llm import (
    codex_internal_enabled,
    default_provider_order,
    run_internal_text,
)


_HYNIX_NOTION_UNAVAILABLE_UNTIL = 0.0
_HYNIX_NOTION_FAILURE_TTL_S = 60.0


def _uid() -> str | None:
    return current_user_id()


def _json_data() -> dict:
    return notion_http.json_object_request()


def _error_response(exc: Exception):
    return notion_http.error_response(exc)


def _session_list(user_id: str | None, limit: int = 30) -> list[dict]:
    uid = user_id or ""
    root = os.path.join(USER_DATA_DIR, uid, "sessions")
    if not os.path.isdir(root):
        return []
    items: list[dict] = []
    for name in os.listdir(root):
        if not name.endswith(".json"):
            continue
        path = os.path.join(root, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items.append({
                "session_id": data.get("session_id") or name[:-5],
                "title": data.get("title") or data.get("first_message") or name[:-5],
                "first_message": data.get("first_message") or "",
                "message_count": data.get("message_count") or len(data.get("messages") or []),
                "timestamp": data.get("timestamp") or os.path.getmtime(path),
            })
        except Exception:
            continue
    items.sort(key=lambda item: float(item.get("timestamp") or 0), reverse=True)
    return items[: max(1, min(100, int(limit or 30)))]


def _text_score(query: str, text: str) -> int:
    q = str(query or "").strip().lower()
    if not q:
        return 0
    haystack = str(text or "").lower()
    words = _search_terms(q)
    return sum(haystack.count(word) for word in words[:12])


def _truthy_arg(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _search_terms(query: str) -> list[str]:
    q = str(query or "").strip().lower()
    if not q:
        return []
    phrases = [item.strip() for item in re.findall(r'"([^"]+)"', q) if item.strip()]
    remainder = re.sub(r'"[^"]+"', " ", q)
    words = [term.strip("'\"") for term in re.split(r"\s+", remainder) if len(term.strip("'\"")) >= 2]
    return (phrases + words)[:12]


def _search_phrases(query: str) -> list[str]:
    return [item.strip().lower() for item in re.findall(r'"([^"]+)"', str(query or "")) if item.strip()][:8]


def _search_snippet(query: str, text: str, max_chars: int = 240) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    terms = _search_terms(query)
    first = -1
    low = value.lower()
    for term in terms:
        pos = low.find(term)
        if pos >= 0 and (first < 0 or pos < first):
            first = pos
    if first < 0:
        return value[:max_chars].strip()
    start = max(0, first - 80)
    end = min(len(value), start + max_chars)
    snippet = value[start:end].strip()
    if start > 0:
        snippet = "... " + snippet
    if end < len(value):
        snippet += " ..."
    return snippet


def _esc(value: object) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def _text_html(value: object) -> str:
    return _esc(value).replace("\n", "<br>")


def _public_link_response(link: dict) -> dict:
    item = dict(link or {})
    token = str(item.get("token") or "")
    if item.get("enabled") and token:
        item["url"] = f"{request.host_url.rstrip('/')}/public/notion/{token}"
    else:
        item["url"] = ""
    return item


def _public_form_control(property_item: dict, required: bool) -> str:
    property_id = str(property_item.get("id") or "")
    property_type = str(property_item.get("type") or "text")
    name = f"f_{property_id}"
    common = f"name='{_esc(name)}' {'required' if required else ''}"
    if property_type == "checkbox":
        return f"<input type='checkbox' {common} value='1'>"
    if property_type == "number":
        return f"<input type='number' {common}>"
    if property_type == "date":
        return f"<input type='date' {common}>"
    if property_type == "email":
        return f"<input type='email' {common} placeholder='name@example.com'>"
    if property_type == "url":
        return f"<input type='url' {common} placeholder='https://'>"
    if property_type == "phone":
        return f"<input type='tel' {common} placeholder='전화번호'>"
    if property_type == "place":
        return f"<input {common} placeholder='주소 또는 장소'>"
    if property_type in {"select", "status"}:
        defaults = ["시작 전", "진행 중", "완료"] if property_type == "status" else []
        options = list(dict.fromkeys(str(item) for item in [*(property_item.get("options") or []), *defaults] if str(item).strip()))
        options_html = "".join(f"<option value='{_esc(item)}'>{_esc(item)}</option>" for item in options)
        return f"<select {common}><option value=''>선택</option>{options_html}</select>"
    if property_type == "text":
        return f"<textarea {common} rows='3'></textarea>"
    placeholder = "쉼표로 여러 값을 구분" if property_type == "multi_select" else str(property_item.get("name") or "응답")
    return f"<input {common} placeholder='{_esc(placeholder)}'>"


def _public_form_html(token: str, block: dict, props: dict) -> str:
    properties = props.get("properties") if isinstance(props.get("properties"), list) else []
    visibility = props.get("property_visibility") if isinstance(props.get("property_visibility"), dict) else {}
    public_types = {"title", "text", "number", "select", "status", "multi_select", "date", "url", "email", "phone", "checkbox", "place"}
    fields = [
        item for item in properties
        if isinstance(item, dict)
        and item.get("id")
        and item.get("type") in public_types
        and (item.get("type") == "title" or visibility.get(str(item.get("id")), not item.get("hidden")) is not False)
    ]
    required = {str(item) for item in (props.get("form_required_properties") or []) if str(item)}
    fields_html = "".join(
        f"<label class='public-form-field'><span>{_esc(item.get('name') or item.get('id'))}{'<b>*</b>' if str(item.get('id')) in required else ''}</span>{_public_form_control(item, str(item.get('id')) in required)}</label>"
        for item in fields
    )
    title = props.get("form_title") or block.get("content") or "새 폼"
    description = props.get("form_description") or ""
    submit_label = props.get("form_submit_label") or "제출"
    block_id = str(block.get("id") or "")
    if not block_id or not fields:
        return "<p class='public-form-empty'>제출 가능한 폼 필드가 없습니다.</p>"
    action = f"/public/notion/{token}/forms/{block_id}" if token else "#"
    return (
        f"<form class='public-form' method='post' action='{_esc(action)}'>"
        f"<header><h2>{_esc(title)}</h2>{f'<p>{_text_html(description)}</p>' if description else ''}</header>"
        "<label class='public-form-honeypot' aria-hidden='true'>웹사이트<input name='_website' tabindex='-1' autocomplete='off'></label>"
        f"{fields_html}<button type='submit' {'disabled' if not token else ''}>{_esc(submit_label)}</button></form>"
    )


def _public_database_value(value: object) -> str:
    if value is True:
        return "✓"
    if value is False or value is None:
        return ""
    if isinstance(value, dict):
        start = str(value.get("start") or "")
        end = str(value.get("end") or "")
        return f"{start}{f' → {end}' if end and end != start else ''}"
    if isinstance(value, list):
        return ", ".join(_public_database_value(item.get("name") if isinstance(item, dict) else item) for item in value)
    return str(value)


def _public_database_html(view_type: str, content: object, props: dict) -> str:
    rows = [item for item in (props.get("rows") if isinstance(props.get("rows"), list) else []) if isinstance(item, dict) and not item.get("archived")][:100]
    properties = [item for item in (props.get("properties") if isinstance(props.get("properties"), list) else []) if isinstance(item, dict) and item.get("id")]
    visibility = props.get("property_visibility") if isinstance(props.get("property_visibility"), dict) else {}
    visible = [item for item in properties[:50] if item.get("type") == "title" or visibility.get(str(item.get("id")), not item.get("hidden")) is not False]
    if not visible:
        visible = [{"id": "title", "name": "Name", "type": "title"}, {"id": "date", "name": "Date", "type": "date"}]
    title_field = next((str(item["id"]) for item in properties if item.get("type") == "title"), "title")
    status_field = str(props.get("board_group_property") or next((item["id"] for item in properties if item.get("type") in {"status", "select"}), "status"))
    date_field = str(props.get("calendar_date_property") or props.get("timeline_date_property") or props.get("feed_date_property") or next((item["id"] for item in properties if item.get("type") == "date"), "date"))
    place_field = str(props.get("map_place_property") or next((item["id"] for item in properties if item.get("type") == "place"), "place"))
    title = _text_html(content or view_type.title())
    if view_type == "board":
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(_public_database_value(row.get(status_field)) or "상태 없음", []).append(row)
        columns = "".join(
            f"<section><h4>{_esc(label)} <small>{len(items)}</small></h4>{''.join(f'<div class=\"public-db-card\">{_text_html(item.get(title_field) or item.get("title") or "Untitled")}</div>' for item in items)}</section>"
            for label, items in groups.items()
        )
        return f"<h3>{title}</h3><div class='public-board'>{columns}</div>"
    if view_type in {"calendar", "timeline"}:
        items = sorted(rows, key=lambda row: _public_database_value(row.get(date_field)))
        body = "".join(f"<li><time>{_text_html(_public_database_value(row.get(date_field)))}</time><span>{_text_html(row.get(title_field) or row.get('title') or 'Untitled')}</span></li>" for row in items)
        return f"<h3>{title}</h3><ol class='public-schedule'>{body}</ol>"
    if view_type == "chart":
        group_field = str(props.get("chart_group_property") or status_field)
        counts: dict[str, int] = {}
        for row in rows:
            key = _public_database_value(row.get(group_field)) or "비어 있음"
            counts[key] = counts.get(key, 0) + 1
        maximum = max([1, *counts.values()])
        body = "".join(f"<div><span>{_esc(label)}</span><i style='--public-chart:{count / maximum * 100}%'></i><strong>{count}</strong></div>" for label, count in counts.items())
        return f"<h3>{title}</h3><div class='public-chart'>{body}</div>"
    if view_type == "dashboard":
        recent = "".join(f"<li>{_text_html(row.get(title_field) or row.get('title') or 'Untitled')}</li>" for row in rows[:8])
        return f"<h3>{title}</h3><div class='public-dashboard'><div><strong>{len(rows)}</strong><span>페이지</span></div><section><h4>최근 항목</h4><ol>{recent}</ol></section></div>"
    if view_type == "feed":
        items = sorted(rows, key=lambda row: _public_database_value(row.get(date_field)), reverse=True)
        body = "".join(f"<article><strong>{_text_html(row.get(title_field) or row.get('title') or 'Untitled')}</strong><time>{_text_html(_public_database_value(row.get(date_field)) or '날짜 없음')}</time></article>" for row in items)
        return f"<h3>{title}</h3><div class='public-feed'>{body}</div>"
    if view_type == "map":
        body = "".join(
            f"<li><strong>{_text_html(row.get(title_field) or row.get('title') or 'Untitled')}</strong><span>{_text_html(_public_database_value(row.get(place_field)))}</span><a href='https://www.openstreetmap.org/search?query={quote_plus(_public_database_value(row.get(place_field)))}' rel='noopener noreferrer'>지도에서 열기</a></li>"
            for row in rows if _public_database_value(row.get(place_field))
        )
        return f"<h3>{title}</h3><ol class='public-map'>{body}</ol>"
    if view_type in {"list", "gallery"}:
        cards = "".join(f"<article class='public-db-card'><strong>{_text_html(row.get(title_field) or row.get('title') or 'Untitled')}</strong><small>{_text_html(_public_database_value(row.get(status_field)) or _public_database_value(row.get(date_field)))}</small></article>" for row in rows)
        return f"<h3>{title}</h3><div class='public-{view_type}'>{cards}</div>"
    headers = "".join(f"<th>{_text_html(item.get('name') or item.get('id'))}</th>" for item in visible)
    body = "".join("<tr>" + "".join(f"<td>{_text_html(_public_database_value(row.get(item.get('id'))))}</td>" for item in visible) + "</tr>" for row in rows)
    return f"<h3>{title}</h3><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>"
def _public_block_html(token: str, block: dict, files_by_id: dict[str, dict]) -> str:
    btype = str(block.get("type") or "paragraph")
    content = block.get("content") or ""
    props = block.get("props") if isinstance(block.get("props"), dict) else {}
    database_types = {"table", "board", "calendar", "list", "gallery", "timeline", "chart", "dashboard", "form", "feed", "map"}
    view_type = str(props.get("view") or btype) if btype in database_types else btype
    if view_type == "form":
        return _public_form_html(token, block, props)
    if view_type in database_types:
        return _public_database_html(view_type, content, props)
    if btype == "heading1":
        return f"<h1>{_text_html(content)}</h1>"
    if btype == "heading2":
        return f"<h2>{_text_html(content)}</h2>"
    if btype == "heading3":
        return f"<h3>{_text_html(content)}</h3>"
    if btype in {"toggle_heading1", "toggle_heading2", "toggle_heading3"}:
        level = int(btype[-1])
        opened = " open" if props.get("open", True) else ""
        return f"<details class=\"toggle-heading toggle-heading-{level}\"{opened}><summary><span>{_text_html(content)}</span></summary></details>"
    if btype == "todo":
        mark = "checked" if props.get("checked") else ""
        return f"<label class='todo'><input type='checkbox' disabled {mark}> <span>{_text_html(content)}</span></label>"
    if btype == "toggle":
        opened = " open" if props.get("open", True) else ""
        return f"<details{opened}><summary>{_text_html(content)}</summary></details>"
    if btype == "callout":
        return f"<aside class='callout'><span>{_esc(props.get('icon') or '💡')}</span><div>{_text_html(content)}</div></aside>"
    if btype == "quote":
        return f"<blockquote>{_text_html(content)}</blockquote>"
    if btype == "code":
        return f"<pre><code>{_esc(content)}</code></pre>"
    if btype == "equation":
        return f"<div class='equation'>{_text_html(content)}</div>"
    if btype == "bullet":
        return f"<ul><li>{_text_html(content)}</li></ul>"
    if btype == "numbered":
        return f"<ol><li>{_text_html(content)}</li></ol>"
    if btype == "divider":
        return "<hr>"
    if btype in {"image", "file", "video", "audio", "pdf"}:
        file_id = str(props.get("file_id") or "")
        file_item = files_by_id.get(file_id) if file_id else None
        label = content or (file_item or {}).get("original_name") or "attachment"
        if not file_id:
            return f"<p>{_text_html(label)}</p>"
        href = f"/public/notion/{_esc(token)}/files/{_esc(file_id)}" if token else f"/api/notion/files/{_esc(file_id)}"
        if btype == "image":
            return f"<figure><img src='{href}' alt='{_esc(label)}'><figcaption>{_text_html(label)}</figcaption></figure>"
        if btype == "video":
            return f"<figure><video src='{href}' controls></video><figcaption>{_text_html(label)}</figcaption></figure>"
        if btype == "audio":
            return f"<figure><audio src='{href}' controls></audio><figcaption>{_text_html(label)}</figcaption></figure>"
        if btype == "pdf":
            return f"<p class='file-link'><a href='{href}'>{_text_html(label)} (PDF)</a></p>"
        return f"<p class='file-link'><a href='{href}'>{_text_html(label)}</a></p>"
    if btype == "bookmark":
        url = str(props.get("url") or props.get("link_url") or "")
        safe_url = _esc(url) if re.match(r"^https?://", url, flags=re.I) else ""
        return f"<p class='bookmark'><a href='{safe_url}' rel='noopener noreferrer'>{_text_html(content or url)}</a></p>" if safe_url else f"<p>{_text_html(content)}</p>"
    if btype == "embed":
        url = str(props.get("url") or "")
        safe_url = _esc(url) if re.match(r"^https?://", url, flags=re.I) else ""
        return f"<p class='embed-link'><a href='{safe_url}' rel='noopener noreferrer'>{_text_html(content or url)}</a></p>" if safe_url else f"<p>{_text_html(content)}</p>"
    if btype == "table_of_contents":
        return "<nav class='toc-note'>Table of contents</nav>"
    if btype == "breadcrumb":
        return "<nav class='toc-note'>Page breadcrumb</nav>"
    if btype == "link_to_page":
        return f"<p class='child-page'>↗ {_text_html(content or props.get('title') or 'Linked page')}</p>"
    if btype == "button":
        return f"<p><span class='public-button'>⚡ {_text_html(props.get('label') or content or 'Button')}</span></p>"
    if btype == "synced_block":
        return f"<aside class='callout'><span>↻</span><div>{_text_html(content)}</div></aside>"
    if btype == "tabs":
        return ""
    if btype == "simple_table":
        rows = props.get("rows") if isinstance(props.get("rows"), list) else []
        body = "".join(
            "<tr>" + "".join(f"<td>{_text_html(cell)}</td>" for cell in row[:50]) + "</tr>"
            for row in rows[:200] if isinstance(row, list)
        )
        return f"<table><tbody>{body}</tbody></table>"
    if btype == "child_page":
        label = content or props.get("title") or "Untitled child page"
        return f"<p class='child-page'>▣ {_text_html(label)}</p>"
    return f"<p>{_text_html(content)}</p>" if str(content).strip() else "<p><br></p>"


def _public_blocks_html(token: str, blocks: list[dict], files_by_id: dict[str, dict]) -> str:
    output: list[str] = []
    tab_controllers = {
        str(block.get("id") or ""): block
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "tabs" and block.get("id")
    }
    index = 0
    while index < len(blocks):
        block = blocks[index] if isinstance(blocks[index], dict) else {}
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        if str(props.get("tab_group") or "") in tab_controllers:
            index += 1
            continue
        if block.get("type") == "tabs":
            controller_id = str(block.get("id") or "")
            tabs = [
                {"id": str(item.get("id") or ""), "name": str(item.get("name") or f"탭 {tab_index + 1}")}
                for tab_index, item in enumerate(props.get("tabs") if isinstance(props.get("tabs"), list) else [])
                if isinstance(item, dict) and item.get("id")
            ][:20]
            if not tabs:
                tabs = [{"id": "default", "name": "탭 1"}]
            active_id = str(props.get("active_tab_id") or tabs[0]["id"])
            if not any(item["id"] == active_id for item in tabs):
                active_id = tabs[0]["id"]
            labels: list[str] = []
            panels: list[str] = []
            for tab_index, tab in enumerate(tabs):
                panel_id = f"public-tab-{index}-{tab_index}"
                is_active = tab["id"] == active_id
                labels.append(
                    f"<button type='button' role='tab' aria-selected='{'true' if is_active else 'false'}' "
                    f"class='{'active' if is_active else ''}' data-public-tab-target='{panel_id}'>{_esc(tab['name'])}</button>"
                )
                members = [
                    candidate for candidate in blocks
                    if isinstance(candidate, dict)
                    and isinstance(candidate.get("props"), dict)
                    and str(candidate["props"].get("tab_group") or "") == controller_id
                    and str(candidate["props"].get("tab_id") or "") == tab["id"]
                ]
                panels.append(
                    f"<section id='{panel_id}' class='public-tab-panel' {'hidden' if not is_active else ''}>"
                    f"{_public_blocks_html(token, members, files_by_id)}</section>"
                )
            output.append(
                f"<div class='public-tabs'><div class='public-tab-list' role='tablist'>{''.join(labels)}</div>"
                f"{''.join(panels)}</div>"
            )
            index += 1
            continue
        group_id = str(props.get("layout_group") or "")
        if not group_id:
            output.append(_public_block_html(token, block, files_by_id))
            index += 1
            continue
        count = max(2, min(3, int(props.get("layout_columns") or 2)))
        columns: list[list[str]] = [[] for _ in range(count)]
        while index < len(blocks):
            member = blocks[index] if isinstance(blocks[index], dict) else {}
            member_props = member.get("props") if isinstance(member.get("props"), dict) else {}
            if str(member_props.get("layout_group") or "") != group_id:
                break
            column = max(0, min(count - 1, int(member_props.get("layout_column") or 0)))
            columns[column].append(_public_block_html(token, member, files_by_id))
            index += 1
        column_html = "".join(f'<div class="public-column">{"".join(items)}</div>' for items in columns)
        output.append(f'<div class="public-columns" style="--public-columns:{count}">{column_html}</div>')
    return "\n".join(output)


def _public_page_html(token: str, data: dict) -> str:
    page = data.get("page") or {}
    files_by_id = {item.get("id"): item for item in data.get("files") or [] if item.get("id")}
    blocks = _public_blocks_html(token, data.get("blocks") or [], files_by_id)
    title = page.get("title") or "Untitled"
    icon = page.get("icon") or "▣"
    updated_at = page.get("updated_at") or 0
    form_success_message = next((
        str((item.get("props") or {}).get("form_success_message") or "응답이 제출되었습니다.")
        for item in (data.get("blocks") or [])
        if isinstance(item, dict) and isinstance(item.get("props"), dict)
        and (item.get("type") == "form" or (item.get("props") or {}).get("view") == "form")
    ), "응답이 제출되었습니다.")
    submitted_notice = f"<div class='public-form-success'>{_text_html(form_success_message[:200])}</div>" if token and request.args.get("submitted") == "1" else ""
    allow_duplicate = bool((data.get("public_link") or {}).get("allow_duplicate", True))
    public_header = (
        f"<header class='public-site-header'><span>{_esc(title)}</span>"
        f"<form id='publicDuplicateForm' method='post' action='/public/notion/{_esc(token)}/duplicate'><button type='submit'>복제</button></form></header>"
        if token and allow_duplicate else ""
    )
    footer_note = (
        f"읽기 전용 공개 페이지 · owner={_esc(page.get('owner_user_id') or '')} · updated={_esc(updated_at)}"
        if token else f"Notion Workspace HTML 내보내기 · updated={_esc(updated_at)}"
    )
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_esc(title)}</title>
  <style>
    :root {{ color-scheme: light; --text:#37352f; --muted:#787774; --border:#e9e9e7; --bg:#fff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif; font-size:16px; line-height:1.6; }}
    main {{ width:min(760px, calc(100% - 48px)); margin:72px auto 120px; }}
    .public-site-header {{ position:sticky; top:0; z-index:5; display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:46px; padding:6px 16px; border-bottom:1px solid var(--border); background:rgba(255,255,255,.94); backdrop-filter:blur(12px); }}
    .public-site-header > span {{ overflow:hidden; color:var(--muted); font-size:14px; text-overflow:ellipsis; white-space:nowrap; }}
    .public-site-header form {{ margin:0; }}
    .public-site-header button {{ min-height:32px; padding:0 13px; border:1px solid #d8d8d5; border-radius:5px; background:#fff; color:var(--text); font-weight:600; cursor:pointer; }}
    .public-site-header button:hover {{ background:#f7f6f3; }}
    .page-icon {{ font-size:42px; margin-bottom:14px; }}
    h1.title {{ margin:0 0 28px; font-size:42px; line-height:1.18; letter-spacing:0; }}
    h1:not(.title) {{ margin:32px 0 8px; font-size:30px; line-height:1.25; }}
    h2 {{ margin:28px 0 8px; font-size:24px; line-height:1.3; }}
    h3 {{ margin:22px 0 8px; font-size:19px; line-height:1.35; }}
    .toggle-heading {{ margin:16px 0; }}
    .toggle-heading summary {{ cursor:pointer; }}
    .toggle-heading-1 summary span {{ font-size:30px; font-weight:700; line-height:1.25; }}
    .toggle-heading-2 summary span {{ font-size:24px; font-weight:650; line-height:1.3; }}
    .toggle-heading-3 summary span {{ font-size:19px; font-weight:650; line-height:1.35; }}
    p, ul, ol, blockquote, pre, table, figure {{ margin:8px 0; }}
    blockquote {{ padding-left:14px; border-left:3px solid var(--text); color:#4f4d48; }}
    pre {{ overflow:auto; padding:14px; border-radius:4px; background:#f7f6f3; }}
    code {{ font-family:"SFMono-Regular","Cascadia Code","Consolas",monospace; font-size:14px; }}
    figure {{ display:inline-block; max-width:100%; }}
    img {{ display:block; max-width:100%; max-height:70vh; border-radius:4px; background:#f7f6f3; }}
    figcaption {{ margin-top:6px; color:var(--muted); font-size:13px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ padding:8px 10px; border:1px solid var(--border); text-align:left; vertical-align:top; }}
    th {{ background:#fbfbfa; color:var(--muted); font-weight:600; }}
    a {{ color:#2f80ed; text-underline-offset:2px; }}
    hr {{ border:0; border-top:1px solid var(--border); margin:22px 0; }}
    .todo {{ display:flex; align-items:flex-start; gap:8px; margin:8px 0; }}
    .public-columns {{ display:grid; grid-template-columns:repeat(var(--public-columns, 2), minmax(0, 1fr)); gap:28px; align-items:start; }}
    .public-column {{ min-width:0; }}
    .public-tab-list {{ display:flex; gap:2px; overflow-x:auto; border-bottom:1px solid var(--border); }}
    .public-tab-list button {{ padding:8px 11px; border:0; border-bottom:2px solid transparent; background:transparent; color:var(--muted); cursor:pointer; }}
    .public-tab-list button.active {{ border-bottom-color:var(--text); color:var(--text); font-weight:600; }}
    .public-tab-panel {{ padding:10px 2px 4px; }}
    .child-page, .file-link {{ color:#55534e; }}
    .public-note {{ margin-top:44px; padding-top:16px; border-top:1px solid var(--border); color:var(--muted); font-size:12px; }}
    .public-form {{ margin:20px 0; padding:28px; border:1px solid var(--border); border-radius:10px; box-shadow:0 3px 14px rgba(15,15,15,.04); }}
    .public-form header {{ margin-bottom:20px; }}
    .public-form header h2 {{ margin:0 0 6px; }}
    .public-form header p {{ margin:0; color:var(--muted); }}
    .public-form-field {{ display:grid; gap:6px; margin:15px 0; font-size:13px; font-weight:600; }}
    .public-form-field b {{ margin-left:3px; color:#e03e3e; }}
    .public-form-field input:not([type=checkbox]), .public-form-field select, .public-form-field textarea {{ width:100%; min-height:38px; padding:7px 9px; border:1px solid #c8c8c5; border-radius:5px; background:#fff; color:var(--text); font:inherit; font-weight:400; }}
    .public-form-field textarea {{ resize:vertical; }}
    .public-form button {{ min-height:38px; padding:0 18px; border:0; border-radius:5px; background:#2383e2; color:#fff; font-weight:650; cursor:pointer; }}
    .public-form-honeypot {{ position:absolute; left:-10000px; width:1px; height:1px; overflow:hidden; }}
    .public-form-success {{ margin:0 0 18px; padding:10px 12px; border-radius:6px; background:#e7f3ec; color:#0f7b6c; font-size:14px; }}
    .public-board, .public-gallery {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; align-items:start; }}
    .public-board section, .public-db-card, .public-feed article, .public-dashboard > * {{ padding:10px; border:1px solid var(--border); border-radius:6px; }}
    .public-board h4 {{ margin:0 0 8px; }}
    .public-db-card {{ display:grid; gap:4px; margin:5px 0; }}
    .public-db-card small, .public-feed time {{ color:var(--muted); font-size:12px; }}
    .public-list, .public-feed {{ display:grid; gap:7px; }}
    .public-feed article {{ display:flex; justify-content:space-between; gap:12px; }}
    .public-schedule, .public-map {{ padding:0; list-style:none; }}
    .public-schedule li, .public-map li {{ display:grid; grid-template-columns:120px minmax(0,1fr) auto; gap:10px; padding:8px 0; border-bottom:1px solid var(--border); }}
    .public-schedule time, .public-map span {{ color:var(--muted); }}
    .public-chart {{ display:grid; gap:7px; }}
    .public-chart > div {{ display:grid; grid-template-columns:100px minmax(0,1fr) 30px; align-items:center; gap:8px; }}
    .public-chart i {{ width:var(--public-chart); height:18px; border-radius:3px; background:#2383e2; }}
    .public-dashboard {{ display:grid; grid-template-columns:160px minmax(0,1fr); gap:10px; }}
    .public-dashboard > div {{ display:grid; place-items:center; align-content:center; }}
    .public-dashboard > div strong {{ font-size:34px; }}
    @media (max-width:720px) {{ .public-columns, .public-dashboard {{ grid-template-columns:1fr; gap:8px; }} .public-form {{ padding:18px; }} .public-schedule li, .public-map li {{ grid-template-columns:1fr; gap:3px; }} }}
  </style>
</head>
<body>
  {public_header}
  <main>
    <div class="page-icon">{_esc(icon)}</div>
    <h1 class="title">{_esc(title)}</h1>
    {submitted_notice}
    {blocks}
    <div class="public-note">{footer_note}</div>
  </main>
  <script>
    document.addEventListener("click", function (event) {{
      var button = event.target.closest("[data-public-tab-target]");
      if (!button) return;
      var tabs = button.closest(".public-tabs");
      if (!tabs) return;
      tabs.querySelectorAll("[data-public-tab-target]").forEach(function (item) {{
        var active = item === button;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", active ? "true" : "false");
      }});
      tabs.querySelectorAll(".public-tab-panel").forEach(function (panel) {{
        panel.hidden = panel.id !== button.dataset.publicTabTarget;
      }});
    }});
    (function () {{
      var form = document.getElementById("publicDuplicateForm");
      if (!form) return;
      var csrfToken = "";
      function loadSession() {{
        return fetch("/api/code/auth/me", {{ credentials: "same-origin" }})
          .then(function (response) {{ return response.json(); }})
          .then(function (data) {{
            csrfToken = data && data.authenticated ? String(data.csrf_token || "") : "";
            return data;
          }});
      }}
      loadSession().catch(function () {{ csrfToken = ""; }});
      form.addEventListener("submit", function (event) {{
        event.preventDefault();
        loadSession().then(function (data) {{
          if (!data || !data.authenticated || !csrfToken) {{
            window.location.assign("/login?next=" + encodeURIComponent(window.location.pathname));
            return null;
          }}
          return fetch(form.action, {{
            method: "POST",
            credentials: "same-origin",
            headers: {{ "X-CSRF-Token": csrfToken }},
          }});
        }}).then(function (response) {{
          if (!response) return;
          if (response.redirected) {{ window.location.assign(response.url); return; }}
          if (!response.ok) throw new Error("HTTP " + response.status);
          window.location.reload();
        }}).catch(function () {{
          window.alert("페이지를 복제하지 못했습니다. 로그인 상태를 확인해 주세요.");
        }});
      }});
    }})();
  </script>
</body>
</html>"""


def _degraded_fields(sources: list[str]) -> dict[str, object]:
    normalized = sorted({str(source).strip() for source in sources if str(source).strip()})
    return {
        "degraded": bool(normalized),
        "degraded_sources": normalized,
    }


def _notion_ai_context(
    user_id: str | None,
    question: str,
    page_id: str = "",
) -> tuple[str, list[str], list[str]]:
    uid = user_id or ""
    used: list[str] = []
    chunks: list[str] = []
    degraded_sources: list[str] = []
    budget = 18000

    def add_page(
        pid: str,
        reason: str,
        max_chars: int = 8000,
        *,
        required: bool = False,
    ) -> None:
        nonlocal budget
        if not pid or pid in used or budget <= 600:
            return
        try:
            exported = ns.export_page_markdown(uid, pid, include_assets=False)
        except Exception:
            if required:
                raise
            degraded_sources.append("related_page_content")
            return
        page = exported.get("page") or {}
        markdown = str(exported.get("markdown") or "")[:max_chars]
        chunk = f"\n--- {reason}: {page.get('title') or pid} ({pid}) ---\n{markdown}\n"
        if len(chunk) > budget:
            chunk = chunk[:budget].rstrip() + "\n... (truncated)\n"
        chunks.append(chunk)
        used.append(pid)
        budget -= len(chunk)

    if page_id:
        add_page(page_id, "current page", 10000, required=True)

    candidates = []
    for page in ns.list_pages(uid):
        pid = page.get("id") or ""
        if not pid or pid in used:
            continue
        try:
            exported = ns.export_page_markdown(uid, pid, include_assets=False)
        except Exception:
            degraded_sources.append("related_page_content")
            continue
        text = f"{page.get('title')}\n{exported.get('markdown') or ''}"
        score = _text_score(question, text)
        if score > 0:
            candidates.append((score, page, exported))
    candidates.sort(key=lambda item: (item[0], int((item[1] or {}).get("updated_at") or 0)), reverse=True)
    for score, page, exported in candidates[:4]:
        if budget <= 600:
            break
        pid = page.get("id") or ""
        markdown = str(exported.get("markdown") or "")[:4000]
        chunk = f"\n--- related page score={score}: {page.get('title') or pid} ({pid}) ---\n{markdown}\n"
        if len(chunk) > budget:
            chunk = chunk[:budget].rstrip() + "\n... (truncated)\n"
        chunks.append(chunk)
        used.append(pid)
        budget -= len(chunk)

    return "\n".join(chunks).strip(), used, degraded_sources


def _call_notion_ai(question: str, context: str, model_id: str = "") -> dict:
    global _HYNIX_NOTION_UNAVAILABLE_UNTIL
    messages = [
        {
            "role": "system",
            "content": (
                "You are Notion AI inside a local Notion-style workspace. "
                "Answer in Korean unless the user asks otherwise. Use the provided page context. "
                "If the context is insufficient, say what is missing. "
                "Do not claim to edit pages unless the user explicitly asks for a draft; return clear text that can be pasted into the page."
            ),
        },
        {"role": "system", "content": f"=== Notion Workspace Context ===\n{context or '(no page context)'}"},
        {"role": "user", "content": question},
    ]
    provider_order = default_provider_order()
    # Client-supplied model names do not bypass launch policy.  Company APIs
    # are attempted in configured order, then GGUF, with Codex present only as
    # the final lane when the process was started with ``--codex``.
    if time.monotonic() < _HYNIX_NOTION_UNAVAILABLE_UNTIL:
        provider_order = [
            provider for provider in provider_order if provider != "hynix_api"
        ]
    if not provider_order:
        text = None
        meta = {"fallback_errors": ["hynix_api: temporarily unavailable after a recent failure"]}
    else:
        text, meta = run_internal_text(
            "notion_ai_answer",
            {"question": question, "has_context": bool(context), "requested_model": model_id or ""},
            messages=messages,
            max_tokens=2048,
            temperature=0.2,
            api_timeout_s=5,
            provider_order=provider_order,
        )
        _HYNIX_NOTION_UNAVAILABLE_UNTIL = (
            0.0 if text else time.monotonic() + _HYNIX_NOTION_FAILURE_TTL_S
        )
    if not text:
        text = _notion_ai_fallback(question, context)
        return {
            "answer": text,
            "model_kind": "fallback",
            "model_used": "local-extractive-fallback",
            "fallback_errors": notion_http.public_fallback_errors(meta),
        }
    return {
        "answer": text.strip(),
        "model_kind": meta.get("model_kind") or "",
        "model_used": meta.get("model_used") or "",
        "fallback_errors": notion_http.public_fallback_errors(meta),
    }


def _notion_ai_fallback(question: str, context: str) -> str:
    """Deterministic fallback used while the Hynix endpoint is unavailable."""
    raw_question = str(question or "")
    if "=== 편집할 블록 ===" in raw_question:
        source = raw_question.split("=== 편집할 블록 ===", 1)[1].strip()
        if not source:
            return "편집할 블록에 텍스트가 없습니다."
        lowered_question = raw_question.lower()
        if "더 짧" in raw_question or "요약" in raw_question:
            sentences = re.split(r"(?<=[.!?。！？])\s+|\n+", source)
            compact = " ".join(item.strip() for item in sentences[:2] if item.strip())
            return (compact or source)[:500]
        if any(term in lowered_question for term in ("액션", "action", "할 일", "체크리스트")):
            lines = [item.strip(" -\t") for item in re.split(r"\n+|(?<=[.!?。！？])\s+", source) if item.strip()]
            selected = [item for item in lines if any(token in item.lower() for token in ("해야", "필요", "todo", "action", "next", "결정"))]
            return "\n".join(f"- [ ] {item}" for item in (selected[:10] or lines[:5]))
        # A disconnected fallback must never invent facts. For rewriting,
        # translation and continuation requests it returns a safe editable
        # draft while preserving the source verbatim.
        if any(term in lowered_question for term in ("translate", "번역", "이어서", "continue")):
            return source
        return re.sub(r"[ \t]+", " ", source).strip()

    cleaned: list[str] = []
    for raw in str(context or "").splitlines():
        line = re.sub(r"^[-#>*\s]+", "", raw).strip()
        if not line or line == "---" or line.startswith(("source_type:", "page_id:", "owner_user_id:", "updated_at:")):
            continue
        if line not in cleaned:
            cleaned.append(line[:360])
        if len(cleaned) >= 10:
            break
    if not cleaned:
        return (
            "현재 연결된 페이지 내용이 없어 로컬 fallback이 답을 만들 수 없습니다. "
            "페이지를 열거나 질문에 필요한 내용을 추가한 뒤 다시 시도해 주세요."
        )
    lowered = str(question or "").lower()
    if any(term in lowered for term in ("요약", "핵심", "정리", "summary")):
        return "페이지에서 확인된 핵심 내용입니다.\n\n" + "\n".join(f"- {line}" for line in cleaned[:7])
    if any(term in lowered for term in ("할 일", "액션", "action", "todo", "해야")):
        candidates = [line for line in cleaned if any(token in line.lower() for token in ("해야", "필요", "todo", "action", "next", "결정"))]
        selected = candidates[:7] or cleaned[:5]
        return "페이지에서 추출한 실행 후보입니다.\n\n" + "\n".join(f"- [ ] {line}" for line in selected)
    return (
        "Hynix Gemma 4 응답을 사용할 수 없어 페이지 본문에서 관련 내용을 추출했습니다.\n\n"
        + "\n".join(f"- {line}" for line in cleaned[:7])
    )


def register_notion_routes(app):
    notion_http.register_error_handlers(app)

    @app.route("/api/notion/inbox", methods=["GET"])
    def api_notion_inbox():
        try:
            uid = _uid()
            degraded_sources: list[str] = []
            limit = max(1, min(500, int(request.args.get("limit") or 200)))
            items = ns.list_inbox_items(uid, limit=limit)
            try:
                from code_assist_v1.outlook_integration import list_notifications

                items.extend(list_notifications(uid, limit=limit))
            except Exception:
                degraded_sources.append("outlook_notifications")
            unique = {str(item.get("id") or ""): item for item in items if item.get("id")}
            items = list(unique.values())
            items.sort(key=lambda item: (
                0 if item.get("due") else 1,
                -int(item.get("trigger_at") or item.get("created_at") or 0)
                if item.get("due")
                else int(item.get("trigger_at") or item.get("created_at") or 0),
            ))
            items = items[:limit]
        except Exception as e:
            return _error_response(e)
        return jsonify({
            "items": items,
            "count": len(items),
            **_degraded_fields(degraded_sources),
        })

    @app.route("/api/notion/pages", methods=["GET"])
    def api_notion_pages_list():
        uid = _uid()
        try:
            items = ns.list_pages(uid, query=request.args.get("q", ""))
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items), "user_id": uid})

    @app.route("/api/notion/search", methods=["GET"])
    def api_notion_search():
        uid = _uid()
        query = str(request.args.get("q") or "").strip()
        title_only = _truthy_arg(request.args.get("title_only"))
        creator = str(request.args.get("creator") or "").strip()
        scope = str(request.args.get("scope") or "all").strip().lower()
        date_filter = str(request.args.get("date") or "all").strip().lower()
        sort = str(request.args.get("sort") or "relevance").strip().lower()
        try:
            pages = ns.list_pages(uid)
            items: list[dict] = []
            degraded_sources: list[str] = []
            terms = _search_terms(query)
            phrases = _search_phrases(query)
            age_seconds = {"today": 86400, "week": 7 * 86400, "month": 31 * 86400, "year": 366 * 86400}
            cutoff = int(time.time()) - age_seconds.get(date_filter, 0) if date_filter in age_seconds else 0
            for page in pages:
                if scope in {"own", "shared"} and page.get("scope") != scope:
                    continue
                if creator:
                    owner = str(page.get("owner_user_id") or "")
                    if creator == "me":
                        if owner != uid:
                            continue
                    elif owner != creator:
                        continue
                if cutoff and int(page.get("updated_at") or 0) < cutoff:
                    continue

                title = str(page.get("title") or "")
                markdown = ""
                searchable = title
                if query and not title_only:
                    try:
                        exported = ns.export_page_markdown(uid, page.get("id") or "", include_assets=False)
                        markdown = str(exported.get("markdown") or "")
                        searchable = f"{title}\n{page.get('owner_user_id') or ''}\n{markdown}"
                    except Exception:
                        degraded_sources.append("page_content")
                        markdown = ""

                if query:
                    if phrases and any(phrase not in searchable.lower() for phrase in phrases):
                        continue
                    title_score = _text_score(query, title) * 12
                    body_score = 0 if title_only else _text_score(query, searchable)
                    exact_bonus = 20 if query.lower() in searchable.lower() else 0
                    score = title_score + body_score + exact_bonus
                    if score <= 0:
                        continue
                else:
                    score = 1

                snippet_source = title if title_only else (markdown or title)
                result = dict(page)
                result.update({
                    "score": score,
                    "snippet": _search_snippet(query, snippet_source) or title,
                    "terms": terms,
                })
                items.append(result)
            if sort == "updated_desc":
                items.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
            elif sort == "created_desc":
                items.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
            else:
                items.sort(key=lambda item: (int(item.get("score") or 0), int(item.get("updated_at") or 0)), reverse=True)
        except Exception as e:
            return _error_response(e)
        return jsonify({
            "items": items[:50],
            "count": len(items),
            "query": query,
            "user_id": uid,
            **_degraded_fields(degraded_sources),
        })

    @app.route("/api/notion/pages", methods=["POST"])
    def api_notion_pages_create():
        data = _json_data()
        try:
            add_to_parent_blocks = notion_http.boolean_field(
                data,
                "add_to_parent_blocks",
                default=False,
            )
            settings = notion_http.object_field(data, "settings")
            page = ns.create_page(
                _uid(),
                title=data.get("title") or "Untitled",
                parent_id=data.get("parent_id") or "",
                icon=data.get("icon") or "",
                cover=data.get("cover") or "",
                settings=settings,
                markdown=data.get("markdown") or "",
                add_to_parent_blocks=add_to_parent_blocks,
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "created", **page})

    @app.route("/api/notion/templates/second_brain", methods=["POST"])
    def api_notion_second_brain_template():
        uid = _uid()
        try:
            with user_write_lock("notion_template", uid):
                result = ns.create_second_brain_template(uid)
        except Exception as e:
            return _error_response(e)
        return jsonify(result)

    @app.route("/api/notion/pages/<path:page_id>", methods=["GET"])
    def api_notion_page_get(page_id):
        try:
            page = ns.get_page(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify(page)

    @app.route("/api/notion/pages/<path:page_id>", methods=["PATCH"])
    def api_notion_page_update(page_id):
        try:
            page = ns.update_page(_uid(), page_id, _json_data())
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "updated", **page})

    @app.route("/api/notion/pages/<path:page_id>/move", methods=["POST"])
    def api_notion_page_move(page_id):
        data = _json_data()
        try:
            page = ns.move_page(
                _uid(),
                page_id,
                parent_id=data.get("parent_id") or "",
                before_id=data.get("before_id") or "",
                after_id=data.get("after_id") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "moved", **page})

    @app.route("/api/notion/pages/<path:page_id>/duplicate", methods=["POST"])
    def api_notion_page_duplicate(page_id):
        try:
            page = ns.duplicate_page(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "duplicated", **page})

    @app.route("/api/notion/pages/<path:page_id>", methods=["DELETE"])
    def api_notion_page_archive(page_id):
        try:
            result = ns.archive_page(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "archived", **result})

    @app.route("/api/notion/trash", methods=["GET"])
    def api_notion_trash_list():
        try:
            items = ns.list_archived_pages(
                _uid(),
                query=request.args.get("q", ""),
                editor=request.args.get("editor", ""),
                scope=request.args.get("scope", "all"),
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/notion/trash/<path:page_id>/restore", methods=["POST"])
    def api_notion_trash_restore(page_id):
        try:
            result = ns.restore_page(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "restored", **result})

    @app.route("/api/notion/trash/<path:page_id>", methods=["DELETE"])
    def api_notion_trash_delete(page_id):
        try:
            result = ns.delete_page_permanent(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "deleted", **result})

    @app.route("/api/notion/pages/<path:page_id>/blocks", methods=["PUT"])
    def api_notion_blocks_replace(page_id):
        data = _json_data()
        try:
            blocks = notion_http.list_field(data, "blocks", required=True)
            page = ns.replace_blocks(_uid(), page_id, blocks)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "saved", **page})

    @app.route("/api/notion/pages/<path:page_id>/save", methods=["POST"])
    def api_notion_page_save(page_id):
        data = _json_data()
        try:
            result = ns.save_page_atomic(
                _uid(),
                page_id,
                base_revision=data.get("base_revision"),
                operation_id=data.get("operation_id"),
                page=data.get("page"),
                blocks=data.get("blocks"),
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "saved", **result})

    @app.route("/api/notion/pages/<path:page_id>/forms/<path:block_id>/submit", methods=["POST"])
    def api_notion_form_submit(page_id, block_id):
        data = _json_data()
        try:
            values = notion_http.object_field(data, "values")
            result = ns.submit_form(
                _uid(),
                page_id,
                block_id,
                values,
            )
        except Exception as e:
            return _error_response(e)
        return jsonify(result)

    @app.route("/api/notion/pages/<path:page_id>/versions", methods=["GET"])
    def api_notion_page_versions(page_id):
        try:
            items = ns.list_page_versions(_uid(), page_id, limit=request.args.get("limit", 30))
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/notion/pages/<path:page_id>/backlinks", methods=["GET"])
    def api_notion_page_backlinks(page_id):
        try:
            items = ns.list_backlinks(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/notion/pages/<path:page_id>/versions/<path:version_id>/restore", methods=["POST"])
    def api_notion_page_version_restore(page_id, version_id):
        try:
            page = ns.restore_page_version(_uid(), page_id, version_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "restored", **page})

    @app.route("/api/notion/pages/<path:page_id>/comments", methods=["GET", "POST"])
    def api_notion_comments(page_id):
        try:
            if request.method == "GET":
                items = ns.list_comments(
                    _uid(),
                    page_id,
                    include_resolved=request.args.get("include_resolved", "1") != "0",
                )
                return jsonify({"items": items, "count": len(items)})
            data = _json_data()
            item = ns.add_comment(
                _uid(),
                page_id,
                content=data.get("content") or "",
                block_id=data.get("block_id") or "",
                anchor=data.get("anchor") if isinstance(data.get("anchor"), dict) else None,
                parent_id=data.get("parent_id") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "created", "comment": item})

    @app.route("/api/notion/pages/<path:page_id>/comments/<path:comment_id>", methods=["PATCH", "DELETE"])
    def api_notion_comment_item(page_id, comment_id):
        try:
            if request.method == "DELETE":
                result = ns.delete_comment(_uid(), page_id, comment_id)
                return jsonify({"status": "deleted", **result})
            item = ns.update_comment(_uid(), page_id, comment_id, _json_data())
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "updated", "comment": item})

    @app.route("/api/notion/pages/<path:page_id>/comments/<path:comment_id>/reactions", methods=["POST"])
    def api_notion_comment_reaction(page_id, comment_id):
        try:
            item = ns.toggle_comment_reaction(
                _uid(), page_id, comment_id, str(_json_data().get("emoji") or ""),
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "updated", "comment": item})

    @app.route("/api/notion/pages/<path:page_id>/files", methods=["POST"])
    def api_notion_file_upload(page_id):
        if "file" not in request.files:
            return notion_http.validation_response("file required")
        f = request.files["file"]
        if not f.filename:
            return notion_http.validation_response("filename required")
        try:
            item = ns.upload_file(
                _uid(),
                page_id,
                filename=f.filename,
                raw=f.read(),
                mime_type=f.mimetype or "",
                insert_block=request.form.get("insert_block", "1") != "0",
                block_type=request.form.get("block_type") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "uploaded", "file": item})

    @app.route("/api/notion/files/<path:file_id>", methods=["GET"])
    def api_notion_file_get(file_id):
        try:
            item = ns.get_file(_uid(), file_id)
            return send_file(
                item["path"],
                mimetype=item.get("mime_type") or None,
                as_attachment=False,
                download_name=item.get("original_name") or item.get("stored_name") or file_id,
            )
        except Exception as e:
            return _error_response(e)

    @app.route("/public/notion/<token>", methods=["GET"])
    def public_notion_page(token):
        try:
            data = ns.resolve_public_page(token)
            return _public_page_html(token, data), 200, {"Content-Type": "text/html; charset=utf-8"}
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                return "Public page not found", 404
            current_app.logger.error(
                "Unhandled public Notion page error",
                exc_info=(type(e), e, e.__traceback__),
            )
            return "Public page could not be loaded", 500

    @app.route("/public/notion/<token>/duplicate", methods=["POST"])
    def public_notion_duplicate(token):
        user_id = _uid()
        if not user_id:
            return redirect(f"/login?next={quote_plus(f'/public/notion/{token}')}", code=303)
        try:
            data = ns.duplicate_public_page(user_id, token)
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                return "Public page not found", 404
            if isinstance(e, PermissionError):
                return "Public page duplication is disabled", 403
            if isinstance(e, ValueError):
                return f"Public page duplication failed: {_esc(e)}", 400
            return "Public page duplication failed", 500
        page_id = str((data.get("page") or {}).get("id") or "")
        return redirect(f"/page/{quote_plus(page_id)}", code=303)

    @app.route("/public/notion/<token>/forms/<path:block_id>", methods=["POST"])
    def public_notion_form_submit(token, block_id):
        if request.form.get("_website"):
            return Response("", status=303, headers={"Location": f"/public/notion/{token}?submitted=1"})
        values = {
            str(key)[2:]: value
            for key, value in request.form.items()
            if str(key).startswith("f_") and 2 < len(str(key)) <= 202
        }
        try:
            ns.submit_public_form(token, block_id, values)
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                return "Public form not found", 404
            if isinstance(e, ValueError):
                return f"Form submission failed: {_esc(e)}", 400
            return "Form submission failed", 500
        return Response("", status=303, headers={"Location": f"/public/notion/{token}?submitted=1"})

    @app.route("/public/notion/<token>/files/<path:file_id>", methods=["GET"])
    def public_notion_file(token, file_id):
        try:
            item = ns.get_public_file(token, file_id)
            return send_file(
                item["path"],
                mimetype=item.get("mime_type") or None,
                as_attachment=False,
                download_name=item.get("original_name") or item.get("stored_name") or file_id,
            )
        except Exception as e:
            return _error_response(e)

    @app.route("/api/notion/pages/<path:page_id>/export_markdown", methods=["GET"])
    def api_notion_page_export(page_id):
        try:
            result = ns.export_page_markdown(_uid(), page_id, include_assets=False)
        except Exception as e:
            return _error_response(e)
        if request.args.get("download") == "1":
            filename = re.sub(r"[^A-Za-z0-9._-]+", "-", str((result.get("page") or {}).get("title") or page_id)).strip("-.") or "notion-page"
            return Response(
                result.get("markdown") or "",
                mimetype="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename[:100]}.md"'},
            )
        return jsonify(result)

    @app.route("/api/notion/pages/<path:page_id>/export_html", methods=["GET"])
    def api_notion_page_export_html(page_id):
        try:
            data = ns.get_page(_uid(), page_id)
            filename = re.sub(r"[^A-Za-z0-9._-]+", "-", str((data.get("page") or {}).get("title") or page_id)).strip("-.") or "notion-page"
            return Response(
                _public_page_html("", data),
                mimetype="text/html; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename[:100]}.html"'},
            )
        except Exception as e:
            return _error_response(e)

    @app.route("/api/notion/pages/<path:page_id>/export_csv", methods=["GET"])
    def api_notion_database_export_csv(page_id):
        try:
            data = ns.get_page(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        block_id = str(request.args.get("block_id") or "")
        database_types = {"table", "board", "calendar", "list", "gallery", "timeline", "chart", "dashboard", "form", "feed", "map"}
        block = next((
            item for item in (data.get("blocks") or [])
            if item.get("type") in database_types and (not block_id or str(item.get("id") or "") == block_id)
        ), None)
        if not block:
            return _error_response(FileNotFoundError("database block not found"))
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        properties = [item for item in (props.get("properties") if isinstance(props.get("properties"), list) else []) if isinstance(item, dict) and item.get("id")]
        rows = [item for item in (props.get("rows") if isinstance(props.get("rows"), list) else []) if isinstance(item, dict) and not item.get("archived")]
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([str(item.get("name") or item.get("id")) for item in properties])
        for row in rows:
            writer.writerow([_public_database_value(row.get(item["id"])) for item in properties])
        title = str(block.get("content") or (data.get("page") or {}).get("title") or "database")
        filename = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-.") or "database"
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename[:100]}.csv"'},
        )

    @app.route("/api/notion/overview", methods=["GET"])
    def api_notion_overview():
        uid = _uid()
        try:
            degraded_sources: list[str] = []
            pages = ns.list_pages(uid)
            sessions = _session_list(uid, limit=5)
            try:
                sources = sb.list_sources(user_id=uid)[:5]
            except Exception:
                sources = []
                degraded_sources.append("second_brain_sources")
            try:
                thoughts = sb.list_thoughts(user_id=uid)[:5]
            except Exception:
                thoughts = []
                degraded_sources.append("second_brain_thoughts")
            try:
                calendar_events = ns.list_calendar_events(uid, limit=30)
            except Exception:
                calendar_events = []
                degraded_sources.append("calendar_events")
            attached = []
            for session in sessions:
                sid = session.get("session_id") or ""
                try:
                    items = ns.list_session_pages(uid, sid)
                except Exception:
                    degraded_sources.append("session_page_bindings")
                    continue
                if items:
                    attached.append({
                        "session_id": sid,
                        "title": session.get("title") or sid,
                        "pages": [item.get("page") for item in items if item.get("page")],
                    })
        except Exception as e:
            return _error_response(e)
        return jsonify({
            "pages": pages,
            "sessions": sessions,
            "sources": sources,
            "thoughts": thoughts,
            "calendar_events": calendar_events,
            "attached_sessions": attached,
            **_degraded_fields(degraded_sources),
        })

    @app.route("/api/notion/pages/<path:page_id>/publish_preview", methods=["GET"])
    def api_notion_publish_preview(page_id):
        try:
            snapshot = notion_publication.capture_page_source_snapshot(
                _uid(),
                page_id,
                source_store=ns,
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({
            "page": snapshot.page_payload(),
            "markdown": snapshot.markdown,
            "image_count": len(snapshot.frozen_images),
            "file_count": sum(1 for asset in snapshot.assets if asset.role == "file"),
            "source_snapshot": snapshot.descriptor(),
        })

    @app.route("/api/notion/pages/<path:page_id>/publish_second_brain", methods=["POST"])
    def api_notion_publish_second_brain(page_id):
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

    @app.route("/api/notion/pages/<path:page_id>/public_link", methods=["GET"])
    def api_notion_public_link_get(page_id):
        try:
            item = ns.get_public_link(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify(_public_link_response(item))

    @app.route("/api/notion/pages/<path:page_id>/public_link", methods=["POST"])
    def api_notion_public_link_enable(page_id):
        try:
            item = ns.enable_public_link(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "enabled", **_public_link_response(item)})

    @app.route("/api/notion/pages/<path:page_id>/public_link", methods=["PATCH"])
    def api_notion_public_link_options(page_id):
        data = _json_data()
        try:
            if "allow_duplicate" not in data:
                raise notion_http.NotionRequestValidationError("allow_duplicate required")
            allow_duplicate = notion_http.boolean_field(data, "allow_duplicate")
            item = ns.update_public_link_options(
                _uid(),
                page_id,
                allow_duplicate=allow_duplicate,
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "updated", **_public_link_response(item)})

    @app.route("/api/notion/pages/<path:page_id>/public_link", methods=["DELETE"])
    def api_notion_public_link_disable(page_id):
        try:
            item = ns.disable_public_link(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "disabled", **_public_link_response(item)})

    @app.route("/api/notion/ai/ask", methods=["POST"])
    def api_notion_ai_ask():
        data = _json_data()
        question = str(data.get("question") or "").strip()
        if not question:
            return notion_http.validation_response("question required")
        page_id = str(data.get("page_id") or "").strip()
        model_id = str(data.get("model") or "").strip()
        try:
            context, page_ids, degraded_sources = _notion_ai_context(
                _uid(),
                question,
                page_id,
            )
            result = _call_notion_ai(question, context, model_id=model_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({
            "status": "answered",
            "question": question,
            "page_ids": page_ids,
            **_degraded_fields(degraded_sources),
            **result,
        })

    @app.route("/api/notion/pages/<path:page_id>/shares", methods=["GET"])
    def api_notion_shares_list(page_id):
        try:
            items = ns.list_shares(_uid(), page_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/notion/pages/<path:page_id>/shares", methods=["POST"])
    def api_notion_share_create(page_id):
        data = _json_data()
        try:
            role = notion_http.enum_field(
                data,
                "role",
                {"view", "comment", "edit"},
                default="view",
            )
            item = ns.share_page(
                _uid(),
                page_id,
                data.get("shared_user_id") or data.get("user_id") or "",
                role=role,
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "shared", "share": item})

    @app.route("/api/notion/pages/<path:page_id>/shares/<path:shared_user_id>", methods=["DELETE"])
    def api_notion_share_revoke(page_id, shared_user_id):
        try:
            item = ns.revoke_share(_uid(), page_id, shared_user_id)
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "revoked", **item})

    @app.route("/api/notion/users", methods=["GET"])
    def api_notion_users():
        try:
            uid = _uid()
            users = [
                item for item in list_public_users(include_data=False)
                if item.get("id") and item.get("id") != uid and not item.get("disabled")
            ]
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": users, "count": len(users)})

    @app.route("/api/notion/chat/sessions", methods=["GET"])
    def api_notion_chat_sessions():
        try:
            try:
                limit = int(request.args.get("limit") or 30)
            except ValueError:
                limit = 30
            items = _session_list(_uid(), limit=limit)
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/notion/pages/<path:page_id>/attach", methods=["POST"])
    def api_notion_page_attach(page_id):
        data = _json_data()
        try:
            item = ns.attach_page_to_session(_uid(), page_id, data.get("session_id") or "")
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "attached", **item})

    @app.route("/api/notion/session_pages", methods=["GET"])
    def api_notion_session_pages():
        try:
            items = ns.list_session_pages(_uid(), request.args.get("session_id") or "")
        except Exception as e:
            return _error_response(e)
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/notion/session_pages/<path:page_id>", methods=["DELETE"])
    def api_notion_session_page_detach(page_id):
        try:
            item = ns.detach_page_from_session(_uid(), page_id, request.args.get("session_id") or "")
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "detached", **item})

    @app.route("/api/notion/import_chat", methods=["POST"])
    def api_notion_import_chat():
        data = _json_data()
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        if not messages:
            return notion_http.validation_response("messages required")
        title = data.get("title") or "Chat Summary"
        try:
            markdown = ns.chat_messages_to_markdown(
                messages,
                title=title,
                session_id=data.get("session_id") or "",
            )
            page = ns.create_page_from_markdown(_uid(), title=title, markdown=markdown, parent_id=data.get("parent_id") or "")
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "imported", **page})

    @app.route("/api/notion/import_markdown", methods=["POST"])
    def api_notion_import_markdown():
        data = _json_data()
        markdown = str(data.get("markdown") or "")
        if not markdown.strip():
            return notion_http.validation_response("markdown required")
        if len(markdown) > 2_000_000:
            return notion_http.validation_response(
                "markdown exceeds 2MB limit",
                code="payload_too_large",
                status=413,
            )
        title = str(data.get("title") or "Imported page").strip()[:160] or "Imported page"
        try:
            page = ns.create_page_from_markdown(
                _uid(),
                title=title,
                markdown=markdown,
                parent_id=data.get("parent_id") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "imported", **page})

    @app.route("/api/notion/import_csv", methods=["POST"])
    def api_notion_import_csv():
        data = _json_data()
        csv_text = str(data.get("csv") or "")
        if not csv_text.strip():
            return notion_http.validation_response("csv required")
        if len(csv_text.encode("utf-8")) > 2_000_000:
            return notion_http.validation_response(
                "csv exceeds 2MB limit",
                code="payload_too_large",
                status=413,
            )
        title = str(data.get("title") or "Imported database").strip()[:160] or "Imported database"
        try:
            page = ns.create_page_from_csv(
                _uid(),
                title=title,
                csv_text=csv_text,
                parent_id=data.get("parent_id") or "",
            )
        except Exception as e:
            return _error_response(e)
        return jsonify({"status": "imported", **page})
