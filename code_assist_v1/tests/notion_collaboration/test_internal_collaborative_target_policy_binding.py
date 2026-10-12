from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from code_assist_v1.tools.bind_internal_collaborative_target_policy import (
    BINDING_INPUT_CONTRACT,
    PolicyBindingError,
    bind_policy,
)
from code_assist_v1.tools.verify_internal_collaborative_target_restore import (
    POLICY_PATH,
    verify,
)


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_tag(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _anchor(key_id: str) -> dict[str, str]:
    key = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "key_id": key_id,
        "public_key_base64": base64.b64encode(key).decode("ascii"),
        "public_key_sha256": "sha256:" + hashlib.sha256(key).hexdigest(),
    }


def _binding_input() -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": BINDING_INPUT_CONTRACT,
        "binding_record_id": "o1b-policy-binding-01",
        "bound_at": "2026-09-07T09:15:00+09:00",
        "release_candidate": {
            "build_sha256": _sha_tag("build"),
            "configuration_sha256": _sha_tag("configuration"),
            "catalog_sha256": _sha_tag("catalog"),
            "schema_sha256": _sha_tag("schema"),
            "sqlite_version": "3.50.4",
            "sqlite_source_id_sha256": _sha_tag("sqlite-source"),
            "successor_flags_default_off": True,
            "one_durable_worker": True,
            "one_projector": True,
        },
        "trust_anchors": {
            role: _anchor(f"{role.replace('_', '-')}-key-01")
            for role in (
                "approval",
                "handoff",
                "evidence_index",
                "receipt_reviewer",
            )
        },
    }


@pytest.mark.unit
def test_o1b_policy_binding_creates_verified_non_executing_successor(
    tmp_path: Path,
) -> None:
    binding_path = tmp_path / "binding-input.json"
    output_path = tmp_path / "bound-policy.json"
    _write(binding_path, _binding_input())
    result = bind_policy(
        base_policy_path=POLICY_PATH,
        expected_base_policy_sha256=_sha256_file(POLICY_PATH),
        binding_input_path=binding_path,
        output_path=output_path,
    )
    report = verify(output_path)
    assert result["status"] == "pass"
    assert result["bound_policy_sha256"] == _sha256_file(output_path)
    assert result["trust_anchor_count"] == 4
    assert result["code_pin_activation_required"] is True
    assert result["target_action_executed"] is False
    assert result["gate_passed"] is False
    assert report["status"] == "pass", report
    assert report["readiness_contract_passed"] is True
    assert report["gate_passed"] is False


@pytest.mark.unit
def test_o1b_policy_binding_rejects_base_policy_hash_drift(tmp_path: Path) -> None:
    binding_path = tmp_path / "binding-input.json"
    _write(binding_path, _binding_input())
    with pytest.raises(PolicyBindingError, match="base_policy_hash_mismatch"):
        bind_policy(
            base_policy_path=POLICY_PATH,
            expected_base_policy_sha256=_sha_tag("wrong-policy"),
            binding_input_path=binding_path,
            output_path=tmp_path / "bound-policy.json",
        )


@pytest.mark.unit
def test_o1b_policy_binding_rejects_pinned_but_invalid_base_policy(
    tmp_path: Path,
) -> None:
    base_value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    base_value["claims"]["O1B_P1_gate_passed"] = True
    base_path = tmp_path / "invalid-base-policy.json"
    binding_path = tmp_path / "binding-input.json"
    output_path = tmp_path / "bound-policy.json"
    _write(base_path, base_value)
    _write(binding_path, _binding_input())
    with pytest.raises(PolicyBindingError, match="bound_policy_readiness_invalid"):
        bind_policy(
            base_policy_path=base_path,
            expected_base_policy_sha256=_sha256_file(base_path),
            binding_input_path=binding_path,
            output_path=output_path,
        )
    assert not output_path.exists()


@pytest.mark.unit
def test_o1b_policy_binding_rejects_reused_public_key(tmp_path: Path) -> None:
    value = _binding_input()
    anchors = value["trust_anchors"]
    assert isinstance(anchors, dict)
    anchors["handoff"] = dict(anchors["approval"])
    anchors["handoff"]["key_id"] = "handoff-key-01"  # type: ignore[index]
    binding_path = tmp_path / "binding-input.json"
    _write(binding_path, value)
    with pytest.raises(PolicyBindingError, match="trust_anchor_public_key_not_distinct"):
        bind_policy(
            base_policy_path=POLICY_PATH,
            expected_base_policy_sha256=_sha256_file(POLICY_PATH),
            binding_input_path=binding_path,
            output_path=tmp_path / "bound-policy.json",
        )


@pytest.mark.unit
def test_o1b_policy_binding_is_create_only(tmp_path: Path) -> None:
    binding_path = tmp_path / "binding-input.json"
    output_path = tmp_path / "bound-policy.json"
    _write(binding_path, _binding_input())
    _write(output_path, {"preserve": True})
    before = output_path.read_bytes()
    with pytest.raises(FileExistsError):
        bind_policy(
            base_policy_path=POLICY_PATH,
            expected_base_policy_sha256=_sha256_file(POLICY_PATH),
            binding_input_path=binding_path,
            output_path=output_path,
        )
    assert output_path.read_bytes() == before


@pytest.mark.unit
def test_o1b_policy_binding_rejects_private_key_field(tmp_path: Path) -> None:
    value = _binding_input()
    anchors = value["trust_anchors"]
    assert isinstance(anchors, dict)
    approval = anchors["approval"]
    assert isinstance(approval, dict)
    approval["private_key_base64"] = "forbidden"
    binding_path = tmp_path / "binding-input.json"
    _write(binding_path, value)
    with pytest.raises(PolicyBindingError, match="public_anchor_shape_invalid"):
        bind_policy(
            base_policy_path=POLICY_PATH,
            expected_base_policy_sha256=_sha256_file(POLICY_PATH),
            binding_input_path=binding_path,
            output_path=tmp_path / "bound-policy.json",
        )
