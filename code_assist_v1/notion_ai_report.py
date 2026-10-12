"""Explicit, replay-safe personal report creation; never an automatic AI effect."""
from __future__ import annotations

import hashlib
import json
import re

from code_assist_v1 import config, notion_store as ns
from code_assist_v1.notion_ai_chat_store import operation_id, ChatConflict


def report_blocks(text):
    """Import prose/tables without interpreting model HTML as app commands."""
    blocks, pending, lines, index, in_code = [], [], text.splitlines(), 0, False
    def flush():
        if pending:
            blocks.extend(ns.markdown_to_blocks("\n".join(pending)))
            pending.clear()
    def cells(line):
        values = [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        return [re.sub(r"\*\*([^*\n]+)\*\*|`([^`\n]+)`", lambda match: match.group(1) or match.group(2), value) for value in values]
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.lstrip().startswith("<"):
            flush()
            blocks.append({"type": "paragraph", "content": line, "props": {}})
            index += 1
            continue
        separator = index + 1
        while separator < len(lines) and not lines[separator].strip():
            separator += 1
        header = cells(line) if not in_code and line.strip().startswith("|") else []
        rules = cells(lines[separator]) if separator < len(lines) else []
        if 2 <= len(header) <= 16 and len(rules) == len(header) and all(re.fullmatch(r":?-{3,}:?", rule) for rule in rules):
            flush()
            rows, cursor = [header], separator + 1
            while cursor < len(lines) and len(rows) <= 200:
                if not lines[cursor].strip():
                    cursor += 1
                    continue
                row = cells(lines[cursor]) if lines[cursor].strip().startswith("|") else []
                if len(row) != len(header):
                    break
                rows.append(row)
                cursor += 1
            blocks.append({"type": "simple_table", "content": "", "props": {"rows": rows, "header_row": True}})
            index = cursor
            continue
        pending.append(line)
        index += 1
    flush()
    if any(block["type"] not in {"paragraph", "heading1", "heading2", "heading3", "heading4", "bullet", "numbered", "todo", "quote", "code", "equation", "divider", "simple_table"} for block in blocks):
        raise ValueError("지원하지 않는 보고서 형식입니다. Markdown으로 내려받아 확인하세요.")
    return blocks


def save_report(uid, cid, message, op, guard):
    from code_assist_v1.notion_collaboration import iam_runtime
    if iam_runtime.runtime_requested():
        raise ValueError("현재 공용 워크스페이스에서는 보고서를 Markdown으로 내려받아 페이지에 넣어 주세요.")
    op = operation_id(op)
    source_op = operation_id(message["operation_id"])
    title = next((line.lstrip("# ").strip() for line in message["text"].splitlines() if line.strip()), "리서치 보고서")[:160]
    # Bind an operation to its source answer; never accept report bodies from a client.
    identity = {"chat_id": cid, "message_operation_id": source_op, "text": message["text"],
                "citations": message.get("citations", [])}
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    receipt_id = "ai-research-report:" + op
    pid = ns.page_id_for_operation(uid, namespace="ai-research-report-v1", operation_id=op)
    from code_assist_v1.notion_ai_report_format import present_report
    blocks = report_blocks(present_report(message)["text"])
    blocks.append({"type": "heading2", "content": "출처", "props": {}})
    deep = message.get('context_info', {}).get('research', {}).get('depth') == 'deep'
    for index, citation in enumerate(message.get("citations", []), 1):
        label = f"[{index}] {citation['title']}" if deep else citation['title']
        if deep and citation.get('location'):
            label += ' — '+citation['location']
        blocks.append({"type": "paragraph", "content": label, "props": {}})
        blocks.append({"type": "quote", "content": citation["quote"], "props": {}})
        if citation.get("url"):
            blocks.append({"type": "paragraph", "content": citation["url"], "props": {}})
    guard()
    with ns.bind_user_data_root(config.USER_DATA_DIR), ns._write_connection(uid, bootstrap_if_missing=True) as con:
        con.execute("BEGIN IMMEDIATE")
        if not ns._atomic_schema_ready(con):
            raise ns.AtomicSaveUnavailable("report save schema is unavailable")
        prior = con.execute("SELECT request_hash,result_json FROM page_save_operations "
                            "WHERE requester_user_id=? AND operation_id=?", (uid, receipt_id)).fetchone()
        if prior:
            if prior["request_hash"] != digest:
                raise ChatConflict("같은 저장 요청 ID의 보고서가 달라졌습니다.")
            saved = con.execute("SELECT archived FROM pages WHERE id=? AND owner_user_id=?", (pid, uid)).fetchone()
            if not saved or saved["archived"]:
                raise FileNotFoundError("저장한 보고서를 찾을 수 없습니다.")
            guard()
            return ns._json_load(prior["result_json"], {})
        if con.execute("SELECT 1 FROM pages WHERE id=?", (pid,)).fetchone():
            raise ChatConflict("저장 요청 ID가 이미 사용되었습니다.")
        guard()
        now = ns._now()
        position = con.execute("SELECT COALESCE(MAX(position),0)+1 FROM pages WHERE archived=0 AND parent_id IS NULL").fetchone()[0]
        con.execute("INSERT INTO pages(id,owner_user_id,parent_id,title,icon,cover,settings_json,position,"
                    "archived,archived_at,created_at,updated_at) VALUES(?,?,NULL,?,'','','{}',?,0,NULL,?,?)",
                    (pid, uid, title, position, now, now))
        ns._replace_blocks_in_connection(con, uid, pid, blocks, now=now)
        ns._record_page_version(con, uid, pid, now=now)
        result = {"page_id": pid, "title": title, "url": "/page/" + pid}
        con.execute("INSERT INTO page_save_operations(requester_user_id,operation_id,page_id,owner_user_id,"
                    "request_hash,result_revision,result_json,created_at) VALUES(?,?,?,?,?,1,?,?)",
                    (uid, receipt_id, pid, uid, digest, ns._json_dump(result), now))
        con.commit()
    return result
