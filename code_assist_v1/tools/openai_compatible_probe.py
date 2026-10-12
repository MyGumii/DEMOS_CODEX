"""Bounded compatibility probe for company OpenAI-compatible chat endpoints.

The probe is intentionally independent from the application process.  It reads
only model metadata from an explicit JSON document, reads a bearer token only
from an explicitly named environment variable, and never records prompt or
response bodies in its report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests


REPORT_SCHEMA = "code-assist.openai-compatible-provider-probe"
REPORT_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_CHECK_NAMES = ("nonstream", "stream", "json_object")
_ERROR_CLASSES = {
    "timeout",
    "connection_error",
    "request_error",
    "response_shape",
    "internal_error",
}


class ProbeConfigurationError(ValueError):
    """The explicit probe input is malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class ModelTarget:
    registry_id: str
    model: str
    url: str


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProbeConfigurationError(f"duplicate JSON member: {key}")
        value[key] = item
    return value


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _safe_identifier(value: Any, *, field: str) -> str:
    text = str(value or "")
    if not _SAFE_ID_RE.fullmatch(text) or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"}
        for character in text
    ):
        raise ProbeConfigurationError(f"{field} is not a safe identifier")
    return text


def _contains_embedded_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            segments = set(re.split(r"[^a-z0-9]+", normalized))
            if (
                normalized in {
                    "api_key",
                    "auth",
                    "authorization",
                    "bearer_token",
                    "password",
                    "private_key",
                    "secret",
                    "token",
                }
                or "password" in segments
                or "secret" in segments
                or "token" in segments
            ):
                return True
            if _contains_embedded_secret(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_embedded_secret(item) for item in value)
    return False


def load_targets(config_path: Path, registry_ids: Iterable[str]) -> tuple[str, tuple[ModelTarget, ...]]:
    try:
        raw = config_path.read_bytes()
    except OSError as exc:
        raise ProbeConfigurationError("configuration file is unavailable") from exc
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_members)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeConfigurationError("configuration must be duplicate-free UTF-8 JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"models"}
        or not isinstance(document.get("models"), dict)
    ):
        raise ProbeConfigurationError("configuration must contain a models object")
    if _contains_embedded_secret(document):
        raise ProbeConfigurationError("configuration contains embedded credential data")

    requested = tuple(_safe_identifier(item, field="registry_id") for item in registry_ids)
    if not requested or len(set(requested)) != len(requested):
        raise ProbeConfigurationError("at least one unique registry_id is required")

    targets: list[ModelTarget] = []
    models = document["models"]
    for registry_id in requested:
        entry = models.get(registry_id)
        if not isinstance(entry, dict):
            raise ProbeConfigurationError(f"unknown registry_id: {registry_id}")
        runtime = str(entry.get("runtime") or "api").lower()
        if runtime != "api":
            raise ProbeConfigurationError(f"registry_id is not an API model: {registry_id}")
        if set(entry) != {"runtime", "model", "url"}:
            raise ProbeConfigurationError(
                f"models.{registry_id} must contain exactly runtime, model, and url"
            )
        model = _safe_identifier(entry.get("model"), field=f"models.{registry_id}.model")
        url = str(entry.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProbeConfigurationError(f"models.{registry_id}.url is not HTTP(S)")
        if not parsed.path.rstrip("/").endswith("/v1/chat/completions"):
            raise ProbeConfigurationError(
                f"models.{registry_id}.url must target /v1/chat/completions"
            )
        if (
            parsed.username
            or parsed.password
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ProbeConfigurationError(f"models.{registry_id}.url contains forbidden authority data")
        targets.append(ModelTarget(registry_id=registry_id, model=model, url=url))
    return _sha256_bytes(raw), tuple(targets)


def _headers(token: str) -> dict[str, str]:
    result = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        result["Authorization"] = f"Bearer {token}"
    return result


def _payload(model: str, marker: str, *, stream: bool, json_mode: bool = False) -> dict[str, Any]:
    if json_mode:
        instruction = (
            "Return exactly one compact JSON object with keys marker and ok, no markdown. "
            f"marker must equal {marker!r} and ok must be true."
        )
    else:
        instruction = f"Return exactly this marker and nothing else: {marker}"
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a bounded provider compatibility probe."},
            {"role": "user", "content": instruction},
        ],
        "temperature": 0,
        "max_tokens": 96,
        "stream": stream,
        "tool_choice": "none",
    }


def _public_error(exc: BaseException) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.RequestException):
        return "request_error"
    if isinstance(exc, (KeyError, TypeError, ValueError, json.JSONDecodeError)):
        return "response_shape"
    return "internal_error"


def _post_nonstream(
    session: requests.Session,
    target: ModelTarget,
    *,
    token: str,
    timeout_s: float,
    verify_tls: bool,
    marker: str,
    json_mode: bool,
) -> dict[str, Any]:
    response = session.post(
        target.url,
        headers=_headers(token),
        json=_payload(target.model, marker, stream=False, json_mode=json_mode),
        timeout=timeout_s,
        verify=verify_tls,
    )
    status = int(response.status_code)
    response.raise_for_status()
    document = response.json()
    content = document["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty assistant content")
    if json_mode:
        parsed = json.loads(content)
        passed = isinstance(parsed, dict) and parsed == {"marker": marker, "ok": True}
    else:
        passed = content.strip() == marker
    return {
        "status_code": status,
        "content_sha256": _sha256_text(content),
        "passed": passed,
    }


def _post_stream(
    session: requests.Session,
    target: ModelTarget,
    *,
    token: str,
    timeout_s: float,
    verify_tls: bool,
    marker: str,
) -> dict[str, Any]:
    response = session.post(
        target.url,
        headers={**_headers(token), "Accept": "text/event-stream"},
        json=_payload(target.model, marker, stream=True),
        stream=True,
        timeout=timeout_s,
        verify=verify_tls,
    )
    status = int(response.status_code)
    response.raise_for_status()
    pieces: list[str] = []
    saw_done = False
    event_count = 0
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data:"):
            continue
        event_count += 1
        chunk = raw_line[5:].strip()
        if chunk == "[DONE]":
            saw_done = True
            break
        if not chunk:
            continue
        event = json.loads(chunk)
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            pieces.append(delta["content"])
    content = "".join(pieces)
    return {
        "status_code": status,
        "event_count": event_count,
        "saw_done": saw_done,
        "content_sha256": _sha256_text(content),
        "passed": saw_done and content.strip() == marker,
    }


def probe_targets(
    *,
    config_path: Path,
    registry_ids: Iterable[str],
    token_env: str | None,
    timeout_s: float,
    verify_tls: bool,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    if timeout_s <= 0 or timeout_s > 300:
        raise ProbeConfigurationError("timeout must be greater than 0 and at most 300 seconds")
    token_name = str(token_env or "")
    if token_name and not _ENV_NAME_RE.fullmatch(token_name):
        raise ProbeConfigurationError("token environment variable name is invalid")
    token = os.environ.get(token_name, "") if token_name else ""
    if token_name and not token:
        raise ProbeConfigurationError("configured token environment variable is unavailable")
    config_sha256, targets = load_targets(config_path, registry_ids)
    client = session or requests.Session()
    owns_client = session is None

    rows: list[dict[str, Any]] = []
    try:
        for ordinal, target in enumerate(targets, start=1):
            marker = f"CODEASSIST-PROBE-{ordinal:02d}-{_sha256_text(target.registry_id)[:12]}"
            checks: dict[str, Any] = {}
            for name, operation in (
            (
                "nonstream",
                lambda: _post_nonstream(
                    client,
                    target,
                    token=token,
                    timeout_s=timeout_s,
                    verify_tls=verify_tls,
                    marker=marker,
                    json_mode=False,
                ),
            ),
            (
                "stream",
                lambda: _post_stream(
                    client,
                    target,
                    token=token,
                    timeout_s=timeout_s,
                    verify_tls=verify_tls,
                    marker=marker,
                ),
            ),
            (
                "json_object",
                lambda: _post_nonstream(
                    client,
                    target,
                    token=token,
                    timeout_s=timeout_s,
                    verify_tls=verify_tls,
                    marker=marker,
                    json_mode=True,
                ),
            ),
            ):
                try:
                    checks[name] = operation()
                except Exception as exc:  # public report is intentionally body-free
                    checks[name] = {"passed": False, "error_class": _public_error(exc)}
            rows.append(
                {
                    "ordinal": ordinal,
                    "registry_id": target.registry_id,
                    "model": target.model,
                    "endpoint_sha256": _sha256_text(target.url),
                    "checks": checks,
                    "passed": all(bool(item.get("passed")) for item in checks.values()),
                }
            )
    finally:
        if owns_client:
            client.close()

    report = {
        "report_schema": REPORT_SCHEMA,
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "config_sha256": config_sha256,
        "token_source": f"env:{token_name}" if token_name else "none",
        "token_present": bool(token),
        "tls_verification": "enabled" if verify_tls else "explicitly_disabled",
        "models": rows,
        "summary": {
            "model_count": len(rows),
            "passed_count": sum(1 for row in rows if row["passed"]),
            "failed_count": sum(1 for row in rows if not row["passed"]),
            "passed": bool(rows) and all(row["passed"] for row in rows),
        },
    }
    report["report_sha256"] = _sha256_bytes(_canonical_bytes(report))
    return report


def _nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_probe_check(value: Any, *, stream: bool) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("passed"), bool):
        return False
    if "error_class" in value:
        return (
            set(value) == {"passed", "error_class"}
            and value["passed"] is False
            and value["error_class"] in _ERROR_CLASSES
        )
    expected = {"status_code", "content_sha256", "passed"}
    if stream:
        expected |= {"event_count", "saw_done"}
    if set(value) != expected:
        return False
    status_code = value.get("status_code")
    content_sha256 = value.get("content_sha256")
    if (
        not isinstance(status_code, int)
        or isinstance(status_code, bool)
        or status_code < 100
        or status_code >= 400
        or not isinstance(content_sha256, str)
        or not _SHA256_RE.fullmatch(content_sha256)
    ):
        return False
    if stream and (
        not _nonnegative_integer(value.get("event_count"))
        or not isinstance(value.get("saw_done"), bool)
    ):
        return False
    return True


def report_contract_errors(
    value: Any, *, expected_config_sha256: str | None = None
) -> tuple[str, ...]:
    errors: set[str] = set()
    if not isinstance(value, dict):
        return ("report_shape",)
    if set(value) != {
        "report_schema",
        "schema_version",
        "generated_at_utc",
        "config_sha256",
        "token_source",
        "token_present",
        "tls_verification",
        "models",
        "summary",
        "report_sha256",
    }:
        errors.add("report_keys")
    if (
        value.get("report_schema") != REPORT_SCHEMA
        or value.get("schema_version") != REPORT_SCHEMA_VERSION
    ):
        errors.add("report_identity")
    generated_at = value.get("generated_at_utc")
    try:
        if not isinstance(generated_at, str) or not generated_at.endswith("Z"):
            raise ValueError
        datetime.fromisoformat(generated_at[:-1] + "+00:00")
    except (TypeError, ValueError):
        errors.add("report_time")
    config_sha256 = value.get("config_sha256")
    if not isinstance(config_sha256, str) or not _SHA256_RE.fullmatch(config_sha256):
        errors.add("report_config_hash")
    if expected_config_sha256 is not None and config_sha256 != expected_config_sha256:
        errors.add("report_config_binding")
    token_source = value.get("token_source")
    if token_source != "none":
        if not isinstance(token_source, str) or not token_source.startswith("env:"):
            errors.add("report_token_source")
        elif not _ENV_NAME_RE.fullmatch(token_source[4:]):
            errors.add("report_token_source")
    if not isinstance(value.get("token_present"), bool):
        errors.add("report_token_presence")
    elif (
        (token_source == "none" and value.get("token_present") is not False)
        or (
            isinstance(token_source, str)
            and token_source.startswith("env:")
            and value.get("token_present") is not True
        )
    ):
        errors.add("report_token_presence")
    if value.get("tls_verification") not in {"enabled", "explicitly_disabled"}:
        errors.add("report_tls")

    models = value.get("models")
    summary = value.get("summary")
    passed_count = 0
    if not isinstance(models, list) or not isinstance(summary, dict):
        errors.add("report_collections")
    else:
        registry_ids: list[str] = []
        for expected_ordinal, row in enumerate(models, start=1):
            if not isinstance(row, dict) or set(row) != {
                "ordinal",
                "registry_id",
                "model",
                "endpoint_sha256",
                "checks",
                "passed",
            }:
                errors.add("report_model_shape")
                continue
            if row.get("ordinal") != expected_ordinal:
                errors.add("report_model_order")
            try:
                registry_id = _safe_identifier(
                    row.get("registry_id"), field="models.registry_id"
                )
                _safe_identifier(row.get("model"), field="models.model")
            except ProbeConfigurationError:
                registry_id = ""
                errors.add("report_model_shape")
            registry_ids.append(registry_id)
            endpoint_sha256 = row.get("endpoint_sha256")
            if not isinstance(endpoint_sha256, str) or not _SHA256_RE.fullmatch(
                endpoint_sha256
            ):
                errors.add("report_model_shape")
            checks = row.get("checks")
            if not isinstance(checks, dict) or set(checks) != set(_CHECK_NAMES):
                errors.add("report_check_shape")
                checks = {}
            else:
                for check_name in _CHECK_NAMES:
                    if not _valid_probe_check(
                        checks.get(check_name), stream=check_name == "stream"
                    ):
                        errors.add("report_check_shape")
            expected_passed = bool(checks) and all(
                isinstance(checks.get(name), dict)
                and checks[name].get("passed") is True
                for name in _CHECK_NAMES
            )
            if row.get("passed") is not expected_passed:
                errors.add("report_model_passed")
            if row.get("passed") is True:
                passed_count += 1
        if len(registry_ids) != len(set(registry_ids)):
            errors.add("report_model_identity")
        if set(summary) != {
            "model_count",
            "passed_count",
            "failed_count",
            "passed",
        }:
            errors.add("report_summary")
        for key in ("model_count", "passed_count", "failed_count"):
            if not _nonnegative_integer(summary.get(key)):
                errors.add("report_summary")
        if (
            summary.get("model_count") != len(models)
            or summary.get("passed_count") != passed_count
            or summary.get("failed_count") != len(models) - passed_count
            or summary.get("passed") is not (
                bool(models) and passed_count == len(models)
            )
        ):
            errors.add("report_summary")
    claimed = value.get("report_sha256")
    if not isinstance(claimed, str) or not _SHA256_RE.fullmatch(claimed):
        errors.add("report_hash_shape")
    else:
        payload = dict(value)
        payload.pop("report_sha256", None)
        if claimed != _sha256_bytes(_canonical_bytes(payload)):
            errors.add("report_hash_binding")
    return tuple(sorted(errors))


def _write_create_only(path: Path, payload: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_duplicate_free_json(path: Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProbeConfigurationError(f"{label} file is unavailable") from exc
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_members
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeConfigurationError(
            f"{label} must be duplicate-free UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProbeConfigurationError(f"{label} must be a JSON object")
    return raw, value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--token-env")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.verify_report:
            if args.models or args.token_env or args.insecure or args.output:
                raise ProbeConfigurationError(
                    "report verification accepts only --config and --verify-report"
                )
            config_raw, config_document = _read_duplicate_free_json(
                args.config, label="configuration"
            )
            if (
                set(config_document) != {"models"}
                or not isinstance(config_document.get("models"), dict)
                or _contains_embedded_secret(config_document)
            ):
                raise ProbeConfigurationError(
                    "configuration is not a sanitized probe document"
                )
            report_raw, report = _read_duplicate_free_json(
                args.verify_report, label="report"
            )
            expected_config_sha256 = _sha256_bytes(config_raw)
            errors = set(
                report_contract_errors(
                    report, expected_config_sha256=expected_config_sha256
                )
            )
            config_models = config_document["models"]
            report_models = report.get("models")
            if isinstance(report_models, list):
                for row in report_models:
                    if not isinstance(row, dict):
                        continue
                    registry_id = row.get("registry_id")
                    entry = config_models.get(registry_id)
                    if (
                        not isinstance(entry, dict)
                        or set(entry) != {"runtime", "model", "url"}
                        or str(entry.get("runtime") or "").lower() != "api"
                        or entry.get("model") != row.get("model")
                        or _sha256_text(str(entry.get("url") or ""))
                        != row.get("endpoint_sha256")
                    ):
                        errors.add("report_config_binding")
            if report_raw != _canonical_bytes(report):
                errors.add("report_document_canonical")
            result = {
                "status": "pass" if not errors else "fail",
                "config_sha256": expected_config_sha256,
                "report_document_sha256": _sha256_bytes(report_raw),
                "error_codes": sorted(errors),
            }
            sys.stdout.buffer.write(_canonical_bytes(result))
            return 0 if not errors else 2
        if not args.models:
            raise ProbeConfigurationError("at least one --model is required")
        report = probe_targets(
            config_path=args.config,
            registry_ids=args.models,
            token_env=args.token_env,
            timeout_s=args.timeout,
            verify_tls=not args.insecure,
        )
        errors = report_contract_errors(report)
        if errors:
            raise ProbeConfigurationError("generated report failed its own contract")
        payload = _canonical_bytes(report)
        if args.output:
            _write_create_only(args.output, payload)
        else:
            sys.stdout.buffer.write(payload)
        return 0 if report["summary"]["passed"] else 2
    except ProbeConfigurationError as exc:
        print(json.dumps({"error": "PROBE_CONFIGURATION_ERROR", "message": str(exc)}), file=sys.stderr)
        return 3
    except FileExistsError:
        print(json.dumps({"error": "OUTPUT_EXISTS"}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
