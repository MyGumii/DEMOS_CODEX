"""Secret-free audit for the company runtime model registry.

This audit does not contact a model endpoint.  It proves that the external
``CODE_ASSIST_API_CONFIG`` document expresses the reviewed automatic route:
Gemma-4, Qwen3.6, Kimi-K3, GLM-5.2, local GGUF, and no configured Codex lane.
The application adds Codex dynamically and last only when ``--codex`` is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPORT_SCHEMA = "code-assist.company-provider-registry-audit"
REPORT_SCHEMA_VERSION = 1
COMPANY_API_ORDER = (
    "gemma4-company",
    "qwen36-company",
    "kimi-k3-company",
    "glm52-company",
)
COMPANY_GGUF_ID = "gguf-gemma4-31b-q4-k-xl"
EXPECTED_MODEL_PRIORITY = (*COMPANY_API_ORDER, COMPANY_GGUF_ID)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_FORBIDDEN_LITERAL_KEYS = {
    "api_key",
    "auth",
    "authorization",
    "bearer_token",
    "password",
    "secret",
    "token",
}


class ProviderRegistryAuditError(ValueError):
    """The runtime registry cannot be safely or unambiguously audited."""


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderRegistryAuditError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _safe_text(value: Any, *, field: str, pattern: re.Pattern[str]) -> str:
    text = value if isinstance(value, str) else ""
    if not pattern.fullmatch(text) or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"}
        for character in text
    ):
        raise ProviderRegistryAuditError(f"{field} is not a safe identifier")
    return text


def _load_document(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProviderRegistryAuditError("configuration file is unavailable") from exc
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_reject_duplicate_members)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderRegistryAuditError(
            "configuration must be duplicate-free UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProviderRegistryAuditError("configuration must be a JSON object")
    return raw, value


def _runtime_kind(entry: dict[str, Any]) -> str:
    runtime = str(entry.get("runtime") or "api").strip().lower()
    env_id = str(entry.get("env_id") or "")
    if runtime == "codex" or env_id == "codex-local":
        return "codex"
    if runtime == "gguf" or env_id.startswith("gguf-"):
        return "gguf"
    return "api"


def _enabled(entry: dict[str, Any]) -> bool:
    return entry.get("enabled") is not False and entry.get("disabled") is not True


def _literal_secret_present(entry: dict[str, Any]) -> bool:
    return any(key.strip().lower() in _FORBIDDEN_LITERAL_KEYS for key in entry)


def _contains_embedded_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if not normalized.endswith(("_env", "_file", "_source")):
                segments = set(re.split(r"[^a-z0-9]+", normalized))
                if (
                    normalized in _FORBIDDEN_LITERAL_KEYS
                    or "password" in segments
                    or "secret" in segments
                    or "token" in segments
                    or normalized in {"api_key_value", "private_key"}
                ):
                    return True
            if _contains_embedded_secret(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_embedded_secret(item) for item in value)
    return False


def audit_runtime_registry(config_path: Path) -> dict[str, Any]:
    raw, document = _load_document(config_path)
    errors: set[str] = set()
    models = document.get("models")
    if not isinstance(models, dict):
        raise ProviderRegistryAuditError("configuration must contain a models object")
    if _contains_embedded_secret(document):
        errors.add("embedded_literal_secret")

    observed_priority = document.get("default_model_priority")
    if not isinstance(observed_priority, list) or not all(
        isinstance(item, str) for item in observed_priority
    ):
        errors.add("priority_shape")
        public_priority: list[str] = []
    else:
        public_priority = list(observed_priority)
        if len(set(public_priority)) != len(public_priority):
            errors.add("priority_duplicate")
        if tuple(public_priority) != EXPECTED_MODEL_PRIORITY:
            errors.add("priority_order")
        if "codex-local" in public_priority:
            errors.add("codex_in_default_priority")

    gguf_settings = document.get("gguf")
    if not isinstance(gguf_settings, dict):
        errors.add("gguf_settings_missing")
    elif gguf_settings.get("auto_register_unconfigured") is not False:
        errors.add("gguf_auto_registration_enabled")

    rows: list[dict[str, Any]] = []
    auth_env_names: set[str] = set()
    for registry_id in EXPECTED_MODEL_PRIORITY:
        entry = models.get(registry_id)
        expected_kind = "gguf" if registry_id == COMPANY_GGUF_ID else "api"
        if not isinstance(entry, dict):
            errors.add(f"missing_model:{registry_id}")
            continue
        kind = _runtime_kind(entry)
        if kind != expected_kind:
            errors.add(f"model_kind:{registry_id}")
        if not _enabled(entry):
            errors.add(f"model_disabled:{registry_id}")
        if _literal_secret_present(entry):
            errors.add(f"literal_secret:{registry_id}")

        try:
            server_model = _safe_text(
                entry.get("model"),
                field=f"models.{registry_id}.model",
                pattern=_MODEL_ID_RE,
            )
        except ProviderRegistryAuditError:
            server_model = ""
            errors.add(f"model_id:{registry_id}")

        row: dict[str, Any] = {
            "registry_id": registry_id,
            "kind": kind,
            "server_model_id": server_model,
            "enabled": _enabled(entry),
        }
        if expected_kind == "api":
            url = entry.get("url") if isinstance(entry.get("url"), str) else ""
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or not parsed.path.rstrip("/").endswith("/v1/chat/completions")
                or parsed.username
                or parsed.password
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                errors.add(f"api_endpoint:{registry_id}")
            token_env = entry.get("api_key_env")
            try:
                token_env = _safe_text(
                    token_env,
                    field=f"models.{registry_id}.api_key_env",
                    pattern=_ENV_NAME_RE,
                )
            except ProviderRegistryAuditError:
                token_env = ""
                errors.add(f"api_token_env:{registry_id}")
            if token_env:
                auth_env_names.add(token_env)
            if any(
                key in entry
                for key in (
                    "api_key_file",
                    "token_file",
                    "auth_file",
                    "api_key_source",
                    "token_source",
                    "auth_source",
                )
            ):
                errors.add(f"api_auth_indirection:{registry_id}")
            row.update(
                {
                    "endpoint_scheme": parsed.scheme,
                    "endpoint_sha256": _sha256_text(url) if url else None,
                    "token_env": token_env,
                }
            )
        else:
            runtime = entry.get("gguf")
            if not isinstance(runtime, dict):
                errors.add("gguf_model_settings_missing")
                model_path_env = ""
            else:
                model_path_env = runtime.get("model_path_env")
                try:
                    model_path_env = _safe_text(
                        model_path_env,
                        field=f"models.{registry_id}.gguf.model_path_env",
                        pattern=_ENV_NAME_RE,
                    )
                except ProviderRegistryAuditError:
                    model_path_env = ""
                    errors.add("gguf_model_path_env")
                if runtime.get("model_path"):
                    errors.add("gguf_literal_model_path")
            row["model_path_env"] = model_path_env
        rows.append(row)

    if len(auth_env_names) != 1:
        errors.add("api_token_env_consistency")

    expected_ids = set(EXPECTED_MODEL_PRIORITY)
    for registry_id, entry in models.items():
        if not isinstance(registry_id, str) or not isinstance(entry, dict):
            errors.add("model_collection_shape")
            continue
        if registry_id in expected_ids or not _enabled(entry):
            continue
        kind = _runtime_kind(entry)
        if kind == "codex":
            errors.add("configured_codex_lane")
        elif kind in {"api", "gguf"}:
            errors.add(f"extra_enabled_model:{registry_id}")

    sorted_errors = sorted(errors)
    report: dict[str, Any] = {
        "report_schema": REPORT_SCHEMA,
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "config_sha256": _sha256_bytes(raw),
        "expected_model_priority": list(EXPECTED_MODEL_PRIORITY),
        "observed_model_priority": public_priority,
        "models": rows,
        "errors": sorted_errors,
        "summary": {
            "expected_model_count": len(EXPECTED_MODEL_PRIORITY),
            "observed_model_count": len(rows),
            "api_model_count": sum(1 for row in rows if row["kind"] == "api"),
            "gguf_model_count": sum(1 for row in rows if row["kind"] == "gguf"),
            "error_count": len(sorted_errors),
            "passed": not sorted_errors,
        },
    }
    report["report_sha256"] = _sha256_bytes(_canonical_bytes(report))
    return report


def report_contract_errors(value: Any) -> tuple[str, ...]:
    errors: set[str] = set()
    if not isinstance(value, dict):
        return ("report_shape",)
    if (
        value.get("report_schema") != REPORT_SCHEMA
        or value.get("schema_version") != REPORT_SCHEMA_VERSION
    ):
        errors.add("report_identity")
    if set(value) != {
        "report_schema",
        "schema_version",
        "generated_at_utc",
        "config_sha256",
        "expected_model_priority",
        "observed_model_priority",
        "models",
        "errors",
        "summary",
        "report_sha256",
    }:
        errors.add("report_keys")
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
    rows = value.get("models")
    policy_errors = value.get("errors")
    summary = value.get("summary")
    if (
        not isinstance(rows, list)
        or not isinstance(policy_errors, list)
        or not isinstance(summary, dict)
    ):
        errors.add("report_collections")
    else:
        if not all(isinstance(item, str) for item in policy_errors):
            errors.add("report_policy_errors")
        elif policy_errors != sorted(set(policy_errors)):
            errors.add("report_policy_errors")
        row_ids: list[str] = []
        api_count = 0
        gguf_count = 0
        for row in rows:
            if not isinstance(row, dict):
                errors.add("report_model_shape")
                continue
            registry_id = row.get("registry_id")
            kind = row.get("kind")
            row_ids.append(registry_id if isinstance(registry_id, str) else "")
            common_keys = {"registry_id", "kind", "server_model_id", "enabled"}
            expected_kind = (
                "gguf" if registry_id == COMPANY_GGUF_ID else "api"
            )
            if kind == "api":
                api_count += 1
            elif kind == "gguf":
                gguf_count += 1
            if expected_kind == "api":
                expected_keys = common_keys | {
                    "endpoint_scheme",
                    "endpoint_sha256",
                    "token_env",
                }
                endpoint_sha256 = row.get("endpoint_sha256")
                if not isinstance(row.get("endpoint_scheme"), str):
                    errors.add("report_model_shape")
                if endpoint_sha256 is not None and (
                    not isinstance(endpoint_sha256, str)
                    or not _SHA256_RE.fullmatch(endpoint_sha256)
                ):
                    errors.add("report_model_shape")
                if not isinstance(row.get("token_env"), str):
                    errors.add("report_model_shape")
            else:
                expected_keys = common_keys | {"model_path_env"}
                if not isinstance(row.get("model_path_env"), str):
                    errors.add("report_model_shape")
            if set(row) != expected_keys:
                errors.add("report_model_shape")
            if (
                not isinstance(registry_id, str)
                or registry_id not in EXPECTED_MODEL_PRIORITY
                or not isinstance(row.get("server_model_id"), str)
                or not isinstance(row.get("kind"), str)
                or not isinstance(row.get("enabled"), bool)
            ):
                errors.add("report_model_shape")
        expected_row_order = [
            item for item in EXPECTED_MODEL_PRIORITY if item in set(row_ids)
        ]
        if row_ids != expected_row_order or len(row_ids) != len(set(row_ids)):
            errors.add("report_model_order")
        if summary.get("expected_model_count") != len(EXPECTED_MODEL_PRIORITY):
            errors.add("report_summary")
        if summary.get("observed_model_count") != len(rows):
            errors.add("report_summary")
        if summary.get("api_model_count") != api_count:
            errors.add("report_summary")
        if summary.get("gguf_model_count") != gguf_count:
            errors.add("report_summary")
        if summary.get("error_count") != len(policy_errors):
            errors.add("report_summary")
        if summary.get("passed") is not (len(policy_errors) == 0):
            errors.add("report_summary")
    if value.get("expected_model_priority") != list(EXPECTED_MODEL_PRIORITY):
        errors.add("report_expected_priority")
    observed_priority = value.get("observed_model_priority")
    if not isinstance(observed_priority, list) or not all(
        isinstance(item, str) for item in observed_priority
    ):
        errors.add("report_observed_priority")
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
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            resolved.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_runtime_registry(args.config)
        if report_contract_errors(report):
            raise ProviderRegistryAuditError(
                "generated report failed its own contract"
            )
        payload = _canonical_bytes(report)
        if args.output:
            _write_create_only(args.output, payload)
        else:
            sys.stdout.buffer.write(payload)
        return 0 if report["summary"]["passed"] else 2
    except ProviderRegistryAuditError as exc:
        print(
            json.dumps(
                {"error": "PROVIDER_REGISTRY_AUDIT_ERROR", "message": str(exc)}
            ),
            file=sys.stderr,
        )
        return 3
    except FileExistsError:
        print(json.dumps({"error": "OUTPUT_EXISTS"}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
