from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.release_candidate import (
    DEFAULT_RELEASE_PATTERNS,
    REPOSITORY_ROOT,
    ReleaseCandidateError,
    build_release_candidate_manifest,
    verify_release_candidate_manifest,
    write_release_candidate_manifest_create_only,
)
from code_assist_v1.tools import freeze_internal_collaborative_release as freezer


RECORDED_AT = "2026-09-07T03:40:00Z"
GENERATION = "deployment-generation-0001"


def _write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    return path


def _fixture(tmp_path: Path) -> tuple[dict[str, tuple[str, ...]], Path]:
    _write(tmp_path / "product" / "app.py", "PRODUCT = 1\n")
    _write(tmp_path / "locks" / "runtime.lock", "dependency==1\n")
    _write(tmp_path / "schema" / "migration.py", "VERSION = 1\n")
    _write(tmp_path / "protocol" / "contract.json", '{"version":1}\n')
    _write(tmp_path / "frontend" / "app.js", "export const version = 1;\n")
    _write(tmp_path / "validation" / "test_app.py", "def test_app(): pass\n")
    schema_catalog = _write(tmp_path / "contracts" / "matrix.json", '{"rows":[]}\n')
    return {
        "product_source": ("product/**/*", "frontend/**/*"),
        "dependency_locks": ("locks/runtime.lock",),
        "database_schema_and_migrations": ("schema/**/*",),
        "collaboration_protocol": ("protocol/**/*",),
        "frontend_build": ("frontend/**/*",),
        "validation_source": ("validation/**/*",),
    }, schema_catalog


def _build(tmp_path: Path, patterns, schema_catalog: Path) -> dict:
    return build_release_candidate_manifest(
        repository_root=tmp_path,
        deployment_generation_id=GENERATION,
        recorded_at=RECORDED_AT,
        patterns=patterns,
        schema_catalog_path=schema_catalog,
    )


@pytest.mark.unit
def test_release_candidate_is_deterministic_and_validation_is_separate(tmp_path: Path) -> None:
    patterns, schema_catalog = _fixture(tmp_path)
    first = _build(tmp_path, patterns, schema_catalog)
    second = _build(tmp_path, patterns, schema_catalog)
    assert first == second
    assert first["release_candidate_id"].startswith("sha256:")
    assert first["claims"]["validation_source_bound_separately"] is True
    assert first["claims"]["artifact_archive_created"] is False

    _write(tmp_path / "validation" / "test_app.py", "def test_app(): assert True\n")
    validation_only = _build(tmp_path, patterns, schema_catalog)
    assert validation_only["release_candidate_id"] == first["release_candidate_id"]
    assert validation_only["validation_manifest_sha256"] != first["validation_manifest_sha256"]

    _write(tmp_path / "product" / "app.py", "PRODUCT = 2\n")
    product_changed = _build(tmp_path, patterns, schema_catalog)
    assert product_changed["release_candidate_id"] != first["release_candidate_id"]
    assert product_changed["source_manifest_sha256"] != first["source_manifest_sha256"]


@pytest.mark.unit
def test_release_candidate_verifier_recomputes_every_bound_file(tmp_path: Path) -> None:
    patterns, schema_catalog = _fixture(tmp_path)
    manifest = _build(tmp_path, patterns, schema_catalog)
    path = tmp_path / "evidence" / "release.json"
    write_release_candidate_manifest_create_only(path, manifest)
    report, exit_code = verify_release_candidate_manifest(
        path,
        repository_root=tmp_path,
        patterns=patterns,
        schema_catalog_path=schema_catalog,
    )
    assert exit_code == 0, report
    assert report["release_candidate_id"] == manifest["release_candidate_id"]
    assert report["file_counts"] == {
        "product_source": 2,
        "dependency_locks": 1,
        "database_schema_and_migrations": 1,
        "collaboration_protocol": 1,
        "frontend_build": 1,
        "validation_source": 1,
    }

    _write(tmp_path / "protocol" / "contract.json", '{"version":2}\n')
    report, exit_code = verify_release_candidate_manifest(
        path,
        repository_root=tmp_path,
        patterns=patterns,
        schema_catalog_path=schema_catalog,
    )
    assert exit_code == 1
    assert report["status"] == "fail"
    assert "drift" in report["errors"][0]["message"]


@pytest.mark.unit
def test_release_candidate_writer_is_create_only(tmp_path: Path) -> None:
    patterns, schema_catalog = _fixture(tmp_path)
    manifest = _build(tmp_path, patterns, schema_catalog)
    path = tmp_path / "release.json"
    write_release_candidate_manifest_create_only(path, manifest)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_release_candidate_manifest_create_only(path, manifest)
    assert path.read_bytes() == original


@pytest.mark.unit
@pytest.mark.parametrize(
    "generation",
    ["short", "contains space", "../escaping-generation", "slash/value"],
)
def test_release_candidate_rejects_unsafe_generation_ids(
    tmp_path: Path, generation: str
) -> None:
    patterns, schema_catalog = _fixture(tmp_path)
    with pytest.raises(ReleaseCandidateError):
        build_release_candidate_manifest(
            repository_root=tmp_path,
            deployment_generation_id=generation,
            recorded_at=RECORDED_AT,
            patterns=patterns,
            schema_catalog_path=schema_catalog,
        )


@pytest.mark.unit
def test_release_candidate_rejects_missing_or_escaping_inventory_patterns(tmp_path: Path) -> None:
    patterns, schema_catalog = _fixture(tmp_path)
    missing = dict(patterns)
    missing["product_source"] = ("missing/**/*",)
    with pytest.raises(ReleaseCandidateError, match="matched no files"):
        _build(tmp_path, missing, schema_catalog)

    escaping = dict(patterns)
    escaping["product_source"] = ("../outside.py",)
    with pytest.raises(ReleaseCandidateError, match="unsafe"):
        _build(tmp_path, escaping, schema_catalog)


@pytest.mark.unit
def test_checked_in_default_release_policy_has_all_canonical_categories() -> None:
    manifest = build_release_candidate_manifest(
        repository_root=REPOSITORY_ROOT,
        deployment_generation_id="local-policy-audit-0001",
        recorded_at=RECORDED_AT,
        patterns=DEFAULT_RELEASE_PATTERNS,
    )
    assert set(manifest["canonical_inputs"]) == {
        "source_manifest_without_runtime_evidence",
        "dependency_lock_hashes",
        "database_schema_version_and_migration_hashes",
        "collaboration_protocol_version",
        "frontend_build_manifest_hash",
    }
    assert manifest["categories"]["product_source"]["file_count"] >= 300
    assert manifest["categories"]["dependency_locks"]["file_count"] == 3
    assert manifest["categories"]["frontend_build"]["file_count"] >= 10
    product_paths = {
        item["path"] for item in manifest["categories"]["product_source"]["files"]
    }
    assert not any("/tests/" in path or "/tools/" in path for path in product_paths)


@pytest.mark.unit
def test_release_freezer_cli_rejects_absolute_or_missing_manifest(capsys, tmp_path: Path) -> None:
    assert freezer.main(
        ["--mode", "verify", "--manifest", str(tmp_path / "manifest.json"), "--compact"]
    ) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["errors"][0]["code"] == "RELEASE_MANIFEST_PATH_INVALID"

    relative = Path("code_assist_v1/docs/notion_parity/evidence/internal_collaboration_promotion/missing.json")
    assert freezer.main(["--mode", "verify", "--manifest", str(relative), "--compact"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["errors"][0]["code"] == "RELEASE_MANIFEST_MISSING"
