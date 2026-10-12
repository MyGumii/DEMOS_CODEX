from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from code_assist_v1.tools.runtime_preflight import (
    APPROVED_NODE_VERSION,
    APPROVED_PYTHON_IMPLEMENTATION,
    APPROVED_PYTHON_VERSION,
    APPROVED_SQLITE_SOURCE_ID,
    APPROVED_SQLITE_VERSION,
    EXPECTED_PRAGMAS,
    FEATURE_FLAGS,
    build_report,
    canonical_json,
)
from code_assist_v1.tools import runtime_preflight


@pytest.mark.unit
def test_ops_004_sqlite_observer_never_checkpoints_on_close(
    isolated_sqlite_db: Path,
) -> None:
    option = sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE
    writer = sqlite3.connect(isolated_sqlite_db, isolation_level=None)
    try:
        writer.setconfig(option, True)
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE checkpoint_probe(id INTEGER PRIMARY KEY)")
        writer.execute("INSERT INTO checkpoint_probe(id) VALUES (1)")
    finally:
        writer.close()

    wal_path = Path(f"{isolated_sqlite_db}-wal")
    before = wal_path.read_bytes()
    assert before

    observation = runtime_preflight._observe_sqlite(str(isolated_sqlite_db))
    assert observation["opened"] is True
    assert observation["no_checkpoint_on_close"] is True
    assert wal_path.read_bytes() == before


def _sqlite_pass() -> dict[str, Any]:
    return {
        "compile_options": ["THREADSAFE=1", "ENABLE_FTS5"],
        "database_list": [{"name": "main", "path": "fixture.db"}],
        "database_path_match": True,
        "foreign_key_violation_count": 0,
        "fts5_compile_option_used": True,
        "fts5_probe_ok": True,
        "json_probe_ok": True,
        "no_checkpoint_on_close": True,
        "opened": True,
        "pragmas": dict(EXPECTED_PRAGMAS),
        "quick_check_ok": True,
        "semantic_edge_baseline_complete": True,
        "semantic_edge_contract_available": True,
        "source_id": APPROVED_SQLITE_SOURCE_ID,
        "version": APPROVED_SQLITE_VERSION,
    }


@pytest.fixture
def runtime_lock(tmp_path: Path) -> Path:
    path = tmp_path / "requirements runtime.lock"
    path.write_text("Zulu_Package==2\nalpha-package==1\n", encoding="utf-8")
    return path


@pytest.fixture
def preflight_kwargs(tmp_path: Path, runtime_lock: Path) -> dict[str, Any]:
    data_root = tmp_path / "explicit data root"

    def storage_observer(paths: dict[str, str]) -> dict[str, dict[str, bool]]:
        return {
            "database": {"exists": True, "is_file": True, "is_directory": False},
            "blob_root": {"exists": True, "is_file": False, "is_directory": True},
            "projection_root": {
                "exists": True,
                "is_file": False,
                "is_directory": True,
            },
            "runtime_lock": {"exists": True, "is_file": True, "is_directory": False},
        }

    def volume_probe(path: str) -> dict[str, Any]:
        candidate = Path(path)
        return {
            "eligible": True,
            "exists": True,
            "reason_codes": [],
            "reparse": {"present": False},
            "resolved_path": str(candidate),
            "syntactic_unc": False,
            "volume": {
                "drive_type": "fixed",
                "filesystem": "NTFS",
                "root": candidate.anchor,
            },
        }

    return {
        "data_root": data_root,
        "runtime_lock": runtime_lock,
        "environ": {},
        "volume_probe": volume_probe,
        "free_space_probe": lambda path: 123456789,
        "package_provider": lambda: {
            "alpha.package": "1",
            "zulu-package": "2",
            "unlocked-extra": "9",
        },
        "sqlite_observer": lambda path: _sqlite_pass(),
        "storage_observer": storage_observer,
        "directory_write_probe": lambda path: {
            "cleanup_succeeded": True,
            "flush_succeeded": True,
            "residue_stable": True,
            "write_succeeded": True,
        },
        "python_provider": lambda: {
            "implementation": APPROVED_PYTHON_IMPLEMENTATION,
            "version": APPROVED_PYTHON_VERSION,
        },
        "node_provider": lambda: {
            "available": True,
            "status": "available",
            "version": APPROVED_NODE_VERSION,
        },
    }


def _run(
    preflight_kwargs: dict[str, Any],
    mode: str = "production",
) -> tuple[dict[str, Any], int]:
    return build_report(mode, **preflight_kwargs)


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_pass_report_is_canonical_and_uses_only_explicit_layout(
    preflight_kwargs: dict[str, Any],
) -> None:
    report, exit_code = _run(preflight_kwargs)

    root = Path(preflight_kwargs["data_root"]).resolve()
    assert exit_code == 0
    assert report["gate"] == {
        "exit_code": 0,
        "message": "All P0.4 runtime preflight checks passed.",
        "reasons": [],
        "status": "pass",
    }
    assert report["paths"]["database"] == str(root / "core" / "knowledge-core.db")
    assert report["paths"]["blob_root"] == str(root / "blobs")
    assert report["paths"]["projection_root"] == str(root / "projections")
    assert report["feature_flags"] == {name: 0 for name in FEATURE_FLAGS}
    assert report["filesystem"]["free_space"] == [
        {"free_bytes": 123456789, "role": "database_parent"},
        {"free_bytes": 123456789, "role": "blob_root"},
        {"free_bytes": 123456789, "role": "projection_root"},
    ]
    serialized = canonical_json(report)
    assert serialized == canonical_json(json.loads(serialized))
    assert "timestamp" not in serialized.lower()
    assert "executable" not in serialized.lower()
    assert ".code-assist-preflight-" not in serialized


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_accepts_explicit_storage_triplet_without_a_data_root(
    preflight_kwargs: dict[str, Any],
    tmp_path: Path,
) -> None:
    preflight_kwargs.pop("data_root")
    preflight_kwargs.update(
        database=tmp_path / "db" / "canonical.db",
        blob_root=tmp_path / "blob root",
        projection_root=tmp_path / "projection root",
    )
    report, exit_code = _run(preflight_kwargs)
    assert exit_code == 0
    assert report["paths"]["data_root"] is None


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_missing_explicit_layout_fails_before_any_observation(
    preflight_kwargs: dict[str, Any],
) -> None:
    calls: list[str] = []
    preflight_kwargs.pop("data_root")
    preflight_kwargs["storage_observer"] = lambda paths: calls.append("storage")  # type: ignore[assignment]
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert report["gate"]["status"] == "fail"
    assert "explicit_storage_paths_required" in report["gate"]["reasons"]
    assert calls == []


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("data_root", "relative-root", "data_root_not_absolute"),
        ("runtime_lock", "relative.lock", "runtime_lock_not_absolute"),
    ],
)
def test_ops_004_relative_explicit_paths_are_fatal_in_development(
    preflight_kwargs: dict[str, Any],
    field: str,
    value: str,
    reason: str,
) -> None:
    preflight_kwargs[field] = value
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert reason in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_syntactic_unc_is_rejected_before_storage_observation(
    preflight_kwargs: dict[str, Any],
) -> None:
    calls: list[str] = []
    preflight_kwargs["data_root"] = r"\\server\share\knowledge"
    preflight_kwargs["storage_observer"] = lambda paths: calls.append("storage")  # type: ignore[assignment]

    report, exit_code = _run(preflight_kwargs, "development")

    assert exit_code == 2
    assert "syntactic_unc" in report["gate"]["reasons"]
    assert calls == []


@pytest.mark.unit
def test_canonical_json_is_ascii_safe_for_any_windows_console_code_page() -> None:
    serialized = canonical_json({"path": "C:\\지식\\Ω\\core.db"})
    assert serialized.encode("ascii")
    assert json.loads(serialized) == {"path": "C:\\지식\\Ω\\core.db"}


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize("flag", FEATURE_FLAGS)
def test_ops_004_each_feature_flag_accepts_only_zero_or_one(
    preflight_kwargs: dict[str, Any], flag: str
) -> None:
    preflight_kwargs["environ"] = {flag: "true"}
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert report["feature_flags"][flag] is None
    assert "feature_flag_invalid" in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("WIKI-002")
def test_multi_wiki_schema_flag_is_reported_without_granting_traffic(
    preflight_kwargs: dict[str, Any],
) -> None:
    preflight_kwargs["environ"] = {"KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": "1"}
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 0
    assert report["feature_flags"]["KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA"] == 1
    assert report["gate"]["status"] == "pass"


@pytest.mark.unit
@pytest.mark.scenario("HSPACE-006")
def test_sbe_2_3_higher_spaces_require_exact_read_prerequisites(
    preflight_kwargs: dict[str, Any],
) -> None:
    preflight_kwargs["environ"] = {"KNOWLEDGE_CORE_HIGHER_SPACES": "1"}
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert "higher_spaces_prerequisite_missing" in report["gate"]["reasons"]

    preflight_kwargs["environ"] = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
        "KNOWLEDGE_CORE_PART_READS": "1",
        "KNOWLEDGE_CORE_HIGHER_SPACES": "1",
    }
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 0
    assert report["gate"]["status"] == "pass"


@pytest.mark.unit
@pytest.mark.scenario("HSPACE-010")
@pytest.mark.parametrize(
    "write_flag",
    ("KNOWLEDGE_CORE_TEAM_WRITES", "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES"),
)
def test_sbe_2_3_higher_writes_require_higher_reads(
    preflight_kwargs: dict[str, Any], write_flag: str
) -> None:
    preflight_kwargs["environ"] = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
        "KNOWLEDGE_CORE_PART_READS": "1",
        write_flag: "1",
    }
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert "higher_write_prerequisite_missing" in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("AGRAPH-009")
def test_sbe_4_1_aggregate_graph_reads_require_higher_reads(
    preflight_kwargs: dict[str, Any],
) -> None:
    preflight_kwargs["environ"] = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
        "KNOWLEDGE_CORE_PART_READS": "1",
        "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": "1",
    }
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert "aggregate_graph_prerequisite_missing" in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("RANK-015", "RANK-018")
@pytest.mark.parametrize(
    ("feature_flag", "reason"),
    (
        (
            "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER",
            "local_cross_encoder_prerequisite_missing",
        ),
        (
            "KNOWLEDGE_CORE_TEMPORAL_RANKING",
            "temporal_ranking_prerequisite_missing",
        ),
    ),
)
def test_sbe_5_2_ranking_features_require_hybrid_parent_in_preflight(
    preflight_kwargs: dict[str, Any], feature_flag: str, reason: str
) -> None:
    preflight_kwargs["environ"] = {feature_flag: "1"}
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert reason in report["gate"]["reasons"]

    preflight_kwargs["environ"] = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
        "KNOWLEDGE_CORE_HYBRID_RERANK": "1",
        feature_flag: "1",
    }
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 0
    assert report["gate"]["status"] == "pass"


@pytest.mark.unit
@pytest.mark.scenario("AUTOEDGE-015")
def test_sbe_4_2_first_enable_requires_completed_semantic_baseline(
    preflight_kwargs: dict[str, Any],
) -> None:
    preflight_kwargs["environ"] = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL": "1",
    }
    observation = _sqlite_pass()
    observation["semantic_edge_baseline_complete"] = False
    preflight_kwargs["sqlite_observer"] = lambda _path: observation
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert report["gate"]["status"] == "fail"
    assert "semantic_edge_baseline_incomplete" in report["gate"]["reasons"]

    observation["semantic_edge_baseline_complete"] = True
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 0
    assert report["gate"]["status"] == "pass"


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("record_present", "reason"),
    [
        (False, "part_write_rollout_missing"),
        (True, "part_write_rollout_unverifiable_p0"),
    ],
)
def test_ops_004_part_writes_never_pass_on_record_presence_alone(
    preflight_kwargs: dict[str, Any],
    tmp_path: Path,
    record_present: bool,
    reason: str,
) -> None:
    record = tmp_path / "rollout evidence.json"
    preflight_kwargs["part_write_rollout_record"] = record
    preflight_kwargs["environ"] = {"KNOWLEDGE_CORE_PART_WRITES": "1"}
    original = preflight_kwargs["storage_observer"]

    def storage(paths: dict[str, str]) -> dict[str, dict[str, bool]]:
        facts = original(paths)
        facts["part_write_rollout_record"] = {
            "exists": record_present,
            "is_file": record_present,
            "is_directory": False,
        }
        return facts

    preflight_kwargs["storage_observer"] = storage
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert reason in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_rollout_record_is_ignored_while_part_writes_are_off(
    preflight_kwargs: dict[str, Any], tmp_path: Path
) -> None:
    preflight_kwargs["part_write_rollout_record"] = tmp_path / "missing record.json"
    report, exit_code = _run(preflight_kwargs)
    assert exit_code == 0
    assert not any(reason.startswith("part_write_rollout") for reason in report["gate"]["reasons"])


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_failpoint_namespace_reports_names_only_and_is_always_fatal(
    preflight_kwargs: dict[str, Any],
) -> None:
    secret_value = "DO-NOT-REPORT-THIS-VALUE"
    preflight_kwargs["environ"] = {
        "CODE_ASSIST_FAILPOINTS": secret_value,
        "CODE_ASSIST_FAILPOINT_publication": secret_value,
        "CODE_ASSIST_FAILPOINT": secret_value,
    }
    report, exit_code = _run(preflight_kwargs, "development")
    serialized = canonical_json(report)
    assert exit_code == 2
    assert report["failpoints"]["names"] == [
        "CODE_ASSIST_FAILPOINTS",
        "CODE_ASSIST_FAILPOINT_publication",
    ]
    assert secret_value not in serialized
    assert "failpoint_configuration_present" in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_runtime_lock_is_parsed_hashed_and_compared_canonically(
    preflight_kwargs: dict[str, Any], runtime_lock: Path
) -> None:
    report, exit_code = _run(preflight_kwargs)
    lock = report["dependencies"]["runtime_lock"]
    assert exit_code == 0
    assert lock["sha256"] == hashlib.sha256(runtime_lock.read_bytes()).hexdigest()
    assert lock["requirements"] == [
        {"name": "alpha-package", "version": "1"},
        {"name": "zulu-package", "version": "2"},
    ]
    assert lock["exact_match"] is True
    assert [item["name"] for item in report["dependencies"]["installed_distributions"]] == [
        "alpha-package",
        "unlocked-extra",
        "zulu-package",
    ]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("packages", "reason"),
    [
        ({"zulu-package": "2"}, "dependency_missing"),
        ({"alpha-package": "99", "zulu-package": "2"}, "dependency_version_mismatch"),
    ],
)
def test_ops_004_dependency_mismatch_warns_in_development_and_fails_production(
    preflight_kwargs: dict[str, Any], packages: dict[str, str], reason: str
) -> None:
    preflight_kwargs["package_provider"] = lambda: packages
    development, dev_exit = _run(preflight_kwargs, "development")
    production, prod_exit = _run(preflight_kwargs, "production")
    assert dev_exit == 0 and development["gate"]["status"] == "warning"
    assert prod_exit == 2 and production["gate"]["status"] == "fail"
    assert reason in development["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize("contents", ["", "Flask>=3\n", "Flask==3\nflask==3\n"])
def test_ops_004_malformed_runtime_lock_is_fatal_in_both_modes(
    preflight_kwargs: dict[str, Any], runtime_lock: Path, contents: str
) -> None:
    runtime_lock.write_text(contents, encoding="utf-8")
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert "runtime_lock_invalid" in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("provider_name", "fact", "reason"),
    [
        (
            "python_provider",
            {"implementation": "PyPy", "version": APPROVED_PYTHON_VERSION},
            "python_implementation_mismatch",
        ),
        (
            "python_provider",
            {"implementation": APPROVED_PYTHON_IMPLEMENTATION, "version": "3.14.2"},
            "python_version_mismatch",
        ),
    ],
)
def test_ops_004_python_runtime_is_a_dependency_contract_fact(
    preflight_kwargs: dict[str, Any], provider_name: str, fact: dict[str, Any], reason: str
) -> None:
    preflight_kwargs[provider_name] = lambda: fact
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 0
    assert report["gate"]["status"] == "warning"
    assert reason in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    "fact",
    [
        {"available": False, "status": "not_found", "version": None},
        {"available": True, "status": "available", "version": "24.0.0"},
    ],
)
def test_ops_004_node_version_is_reported_without_inventing_a_production_pin(
    preflight_kwargs: dict[str, Any], fact: dict[str, Any]
) -> None:
    preflight_kwargs["node_provider"] = lambda: fact
    report, exit_code = _run(preflight_kwargs, "production")
    assert exit_code == 0
    assert report["gate"]["status"] == "pass"
    assert report["dependencies"]["node"]["baseline_match"] is False


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_duplicate_installed_versions_cannot_satisfy_runtime_lock(
    preflight_kwargs: dict[str, Any],
) -> None:
    preflight_kwargs["package_provider"] = lambda: [
        {"name": "alpha-package", "version": "0"},
        {"name": "alpha_package", "version": "1"},
        {"name": "zulu-package", "version": "2"},
    ]

    report, exit_code = _run(preflight_kwargs, "production")

    assert exit_code == 2
    lock = report["dependencies"]["runtime_lock"]
    assert lock["exact_match"] is False
    assert lock["ambiguous_distributions"] == [
        {"name": "alpha-package", "versions": ["0", "1"]}
    ]
    assert "dependency_ambiguous_distribution" in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("role", "reason"),
    [
        ("database", "database_missing"),
        ("blob_root", "blob_root_missing"),
        ("projection_root", "projection_root_missing"),
        ("runtime_lock", "runtime_lock_missing"),
    ],
)
def test_ops_004_each_required_existing_path_is_fail_closed(
    preflight_kwargs: dict[str, Any], role: str, reason: str
) -> None:
    original = preflight_kwargs["storage_observer"]

    def storage(paths: dict[str, str]) -> dict[str, dict[str, bool]]:
        facts = original(paths)
        facts[role] = {"exists": False, "is_file": False, "is_directory": False}
        return facts

    preflight_kwargs["storage_observer"] = storage
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert reason in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_ineligible_volume_forwards_only_stable_reason_codes(
    preflight_kwargs: dict[str, Any],
) -> None:
    preflight_kwargs["volume_probe"] = lambda path: {
        "eligible": False,
        "reason_codes": ["filesystem_not_ntfs", "drive_type_remote"],
        "exception": "SECRET RAW EXCEPTION",
    }
    report, exit_code = _run(preflight_kwargs, "development")
    serialized = canonical_json(report)
    assert exit_code == 2
    assert "drive_type_remote" in report["gate"]["reasons"]
    assert "filesystem_not_ntfs" in report["gate"]["reasons"]
    assert "SECRET RAW EXCEPTION" not in serialized
    assert report["sqlite"] == {"status": "skipped"}


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("layout", "reason"),
    [
        ("database_in_blob", "database_inside_blob_root"),
        ("database_in_projection", "database_inside_projection_root"),
        ("directory_overlap", "blob_projection_overlap"),
    ],
)
def test_ops_004_unsafe_storage_layout_is_rejected_before_write_probe(
    preflight_kwargs: dict[str, Any],
    tmp_path: Path,
    layout: str,
    reason: str,
) -> None:
    root = tmp_path / "layout"
    database = root / "core" / "knowledge-core.db"
    blob = root / "blobs"
    projection = root / "projections"
    if layout == "database_in_blob":
        database = blob / "knowledge-core.db"
    elif layout == "database_in_projection":
        database = projection / "knowledge-core.db"
    else:
        projection = blob / "generated"
    preflight_kwargs.update(
        data_root=root,
        database=database,
        blob_root=blob,
        projection_root=projection,
    )
    write_calls: list[str] = []
    preflight_kwargs["directory_write_probe"] = lambda path: write_calls.append(path)  # type: ignore[assignment]

    report, exit_code = _run(preflight_kwargs, "development")

    assert exit_code == 2
    assert reason in report["gate"]["reasons"]
    assert write_calls == []
    assert report["sqlite"] == {"status": "skipped"}


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_final_reparse_targets_drive_layout_validation(
    preflight_kwargs: dict[str, Any],
    tmp_path: Path,
) -> None:
    root = tmp_path / "lexically-separated"
    database = root / "core" / "knowledge-core.db"
    blob = root / "blobs"
    projection = root / "projections"
    preflight_kwargs.update(
        data_root=root,
        database=database,
        blob_root=blob,
        projection_root=projection,
    )

    def aliased_volume(path: str) -> dict[str, Any]:
        finals = {
            str(database): r"D:\canonical\shared\knowledge-core.db",
            str(database.parent): r"D:\canonical\shared",
            str(blob): r"D:\canonical\shared",
            str(projection): r"D:\canonical\projections",
        }
        return {
            "eligible": True,
            "exists": True,
            "reason_codes": [],
            "resolved_path": finals[path],
            "volume": {
                "drive_type": "fixed",
                "filesystem": "NTFS",
                "root": "D:\\",
            },
        }

    preflight_kwargs["volume_probe"] = aliased_volume
    report, exit_code = _run(preflight_kwargs, "development")

    assert exit_code == 2
    assert "database_inside_blob_root" in report["gate"]["reasons"]
    assert report["sqlite"] == {"status": "skipped"}


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_volume_root_and_incomplete_eligible_probe_fail_closed(
    preflight_kwargs: dict[str, Any],
) -> None:
    original_probe = preflight_kwargs["volume_probe"]

    def volume_root_probe(path: str) -> dict[str, Any]:
        fact = original_probe(path)
        if path.endswith("blobs"):
            fact["resolved_path"] = fact["volume"]["root"]
        return fact

    preflight_kwargs["volume_probe"] = volume_root_probe
    root_report, root_exit = _run(preflight_kwargs, "development")
    assert root_exit == 2
    assert "blob_root_is_volume_root" in root_report["gate"]["reasons"]

    preflight_kwargs["volume_probe"] = lambda path: {
        "eligible": True,
        "exists": True,
        "reason_codes": [],
    }
    incomplete_report, incomplete_exit = _run(preflight_kwargs, "development")
    assert incomplete_exit == 2
    assert "volume_probe_incomplete" in incomplete_report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("failed_key", "reason"),
    [
        ("write_succeeded", "directory_write_probe_failed"),
        ("flush_succeeded", "directory_write_probe_failed"),
        ("cleanup_succeeded", "directory_probe_cleanup_failed"),
        ("residue_stable", "directory_probe_residue_unstable"),
    ],
)
def test_ops_004_directory_write_flush_cleanup_and_residue_are_independent_gates(
    preflight_kwargs: dict[str, Any], failed_key: str, reason: str
) -> None:
    def directory_probe(path: str) -> dict[str, bool]:
        fact = {
            "cleanup_succeeded": True,
            "flush_succeeded": True,
            "residue_stable": True,
            "write_succeeded": True,
        }
        fact[failed_key] = False
        return fact

    preflight_kwargs["directory_write_probe"] = directory_probe
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert reason in report["gate"]["reasons"]
    assert report["sqlite"] == {"status": "skipped"}


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_free_space_observation_failure_is_fatal(
    preflight_kwargs: dict[str, Any],
) -> None:
    preflight_kwargs["free_space_probe"] = lambda path: -1
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert "free_space_probe_failed" in report["gate"]["reasons"]


def _sqlite_mutation(field: str, value: Any) -> dict[str, Any]:
    observation = _sqlite_pass()
    observation[field] = value
    return observation


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("version", "3.53.5", "sqlite_version_mismatch"),
        ("source_id", "different source", "sqlite_source_id_mismatch"),
        ("fts5_compile_option_used", False, "sqlite_enable_fts5_missing"),
        ("compile_options", ["THREADSAFE=1"], "sqlite_enable_fts5_missing"),
        ("fts5_probe_ok", False, "sqlite_fts5_probe_failed"),
        ("json_probe_ok", False, "sqlite_json_probe_failed"),
    ],
)
def test_ops_004_sqlite_runtime_contract_mismatches_warn_dev_and_fail_prod(
    preflight_kwargs: dict[str, Any], field: str, value: Any, reason: str
) -> None:
    preflight_kwargs["sqlite_observer"] = lambda path: _sqlite_mutation(field, value)
    development, dev_exit = _run(preflight_kwargs, "development")
    production, prod_exit = _run(preflight_kwargs, "production")
    assert dev_exit == 0 and development["gate"]["status"] == "warning"
    assert prod_exit == 2 and production["gate"]["status"] == "fail"
    assert reason in development["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize("pragma_name", tuple(EXPECTED_PRAGMAS))
def test_ops_004_every_required_pragma_is_read_back_and_gated(
    preflight_kwargs: dict[str, Any], pragma_name: str
) -> None:
    observation = _sqlite_pass()
    observation["pragmas"][pragma_name] = (
        "delete" if pragma_name == "journal_mode" else 999
    )
    preflight_kwargs["sqlite_observer"] = lambda path: observation
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 0
    assert report["gate"]["status"] == "warning"
    assert any(
        reason.startswith("sqlite_") and reason.endswith("_mismatch")
        for reason in report["gate"]["reasons"]
    )


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("opened", False, "database_open_failed"),
        ("database_path_match", False, "sqlite_database_path_mismatch"),
        ("quick_check_ok", False, "sqlite_quick_check_failed"),
        ("foreign_key_violation_count", 1, "sqlite_foreign_key_check_failed"),
    ],
)
def test_ops_004_database_open_path_and_integrity_failures_are_always_fatal(
    preflight_kwargs: dict[str, Any], field: str, value: Any, reason: str
) -> None:
    preflight_kwargs["sqlite_observer"] = lambda path: _sqlite_mutation(field, value)
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == 2
    assert report["gate"]["status"] == "fail"
    assert reason in report["gate"]["reasons"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
@pytest.mark.parametrize(
    ("stage", "reason", "dev_exit"),
    [
        ("apply_pragmas", "sqlite_probe_failed", 0),
        ("fts5", "sqlite_probe_failed", 0),
        ("database_list", "sqlite_database_path_probe_failed", 2),
        ("quick_check", "sqlite_quick_check_failed", 2),
        ("foreign_key_check", "sqlite_foreign_key_check_failed", 2),
    ],
)
def test_ops_004_partial_sqlite_probe_failure_has_explicit_safety_classification(
    preflight_kwargs: dict[str, Any], stage: str, reason: str, dev_exit: int
) -> None:
    preflight_kwargs["sqlite_observer"] = lambda path: {
        "failure_stage": stage,
        "opened": True,
    }
    report, exit_code = _run(preflight_kwargs, "development")
    assert exit_code == dev_exit
    assert reason in report["gate"]["reasons"]
    if dev_exit == 0:
        assert report["gate"]["status"] == "warning"
        assert report["gate"]["reasons"] == [reason]
    else:
        assert report["gate"]["status"] == "fail"


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_sqlite_report_sorts_compile_options_and_strips_raw_errors(
    preflight_kwargs: dict[str, Any],
) -> None:
    observation = _sqlite_pass()
    observation["compile_options"] = ["THREADSAFE=1", "ENABLE_FTS5", "A_OPTION"]
    observation["exception"] = "RAW SECRET EXCEPTION"
    preflight_kwargs["sqlite_observer"] = lambda path: observation
    report, exit_code = _run(preflight_kwargs)
    assert exit_code == 0
    assert report["sqlite"]["compile_options"] == [
        "A_OPTION",
        "ENABLE_FTS5",
        "THREADSAFE=1",
    ]
    assert "RAW SECRET EXCEPTION" not in canonical_json(report)


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_short_mode_aliases_normalize_to_canonical_report_values(
    preflight_kwargs: dict[str, Any],
) -> None:
    dev_report, _ = _run(preflight_kwargs, "dev")
    prod_report, _ = _run(preflight_kwargs, "prod")
    assert dev_report["mode"] == "development"
    assert prod_report["mode"] == "production"


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_invalid_cli_input_is_one_json_object_with_exit_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = runtime_preflight.main(["--mode", "invalid"])
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 2
    assert captured.err == ""
    assert captured.out.endswith("\n") and len(captured.out.splitlines()) == 1
    assert report["gate"]["reasons"] == ["invalid_cli_arguments"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_equals_mode_is_preserved_in_invalid_cli_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = runtime_preflight.main(["--mode=production", "--bogus"])
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 2
    assert captured.err == ""
    assert report["mode"] == "production"
    assert report["gate"]["reasons"] == ["invalid_cli_arguments"]


@pytest.mark.unit
@pytest.mark.scenario("OPS-004")
def test_ops_004_unexpected_cli_failure_is_sanitized_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET EXCEPTION DETAIL"

    def explode(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], int]:
        raise RuntimeError(secret)

    monkeypatch.setattr(runtime_preflight, "build_report", explode)
    exit_code = runtime_preflight.main(
        ["--mode", "production", "--runtime-lock", "C:\\explicit.lock"]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 2
    assert captured.err == ""
    assert report["mode"] == "production"
    assert report["gate"]["reasons"] == ["preflight_internal_error"]
    assert secret not in captured.out
