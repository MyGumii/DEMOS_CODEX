from __future__ import annotations

import json
import uuid

import pytest

from code_assist_v1.tests.knowledge_spaces.integration.test_restore_drill import (
    _source,
    _target,
)
from code_assist_v1.tools.backup_restore import main


pytestmark = pytest.mark.integration


def _source_arguments(layout, backup_root) -> list[str]:
    return [
        "--data-root",
        str(layout.data_root),
        "--database",
        str(layout.database),
        "--blob-root",
        str(layout.blob_root),
        "--projection-root",
        str(layout.projection_root),
        "--backup-root",
        str(backup_root),
    ]


def test_operator_cli_creates_and_restores_one_verified_backup(
    isolated_knowledge_layout,
    capfd,
) -> None:
    _factory, source = _source(isolated_knowledge_layout)
    backup_root = isolated_knowledge_layout.root.parent / (
        "backup-cli-" + uuid.uuid4().hex
    )
    backup_root.mkdir()
    run_id = "00000000-0000-4000-8000-000000007480"
    assert main(
        [
            "create",
            *_source_arguments(source, backup_root),
            "--backup-run-id",
            run_id,
            "--app-version",
            "0.1.0",
            "--build-revision",
            "a" * 64,
            "--configuration-version",
            "cli-test-v1",
            "--lease-hmac-key-set-version",
            "cli-key-set-v1",
        ]
    ) == 0
    created = json.loads(capfd.readouterr().out)
    assert created["status"] == "pass"
    assert created["operation"] == "create"
    assert created["result"]["state"] == "verified"

    target = _target(isolated_knowledge_layout)
    assert main(
        [
            "restore-drill",
            *_source_arguments(source, backup_root),
            "--backup-run-id",
            run_id,
            "--target-data-root",
            str(target.data_root),
            "--target-database",
            str(target.database),
            "--target-blob-root",
            str(target.blob_root),
            "--target-projection-root",
            str(target.projection_root),
        ]
    ) == 0
    restored = json.loads(capfd.readouterr().out)
    assert restored["status"] == "pass"
    assert restored["operation"] == "restore-drill"
    assert restored["result"]["passed"] is True
    assert restored["result"]["projection_space_count"] == 2

    assert main(["restore-drill"]) == 2
    error = json.loads(capfd.readouterr().out)
    assert error == {
        "error": {
            "code": "CLI_INVALID_ARGUMENT",
            "message": "The backup operation arguments are invalid.",
        },
        "status": "error",
    }
