"""Seal a browser-version provenance correction over immutable V0R-P1 v3.

The v3 PNG, mask, ingest, semantic, and cleanup evidence remains immutable.
This wrapper creates a new receipt whose only material correction is binding
the capture windows to the actual Chrome 153.0.8010.12 process.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from code_assist_v1.tools import seal_v0r_corrective_reference_receipt as sealer
from code_assist_v1.tools import (
    seal_v0r_p1_lossless_exact_v3_20260830 as v3,
)
from code_assist_v1.tools import (
    verify_internal_collaborative_reference_multiuser_receipt as receipt_verifier,
)


V3_RECEIPT_REPO_PATH = v3.V3_RECEIPT_REPO_PATH
EXPECTED_V3_RECEIPT_SHA256 = (
    "sha256:b6a8a550455ccfbd9a41dd4a35643f57930ce6c1ad1cad7a0f1faace48ca6984"
)
V4_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-exact-v4-browser-20260830.json"
)
V4_CHECKPOINT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-exact-v4-browser-"
    "cleanup-gate-pass-20260830.json"
)
BROWSER_VERSION_EVIDENCE_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-exact-v3-"
    "browser-version-correction-20260830.json"
)
EXPECTED_BROWSER_VERSION_EVIDENCE_SHA256 = (
    "sha256:7afc09c455c941f1e76b468ed25dbf71a9d91d9110abfc745adb3ed646895689"
)
CHROME_VERSION = "153.0.8010.12"
CHROME_MAJOR_VERSION = "153"
RECEIPT_ID = "V0R-P1-L6-REFERENCE-MULTIUSER-LOSSLESS-EXACT-V4-BROWSER-20260830"
SUPERSESSION_AUTHORITY_TEXT = v3.SUPERSESSION_AUTHORITY_TEXT
REFERENCE_BUILD_IDENTIFIER = (
    "notion-web-live-ui-source-lossless-exact-v4-browser-"
    "provenance-correction-20260830"
)


def _path(repo_path: str) -> Path:
    return v3._path(repo_path)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _configure_sealer(
    *,
    manifest: Mapping[str, Any],
    semantic_review: Mapping[str, Any],
    ingest_audit: Mapping[str, Any],
    audit_sha256: str,
    manifest_file_sha256: str,
) -> None:
    sealer.ORIGINAL_RECEIPT_REPO_PATH = V3_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_RECEIPT_REPO_PATH = V4_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_CHECKPOINT_REPO_PATH = V4_CHECKPOINT_REPO_PATH
    sealer.SCREENSHOT_REPO_ROOT = v3.SCREENSHOT_REPO_ROOT
    sealer.MASK_REPO_ROOT = v3.MASK_REPO_ROOT
    sealer.RAW_REPO_ROOT = v3.RAW_REPO_ROOT
    sealer.PREDECESSOR_REPO_PATH = BROWSER_VERSION_EVIDENCE_REPO_PATH
    sealer.EXPECTED_ORIGINAL_RECEIPT_SHA256 = EXPECTED_V3_RECEIPT_SHA256
    sealer.SUPERSESSION_AUTHORITY_TEXT = SUPERSESSION_AUTHORITY_TEXT
    sealer.CLEANUP_AUTHORITY_TEXT = v3.CLEANUP_AUTHORITY_TEXT
    sealer.REFERENCE_BUILD_IDENTIFIER = REFERENCE_BUILD_IDENTIFIER
    sealer.REQUIRE_EXACT_REFERENCE_GATE = True
    receipt_verifier.ORIGINAL_RECEIPT_REPO_PATH = (
        v3.SEMANTIC_ROOT_RECEIPT_REPO_PATH
    )

    original_build_receipt = sealer.build_receipt
    original_build_checkpoint = sealer.build_checkpoint

    def build_receipt(*, recorded_at: str) -> dict[str, Any]:
        receipt = v3._decorate_receipt(
            original_build_receipt(recorded_at=recorded_at),
            manifest=manifest,
            semantic_review=semantic_review,
            audit=ingest_audit,
            audit_sha256=audit_sha256,
            manifest_file_sha256=manifest_file_sha256,
        )
        receipt["receipt_id"] = RECEIPT_ID
        run = receipt["run"]
        run["run_id"] = "v0r-p1-lossless-exact-v4-browser-reference-20260830"
        run["semantic_action_log_source"] = V3_RECEIPT_REPO_PATH
        run["prior_corrective_receipt_sha256"] = EXPECTED_V3_RECEIPT_SHA256
        run["chrome_major_version"] = CHROME_MAJOR_VERSION
        run["chrome_version"] = CHROME_VERSION
        run["browser_version_evidence"] = {
            "path": BROWSER_VERSION_EVIDENCE_REPO_PATH,
            "sha256": EXPECTED_BROWSER_VERSION_EVIDENCE_SHA256,
            "host_renderer_window_handle": 394984,
            "collaborator_renderer_window_handle": 853918,
            "resolved_process_id": 8288,
            "both_renderer_handles_resolve_to_same_process": True,
        }
        for capture in receipt.get("captures", []):
            environment = dict(capture["environment"])
            environment["browser"] = dict(environment["browser"])
            environment["browser"]["major_version"] = CHROME_MAJOR_VERSION
            normalized_environment = sealer.validate_capture_environment(environment)
            capture["environment"] = {
                name: value
                for name, value in normalized_environment.items()
                if name != "environment_sha256"
            }
            capture["environment_sha256"] = normalized_environment[
                "environment_sha256"
            ]
        receipt["corrective_evidence"]["browser_version_correction"] = {
            "status": "pass",
            "prior_declared_chrome_major_version": "151",
            "corrected_chrome_major_version": CHROME_MAJOR_VERSION,
            "corrected_chrome_version": CHROME_VERSION,
            "evidence_path": BROWSER_VERSION_EVIDENCE_REPO_PATH,
            "evidence_sha256": EXPECTED_BROWSER_VERSION_EVIDENCE_SHA256,
            "reference_png_modified": False,
            "reference_mask_modified": False,
            "v3_receipt_modified": False,
        }
        return receipt

    def build_checkpoint(
        *, recorded_at: str, receipt_sha256: str, audit: dict[str, Any]
    ) -> dict[str, Any]:
        checkpoint = v3._decorate_checkpoint(
            original_build_checkpoint(
                recorded_at=recorded_at,
                receipt_sha256=receipt_sha256,
                audit=audit,
            ),
            audit=ingest_audit,
            audit_sha256=audit_sha256,
            manifest_file_sha256=manifest_file_sha256,
        )
        checkpoint["checkpoint_id"] = (
            "UI-INTERNAL-COLLABORATION-V0R-P1-LOSSLESS-EXACT-V4-"
            "BROWSER-CLEANUP-GATE-PASS-20260830"
        )
        checkpoint["checkpoint_kind"] = (
            "source_lossless_exact_reference_browser_provenance_correction"
        )
        checkpoint["purpose"] = (
            "Preserve the immutable v3 source-lossless evidence while correcting "
            "its inherited browser major to the Chrome 153 process resolved from "
            "both capture renderer handles."
        )
        checkpoint["browser_version_provenance"] = {
            "evidence_path": BROWSER_VERSION_EVIDENCE_REPO_PATH,
            "evidence_sha256": EXPECTED_BROWSER_VERSION_EVIDENCE_SHA256,
            "chrome_version": CHROME_VERSION,
            "chrome_major_version": CHROME_MAJOR_VERSION,
            "status": "pass",
        }
        checkpoint["supersession"]["predecessor_receipt_path"] = (
            V3_RECEIPT_REPO_PATH
        )
        checkpoint["supersession"]["predecessor_receipt_sha256"] = (
            EXPECTED_V3_RECEIPT_SHA256
        )
        checkpoint["supersession"]["original_semantic_receipt_path"] = (
            v3.SEMANTIC_ROOT_RECEIPT_REPO_PATH
        )
        checkpoint["supersession"]["original_semantic_receipt_sha256"] = (
            "sha256:5ef98450166e00b750890599049acc301d99915332f1c1542c7bd777fc98055f"
        )
        checkpoint["next_action"] = (
            "Run V0B-P1 Full36 against the v4 receipt with Chrome major 153, "
            "then require Core25 and quiet-host performance before sealing V0B."
        )
        return checkpoint

    sealer.build_receipt = build_receipt
    sealer.build_checkpoint = build_checkpoint


def main() -> int:
    if sealer._sha256_file(_path(V3_RECEIPT_REPO_PATH)) != (
        EXPECTED_V3_RECEIPT_SHA256
    ):
        raise ValueError("V0R_V4_V3_RECEIPT_HASH_DRIFT")
    if sealer._sha256_file(_path(BROWSER_VERSION_EVIDENCE_REPO_PATH)) != (
        EXPECTED_BROWSER_VERSION_EVIDENCE_SHA256
    ):
        raise ValueError("V0R_V4_BROWSER_VERSION_EVIDENCE_HASH_DRIFT")
    if _path(V4_RECEIPT_REPO_PATH).exists() or _path(
        V4_CHECKPOINT_REPO_PATH
    ).exists():
        raise FileExistsError("V0R_V4_IMMUTABLE_OUTPUT_EXISTS")

    manifest_path = _path(v3.V3_INGEST_MANIFEST_REPO_PATH)
    manifest = _load(manifest_path)
    if not isinstance(manifest, Mapping):
        raise ValueError("V0R_V4_INGEST_MANIFEST_OBJECT_REQUIRED")
    semantic_review = v3._validate_semantic_postconditions(manifest)
    audit, audit_sha256, manifest_file_sha256 = (
        v3._load_or_create_ingest_audit(
            manifest_path=manifest_path,
            screenshot_root=_path(v3.SCREENSHOT_REPO_ROOT),
            audit_path=_path(v3.V3_INGEST_AUDIT_REPO_PATH),
        )
    )
    reviewed_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    v3._regenerate_masks(reviewed_at)
    _configure_sealer(
        manifest=manifest,
        semantic_review=semantic_review,
        ingest_audit=audit,
        audit_sha256=audit_sha256,
        manifest_file_sha256=manifest_file_sha256,
    )
    result = sealer.seal()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
