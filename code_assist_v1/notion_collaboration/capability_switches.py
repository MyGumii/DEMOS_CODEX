"""Fail-closed K0 control plane for new collaborative Notion capabilities.

The module is intentionally pure: it does not read ``os.environ``, import a web
application, open a database, or start a process.  A composition root must pass
an explicit mapping and a runtime/request scope.  Existing single-user Notion
behavior is outside this namespace; the aggregate switch controls only the new
collaborative successor capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping


SWITCH_CONTRACT = "code_assist.notion.collaboration_capability_switches.v1"
PREFIX = "CODE_ASSIST_NOTION_COLLAB_"
AGGREGATE_KEY = PREFIX + "ENABLED"
REALM_KEY = PREFIX + "REALM"
ROLLOUT_KEY = PREFIX + "ROLLOUT"
GENERATION_KEY = PREFIX + "DEPLOYMENT_GENERATION_ID"
CANARY_SUBJECTS_KEY = PREFIX + "CANARY_SUBJECT_IDS_JSON"
CANARY_WORKSPACES_KEY = PREFIX + "CANARY_WORKSPACE_IDS_JSON"

CAPABILITIES = (
    "canonical_authority",
    "realtime_transport",
    "workspace_iam",
    "presence_comments_mentions",
    "collaborative_editing",
    "second_brain_publication",
    "database_features",
    "files",
    "history",
    "search",
    "offline_outbox",
)
CAPABILITY_KEYS = {
    capability: PREFIX + "CAP_" + capability.upper()
    for capability in CAPABILITIES
}
ALLOWED_KEYS = frozenset(
    {
        AGGREGATE_KEY,
        REALM_KEY,
        ROLLOUT_KEY,
        GENERATION_KEY,
        CANARY_SUBJECTS_KEY,
        CANARY_WORKSPACES_KEY,
        *CAPABILITY_KEYS.values(),
    }
)
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})
REALMS = frozenset({"development", "production"})
ROLLOUTS = frozenset({"canary", "full"})
GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
IDENTIFIER_RE = re.compile(r"^[^\x00-\x20\x7f]{1,128}$")

BASE_SCHEMA_VERSION = 2026081317
PARALLEL_ARCHIVE_SCHEMA_VERSION = 2026081318
COLLABORATION_SCHEMA_VERSION = 2026082401


class CapabilitySwitchError(ValueError):
    """A switch, rollout, or schema invariant failed."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _parse_bool(value: object) -> bool | None:
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None


def _parse_identifier_list(raw: object, reason: str, reasons: set[str]) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        reasons.add(reason)
        return ()
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or IDENTIFIER_RE.fullmatch(item) is None for item in value)
        or len(set(value)) != len(value)
    ):
        reasons.add(reason)
        return ()
    return tuple(sorted(value))


@dataclass(frozen=True, slots=True)
class RuntimeScope:
    realm: str
    deployment_generation_id: str
    schema_compatible: bool
    full_rollout_authorized: bool = False


@dataclass(frozen=True, slots=True)
class RequestScope:
    subject_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class CapabilitySwitchConfiguration:
    realm: str
    rollout: str
    deployment_generation_id: str
    enabled_capabilities: frozenset[str]
    canary_subject_ids: tuple[str, ...]
    canary_workspace_ids: tuple[str, ...]
    configuration_fingerprint: str

    @property
    def aggregate_enabled(self) -> bool:
        return True

    def allows(self, capability: str, *, runtime: RuntimeScope, request: RequestScope) -> bool:
        """Evaluate every fence; no individual capability bypasses the aggregate."""

        if capability not in CAPABILITIES or capability not in self.enabled_capabilities:
            return False
        if (
            runtime.realm != self.realm
            or runtime.deployment_generation_id != self.deployment_generation_id
            or runtime.schema_compatible is not True
        ):
            return False
        if not request.subject_id or not request.workspace_id:
            return False
        if self.rollout == "canary":
            return request.subject_id in self.canary_subject_ids and request.workspace_id in self.canary_workspace_ids
        return self.rollout == "full" and runtime.full_rollout_authorized is True

    def public_contract(self) -> dict[str, Any]:
        return {
            "contract": SWITCH_CONTRACT,
            "aggregate_enabled": True,
            "realm": self.realm,
            "rollout": self.rollout,
            "deployment_generation_id": self.deployment_generation_id,
            "enabled_capabilities": sorted(self.enabled_capabilities),
            "canary_subject_count": len(self.canary_subject_ids),
            "canary_subject_set_sha256": _sha256(_canonical_bytes(self.canary_subject_ids)),
            "canary_workspace_count": len(self.canary_workspace_ids),
            "canary_workspace_set_sha256": _sha256(_canonical_bytes(self.canary_workspace_ids)),
            "configuration_fingerprint": self.configuration_fingerprint,
            "commercial_plan_or_storage_quota_enforced": False,
            "sbe_flag_alias_or_bypass_allowed": False,
        }


@dataclass(frozen=True, slots=True)
class CapabilitySwitchLoad:
    state: str
    configuration: CapabilitySwitchConfiguration | None
    reason_codes: tuple[str, ...]

    @property
    def enabled(self) -> bool:
        return self.state == "enabled" and self.configuration is not None


def load_capability_switches(environ: Mapping[str, str]) -> CapabilitySwitchLoad:
    """Parse only the dedicated Notion namespace and fail closed on ambiguity."""

    unknown = sorted(key for key in environ if str(key).startswith(PREFIX) and key not in ALLOWED_KEYS)
    raw_aggregate = _parse_bool(environ.get(AGGREGATE_KEY, ""))
    if raw_aggregate is not True:
        reasons = {"AGGREGATE_DISABLED" if raw_aggregate is False else "AGGREGATE_INVALID"}
        if unknown:
            reasons.add("UNKNOWN_NOTION_COLLAB_KEY")
        if any(_parse_bool(environ.get(key, "")) is True for key in CAPABILITY_KEYS.values()):
            reasons.add("CAPABILITY_REQUESTED_WHILE_AGGREGATE_DISABLED")
        return CapabilitySwitchLoad("disabled" if raw_aggregate is False else "invalid", None, tuple(sorted(reasons)))

    reasons: set[str] = set()
    if unknown:
        reasons.add("UNKNOWN_NOTION_COLLAB_KEY")
    realm = str(environ.get(REALM_KEY, ""))
    if realm not in REALMS:
        reasons.add("REALM_INVALID")
    rollout = str(environ.get(ROLLOUT_KEY, ""))
    if rollout not in ROLLOUTS:
        reasons.add("ROLLOUT_INVALID")
    generation = str(environ.get(GENERATION_KEY, ""))
    if GENERATION_RE.fullmatch(generation) is None:
        reasons.add("DEPLOYMENT_GENERATION_INVALID")

    enabled: set[str] = set()
    for capability, key in CAPABILITY_KEYS.items():
        parsed = _parse_bool(environ.get(key, ""))
        if parsed is None:
            reasons.add("CAPABILITY_VALUE_INVALID")
        elif parsed:
            enabled.add(capability)

    subjects = _parse_identifier_list(environ.get(CANARY_SUBJECTS_KEY), "CANARY_SUBJECTS_INVALID", reasons)
    workspaces = _parse_identifier_list(environ.get(CANARY_WORKSPACES_KEY), "CANARY_WORKSPACES_INVALID", reasons)
    if rollout == "canary" and (not subjects or not workspaces):
        reasons.add("CANARY_SCOPE_REQUIRED")
    if rollout == "full" and (subjects or workspaces):
        reasons.add("FULL_ROLLOUT_MUST_NOT_CARRY_CANARY_SCOPE")
    if reasons:
        return CapabilitySwitchLoad("invalid", None, tuple(sorted(reasons)))

    descriptor = {
        "contract": SWITCH_CONTRACT,
        "realm": realm,
        "rollout": rollout,
        "deployment_generation_id": generation,
        "enabled_capabilities": sorted(enabled),
        "canary_subject_ids": list(subjects),
        "canary_workspace_ids": list(workspaces),
    }
    configuration = CapabilitySwitchConfiguration(
        realm=realm,
        rollout=rollout,
        deployment_generation_id=generation,
        enabled_capabilities=frozenset(enabled),
        canary_subject_ids=subjects,
        canary_workspace_ids=workspaces,
        configuration_fingerprint=_sha256(_canonical_bytes(descriptor)),
    )
    return CapabilitySwitchLoad("enabled", configuration, ())


def evaluate_schema_gate(
    *,
    observed_schema_version: int,
    aggregate_enabled: bool,
    additive_forward_compatibility_attested: bool,
    collaboration_migration_implemented: bool,
) -> dict[str, Any]:
    """Return a rollback-safe decision; this function never prescribes downgrade."""

    lineage_known = observed_schema_version in {
        BASE_SCHEMA_VERSION,
        PARALLEL_ARCHIVE_SCHEMA_VERSION,
        COLLABORATION_SCHEMA_VERSION,
    }
    legacy_application_may_start = lineage_known and (
        observed_schema_version == BASE_SCHEMA_VERSION or additive_forward_compatibility_attested
    )
    collaboration_may_start = all(
        (
            aggregate_enabled,
            observed_schema_version == COLLABORATION_SCHEMA_VERSION,
            additive_forward_compatibility_attested,
            collaboration_migration_implemented,
        )
    )
    return {
        "contract": "code_assist.notion.collaboration_schema_gate.v1",
        "observed_schema_version": observed_schema_version,
        "lineage_known": lineage_known,
        "legacy_application_may_start": legacy_application_may_start,
        "collaboration_may_start": collaboration_may_start,
        "schema_downgrade_required": False,
        "rollback_action": "disable_Notion_collaboration_aggregate_and_run_compatible_code",
        "reason": (
            "COLLABORATION_READY"
            if collaboration_may_start
            else "AGGREGATE_OFF_LEGACY_COMPATIBLE"
            if not aggregate_enabled and legacy_application_may_start
            else "SCHEMA_OR_MIGRATION_NOT_READY"
        ),
    }


__all__ = [
    "AGGREGATE_KEY",
    "BASE_SCHEMA_VERSION",
    "CANARY_SUBJECTS_KEY",
    "CANARY_WORKSPACES_KEY",
    "CAPABILITIES",
    "CAPABILITY_KEYS",
    "COLLABORATION_SCHEMA_VERSION",
    "CapabilitySwitchConfiguration",
    "CapabilitySwitchError",
    "CapabilitySwitchLoad",
    "GENERATION_KEY",
    "PARALLEL_ARCHIVE_SCHEMA_VERSION",
    "REALM_KEY",
    "ROLLOUT_KEY",
    "RequestScope",
    "RuntimeScope",
    "evaluate_schema_gate",
    "load_capability_switches",
]
