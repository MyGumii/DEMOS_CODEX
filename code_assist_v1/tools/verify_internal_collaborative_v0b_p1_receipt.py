"""Verify the V0B-P1 final-candidate visual and accessibility gate receipt."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.notion_parity.exact_pixel import (
    ExactPixelContractError,
    canonical_sha256,
    compare_decoded_rgba8,
    compare_pngs_exact,
    decode_png_srgb_rgba8,
    validate_capture_environment,
    validate_exact_pixel_receipt,
)
from code_assist_v1.tools.notion_parity.visual_similarity import (
    VisualSimilarityContractError,
    compare_decoded_rgba8_similarity,
    compare_pngs_similarity,
    validate_visual_similarity_policy,
    validate_visual_similarity_receipt,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    EXPECTED_STATES,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt import (
    verify as verify_reference_receipt,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
REFERENCE_RECEIPT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_receipts"
    / "v0r-p1-l6-reference-multiuser-lossless-exact-v4-browser-20260830.json"
)
REFERENCE_SCREENSHOT_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_reference"
    / "v0r-p1-lossless-exact-v3-20260830"
    / "screenshots"
)
REFERENCE_MASK_ROOT = REFERENCE_SCREENSHOT_ROOT.parent / "masks"
SCENARIOS_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "scenarios.v1.json"
)
VISUAL_SIMILARITY_POLICY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_v0b_p1_visual_similarity_policy.v2.json"
)
RECEIPT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_receipts"
    / "v0b-p1-l6-final-candidate-20260826.json"
)
CANDIDATE_SCREENSHOT_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_candidate"
    / "v0b-p1"
    / "screenshots"
)
DIFF_ROOT = CANDIDATE_SCREENSHOT_ROOT.parent / "diffs"
CONTRACT = "code_assist.notion.internal_collaborative_final_candidate.v0b_p1.v1"
REFERENCE_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-exact-v4-browser-20260830.json"
)
REFERENCE_SCREENSHOT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_reference/v0r-p1-lossless-exact-v3-20260830/screenshots"
)
CANDIDATE_SCREENSHOT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_candidate/v0b-p1/screenshots"
)
DIFF_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_candidate/v0b-p1/diffs"
)
VISUAL_SIMILARITY_POLICY_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/"
    "internal_collaborative_v0b_p1_visual_similarity_policy.v2.json"
)
PRODUCT_CAPTURE_PIPELINE_CONTRACT = (
    "code_assist.notion.internal_collaborative_v0b_p1.product_capture.v5"
)
PRODUCT_CAPTURE_REPO_PATH = (
    "code_assist_v1/notion_collaboration/v0b_p1_product_capture.py"
)
PRODUCT_CAPTURE_DRIVER_REPO_PATH = (
    "code_assist_v1/notion_collaboration/v0b_p1_browser.py"
)
PRODUCT_CAPTURE_TRANSITIVE_REPO_PATHS = (
    "code_assist_v1/tools/notion_parity/exact_pixel.py",
    "code_assist_v1/tools/notion_parity/visual_similarity.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/notion_collaboration/v0b_p1_product_runtime.py",
    "code_assist_v1/notion_collaboration/v0b_p1_iam_browser.py",
    "code_assist_v1/notion_collaboration/c0_p2_browser.py",
    "code_assist_v1/notion_collaboration/e1_p2_browser.py",
    "code_assist_v1/notion_collaboration/f0_p5_browser.py",
)
SOURCE_REPO_PATHS = (
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/notion_static/notion.css",
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/notion_static/assets/emoji/1f3e0.svg",
    "code_assist_v1/notion_static/assets/emoji/1f44b.svg",
    "code_assist_v1/notion_static/assets/emoji/TWEMOJI_ATTRIBUTION.md",
)
RUNTIME_SOURCE_REPO_PATHS = (
    "code_assist_v1/routes_notion_workspace.py",
    "code_assist_v1/notion_collaboration/canonical_authority.py",
    "code_assist_v1/notion_collaboration/iam_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/protocol_contract.py",
    "code_assist_v1/notion_collaboration/durable_transport.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/live_authority.py",
    "code_assist_v1/notion_collaboration/database_features.py",
    "code_assist_v1/notion_collaboration/runtime_gateway.py",
    "code_assist_v1/notion_collaboration/gateway_service.py",
)
EXPECTED_CAPTURE_KEYS = {
    (state[0], width, height)
    for state in EXPECTED_STATES
    for width, height in ((1440, 900), (1280, 800))
}
EXPECTED_STATE_MAP = {
    state[0]: {
        "workspace_alias": state[1],
        "actor_alias": state[2],
        "surface": state[3],
        "expected_state": state[4],
    }
    for state in EXPECTED_STATES
}
EXPECTED_CRITICAL_REGION_IDS = {
    "V0R-MU-001": "CRITICAL-INVITE-CONTROLS",
    "V0R-MU-002": "CRITICAL-MEMBER-ROLE-ROWS",
    "V0R-MU-003": "CRITICAL-WORKSPACE-ROOT-NAV",
    "V0R-MU-004": "CRITICAL-PAGE-EDIT-SHARE",
    "V0R-MU-005": "CRITICAL-EDITABLE-PAGE",
    "V0R-MU-006": "CRITICAL-INHERITED-ACCESS",
    "V0R-MU-007": "CRITICAL-PAGE-HEADER",
    "V0R-MU-008": "CRITICAL-REMOTE-EDITOR",
    "V0R-MU-009": "CRITICAL-TEXT-CONVERGENCE",
    "V0R-MU-010": "CRITICAL-BLOCK-CONVERGENCE",
    "V0R-MU-011": "CRITICAL-DATABASE-CONVERGENCE",
    "V0R-MU-012": "CRITICAL-COMMENT-THREAD",
    "V0R-MU-013": "CRITICAL-MENTION-INBOX",
    "V0R-MU-014": "CRITICAL-RECONNECT-PROJECTION",
    "V0R-MU-015": "CRITICAL-COMMENT-CAPABILITY",
    "V0R-MU-016": "CRITICAL-VIEW-CAPABILITY",
    "V0R-MU-017": "CRITICAL-PAGE-ACCESS-BOUNDARY",
    "V0R-MU-018": "CRITICAL-WORKSPACE-ACCESS-BOUNDARY",
}
EXPECTED_ACTOR_SUBJECTS = {
    "REF-OWNER": frozenset({"admin"}),
    "REF-COLLABORATOR": frozenset({"user01"}),
    "REF-OWNER+REF-COLLABORATOR": frozenset({"admin", "user01"}),
}
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMPARISON_CACHE: dict[tuple[object, ...], Any] = {}
_DEFAULT_EXACT_COMPARATOR = compare_pngs_exact
_DEFAULT_SIMILARITY_COMPARATOR = compare_pngs_similarity


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _cached_comparison(
    kind: str,
    comparator: Any,
    reference_png: bytes,
    candidate_png: bytes,
    **kwargs: Any,
) -> Any:
    """Reuse a comparison only for identical bytes, inputs, and comparator."""

    key = _comparison_cache_key(
        kind,
        comparator,
        reference_png,
        candidate_png,
        kwargs,
    )
    if key is None:
        return comparator(reference_png, candidate_png, **kwargs)
    cached = _COMPARISON_CACHE.get(key)
    if cached is not None:
        return cached
    result = comparator(reference_png, candidate_png, **kwargs)
    _store_comparison(key, result)
    return result


def _comparison_cache_key(
    kind: str,
    comparator: Any,
    reference_png: bytes,
    candidate_png: bytes,
    kwargs: Mapping[str, Any],
) -> tuple[object, ...] | None:
    try:
        inputs_sha256 = canonical_sha256(kwargs)
    except (TypeError, ValueError):
        return None
    return (
        kind,
        comparator,
        hashlib.sha256(reference_png).digest(),
        hashlib.sha256(candidate_png).digest(),
        inputs_sha256,
    )


def _store_comparison(key: tuple[object, ...], result: Any) -> None:
    if len(_COMPARISON_CACHE) >= 256:
        _COMPARISON_CACHE.pop(next(iter(_COMPARISON_CACHE)))
    _COMPARISON_CACHE[key] = result


def _default_comparison_task(
    task: tuple[
        int,
        Path,
        Path,
        dict[str, Any],
        dict[str, Any] | None,
    ],
) -> tuple[int, Any, Any | None]:
    """Recompute one independent capture pair in a worker process."""

    index, reference_file, candidate_file, exact_kwargs, visual_kwargs = task
    reference_png = reference_file.read_bytes()
    candidate_png = candidate_file.read_bytes()
    reference = decode_png_srgb_rgba8(reference_png)
    candidate = decode_png_srgb_rgba8(candidate_png)
    reference_environment = validate_capture_environment(
        exact_kwargs["reference_environment"]
    )
    candidate_environment = validate_capture_environment(
        exact_kwargs["candidate_environment"]
    )
    comparable_reference = {
        key: value
        for key, value in reference_environment.items()
        if key not in {"source", "environment_sha256"}
    }
    comparable_candidate = {
        key: value
        for key, value in candidate_environment.items()
        if key not in {"source", "environment_sha256"}
    }
    if comparable_reference != comparable_candidate:
        raise ExactPixelContractError(
            "ENV_MISMATCH",
            "$",
            "reference and candidate capture environments differ",
        )
    viewport = comparable_reference["viewport"]
    expected_width = viewport["width"] * comparable_reference["device_scale_factor"]
    expected_height = viewport["height"] * comparable_reference["device_scale_factor"]
    if (reference.width, reference.height) != (expected_width, expected_height):
        raise ExactPixelContractError(
            "ENV_GEOMETRY",
            "$",
            "decoded image dimensions do not match capture geometry",
        )
    environment_sha256 = canonical_sha256(comparable_reference)
    exact = compare_decoded_rgba8(
        reference,
        candidate,
        scenario_id=exact_kwargs["scenario_id"],
        mask_manifest=exact_kwargs["mask_manifest"],
        environment_sha256=environment_sha256,
    )
    visual = (
        compare_decoded_rgba8_similarity(
            reference,
            candidate,
            state_id=visual_kwargs["state_id"],
            scenario_id=visual_kwargs["scenario_id"],
            mask_manifest=visual_kwargs["mask_manifest"],
            policy=visual_kwargs["policy"],
            environment_sha256=environment_sha256,
        )
        if visual_kwargs is not None
        else None
    )
    return index, exact, visual


def _parallel_default_comparisons(
    captures: Any,
    *,
    reference_map: Mapping[tuple[str, int, int], Mapping[str, Any]],
    reference_screenshot_root: Path,
    candidate_screenshot_root: Path,
    visual_similarity_policy: Mapping[str, Any],
) -> dict[int, tuple[Any, Any | None]]:
    """Recompute valid-looking capture pairs concurrently without weakening fallback.

    The optimization is used only with the module's original comparators. Tests
    and callers that replace either comparator keep the original sequential
    execution path. A worker failure merely omits that item so ``verify``
    recomputes it synchronously and preserves the canonical error behavior.
    """

    if (
        compare_pngs_exact is not _DEFAULT_EXACT_COMPARATOR
        or compare_pngs_similarity is not _DEFAULT_SIMILARITY_COMPARATOR
        or not isinstance(captures, list)
        or len(captures) < 2
        or not visual_similarity_policy
    ):
        return {}

    tasks: list[
        tuple[int, Path, Path, dict[str, Any], dict[str, Any] | None]
    ] = []
    cache_keys: dict[
        int, tuple[tuple[object, ...], tuple[object, ...] | None]
    ] = {}
    results: dict[int, tuple[Any, Any | None]] = {}
    for index, item in enumerate(captures):
        if not isinstance(item, dict):
            continue
        viewport = item.get("viewport")
        state_id = item.get("state_id")
        width = viewport.get("width") if isinstance(viewport, dict) else None
        height = viewport.get("height") if isinstance(viewport, dict) else None
        if not (
            isinstance(state_id, str)
            and isinstance(width, int)
            and isinstance(height, int)
        ):
            continue
        reference_item = reference_map.get((state_id, width, height))
        if not isinstance(reference_item, Mapping):
            continue
        reference_file = _safe_file(
            reference_screenshot_root,
            item.get("reference_screenshot_relative_path"),
            suffix=".png",
        )
        candidate_file = _safe_file(
            candidate_screenshot_root,
            item.get("candidate_screenshot_relative_path"),
            suffix=".png",
        )
        if reference_file is None or candidate_file is None:
            continue
        scenario_id = f"V0B-P1-{state_id}-{width}X{height}"
        exact_kwargs = {
            "scenario_id": scenario_id,
            "mask_manifest": item.get("mask_manifest"),
            "reference_environment": reference_item.get("environment"),
            "candidate_environment": item.get("candidate_environment"),
        }
        visual_kwargs = (
            {
                "state_id": state_id,
                "scenario_id": scenario_id,
                "mask_manifest": item.get("mask_manifest"),
                "policy": visual_similarity_policy,
                "reference_environment": reference_item.get("environment"),
                "candidate_environment": item.get("candidate_environment"),
            }
            if item.get("visual_similarity_report") is not None
            else None
        )
        try:
            reference_png = reference_file.read_bytes()
            candidate_png = candidate_file.read_bytes()
        except OSError:
            continue
        exact_key = _comparison_cache_key(
            "exact",
            compare_pngs_exact,
            reference_png,
            candidate_png,
            exact_kwargs,
        )
        visual_key = (
            _comparison_cache_key(
                "similarity",
                compare_pngs_similarity,
                reference_png,
                candidate_png,
                visual_kwargs,
            )
            if visual_kwargs is not None
            else None
        )
        if exact_key is None or (visual_kwargs is not None and visual_key is None):
            continue
        cached_exact = _COMPARISON_CACHE.get(exact_key)
        cached_visual = (
            _COMPARISON_CACHE.get(visual_key)
            if visual_key is not None
            else None
        )
        if cached_exact is not None and (
            visual_kwargs is None or cached_visual is not None
        ):
            results[index] = (cached_exact, cached_visual)
            continue
        cache_keys[index] = (exact_key, visual_key)
        tasks.append(
            (index, reference_file, candidate_file, exact_kwargs, visual_kwargs)
        )

    worker_count = min(8, len(tasks), os.cpu_count() or 1)
    if worker_count < 2:
        return results
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_default_comparison_task, task): task[0]
                for task in tasks
            }
            for future in as_completed(futures):
                try:
                    index, exact, visual = future.result()
                except Exception:
                    continue
                results[index] = (exact, visual)
                exact_key, visual_key = cache_keys[index]
                _store_comparison(exact_key, exact)
                if visual_key is not None and visual is not None:
                    _store_comparison(visual_key, visual)
    except Exception:
        return results
    return results


def _source_binding(relative_paths: Sequence[str]) -> dict[str, Any]:
    digest = hashlib.sha256()
    files: list[dict[str, str]] = []
    for relative in relative_paths:
        path = REPOSITORY_ROOT / Path(*PurePosixPath(relative).parts)
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
        files.append({"path": relative, "sha256": "sha256:" + hashlib.sha256(payload).hexdigest()})
    return {"build_sha256": "sha256:" + digest.hexdigest(), "files": files}


def current_source_binding() -> dict[str, Any]:
    return _source_binding(SOURCE_REPO_PATHS)


def current_runtime_source_binding() -> dict[str, Any]:
    return _source_binding(RUNTIME_SOURCE_REPO_PATHS)


def current_capture_pipeline_binding() -> dict[str, Any]:
    """Bind the only capture path eligible to close current V0B pixels."""

    collector = REPOSITORY_ROOT / Path(*PurePosixPath(PRODUCT_CAPTURE_REPO_PATH).parts)
    driver = REPOSITORY_ROOT / Path(
        *PurePosixPath(PRODUCT_CAPTURE_DRIVER_REPO_PATH).parts
    )
    transitive_files = [
        {
            "path": relative,
            "sha256": _sha256_file(
                REPOSITORY_ROOT / Path(*PurePosixPath(relative).parts)
            ),
        }
        for relative in PRODUCT_CAPTURE_TRANSITIVE_REPO_PATHS
    ]
    policy_path = REPOSITORY_ROOT / Path(
        *PurePosixPath(VISUAL_SIMILARITY_POLICY_REPO_PATH).parts
    )
    policy = validate_visual_similarity_policy(
        _load(policy_path),
        expected_keys=EXPECTED_CAPTURE_KEYS,
    )
    _validate_critical_region_contract(policy)
    return {
        "contract": PRODUCT_CAPTURE_PIPELINE_CONTRACT,
        "mode": "product_render_only",
        "collector_path": PRODUCT_CAPTURE_REPO_PATH,
        "collector_sha256": _sha256_file(collector),
        "browser_driver_path": PRODUCT_CAPTURE_DRIVER_REPO_PATH,
        "browser_driver_sha256": _sha256_file(driver),
        "transitive_files": transitive_files,
        "visual_similarity_policy": {
            "path": VISUAL_SIMILARITY_POLICY_REPO_PATH,
            "file_sha256": _sha256_file(policy_path),
            "policy_id": policy["policy_id"],
            "policy_sha256": policy["policy_sha256"],
        },
        "reference_pixels_available_to_page": False,
        "capture_only_visual_mutation": False,
        "post_capture_reference_matte": False,
    }


def _validate_critical_region_contract(policy: Mapping[str, Any]) -> None:
    """Require one bounded state-specific critical region in every policy row."""

    broad_ids = {"APP-CHROME", "PRIMARY-CONTENT", "SIDEBAR"}
    for row in policy["states"]:
        state_id = row["state_id"]
        expected_critical_id = EXPECTED_CRITICAL_REGION_IDS.get(state_id)
        region_ids = {region["id"] for region in row["required_regions"]}
        if expected_critical_id is None or region_ids != broad_ids | {expected_critical_id}:
            raise VisualSimilarityContractError(
                "POLICY_CRITICAL_REGIONS",
                f"$.states.{state_id}.required_regions",
                "three broad regions and the exact state-specific critical region are required",
            )
        critical = next(
            region
            for region in row["required_regions"]
            if region["id"] == expected_critical_id
        )
        rect = critical["rect"]
        area = rect["width"] * rect["height"]
        viewport_area = row["viewport"]["width"] * row["viewport"]["height"]
        if area < 1_000 or area * 100 > viewport_area * 36:
            raise VisualSimilarityContractError(
                "POLICY_CRITICAL_REGION_BOUNDS",
                f"$.states.{state_id}.{expected_critical_id}.rect",
                "critical region must cover 1,000 pixels through 36 percent of the viewport",
            )


def _valid_time(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _safe_file(root: Path, relative: Any, *, suffix: str) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.suffix.lower() != suffix:
        return None
    candidate = (root / Path(*pure.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _runtime_root_matches(actual: Path, declared: Any, legacy: str) -> bool:
    if declared == legacy:
        return True
    if not isinstance(declared, str) or not declared or "\\" in declared:
        return False
    pure = PurePosixPath(declared)
    if pure.is_absolute() or ".." in pure.parts:
        return False
    expected = REPOSITORY_ROOT / Path(*pure.parts)
    return actual.resolve(strict=False) == expected.resolve(strict=False)


def _expected_scenario_ids(path: Path) -> list[str]:
    document = _load(path)
    rows = document.get("required_scenarios") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError("required_scenarios must be an array")
    return [str(item["scenario_id"]) for item in rows]


def _reference_capture_map(receipt: Mapping[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    result: dict[tuple[str, int, int], dict[str, Any]] = {}
    captures = receipt.get("captures")
    if not isinstance(captures, list):
        return result
    for item in captures:
        if not isinstance(item, dict):
            continue
        viewport = item.get("viewport")
        if not isinstance(viewport, dict):
            continue
        key = (item.get("state_id"), viewport.get("width"), viewport.get("height"))
        if all(isinstance(value, (str, int)) for value in key):
            result[key] = item
    return result


def _valid_named_nodes(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, dict)
            and set(item) == {"alias", "name", "role", "state"}
            and all(isinstance(item.get(field), str) and item.get(field) for field in item)
            and item.get("state", "").startswith("visible")
            for item in value
        )
    )


def verify(
    path: Path = RECEIPT_PATH,
    *,
    reference_receipt_path: Path = REFERENCE_RECEIPT_PATH,
    reference_screenshot_root: Path = REFERENCE_SCREENSHOT_ROOT,
    reference_mask_root: Path | None = REFERENCE_MASK_ROOT,
    candidate_screenshot_root: Path = CANDIDATE_SCREENSHOT_ROOT,
    diff_root: Path = DIFF_ROOT,
    scenarios_path: Path = SCENARIOS_PATH,
    visual_similarity_policy_path: Path = VISUAL_SIMILARITY_POLICY_PATH,
    allow_receipt_bound_runtime_for_visual_audit: bool = False,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        receipt = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_final_candidate.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [{"code": "RECEIPT_READ_FAILED", "subject": str(path), "message": str(exc)}],
        }
    if not isinstance(receipt, dict):
        return {
            "contract": "code_assist.notion.internal_collaborative_final_candidate.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [{"code": "RECEIPT_SHAPE", "subject": "$", "message": "receipt must be an object"}],
        }

    check(
        receipt.get("schema_version") == 1
        and receipt.get("contract") == CONTRACT
        and receipt.get("packet_id") == "V0B-P1"
        and isinstance(receipt.get("receipt_id"), str)
        and str(receipt.get("receipt_id", "")).startswith("V0B-P1-L6-FINAL-CANDIDATE-")
        and _valid_time(receipt.get("recorded_at")),
        "RECEIPT_IDENTITY",
        "$",
        "V0B-P1 receipt identity, id, or time drift",
    )

    try:
        reference_receipt = _load(reference_receipt_path)
        reference_report = verify_reference_receipt(
            reference_receipt_path,
            screenshot_root=reference_screenshot_root,
            mask_root=reference_mask_root,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        reference_receipt = {}
        reference_report = {"status": "fail", "error": str(exc)}
    binding = receipt.get("reference_receipt")
    reference_transport = (
        reference_receipt.get("capture_transport")
        if isinstance(reference_receipt, dict)
        else None
    )
    reference_transport_eligible = reference_transport is None or (
        isinstance(reference_transport, dict)
        and reference_transport.get("source_encoding_lossless") is True
        and reference_transport.get("zero_RGBA_equivalence_claimed") is True
    )
    check(
        reference_report.get("status") == "pass"
        and isinstance(binding, dict)
        and _runtime_root_matches(
            reference_receipt_path,
            binding.get("path"),
            REFERENCE_REPO_PATH,
        )
        and binding.get("sha256") == _sha256_file(reference_receipt_path)
        and binding.get("capture_count") == 36
        and binding.get("capture_state_count") == 18,
        "REFERENCE_BINDING",
        "$.reference_receipt",
        "current verifier-passed 18-state V0R reference receipt required",
    )
    check(
        reference_transport_eligible,
        "REFERENCE_CAPTURE_TRANSPORT",
        "$.reference_receipt",
        "visual comparison requires a source-lossless reference transport",
    )

    try:
        visual_similarity_policy = validate_visual_similarity_policy(
            _load(visual_similarity_policy_path),
            expected_keys=EXPECTED_CAPTURE_KEYS,
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        visual_similarity_policy = {}
        check(
            False,
            "VISUAL_SIMILARITY_POLICY",
            str(visual_similarity_policy_path),
            str(exc),
        )

    source = receipt.get("release_candidate")
    expected_source = current_source_binding()
    check(
        isinstance(source, dict)
        and source.get("source_build_sha256") == expected_source["build_sha256"]
        and source.get("source_files") == expected_source["files"],
        "RELEASE_CANDIDATE_BINDING",
        "$.release_candidate",
        "current exact UI source build and per-file hashes required",
    )
    candidate_build = expected_source["build_sha256"]
    runtime_source = receipt.get("runtime_candidate")
    expected_runtime_source = current_runtime_source_binding()
    runtime_binding_matches = (
        isinstance(runtime_source, dict)
        and runtime_source.get("source_build_sha256")
        == expected_runtime_source["build_sha256"]
        and runtime_source.get("source_files") == expected_runtime_source["files"]
    )
    runtime_compatibility_required = (
        allow_receipt_bound_runtime_for_visual_audit
        and not runtime_binding_matches
    )
    check(
        runtime_binding_matches or runtime_compatibility_required,
        "RUNTIME_CANDIDATE_BINDING",
        "$.runtime_candidate",
        "current IAM/editing/live/gateway schema-protocol source binding required",
    )

    try:
        expected_scenarios = _expected_scenario_ids(scenarios_path)
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        expected_scenarios = []
        check(False, "SCENARIO_CATALOG", str(scenarios_path), str(exc))
    core = receipt.get("core_25")
    rows = core.get("scenario_results") if isinstance(core, dict) else None
    actual_scenarios = [item.get("scenario_id") for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    check(
        isinstance(core, dict)
        and core.get("status") == "pass"
        and core.get("source_build_sha256") == candidate_build
        and core.get("collected_test_count") == 21
        and core.get("passed_test_count") == 21
        and core.get("failed_test_count") == 0
        and _valid_time(core.get("started_at"))
        and _valid_time(core.get("ended_at"))
        and actual_scenarios == expected_scenarios
        and len(set(actual_scenarios)) == 25
        and all(
            isinstance(item, dict)
            and set(item) == {"scenario_id", "status"}
            and item.get("status") == "pass"
            for item in rows or []
        ),
        "CORE_25_GATE",
        "$.core_25",
        "fresh current-build 21-test/25-scenario all-pass evidence required",
    )

    performance = receipt.get("performance")
    check(
        isinstance(performance, dict)
        and performance.get("scenario_id") == "UI-PERF-001"
        and performance.get("status") == "pass"
        and performance.get("source_build_sha256") == candidate_build
        and isinstance(performance.get("observed_p95_ms"), (int, float))
        and isinstance(performance.get("approved_budget_ms"), (int, float))
        and performance.get("observed_p95_ms") <= performance.get("approved_budget_ms")
        and performance.get("quiet_host_verified") is True
        and _valid_time(performance.get("started_at"))
        and _valid_time(performance.get("ended_at")),
        "PERFORMANCE_GATE",
        "$.performance",
        "fresh quiet-host UI-PERF-001 pass within the approved budget required",
    )

    run = receipt.get("run")
    expected_capture_pipeline = current_capture_pipeline_binding()
    check(
        isinstance(run, dict)
        and isinstance(run.get("run_id"), str)
        and str(run.get("run_id", "")).startswith("v0b-p1-")
        and run.get("browser") == "chrome_two_independent_ephemeral_contexts"
        and isinstance(run.get("chrome_major_version"), str)
        and str(run.get("chrome_major_version", "")).isdigit()
        and _runtime_root_matches(
            candidate_screenshot_root,
            run.get("candidate_screenshot_root"),
            CANDIDATE_SCREENSHOT_REPO_ROOT,
        )
        and _runtime_root_matches(diff_root, run.get("diff_root"), DIFF_REPO_ROOT)
        and run.get("capture_pipeline") == expected_capture_pipeline
        and _valid_time(run.get("started_at"))
        and _valid_time(run.get("ended_at")),
        "RUN_BOUNDARY",
        "$.run",
        "exact two-context Chrome product-render-only candidate run boundary required",
    )

    reference_map = _reference_capture_map(reference_receipt if isinstance(reference_receipt, dict) else {})
    captures = receipt.get("captures")
    actual_keys: list[tuple[str, int, int]] = []
    passed_pixel_count = 0
    passed_visual_similarity_count = 0
    passed_accessibility_count = 0
    seen_candidate_paths: set[str] = set()
    seen_diff_paths: set[str] = set()
    parallel_comparisons = _parallel_default_comparisons(
        captures,
        reference_map=reference_map,
        reference_screenshot_root=reference_screenshot_root,
        candidate_screenshot_root=candidate_screenshot_root,
        visual_similarity_policy=visual_similarity_policy,
    )
    if isinstance(captures, list):
        for index, item in enumerate(captures):
            location = f"$.captures[{index}]"
            if not isinstance(item, dict):
                check(False, "CAPTURE_SHAPE", location, "capture must be an object")
                continue
            viewport = item.get("viewport")
            width = viewport.get("width") if isinstance(viewport, dict) else None
            height = viewport.get("height") if isinstance(viewport, dict) else None
            state_id = item.get("state_id")
            key = (state_id, width, height)
            if isinstance(state_id, str) and isinstance(width, int) and isinstance(height, int):
                actual_keys.append((state_id, width, height))
            expected = EXPECTED_STATE_MAP.get(str(state_id), {})
            check(
                bool(expected)
                and all(item.get(field) == value for field, value in expected.items()),
                "CAPTURE_STATE_BINDING",
                location,
                "candidate capture state, workspace, actor, surface, or expected state drift",
            )
            check(
                item.get("capture_matte") is None
                and item.get("capture_provenance")
                == {
                    "mode": "product_render_only",
                    "browser_screenshot_before_reference_pixel_read": True,
                    "reference_pixels_injected": False,
                    "capture_only_visual_mutation": False,
                    "post_capture_reference_matte": False,
                },
                "CAPTURE_PROVENANCE",
                location,
                "candidate pixels must come from the unmodified product render before reference pixels are read",
            )
            runtime_binding = item.get("runtime_binding")
            allowed_subjects = EXPECTED_ACTOR_SUBJECTS.get(
                str(item.get("actor_alias")), frozenset()
            )
            check(
                isinstance(runtime_binding, dict)
                and set(runtime_binding)
                in (
                    {"workspace_name", "active_actor_subject_id", "active_page_title"},
                    {
                        "workspace_name",
                        "active_actor_subject_id",
                        "active_page_title",
                        "current_surface_subject_id",
                        "subject_binding_source",
                    },
                )
                and (
                    item.get("surface") == "access_boundary"
                    or runtime_binding.get("workspace_name") == item.get("workspace_alias")
                )
                and runtime_binding.get("active_actor_subject_id") in allowed_subjects
                and isinstance(runtime_binding.get("active_page_title"), str),
                "RUNTIME_BINDING",
                f"{location}.runtime_binding",
                "runtime workspace and active actor must match the named synthetic state",
            )
            if isinstance(runtime_binding, dict) and "subject_binding_source" in runtime_binding:
                current_surface_subject = runtime_binding.get("current_surface_subject_id")
                binding_source = runtime_binding.get("subject_binding_source")
                check(
                    isinstance(current_surface_subject, str)
                    and (
                        (
                            binding_source == "current_surface_state"
                            and current_surface_subject
                            == runtime_binding.get("active_actor_subject_id")
                        )
                        or (
                            binding_source
                            == "same_ephemeral_browser_context_pre_revoke"
                            and item.get("surface") == "access_boundary"
                            and current_surface_subject == ""
                        )
                    ),
                    "RUNTIME_SUBJECT_PROVENANCE",
                    f"{location}.runtime_binding",
                    "active actor must be current-surface bound or inherited only from the same pre-revoke browser context",
                )

            reference_item = reference_map.get(key)
            reference_relative = item.get("reference_screenshot_relative_path")
            reference_file = _safe_file(reference_screenshot_root, reference_relative, suffix=".png")
            check(
                isinstance(reference_item, dict)
                and reference_relative == reference_item.get("screenshot_relative_path")
                and item.get("reference_screenshot_sha256") == reference_item.get("screenshot_sha256")
                and reference_file is not None
                and _sha256_file(reference_file) == item.get("reference_screenshot_sha256"),
                "REFERENCE_CAPTURE_BINDING",
                location,
                "exact V0R state/viewport screenshot binding required",
            )

            candidate_relative = item.get("candidate_screenshot_relative_path")
            candidate_file = _safe_file(candidate_screenshot_root, candidate_relative, suffix=".png")
            check(
                candidate_file is not None
                and isinstance(candidate_relative, str)
                and candidate_relative not in seen_candidate_paths
                and _SHA256_RE.fullmatch(str(item.get("candidate_screenshot_sha256", ""))) is not None
                and _sha256_file(candidate_file) == item.get("candidate_screenshot_sha256"),
                "CANDIDATE_SCREENSHOT",
                location,
                "unique hash-bound in-root candidate PNG required",
            )
            if isinstance(candidate_relative, str):
                seen_candidate_paths.add(candidate_relative)

            diff_relative = item.get("diff_relative_path")
            diff_file = _safe_file(diff_root, diff_relative, suffix=".png")
            check(
                diff_file is not None
                and isinstance(diff_relative, str)
                and diff_relative not in seen_diff_paths
                and _SHA256_RE.fullmatch(str(item.get("diff_sha256", ""))) is not None
                and _sha256_file(diff_file) == item.get("diff_sha256"),
                "DIFF_ARTIFACT",
                location,
                "unique hash-bound in-root exact diff PNG required",
            )
            if isinstance(diff_relative, str):
                seen_diff_paths.add(diff_relative)

            reference_environment = reference_item.get("environment") if isinstance(reference_item, dict) else None
            candidate_environment = item.get("candidate_environment")
            try:
                normalized_candidate = validate_capture_environment(candidate_environment)
            except (ExactPixelContractError, TypeError) as exc:
                normalized_candidate = {}
                check(False, "CANDIDATE_ENVIRONMENT", f"{location}.candidate_environment", str(exc))
            check(
                normalized_candidate.get("viewport") == viewport
                and normalized_candidate.get("source", {}).get("kind") == "candidate"
                and normalized_candidate.get("source", {}).get("build_sha256") == candidate_build
                and normalized_candidate.get("browser", {}).get("major_version") == run.get("chrome_major_version")
                and normalized_candidate.get("environment_sha256") == item.get("candidate_environment_sha256"),
                "CANDIDATE_ENVIRONMENT_BINDING",
                f"{location}.candidate_environment",
                "frozen current-build candidate environment required",
            )

            reference_png: bytes | None = None
            candidate_png: bytes | None = None
            precomputed = parallel_comparisons.get(index)
            report = item.get("exact_pixel_report")
            try:
                validated_report = validate_exact_pixel_receipt(report)
                if reference_file is None or candidate_file is None:
                    raise ExactPixelContractError("CAPTURE_FILE", location, "capture file unavailable")
                if precomputed is not None:
                    recomputed = precomputed[0]
                else:
                    reference_png = reference_file.read_bytes()
                    candidate_png = candidate_file.read_bytes()
                    recomputed = _cached_comparison(
                        "exact",
                        compare_pngs_exact,
                        reference_png,
                        candidate_png,
                        scenario_id=f"V0B-P1-{state_id}-{width}X{height}",
                        mask_manifest=item.get("mask_manifest"),
                        reference_environment=reference_environment,
                        candidate_environment=candidate_environment,
                    )
            except (OSError, ExactPixelContractError, TypeError) as exc:
                validated_report = {}
                recomputed = None
                check(False, "EXACT_PIXEL_RECOMPUTE", location, str(exc))
            exact_integrity = (
                recomputed is not None
                and validated_report == recomputed.report
                and report == recomputed.report
                and diff_file is not None
                and diff_file.read_bytes() == recomputed.diff_png
                and item.get("diff_sha256") == recomputed.report.get("diff_image_sha256")
            )
            check(
                exact_integrity,
                "EXACT_PIXEL_DIAGNOSTIC_INTEGRITY",
                location,
                "the exact diagnostic report and diff must recompute byte-for-byte",
            )
            exact_pass = (
                exact_integrity
                and report.get("status") == "pass"
                and report.get("differing_pixel_count") == 0
            )
            passed_pixel_count += int(exact_pass)

            visual_report = item.get("visual_similarity_report")
            visual_pass = exact_pass and visual_report is None
            if visual_report is not None:
                try:
                    validated_visual_report = validate_visual_similarity_receipt(
                        visual_report
                    )
                    if reference_file is None or candidate_file is None:
                        raise VisualSimilarityContractError(
                            "CAPTURE_FILE", location, "capture file unavailable"
                        )
                    if precomputed is not None and precomputed[1] is not None:
                        recomputed_visual = precomputed[1]
                    else:
                        if reference_png is None or candidate_png is None:
                            reference_png = reference_file.read_bytes()
                            candidate_png = candidate_file.read_bytes()
                        recomputed_visual = _cached_comparison(
                            "similarity",
                            compare_pngs_similarity,
                            reference_png,
                            candidate_png,
                            state_id=str(state_id),
                            scenario_id=f"V0B-P1-{state_id}-{width}X{height}",
                            mask_manifest=item.get("mask_manifest"),
                            policy=visual_similarity_policy,
                            reference_environment=reference_environment,
                            candidate_environment=candidate_environment,
                        )
                except (
                    OSError,
                    ExactPixelContractError,
                    VisualSimilarityContractError,
                    TypeError,
                ) as exc:
                    validated_visual_report = {}
                    recomputed_visual = None
                    check(False, "VISUAL_SIMILARITY_RECOMPUTE", location, str(exc))
                visual_pass = (
                    recomputed_visual is not None
                    and validated_visual_report == recomputed_visual.report
                    and visual_report == recomputed_visual.report
                    and visual_report.get("status") == "pass"
                )
            check(
                visual_pass,
                "VISUAL_SIMILARITY_GATE",
                location,
                "reviewed whole-frame and required-region similarity thresholds must all pass",
            )
            passed_visual_similarity_count += int(visual_pass)

            accessibility = item.get("accessibility")
            focus = accessibility.get("focus") if isinstance(accessibility, dict) else None
            keyboard_path = accessibility.get("keyboard_path") if isinstance(accessibility, dict) else None
            keyboard_execution = accessibility.get("keyboard_execution") if isinstance(accessibility, dict) else None
            live = accessibility.get("live_regions") if isinstance(accessibility, dict) else None
            geometry = accessibility.get("scroll_geometry") if isinstance(accessibility, dict) else None
            accessibility_pass = (
                isinstance(accessibility, dict)
                and accessibility.get("status") == "pass"
                and isinstance(focus, dict)
                and set(focus) == {"active_alias", "name", "role", "state", "sequence"}
                and all(isinstance(focus.get(field), str) and focus.get(field) for field in ("active_alias", "name", "role", "state"))
                and isinstance(focus.get("sequence"), list)
                and bool(focus.get("sequence"))
                and all(isinstance(value, str) and value for value in focus.get("sequence", []))
                and isinstance(keyboard_path, list)
                and bool(keyboard_path)
                and all(isinstance(value, str) and value for value in keyboard_path)
                and isinstance(keyboard_execution, dict)
                and keyboard_execution.get("kind") == "focus_navigation_probe"
                and keyboard_execution.get("status") == "pass"
                and keyboard_execution.get("sequence") == ["Tab", "Shift+Tab"]
                and isinstance(keyboard_execution.get("steps"), list)
                and len(keyboard_execution.get("steps")) == 2
                and all(
                    isinstance(step, dict)
                    and set(step) == {"key", "name", "role", "state"}
                    and step.get("key") in {"Tab", "Shift+Tab"}
                    and all(isinstance(step.get(field), str) and step.get(field) for field in step)
                    and step.get("state") == "visible,focused"
                    for step in keyboard_execution.get("steps", [])
                )
                and _valid_named_nodes(accessibility.get("name_role_state"))
                and isinstance(live, dict)
                and set(live) == {"expected", "nodes"}
                and isinstance(live.get("expected"), bool)
                and isinstance(live.get("nodes"), list)
                and (not live.get("expected") or bool(live.get("nodes")))
                and all(
                    isinstance(node, dict)
                    and set(node) == {"alias", "name", "role", "state", "live"}
                    and all(isinstance(node.get(field), str) and node.get(field) for field in node)
                    for node in live.get("nodes", [])
                )
                and isinstance(geometry, dict)
                and set(geometry) == {"x", "y", "width", "height", "scroll_x", "scroll_y"}
                and all(isinstance(geometry.get(field), int) for field in geometry)
            )
            check(
                accessibility_pass,
                "ACCESSIBILITY_GATE",
                f"{location}.accessibility",
                "complete focus, keyboard, name/role/state, live-region, and scroll evidence required",
            )
            passed_accessibility_count += int(accessibility_pass)

            outcome = item.get("interaction_outcome")
            check(
                isinstance(outcome, dict)
                and outcome.get("status") == "pass"
                and outcome.get("expected_state") == expected.get("expected_state")
                and isinstance(outcome.get("observation"), str)
                and bool(outcome.get("observation"))
                and _valid_time(item.get("captured_at"))
                and item.get("raw_identifier_persisted") is False
                and item.get("personal_content_visible") is False,
                "INTERACTION_AND_REDACTION",
                location,
                "passed state interaction, timestamp, and redaction evidence required",
            )

    check(
        isinstance(captures, list)
        and len(captures) == 36
        and len(actual_keys) == len(set(actual_keys))
        and set(actual_keys) == EXPECTED_CAPTURE_KEYS,
        "CAPTURE_DENOMINATOR",
        "$.captures",
        "exact 18-state by 2-viewport candidate capture set required",
    )

    components = receipt.get("component_journeys")
    check(
        isinstance(components, dict)
        and set(components) == {"I0-P2", "C0-P2", "E1-P2", "F0-P5"}
        and all(
            isinstance(item, dict)
            and item.get("status") == "pass"
            and item.get("synthetic_member_absent_after_cleanup") is True
            and (
                item.get("test_root_moved_to_trash") is True
                or item.get("test_page_moved_to_trash") is True
            )
            and item.get("browser", {}).get("browser_closed") is True
            for item in components.values()
        ),
        "COMPONENT_JOURNEY_GATE",
        "$.component_journeys",
        "all four browser journeys must prove page/root, member, and browser cleanup",
    )

    cleanup = receipt.get("cleanup")
    check(
        isinstance(cleanup, dict)
        and cleanup.get("status") == "complete"
        and cleanup.get("synthetic_only") is True
        and cleanup.get("active_test_grant_count") == 0
        and cleanup.get("active_test_member_count") == 0
        and cleanup.get("active_fixture_root_count") == 0
        and cleanup.get("component_cleanup_verified") is True
        and cleanup.get("browser_closed") is True
        and cleanup.get("permanent_delete_used") is False
        and _valid_time(cleanup.get("completed_at"))
        and cleanup.get("cleanup_receipt_sha256")
        == canonical_sha256({key: value for key, value in cleanup.items() if key != "cleanup_receipt_sha256"}),
        "CLEANUP_GATE",
        "$.cleanup",
        "complete synthetic fixture, grant, and browser cleanup required",
    )

    claims = receipt.get("claims")
    exact_all = passed_pixel_count == 36
    visual_similarity_all = passed_visual_similarity_count == 36
    check(
        isinstance(claims, dict)
        and claims.get("zero_unmasked_RGBA_difference") is exact_all
        and (
            claims.get("strict_visual_similarity_passed") is True
            or (exact_all and "strict_visual_similarity_passed" not in claims)
        )
        and visual_similarity_all
        and all(
            claims.get(field) is True
            for field in (
                "V0R_P1_gate_passed",
                "final_release_candidate_core_25_rerun",
                "all_adopted_multiuser_states_captured",
                "accessibility_evidence_complete",
                "PIX_001_closed",
                "A11Y_001_closed",
                "UI_CORE_25_closed",
                "V0B_P1_gate_passed",
            )
        )
        and all(
            claims.get(field) is False
            for field in (
                "production_changed",
                "second_brain_changed",
                "product_complete",
                "active_shared_goal_complete",
            )
        ),
        "CLAIMS_GATE",
        "$.claims",
        "only complete verified V0B evidence may close the three V0 rows",
    )

    normalized = sorted(
        {
            (item["code"], item["subject"], item["message"]): item
            for item in errors
        }.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_final_candidate.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "receipt_sha256": _sha256_file(path),
        "core_scenario_count": len(actual_scenarios),
        "capture_count": len(captures) if isinstance(captures, list) else 0,
        "capture_state_count": len({key[0] for key in actual_keys}),
        "viewport_count": len({(key[1], key[2]) for key in actual_keys}),
        "exact_pixel_pass_count": passed_pixel_count,
        "visual_similarity_pass_count": passed_visual_similarity_count,
        "accessibility_pass_count": passed_accessibility_count,
        "runtime_compatibility_required": runtime_compatibility_required,
        "gate_passed": not normalized and not runtime_compatibility_required,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    parser.add_argument("--reference-receipt", type=Path, default=REFERENCE_RECEIPT_PATH)
    parser.add_argument("--reference-screenshot-root", type=Path, default=REFERENCE_SCREENSHOT_ROOT)
    parser.add_argument("--reference-mask-root", type=Path, default=REFERENCE_MASK_ROOT)
    parser.add_argument("--candidate-screenshot-root", type=Path, default=CANDIDATE_SCREENSHOT_ROOT)
    parser.add_argument("--diff-root", type=Path, default=DIFF_ROOT)
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS_PATH)
    parser.add_argument(
        "--visual-similarity-policy",
        type=Path,
        default=VISUAL_SIMILARITY_POLICY_PATH,
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(
        args.receipt,
        reference_receipt_path=args.reference_receipt,
        reference_screenshot_root=args.reference_screenshot_root,
        reference_mask_root=args.reference_mask_root,
        candidate_screenshot_root=args.candidate_screenshot_root,
        diff_root=args.diff_root,
        scenarios_path=args.scenarios,
        visual_similarity_policy_path=args.visual_similarity_policy,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CANDIDATE_SCREENSHOT_ROOT",
    "CONTRACT",
    "DIFF_ROOT",
    "EXPECTED_CAPTURE_KEYS",
    "RECEIPT_PATH",
    "VISUAL_SIMILARITY_POLICY_PATH",
    "VISUAL_SIMILARITY_POLICY_REPO_PATH",
    "current_source_binding",
    "verify",
]
