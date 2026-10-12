"""Native provider deltas for the AI home, with no buffered typing animation."""
from __future__ import annotations

import json
import os
import re
import tempfile

import requests

from code_assist_v1 import config, internal_llm
from code_assist_v1.llm_wire import chat_payload, encode_chat_payload
from code_assist_v1 import notion_ai_chat_artifacts as artifacts


class StreamInterrupted(RuntimeError):
    """An upstream response ended without a complete answer."""


def schema_enabled(mode):
    # Explicit host opt-in, after verifying the actual provider's wire support.
    return mode != "chat" and os.environ.get("CODE_ASSIST_NOTION_AI_JSON_SCHEMA") == "1"


def provider_chunks(lane, cfg, payload, system, mode, guard, *, max_tokens=10000, response_schema=None, user_prompt=None, read_timeout=60, reasoning_char_budget=None):
    messages = internal_llm._messages_for_task("notion_ai_chat", payload,
        output_format="text" if mode == "chat" else "json", system_prompt=system, user_prompt=user_prompt)
    if lane == "codex":
        from code_assist_v1.codex_bridge import stream_codex_exec
        with tempfile.TemporaryDirectory(prefix="notion-ai-chat-") as scratch:
            isolated = {**cfg, "codex_cwd": scratch, "codex_sandbox": "read-only",
                        "codex_ephemeral": "1", "codex_reasoning_effort": "low"}
            stream = stream_codex_exec(messages, isolated, timeout_s=180,
                                      preserve_input=True, attempt_guard=guard)
            try:
                for event in stream:
                    if event.get("error"):
                        raise StreamInterrupted("provider stream failed")
                    if event.get("delta"):
                        yield str(event["delta"])
            finally:
                stream.close()
        return
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    token, _ = config.resolve_api_token(cfg)
    explicit_auth = any(k in cfg for k in ("api_key_env", "token_env", "auth_env",
        "api_key_file", "token_file", "auth_file", "api_key_source", "token_source", "auth_source"))
    if token:
        headers["Authorization"] = "Bearer " + token
    elif explicit_auth:
        raise StreamInterrupted("configured provider credential is unavailable")
    body = chat_payload(cfg.get("model"), messages, max_tokens=max_tokens, temperature=0.2)
    body["stream"] = True
    if reasoning_char_budget:
        body.update(reasoning_format='auto', reasoning_control=True,
                    chat_template_kwargs={'enable_thinking':True})
    if schema_enabled(mode) or response_schema is not None and schema_enabled("research"):
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "notion_ai_" + mode, "strict": True,
            "schema": response_schema or artifacts.response_schema(mode, payload["references"])}}
    wire = encode_chat_payload(body)
    if len(wire) > 768000:
        raise StreamInterrupted("provider request exceeds byte budget")
    verify = str(cfg.get("tls_ca_bundle") or "").strip() or (cfg.get("tls_verify", True) is not False)

    class GuardedSession(requests.Session):
        def send(self, request, **kwargs):
            guard()  # Also fences redirected requests after auth/environment preparation.
            response = super().send(request, **kwargs)
            try:
                guard()
            except BaseException:
                response.close()
                raise
            return response

    with GuardedSession() as session:
        with session.post(cfg.get("url"), data=wire, headers=headers, timeout=(10, read_timeout),
                          verify=verify, stream=True) as upstream:
            if upstream.status_code >= 400:
                raise StreamInterrupted("provider HTTP request failed")
            upstream.encoding = "utf-8"
            finished = False
            reasoning_characters, reasoning_ended = 0, False
            frame = []
            # Small reads matter: requests' default buffering can hide short answers.
            for line in upstream.iter_lines(chunk_size=1, decode_unicode=True):
                if line.startswith("data:"):
                    frame.append(line[5:].lstrip(" "))
                    continue
                if line or not frame:
                    continue
                data = "\n".join(frame); frame = []
                if data == "[DONE]":
                    if not finished:
                        raise StreamInterrupted("provider omitted completion status")
                    return
                try:
                    event = json.loads(data)
                    if event.get("error"):
                        raise StreamInterrupted("provider stream reported an error")
                    choices = event.get("choices", [])
                    if not choices:  # Optional usage frame.
                        continue
                    choice = choices[0]
                    if reasoning_char_budget:
                        guard()
                        thought = choice.get('delta',{}).get('reasoning_content') or ''
                        if not isinstance(thought,str):
                            raise StreamInterrupted('invalid provider reasoning frame')
                        reasoning_characters += len(thought)
                        if reasoning_characters >= reasoning_char_budget and not reasoning_ended:
                            completion_id = event.get('id')
                            if not isinstance(completion_id,str) or not 1 <= len(completion_id) <= 200:
                                raise StreamInterrupted('provider reasoning control unavailable')
                            with session.post(cfg['url'].rstrip('/')+'/control',
                                json={'id':completion_id,'action':'reasoning_end'}, headers=headers,
                                timeout=(3,10),verify=verify,allow_redirects=False) as control:
                                if control.status_code != 200 or control.json().get('success') is not True:
                                    raise StreamInterrupted('provider reasoning control unavailable')
                            reasoning_ended = True
                    reason = choice.get("finish_reason")
                    if reason:
                        if reason != "stop":
                            raise StreamInterrupted("provider answer was incomplete")
                        finished = True
                    delta = choice.get("delta", {}).get("content")
                    if delta:
                        if not isinstance(delta, str):
                            raise StreamInterrupted("invalid provider delta")
                        yield delta
                except (ValueError, TypeError, AttributeError, IndexError) as exc:
                    raise StreamInterrupted("invalid provider stream frame") from exc
            raise StreamInterrupted("provider stream disconnected before DONE")


class AnswerDecoder:
    """Expose only the answer JSON string while structured results are generated."""
    def __init__(self):
        self.raw = ""
        self.position = None
        self.finished = False

    def feed(self, chunk):
        self.raw += chunk
        if self.finished:
            return ""
        if self.position is None:
            match = re.search(r'"answer"\s*:\s*"', self.raw)
            if not match:
                return ""
            self.position = match.end()
        out = []
        while self.position < len(self.raw):
            pos = self.position
            char = self.raw[pos]
            if char == '"':
                self.finished = True
                break
            if char != "\\":
                out.append(char); self.position += 1
                continue
            if pos + 1 >= len(self.raw):
                break
            end = pos + (6 if self.raw[pos + 1] == "u" else 2)
            if end > len(self.raw):
                break
            escape = self.raw[pos:end]
            if escape.startswith("\\u"):
                code = int(escape[2:], 16)
                if 0xD800 <= code <= 0xDBFF:
                    if end + 6 > len(self.raw):
                        break
                    escape += self.raw[end:end + 6]; end += 6
            decoded = json.loads('"' + escape + '"')
            if any(0xD800 <= ord(c) <= 0xDFFF for c in decoded):
                raise ValueError("invalid surrogate in answer")
            out.append(decoded); self.position = end
        return "".join(out)
