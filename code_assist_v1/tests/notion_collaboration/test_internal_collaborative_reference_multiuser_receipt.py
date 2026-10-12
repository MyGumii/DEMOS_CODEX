from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt as reference_verifier
from code_assist_v1.tools.internal_collaborative_reference_multiuser_approval import (
    EXACT_APPROVAL_TEXT,
    compile_approval_receipt,
)
from code_assist_v1.tools.notion_parity.exact_pixel import (
    CAPTURE_ENVIRONMENT_CONTRACT,
    canonical_sha256,
    encode_rgba_png,
    validate_capture_environment,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    CONTRACT_PATH as EXECUTION_MANIFEST_PATH,
    EXPECTED_ACTIONS,
    EXPECTED_STATES,
)
from code_assist_v1.tools.verify_v0r_p1_lossless_capture_ingest import (
    AUDIT_CONTRACT as LOSSLESS_INGEST_AUDIT_CONTRACT,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt import (
    APPROVAL_REPO_PATH,
    CONTRACT,
    MANIFEST_REPO_PATH,
    SCREENSHOT_REPO_ROOT,
    SUPERSESSION_REFERENCE_ROOT_PREFIXES,
    _load,
    _masked_union_area,
    _png_hash_and_geometry,
    _supersession_chain,
    verify,
)


TIME = "2026-08-25T20:30:00+09:00"
HASH_A = "sha256:" + "1" * 64
HASH_B = "sha256:" + "2" * 64


@pytest.mark.unit
def test_v0r_masked_union_area_is_exact_without_materializing_pixels() -> None:
    assert _masked_union_area([]) == 0
    assert _masked_union_area(
        [
            {"x": 0, "y": 0, "width": 2, "height": 2},
            {"x": 1, "y": 1, "width": 2, "height": 2},
            {"x": 4, "y": 0, "width": 1, "height": 3},
        ]
    ) == 10
    assert _masked_union_area(
        [{"x": 0, "y": 0, "width": 1440, "height": 900}]
    ) == 1_296_000


@pytest.mark.unit
def test_v0r_png_geometry_cache_is_content_and_decoder_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bytes] = []

    def decode(payload: bytes) -> SimpleNamespace:
        calls.append(payload)
        width, height = (int(value) for value in payload.decode("ascii").split("x"))
        return SimpleNamespace(width=width, height=height)

    monkeypatch.setattr(
        "code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt.decode_png_srgb_rgba8",
        decode,
    )
    first = _png_hash_and_geometry(b"1440x900")
    repeated = _png_hash_and_geometry(b"1440x900")
    changed = _png_hash_and_geometry(b"1280x800")

    assert first == repeated
    assert first[1] == (1440, 900)
    assert changed[1] == (1280, 800)
    assert calls == [b"1440x900", b"1280x800"]


@pytest.mark.unit
def test_v0r_parallel_metadata_populates_parent_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_verifier, "_PNG_GEOMETRY_CACHE", {})
    submitted: list[tuple[int, Path]] = []

    class Future:
        def __init__(self, task: tuple[int, Path]) -> None:
            self.task = task

        def result(self) -> tuple[int, str, tuple[int, int]]:
            index, path = self.task
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            return index, digest, (1440 - index * 160, 900 - index * 100)

    class Executor:
        def __init__(self, *, max_workers: int) -> None:
            assert max_workers == 2

        def __enter__(self) -> "Executor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def submit(
            self,
            function: object,
            task: tuple[int, Path],
        ) -> Future:
            assert function is reference_verifier._screenshot_metadata_task
            submitted.append(task)
            return Future(task)

    monkeypatch.setattr(reference_verifier, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(reference_verifier, "as_completed", lambda futures: futures)
    captures: list[dict[str, object]] = []
    for index in range(2):
        name = f"capture-{index}.png"
        (tmp_path / name).write_bytes(f"png-{index}".encode("ascii"))
        captures.append({"screenshot_relative_path": name})

    first = reference_verifier._parallel_screenshot_metadata(
        captures,
        screenshot_root=tmp_path,
    )
    second = reference_verifier._parallel_screenshot_metadata(
        captures,
        screenshot_root=tmp_path,
    )

    assert first == second
    assert first[0][1] == (1440, 900)
    assert first[1][1] == (1280, 800)
    assert len(submitted) == 2
    assert len(reference_verifier._PNG_GEOMETRY_CACHE) == 2


@pytest.mark.unit
def test_v0r_p1_supersession_roots_include_immutable_lossless_runs() -> None:
    assert (
        "code_assist_v1/docs/notion_parity/evidence/"
        "internal_collaboration_reference/v0r-p1-corrective/"
        in SUPERSESSION_REFERENCE_ROOT_PREFIXES
    )
    assert (
        "code_assist_v1/docs/notion_parity/evidence/"
        "internal_collaboration_reference/v0r-p1-lossless-20260829/screenshots"
    ).startswith(SUPERSESSION_REFERENCE_ROOT_PREFIXES)


@pytest.mark.unit
def test_v0r_p1_v2_supersession_chain_reaches_original_semantic_receipt() -> None:
    receipt_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "notion_parity"
        / "evidence"
        / "internal_collaboration_receipts"
        / "v0r-p1-l6-reference-multiuser-lossless-corrective-v2-20260830.json"
    )

    result = _supersession_chain(_load(receipt_path))

    assert result["valid"] is True
    assert result["link_count"] == 3
    assert result["reaches_original_semantic_receipt"] is True
    assert all(link["hash_matches"] is True for link in result["links"])


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _environment(width: int, height: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": CAPTURE_ENVIRONMENT_CONTRACT,
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "zoom": 1,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "color_scheme": "light",
        "reduced_motion": "reduce",
        "time": {"frozen": True, "epoch_ms": 1787656800000},
        "animations": {
            "disabled": True,
            "transition_duration_ms": 0,
            "caret_hidden": True,
        },
        "font_manifest": [
            {
                "family": "Arial",
                "style": "normal",
                "weight": 400,
                "sha256": HASH_A,
            }
        ],
        "browser": {"engine": "chromium", "channel": "chrome", "major_version": "140"},
        "capture": {"full_page": False, "omit_background": False},
        "source": {"kind": "reference", "build_sha256": HASH_B},
    }


def _approval_receipt(path: Path) -> Path:
    return _write(
        path,
        compile_approval_receipt(
            approval_text=EXACT_APPROVAL_TEXT,
            recorded_at=TIME,
        ),
    )


def _evidence(
    root: Path,
    approval_path: Path,
    *,
    real_png: bool = False,
) -> dict[str, object]:
    actions = [
        {
            "order": index + 1,
            "id": action[0],
            "workspace": action[1],
            "actor": action[2],
            "mutation_class": action[3],
            "started_at": TIME,
            "ended_at": TIME,
            "status": "pass",
            "result": f"{action[0]}_complete",
            "raw_identifier_persisted": False,
        }
        for index, action in enumerate(EXPECTED_ACTIONS)
    ]
    captures: list[dict[str, object]] = []
    for state in EXPECTED_STATES:
        for width, height in ((1440, 900), (1280, 800)):
            relative = f"{state[0]}/{width}x{height}.png"
            screenshot = root / Path(*relative.split("/"))
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            if real_png:
                screenshot.write_bytes(
                    encode_rgba_png(width, height, b"\xf7\xf7\xf5\xff" * (width * height))
                )
            else:
                screenshot.write_bytes(f"{width}x{height}".encode("ascii"))
            environment = _environment(width, height)
            normalized = validate_capture_environment(environment)
            captures.append(
                {
                    "state_id": state[0],
                    "workspace_alias": state[1],
                    "actor_alias": state[2],
                    "surface": state[3],
                    "expected_state": state[4],
                    "viewport": {"width": width, "height": height},
                    "environment": environment,
                    "environment_sha256": normalized["environment_sha256"],
                    "screenshot_relative_path": relative,
                    "screenshot_sha256": _sha256_file(screenshot),
                    "reference_build_identifier": "notion-reference-build-synthetic-test",
                    "reference_build_sha256": HASH_B,
                    "focus": {
                        "active_alias": "PAGE-CANVAS",
                        "sequence": ["PAGE-CANVAS"],
                    },
                    "accessible_name_role_state": [
                        {
                            "alias": "PAGE-CANVAS",
                            "name": "synthetic page",
                            "role": "region",
                            "state": "visible",
                        }
                    ],
                    "interaction_outcome": {
                        "status": "pass",
                        "expected_state": state[4],
                        "observation": "synthetic verifier fixture",
                    },
                    "captured_at": TIME,
                    "raw_identifier_persisted": False,
                    "personal_content_visible": False,
                }
            )
    cleanup: dict[str, object] = {
        "status": "complete",
        "trigger": "after_success",
        "mode": "trash_only_recoverable",
        "revoked_page_grant_count": 1,
        "revoked_workspace_membership_count": 1,
        "active_test_grant_count": 0,
        "active_fixture_root_count": 0,
        "trashed_fixture_aliases": ["V0R-A-ROOT", "V0R-B-ROOT"],
        "permanent_delete_used": False,
        "completed_at": TIME,
    }
    cleanup["cleanup_receipt_sha256"] = canonical_sha256(cleanup)
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "packet_id": "V0R-P1",
        "receipt_id": "V0R-P1-L6-REFERENCE-MULTIUSER-SYNTHETIC-TEST",
        "recorded_at": TIME,
        "execution_manifest": {
            "path": MANIFEST_REPO_PATH,
            "sha256": _sha256_file(EXECUTION_MANIFEST_PATH),
            "audit_status": "pass",
        },
        "approval_receipt": {
            "path": APPROVAL_REPO_PATH,
            "sha256": _sha256_file(approval_path),
            "approval_id": "AP-REFERENCE-MULTIUSER",
        },
        "run": {
            "run_id": "v0r-p1-synthetic-test",
            "started_at": TIME,
            "ended_at": TIME,
            "browser": "chrome_signed_in_two_independent_test_sessions",
            "chrome_major_version": "140",
            "reference_build_identifier": "notion-reference-build-synthetic-test",
            "reference_build_sha256": HASH_B,
            "workspace_aliases": ["REF-FREE-A", "REF-FREE-B"],
            "identity_aliases": ["REF-OWNER", "REF-COLLABORATOR"],
            "screenshot_root": SCREENSHOT_REPO_ROOT,
            "action_log_sha256": canonical_sha256(actions),
        },
        "actions": actions,
        "captures": captures,
        "cleanup": cleanup,
        "redaction": {
            "status": "pass",
            "raw_email_or_account_identifier_persisted": False,
            "credentials_cookie_token_or_storage_persisted": False,
            "raw_dom_or_network_payload_persisted": False,
            "personal_workspace_or_page_captured": False,
            "non_test_notification_sent": False,
            "public_publish_or_public_link_used": False,
            "permanent_delete_used": False,
        },
        "claims": {
            "owner_approval_received": True,
            "external_reference_mutation_executed": True,
            "reference_action_log_complete": True,
            "reference_screenshots_captured": True,
            "capture_environment_receipts_complete": True,
            "cleanup_executed": True,
            "normalized_evidence_sealed": True,
            "V0R_P1_reference_capture_complete": True,
            "V0R_P1_gate_passed": True,
            "zero_RGBA_difference_claimed": False,
            "PIX_001_closed": False,
            "A11Y_001_closed": False,
            "UI_CORE_25_closed": False,
            "production_changed": False,
            "second_brain_changed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
    }


@pytest.fixture
def cheap_png_decoder(monkeypatch: pytest.MonkeyPatch) -> None:
    def decode(payload: bytes) -> SimpleNamespace:
        width, height = (int(value) for value in payload.decode("ascii").split("x"))
        return SimpleNamespace(width=width, height=height)

    monkeypatch.setattr(
        "code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt.decode_png_srgb_rgba8",
        decode,
    )


@pytest.mark.unit
def test_v0r_p1_complete_receipt_requires_all_36_captures_and_cleanup(
    tmp_path: Path,
    cheap_png_decoder: None,
) -> None:
    approval = _approval_receipt(tmp_path / "approval.json")
    screenshots = tmp_path / "screenshots"
    receipt = _write(tmp_path / "receipt.json", _evidence(screenshots, approval))

    report = verify(
        receipt,
        approval_receipt_path=approval,
        screenshot_root=screenshots,
    )

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["action_count"] == 11
    assert report["capture_count"] == 36
    assert report["capture_state_count"] == 18
    assert report["viewport_count"] == 2
    assert report["cleanup_complete"] is True
    assert report["gate_passed"] is True
    assert report["gate_scope"] == "semantic_reference_capture_and_cleanup"
    assert report["semantic_reference_gate_passed"] is True
    assert report["exact_reference_transport"]["declared"] is False
    assert report["exact_reference_gate_passed"] is False
    assert report["zero_RGBA_difference_claimed"] is False
    assert report["product_complete"] is False


@pytest.mark.integration
def test_v0r_p1_complete_receipt_decodes_all_strict_png_dimensions(
    tmp_path: Path,
) -> None:
    approval = _approval_receipt(tmp_path / "approval.json")
    screenshots = tmp_path / "screenshots"
    receipt = _write(
        tmp_path / "receipt.json",
        _evidence(screenshots, approval, real_png=True),
    )

    report = verify(
        receipt,
        approval_receipt_path=approval,
        screenshot_root=screenshots,
    )

    assert report["status"] == "pass"
    assert report["capture_count"] == 36
    assert report["gate_passed"] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("capture_transport", "exact_gate_passed", "reason_codes"),
    [
        (
            {
                "browser_capture_returned_codec": "png",
                "stored_codec": "png_srgb_rgba8",
                "conversion": "none_source_bytes_copied_without_reencode",
                "source_bytes_preserved": True,
                "lossless_storage_after_decode": True,
                "source_encoding_lossless": True,
                "zero_RGBA_equivalence_claimed": True,
            },
            False,
            ["LOSSLESS_INGEST_AUDIT_NOT_DECLARED"],
        ),
        (
            {
                "browser_capture_returned_codec": "jpeg",
                "stored_codec": "png_srgb_rgba8",
                "lossless_storage_after_decode": True,
                "source_encoding_lossless": False,
                "zero_RGBA_equivalence_claimed": False,
            },
            False,
            [
                "SOURCE_CODEC_NOT_PNG",
                "SOURCE_ENCODING_NOT_LOSSLESS",
                "ZERO_RGBA_EQUIVALENCE_NOT_DECLARED",
            ],
        ),
    ],
)
def test_v0r_p1_reference_verifier_separates_semantic_and_exact_transport_gates(
    tmp_path: Path,
    cheap_png_decoder: None,
    capture_transport: dict[str, object],
    exact_gate_passed: bool,
    reason_codes: list[str],
) -> None:
    approval = _approval_receipt(tmp_path / "approval.json")
    screenshots = tmp_path / "screenshots"
    evidence = _evidence(screenshots, approval)
    evidence["capture_transport"] = capture_transport
    receipt = _write(tmp_path / "receipt.json", evidence)

    report = verify(
        receipt,
        approval_receipt_path=approval,
        screenshot_root=screenshots,
    )

    assert report["status"] == "pass"
    assert report["semantic_reference_gate_passed"] is True
    assert report["exact_reference_gate_passed"] is exact_gate_passed
    assert report["exact_reference_transport"]["reason_codes"] == reason_codes


def _bind_lossless_ingest_audit(
    tmp_path: Path, evidence: dict[str, object]
) -> None:
    transport = evidence["capture_transport"]
    transport.update(
        {
            "browser_capture_returned_codec": "png",
            "stored_codec": "png_srgb_rgba8",
            "conversion": "none_source_bytes_copied_without_reencode",
            "source_bytes_preserved": True,
            "source_encoding_lossless": True,
            "zero_RGBA_equivalence_claimed": True,
        }
    )
    manifest_sha256 = "sha256:" + "a" * 64
    checks = []
    for capture in evidence["captures"]:
        viewport = capture["viewport"]
        checks.append(
            {
                "scenario_id": (
                    f"{capture['state_id']}__{viewport['width']}x{viewport['height']}"
                ),
                "source_bytes_preserved": True,
            }
        )
    audit = {
        "schema_version": 1,
        "contract": LOSSLESS_INGEST_AUDIT_CONTRACT,
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "capture_count": 36,
        "expected_capture_count": 36,
        "capture_state_count": 18,
        "viewport_count": 2,
        "source_bytes_preserved_count": 36,
        "exact_reference_transport_eligible": True,
        "capture_transport": {
            "browser_capture_returned_codec": "png",
            "stored_codec": "png_srgb_rgba8",
            "conversion": "none_source_bytes_copied_without_reencode",
            "source_bytes_preserved": True,
            "source_encoding_lossless": True,
            "zero_RGBA_equivalence_claimed": True,
        },
        "capture_manifest_sha256": manifest_sha256,
        "capture_checks": checks,
    }
    audit_path = _write(tmp_path / "lossless-ingest-audit.json", audit)
    transport["lossless_ingest_audit"] = {
        "relative_path": audit_path.name,
        "sha256": _sha256_file(audit_path),
        "capture_manifest_sha256": manifest_sha256,
    }


@pytest.mark.unit
def test_v0r_p1_reference_verifier_accepts_hash_bound_lossless_ingest_audit(
    tmp_path: Path,
    cheap_png_decoder: None,
) -> None:
    approval = _approval_receipt(tmp_path / "approval.json")
    screenshots = tmp_path / "screenshots"
    evidence = _evidence(screenshots, approval)
    evidence["capture_transport"] = {}
    _bind_lossless_ingest_audit(tmp_path, evidence)
    receipt = _write(tmp_path / "receipt.json", evidence)

    report = verify(
        receipt,
        approval_receipt_path=approval,
        screenshot_root=screenshots,
    )

    assert report["semantic_reference_gate_passed"] is True
    assert report["exact_reference_gate_passed"] is True
    binding = report["exact_reference_transport"]["lossless_ingest_audit"]
    assert binding["declared"] is True
    assert binding["valid"] is True
    assert binding["reason_codes"] == []


@pytest.mark.unit
def test_v0r_p1_reference_verifier_rejects_lossless_ingest_audit_hash_drift(
    tmp_path: Path,
    cheap_png_decoder: None,
) -> None:
    approval = _approval_receipt(tmp_path / "approval.json")
    screenshots = tmp_path / "screenshots"
    evidence = _evidence(screenshots, approval)
    evidence["capture_transport"] = {}
    _bind_lossless_ingest_audit(tmp_path, evidence)
    evidence["capture_transport"]["lossless_ingest_audit"]["sha256"] = (
        "sha256:" + "0" * 64
    )
    receipt = _write(tmp_path / "receipt.json", evidence)

    report = verify(
        receipt,
        approval_receipt_path=approval,
        screenshot_root=screenshots,
    )

    assert report["semantic_reference_gate_passed"] is True
    assert report["exact_reference_gate_passed"] is False
    assert "LOSSLESS_INGEST_AUDIT_HASH_DRIFT" in report[
        "exact_reference_transport"
    ]["reason_codes"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (lambda value: value["captures"].pop(), "CAPTURE_DENOMINATOR"),
        (
            lambda value: value["captures"][0].__setitem__(
                "screenshot_sha256", "sha256:" + "0" * 64
            ),
            "SCREENSHOT_HASH_DRIFT",
        ),
        (
            lambda value: value["captures"][0].__setitem__(
                "screenshot_relative_path", "../escape.png"
            ),
            "SCREENSHOT_PATH",
        ),
        (
            lambda value: value["cleanup"].__setitem__("active_test_grant_count", 1),
            "CLEANUP_EVIDENCE",
        ),
        (
            lambda value: value["redaction"].__setitem__(
                "personal_workspace_or_page_captured", True
            ),
            "REDACTION_EVIDENCE",
        ),
        (
            lambda value: value["claims"].__setitem__("PIX_001_closed", True),
            "CLAIMS_FALSE_GREEN",
        ),
    ],
)
def test_v0r_p1_receipt_rejects_missing_tampered_unsafe_or_false_green_evidence(
    tmp_path: Path,
    cheap_png_decoder: None,
    mutate,
    error_code: str,
) -> None:
    approval = _approval_receipt(tmp_path / "approval.json")
    screenshots = tmp_path / "screenshots"
    value = _evidence(screenshots, approval)
    candidate = copy.deepcopy(value)
    mutate(candidate)
    receipt = _write(tmp_path / "candidate.json", candidate)

    report = verify(
        receipt,
        approval_receipt_path=approval,
        screenshot_root=screenshots,
    )

    assert report["status"] == "fail"
    assert error_code in {item["code"] for item in report["errors"]}
    assert report["gate_passed"] is False


@pytest.mark.unit
def test_v0r_p1_receipt_rejects_tampered_owner_approval(
    tmp_path: Path,
    cheap_png_decoder: None,
) -> None:
    approval = _approval_receipt(tmp_path / "approval.json")
    screenshots = tmp_path / "screenshots"
    value = _evidence(screenshots, approval)
    tampered_approval = json.loads(approval.read_text(encoding="utf-8"))
    tampered_approval["authorization"]["permanent_delete_allowed"] = True
    _write(approval, tampered_approval)
    receipt = _write(tmp_path / "receipt.json", value)

    report = verify(
        receipt,
        approval_receipt_path=approval,
        screenshot_root=screenshots,
    )

    assert report["status"] == "fail"
    assert "APPROVAL_BINDING" in {item["code"] for item in report["errors"]}
    assert report["gate_passed"] is False
