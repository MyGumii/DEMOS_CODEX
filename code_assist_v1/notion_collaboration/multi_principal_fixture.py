"""Deterministic synthetic workspace/page/grant fixture for four principals.

This is harness data, not a product authority.  It accepts only a verified
synthetic pair root and creates one new marker-bound sibling root.  Credentials,
sessions, secrets, user content, and runtime state are never copied.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .synthetic_pair_root import PRINCIPALS, verify_synthetic_pair_root


MARKER_NAME = ".code-assist-multi-principal-fixture.json"
SEED_NAME = "fixture/workspace_seed.json"
PROVENANCE_NAME = "fixture/provenance.json"
ALLOWED_FILES = frozenset({MARKER_NAME, SEED_NAME, PROVENANCE_NAME})
WORKSPACE_ID = "fixture-workspace-primary"
PAGE_IDS = (
    "fixture-page-team-home",
    "fixture-page-shared-edit",
    "fixture-page-guest-shared",
    "fixture-page-owner-private",
)
ACTION_ORDER = ("discover", "read", "comment", "edit", "share")
FORBIDDEN_SEED_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "cookie",
    "session",
    "authorization",
    "content_body",
    "private_content",
)
SENSITIVE_EVIDENCE_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "cookie",
    "session",
    "authorization",
    "absolute_path",
    "filesystem_path",
    "runtime_path",
)
REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class MultiPrincipalFixtureError(RuntimeError):
    pass


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite number: {value}")),
    )


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(_canonical_bytes(value))
        output.flush()
        os.fsync(output.fileno())


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _root_locator_sha256(path: Path) -> str:
    return _sha256_bytes(os.path.normcase(str(path.resolve())).encode("utf-8"))


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise MultiPrincipalFixtureError("FIXTURE_PATH_UNREADABLE") from exc
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_FLAG)


def _validate_new_root(
    root: Path,
    allowed_parent: Path,
    synthetic_pair_root: Path,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    if not all(path.is_absolute() for path in (root, allowed_parent, synthetic_pair_root)):
        raise MultiPrincipalFixtureError("ABSOLUTE_PATH_REQUIRED")
    parent = allowed_parent.resolve(strict=True)
    pair_root = synthetic_pair_root.resolve(strict=True)
    candidate = root.resolve(strict=False)
    if not parent.is_dir() or _is_reparse(parent):
        raise MultiPrincipalFixtureError("PARENT_REPARSE_OR_INVALID")
    if pair_root.parent != parent or candidate.parent != parent:
        raise MultiPrincipalFixtureError("ROOT_PARENT_MISMATCH")
    if candidate.exists():
        raise MultiPrincipalFixtureError("FIXTURE_ROOT_ALREADY_EXISTS")
    if candidate in {parent, pair_root} or candidate.name in {"", ".", ".."}:
        raise MultiPrincipalFixtureError("FIXTURE_ROOT_INVALID")
    pair_receipt = verify_synthetic_pair_root(pair_root)
    return candidate, parent, pair_root, pair_receipt


def _principal_rows() -> list[dict[str, str]]:
    return [
        {"principal_id": principal_id, "fixture_role": fixture_role, "auth_role": auth_role}
        for principal_id, fixture_role, auth_role in PRINCIPALS
    ]


def _seed_document() -> dict[str, Any]:
    principals = _principal_rows()
    pages = [
        {
            "page_id": PAGE_IDS[0],
            "parent_page_id": None,
            "title": "Synthetic Team Home",
            "fixture_scope": "workspace_default",
            "body_present": False,
        },
        {
            "page_id": PAGE_IDS[1],
            "parent_page_id": PAGE_IDS[0],
            "title": "Synthetic Shared Edit",
            "fixture_scope": "workspace_default",
            "body_present": False,
        },
        {
            "page_id": PAGE_IDS[2],
            "parent_page_id": PAGE_IDS[0],
            "title": "Synthetic Guest Shared",
            "fixture_scope": "direct_page_guest",
            "body_present": False,
        },
        {
            "page_id": PAGE_IDS[3],
            "parent_page_id": PAGE_IDS[0],
            "title": "Synthetic Owner Private",
            "fixture_scope": "owner_private",
            "body_present": False,
        },
    ]
    grants = [
        {
            "grant_id": "fixture-grant-workspace-owner",
            "principal_id": "fixture-owner",
            "resource_type": "workspace",
            "resource_id": WORKSPACE_ID,
            "role": "owner",
            "direct": True,
        },
        {
            "grant_id": "fixture-grant-workspace-member",
            "principal_id": "fixture-member",
            "resource_type": "workspace",
            "resource_id": WORKSPACE_ID,
            "role": "member",
            "direct": True,
        },
        {
            "grant_id": "fixture-grant-page-guest",
            "principal_id": "fixture-page-guest",
            "resource_type": "page",
            "resource_id": PAGE_IDS[2],
            "role": "commenter",
            "direct": True,
        },
    ]
    expected_access = {
        "fixture-owner": {
            PAGE_IDS[0]: list(ACTION_ORDER),
            PAGE_IDS[1]: list(ACTION_ORDER),
            PAGE_IDS[2]: list(ACTION_ORDER),
            PAGE_IDS[3]: list(ACTION_ORDER),
        },
        "fixture-member": {
            PAGE_IDS[0]: ["discover", "read", "comment", "edit"],
            PAGE_IDS[1]: ["discover", "read", "comment", "edit"],
            PAGE_IDS[2]: ["discover", "read", "comment", "edit"],
            PAGE_IDS[3]: [],
        },
        "fixture-page-guest": {
            PAGE_IDS[0]: [],
            PAGE_IDS[1]: [],
            PAGE_IDS[2]: ["read", "comment"],
            PAGE_IDS[3]: [],
        },
        "fixture-outsider": {page_id: [] for page_id in PAGE_IDS},
    }
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.multi_principal_workspace_seed.v1",
        "synthetic_only": True,
        "product_authority": False,
        "workspace": {
            "workspace_id": WORKSPACE_ID,
            "name": "Synthetic Collaboration Workspace",
            "member_directory_visible_to_page_guests": False,
            "public_access_enabled": False,
        },
        "principals": principals,
        "pages": pages,
        "grants": grants,
        "expected_access": expected_access,
        "access_action_order": list(ACTION_ORDER),
        "credential_or_content_copy_count": 0,
    }


def _walk_exact_files(root: Path) -> set[str]:
    files: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        if _is_reparse(directory):
            raise MultiPrincipalFixtureError("REPARSE_ENTRY_FORBIDDEN")
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink() or _is_reparse(path):
                    raise MultiPrincipalFixtureError("REPARSE_ENTRY_FORBIDDEN")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.add(path.relative_to(root).as_posix())
                else:
                    raise MultiPrincipalFixtureError("NONREGULAR_ENTRY_FORBIDDEN")
    return files


def _assert_seed_has_no_sensitive_keys(value: Any, subject: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if any(fragment in lowered for fragment in FORBIDDEN_SEED_KEY_FRAGMENTS):
                raise MultiPrincipalFixtureError("SENSITIVE_SEED_FIELD_FORBIDDEN")
            _assert_seed_has_no_sensitive_keys(child, subject + "." + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_seed_has_no_sensitive_keys(child, f"{subject}[{index}]")


def _principal_alias_map_from_receipt(receipt: Mapping[str, Any]) -> dict[str, str]:
    root_id = receipt["root_id"]
    return {
        principal_id: "principal:" + fixture_role + ":" + hashlib.sha256(
            f"{root_id}:{principal_id}".encode("utf-8")
        ).hexdigest()[:20]
        for principal_id, fixture_role, _ in PRINCIPALS
    }


def principal_alias_map(synthetic_pair_root: Path) -> dict[str, str]:
    return _principal_alias_map_from_receipt(verify_synthetic_pair_root(Path(synthetic_pair_root)))


def redact_principal_evidence(
    value: Any,
    *,
    aliases: Mapping[str, str],
    forbidden_values: Sequence[str] = (),
) -> Any:
    """Return JSON-safe evidence with identities aliased and secrets redacted."""

    if set(aliases) != {item[0] for item in PRINCIPALS} or any(
        not isinstance(alias, str) or not alias.startswith("principal:") for alias in aliases.values()
    ):
        raise MultiPrincipalFixtureError("PRINCIPAL_ALIAS_DENOMINATOR_INVALID")
    sensitive_values = tuple(item for item in forbidden_values if isinstance(item, str) and item)

    def visit(item: Any, *, key: str | None = None) -> Any:
        if key is not None and any(fragment in key.casefold() for fragment in SENSITIVE_EVIDENCE_KEY_FRAGMENTS):
            return "[REDACTED]"
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise MultiPrincipalFixtureError("NONFINITE_EVIDENCE_FORBIDDEN")
            return item
        if isinstance(item, str):
            rendered = item
            for sensitive in sensitive_values:
                rendered = rendered.replace(sensitive, "[REDACTED]")
            for principal_id, alias in aliases.items():
                rendered = rendered.replace(principal_id, alias)
            return rendered
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for raw_key, child in item.items():
                if not isinstance(raw_key, str):
                    raise MultiPrincipalFixtureError("EVIDENCE_KEY_INVALID")
                rendered_key = visit(raw_key)
                if rendered_key in result:
                    raise MultiPrincipalFixtureError("REDACTED_EVIDENCE_KEY_COLLISION")
                result[rendered_key] = visit(child, key=raw_key)
            return result
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        raise MultiPrincipalFixtureError("EVIDENCE_TYPE_INVALID")

    return visit(value)


def build_multi_principal_fixture_root(
    root: Path,
    *,
    allowed_parent: Path,
    synthetic_pair_root: Path,
    created_by: str,
) -> dict[str, Any]:
    candidate, parent, pair_root, pair_receipt = _validate_new_root(
        Path(root), Path(allowed_parent), Path(synthetic_pair_root)
    )
    if not isinstance(created_by, str) or not created_by or len(created_by) > 128:
        raise MultiPrincipalFixtureError("CREATOR_INVALID")
    temporary = Path(tempfile.mkdtemp(prefix=".code-assist-principal-staging-", dir=parent))
    published = False
    try:
        if _is_reparse(temporary):
            raise MultiPrincipalFixtureError("STAGING_REPARSE_FORBIDDEN")
        seed = _seed_document()
        _assert_seed_has_no_sensitive_keys(seed)
        _write_json(temporary / SEED_NAME, seed)
        provenance = {
            "schema_version": 1,
            "contract": "code_assist.notion.multi_principal_fixture_provenance.v1",
            "fixture_id": str(uuid4()),
            "created_by": created_by,
            "synthetic_only": True,
            "product_authority": False,
            "source_pair_root_locator_sha256": pair_receipt["root_locator_sha256"],
            "source_pair_marker_sha256": pair_receipt["marker_sha256"],
            "source_principal_count": len(PRINCIPALS),
            "credential_session_secret_content_copy_count": 0,
            "workspace_seed_sha256": _sha256_file(temporary / SEED_NAME),
        }
        _write_json(temporary / PROVENANCE_NAME, provenance)
        marker = {
            "schema_version": 1,
            "contract": "code_assist.notion.multi_principal_fixture_marker.v1",
            "fixture_id": provenance["fixture_id"],
            "synthetic_only": True,
            "root_locator_sha256": _root_locator_sha256(candidate),
            "source_pair_root_locator_sha256": pair_receipt["root_locator_sha256"],
            "allowed_relative_files": sorted(ALLOWED_FILES),
            "provenance_sha256": _sha256_file(temporary / PROVENANCE_NAME),
        }
        _write_json(temporary / MARKER_NAME, marker)
        os.replace(temporary, candidate)
        published = True
        return verify_multi_principal_fixture_root(candidate, synthetic_pair_root=pair_root)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, MultiPrincipalFixtureError):
            raise
        raise MultiPrincipalFixtureError("FIXTURE_BUILD_FAILED") from exc
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def verify_multi_principal_fixture_root(root: Path, *, synthetic_pair_root: Path) -> dict[str, Any]:
    candidate = Path(root).resolve(strict=True)
    pair_root = Path(synthetic_pair_root).resolve(strict=True)
    if not candidate.is_dir() or _is_reparse(candidate):
        raise MultiPrincipalFixtureError("FIXTURE_ROOT_REPARSE_OR_INVALID")
    pair_receipt = verify_synthetic_pair_root(pair_root)
    if _walk_exact_files(candidate) != ALLOWED_FILES:
        raise MultiPrincipalFixtureError("FIXTURE_FILE_DENOMINATOR_MISMATCH")
    try:
        marker = _load_json(candidate / MARKER_NAME)
        provenance = _load_json(candidate / PROVENANCE_NAME)
        seed = _load_json(candidate / SEED_NAME)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise MultiPrincipalFixtureError("FIXTURE_DOCUMENT_INVALID") from exc
    for relative, document in ((MARKER_NAME, marker), (PROVENANCE_NAME, provenance), (SEED_NAME, seed)):
        if not isinstance(document, dict) or (candidate / relative).read_bytes() != _canonical_bytes(document):
            raise MultiPrincipalFixtureError("FIXTURE_DOCUMENT_NONCANONICAL")
    if seed != _seed_document():
        raise MultiPrincipalFixtureError("WORKSPACE_SEED_DRIFT")
    _assert_seed_has_no_sensitive_keys(seed)
    if marker.get("fixture_id") != provenance.get("fixture_id"):
        raise MultiPrincipalFixtureError("FIXTURE_ID_MISMATCH")
    if marker.get("root_locator_sha256") != _root_locator_sha256(candidate):
        raise MultiPrincipalFixtureError("FIXTURE_ROOT_LOCATOR_MISMATCH")
    if set(marker.get("allowed_relative_files", [])) != ALLOWED_FILES:
        raise MultiPrincipalFixtureError("FIXTURE_MARKER_DENOMINATOR_MISMATCH")
    if marker.get("provenance_sha256") != _sha256_file(candidate / PROVENANCE_NAME):
        raise MultiPrincipalFixtureError("FIXTURE_PROVENANCE_HASH_MISMATCH")
    if provenance.get("workspace_seed_sha256") != _sha256_file(candidate / SEED_NAME):
        raise MultiPrincipalFixtureError("WORKSPACE_SEED_HASH_MISMATCH")
    for document in (marker, provenance):
        if document.get("source_pair_root_locator_sha256") != pair_receipt["root_locator_sha256"]:
            raise MultiPrincipalFixtureError("SOURCE_PAIR_BINDING_MISMATCH")
    if provenance.get("source_pair_marker_sha256") != pair_receipt["marker_sha256"]:
        raise MultiPrincipalFixtureError("SOURCE_PAIR_BINDING_MISMATCH")
    if provenance.get("credential_session_secret_content_copy_count") != 0:
        raise MultiPrincipalFixtureError("FORBIDDEN_COPY_CLAIM_INVALID")
    principals = seed.get("principals", [])
    ids = [item.get("principal_id") for item in principals if isinstance(item, dict)]
    roles = [item.get("fixture_role") for item in principals if isinstance(item, dict)]
    if ids != [item[0] for item in PRINCIPALS] or roles != [item[1] for item in PRINCIPALS]:
        raise MultiPrincipalFixtureError("PRINCIPAL_DENOMINATOR_MISMATCH")
    if any(count != 1 for count in Counter(ids).values()):
        raise MultiPrincipalFixtureError("PRINCIPAL_DENOMINATOR_MISMATCH")
    aliases = _principal_alias_map_from_receipt(pair_receipt)
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.multi_principal_fixture_receipt.v1",
        "status": "pass",
        "fixture_id": marker["fixture_id"],
        "root_locator_sha256": marker["root_locator_sha256"],
        "source_pair_root_locator_sha256": pair_receipt["root_locator_sha256"],
        "workspace_seed_sha256": provenance["workspace_seed_sha256"],
        "workspace_count": 1,
        "page_count": len(PAGE_IDS),
        "grant_count": len(seed["grants"]),
        "principal_count": len(PRINCIPALS),
        "principals": [
            {"fixture_role": fixture_role, "alias": aliases[principal_id]}
            for principal_id, fixture_role, _ in PRINCIPALS
        ],
        "credential_session_secret_content_copy_count": 0,
        "product_authority": False,
        "plaintext_principal_ids_or_credentials_in_receipt": False,
    }


def load_multi_principal_seed(root: Path, *, synthetic_pair_root: Path) -> dict[str, Any]:
    verify_multi_principal_fixture_root(root, synthetic_pair_root=synthetic_pair_root)
    return _load_json(Path(root).resolve(strict=True) / SEED_NAME)


__all__ = [
    "ACTION_ORDER",
    "ALLOWED_FILES",
    "MARKER_NAME",
    "MultiPrincipalFixtureError",
    "PAGE_IDS",
    "PROVENANCE_NAME",
    "SEED_NAME",
    "WORKSPACE_ID",
    "build_multi_principal_fixture_root",
    "load_multi_principal_seed",
    "principal_alias_map",
    "redact_principal_evidence",
    "verify_multi_principal_fixture_root",
]
