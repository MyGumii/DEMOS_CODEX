"""Create and verify one disposable, synthetic-only paired development root.

The builder has no source/copy argument and accepts only a new child of an
explicitly bound parent.  It creates four synthetic principals, a new session
secret, and provenance; it never opens existing Code Assist data roots.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import tempfile
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from werkzeug.security import check_password_hash, generate_password_hash


MARKER_NAME = ".code-assist-synthetic-pair-root.json"
PROVENANCE_RELATIVE = "fixture/provenance.json"
CREDENTIALS_RELATIVE = "fixture/principals.credentials.json"
USERS_RELATIVE = "data/.runtime/users.json"
SECRET_RELATIVE = "data/.runtime/secret.key"
ALLOWED_FILES = frozenset(
    {MARKER_NAME, PROVENANCE_RELATIVE, CREDENTIALS_RELATIVE, USERS_RELATIVE, SECRET_RELATIVE}
)
PRINCIPALS = (
    ("fixture-owner", "owner", "admin"),
    ("fixture-member", "member", "user"),
    ("fixture-page-guest", "internal_page_guest", "user"),
    ("fixture-outsider", "outsider", "user"),
)
CREDENTIAL_VERIFY_ATTEMPTS = 3
FORBIDDEN_COPIED_CATEGORIES = (
    "knowledge",
    "user_data",
    "sessions",
    "workspace",
    "blobs",
    "projections",
    "audit",
    "runtime",
    "source_secret",
)
REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class SyntheticPairRootError(RuntimeError):
    pass


class DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite number: {value}")),
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def _canonical_document_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(value + "\n")
        output.flush()
        os.fsync(output.fileno())
    try:
        path.chmod(0o600)
    except OSError:
        pass


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
        raise SyntheticPairRootError("ROOT_PATH_UNREADABLE") from exc
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_FLAG)


def _validate_new_root(root: Path, allowed_parent: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or not allowed_parent.is_absolute():
        raise SyntheticPairRootError("ABSOLUTE_PATH_REQUIRED")
    parent = allowed_parent.resolve(strict=True)
    candidate = root.resolve(strict=False)
    if candidate.parent != parent:
        raise SyntheticPairRootError("ROOT_PARENT_MISMATCH")
    if candidate.exists():
        raise SyntheticPairRootError("ROOT_ALREADY_EXISTS")
    if not parent.is_dir() or _is_reparse(parent):
        raise SyntheticPairRootError("PARENT_REPARSE_OR_INVALID")
    if candidate.name in {"", ".", ".."}:
        raise SyntheticPairRootError("ROOT_NAME_INVALID")
    return candidate, parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _credential_matches(password_hash: Any, password: Any) -> bool:
    """Retry a transient false negative without weakening mismatch rejection."""

    if not isinstance(password_hash, str) or not isinstance(password, str):
        return False
    for _ in range(CREDENTIAL_VERIFY_ATTEMPTS):
        try:
            if check_password_hash(password_hash, password):
                return True
        except (TypeError, ValueError):
            return False
    return False


def build_synthetic_pair_root(
    root: Path,
    *,
    allowed_parent: Path,
    created_by: str,
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
    now: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    """Atomically publish a new synthetic root and return a redacted receipt."""

    candidate, parent = _validate_new_root(Path(root), Path(allowed_parent))
    if not isinstance(created_by, str) or not created_by or len(created_by) > 128:
        raise SyntheticPairRootError("CREATOR_INVALID")
    temporary = Path(tempfile.mkdtemp(prefix=".code-assist-pair-staging-", dir=parent))
    published = False
    try:
        if _is_reparse(temporary):
            raise SyntheticPairRootError("STAGING_REPARSE_FORBIDDEN")
        root_id = str(uuid4())
        created_at = now()
        created_epoch = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
        pair_secret = token_factory(48)
        if len(pair_secret.encode("utf-8")) < 32:
            raise SyntheticPairRootError("GENERATED_SECRET_TOO_SHORT")
        credentials: list[dict[str, str]] = []
        users: dict[str, dict[str, Any]] = {}
        for user_id, fixture_role, auth_role in PRINCIPALS:
            password = token_factory(24)
            if len(password) < 16:
                raise SyntheticPairRootError("GENERATED_PASSWORD_TOO_SHORT")
            credentials.append({"id": user_id, "fixture_role": fixture_role, "password": password})
            users[user_id] = {
                "id": user_id,
                "display_name": f"Synthetic {fixture_role.replace('_', ' ').title()}",
                "role": auth_role,
                "disabled": False,
                "password_hash": generate_password_hash(password),
                "must_change_password": False,
                "password_changed_at": created_epoch,
                "created_at": created_epoch,
                "updated_at": created_epoch,
                "synthetic_fixture_role": fixture_role,
            }

        _write_json(temporary / USERS_RELATIVE, {"users": users})
        _write_secret(temporary / SECRET_RELATIVE, pair_secret)
        _write_json(
            temporary / CREDENTIALS_RELATIVE,
            {
                "schema_version": 1,
                "contract": "code_assist.notion.synthetic_pair_credentials.v1",
                "root_id": root_id,
                "synthetic_only": True,
                "principals": credentials,
            },
        )
        payload_hashes = {
            relative: _sha256_file(temporary / relative)
            for relative in (USERS_RELATIVE, SECRET_RELATIVE, CREDENTIALS_RELATIVE)
        }
        provenance = {
            "schema_version": 1,
            "contract": "code_assist.notion.synthetic_pair_provenance.v1",
            "root_id": root_id,
            "created_at": created_at,
            "created_by": created_by,
            "synthetic_only": True,
            "source_copy_api_available": False,
            "copied_source_count": 0,
            "forbidden_copied_categories": {key: False for key in FORBIDDEN_COPIED_CATEGORIES},
            "secret_source": "cryptographic_generation",
            "principal_source": "synthetic_generation",
            "principal_roles": [item[1] for item in PRINCIPALS],
            "file_sha256": payload_hashes,
            "secret_fingerprint": _sha256_bytes(pair_secret.encode("utf-8")),
        }
        _write_json(temporary / PROVENANCE_RELATIVE, provenance)
        marker = {
            "schema_version": 1,
            "contract": "code_assist.notion.synthetic_pair_root_marker.v1",
            "root_id": root_id,
            "synthetic_only": True,
            "allowed_relative_files": sorted(ALLOWED_FILES),
            "provenance_sha256": _sha256_file(temporary / PROVENANCE_RELATIVE),
            "root_locator_sha256": _root_locator_sha256(candidate),
        }
        _write_json(temporary / MARKER_NAME, marker)
        os.replace(temporary, candidate)
        published = True
        return verify_synthetic_pair_root(candidate)
    except (OSError, ValueError) as exc:
        if isinstance(exc, SyntheticPairRootError):
            raise
        raise SyntheticPairRootError("SYNTHETIC_ROOT_BUILD_FAILED") from exc
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _walk_exact_files(root: Path) -> set[str]:
    files: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        if _is_reparse(directory):
            raise SyntheticPairRootError("REPARSE_ENTRY_FORBIDDEN")
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink() or _is_reparse(path):
                    raise SyntheticPairRootError("REPARSE_ENTRY_FORBIDDEN")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.add(path.relative_to(root).as_posix())
                else:
                    raise SyntheticPairRootError("NONREGULAR_ENTRY_FORBIDDEN")
    return files


def verify_synthetic_pair_root(root: Path) -> dict[str, Any]:
    candidate = Path(root).resolve(strict=True)
    if not candidate.is_dir() or _is_reparse(candidate):
        raise SyntheticPairRootError("ROOT_REPARSE_OR_INVALID")
    files = _walk_exact_files(candidate)
    if files != ALLOWED_FILES:
        raise SyntheticPairRootError("ROOT_FILE_DENOMINATOR_MISMATCH")
    try:
        marker = _load_json(candidate / MARKER_NAME)
        provenance = _load_json(candidate / PROVENANCE_RELATIVE)
        users_document = _load_json(candidate / USERS_RELATIVE)
        credentials_document = _load_json(candidate / CREDENTIALS_RELATIVE)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SyntheticPairRootError("ROOT_DOCUMENT_INVALID") from exc
    if not all(isinstance(value, dict) for value in (marker, provenance, users_document, credentials_document)):
        raise SyntheticPairRootError("ROOT_DOCUMENT_INVALID")
    for relative, document in (
        (MARKER_NAME, marker),
        (PROVENANCE_RELATIVE, provenance),
        (USERS_RELATIVE, users_document),
        (CREDENTIALS_RELATIVE, credentials_document),
    ):
        if (candidate / relative).read_bytes() != _canonical_document_bytes(document):
            raise SyntheticPairRootError("ROOT_DOCUMENT_NONCANONICAL")
    if marker.get("synthetic_only") is not True or provenance.get("synthetic_only") is not True:
        raise SyntheticPairRootError("SYNTHETIC_MARKER_INVALID")
    if marker.get("root_id") != provenance.get("root_id") or marker.get("root_id") != credentials_document.get("root_id"):
        raise SyntheticPairRootError("ROOT_ID_MISMATCH")
    if marker.get("root_locator_sha256") != _root_locator_sha256(candidate):
        raise SyntheticPairRootError("ROOT_LOCATOR_MISMATCH")
    if set(marker.get("allowed_relative_files", [])) != ALLOWED_FILES:
        raise SyntheticPairRootError("MARKER_FILE_DENOMINATOR_MISMATCH")
    if marker.get("provenance_sha256") != _sha256_file(candidate / PROVENANCE_RELATIVE):
        raise SyntheticPairRootError("PROVENANCE_HASH_MISMATCH")
    if provenance.get("copied_source_count") != 0 or provenance.get("source_copy_api_available") is not False:
        raise SyntheticPairRootError("SOURCE_COPY_CLAIM_INVALID")
    forbidden = provenance.get("forbidden_copied_categories")
    if not isinstance(forbidden, dict) or set(forbidden) != set(FORBIDDEN_COPIED_CATEGORIES) or any(forbidden.values()):
        raise SyntheticPairRootError("FORBIDDEN_COPY_CLAIM_INVALID")
    payload_hashes = provenance.get("file_sha256")
    if not isinstance(payload_hashes, dict) or set(payload_hashes) != {USERS_RELATIVE, SECRET_RELATIVE, CREDENTIALS_RELATIVE}:
        raise SyntheticPairRootError("PAYLOAD_HASH_DENOMINATOR_MISMATCH")
    for relative, expected in payload_hashes.items():
        if relative not in ALLOWED_FILES or expected != _sha256_file(candidate / relative):
            raise SyntheticPairRootError("PAYLOAD_HASH_MISMATCH")
    secret = (candidate / SECRET_RELATIVE).read_text(encoding="utf-8").strip()
    if len(secret.encode("utf-8")) < 32 or provenance.get("secret_fingerprint") != _sha256_bytes(secret.encode("utf-8")):
        raise SyntheticPairRootError("GENERATED_SECRET_INVALID")
    users = users_document.get("users")
    principals = credentials_document.get("principals")
    expected_ids = [item[0] for item in PRINCIPALS]
    expected_roles = [item[1] for item in PRINCIPALS]
    if not isinstance(users, dict) or set(users) != set(expected_ids) or not isinstance(principals, list):
        raise SyntheticPairRootError("PRINCIPAL_DENOMINATOR_MISMATCH")
    ids = [item.get("id") for item in principals if isinstance(item, dict)]
    roles = [item.get("fixture_role") for item in principals if isinstance(item, dict)]
    if ids != expected_ids or roles != expected_roles or any(count != 1 for count in Counter(ids).values()):
        raise SyntheticPairRootError("PRINCIPAL_DENOMINATOR_MISMATCH")
    for principal in principals:
        user = users.get(principal["id"], {})
        if user.get("synthetic_fixture_role") != principal["fixture_role"] or not _credential_matches(
            user.get("password_hash"), principal.get("password")
        ):
            raise SyntheticPairRootError("PRINCIPAL_CREDENTIAL_MISMATCH")
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.synthetic_pair_root_receipt.v1",
        "status": "pass",
        "root_id": marker["root_id"],
        "root_locator_sha256": marker["root_locator_sha256"],
        "marker_sha256": _sha256_file(candidate / MARKER_NAME),
        "provenance_sha256": marker["provenance_sha256"],
        "secret_fingerprint": provenance["secret_fingerprint"],
        "principal_ids": expected_ids,
        "principal_roles": expected_roles,
        "copied_source_count": 0,
        "forbidden_copied_category_count": len(FORBIDDEN_COPIED_CATEGORIES),
        "plaintext_credentials_in_receipt": False,
    }


def load_fixture_credentials(root: Path) -> list[dict[str, str]]:
    verify_synthetic_pair_root(root)
    document = _load_json(Path(root).resolve(strict=True) / CREDENTIALS_RELATIVE)
    return [dict(item) for item in document["principals"]]


__all__ = [
    "ALLOWED_FILES",
    "MARKER_NAME",
    "PRINCIPALS",
    "SyntheticPairRootError",
    "build_synthetic_pair_root",
    "load_fixture_credentials",
    "verify_synthetic_pair_root",
]
