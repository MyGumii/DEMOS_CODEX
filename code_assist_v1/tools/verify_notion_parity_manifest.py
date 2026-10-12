"""Strict, read-only verification for the NP-0 Notion parity ledger.

The verifier is deliberately standard-library only.  It never imports the
application, opens a Notion store, or contacts Notion.  Official documentation
is scope evidence; only explicit captured artifacts can advance a feature to
``live_characterized`` or ``differential_passed``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "docs" / "notion_parity_manifest.json"

STATUSES = (
    "exists",
    "contract_tested",
    "live_characterized",
    "differential_passed",
)
STATUS_RANK = {status: index for index, status in enumerate(STATUSES)}
EQUIVALENCES = (
    "exact_behavioral",
    "semantic_equivalent",
    "adapter",
    "deferred_beta",
    "excluded_legal",
)
PLATFORMS = (
    "desktop_web",
    "desktop_app_windows",
    "desktop_app_macos",
    "mobile_web",
    "ios_app",
    "android_app",
    "public_web",
)
SUBSCRIPTION_PLANS = (
    "free",
    "plus",
    "business",
    "enterprise",
    "not_applicable",
)
ROLES = (
    "workspace_owner",
    "workspace_admin",
    "workspace_member",
    "guest_editor",
    "guest_commenter",
    "guest_viewer",
    "public_anonymous",
)
EVIDENCE_KINDS = (
    "source",
    "contract_test",
    "specification",
    "official_reference",
    "live_characterization",
    "differential_test",
    "adapter_contract",
    "legal_review",
)

REQUIRED_BASELINE_PROFILES: dict[str, tuple[str, str, str]] = {
    "desktop_web_free_owner": ("desktop_web", "free", "workspace_owner"),
    "desktop_web_plus_member": ("desktop_web", "plus", "workspace_member"),
    "desktop_windows_business_member": (
        "desktop_app_windows",
        "business",
        "workspace_member",
    ),
    "desktop_macos_enterprise_admin": (
        "desktop_app_macos",
        "enterprise",
        "workspace_admin",
    ),
    "desktop_web_business_guest_editor": (
        "desktop_web",
        "business",
        "guest_editor",
    ),
    "desktop_web_business_guest_commenter": (
        "desktop_web",
        "business",
        "guest_commenter",
    ),
    "desktop_web_business_guest_viewer": (
        "desktop_web",
        "business",
        "guest_viewer",
    ),
    "mobile_web_free_owner": ("mobile_web", "free", "workspace_owner"),
    "ios_plus_member": ("ios_app", "plus", "workspace_member"),
    "android_plus_member": ("android_app", "plus", "workspace_member"),
    "public_web_anonymous": (
        "public_web",
        "not_applicable",
        "public_anonymous",
    ),
}
PHASE_TAXONOMY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("NP-0", "기준선·검증", ()),
    ("NP-1", "런타임·호환", ("NP-0",)),
    ("NP-2", "Workspace·IAM", ("NP-0", "NP-1")),
    ("NP-3", "Document·Editor", ("NP-1", "NP-2")),
    ("NP-4", "Database", ("NP-1", "NP-2", "NP-3")),
    ("NP-5", "Realtime·협업", ("NP-1", "NP-2", "NP-3", "NP-4")),
    ("NP-6", "탐색·업무", ("NP-2", "NP-3", "NP-4", "NP-5")),
    ("NP-7", "Forms·Sharing·Sites", ("NP-2", "NP-4", "NP-5", "NP-6")),
    ("NP-8", "Mobile·Offline", ("NP-1", "NP-2", "NP-3", "NP-4", "NP-5")),
    ("NP-9", "자동화·연결", ("NP-1", "NP-4", "NP-5", "NP-7")),
    (
        "NP-10",
        "AI·Agents·Enterprise",
        ("NP-1", "NP-2", "NP-3", "NP-4", "NP-5", "NP-6", "NP-7", "NP-8", "NP-9"),
    ),
    (
        "NP-11",
        "이관·Pilot·최종",
        ("NP-0", "NP-1", "NP-2", "NP-3", "NP-4", "NP-5", "NP-6", "NP-7", "NP-8", "NP-9", "NP-10"),
    ),
)
PHASE_IDS = tuple(identifier for identifier, _title, _dependencies in PHASE_TAXONOMY)
PHASE_TITLES = {
    identifier: title for identifier, title, _dependencies in PHASE_TAXONOMY
}
PHASE_DEPENDENCIES = {
    identifier: dependencies
    for identifier, _title, dependencies in PHASE_TAXONOMY
}
REQUIRED_PHASE_IDS = set(PHASE_IDS)
AREA_OWNER_PHASES = {
    "persistence": "NP-1",
    "editor": "NP-3",
    "sidebar": "NP-2",
    "search": "NP-6",
    "database_views_properties": "NP-4",
    "comments_inbox": "NP-5",
    "public": "NP-7",
    "mobile_offline": "NP-8",
    "accessibility": "NP-0",
    "realtime_states": "NP-5",
    "second_brain": "NP-1",
    "automation_connections": "NP-9",
    "ai_agents_enterprise": "NP-10",
    "migration_pilot_final": "NP-11",
    "legal_boundaries": "NP-0",
}
REQUIRED_FEATURE_AREAS = set(AREA_OWNER_PHASES)
REQUIRED_SECOND_BRAIN_GATES = {
    "SB-AGENT-LINK",
    "SB-TEMPLATE-CREATION",
    "SB-SESSION-ATTACHMENT",
    "SB-PUBLISH-PREVIEW",
    "SB-PUBLISH-CONTRACT",
    "SB-SERIALIZATION-COMPATIBILITY",
}

LOCAL_EVIDENCE_KINDS = {
    "source",
    "contract_test",
    "specification",
    "live_characterization",
    "differential_test",
    "adapter_contract",
    "legal_review",
}
STATUS_REQUIRED_EVIDENCE = {
    "exists": set(),
    "contract_tested": {"contract_test"},
    "live_characterized": {"contract_test", "live_characterization"},
    "differential_passed": {
        "contract_test",
        "live_characterization",
        "differential_test",
    },
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "manifest_id",
    "reference",
    "vocabulary",
    "baseline_profiles",
    "delivery_phases",
    "feature_areas",
    "features",
    "evidence",
    "second_brain_hard_gates",
}
REFERENCE_KEYS = {
    "product",
    "baseline_date",
    "locale",
    "timezone",
    "comparison_policy",
}
VOCABULARY_KEYS = {
    "statuses",
    "equivalences",
    "platforms",
    "subscription_plans",
    "roles",
    "evidence_kinds",
}
PROFILE_KEYS = {"id", "platform", "subscription_plan", "role", "locale"}
PHASE_KEYS = {"id", "title", "depends_on", "exit_gate"}
AREA_KEYS = {"id", "title", "owner_phase", "required"}
FEATURE_KEYS = {
    "id",
    "area",
    "title",
    "status",
    "equivalence",
    "phase",
    "profiles",
    "evidence",
    "dependencies",
    "notes",
}
EVIDENCE_REQUIRED_KEYS = {"id", "kind", "subjects", "description"}
EVIDENCE_ALLOWED_KEYS = EVIDENCE_REQUIRED_KEYS | {
    "path",
    "url",
    "locator",
    "profiles",
    "recorded_at",
    "sha256",
}
GATE_KEYS = {
    "id",
    "title",
    "status",
    "equivalence",
    "blocking",
    "evidence",
    "acceptance",
}

MANIFEST_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,79}$")
FEATURE_ID_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)
EVIDENCE_ID_RE = re.compile(r"^EV-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
GATE_ID_RE = re.compile(r"^SB-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a key."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - covered via main
        raise ValueError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def strict_json_load(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8") from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJsonKeyError(f"duplicate key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number in {label}: {value}")

    return json.loads(
        text,
        object_pairs_hook=pairs_hook,
        parse_constant=reject_constant,
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


class ManifestValidator:
    """Validate one already-decoded manifest against the versioned NP-0 rules."""

    def __init__(self, manifest: Any, *, project_root: Path) -> None:
        self.manifest = manifest
        self.project_root = project_root.resolve(strict=False)
        self.errors: list[dict[str, str]] = []
        self._error_keys: set[tuple[str, str, str]] = set()
        self.profiles: dict[str, Mapping[str, Any]] = {}
        self.phases: dict[str, Mapping[str, Any]] = {}
        self.areas: dict[str, Mapping[str, Any]] = {}
        self.features: dict[str, Mapping[str, Any]] = {}
        self.evidence: dict[str, Mapping[str, Any]] = {}
        self.gates: dict[str, Mapping[str, Any]] = {}

    def error(self, code: str, location: str, message: str) -> None:
        key = (code, location, message)
        if key in self._error_keys:
            return
        self._error_keys.add(key)
        self.errors.append({"code": code, "location": location, "message": message})

    def _object(self, value: Any, location: str) -> Mapping[str, Any] | None:
        if not isinstance(value, dict):
            self.error("TYPE_INVALID", location, "Expected an object.")
            return None
        return value

    def _array(self, value: Any, location: str) -> list[Any] | None:
        if not isinstance(value, list):
            self.error("TYPE_INVALID", location, "Expected an array.")
            return None
        return value

    def _string(self, value: Any, location: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            self.error("STRING_INVALID", location, "Expected a non-empty string.")
            return None
        return value

    def _exact_keys(
        self,
        value: Mapping[str, Any],
        expected: set[str],
        location: str,
    ) -> None:
        for key in sorted(expected - set(value)):
            self.error("REQUIRED_FIELD_MISSING", location, f"Missing field: {key}")
        for key in sorted(set(value) - expected):
            self.error("UNKNOWN_FIELD", location, f"Unknown field: {key}")

    def _unique_strings(
        self,
        value: Any,
        location: str,
        *,
        allow_empty: bool = False,
    ) -> list[str]:
        items = self._array(value, location)
        if items is None:
            return []
        if not items and not allow_empty:
            self.error("ARRAY_EMPTY", location, "At least one item is required.")
        result: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            item_location = f"{location}[{index}]"
            text = self._string(item, item_location)
            if text is None:
                continue
            if text in seen:
                self.error("DUPLICATE_VALUE", item_location, f"Duplicate value: {text}")
                continue
            seen.add(text)
            result.append(text)
        return result

    def _register(
        self,
        registry: dict[str, Mapping[str, Any]],
        identifier: str | None,
        item: Mapping[str, Any],
        location: str,
    ) -> None:
        if identifier is None:
            return
        if identifier in registry:
            self.error("DUPLICATE_ID", location, f"Duplicate id: {identifier}")
            return
        registry[identifier] = item

    def _validate_reference(self, root: Mapping[str, Any]) -> None:
        reference = self._object(root.get("reference"), "reference")
        if reference is None:
            return
        self._exact_keys(reference, REFERENCE_KEYS, "reference")
        if reference.get("product") != "Notion":
            self.error("REFERENCE_PRODUCT_INVALID", "reference.product", "Product must be Notion.")
        baseline = self._string(reference.get("baseline_date"), "reference.baseline_date")
        if baseline is not None:
            try:
                date.fromisoformat(baseline)
            except ValueError:
                self.error(
                    "BASELINE_DATE_INVALID",
                    "reference.baseline_date",
                    "Baseline date must use YYYY-MM-DD.",
                )
        for key in ("locale", "timezone", "comparison_policy"):
            self._string(reference.get(key), f"reference.{key}")
        policy = str(reference.get("comparison_policy", ""))
        if "live_characterized" not in policy or "differential_passed" not in policy:
            self.error(
                "COMPARISON_POLICY_INCOMPLETE",
                "reference.comparison_policy",
                "Comparison policy must distinguish live characterization and differential passing.",
            )

    def _validate_vocabulary(self, root: Mapping[str, Any]) -> None:
        vocabulary = self._object(root.get("vocabulary"), "vocabulary")
        if vocabulary is None:
            return
        self._exact_keys(vocabulary, VOCABULARY_KEYS, "vocabulary")
        expected = {
            "statuses": STATUSES,
            "equivalences": EQUIVALENCES,
            "platforms": PLATFORMS,
            "subscription_plans": SUBSCRIPTION_PLANS,
            "roles": ROLES,
            "evidence_kinds": EVIDENCE_KINDS,
        }
        for key, allowed in expected.items():
            if vocabulary.get(key) != list(allowed):
                self.error(
                    "VOCABULARY_MISMATCH",
                    f"vocabulary.{key}",
                    "Vocabulary must exactly match the versioned enum, including order.",
                )

    def _validate_profiles(self, root: Mapping[str, Any]) -> None:
        profiles = self._array(root.get("baseline_profiles"), "baseline_profiles")
        if profiles is None:
            return
        for index, raw in enumerate(profiles):
            location = f"baseline_profiles[{index}]"
            item = self._object(raw, location)
            if item is None:
                continue
            self._exact_keys(item, PROFILE_KEYS, location)
            identifier = self._string(item.get("id"), f"{location}.id")
            if identifier and not PROFILE_ID_RE.fullmatch(identifier):
                self.error("PROFILE_ID_INVALID", f"{location}.id", "Invalid profile id.")
            self._register(self.profiles, identifier, item, location)
            for key, allowed in (
                ("platform", PLATFORMS),
                ("subscription_plan", SUBSCRIPTION_PLANS),
                ("role", ROLES),
            ):
                if item.get(key) not in allowed:
                    self.error(
                        "PROFILE_ENUM_INVALID",
                        f"{location}.{key}",
                        f"Invalid {key} value.",
                    )
            self._string(item.get("locale"), f"{location}.locale")

        for identifier, expected in REQUIRED_BASELINE_PROFILES.items():
            item = self.profiles.get(identifier)
            if item is None:
                self.error(
                    "BASELINE_PROFILE_MISSING",
                    "baseline_profiles",
                    f"Required baseline profile is missing: {identifier}",
                )
                continue
            actual = (
                item.get("platform"),
                item.get("subscription_plan"),
                item.get("role"),
            )
            if actual != expected:
                self.error(
                    "BASELINE_PROFILE_MISMATCH",
                    f"baseline_profiles.{identifier}",
                    "Required profile platform, plan, or role was changed.",
                )

    def _validate_phases(self, root: Mapping[str, Any]) -> None:
        phases = self._array(root.get("delivery_phases"), "delivery_phases")
        if phases is None:
            return
        phase_dependencies: dict[str, list[str]] = {}
        phase_order: list[str] = []
        for index, raw in enumerate(phases):
            location = f"delivery_phases[{index}]"
            item = self._object(raw, location)
            if item is None:
                continue
            self._exact_keys(item, PHASE_KEYS, location)
            identifier = self._string(item.get("id"), f"{location}.id")
            self._register(self.phases, identifier, item, location)
            if identifier:
                phase_order.append(identifier)
            title = self._string(item.get("title"), f"{location}.title")
            if identifier in PHASE_TITLES and title != PHASE_TITLES[identifier]:
                self.error(
                    "DELIVERY_PHASE_TITLE_MISMATCH",
                    f"{location}.title",
                    f"{identifier} title must be: {PHASE_TITLES[identifier]}",
                )
            self._string(item.get("exit_gate"), f"{location}.exit_gate")
            dependencies = self._unique_strings(
                item.get("depends_on"),
                f"{location}.depends_on",
                allow_empty=True,
            )
            if identifier:
                phase_dependencies[identifier] = dependencies
                expected_dependencies = PHASE_DEPENDENCIES.get(identifier)
                if (
                    expected_dependencies is not None
                    and dependencies != list(expected_dependencies)
                ):
                    self.error(
                        "DELIVERY_PHASE_DEPENDENCY_MISMATCH",
                        f"{location}.depends_on",
                        f"{identifier} dependencies must match the roadmap taxonomy and order.",
                    )

        if len(phases) != len(PHASE_IDS):
            self.error(
                "DELIVERY_PHASE_COUNT_INVALID",
                "delivery_phases",
                f"Expected exactly {len(PHASE_IDS)} roadmap phases.",
            )
        if phase_order != list(PHASE_IDS):
            self.error(
                "DELIVERY_PHASE_ORDER_INVALID",
                "delivery_phases",
                "Delivery phases must appear exactly in NP-0 through NP-11 roadmap order.",
            )
        for identifier in sorted(REQUIRED_PHASE_IDS - set(self.phases)):
            self.error(
                "DELIVERY_PHASE_MISSING",
                "delivery_phases",
                f"Required delivery phase is missing: {identifier}",
            )
        for identifier in sorted(set(self.phases) - REQUIRED_PHASE_IDS):
            self.error(
                "DELIVERY_PHASE_UNKNOWN",
                "delivery_phases",
                f"Delivery phase is not in the NP-0 through NP-11 roadmap: {identifier}",
            )
        for identifier, dependencies in phase_dependencies.items():
            for dependency in dependencies:
                if dependency not in self.phases:
                    self.error(
                        "PHASE_DEPENDENCY_UNKNOWN",
                        f"delivery_phases.{identifier}.depends_on",
                        f"Unknown phase dependency: {dependency}",
                    )
                if dependency == identifier:
                    self.error(
                        "PHASE_DEPENDENCY_SELF",
                        f"delivery_phases.{identifier}.depends_on",
                        "A phase cannot depend on itself.",
                    )
        self._validate_dependency_cycles(
            phase_dependencies,
            code="PHASE_DEPENDENCY_CYCLE",
            location="delivery_phases",
        )

    def _validate_areas(self, root: Mapping[str, Any]) -> None:
        areas = self._array(root.get("feature_areas"), "feature_areas")
        if areas is None:
            return
        for index, raw in enumerate(areas):
            location = f"feature_areas[{index}]"
            item = self._object(raw, location)
            if item is None:
                continue
            self._exact_keys(item, AREA_KEYS, location)
            identifier = self._string(item.get("id"), f"{location}.id")
            self._register(self.areas, identifier, item, location)
            self._string(item.get("title"), f"{location}.title")
            owner_phase = item.get("owner_phase")
            if owner_phase not in self.phases:
                self.error(
                    "AREA_PHASE_UNKNOWN",
                    f"{location}.owner_phase",
                    "Feature area references an unknown delivery phase.",
                )
            expected_owner = AREA_OWNER_PHASES.get(identifier or "")
            if expected_owner is not None and owner_phase != expected_owner:
                self.error(
                    "AREA_OWNER_PHASE_MISMATCH",
                    f"{location}.owner_phase",
                    f"{identifier} must be owned by {expected_owner} in the roadmap taxonomy.",
                )
            if item.get("required") is not True:
                self.error(
                    "AREA_NOT_REQUIRED",
                    f"{location}.required",
                    "Every roadmap feature area must be a required ledger area.",
                )
        for identifier in sorted(REQUIRED_FEATURE_AREAS - set(self.areas)):
            self.error(
                "FEATURE_AREA_MISSING",
                "feature_areas",
                f"Required feature area is missing: {identifier}",
            )
        for identifier in sorted(set(self.areas) - REQUIRED_FEATURE_AREAS):
            self.error(
                "FEATURE_AREA_UNKNOWN",
                "feature_areas",
                f"Feature area is not assigned in the NP-0 through NP-11 roadmap: {identifier}",
            )
        owned_phases = {
            str(item.get("owner_phase")) for item in self.areas.values()
        }
        for phase_id in PHASE_IDS:
            if phase_id not in owned_phases:
                self.error(
                    "DELIVERY_PHASE_WITHOUT_OWNER",
                    "feature_areas",
                    f"No required feature area is owned by {phase_id}.",
                )

    def _validate_features(self, root: Mapping[str, Any]) -> None:
        features = self._array(root.get("features"), "features")
        if features is None:
            return
        feature_dependencies: dict[str, list[str]] = {}
        for index, raw in enumerate(features):
            location = f"features[{index}]"
            item = self._object(raw, location)
            if item is None:
                continue
            self._exact_keys(item, FEATURE_KEYS, location)
            identifier = self._string(item.get("id"), f"{location}.id")
            if identifier and not FEATURE_ID_RE.fullmatch(identifier):
                self.error("FEATURE_ID_INVALID", f"{location}.id", "Invalid feature id.")
            self._register(self.features, identifier, item, location)
            self._string(item.get("title"), f"{location}.title")
            self._string(item.get("notes"), f"{location}.notes")
            if item.get("area") not in self.areas:
                self.error(
                    "FEATURE_AREA_UNKNOWN",
                    f"{location}.area",
                    "Feature references an unknown area.",
                )
            if item.get("phase") not in self.phases:
                self.error(
                    "FEATURE_PHASE_UNKNOWN",
                    f"{location}.phase",
                    "Feature references an unknown phase.",
                )
            if item.get("status") not in STATUSES:
                self.error(
                    "STATUS_ENUM_INVALID",
                    f"{location}.status",
                    "Feature status is not in the versioned enum.",
                )
            if item.get("equivalence") not in EQUIVALENCES:
                self.error(
                    "EQUIVALENCE_ENUM_INVALID",
                    f"{location}.equivalence",
                    "Feature equivalence is not in the versioned enum.",
                )
            if (
                item.get("equivalence") == "deferred_beta"
                and item.get("status") == "differential_passed"
            ):
                self.error(
                    "DEFERRED_FEATURE_OVERCLAIMED",
                    f"{location}.status",
                    "A deferred beta cannot be marked differential_passed.",
                )
            profiles = self._unique_strings(item.get("profiles"), f"{location}.profiles")
            for profile in profiles:
                if profile not in self.profiles:
                    self.error(
                        "FEATURE_PROFILE_UNKNOWN",
                        f"{location}.profiles",
                        f"Unknown profile: {profile}",
                    )
            self._unique_strings(item.get("evidence"), f"{location}.evidence")
            dependencies = self._unique_strings(
                item.get("dependencies"),
                f"{location}.dependencies",
                allow_empty=True,
            )
            if identifier:
                feature_dependencies[identifier] = dependencies

        for identifier, dependencies in feature_dependencies.items():
            for dependency in dependencies:
                if dependency not in self.features:
                    self.error(
                        "FEATURE_DEPENDENCY_UNKNOWN",
                        f"features.{identifier}.dependencies",
                        f"Unknown feature dependency: {dependency}",
                    )
                if dependency == identifier:
                    self.error(
                        "FEATURE_DEPENDENCY_SELF",
                        f"features.{identifier}.dependencies",
                        "A feature cannot depend on itself.",
                    )
        self._validate_dependency_cycles(
            feature_dependencies,
            code="FEATURE_DEPENDENCY_CYCLE",
            location="features",
        )
        represented_areas = {str(item.get("area")) for item in self.features.values()}
        for identifier in sorted(REQUIRED_FEATURE_AREAS - represented_areas):
            self.error(
                "FEATURE_AREA_UNREPRESENTED",
                "features",
                f"Required feature area has no ledger feature: {identifier}",
            )

    def _validate_gates(self, root: Mapping[str, Any]) -> None:
        gates = self._array(
            root.get("second_brain_hard_gates"),
            "second_brain_hard_gates",
        )
        if gates is None:
            return
        for index, raw in enumerate(gates):
            location = f"second_brain_hard_gates[{index}]"
            item = self._object(raw, location)
            if item is None:
                continue
            self._exact_keys(item, GATE_KEYS, location)
            identifier = self._string(item.get("id"), f"{location}.id")
            if identifier and not GATE_ID_RE.fullmatch(identifier):
                self.error("SECOND_BRAIN_GATE_ID_INVALID", f"{location}.id", "Invalid gate id.")
            self._register(self.gates, identifier, item, location)
            self._string(item.get("title"), f"{location}.title")
            if item.get("status") not in STATUSES:
                self.error(
                    "STATUS_ENUM_INVALID",
                    f"{location}.status",
                    "Second Brain gate status is not in the versioned enum.",
                )
            if item.get("equivalence") != "adapter":
                self.error(
                    "SECOND_BRAIN_GATE_NOT_ADAPTER",
                    f"{location}.equivalence",
                    "Second Brain hard gates must use adapter equivalence.",
                )
            if item.get("blocking") is not True:
                self.error(
                    "SECOND_BRAIN_GATE_NOT_BLOCKING",
                    f"{location}.blocking",
                    "Second Brain hard gates must be blocking.",
                )
            self._unique_strings(item.get("evidence"), f"{location}.evidence")
            self._unique_strings(item.get("acceptance"), f"{location}.acceptance")

        for identifier in sorted(REQUIRED_SECOND_BRAIN_GATES - set(self.gates)):
            self.error(
                "SECOND_BRAIN_GATE_MISSING",
                "second_brain_hard_gates",
                f"Required Second Brain hard gate is missing: {identifier}",
            )

    def _validate_evidence(self, root: Mapping[str, Any]) -> None:
        records = self._array(root.get("evidence"), "evidence")
        if records is None:
            return
        known_subjects = set(self.features) | set(self.gates)
        for index, raw in enumerate(records):
            location = f"evidence[{index}]"
            item = self._object(raw, location)
            if item is None:
                continue
            keys = set(item)
            for key in sorted(EVIDENCE_REQUIRED_KEYS - keys):
                self.error("REQUIRED_FIELD_MISSING", location, f"Missing field: {key}")
            for key in sorted(keys - EVIDENCE_ALLOWED_KEYS):
                self.error("UNKNOWN_FIELD", location, f"Unknown field: {key}")
            identifier = self._string(item.get("id"), f"{location}.id")
            if identifier and not EVIDENCE_ID_RE.fullmatch(identifier):
                self.error("EVIDENCE_ID_INVALID", f"{location}.id", "Invalid evidence id.")
            self._register(self.evidence, identifier, item, location)
            kind = item.get("kind")
            if kind not in EVIDENCE_KINDS:
                self.error(
                    "EVIDENCE_KIND_INVALID",
                    f"{location}.kind",
                    "Evidence kind is not in the versioned enum.",
                )
            self._string(item.get("description"), f"{location}.description")
            subjects = self._unique_strings(item.get("subjects"), f"{location}.subjects")
            for subject in subjects:
                if subject not in known_subjects:
                    self.error(
                        "EVIDENCE_SUBJECT_UNKNOWN",
                        f"{location}.subjects",
                        f"Unknown evidence subject: {subject}",
                    )

            path_value = item.get("path")
            url_value = item.get("url")
            if kind in LOCAL_EVIDENCE_KINDS:
                path_text = self._string(path_value, f"{location}.path")
                self._string(item.get("locator"), f"{location}.locator")
                if path_text is not None:
                    self._validate_evidence_path(path_text, f"{location}.path")
                if url_value is not None:
                    self.error(
                        "EVIDENCE_LOCATION_CONFLICT",
                        location,
                        "Local evidence cannot also declare a URL.",
                    )
            elif kind == "official_reference":
                url_text = self._string(url_value, f"{location}.url")
                if url_text is not None:
                    parsed = urlsplit(url_text)
                    if parsed.scheme != "https" or not parsed.netloc:
                        self.error(
                            "OFFICIAL_REFERENCE_URL_INVALID",
                            f"{location}.url",
                            "Official references must use an absolute HTTPS URL.",
                        )
                if path_value is not None:
                    self.error(
                        "EVIDENCE_LOCATION_CONFLICT",
                        location,
                        "Official reference evidence cannot also declare a local path.",
                    )

            if kind in {"live_characterization", "differential_test"}:
                profiles = self._unique_strings(
                    item.get("profiles"),
                    f"{location}.profiles",
                )
                for profile in profiles:
                    if profile not in self.profiles:
                        self.error(
                            "EVIDENCE_PROFILE_UNKNOWN",
                            f"{location}.profiles",
                            f"Unknown evidence profile: {profile}",
                        )
                recorded_at = self._string(item.get("recorded_at"), f"{location}.recorded_at")
                if recorded_at is not None:
                    try:
                        timestamp = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
                        if timestamp.tzinfo is None:
                            raise ValueError("timezone missing")
                    except ValueError:
                        self.error(
                            "EVIDENCE_TIMESTAMP_INVALID",
                            f"{location}.recorded_at",
                            "Live evidence requires an ISO-8601 timestamp with timezone.",
                        )
                sha256 = self._string(item.get("sha256"), f"{location}.sha256")
                if sha256 is not None and not SHA256_RE.fullmatch(sha256):
                    self.error(
                        "EVIDENCE_SHA256_INVALID",
                        f"{location}.sha256",
                        "Live evidence requires a lowercase SHA-256 digest.",
                    )

    def _validate_evidence_path(self, value: str, location: str) -> None:
        path = Path(value)
        if path.is_absolute():
            self.error("EVIDENCE_PATH_ABSOLUTE", location, "Evidence path must be project-relative.")
            return
        resolved = (self.project_root / path).resolve(strict=False)
        if not _is_relative_to(resolved, self.project_root):
            self.error("EVIDENCE_PATH_ESCAPE", location, "Evidence path escapes the project root.")
            return
        if not resolved.is_file():
            self.error("EVIDENCE_PATH_MISSING", location, f"Evidence file does not exist: {value}")

    def _validate_evidence_links(self) -> None:
        subjects: Iterable[tuple[str, Mapping[str, Any], str]] = (
            [(identifier, item, "features") for identifier, item in self.features.items()]
            + [
                (identifier, item, "second_brain_hard_gates")
                for identifier, item in self.gates.items()
            ]
        )
        for identifier, item, section in subjects:
            references = item.get("evidence")
            if not isinstance(references, list):
                continue
            kinds: set[str] = set()
            subject_evidence_count = 0
            evidence_profiles: set[str] = set()
            for evidence_id in references:
                if not isinstance(evidence_id, str):
                    continue
                record = self.evidence.get(evidence_id)
                if record is None:
                    self.error(
                        "EVIDENCE_REFERENCE_UNKNOWN",
                        f"{section}.{identifier}.evidence",
                        f"Unknown evidence id: {evidence_id}",
                    )
                    continue
                record_subjects = record.get("subjects")
                if not isinstance(record_subjects, list) or identifier not in record_subjects:
                    self.error(
                        "EVIDENCE_SUBJECT_MISMATCH",
                        f"{section}.{identifier}.evidence",
                        f"Evidence {evidence_id} does not name {identifier} as a subject.",
                    )
                    continue
                subject_evidence_count += 1
                kind = record.get("kind")
                if isinstance(kind, str):
                    kinds.add(kind)
                profiles = record.get("profiles")
                if isinstance(profiles, list):
                    evidence_profiles.update(
                        profile for profile in profiles if isinstance(profile, str)
                    )

            if subject_evidence_count == 0:
                self.error(
                    "FEATURE_EVIDENCE_MISSING" if section == "features" else "GATE_EVIDENCE_MISSING",
                    f"{section}.{identifier}.evidence",
                    "At least one subject-scoped evidence record is required.",
                )
            status = item.get("status")
            if status in STATUS_REQUIRED_EVIDENCE:
                missing = STATUS_REQUIRED_EVIDENCE[status] - kinds
                if missing:
                    self.error(
                        "STATUS_EVIDENCE_MISSING",
                        f"{section}.{identifier}.status",
                        f"Status {status} lacks required subject evidence: {', '.join(sorted(missing))}",
                    )
                if status in {"live_characterized", "differential_passed"}:
                    declared_profiles = {
                        profile
                        for profile in item.get("profiles", [])
                        if isinstance(profile, str)
                    }
                    if declared_profiles - evidence_profiles:
                        self.error(
                            "LIVE_PROFILE_EVIDENCE_MISSING",
                            f"{section}.{identifier}.status",
                            "Live status lacks captured evidence for every declared profile.",
                        )
            if item.get("equivalence") == "excluded_legal" and "legal_review" not in kinds:
                self.error(
                    "LEGAL_EVIDENCE_MISSING",
                    f"{section}.{identifier}.equivalence",
                    "excluded_legal requires subject-scoped legal_review evidence.",
                )

    def _validate_dependency_cycles(
        self,
        graph: Mapping[str, list[str]],
        *,
        code: str,
        location: str,
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, trail: tuple[str, ...]) -> None:
            if node in visited:
                return
            if node in visiting:
                cycle = " -> ".join((*trail, node))
                self.error(code, location, f"Dependency cycle: {cycle}")
                return
            visiting.add(node)
            for dependency in graph.get(node, []):
                if dependency in graph:
                    visit(dependency, (*trail, node))
            visiting.remove(node)
            visited.add(node)

        for node in sorted(graph):
            visit(node, ())

    def validate(self) -> dict[str, Any]:
        root = self._object(self.manifest, "<root>")
        if root is not None:
            self._exact_keys(root, TOP_LEVEL_KEYS, "<root>")
            if root.get("schema_version") != SCHEMA_VERSION:
                self.error(
                    "SCHEMA_VERSION_INVALID",
                    "schema_version",
                    f"schema_version must be {SCHEMA_VERSION}.",
                )
            manifest_id = self._string(root.get("manifest_id"), "manifest_id")
            if manifest_id and not MANIFEST_ID_RE.fullmatch(manifest_id):
                self.error("MANIFEST_ID_INVALID", "manifest_id", "Invalid manifest id.")
            self._validate_reference(root)
            self._validate_vocabulary(root)
            self._validate_profiles(root)
            self._validate_phases(root)
            self._validate_areas(root)
            self._validate_features(root)
            self._validate_gates(root)
            self._validate_evidence(root)
            self._validate_evidence_links()

        errors = sorted(
            self.errors,
            key=lambda item: (item["code"], item["location"], item["message"]),
        )
        status_counts = Counter(
            str(feature.get("status")) for feature in self.features.values()
        )
        equivalence_counts = Counter(
            str(feature.get("equivalence")) for feature in self.features.values()
        )
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "pass" if not errors else "fail",
            "error_count": len(errors),
            "errors": errors,
            "summary": {
                "baseline_profile_count": len(self.profiles),
                "delivery_phase_count": len(self.phases),
                "feature_area_count": len(self.areas),
                "feature_count": len(self.features),
                "evidence_count": len(self.evidence),
                "second_brain_hard_gate_count": len(self.gates),
                "status_counts": dict(sorted(status_counts.items())),
                "equivalence_counts": dict(sorted(equivalence_counts.items())),
            },
        }


def validate_manifest_data(
    manifest: Any,
    *,
    project_root: str | Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], int]:
    validator = ManifestValidator(manifest, project_root=Path(project_root))
    report = validator.validate()
    return report, 0 if report["status"] == "pass" else 2


def verify_manifest(
    manifest_path: str | Path = DEFAULT_MANIFEST,
    *,
    project_root: str | Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], int]:
    path = Path(manifest_path)
    try:
        raw = path.read_bytes()
        manifest = strict_json_load(raw, label=str(path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "fail",
            "error_count": 1,
            "errors": [
                {
                    "code": "MANIFEST_READ_INVALID",
                    "location": str(path),
                    "message": str(exc),
                }
            ],
            "summary": {},
            "input_sha256": "",
        }
        return report, 2

    report, exit_code = validate_manifest_data(manifest, project_root=project_root)
    report["input_sha256"] = hashlib.sha256(raw).hexdigest()
    report["manifest_path"] = str(path.resolve(strict=False))
    return report, exit_code


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="verify_notion_parity_manifest")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        project_root = Path(arguments.project_root)
        if not project_root.is_absolute():
            raise ValueError("--project-root must be absolute")
        report, exit_code = verify_manifest(
            arguments.manifest,
            project_root=project_root,
        )
    except ValueError as exc:
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "fail",
            "error_count": 1,
            "errors": [
                {
                    "code": "CLI_ARGUMENT_INVALID",
                    "location": "<cli>",
                    "message": str(exc),
                }
            ],
            "summary": {},
            "input_sha256": "",
        }
        exit_code = 2
    sys.stdout.write(canonical_json(report) + "\n")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
