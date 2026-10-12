"""Read-only verification for one compiled Z0 final-evidence manifest.

This verifier reconstructs the manifest against current repository authority,
the live zero-error SBE contract audit, all seven supporting-suite receipts,
the frozen release candidate, and the production-canary promotion receipt.  It
creates no receipt, executes no suite or operator action, enables no rollout,
and awards no packet or gate credit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import code_assist_v1.tools.compile_internal_collaborative_z0_final_manifest as compiler
import code_assist_v1.tools.internal_collaborative_validation as validation


VERIFY_CONTRACT = (
    "code_assist.notion.internal_collaborative_z0_final_manifest_verification.v1"
)


class Z0ManifestVerifyError(ValueError):
    """The selected manifest cannot be verified under the closed contract."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise Z0ManifestVerifyError("invalid_arguments")


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _report(
    *,
    status: str,
    errors: list[dict[str, str]],
    manifest_id: Any = None,
    manifest_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "report_schema_version": 1,
        "contract": VERIFY_CONTRACT,
        "status": status,
        "error_count": len(errors),
        "errors": errors,
        "manifest_id": manifest_id if isinstance(manifest_id, str) else None,
        "manifest_sha256": manifest_sha256,
        "suite_receipt_count": (
            len(validation.Z0_REQUIRED_SUITE_LANES) if status == "pass" else 0
        ),
        "promotion_receipt_count": 1 if status == "pass" else 0,
        "read_only": True,
        "receipt_created": False,
        "tests_executed_by_verifier": False,
        "operator_actions_executed": False,
        "full_rollout_enabled": False,
        "new_packet_or_gate_credit_awarded": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }


def _collect_watched_evidence(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    manifest_sha256: str,
    authority_paths: set[Path],
) -> dict[Path, str]:
    """Best-effort snapshot of every directly bound immutable input.

    Malformed declarations are deliberately left to the closed central
    validator so its precise contract error remains visible.  Every resolvable
    input is still watched for byte changes during this verification.
    """

    watched = {
        item.resolve(strict=True): validation.sha256_file(item)
        for item in authority_paths
    }
    watched[manifest_path] = manifest_sha256
    suite_bindings = manifest.get("suite_receipts")
    if isinstance(suite_bindings, list):
        for binding in suite_bindings:
            if not isinstance(binding, dict) or not isinstance(
                binding.get("path"), str
            ):
                continue
            try:
                suite_path = compiler._resolve_input(  # noqa: SLF001
                    Path(binding["path"]),
                    root=validation.Z0_FINAL_EVIDENCE_ROOT,
                    label="suite_receipt",
                )
                suite, suite_sha256 = compiler._load_once(suite_path)  # noqa: SLF001
                watched[suite_path] = suite_sha256
                release = (
                    suite.get("release_candidate_manifest")
                    if isinstance(suite, dict)
                    else None
                )
                if isinstance(release, dict) and isinstance(
                    release.get("path"), str
                ):
                    release_path = compiler._resolve_input(  # noqa: SLF001
                        Path(release["path"]),
                        root=validation.PROMOTION_EVIDENCE_ROOT,
                        label="release_manifest",
                    )
                    watched[release_path] = validation.sha256_file(release_path)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                compiler.Z0ManifestCompileError,
            ):
                continue
    promotion = manifest.get("promotion_receipt")
    if isinstance(promotion, dict) and isinstance(promotion.get("path"), str):
        try:
            promotion_path = compiler._resolve_input(  # noqa: SLF001
                Path(promotion["path"]),
                root=validation.DEFAULT_RECEIPTS_ROOT,
                label="promotion_receipt",
            )
            watched[promotion_path] = validation.sha256_file(promotion_path)
        except (OSError, compiler.Z0ManifestCompileError):
            pass
    return watched


def _assert_unchanged(bindings: dict[Path, str]) -> None:
    if any(validation.sha256_file(path) != expected for path, expected in bindings.items()):
        raise Z0ManifestVerifyError("evidence_changed_during_verify")


def verify_z0_final_manifest(
    path: Path,
    *,
    expected_manifest_sha256: str,
) -> tuple[dict[str, Any], int]:
    """Freshly reconstruct one existing immutable manifest without mutation."""

    try:
        expected = validation.normalize_sha256(expected_manifest_sha256)
        if expected is None:
            raise Z0ManifestVerifyError("expected_manifest_sha256_invalid")
        manifest_path = compiler._resolve_input(  # noqa: SLF001
            path,
            root=validation.Z0_FINAL_EVIDENCE_ROOT,
            label="z0_final_manifest",
        )
        manifest, manifest_sha256 = compiler._load_once(manifest_path)  # noqa: SLF001
        if not isinstance(manifest, dict):
            raise Z0ManifestVerifyError("manifest_object_required")
        if manifest_sha256 != expected:
            return (
                _report(
                    status="fail",
                    errors=[
                        _error(
                            "manifest_sha256_mismatch",
                            "z0_final_manifest",
                            "selected manifest bytes differ from the caller-pinned hash",
                        )
                    ],
                    manifest_id=manifest.get("manifest_id"),
                    manifest_sha256=manifest_sha256,
                ),
                1,
            )
        if set(manifest) != validation.Z0_FINAL_MANIFEST_FIELDS:
            raise Z0ManifestVerifyError("manifest_field_set_invalid")

        sbe_report = compiler._current_sbe_report()  # noqa: SLF001
        current, watched_paths = compiler._current_authority(sbe_report)  # noqa: SLF001
        manifest_authority = {
            key: manifest.get(key)
            for key in current
            if key != "goal_status_observed"
        }
        expected_authority = {
            key: value
            for key, value in current.items()
            if key != "goal_status_observed"
        }
        if manifest_authority != expected_authority:
            raise Z0ManifestVerifyError("manifest_current_authority_mismatch")

        watched_hashes = _collect_watched_evidence(
            manifest,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            authority_paths=watched_paths,
        )
        ladder = validation.load_json(validation.DEFAULT_LADDER)
        if not isinstance(ladder, dict):
            raise Z0ManifestVerifyError("validation_ladder_invalid")
        errors = validation._validate_z0_final_evidence(  # noqa: SLF001
            compiler._staged_generic_receipt(  # noqa: SLF001
                manifest,
                manifest_path=manifest_path,
                manifest_sha256=manifest_sha256,
            ),
            "z0_final_manifest",
            ladder=ladder,
            sbe_report=sbe_report,
        )
        if errors:
            return (
                _report(
                    status="fail",
                    errors=errors,
                    manifest_id=manifest.get("manifest_id"),
                    manifest_sha256=manifest_sha256,
                ),
                1,
            )

        _assert_unchanged(watched_hashes)
        current_sbe = compiler._current_sbe_report()  # noqa: SLF001
        if current_sbe.get("input_sha256") != sbe_report.get("input_sha256"):
            raise Z0ManifestVerifyError("sbe_contract_audit_changed_during_verify")
        current_after, watched_paths_after = compiler._current_authority(  # noqa: SLF001
            current_sbe
        )
        if current_after != current or watched_paths_after != watched_paths:
            raise Z0ManifestVerifyError("authority_changed_during_verify")
        _assert_unchanged(watched_hashes)
        return (
            _report(
                status="pass",
                errors=[],
                manifest_id=manifest["manifest_id"],
                manifest_sha256=manifest_sha256,
            ),
            0,
        )
    except FileNotFoundError:
        code = "MANIFEST_NOT_FOUND"
    except Z0ManifestVerifyError as exc:
        code = str(exc) or "Z0_FINAL_MANIFEST_VERIFY_FAILED"
    except compiler.Z0ManifestCompileError as exc:
        code = str(exc) or "Z0_FINAL_MANIFEST_VERIFY_FAILED"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError):
        code = "Z0_FINAL_MANIFEST_VERIFY_FAILED"
    return (
        _report(
            status="fail",
            errors=[
                _error(
                    code,
                    "z0_final_manifest",
                    "the selected final manifest could not be reconstructed",
                )
            ],
        ),
        1,
    )


def _parser() -> _Parser:
    parser = _Parser(prog="verify-internal-collaborative-z0-final-manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    compact = False
    try:
        arguments = _parser().parse_args(argv)
        compact = arguments.compact
        report, exit_code = verify_z0_final_manifest(
            arguments.manifest,
            expected_manifest_sha256=arguments.expected_manifest_sha256,
        )
    except Z0ManifestVerifyError as exc:
        report = _report(
            status="fail",
            errors=[
                _error(
                    str(exc) or "invalid_arguments",
                    "arguments",
                    "the verifier arguments are invalid",
                )
            ],
        )
        exit_code = 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if compact else 2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "VERIFY_CONTRACT",
    "Z0ManifestVerifyError",
    "main",
    "verify_z0_final_manifest",
]
