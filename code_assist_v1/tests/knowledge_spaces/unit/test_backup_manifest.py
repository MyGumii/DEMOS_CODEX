from __future__ import annotations

import copy
import json

import pytest

from code_assist_v1.knowledge_core.backup_manifest import (
    VerifiedBackupBlob,
    backup_manifest_errors,
    build_backup_manifest,
    canonical_json_bytes,
    manifest_sha256,
)


pytestmark = pytest.mark.unit


def _manifest(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "backup_run_id": "00000000-0000-4000-8000-000000007400",
        "app_version": "0.1.0",
        "build_revision": "a" * 64,
        "snapshot_db_sha256": "b" * 64,
        "snapshot_db_size_bytes": 8192,
        "snapshot_audit_high_water": 42,
        "blobs": (
            VerifiedBackupBlob(
                "d" * 64,
                7,
                "2026-08-15T00:00:01Z",
                "2026-08-15T00:00:02Z",
            ),
            VerifiedBackupBlob(
                "c" * 64,
                5,
                "2026-08-15T00:00:01Z",
                "2026-08-15T00:00:02Z",
            ),
        ),
        "configuration_version": "config-v1",
        "lease_hmac_key_set_version": "lease-keys-v1",
        "created_at": "2026-08-15T00:00:00Z",
        "completed_at": "2026-08-15T00:00:03Z",
    }
    values.update(overrides)
    return build_backup_manifest(**values)  # type: ignore[arg-type]


def _reseal(value: dict[str, object]) -> None:
    value["manifest_sha256"] = manifest_sha256(value)


def test_manifest_is_deterministic_sorted_and_self_verified() -> None:
    first = _manifest()
    second = _manifest(blobs=tuple(reversed((
        VerifiedBackupBlob(
            "d" * 64, 7, "2026-08-15T00:00:01Z", "2026-08-15T00:00:02Z"
        ),
        VerifiedBackupBlob(
            "c" * 64, 5, "2026-08-15T00:00:01Z", "2026-08-15T00:00:02Z"
        ),
    ))))
    assert first == second
    assert backup_manifest_errors(first) == ()
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert [row["sha256"] for row in first["blobs"]] == ["c" * 64, "d" * 64]
    assert first["blob_count"] == 2
    assert first["blob_bytes"] == 12


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("build_revision", "not-a-hash", "build_revision"),
        ("physical_schema_version", 6, "physical_schema_version"),
        ("schema_catalog_sha256", "0" * 64, "schema_catalog_sha256"),
        ("physical_schema_sha256", "0" * 64, "physical_schema_sha256"),
        ("sqlite_version", "3.99.0", "sqlite_version"),
        ("snapshot_db_relative_path", "../source.sqlite", "snapshot_path"),
        ("configuration_version", "bad/value", "configuration_version"),
        ("foreign_key_violation_count", 1, "snapshot_integrity"),
        ("completed_at", "2026-02-31T00:00:00Z", "manifest_time"),
        ("completed_at", "2026-08-14T23:59:59Z", "manifest_chronology"),
    ],
)
def test_manifest_rejects_resealed_authority_and_integrity_tampering(
    field: str, value: object, code: str
) -> None:
    manifest = _manifest()
    manifest[field] = value
    _reseal(manifest)
    assert code in backup_manifest_errors(manifest)


def test_blob_identity_counts_path_order_and_hash_are_independently_closed() -> None:
    baseline = _manifest()
    for mutate, expected in (
        (lambda row: row.update(size_bytes=99), "blob_bytes"),
        (lambda row: row.update(relative_path="blobs/aa/bb/" + "c" * 64), "blob_path"),
        (lambda row: row.update(status="pending"), "blob_status"),
        (lambda row: row.update(error={"secret": "no"}), "blob_status"),
        (lambda row: row.update(verified_at="2026-08-15T00:00:00Z"), "blob_time"),
    ):
        value = copy.deepcopy(baseline)
        mutate(value["blobs"][0])
        _reseal(value)
        assert expected in backup_manifest_errors(value)

    value = copy.deepcopy(baseline)
    value["blobs"].reverse()
    value["blobs_sha256"] = "0" * 64
    _reseal(value)
    errors = backup_manifest_errors(value)
    assert "blob_order" in errors
    assert "blobs_sha256" in errors


def test_manifest_validator_is_total_for_arbitrary_json_shapes() -> None:
    corpus: list[object] = [
        None,
        [],
        {"backup_run_id": []},
        {"blobs": [{"sha256": []}]},
        {"manifest_sha256": {}},
        json.loads('{"state":"verified"}'),
    ]
    for value in corpus:
        errors = backup_manifest_errors(value)
        assert errors
        assert tuple(sorted(set(errors))) == errors


def test_builder_rejects_secret_like_or_control_bearing_version_values() -> None:
    with pytest.raises(ValueError, match="app_version"):
        _manifest(app_version="release\u202e1")
    with pytest.raises(ValueError, match="configuration_version"):
        _manifest(configuration_version="config\nsecret")
