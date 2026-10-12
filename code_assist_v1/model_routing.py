"""Deterministic model-attempt ordering for local and company deployments.

The model registry remains deployment-owned.  This module only freezes the
runtime lanes: configured APIs first, local GGUF second, and the optional
Codex bridge last.  It deliberately does not contain company model names or
endpoint URLs.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from typing import Any


MODEL_KINDS = ("api", "gguf", "codex")


def codex_fallback_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get("CODE_ASSIST_ENABLE_CODEX_BRIDGE") or "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def apply_codex_launch_policy(
    enabled: bool,
    *,
    environ: dict[str, str] | None = None,
) -> None:
    """Apply ``--codex`` semantics without changing the default model."""

    target = os.environ if environ is None else environ
    target["CODE_ASSIST_ENABLE_CODEX_BRIDGE"] = "1" if enabled else "0"
    if target.get("CODE_ASSIST_DEFAULT_MODEL") == "codex-local":
        target.pop("CODE_ASSIST_DEFAULT_MODEL", None)


def model_kind(config: Mapping[str, Any]) -> str:
    runtime = str(config.get("runtime") or "").strip().lower()
    env_id = str(config.get("env_id") or "").strip()
    if runtime == "codex" or env_id == "codex-local":
        return "codex"
    if runtime == "gguf" or env_id.startswith("gguf-"):
        return "gguf"
    return "api"


def model_enabled(config: Mapping[str, Any] | None) -> bool:
    return bool(config) and config.get("enabled") is not False and not bool(
        config.get("disabled")
    )


def merged_model_config(
    model_id: str,
    *,
    registry: Mapping[str, Mapping[str, Any]],
    environment: Mapping[str, Mapping[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    """Resolve a registry ID or environment alias to one canonical config."""

    if model_id in registry:
        registry_config = dict(registry.get(model_id) or {})
        env_id = str(registry_config.get("env_id") or model_id)
        config = {**registry_config, **dict(environment.get(env_id) or {})}
        config.setdefault("env_id", env_id)
        config.setdefault("_registry_id", model_id)
        return model_id, config

    if model_id in environment:
        config = dict(environment.get(model_id) or {})
        registry_id = str(config.get("_registry_id") or "")
        if registry_id and registry_id in registry:
            registry_config = dict(registry.get(registry_id) or {})
            config = {**registry_config, **config}
            config.setdefault("_registry_id", registry_id)
            config.setdefault("env_id", model_id)
            return registry_id, config
        config.setdefault("env_id", model_id)
        return model_id, config

    for registry_id, raw in registry.items():
        if str((raw or {}).get("env_id") or "") == model_id:
            return merged_model_config(
                registry_id,
                registry=registry,
                environment=environment,
            )
    return None


def _identity(canonical_id: str, config: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        model_kind(config),
        str(config.get("_registry_id") or canonical_id),
        str(config.get("env_id") or ""),
        str(config.get("model") or ""),
        str(config.get("url") or ""),
    )


def ordered_model_candidates(
    *,
    requested_model_id: str | None,
    priority_ids: Sequence[str],
    registry: Mapping[str, Mapping[str, Any]],
    environment: Mapping[str, Mapping[str, Any]],
    codex_enabled: bool,
    include_fallback: bool,
) -> list[str]:
    """Return unique model IDs in the frozen attempt order.

    A caller-selected model is first because the UI explicitly promises a
    manual override.  All remaining enabled models are stable-partitioned into
    API, GGUF, and Codex lanes.  Codex is omitted entirely unless launch-time
    policy enabled the bridge.
    """

    raw_ids: list[str] = []
    for value in (*priority_ids, *registry.keys(), *environment.keys()):
        candidate = str(value or "").strip()
        if candidate and candidate not in raw_ids:
            raw_ids.append(candidate)

    requested = str(requested_model_id or "").strip()
    resolved: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[tuple[str, ...]] = set()

    def add(model_id: str) -> None:
        item = merged_model_config(
            model_id,
            registry=registry,
            environment=environment,
        )
        if item is None:
            return
        canonical_id, config = item
        if not model_enabled(config):
            return
        kind = model_kind(config)
        if kind == "codex" and not codex_enabled:
            return
        identity = _identity(canonical_id, config)
        if identity in seen:
            return
        seen.add(identity)
        resolved.append((canonical_id, kind, config))

    if requested:
        add(requested)
        if not include_fallback:
            return [item[0] for item in resolved]

    remaining: dict[str, list[str]] = {kind: [] for kind in MODEL_KINDS}
    requested_identity = set(seen)
    for model_id in raw_ids:
        item = merged_model_config(
            model_id,
            registry=registry,
            environment=environment,
        )
        if item is None:
            continue
        canonical_id, config = item
        if not model_enabled(config):
            continue
        kind = model_kind(config)
        if kind == "codex" and not codex_enabled:
            continue
        identity = _identity(canonical_id, config)
        if identity in requested_identity:
            continue
        requested_identity.add(identity)
        remaining[kind].append(canonical_id)

    for kind in MODEL_KINDS:
        for model_id in remaining[kind]:
            add(model_id)
    return [item[0] for item in resolved]


__all__ = [
    "MODEL_KINDS",
    "apply_codex_launch_policy",
    "codex_fallback_enabled",
    "merged_model_config",
    "model_enabled",
    "model_kind",
    "ordered_model_candidates",
]
