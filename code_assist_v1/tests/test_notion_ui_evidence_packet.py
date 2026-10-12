from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.notion_parity.ui_actions import build_action_log
from code_assist_v1.tools.notion_parity.ui_evidence_packet import (
    CONTRACT,
    EvidencePacketError,
    redaction_allowlist_sha256,
    verify_evidence_packet,
    write_evidence_packet,
)
from code_assist_v1.tools.notion_parity.ui_reference_guard import (
    ReferenceC0Observation,
    evaluate_reference_c0,
)


SHA_SOURCE = "sha256:" + "a" * 64
SHA_POLICY = "sha256:" + "b" * 64
SHA_ALLOWLIST = redaction_allowlist_sha256(())


def _actions() -> list[dict[str, object]]:
    return [
        {
            "action_id": "UI-SHELL-002-A01",
            "scenario_id": "UI-SHELL-002",
            "surface": "sidebar",
            "verb": "click",
            "target_alias": "SIDEBAR-COLLAPSE",
            "input_alias": None,
            "expected_state_id": "STATE-SIDEBAR-COLLAPSED",
            "expected_focus_alias": "SIDEBAR-RESTORE",
            "capture_after": True,
            "mutation_class": "read_only",
            "recovery_action_id": None,
        }
    ]


def _capture(side: str, action_hash: str) -> dict[str, object]:
    return {
        "capture_schema_version": 1,
        "scenario_id": "UI-SHELL-002",
        "side": side,
        "profile": "free-owner-chrome-desktop-ko-KR",
        "viewport": {"width": 1440, "height": 900, "scale": 1},
        "state": {"visible": True, "workspace_alias": "REF-FREE-A"},
        "keyboard": {"result": "success"},
        "focus": {"name": "SIDEBAR-RESTORE", "state": "focused"},
        "a11y": {"role": "BUTTON", "name": "SIDEBAR-RESTORE", "state": "enabled"},
        "semantic_visual": {"region": "SIDEBAR", "state": "visible"},
        "error_recovery": {"result": "unchanged"},
        "geometry": {"SIDEBAR": {"x": 0, "y": 0, "width": 270, "height": 900}},
        "latency_ms": 12,
        "action_log_sha256": action_hash,
        "screenshot_sha256": None,
        "source_build_sha256": SHA_SOURCE,
        "redaction": {"status": "pass", "rule_set": "core-free-ui-v1"},
    }


def _packet_values() -> tuple[dict[str, object], dict[str, object]]:
    action_log = build_action_log(
        scenario_id="UI-SHELL-002",
        side="local",
        run_id="ui1-packet-test",
        actions=_actions(),
    )
    action_hash = canonical_sha256(action_log)
    reference = _capture("reference", action_hash)
    local = _capture("local", action_hash)
    reference_hash = canonical_sha256(reference)
    local_hash = canonical_sha256(local)
    differential = {
        "report_schema_version": 1,
        "status": "pass",
        "reference_sha256": reference_hash,
        "candidate_sha256": local_hash,
        "policy_sha256": SHA_POLICY,
        "difference_count": 0,
        "differences": [],
    }
    artifacts = {
        "evidence/ui/actions.json": action_log,
        "evidence/ui/reference.json": reference,
        "evidence/ui/local.json": local,
        "evidence/ui/differential.json": differential,
    }
    manifest = {
        "schema_version": 1,
        "contract": "notion_core_free_ui_evidence_v1",
        "scenario_id": "UI-SHELL-002",
        "run_id": "ui1-packet-test",
        "observation_state": "differential-passed",
        "promotion_status": "differential_passed",
        "profile": "free-owner-chrome-desktop-ko-KR",
        "source_build_sha256": SHA_SOURCE,
        "action_log": {"kind": "action_log", "path": "evidence/ui/actions.json", "sha256": action_hash},
        "captures": {
            "reference": {"kind": "capture", "path": "evidence/ui/reference.json", "sha256": reference_hash},
            "local": {"kind": "capture", "path": "evidence/ui/local.json", "sha256": local_hash},
        },
        "differential": {
            "kind": "differential",
            "path": "evidence/ui/differential.json",
            "sha256": canonical_sha256(differential),
            "status": "pass",
            "reference_sha256": reference_hash,
            "candidate_sha256": local_hash,
            "policy_sha256": SHA_POLICY,
        },
        "screenshots": [],
        "reviewer": "REVIEWER-XHIGH",
        "reviewed_at": "2026-08-14T22:00:00+09:00",
        "redaction": {"status": "pass", "rule_set": "core-free-ui-v1", "allowlist_sha256": SHA_ALLOWLIST},
        "raw_artifact_deletion": {"status": "not_applicable", "record_sha256": None},
    }
    return manifest, artifacts


def _c0_decision():
    return evaluate_reference_c0(
        ReferenceC0Observation(
            run_id="ui1-packet-test",
            browser_connection_visible=True,
            chrome_session_confirmed=True,
            signed_in_visible=True,
            workspace_switcher_opened=True,
            ref_free_a_confirmed=True,
            ref_free_b_confirmed=True,
            other_workspace_labels_visible=True,
        )
    )


def _write_packet(
    evidence_root: Path,
    manifest: dict[str, object],
    artifacts: dict[str, object],
):
    return write_evidence_packet(
        test_root=evidence_root,
        manifest=manifest,
        json_artifacts=artifacts,
        reference_c0_decision=_c0_decision(),
    )


@pytest.fixture
def evidence_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODE_ASSIST_TEST_MODE", "1")
    root = tmp_path / "evidence-root"
    root.mkdir()
    return root


@pytest.mark.unit
def test_writer_seals_canonical_exact_packet_and_returns_path_free_receipt(
    evidence_root: Path,
) -> None:
    manifest, artifacts = _packet_values()
    with pytest.raises(EvidencePacketError, match="requires a C0 decision"):
        write_evidence_packet(
            test_root=evidence_root,
            manifest=manifest,
            json_artifacts=artifacts,
        )
    receipt = _write_packet(evidence_root, manifest, artifacts)
    packet = evidence_root / "ui1-packet-test--UI-SHELL-002"
    report, exit_code = verify_evidence_packet(packet, test_root=evidence_root)

    assert receipt == {
        "contract": CONTRACT,
        "status": "pass",
        "scenario_id": "UI-SHELL-002",
        "run_id": "ui1-packet-test",
        "manifest_sha256": canonical_sha256(manifest),
        "seal_sha256": report["seal_sha256"],
        "reference_c0_sha256": _c0_decision().sha256(),
        "artifact_count": 4,
        "path_disclosed": False,
    }
    assert exit_code == 0
    assert report["status"] == "pass"
    assert sorted(path.relative_to(packet).as_posix() for path in packet.rglob("*") if path.is_file()) == [
        "evidence/ui/actions.json",
        "evidence/ui/differential.json",
        "evidence/ui/local.json",
        "evidence/ui/reference.json",
        "manifest.json",
        "packet-seal.json",
    ]
    assert (packet / "manifest.json").read_bytes().endswith(b"\n")
    assert b" " not in (packet / "manifest.json").read_bytes()


@pytest.mark.unit
def test_writer_rejects_missing_extra_hash_drift_and_duplicate_packet(
    evidence_root: Path,
) -> None:
    manifest, artifacts = _packet_values()
    missing = dict(artifacts)
    missing.pop("evidence/ui/local.json")
    with pytest.raises(EvidencePacketError, match="ARTIFACT_MISSING"):
        _write_packet(evidence_root, manifest, missing)

    extra = dict(artifacts)
    extra["evidence/ui/extra.json"] = {}
    with pytest.raises(EvidencePacketError, match="ARTIFACT_EXTRA"):
        _write_packet(evidence_root, manifest, extra)

    drifted = copy.deepcopy(artifacts)
    drifted["evidence/ui/local.json"]["state"]["visible"] = False
    with pytest.raises(EvidencePacketError, match="ARTIFACT_HASH"):
        _write_packet(evidence_root, manifest, drifted)

    bad_allowlist = copy.deepcopy(manifest)
    bad_allowlist["redaction"]["allowlist_sha256"] = SHA_POLICY
    with pytest.raises(EvidencePacketError, match="REDACTION_ALLOWLIST_HASH"):
        _write_packet(evidence_root, bad_allowlist, artifacts)

    _write_packet(evidence_root, manifest, artifacts)
    with pytest.raises(EvidencePacketError, match="already exists"):
        _write_packet(evidence_root, manifest, artifacts)


@pytest.mark.unit
def test_verifier_rejects_tamper_extra_file_and_noncanonical_json(
    evidence_root: Path,
) -> None:
    manifest, artifacts = _packet_values()
    _write_packet(evidence_root, manifest, artifacts)
    packet = evidence_root / "ui1-packet-test--UI-SHELL-002"

    local_path = packet / "evidence" / "ui" / "local.json"
    local_path.write_text(json.dumps(artifacts["evidence/ui/local.json"], indent=2), encoding="utf-8")
    report, exit_code = verify_evidence_packet(packet, test_root=evidence_root)
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"ARTIFACT_CANONICAL"}

    local_path.write_text(
        json.dumps(artifacts["evidence/ui/local.json"], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (packet / "unreferenced.txt").write_text("synthetic", encoding="utf-8")
    report, exit_code = verify_evidence_packet(packet, test_root=evidence_root)
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"PACKET_FILE_EXTRA"}


@pytest.mark.unit
def test_verifier_rejects_duplicate_json_keys_and_unsafe_root(
    evidence_root: Path,
) -> None:
    manifest, artifacts = _packet_values()
    _write_packet(evidence_root, manifest, artifacts)
    packet = evidence_root / "ui1-packet-test--UI-SHELL-002"
    action_path = packet / "evidence" / "ui" / "actions.json"
    action_path.write_text('{"run_id":"ui1-packet-test","run_id":"ui1-packet-test"}\n', encoding="utf-8")
    report, exit_code = verify_evidence_packet(packet, test_root=evidence_root)
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"ARTIFACT_JSON"}

    with pytest.raises(Exception):
        verify_evidence_packet(packet, test_root=Path.cwd())


@pytest.mark.unit
def test_verifier_rejects_c0_seal_tamper(evidence_root: Path) -> None:
    manifest, artifacts = _packet_values()
    _write_packet(evidence_root, manifest, artifacts)
    packet = evidence_root / "ui1-packet-test--UI-SHELL-002"
    seal_path = packet / "packet-seal.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["reference_c0_sha256"] = SHA_POLICY
    seal_path.write_bytes(
        (
            json.dumps(
                seal,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )

    report, exit_code = verify_evidence_packet(packet, test_root=evidence_root)
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"SEAL_C0_HASH"}


@pytest.mark.unit
def test_packet_module_import_has_no_application_or_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    script = r'''
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
import code_assist_v1.tools.notion_parity.ui_evidence_packet  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
forbidden = sorted(
    name for name in sys.modules
    if name in {
        "code_assist_v1.app_code",
        "code_assist_v1.auth",
        "code_assist_v1.config",
        "code_assist_v1.data_paths",
        "code_assist_v1.knowledge_core",
    }
    or name.startswith("code_assist_v1.routes_")
    or name.startswith("code_assist_v1.notion_workspace")
)
print(json.dumps({"forbidden": forbidden, "changed": before != after}, sort_keys=True))
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = __import__("subprocess").run(
        [__import__("sys").executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"changed": False, "forbidden": []}
