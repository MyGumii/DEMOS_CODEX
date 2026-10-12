from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.synthetic_pair_root import (
    ALLOWED_FILES,
    CREDENTIALS_RELATIVE,
    MARKER_NAME,
    PRINCIPALS,
    PROVENANCE_RELATIVE,
    SECRET_RELATIVE,
    USERS_RELATIVE,
    SyntheticPairRootError,
    build_synthetic_pair_root,
    load_fixture_credentials,
    verify_synthetic_pair_root,
)


class _Tokens:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self, size: int) -> str:
        self.index += 1
        return f"synthetic-{self.index:02d}-" + ("x" * max(size, 40))


def _build(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "pair-root"
    receipt = build_synthetic_pair_root(
        root,
        allowed_parent=tmp_path,
        created_by="pytest-d0a-p2",
        token_factory=_Tokens(),
        now=lambda: "2026-08-23T06:20:00Z",
    )
    return root, receipt


@pytest.mark.unit
def test_builder_publishes_exact_marker_bound_synthetic_root(tmp_path: Path) -> None:
    root, receipt = _build(tmp_path)
    files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    assert files == ALLOWED_FILES
    assert receipt["status"] == "pass"
    assert receipt["principal_ids"] == [item[0] for item in PRINCIPALS]
    assert receipt["principal_roles"] == [item[1] for item in PRINCIPALS]
    assert receipt["copied_source_count"] == 0
    assert receipt["plaintext_credentials_in_receipt"] is False
    assert str(root) not in json.dumps(receipt, sort_keys=True)
    assert verify_synthetic_pair_root(root) == receipt


@pytest.mark.unit
def test_four_credentials_match_compatible_auth_registry_without_content_dirs(tmp_path: Path) -> None:
    root, _ = _build(tmp_path)
    credentials = load_fixture_credentials(root)
    users = json.loads((root / USERS_RELATIVE).read_text(encoding="utf-8"))["users"]
    assert {item["id"] for item in credentials} == set(users)
    assert users["fixture-owner"]["role"] == "admin"
    assert all(not user["disabled"] for user in users.values())
    assert not any((root / name).exists() for name in ("knowledge", "user_data", "sessions", "workspace", ".runtime"))
    assert (root / "data" / ".runtime" / "users.json").is_file()


@pytest.mark.unit
def test_credential_verification_retries_transient_false_but_keeps_persistent_mismatch_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_assist_v1.notion_collaboration import synthetic_pair_root as module

    root, receipt = _build(tmp_path)
    transient_calls = 0

    def transient_false(_password_hash: str, _password: str) -> bool:
        nonlocal transient_calls
        transient_calls += 1
        return transient_calls > 1

    monkeypatch.setattr(module, "check_password_hash", transient_false)
    assert verify_synthetic_pair_root(root) == receipt
    assert transient_calls == len(PRINCIPALS) + 1

    persistent_calls = 0

    def persistent_false(_password_hash: str, _password: str) -> bool:
        nonlocal persistent_calls
        persistent_calls += 1
        return False

    monkeypatch.setattr(module, "check_password_hash", persistent_false)
    with pytest.raises(SyntheticPairRootError, match="PRINCIPAL_CREDENTIAL_MISMATCH"):
        verify_synthetic_pair_root(root)
    assert persistent_calls == module.CREDENTIAL_VERIFY_ATTEMPTS


@pytest.mark.unit
def test_provenance_proves_generation_and_zero_copy(tmp_path: Path) -> None:
    root, _ = _build(tmp_path)
    provenance = json.loads((root / PROVENANCE_RELATIVE).read_text(encoding="utf-8"))
    assert provenance["source_copy_api_available"] is False
    assert provenance["copied_source_count"] == 0
    assert provenance["secret_source"] == "cryptographic_generation"
    assert provenance["principal_source"] == "synthetic_generation"
    assert set(provenance["forbidden_copied_categories"].values()) == {False}
    assert set(provenance["file_sha256"]) == {USERS_RELATIVE, SECRET_RELATIVE, CREDENTIALS_RELATIVE}


@pytest.mark.unit
def test_builder_is_create_only_and_preserves_existing_target(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    sentinel = root / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(SyntheticPairRootError, match="ROOT_ALREADY_EXISTS"):
        build_synthetic_pair_root(root, allowed_parent=tmp_path, created_by="pytest", token_factory=_Tokens())
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert list(root.iterdir()) == [sentinel]


@pytest.mark.unit
def test_builder_requires_absolute_exact_parent_and_no_reparse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(SyntheticPairRootError, match="ABSOLUTE_PATH_REQUIRED"):
        build_synthetic_pair_root(Path("relative"), allowed_parent=tmp_path, created_by="pytest")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(SyntheticPairRootError, match="ROOT_PARENT_MISMATCH"):
        build_synthetic_pair_root(other / "pair", allowed_parent=tmp_path, created_by="pytest")

    from code_assist_v1.notion_collaboration import synthetic_pair_root as module

    monkeypatch.setattr(module, "_is_reparse", lambda path: Path(path) == tmp_path)
    with pytest.raises(SyntheticPairRootError, match="PARENT_REPARSE_OR_INVALID"):
        build_synthetic_pair_root(tmp_path / "pair", allowed_parent=tmp_path, created_by="pytest")


@pytest.mark.unit
@pytest.mark.parametrize("relative", [USERS_RELATIVE, SECRET_RELATIVE, CREDENTIALS_RELATIVE, PROVENANCE_RELATIVE, MARKER_NAME])
def test_every_bound_file_tamper_fails_closed(tmp_path: Path, relative: str) -> None:
    root, _ = _build(tmp_path)
    path = root / relative
    if path.suffix == ".json":
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    else:
        path.write_text("tampered-secret\n", encoding="utf-8")
    with pytest.raises(SyntheticPairRootError):
        verify_synthetic_pair_root(root)


@pytest.mark.unit
def test_extra_file_and_duplicate_json_key_fail_closed(tmp_path: Path) -> None:
    root, _ = _build(tmp_path)
    (root / "unexpected.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SyntheticPairRootError, match="ROOT_FILE_DENOMINATOR_MISMATCH"):
        verify_synthetic_pair_root(root)

    (root / "unexpected.txt").unlink()
    (root / MARKER_NAME).write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(SyntheticPairRootError, match="ROOT_DOCUMENT_INVALID"):
        verify_synthetic_pair_root(root)


@pytest.mark.unit
def test_generation_failure_does_not_publish_or_leave_staging(tmp_path: Path) -> None:
    root = tmp_path / "pair-root"
    with pytest.raises(SyntheticPairRootError, match="GENERATED_SECRET_TOO_SHORT"):
        build_synthetic_pair_root(root, allowed_parent=tmp_path, created_by="pytest", token_factory=lambda _size: "short")
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_builder_api_has_no_source_or_copy_parameter() -> None:
    parameters = set(inspect.signature(build_synthetic_pair_root).parameters)
    assert not {"source", "source_root", "copy_from", "existing_root"}.intersection(parameters)
