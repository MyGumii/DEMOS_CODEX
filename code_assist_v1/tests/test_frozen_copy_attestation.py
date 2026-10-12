from __future__ import annotations

import json
from pathlib import Path

from code_assist_v1.tools.frozen_copy_attestation import (
    attest_frozen_copy,
    main,
)


_CREATED = "2026-08-17T03:00:00.000000Z"


def _tree(root: Path) -> None:
    (root / "nested").mkdir(parents=True)
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    (root / "nested" / "b.json").write_text('{"ok":true}\n', encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")


def test_matching_disjoint_copy_produces_body_free_bound_receipt(tmp_path: Path) -> None:
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    _tree(source)
    _tree(snapshot)

    report = attest_frozen_copy(
        source,
        snapshot,
        snapshot_id="snapshot-test",
        snapshot_created_at_utc=_CREATED,
    )

    assert report["passed"] is True
    assert report["source_stable"] is True
    assert report["snapshot_matches"] is True
    assert report["file_count"] == 2
    serialized = json.dumps(report)
    assert "alpha" not in serialized
    assert "b.json" not in serialized


def test_mismatch_fails_and_cli_never_overwrites_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    _tree(source)
    _tree(snapshot)
    (snapshot / "a.txt").write_text("different\n", encoding="utf-8")
    output = tmp_path / "receipt.json"
    argv = [
        "--source-root",
        str(source),
        "--snapshot-root",
        str(snapshot),
        "--snapshot-id",
        "snapshot-test",
        "--snapshot-created-at-utc",
        _CREATED,
        "--output",
        str(output),
    ]

    assert main(argv) == 2
    original = output.read_bytes()
    assert main(argv) == 3
    assert output.read_bytes() == original

