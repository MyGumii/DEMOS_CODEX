"""Fail-closed verifier for the E0-P1 CRDT candidate matrix and common corpus.

The verifier is deliberately read-only.  It validates the frozen observation
snapshot and runtime-neutral corpus without importing, downloading, installing,
or selecting any candidate runtime.
"""

from __future__ import annotations

from collections import Counter
import argparse
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_LADDER,
    repository_path,
    sha256_file,
)
from code_assist_v1.tools.verify_internal_collaborative_e0_runtime_lock_successor import (
    FROZEN_RUNTIME_LOCK_SHA256,
    RUNTIME_LOCK_RELATIVE,
    accepts_frozen_runtime_lock_binding,
)
from code_assist_v1.tools.verify_notion_parity_catalog import (
    JsonSchemaValidator,
    load_json,
)


REPORT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
DEFAULT_MATRIX = CONTRACT_ROOT / "internal_collaborative_crdt_candidate_matrix.v1.json"
DEFAULT_MATRIX_SCHEMA = CONTRACT_ROOT / "internal_collaborative_crdt_candidate_matrix.v1.schema.json"
DEFAULT_CORPUS = CONTRACT_ROOT / "internal_collaborative_crdt_corpus.v1.json"
DEFAULT_CORPUS_SCHEMA = CONTRACT_ROOT / "internal_collaborative_crdt_corpus.v1.schema.json"

EXPECTED_PACKAGES = (
    "pycrdt",
    "pycrdt-websocket",
    "uvicorn",
    "websockets",
    "yjs",
    "@hocuspocus/server",
)
EXPECTED_PACKAGE_FACTS = {
    "pycrdt": (
        "pypi",
        "0.14.3",
        "MIT",
        "pycrdt-0.14.3-cp314-cp314-win_amd64.whl",
        "sha256:5f9c5fde46e7961b3595141833d96ce31badcc980c6256e5e62689e5b4d404bd",
    ),
    "pycrdt-websocket": (
        "pypi",
        "0.16.4",
        "MIT",
        "pycrdt_websocket-0.16.4-py3-none-any.whl",
        "sha256:5c1612ea4e8743c4eb9d46b938b9e727ddfc7aee0d20130627e0d6c1fcc083da",
    ),
    "uvicorn": (
        "pypi",
        "0.52.4",
        "BSD-3-Clause",
        "uvicorn-0.52.4-py3-none-any.whl",
        "sha256:f86e41a149d7d05a9969337e3946a9c171c06a5d42680896daaba624aeac8da1",
    ),
    "websockets": (
        "pypi",
        "17.0.1",
        "BSD-3-Clause",
        "websockets-17.0.1-cp314-cp314-win_amd64.whl",
        "sha256:81ce19c6046ace11da7001781be7317bb1dc389f399af4b2ed962190f76f9add",
    ),
    "yjs": (
        "npm",
        "13.6.32",
        "MIT",
        "yjs@13.6.32",
        "sha512:lfiJIIC4Xayt5ItynE407ehlE03pCjeOc4hkR4yxxvvNJ4kuiN25B0g+Qp8XagYz361LLL7DCzR5bvFJ81QKtQ==",
    ),
    "@hocuspocus/server": (
        "npm",
        "4.6.0",
        "MIT",
        "@hocuspocus/server@4.6.0",
        "sha512:Ex4tka1vsm9NycHU1vPwxj3weQj+L2+736U8uentRyykAefUcYkHEh7Gfxiy9PHBholWcw1H+oYgZ3LP0ShTWQ==",
    ),
}
EXPECTED_CANDIDATES = (
    "PY_NATIVE_PYCRDT",
    "PY_OPAQUE_NODE_COMPACTOR",
    "NODE_YJS_HOCUSPOCUS",
)
EXPECTED_CANDIDATE_PACKAGES = {
    "PY_NATIVE_PYCRDT": ("pycrdt", "pycrdt-websocket", "uvicorn"),
    "PY_OPAQUE_NODE_COMPACTOR": ("uvicorn", "websockets", "yjs"),
    "NODE_YJS_HOCUSPOCUS": ("yjs", "@hocuspocus/server"),
}
EXPECTED_CRITERIA = (
    "ZERO_ACKNOWLEDGED_LOSS",
    "DETERMINISTIC_CONVERGENCE",
    "KOREAN_IME_FIDELITY",
    "WINDOWS_REPRODUCIBILITY",
    "WIRE_AND_REPLAY_STABILITY",
    "IAM_FENCE_INTEGRATION",
    "CHECKPOINT_COMPACTION_BACKUP",
    "BACKPRESSURE_AND_FAULTS",
    "RESOURCE_COST",
    "LICENSE_AND_OPERATIONS",
)
EXPECTED_SCHEDULES = (
    "CAUSAL_FORWARD",
    "CONCURRENT_REVERSE",
    "DUPLICATE_EVERY_UPDATE",
    "DETERMINISTIC_OUT_OF_ORDER",
    "RESTART_AFTER_HALF",
    "COMPACT_THEN_TAIL_REPLAY",
)
EXPECTED_ASSERTIONS = (
    "CONVERGED_CANONICAL_HASH",
    "ZERO_ACKNOWLEDGED_GRAPHEME_LOSS",
    "NO_DUPLICATE_ACKNOWLEDGED_GRAPHEME",
    "NFC_AND_UNICODE_VALID",
    "TYPED_STRUCTURE_VALID",
    "LOCAL_UNDO_PRESERVES_REMOTE",
    "IME_INTERMEDIATE_NOT_BROADCAST",
    "IME_COMMIT_ONE_UNDO_UNIT",
    "RESTART_REPLAY_EQUIVALENT",
    "COMPACTION_EQUIVALENT",
)
COMMON_ASSERTIONS = frozenset(EXPECTED_ASSERTIONS[:4])
EXPECTED_SCENARIOS = (
    "TEXT-001",
    "TEXT-002",
    "FORMAT-001",
    "BLOCK-001",
    "BLOCK-002",
    "BLOCK-003",
    "DB-001",
    "DB-002",
    "IME-001",
    "IME-002",
    "IME-003",
    "UNDO-001",
    "NET-001",
    "RECOVERY-001",
    "COMPACT-001",
)
EXPECTED_CATEGORY_COUNTS = {
    "same_block_text": 2,
    "rich_text_format": 1,
    "block_tree": 3,
    "database": 2,
    "korean_ime": 3,
    "undo_redo": 1,
    "network_replay": 1,
    "restart_replay": 1,
    "compaction": 1,
}
EXPECTED_METRICS = (
    "acknowledged_grapheme_count",
    "acknowledged_character_loss_count",
    "convergence_mismatch_count",
    "invalid_unicode_count",
    "duplicate_effect_count",
    "encoded_update_bytes",
    "checkpoint_state_bytes",
    "operations_per_second",
    "snapshot_duration_ms",
    "replay_duration_ms",
    "peak_process_rss_bytes",
)
EXPECTED_L3_TESTS = (
    "code_assist_v1/tests/notion_collaboration/test_crdt_candidate_corpus.py",
    "code_assist_v1/tests/notion_collaboration/test_crdt_runtime_spike_contract.py",
    "code_assist_v1/tests/notion_collaboration/test_crdt_runtime_spike_executor.py",
    "code_assist_v1/tests/notion_collaboration/test_crdt_candidate_benchmark.py",
    "code_assist_v1/tests/notion_collaboration/test_crdt_candidate_benchmark_executor.py",
    "code_assist_v1/tests/notion_collaboration/test_crdt_winner_selection.py",
    "code_assist_v1/tests/notion_collaboration/test_crdt_winner_selection_executor.py",
    "code_assist_v1/tests/notion_collaboration/test_ticket_protocol.py",
    "code_assist_v1/tests/notion_collaboration/test_sequence_ack_replay.py",
    "code_assist_v1/tests/notion_collaboration/test_compaction_boundary.py",
    "code_assist_v1/tests/notion_collaboration/test_backpressure.py",
    "code_assist_v1/tests/notion_collaboration/test_selected_runtime_contract.py",
    "code_assist_v1/tests/notion_collaboration/test_selected_runtime_topology_adr.py",
    "code_assist_v1/tests/notion_collaboration/test_durable_transport_migration.py",
    "code_assist_v1/tests/notion_collaboration/test_durable_transport_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_durable_transport_compaction.py",
    "code_assist_v1/tests/notion_collaboration/test_runtime_gateway.py",
    "code_assist_v1/tests/notion_collaboration/test_runtime_gateway_redaction.py",
    "code_assist_v1/tests/notion_collaboration/test_runtime_gateway_lifecycle.py",
    "code_assist_v1/tests/notion_collaboration/test_runtime_gateway_cross_process.py",
)
EXPECTED_DELIVERABLES = {
    "candidate_runtime_dependency_and_license_matrix",
    "common_zero_loss_convergence_and_korean_ime_corpus",
}
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DYNAMIC_COMPLETION_MATRIX_PATH = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/"
    "internal_collaborative_completion_matrix.v1.json"
)
E0_FROZEN_COMPLETION_MATRIX_SHA256 = (
    "sha256:60c35c589563c49730e6bfca7017a29d41a1a7fee2aff2a36e09cb850c2548da"
)
DYNAMIC_AGENTS_PATH = "code_assist_v1/AGENTS.md"
E0_FROZEN_AGENTS_SHA256 = "sha256:916383ca3674842d7dd2f9ef77af989b3b7dfe38e4c3f745072c6f9655d47549"


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _sorted_errors(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {(item["code"], item["subject"], item["message"]): item for item in errors}
    return [unique[key] for key in sorted(unique)]


def _load(path: Path, label: str, errors: list[dict[str, str]]) -> Any | None:
    try:
        return load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error(f"{label}_READ_FAILED", str(path), f"strict JSON read failed: {exc}"))
        return None


def _schema_validate(
    value: Any,
    schema: Any,
    label: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(schema, dict):
        return
    for item in JsonSchemaValidator(schema).validate(value):
        errors.append(
            _error(
                f"{label}_SCHEMA_{item['code']}",
                item["subject"],
                item["message"],
            )
        )


def _ids(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(
        item.get("id")
        for item in values
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )


def _validate_authority_inputs(
    value: dict[str, Any],
    label: str,
    errors: list[dict[str, str]],
) -> None:
    refs = value.get("authority_inputs", [])
    paths: list[str] = []
    for index, item in enumerate(refs if isinstance(refs, list) else []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        digest = item.get("sha256")
        subject = f"{label}.authority_inputs[{index}]"
        if not isinstance(path, str) or path in paths:
            errors.append(_error("AUTHORITY_PATH_DUPLICATE_OR_INVALID", subject, str(path)))
            continue
        paths.append(path)
        resolved = repository_path(path)
        if resolved is None or not resolved.is_file():
            errors.append(_error("AUTHORITY_FILE_MISSING", subject, path))
        elif path == DYNAMIC_AGENTS_PATH and digest == E0_FROZEN_AGENTS_SHA256:
            try:
                current_agents = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                current_agents = ""
            if not current_agents.startswith("# Shared Knowledge Agent implementation instructions"):
                errors.append(_error("DYNAMIC_AGENTS_INVALID", subject, path))
        elif path == DYNAMIC_COMPLETION_MATRIX_PATH and digest == E0_FROZEN_COMPLETION_MATRIX_SHA256:
            try:
                current = load_json(resolved)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                errors.append(_error("DYNAMIC_MATRIX_INVALID", subject, path))
                continue
            row_ids = [
                row.get("id")
                for row in current.get("rows", [])
                if isinstance(row, dict)
            ] if isinstance(current, dict) else []
            if (
                not isinstance(current, dict)
                or current.get("contract")
                != "code_assist.notion.internal_collaborative_completion_matrix.v1"
                or len(row_ids) != 28
                or len(set(row_ids)) != 28
            ):
                errors.append(_error("DYNAMIC_MATRIX_INVALID", subject, path))
        elif path == RUNTIME_LOCK_RELATIVE and digest == FROZEN_RUNTIME_LOCK_SHA256:
            if (
                digest != sha256_file(resolved)
                and not accepts_frozen_runtime_lock_binding(path, digest)
            ):
                errors.append(_error("AUTHORITY_HASH_DRIFT", subject, path))
        elif not SHA256_RE.fullmatch(str(digest)) or digest != sha256_file(resolved):
            errors.append(_error("AUTHORITY_HASH_DRIFT", subject, path))


def _validate_matrix(matrix: dict[str, Any], errors: list[dict[str, str]]) -> None:
    _validate_authority_inputs(matrix, "matrix", errors)
    observations = matrix.get("source_observations", [])
    package_ids = tuple(item.get("package_id") for item in observations if isinstance(item, dict))
    if package_ids != EXPECTED_PACKAGES or len(set(package_ids)) != len(package_ids):
        errors.append(_error("PACKAGE_DENOMINATOR_DRIFT", "matrix.source_observations", repr(package_ids)))
    for item in observations if isinstance(observations, list) else []:
        if not isinstance(item, dict) or item.get("package_id") not in EXPECTED_PACKAGE_FACTS:
            continue
        package_id = item["package_id"]
        actual = (
            item.get("ecosystem"),
            item.get("observed_version"),
            item.get("license_spdx"),
            item.get("target_artifact"),
            item.get("target_artifact_integrity"),
        )
        if actual != EXPECTED_PACKAGE_FACTS[package_id]:
            errors.append(_error("PACKAGE_OBSERVATION_DRIFT", f"package:{package_id}", repr(actual)))
        registry = urlparse(str(item.get("registry_url", "")))
        repository = urlparse(str(item.get("repository_url", "")))
        expected_registry = "pypi.org" if item.get("ecosystem") == "pypi" else "www.npmjs.com"
        if registry.scheme != "https" or registry.hostname != expected_registry:
            errors.append(_error("PACKAGE_SOURCE_INVALID", f"package:{package_id}.registry_url", registry.geturl()))
        if repository.scheme != "https" or repository.hostname != "github.com":
            errors.append(_error("PACKAGE_SOURCE_INVALID", f"package:{package_id}.repository_url", repository.geturl()))
        if item.get("target_artifact_available") is not True or item.get("locally_installed") is not False:
            errors.append(_error("PACKAGE_AVAILABILITY_FALSE_GREEN", f"package:{package_id}", "snapshot boundary changed"))

    candidates = matrix.get("candidates", [])
    candidate_ids = _ids(candidates)
    if candidate_ids != EXPECTED_CANDIDATES or len(set(candidate_ids)) != len(candidate_ids):
        errors.append(_error("CANDIDATE_DENOMINATOR_DRIFT", "matrix.candidates", repr(candidate_ids)))
    package_set = set(EXPECTED_PACKAGES)
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict) or candidate.get("id") not in EXPECTED_CANDIDATE_PACKAGES:
            continue
        candidate_id = candidate["id"]
        packages = tuple(candidate.get("package_ids", []))
        if packages != EXPECTED_CANDIDATE_PACKAGES[candidate_id] or not set(packages) <= package_set:
            errors.append(_error("CANDIDATE_PACKAGE_DRIFT", f"candidate:{candidate_id}", repr(packages)))
        if candidate.get("eligibility") != "eligible_for_isolated_spike_not_selected":
            errors.append(_error("CANDIDATE_FALSE_SELECTION", f"candidate:{candidate_id}", str(candidate.get("eligibility"))))

    criteria = _ids(matrix.get("decision_criteria", []))
    if criteria != EXPECTED_CRITERIA:
        errors.append(_error("DECISION_CRITERIA_DRIFT", "matrix.decision_criteria", repr(criteria)))
    allowed = matrix.get("license_policy", {}).get("allowed_spdx")
    licenses = {item.get("license_spdx") for item in observations if isinstance(item, dict)}
    if allowed != ["BSD-3-Clause", "MIT"] or not licenses <= set(allowed or []):
        errors.append(_error("LICENSE_POLICY_DRIFT", "matrix.license_policy", repr(allowed)))
    if matrix.get("license_policy", {}).get("hosted_service_required") is not False:
        errors.append(_error("HOSTED_DEPENDENCY_FORBIDDEN", "matrix.license_policy", "hosted service must not be required"))

    boundary = matrix.get("spike_boundary", {})
    required_false = (
        "production_lockfile_modification",
        "production_runtime_import",
        "schema_migration_allocation",
        "service_or_port_start",
        "live_data_or_identity_access",
    )
    if any(boundary.get(key) is not False for key in required_false):
        errors.append(_error("SPIKE_BOUNDARY_WEAKENED", "matrix.spike_boundary", "a forbidden effect became true"))
    if boundary.get("winner_selection_owner") != "E0-P4":
        errors.append(_error("WINNER_OWNER_DRIFT", "matrix.spike_boundary.winner_selection_owner", str(boundary.get("winner_selection_owner"))))

    availability = matrix.get("availability_conclusion", {})
    if (
        availability.get("every_candidate_dependency_locally_available") is not False
        or tuple(availability.get("unavailable_package_ids", [])) != tuple(sorted(EXPECTED_PACKAGES))
        or availability.get("next_packet") != "E0-P2"
        or availability.get("next_packet_requires_approval") is not True
        or availability.get("required_approval_id") != "AP-RUNTIME-SPIKE"
    ):
        errors.append(_error("APPROVAL_BOUNDARY_DRIFT", "matrix.availability_conclusion", "E0-P2 approval boundary changed"))
    claims = matrix.get("claims", {})
    if any(
        claims.get(key) is not False
        for key in (
            "candidate_winner_selected",
            "dependency_downloaded_or_installed",
            "production_dependency_pinned",
            "runtime_implemented",
            "product_complete",
        )
    ):
        errors.append(_error("MATRIX_FALSE_GREEN", "matrix.claims", "implementation or completion was claimed"))


def _has_cycle(operations: list[dict[str, Any]]) -> bool:
    graph = {item["id"]: tuple(item.get("causally_after", [])) for item in operations}
    active: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> bool:
        if node in active:
            return True
        if node in done:
            return False
        active.add(node)
        if any(dependency in graph and visit(dependency) for dependency in graph.get(node, ())):
            return True
        active.remove(node)
        done.add(node)
        return False

    return any(visit(node) for node in graph)


def _validate_corpus(corpus: dict[str, Any], matrix_path: Path, errors: list[dict[str, str]]) -> None:
    _validate_authority_inputs(corpus, "corpus", errors)
    matrix_refs = [
        item
        for item in corpus.get("authority_inputs", [])
        if isinstance(item, dict) and item.get("path", "").endswith("internal_collaborative_crdt_candidate_matrix.v1.json")
    ]
    if len(matrix_refs) != 1 or matrix_refs[0].get("sha256") != sha256_file(matrix_path):
        errors.append(_error("CORPUS_MATRIX_BINDING_DRIFT", "corpus.authority_inputs", "candidate matrix hash differs"))

    canonical = corpus.get("canonical_document_contract", {})
    if (
        canonical.get("candidate_specific_binary_hash_is_product_hash") is not False
        or "NFC strings" not in str(canonical.get("canonical_json"))
        or "sha256" not in str(canonical.get("canonical_hash"))
    ):
        errors.append(_error("CANONICALIZATION_DRIFT", "corpus.canonical_document_contract", "canonical semantic hash weakened"))
    acknowledgement = corpus.get("acknowledgement_contract", {})
    if any(
        acknowledgement.get(key) != 0
        for key in (
            "acknowledged_character_loss_budget",
            "convergence_mismatch_budget",
            "invalid_unicode_budget",
        )
    ):
        errors.append(_error("ZERO_LOSS_BUDGET_DRIFT", "corpus.acknowledgement_contract", "all budgets must remain zero"))
    for required in ("exactly once", "causally authorized explicit delete"):
        if required not in str(acknowledgement.get("zero_loss_rule")):
            errors.append(_error("ACKNOWLEDGEMENT_RULE_DRIFT", "corpus.acknowledgement_contract.zero_loss_rule", required))

    actor_ids = _ids(corpus.get("actors", []))
    if actor_ids != ("owner", "member", "guest"):
        errors.append(_error("ACTOR_DENOMINATOR_DRIFT", "corpus.actors", repr(actor_ids)))
    schedule_ids = _ids(corpus.get("delivery_schedules", []))
    if schedule_ids != EXPECTED_SCHEDULES:
        errors.append(_error("SCHEDULE_DENOMINATOR_DRIFT", "corpus.delivery_schedules", repr(schedule_ids)))
    assertion_ids = _ids(corpus.get("assertion_definitions", []))
    if assertion_ids != EXPECTED_ASSERTIONS:
        errors.append(_error("ASSERTION_DENOMINATOR_DRIFT", "corpus.assertion_definitions", repr(assertion_ids)))

    scenarios = corpus.get("scenarios", [])
    scenario_ids = _ids(scenarios)
    if scenario_ids != EXPECTED_SCENARIOS or len(set(scenario_ids)) != len(scenario_ids):
        errors.append(_error("SCENARIO_DENOMINATOR_DRIFT", "corpus.scenarios", repr(scenario_ids)))
    categories = Counter(item.get("category") for item in scenarios if isinstance(item, dict))
    if dict(categories) != EXPECTED_CATEGORY_COUNTS:
        errors.append(_error("SCENARIO_CATEGORY_DRIFT", "corpus.scenarios", repr(dict(categories))))

    all_operation_ids: set[str] = set()
    all_grapheme_ids: set[str] = set()
    known_actors = set(actor_ids)
    known_schedules = set(schedule_ids)
    known_assertions = set(assertion_ids)
    for scenario in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(scenario, dict):
            continue
        scenario_id = str(scenario.get("id"))
        operations = [item for item in scenario.get("operations", []) if isinstance(item, dict)]
        operation_ids = {str(item.get("id")) for item in operations}
        if len(operation_ids) != len(operations) or all_operation_ids.intersection(operation_ids):
            errors.append(_error("OPERATION_ID_DUPLICATE", f"scenario:{scenario_id}", repr(sorted(operation_ids))))
        all_operation_ids.update(operation_ids)
        if _has_cycle(operations):
            errors.append(_error("CAUSAL_CYCLE", f"scenario:{scenario_id}", "operation dependency cycle"))
        scenario_assertions = set(scenario.get("assertion_ids", []))
        if not COMMON_ASSERTIONS <= scenario_assertions or not scenario_assertions <= known_assertions:
            errors.append(_error("SCENARIO_ASSERTION_DRIFT", f"scenario:{scenario_id}", repr(sorted(scenario_assertions))))
        scenario_schedules = set(scenario.get("schedule_ids", []))
        if len(scenario_schedules) < 3 or not scenario_schedules <= known_schedules:
            errors.append(_error("SCENARIO_SCHEDULE_DRIFT", f"scenario:{scenario_id}", repr(sorted(scenario_schedules))))
        for fragment in scenario.get("expected_visible_fragments", []):
            if not isinstance(fragment, str) or unicodedata.normalize("NFC", fragment) != fragment:
                errors.append(_error("NON_NFC_CORPUS_TEXT", f"scenario:{scenario_id}.expected_visible_fragments", repr(fragment)))

        known_graphemes = {
            str(item.get("id"))
            for item in scenario.get("initial_graphemes", [])
            if isinstance(item, dict)
        }
        for operation in operations:
            operation_id = str(operation.get("id"))
            dependencies = set(operation.get("causally_after", []))
            if not dependencies <= operation_ids:
                errors.append(_error("CAUSAL_REFERENCE_UNKNOWN", f"operation:{operation_id}", repr(sorted(dependencies))))
            if operation.get("actor") not in known_actors:
                errors.append(_error("OPERATION_ACTOR_UNKNOWN", f"operation:{operation_id}", str(operation.get("actor"))))
            graphemes = operation.get("acknowledged_graphemes", [])
            values: list[str] = []
            for grapheme in graphemes if isinstance(graphemes, list) else []:
                if not isinstance(grapheme, dict):
                    continue
                grapheme_id = str(grapheme.get("id"))
                value = grapheme.get("value")
                if grapheme_id in known_graphemes or grapheme_id in all_grapheme_ids:
                    errors.append(_error("GRAPHEME_ID_DUPLICATE", f"operation:{operation_id}", grapheme_id))
                known_graphemes.add(grapheme_id)
                all_grapheme_ids.add(grapheme_id)
                if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
                    errors.append(_error("NON_NFC_CORPUS_TEXT", f"operation:{operation_id}", repr(value)))
                else:
                    values.append(value)
            deletes = set(operation.get("explicit_delete_ids", []))
            if not deletes <= known_graphemes:
                errors.append(_error("DELETE_GRAPHEME_UNKNOWN", f"operation:{operation_id}", repr(sorted(deletes - known_graphemes))))
            payload = operation.get("payload", {})
            acknowledged_text = "".join(values)
            if operation.get("kind") in {"text_insert", "ime_commit"} and payload.get("text") != acknowledged_text:
                errors.append(_error("ACKNOWLEDGED_TEXT_DRIFT", f"operation:{operation_id}", repr(acknowledged_text)))
            if operation.get("kind") == "cell_set" and isinstance(payload.get("value"), str) and payload.get("value") != acknowledged_text:
                errors.append(_error("ACKNOWLEDGED_TEXT_DRIFT", f"operation:{operation_id}", repr(acknowledged_text)))

        ime_trace = scenario.get("ime_trace")
        if scenario.get("category") == "korean_ime":
            if not isinstance(ime_trace, dict):
                errors.append(_error("IME_TRACE_MISSING", f"scenario:{scenario_id}", "Korean IME trace required"))
                continue
            events = ime_trace.get("events", [])
            event_types = [item.get("type") for item in events if isinstance(item, dict)]
            if not event_types or event_types[0] != "compositionstart" or event_types[-1] != "compositionend":
                errors.append(_error("IME_EVENT_BOUNDARY_DRIFT", f"scenario:{scenario_id}", repr(event_types)))
            commit_id = ime_trace.get("committed_operation_id")
            commits = [item for item in operations if item.get("id") == commit_id and item.get("kind") == "ime_commit"]
            expected_text = ime_trace.get("expected_committed_text")
            if len(commits) != 1 or commits[0].get("payload", {}).get("text") != expected_text:
                errors.append(_error("IME_COMMIT_DRIFT", f"scenario:{scenario_id}", str(commit_id)))
            if not events or events[-1].get("data") != expected_text:
                errors.append(_error("IME_COMMIT_DRIFT", f"scenario:{scenario_id}.events", repr(expected_text)))
            if not {"IME_INTERMEDIATE_NOT_BROADCAST", "IME_COMMIT_ONE_UNDO_UNIT"} <= scenario_assertions:
                errors.append(_error("IME_ASSERTION_DRIFT", f"scenario:{scenario_id}", "IME assertions missing"))
        elif ime_trace is not None:
            errors.append(_error("IME_TRACE_UNEXPECTED", f"scenario:{scenario_id}", "only Korean IME scenarios may define traces"))

    metrics = tuple(corpus.get("required_metrics", []))
    if metrics != EXPECTED_METRICS:
        errors.append(_error("METRIC_DENOMINATOR_DRIFT", "corpus.required_metrics", repr(metrics)))
    adapter = corpus.get("adapter_contract", {})
    if (
        adapter.get("all_candidates_execute_identical_corpus") is not True
        or adapter.get("candidate_specific_expected_result_edit_forbidden") is not True
        or adapter.get("browser_native_ime_evidence_still_required_later") is not True
        or adapter.get("canonical_semantic_hash_must_converge_within_candidate") is not True
    ):
        errors.append(_error("ADAPTER_CONTRACT_DRIFT", "corpus.adapter_contract", "shared corpus contract weakened"))
    claims = corpus.get("claims", {})
    if any(
        claims.get(key) is not False
        for key in (
            "candidate_adapter_implemented",
            "candidate_execution_performed",
            "candidate_winner_selected",
            "native_browser_ime_proven_by_this_corpus",
            "runtime_implemented",
            "product_complete",
        )
    ):
        errors.append(_error("CORPUS_FALSE_GREEN", "corpus.claims", "execution or completion was claimed"))


def _validate_catalog_and_lane(
    catalog: dict[str, Any],
    ladder: dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    packet = next((item for item in catalog.get("packets", []) if item.get("id") == "E0-P1"), None)
    if not isinstance(packet, dict):
        errors.append(_error("PACKET_MISSING", "catalog.packets", "E0-P1"))
    else:
        if set(packet.get("deliverables", [])) != EXPECTED_DELIVERABLES:
            errors.append(_error("PACKET_DELIVERABLE_DRIFT", "packet:E0-P1.deliverables", repr(packet.get("deliverables"))))
        if packet.get("prerequisites") != ["A0A-P1"] or packet.get("approval") is not None:
            errors.append(_error("PACKET_BOUNDARY_DRIFT", "packet:E0-P1", "prerequisite or approval changed"))
    lane = next(
        (
            item
            for rung in ladder.get("rungs", [])
            for item in rung.get("lanes", [])
            if item.get("id") == "L3-CRDT-PROTOCOL-CORPUS"
        ),
        None,
    )
    if not isinstance(lane, dict):
        errors.append(_error("L3_LANE_MISSING", "ladder.rungs", "L3-CRDT-PROTOCOL-CORPUS"))
        return
    lane_tests = tuple(item for item in lane.get("argv", []) if isinstance(item, str) and item.endswith(".py"))
    if lane_tests != EXPECTED_L3_TESTS or tuple(lane.get("required_deliverables", [])) != EXPECTED_L3_TESTS:
        errors.append(_error("L3_LANE_TEST_DRIFT", "lane:L3-CRDT-PROTOCOL-CORPUS", repr(lane_tests)))


def verify_crdt_corpus(
    *,
    matrix_path: Path = DEFAULT_MATRIX,
    matrix_schema_path: Path = DEFAULT_MATRIX_SCHEMA,
    corpus_path: Path = DEFAULT_CORPUS,
    corpus_schema_path: Path = DEFAULT_CORPUS_SCHEMA,
    catalog_path: Path = DEFAULT_CATALOG,
    ladder_path: Path = DEFAULT_LADDER,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    matrix = _load(Path(matrix_path), "MATRIX", errors)
    matrix_schema = _load(Path(matrix_schema_path), "MATRIX_SCHEMA", errors)
    corpus = _load(Path(corpus_path), "CORPUS", errors)
    corpus_schema = _load(Path(corpus_schema_path), "CORPUS_SCHEMA", errors)
    catalog = _load(Path(catalog_path), "CATALOG", errors)
    ladder = _load(Path(ladder_path), "LADDER", errors)
    if isinstance(matrix, dict) and isinstance(matrix_schema, dict):
        _schema_validate(matrix, matrix_schema, "MATRIX", errors)
        _validate_matrix(matrix, errors)
    if isinstance(corpus, dict) and isinstance(corpus_schema, dict):
        _schema_validate(corpus, corpus_schema, "CORPUS", errors)
        _validate_corpus(corpus, Path(matrix_path), errors)
    if isinstance(catalog, dict) and isinstance(ladder, dict):
        _validate_catalog_and_lane(catalog, ladder, errors)
    ordered = _sorted_errors(errors)
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract": "code_assist.notion.internal_collaborative_crdt_corpus.audit.v1",
        "status": "pass" if not ordered else "fail",
        "error_count": len(ordered),
        "errors": ordered,
        "matrix_sha256": sha256_file(Path(matrix_path)) if Path(matrix_path).is_file() else None,
        "corpus_sha256": sha256_file(Path(corpus_path)) if Path(corpus_path).is_file() else None,
        "summary": {
            "packet_id": matrix.get("packet_id") if isinstance(matrix, dict) else None,
            "candidate_count": len(matrix.get("candidates", [])) if isinstance(matrix, dict) else 0,
            "package_observation_count": len(matrix.get("source_observations", [])) if isinstance(matrix, dict) else 0,
            "locally_available_package_count": sum(
                item.get("locally_installed") is True
                for item in matrix.get("source_observations", [])
                if isinstance(item, dict)
            ) if isinstance(matrix, dict) else 0,
            "scenario_count": len(corpus.get("scenarios", [])) if isinstance(corpus, dict) else 0,
            "korean_ime_scenario_count": sum(
                item.get("category") == "korean_ime"
                for item in corpus.get("scenarios", [])
                if isinstance(item, dict)
            ) if isinstance(corpus, dict) else 0,
            "next_packet_requires_approval": matrix.get("availability_conclusion", {}).get("next_packet_requires_approval")
            if isinstance(matrix, dict)
            else None,
            "candidate_winner_selected": matrix.get("claims", {}).get("candidate_winner_selected")
            if isinstance(matrix, dict)
            else None,
            "runtime_implemented": matrix.get("claims", {}).get("runtime_implemented")
            if isinstance(matrix, dict)
            else None,
            "product_complete": matrix.get("claims", {}).get("product_complete")
            if isinstance(matrix, dict)
            else None,
        },
    }
    return report, 0 if not ordered else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--matrix-schema", type=Path, default=DEFAULT_MATRIX_SCHEMA)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--corpus-schema", type=Path, default=DEFAULT_CORPUS_SCHEMA)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = verify_crdt_corpus(
        matrix_path=args.matrix,
        matrix_schema_path=args.matrix_schema,
        corpus_path=args.corpus,
        corpus_schema_path=args.corpus_schema,
        catalog_path=args.catalog,
        ladder_path=args.ladder,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
