"""Create and verify sealed Core Free UI evidence packets.

The packet is test/evidence infrastructure only.  It writes exclusively below
an explicitly supplied isolated test root, never discovers application data,
and imports no application or browser runtime.  JSON artifacts are canonical
and the manifest is the final commit marker.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any, Iterable, Mapping, Sequence
import uuid

from code_assist_v1.test_safety import assert_isolated_test_path
from code_assist_v1.tools.notion_parity.evidence import canonical_sha256, load_json
from code_assist_v1.tools.notion_parity.ui_actions import (
    LOG_KEYS,
    PROFILE_ID,
    validate_action_sequence,
)
from code_assist_v1.tools.notion_parity.ui_capture import validate_capture
from code_assist_v1.tools.notion_parity.ui_evidence import (
    PROFILE,
    validate_evidence_manifest,
)
from code_assist_v1.tools.notion_parity.ui_reference_guard import (
    ReferenceC0Decision,
    reference_c0_decision_from_descriptor,
)


CONTRACT = "notion_core_free_ui_evidence_packet_v1"
MANIFEST_NAME = "manifest.json"
SEAL_NAME = "packet-seal.json"
SEAL_CONTRACT = "notion_core_free_ui_evidence_packet_seal_v1"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024
_DIFFERENTIAL_KEYS = frozenset(
    {
        "report_schema_version",
        "status",
        "reference_sha256",
        "candidate_sha256",
        "policy_sha256",
        "difference_count",
        "differences",
    }
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SEAL_KEYS = frozenset(
    {
        "contract",
        "manifest_sha256",
        "artifact_index_sha256",
        "reference_c0",
        "reference_c0_sha256",
    }
)


class EvidencePacketError(ValueError):
    """Raised before promotion when an evidence packet is unsafe or invalid."""


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _sorted(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {
        (item["code"], item["subject"], item["message"]): item for item in errors
    }
    return [unique[key] for key in sorted(unique)]


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvidencePacketError("artifact is not canonical JSON") from exc
    return payload.encode("utf-8") + b"\n"


def _bytes_sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def redaction_allowlist_sha256(values: Sequence[str]) -> str:
    """Bind the caller-supplied synthetic strings to the packet manifest."""

    if any(not isinstance(value, str) or not value for value in values):
        raise EvidencePacketError("synthetic allowlist entries must be non-empty strings")
    return canonical_sha256(
        {
            "contract": "notion_core_free_ui_redaction_allowlist_v1",
            "values": sorted(set(values)),
        }
    )


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _isolated_root(
    test_root: str | os.PathLike[str],
) -> Path:
    raw_root = Path(test_root)
    root = assert_isolated_test_path(raw_root, test_root=raw_root)
    if not root.is_dir():
        raise EvidencePacketError("isolated evidence root must already exist")
    if _is_link_like(raw_root):
        raise EvidencePacketError("isolated evidence root cannot be a link or reparse point")
    return root


def _safe_artifact_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise EvidencePacketError("artifact path is not a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EvidencePacketError("artifact path is not a safe relative path")
    return path


def _artifact_descriptors(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    action_log = manifest.get("action_log")
    if isinstance(action_log, dict):
        descriptors.append(action_log)
    captures = manifest.get("captures")
    if isinstance(captures, dict):
        for side in ("reference", "local"):
            item = captures.get(side)
            if isinstance(item, dict):
                descriptors.append(item)
    differential = manifest.get("differential")
    if isinstance(differential, dict):
        descriptors.append(differential)
    screenshots = manifest.get("screenshots")
    if isinstance(screenshots, list):
        descriptors.extend(item for item in screenshots if isinstance(item, dict))

    output: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        raw_path = descriptor.get("path")
        path = str(_safe_artifact_path(raw_path))
        if path in {MANIFEST_NAME, SEAL_NAME} or path in output:
            raise EvidencePacketError("artifact paths must be unique and cannot name the manifest")
        output[path] = descriptor
    return output


def _artifact_index_sha256(descriptors: Mapping[str, Mapping[str, Any]]) -> str:
    return canonical_sha256(
        [
            {
                "kind": descriptors[path].get("kind"),
                "path": path,
                "sha256": descriptors[path].get("sha256"),
            }
            for path in sorted(descriptors)
        ]
    )


def _build_packet_seal(
    manifest: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    decision: ReferenceC0Decision | None,
) -> dict[str, Any]:
    captures = manifest.get("captures")
    reference_required = bool(
        isinstance(captures, dict) and isinstance(captures.get("reference"), dict)
    )
    if reference_required:
        if not isinstance(decision, ReferenceC0Decision):
            raise EvidencePacketError("reference evidence requires a C0 decision")
        if decision.run_id != manifest.get("run_id"):
            raise EvidencePacketError("C0 decision run does not match the manifest")
        if decision.status != "pass" or not decision.capture_allowed:
            raise EvidencePacketError("reference evidence requires a passing C0 decision")
        c0_descriptor: dict[str, Any] | None = decision.descriptor()
        c0_sha256: str | None = decision.sha256()
    else:
        if decision is not None:
            raise EvidencePacketError("C0 decision is only valid for reference evidence")
        c0_descriptor = None
        c0_sha256 = None
    return {
        "contract": SEAL_CONTRACT,
        "manifest_sha256": canonical_sha256(manifest),
        "artifact_index_sha256": _artifact_index_sha256(descriptors),
        "reference_c0": c0_descriptor,
        "reference_c0_sha256": c0_sha256,
    }


def _validate_packet_seal(
    seal: Any,
    manifest: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, str]], str | None]:
    errors: list[dict[str, str]] = []
    if not isinstance(seal, dict) or set(seal) != _SEAL_KEYS:
        return [_error("SEAL_FIELDS", SEAL_NAME, "packet seal fields are not exact")], None
    if seal.get("contract") != SEAL_CONTRACT:
        errors.append(_error("SEAL_CONTRACT", SEAL_NAME, "packet seal contract is invalid"))
    if seal.get("manifest_sha256") != canonical_sha256(manifest):
        errors.append(_error("SEAL_MANIFEST", SEAL_NAME, "packet seal does not bind the manifest"))
    if seal.get("artifact_index_sha256") != _artifact_index_sha256(descriptors):
        errors.append(_error("SEAL_ARTIFACTS", SEAL_NAME, "packet seal does not bind the artifact index"))

    captures = manifest.get("captures")
    reference_required = bool(
        isinstance(captures, dict) and isinstance(captures.get("reference"), dict)
    )
    descriptor = seal.get("reference_c0")
    digest = seal.get("reference_c0_sha256")
    if reference_required:
        try:
            decision = reference_c0_decision_from_descriptor(descriptor)
        except (TypeError, ValueError):
            errors.append(_error("SEAL_C0", SEAL_NAME, "packet seal C0 decision is invalid"))
        else:
            if decision.run_id != manifest.get("run_id"):
                errors.append(_error("SEAL_C0_RUN", SEAL_NAME, "C0 run does not match manifest"))
            if decision.status != "pass" or not decision.capture_allowed:
                errors.append(_error("SEAL_C0_STATUS", SEAL_NAME, "C0 decision did not pass"))
            if digest != decision.sha256():
                errors.append(_error("SEAL_C0_HASH", SEAL_NAME, "C0 decision hash does not match"))
    elif descriptor is not None or digest is not None:
        errors.append(_error("SEAL_C0_UNEXPECTED", SEAL_NAME, "C0 decision is unexpected without reference evidence"))
    return _sorted(errors), digest if isinstance(digest, str) else None


def _validate_action_log(
    value: Any,
    manifest: Mapping[str, Any],
    synthetic_allowlist: Sequence[str],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(value, dict):
        return [_error("ACTION_LOG_TYPE", "$", "action log must be an object")]
    for key in sorted(LOG_KEYS - set(value)):
        errors.append(_error("FIELD_MISSING", f"$.{key}", "required field is missing"))
    for key in sorted(set(value) - LOG_KEYS):
        errors.append(_error("UNKNOWN_FIELD", f"$.{key}", "unknown field"))
    if value.get("action_log_schema_version") != 1:
        errors.append(_error("ACTION_LOG_SCHEMA", "$.action_log_schema_version", "unsupported action log schema"))
    if value.get("scenario_id") != manifest.get("scenario_id"):
        errors.append(_error("ACTION_LOG_SCENARIO", "$.scenario_id", "scenario does not match manifest"))
    if value.get("run_id") != manifest.get("run_id"):
        errors.append(_error("ACTION_LOG_RUN", "$.run_id", "run does not match manifest"))
    if value.get("profile_id") != PROFILE_ID:
        errors.append(_error("ACTION_LOG_PROFILE", "$.profile_id", "profile does not match Core UI"))
    if value.get("side") not in {"reference", "local"}:
        errors.append(_error("ACTION_LOG_SIDE", "$.side", "side must be reference or local"))
    errors.extend(
        validate_action_sequence(
            value.get("actions"),
            scenario_id=str(manifest.get("scenario_id", "")),
            synthetic_allowlist=synthetic_allowlist,
        )
    )
    return _sorted(errors)


def _validate_differential(
    value: Any,
    manifest_item: Mapping[str, Any],
    capture_hashes: Mapping[str, str],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(value, dict):
        return [_error("DIFFERENTIAL_TYPE", "$", "differential must be an object")]
    for key in sorted(_DIFFERENTIAL_KEYS - set(value)):
        errors.append(_error("FIELD_MISSING", f"$.{key}", "required field is missing"))
    for key in sorted(set(value) - _DIFFERENTIAL_KEYS):
        errors.append(_error("UNKNOWN_FIELD", f"$.{key}", "unknown field"))
    if value.get("report_schema_version") != 1:
        errors.append(_error("DIFFERENTIAL_SCHEMA", "$.report_schema_version", "unsupported differential schema"))
    if value.get("status") != manifest_item.get("status"):
        errors.append(_error("DIFFERENTIAL_STATUS", "$.status", "status does not match manifest"))
    for field in ("reference_sha256", "candidate_sha256", "policy_sha256"):
        if value.get(field) != manifest_item.get(field):
            errors.append(_error("DIFFERENTIAL_LINK", f"$.{field}", "hash does not match manifest"))
    if capture_hashes.get("reference") != value.get("reference_sha256"):
        errors.append(_error("DIFFERENTIAL_CAPTURE", "$.reference_sha256", "reference capture hash does not match"))
    if capture_hashes.get("local") != value.get("candidate_sha256"):
        errors.append(_error("DIFFERENTIAL_CAPTURE", "$.candidate_sha256", "local capture hash does not match"))
    count = value.get("difference_count")
    differences = value.get("differences")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        errors.append(_error("DIFFERENCE_COUNT", "$.difference_count", "difference count must be a non-negative integer"))
    if not isinstance(differences, list):
        errors.append(_error("DIFFERENCES_TYPE", "$.differences", "differences must be an array"))
    elif isinstance(count, int) and not isinstance(count, bool) and count != len(differences):
        errors.append(_error("DIFFERENCE_COUNT", "$.difference_count", "difference count does not match array"))
    if value.get("status") == "pass" and count != 0:
        errors.append(_error("DIFFERENTIAL_STATUS", "$.status", "passing differential must have zero differences"))
    if value.get("status") == "fail" and count == 0:
        errors.append(_error("DIFFERENTIAL_STATUS", "$.status", "failing differential must have differences"))
    return _sorted(errors)


def _validate_packet_values(
    manifest: Any,
    json_artifacts: Mapping[str, Any],
    redacted_screenshots: Mapping[str, bytes],
    *,
    synthetic_allowlist: Sequence[str],
) -> list[dict[str, str]]:
    errors = list(validate_evidence_manifest(manifest))
    if errors or not isinstance(manifest, dict):
        return _sorted(errors)
    try:
        allowlist_digest = redaction_allowlist_sha256(synthetic_allowlist)
    except EvidencePacketError as exc:
        errors.append(_error("REDACTION_ALLOWLIST", "$.redaction", str(exc)))
    else:
        redaction = manifest.get("redaction")
        if not isinstance(redaction, dict) or redaction.get("allowlist_sha256") != allowlist_digest:
            errors.append(
                _error(
                    "REDACTION_ALLOWLIST_HASH",
                    "$.redaction.allowlist_sha256",
                    "redaction allowlist hash does not match the supplied allowlist",
                )
            )
    if errors:
        return _sorted(errors)
    try:
        descriptors = _artifact_descriptors(manifest)
    except EvidencePacketError as exc:
        return [_error("ARTIFACT_PATH", "$", str(exc))]
    supplied = set(json_artifacts) | set(redacted_screenshots)
    expected = set(descriptors)
    for path in sorted(expected - supplied):
        errors.append(_error("ARTIFACT_MISSING", path, "referenced artifact was not supplied"))
    for path in sorted(supplied - expected):
        errors.append(_error("ARTIFACT_EXTRA", path, "unreferenced artifact was supplied"))
    for path in sorted(set(json_artifacts) & set(redacted_screenshots)):
        errors.append(_error("ARTIFACT_DUPLICATE", path, "artifact was supplied twice"))

    capture_hashes: dict[str, str] = {}
    for path in sorted(expected & supplied):
        descriptor = descriptors[path]
        kind = descriptor.get("kind")
        if path in json_artifacts:
            value = json_artifacts[path]
            try:
                size = len(_canonical_json_bytes(value))
            except EvidencePacketError:
                errors.append(_error("ARTIFACT_JSON", path, "artifact is not canonical JSON"))
                continue
            if size > MAX_JSON_BYTES:
                errors.append(_error("ARTIFACT_SIZE", path, "JSON artifact exceeds the bounded size"))
            digest = canonical_sha256(value)
            if kind == "action_log":
                errors.extend(_validate_action_log(value, manifest, synthetic_allowlist))
            elif kind == "capture":
                capture_errors = validate_capture(value, synthetic_allowlist=synthetic_allowlist)
                errors.extend(
                    _error(item["code"], f"{path}:{item['subject']}", item["message"])
                    for item in capture_errors
                )
                if isinstance(value, dict):
                    side = value.get("side")
                    if side in {"reference", "local"}:
                        capture_hashes[side] = digest
                    if value.get("scenario_id") != manifest.get("scenario_id"):
                        errors.append(_error("CAPTURE_SCENARIO", path, "capture scenario does not match manifest"))
                    if value.get("profile") != PROFILE:
                        errors.append(_error("CAPTURE_PROFILE", path, "capture profile does not match manifest"))
                    action_descriptor = manifest.get("action_log")
                    if isinstance(action_descriptor, dict) and value.get("action_log_sha256") != action_descriptor.get("sha256"):
                        errors.append(_error("CAPTURE_ACTION_LOG", path, "capture action log hash does not match"))
                    if value.get("source_build_sha256") != manifest.get("source_build_sha256"):
                        errors.append(_error("CAPTURE_SOURCE", path, "capture source hash does not match manifest"))
            elif kind == "differential":
                pass
            else:
                errors.append(_error("ARTIFACT_KIND", path, "this artifact kind must not contain JSON"))
        else:
            payload = redacted_screenshots[path]
            if kind != "screenshot_redacted":
                errors.append(_error("ARTIFACT_KIND", path, "only redacted screenshots may contain binary data"))
                continue
            if not isinstance(payload, bytes) or not payload.startswith(_PNG_SIGNATURE):
                errors.append(_error("SCREENSHOT_FORMAT", path, "redacted screenshot must be PNG bytes"))
                continue
            if not path.lower().endswith(".png"):
                errors.append(_error("SCREENSHOT_PATH", path, "redacted screenshot path must end in .png"))
            if len(payload) > MAX_SCREENSHOT_BYTES:
                errors.append(_error("ARTIFACT_SIZE", path, "screenshot exceeds the bounded size"))
            digest = _bytes_sha256(payload)
        if descriptor.get("sha256") != digest:
            errors.append(_error("ARTIFACT_HASH", path, "artifact hash does not match manifest"))

    captures = manifest.get("captures")
    if isinstance(captures, dict):
        for side in ("reference", "local"):
            descriptor = captures.get(side)
            if isinstance(descriptor, dict) and capture_hashes.get(side) != descriptor.get("sha256"):
                errors.append(_error("CAPTURE_SIDE", descriptor.get("path", side), "capture side or hash does not match manifest"))

    differential_descriptor = manifest.get("differential")
    if isinstance(differential_descriptor, dict):
        path = differential_descriptor.get("path")
        if isinstance(path, str) and path in json_artifacts:
            errors.extend(
                _error(item["code"], f"{path}:{item['subject']}", item["message"])
                for item in _validate_differential(
                    json_artifacts[path], differential_descriptor, capture_hashes
                )
            )

    screenshot_hashes = {
        descriptor.get("sha256")
        for descriptor in manifest.get("screenshots", [])
        if isinstance(descriptor, dict)
    }
    if isinstance(captures, dict):
        for side in ("reference", "local"):
            descriptor = captures.get(side)
            if not isinstance(descriptor, dict):
                continue
            value = json_artifacts.get(descriptor.get("path"))
            screenshot = value.get("screenshot_sha256") if isinstance(value, dict) else None
            if screenshot is not None and screenshot not in screenshot_hashes:
                errors.append(_error("SCREENSHOT_LINK", str(descriptor.get("path")), "capture screenshot is not in the manifest"))
    return _sorted(errors)


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _actual_packet_files(packet_dir: Path) -> tuple[set[str], list[dict[str, str]]]:
    paths: set[str] = set()
    errors: list[dict[str, str]] = []
    for path in packet_dir.rglob("*"):
        relative = path.relative_to(packet_dir).as_posix()
        if _is_link_like(path):
            errors.append(_error("PACKET_LINK", relative, "links and reparse points are forbidden"))
        elif path.is_file():
            paths.add(relative)
        elif not path.is_dir():
            errors.append(_error("PACKET_ENTRY", relative, "unsupported filesystem entry"))
    return paths, errors


def verify_evidence_packet(
    packet_dir: str | os.PathLike[str],
    *,
    test_root: str | os.PathLike[str],
    synthetic_allowlist: Sequence[str] = (),
) -> tuple[dict[str, Any], int]:
    """Verify exact packet membership, canonical bytes, hashes and cross-links."""

    errors: list[dict[str, str]] = []
    root = _isolated_root(test_root)
    packet = assert_isolated_test_path(packet_dir, test_root=root)
    if packet.parent != root:
        errors.append(_error("PACKET_LOCATION", "$", "packet must be an immediate child of the evidence root"))
    if not packet.is_dir() or _is_link_like(packet):
        errors.append(_error("PACKET_DIRECTORY", "$", "packet directory is missing or unsafe"))
    manifest: Any = None
    seal: Any = None
    json_artifacts: dict[str, Any] = {}
    screenshots: dict[str, bytes] = {}
    descriptors: dict[str, dict[str, Any]] = {}
    if not errors:
        manifest_path = packet / MANIFEST_NAME
        try:
            manifest = load_json(manifest_path)
            if manifest_path.read_bytes() != _canonical_json_bytes(manifest):
                errors.append(_error("MANIFEST_CANONICAL", MANIFEST_NAME, "manifest bytes are not canonical"))
            descriptors = _artifact_descriptors(manifest)
        except (OSError, ValueError, EvidencePacketError):
            errors.append(_error("MANIFEST_READ", MANIFEST_NAME, "manifest is missing or invalid"))
    if not errors:
        seal_path = packet / SEAL_NAME
        try:
            seal = load_json(seal_path)
            if seal_path.read_bytes() != _canonical_json_bytes(seal):
                errors.append(_error("SEAL_CANONICAL", SEAL_NAME, "packet seal bytes are not canonical"))
        except (OSError, ValueError, EvidencePacketError):
            errors.append(_error("SEAL_READ", SEAL_NAME, "packet seal is missing or invalid"))
    reference_c0_sha256: str | None = None
    if not errors:
        seal_errors, reference_c0_sha256 = _validate_packet_seal(
            seal, manifest, descriptors
        )
        errors.extend(seal_errors)
    if not errors:
        actual_files, filesystem_errors = _actual_packet_files(packet)
        errors.extend(filesystem_errors)
        expected_files = {MANIFEST_NAME, SEAL_NAME, *descriptors}
        for path in sorted(expected_files - actual_files):
            errors.append(_error("PACKET_FILE_MISSING", path, "packet file is missing"))
        for path in sorted(actual_files - expected_files):
            errors.append(_error("PACKET_FILE_EXTRA", path, "packet contains an unreferenced file"))
        if not errors:
            for path, descriptor in descriptors.items():
                artifact_path = packet.joinpath(*PurePosixPath(path).parts)
                try:
                    payload = artifact_path.read_bytes()
                except OSError:
                    errors.append(_error("ARTIFACT_READ", path, "artifact could not be read"))
                    continue
                if descriptor.get("kind") == "screenshot_redacted":
                    screenshots[path] = payload
                    continue
                try:
                    value = load_json(artifact_path)
                    if payload != _canonical_json_bytes(value):
                        errors.append(_error("ARTIFACT_CANONICAL", path, "JSON artifact bytes are not canonical"))
                    json_artifacts[path] = value
                except (OSError, ValueError, EvidencePacketError):
                    errors.append(_error("ARTIFACT_JSON", path, "JSON artifact is invalid"))
    if not errors:
        errors.extend(
            _validate_packet_values(
                manifest,
                json_artifacts,
                screenshots,
                synthetic_allowlist=synthetic_allowlist,
            )
        )
    errors = _sorted(errors)
    report = {
        "report_schema_version": 1,
        "contract": CONTRACT,
        "status": "pass" if not errors else "fail",
        "manifest_sha256": canonical_sha256(manifest) if isinstance(manifest, dict) else None,
        "seal_sha256": canonical_sha256(seal) if isinstance(seal, dict) else None,
        "reference_c0_sha256": reference_c0_sha256,
        "artifact_count": len(descriptors),
        "error_count": len(errors),
        "errors": errors,
    }
    return report, 0 if not errors else 1


def write_evidence_packet(
    *,
    test_root: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    json_artifacts: Mapping[str, Any],
    redacted_screenshots: Mapping[str, bytes] | None = None,
    synthetic_allowlist: Sequence[str] = (),
    reference_c0_decision: ReferenceC0Decision | None = None,
) -> dict[str, Any]:
    """Write a new sealed packet and return an identifier/path-free receipt."""

    root = _isolated_root(test_root)
    screenshots = {} if redacted_screenshots is None else dict(redacted_screenshots)
    errors = _validate_packet_values(
        manifest,
        json_artifacts,
        screenshots,
        synthetic_allowlist=synthetic_allowlist,
    )
    if errors:
        first = errors[0]
        raise EvidencePacketError(f"{first['code']}:{first['subject']}")
    descriptors = _artifact_descriptors(manifest)
    seal = _build_packet_seal(manifest, descriptors, reference_c0_decision)
    run_id = manifest["run_id"]
    scenario_id = manifest["scenario_id"]
    packet_name = f"{run_id}--{scenario_id}"
    packet = root / packet_name
    if packet.exists():
        raise EvidencePacketError("evidence packet already exists")
    staging = root / f".{packet_name}.staging-{uuid.uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        for path in sorted(json_artifacts):
            target = staging.joinpath(*_safe_artifact_path(path).parts)
            _write_exclusive(target, _canonical_json_bytes(json_artifacts[path]))
        for path in sorted(screenshots):
            target = staging.joinpath(*_safe_artifact_path(path).parts)
            _write_exclusive(target, screenshots[path])
        _write_exclusive(staging / SEAL_NAME, _canonical_json_bytes(seal))
        _write_exclusive(staging / MANIFEST_NAME, _canonical_json_bytes(manifest))
        report, exit_code = verify_evidence_packet(
            staging,
            test_root=root,
            synthetic_allowlist=synthetic_allowlist,
        )
        if exit_code:
            raise EvidencePacketError("written evidence packet did not verify")
        os.replace(staging, packet)
    except Exception:
        if staging.exists() and staging.is_dir() and not _is_link_like(staging):
            shutil.rmtree(staging)
        raise
    final_report, final_exit_code = verify_evidence_packet(
        packet,
        test_root=root,
        synthetic_allowlist=synthetic_allowlist,
    )
    if final_exit_code:
        raise EvidencePacketError("final evidence packet did not verify")
    return {
        "contract": CONTRACT,
        "status": "pass",
        "scenario_id": scenario_id,
        "run_id": run_id,
        "manifest_sha256": final_report["manifest_sha256"],
        "seal_sha256": final_report["seal_sha256"],
        "reference_c0_sha256": final_report["reference_c0_sha256"],
        "artifact_count": final_report["artifact_count"],
        "path_disclosed": False,
    }


__all__ = [
    "CONTRACT",
    "EvidencePacketError",
    "MANIFEST_NAME",
    "SEAL_NAME",
    "SEAL_CONTRACT",
    "redaction_allowlist_sha256",
    "verify_evidence_packet",
    "write_evidence_packet",
]
