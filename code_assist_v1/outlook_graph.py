"""Single-tenant Microsoft Graph OAuth and read-only Outlook access.

Only delegated ``Mail.Read`` access is requested. MSAL token cache data and
short-lived OAuth flow state are encrypted at rest with Fernet before they are
written to the per-user workspace.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse

import requests

try:
    import msal
except ImportError:  # pragma: no cover - exercised by configuration status
    msal = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - exercised by configuration status
    Fernet = None

    class InvalidToken(Exception):
        pass

from code_assist_v1.auth import safe_user_id
from code_assist_v1.concurrency import user_write_lock
from code_assist_v1.config import RUNTIME_DIR, USER_DATA_DIR
from code_assist_v1.job_manager import redact_sensitive


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_AUTHORITY_BASE = "https://login.microsoftonline.com"
GRAPH_SCOPES = ("Mail.Read",)
GRAPH_PERMISSION_DESCRIPTION = "Delegated Mail.Read (signed-in user's mailbox only)"
OAUTH_FLOW_TTL_SECONDS = 10 * 60
TOKEN_KEY_FILE = os.path.join(RUNTIME_DIR, "outlook_graph.key")
_TOKEN_FILE = "graph_token.enc"
_FLOW_FILE = "graph_oauth_flow.enc"
_crypto_guard = threading.RLock()

_MESSAGE_SELECT = ",".join(
    (
        "id",
        "internetMessageId",
        "conversationId",
        "subject",
        "from",
        "toRecipients",
        "ccRecipients",
        "receivedDateTime",
        "lastModifiedDateTime",
        "isRead",
        "importance",
        "body",
        "hasAttachments",
    )
)


class GraphConfigurationError(RuntimeError):
    pass


class GraphAuthenticationRequired(RuntimeError):
    pass


class GraphOAuthError(RuntimeError):
    pass


class GraphRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status_code = int(status_code or 0)
        self.code = str(code or "")


class _MailHtmlTextParser(HTMLParser):
    """Extract readable text if Graph ever ignores the plain-text preference."""

    _BREAK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "div", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
        "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
    }
    _IGNORED_TAGS = {"head", "script", "style", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = str(tag or "").lower()
        if normalized in self._IGNORED_TAGS:
            self._ignored_depth += 1
        elif not self._ignored_depth and normalized in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = str(tag or "").lower()
        if normalized in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        elif not self._ignored_depth and normalized in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _uid(user_id: str | None) -> str:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("authenticated user required")
    return uid


def _root(user_id: str | None) -> str:
    path = os.path.join(USER_DATA_DIR, _uid(user_id), "outlook")
    os.makedirs(path, exist_ok=True)
    return path


def _token_path(user_id: str | None) -> str:
    return os.path.join(_root(user_id), _TOKEN_FILE)


def _flow_path(user_id: str | None) -> str:
    return os.path.join(_root(user_id), _FLOW_FILE)


def _read_secret(env_name: str, file_env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    path = os.environ.get(file_env_name, "").strip()
    if not path:
        return ""
    try:
        with open(os.path.abspath(path), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _guid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, AttributeError, TypeError):
        return ""


def _valid_redirect_uri(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.fragment or parsed.query or parsed.username or parsed.password:
        return False
    if parsed.scheme == "https" and bool(parsed.netloc):
        return True
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in {"localhost", "127.0.0.1"}


def graph_configuration() -> dict[str, Any]:
    raw_tenant = os.environ.get("CODE_ASSIST_MS_TENANT_ID", "").strip()
    raw_client = os.environ.get("CODE_ASSIST_MS_CLIENT_ID", "").strip()
    redirect_uri = os.environ.get("CODE_ASSIST_MS_REDIRECT_URI", "").strip()
    tenant_id = _guid(raw_tenant)
    client_id = _guid(raw_client)
    client_secret = _read_secret("CODE_ASSIST_MS_CLIENT_SECRET", "CODE_ASSIST_MS_CLIENT_SECRET_FILE")
    missing: list[str] = []
    if not tenant_id:
        missing.append("CODE_ASSIST_MS_TENANT_ID (GUID)")
    if not client_id:
        missing.append("CODE_ASSIST_MS_CLIENT_ID (GUID)")
    if not client_secret:
        missing.append("CODE_ASSIST_MS_CLIENT_SECRET 또는 CODE_ASSIST_MS_CLIENT_SECRET_FILE")
    if not _valid_redirect_uri(redirect_uri):
        missing.append("CODE_ASSIST_MS_REDIRECT_URI (HTTPS; localhost 개발만 HTTP 허용)")
    if msal is None:
        missing.append("Python package: msal")
    if Fernet is None:
        missing.append("Python package: cryptography")
    return {
        "configured": not missing,
        "single_tenant": True,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "authority": f"{GRAPH_AUTHORITY_BASE}/{tenant_id}" if tenant_id else "",
        "scopes": list(GRAPH_SCOPES),
        "missing": missing,
    }


def _masked_guid(value: str) -> str:
    return f"{value[:8]}…{value[-4:]}" if len(value) >= 13 else ""


def public_configuration() -> dict[str, Any]:
    cfg = graph_configuration()
    return {
        "configured": bool(cfg["configured"]),
        "single_tenant": True,
        "tenant_id_hint": _masked_guid(str(cfg.get("tenant_id") or "")),
        "client_id_hint": _masked_guid(str(cfg.get("client_id") or "")),
        "redirect_uri": cfg.get("redirect_uri") or "",
        "permissions": GRAPH_PERMISSION_DESCRIPTION,
        "missing": list(cfg.get("missing") or []),
    }


def _require_configuration() -> dict[str, Any]:
    cfg = graph_configuration()
    if not cfg["configured"]:
        missing = ", ".join(cfg.get("missing") or [])
        raise GraphConfigurationError(f"Microsoft Graph single-tenant 설정이 필요합니다: {missing}")
    return cfg


def _key_file_path() -> str:
    return os.path.abspath(os.environ.get("CODE_ASSIST_MS_TOKEN_KEY_FILE", "").strip() or TOKEN_KEY_FILE)


def _fernet():
    if Fernet is None:
        raise GraphConfigurationError("cryptography 패키지가 필요합니다")
    supplied = os.environ.get("CODE_ASSIST_MS_TOKEN_KEY", "").strip()
    if supplied:
        try:
            return Fernet(supplied.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise GraphConfigurationError("CODE_ASSIST_MS_TOKEN_KEY가 올바른 Fernet 키가 아닙니다") from exc
    path = _key_file_path()
    with _crypto_guard:
        try:
            with open(path, "rb") as handle:
                key = handle.read().strip()
            return Fernet(key)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise GraphConfigurationError("Microsoft Graph 토큰 암호화 키를 읽을 수 없습니다") from exc
        os.makedirs(os.path.dirname(path), exist_ok=True)
        key = Fernet.generate_key()
        temporary = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
        with open(temporary, "xb") as handle:
            handle.write(key + b"\n")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        try:
            os.link(temporary, path)
        except FileExistsError:
            with open(path, "rb") as handle:
                key = handle.read().strip()
        except OSError as exc:
            if not os.path.isfile(path):
                raise GraphConfigurationError("Microsoft Graph 토큰 암호화 키를 만들 수 없습니다") from exc
            with open(path, "rb") as handle:
                key = handle.read().strip()
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass
        return Fernet(key)


def _write_encrypted(path: str, value: dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = _fernet().encrypt(raw)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    with open(temporary, "wb") as handle:
        handle.write(encrypted)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _read_encrypted(path: str) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            encrypted = handle.read()
        decoded = _fernet().decrypt(encrypted)
        value = json.loads(decoded.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError, InvalidToken) as exc:
        raise GraphAuthenticationRequired("저장된 Microsoft 인증 정보를 읽을 수 없습니다. 다시 연결해 주세요.") from exc


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _new_cache(serialized: str = ""):
    if msal is None:
        raise GraphConfigurationError("msal 패키지가 필요합니다")
    cache = msal.SerializableTokenCache()
    if serialized:
        cache.deserialize(serialized)
    return cache


def _msal_app(cfg: dict[str, Any], cache=None):
    if msal is None:
        raise GraphConfigurationError("msal 패키지가 필요합니다")
    return msal.ConfidentialClientApplication(
        cfg["client_id"],
        authority=cfg["authority"],
        client_credential=cfg["client_secret"],
        token_cache=cache,
        validate_authority=True,
        enable_pii_log=False,
        app_name="code-assist-notion-outlook",
        app_version="1",
    )


def begin_authorization(user_id: str | None) -> str:
    uid = _uid(user_id)
    cfg = _require_configuration()
    flow = _msal_app(cfg).initiate_auth_code_flow(
        scopes=list(GRAPH_SCOPES),
        redirect_uri=cfg["redirect_uri"],
        prompt="select_account",
        response_mode="query",
    )
    if not isinstance(flow, dict) or not flow.get("auth_uri") or not flow.get("state"):
        raise GraphOAuthError("Microsoft 로그인 흐름을 시작하지 못했습니다")
    record = {
        "created_at": int(time.time()),
        "tenant_id": cfg["tenant_id"],
        "client_id": cfg["client_id"],
        "flow": flow,
    }
    with user_write_lock("outlook_graph_auth", uid):
        _write_encrypted(_flow_path(uid), record)
    return str(flow["auth_uri"])


def _safe_oauth_error(result: dict[str, Any]) -> str:
    code = re.sub(r"[^A-Za-z0-9_.-]", "", str(result.get("error") or "oauth_error"))[:80]
    return f"Microsoft 로그인에 실패했습니다 ({code or 'oauth_error'})"


def _graph_error(response: requests.Response) -> tuple[str, str]:
    code = ""
    try:
        error = (response.json() or {}).get("error") or {}
        code = re.sub(r"[^A-Za-z0-9_.-]", "", str(error.get("code") or ""))[:80]
    except (ValueError, TypeError, AttributeError):
        pass
    suffix = f" ({code})" if code else ""
    return f"Microsoft Graph 요청 실패: HTTP {response.status_code}{suffix}", code


def _validate_graph_url(value: str) -> str:
    url = value if value.startswith("http") else f"{GRAPH_BASE_URL}/{value.lstrip('/')}"
    parsed = urlparse(url)
    try:
        invalid_port = parsed.port not in {None, 443}
    except ValueError:
        invalid_port = True
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "graph.microsoft.com"
        or parsed.username
        or parsed.password
        or invalid_port
    ):
        raise GraphRequestError("허용되지 않은 Microsoft Graph URL입니다")
    if not parsed.path.startswith("/v1.0/"):
        raise GraphRequestError("Microsoft Graph v1.0 URL만 허용됩니다")
    return url


def _request_with_token(
    access_token: str,
    path_or_url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    url = _validate_graph_url(path_or_url)
    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    request_headers.update(headers or {})
    try:
        response = requests.get(
            url,
            params=params,
            headers=request_headers,
            timeout=max(5, min(60, int(timeout or 30))),
        )
    except requests.RequestException as exc:
        raise GraphRequestError(redact_sensitive(f"Microsoft Graph 네트워크 오류: {exc}")) from exc
    if response.status_code == 401:
        raise GraphAuthenticationRequired("Microsoft 로그인이 만료되었습니다. 다시 연결해 주세요.")
    if response.status_code >= 400:
        message, code = _graph_error(response)
        raise GraphRequestError(message, status_code=response.status_code, code=code)
    try:
        data = response.json()
    except ValueError as exc:
        raise GraphRequestError("Microsoft Graph가 올바르지 않은 응답을 반환했습니다") from exc
    if not isinstance(data, dict):
        raise GraphRequestError("Microsoft Graph 응답 형식이 올바르지 않습니다")
    return data


def complete_authorization(user_id: str | None, auth_response: dict[str, Any]) -> dict[str, Any]:
    uid = _uid(user_id)
    cfg = _require_configuration()
    with user_write_lock("outlook_graph_auth", uid):
        record = _read_encrypted(_flow_path(uid))
        _remove_file(_flow_path(uid))
        if not record or not isinstance(record.get("flow"), dict):
            raise GraphOAuthError("Microsoft 로그인 요청이 없거나 만료되었습니다")
        if int(time.time()) - int(record.get("created_at") or 0) > OAUTH_FLOW_TTL_SECONDS:
            raise GraphOAuthError("Microsoft 로그인 요청이 만료되었습니다. 다시 연결해 주세요.")
        if record.get("tenant_id") != cfg["tenant_id"] or record.get("client_id") != cfg["client_id"]:
            raise GraphOAuthError("Microsoft 앱 설정이 로그인 도중 변경되었습니다")
        cache = _new_cache()
        try:
            result = _msal_app(cfg, cache).acquire_token_by_auth_code_flow(
                record["flow"],
                dict(auth_response or {}),
                scopes=list(GRAPH_SCOPES),
            )
        except ValueError as exc:
            raise GraphOAuthError("Microsoft 로그인 state 또는 nonce 검증에 실패했습니다") from exc
        if not isinstance(result, dict) or not result.get("access_token"):
            raise GraphOAuthError(_safe_oauth_error(result if isinstance(result, dict) else {}))
        claims = result.get("id_token_claims") if isinstance(result.get("id_token_claims"), dict) else {}
        if str(claims.get("tid") or "").lower() != cfg["tenant_id"].lower():
            raise GraphOAuthError("허용된 회사 Tenant 계정이 아닙니다")
        inbox = _request_with_token(
            str(result["access_token"]),
            "/me/mailFolders/inbox",
            params={"$select": "totalItemCount,unreadItemCount"},
        )
        accounts = _msal_app(cfg, cache).get_accounts()
        account = accounts[0] if accounts else {}
        token_record = {
            "version": 1,
            "tenant_id": cfg["tenant_id"],
            "client_id": cfg["client_id"],
            "home_account_id": account.get("home_account_id") or "",
            "cache": cache.serialize(),
            "saved_at": int(time.time()),
            "account": {
                "id": str(claims.get("oid") or claims.get("sub") or ""),
                "display_name": str(claims.get("name") or ""),
                "email": str(claims.get("preferred_username") or claims.get("email") or ""),
            },
        }
        _write_encrypted(_token_path(uid), token_record)
    return {
        "available": True,
        "authenticated": True,
        "connector": "microsoft_graph",
        "account": token_record["account"],
        "inbox_count": int(inbox.get("totalItemCount") or 0),
        "unread_count": int(inbox.get("unreadItemCount") or 0),
        "checked_at": int(time.time()),
    }


def _load_token_record(user_id: str) -> dict[str, Any]:
    cfg = _require_configuration()
    record = _read_encrypted(_token_path(user_id))
    if not record:
        raise GraphAuthenticationRequired("Microsoft Outlook 계정을 연결해 주세요")
    if record.get("tenant_id") != cfg["tenant_id"] or record.get("client_id") != cfg["client_id"]:
        raise GraphAuthenticationRequired("Microsoft 앱 설정이 변경되었습니다. Outlook을 다시 연결해 주세요.")
    return record


def _access_token(user_id: str | None) -> str:
    uid = _uid(user_id)
    cfg = _require_configuration()
    with user_write_lock("outlook_graph_auth", uid):
        record = _load_token_record(uid)
        cache = _new_cache(str(record.get("cache") or ""))
        app = _msal_app(cfg, cache)
        account_id = str(record.get("home_account_id") or "")
        accounts = app.get_accounts(home_account_id=account_id) if account_id else app.get_accounts()
        if not accounts:
            raise GraphAuthenticationRequired("Microsoft 토큰 계정을 찾을 수 없습니다. 다시 연결해 주세요.")
        result = app.acquire_token_silent(list(GRAPH_SCOPES), account=accounts[0])
        if not isinstance(result, dict) or not result.get("access_token"):
            raise GraphAuthenticationRequired(_safe_oauth_error(result if isinstance(result, dict) else {}))
        if cache.has_state_changed:
            record["cache"] = cache.serialize()
            record["saved_at"] = int(time.time())
            _write_encrypted(_token_path(uid), record)
        return str(result["access_token"])


def graph_get(
    user_id: str | None,
    path_or_url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    return _request_with_token(
        _access_token(user_id),
        path_or_url,
        params=params,
        headers=headers,
        timeout=timeout,
    )


def disconnect(user_id: str | None) -> dict[str, Any]:
    uid = _uid(user_id)
    account: dict[str, Any] = {}
    with user_write_lock("outlook_graph_auth", uid):
        try:
            account = dict((_read_encrypted(_token_path(uid)).get("account") or {}))
        except GraphAuthenticationRequired:
            account = {}
        _remove_file(_token_path(uid))
        _remove_file(_flow_path(uid))
    return {"disconnected": True, "account": account}


def has_token(user_id: str | None) -> bool:
    try:
        uid = _uid(user_id)
        cfg = graph_configuration()
        if not cfg["configured"]:
            return False
        record = _read_encrypted(_token_path(uid))
        return bool(
            record
            and record.get("tenant_id") == cfg["tenant_id"]
            and record.get("client_id") == cfg["client_id"]
            and record.get("cache")
        )
    except (GraphConfigurationError, GraphAuthenticationRequired):
        return False


def connection_status(user_id: str | None, *, probe: bool = False) -> dict[str, Any]:
    uid = _uid(user_id)
    configuration = public_configuration()
    status: dict[str, Any] = {
        "available": False,
        "authenticated": False,
        "requires_auth": bool(configuration["configured"]),
        "connector": "microsoft_graph",
        "configuration": configuration,
        "account": {},
        "checked_at": int(time.time()),
    }
    if not configuration["configured"]:
        status["error"] = "Microsoft Graph single-tenant 서버 설정이 필요합니다"
        return status
    try:
        record = _load_token_record(uid)
        status["authenticated"] = True
        status["available"] = True
        status["requires_auth"] = False
        status["account"] = dict(record.get("account") or {})
        if probe:
            inbox = graph_get(
                uid,
                "/me/mailFolders/inbox",
                params={"$select": "totalItemCount,unreadItemCount"},
            )
            status["inbox_count"] = int(inbox.get("totalItemCount") or 0)
            status["unread_count"] = int(inbox.get("unreadItemCount") or 0)
    except GraphAuthenticationRequired as exc:
        status["error"] = redact_sensitive(exc)
        status["requires_auth"] = True
    except (GraphConfigurationError, GraphRequestError) as exc:
        status["available"] = False
        status["error"] = redact_sensitive(exc)
    return status


def _recipient_text(items: Any) -> str:
    values: list[str] = []
    for item in items if isinstance(items, list) else []:
        address = (item or {}).get("emailAddress") if isinstance(item, dict) else {}
        address = address if isinstance(address, dict) else {}
        name = str(address.get("name") or "").strip()
        email = str(address.get("address") or "").strip()
        value = f"{name} <{email}>" if name and email and name.lower() != email.lower() else (email or name)
        if value:
            values.append(value)
    return "; ".join(values)


def _attachment_metadata(user_id: str, message_id: str) -> list[dict[str, Any]]:
    if not message_id:
        return []
    data = graph_get(
        user_id,
        f"/me/messages/{quote(message_id, safe='')}/attachments",
        params={"$select": "id,name,size,contentType,isInline"},
    )
    return [
        {
            "name": str((item or {}).get("name") or "attachment")[:500],
            "size": int((item or {}).get("size") or 0),
            "content_type": str((item or {}).get("contentType") or "")[:200],
            "inline": bool((item or {}).get("isInline")),
        }
        for item in (data.get("value") or [])[:50]
        if isinstance(item, dict)
    ]


def _message_body_text(body: dict[str, Any]) -> str:
    content = str(body.get("content") or "")
    content_type = str(body.get("contentType") or "").strip().lower()
    if content_type != "html":
        return content[:50000]
    parser = _MailHtmlTextParser()
    try:
        parser.feed(content)
        parser.close()
        return parser.text()[:50000]
    except (ValueError, TypeError):
        return re.sub(r"<[^>]+>", " ", content)[:50000]


def _normalize_message(user_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("@removed") or not item.get("id"):
        return None
    sender = item.get("from") if isinstance(item.get("from"), dict) else {}
    sender = sender.get("emailAddress") if isinstance(sender.get("emailAddress"), dict) else {}
    importance = {"low": 0, "normal": 1, "high": 2}.get(str(item.get("importance") or "normal").lower(), 1)
    body = item.get("body") if isinstance(item.get("body"), dict) else {}
    attachments = _attachment_metadata(user_id, str(item.get("id") or "")) if item.get("hasAttachments") else []
    return {
        "entry_id": str(item.get("id") or ""),
        "internet_message_id": str(item.get("internetMessageId") or ""),
        "conversation_id": str(item.get("conversationId") or ""),
        "subject": str(item.get("subject") or ""),
        "sender_name": str(sender.get("name") or ""),
        "sender_address": str(sender.get("address") or ""),
        "to": _recipient_text(item.get("toRecipients")),
        "cc": _recipient_text(item.get("ccRecipients")),
        "received_at": str(item.get("receivedDateTime") or ""),
        "modified_at": str(item.get("lastModifiedDateTime") or ""),
        "unread": not bool(item.get("isRead")),
        "importance": importance,
        "body": _message_body_text(body),
        "attachments": attachments,
    }


def fetch_delta(
    user_id: str | None,
    *,
    since_utc: str,
    limit: int,
    cursor_url: str = "",
) -> dict[str, Any]:
    uid = _uid(user_id)
    page_size = max(1, min(100, int(limit or 30)))
    headers = {
        "Prefer": f'outlook.body-content-type="text", IdType="ImmutableId", odata.maxpagesize={page_size}',
    }
    params = None
    target = cursor_url or "/me/mailFolders/inbox/messages/delta"
    if not cursor_url:
        params = {
            "$select": _MESSAGE_SELECT,
            "$filter": f"receivedDateTime ge {since_utc}",
            "$orderby": "receivedDateTime desc",
            "$top": str(page_size),
        }
    cursor_reset = False
    try:
        data = graph_get(uid, target, params=params, headers=headers, timeout=45)
    except GraphRequestError as exc:
        if not cursor_url or exc.status_code != 410:
            raise
        # Delta tokens can expire. Resume from the bounded lookback window rather
        # than leaving the user's scheduler permanently stuck on a stale cursor.
        cursor_reset = True
        data = graph_get(
            uid,
            "/me/mailFolders/inbox/messages/delta",
            params={
                "$select": _MESSAGE_SELECT,
                "$filter": f"receivedDateTime ge {since_utc}",
                "$orderby": "receivedDateTime desc",
                "$top": str(page_size),
            },
            headers=headers,
            timeout=45,
        )
    messages: list[dict[str, Any]] = []
    for item in data.get("value") or []:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_message(uid, item)
        if normalized:
            messages.append(normalized)
    next_link = str(data.get("@odata.nextLink") or "")
    delta_link = str(data.get("@odata.deltaLink") or "")
    return {
        "available": True,
        "connector": "microsoft_graph",
        "messages": messages,
        "total_count": len(messages),
        "cursor_url": next_link or delta_link,
        "cursor_complete": bool(delta_link and not next_link),
        "cursor_reset": cursor_reset,
    }


def fetch_unread(user_id: str | None, *, limit: int) -> dict[str, Any]:
    uid = _uid(user_id)
    page_size = max(1, min(500, int(limit or 100)))
    inbox = graph_get(uid, "/me/mailFolders/inbox", params={"$select": "totalItemCount,unreadItemCount"})
    data = graph_get(
        uid,
        "/me/mailFolders/inbox/messages",
        params={
            "$select": _MESSAGE_SELECT,
            "$filter": "isRead eq false",
            "$orderby": "receivedDateTime desc",
            "$top": str(page_size),
        },
        headers={"Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"'},
        timeout=45,
    )
    messages: list[dict[str, Any]] = []
    for item in data.get("value") or []:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_message(uid, item)
        if normalized:
            messages.append(normalized)
    return {
        "available": True,
        "connector": "microsoft_graph",
        "messages": messages,
        "total_count": int(inbox.get("unreadItemCount") or len(messages)),
        "inbox_count": int(inbox.get("totalItemCount") or 0),
    }
