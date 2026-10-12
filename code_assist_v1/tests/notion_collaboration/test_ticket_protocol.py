from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.notion_collaboration.protocol_contract import (
    EnvelopeRejected,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    ProtocolEnvelope,
    StaleEpoch,
    TicketAuthority,
    TicketGrant,
    TicketRejected,
    session_binding_sha256,
)
from code_assist_v1.tools.verify_internal_collaborative_protocol_contract import (
    DEFAULT_PROTOCOL,
    verify_protocol_contract,
)
from code_assist_v1.tools.verify_notion_parity_catalog import load_json


ORIGIN = "http://127.0.0.1:10112"
SESSION_BINDING = session_binding_sha256("synthetic-session-secret")


def _issue(authority: TicketAuthority, **overrides: object):
    values: dict[str, object] = {
        "csrf_valid": True,
        "subject_id": "subject-owner",
        "workspace_id": "workspace-alpha",
        "page_id": "page-shared",
        "client_id": "client-browser-a",
        "session_binding_sha256": SESSION_BINDING,
        "origin": ORIGIN,
        "capabilities": ("read", "write"),
        "workspace_epoch": 4,
        "page_epoch": 7,
        "now_ms": 1_000,
        "ttl_ms": 10_000,
    }
    values.update(overrides)
    return authority.issue(**values)  # type: ignore[arg-type]


def _consume(
    authority: TicketAuthority,
    token: str,
    **overrides: object,
) -> TicketGrant:
    values: dict[str, object] = {
        "origin": ORIGIN,
        "subject_id": "subject-owner",
        "workspace_id": "workspace-alpha",
        "page_id": "page-shared",
        "client_id": "client-browser-a",
        "session_binding_sha256": SESSION_BINDING,
        "current_workspace_epoch": 4,
        "current_page_epoch": 7,
        "now_ms": 1_001,
    }
    values.update(overrides)
    return authority.consume(token, **values)  # type: ignore[arg-type]


def _codes(report: dict[str, object]) -> set[str]:
    return {item["code"] for item in report["errors"]}  # type: ignore[index]


@pytest.mark.unit
def test_t0a_p1_checked_in_ticket_threat_envelope_and_sequence_contract_is_current() -> None:
    report, exit_code = verify_protocol_contract()
    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["summary"] == {
        "packet_id": "T0A-P1",
        "threat_count": 7,
        "ticket_binding_count": 12,
        "message_type_count": 6,
        "identity_dimension_count": 6,
        "gateway_transport_implemented": False,
        "durable_event_store_implemented": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_t0a_p1_ticket_issue_requires_csrf_and_enforces_short_lifetime() -> None:
    authority = TicketAuthority()
    with pytest.raises(TicketRejected, match="^collaboration ticket rejected$"):
        _issue(authority, csrf_valid=False)
    with pytest.raises(ValueError, match="ttl exceeds"):
        _issue(authority, ttl_ms=30_001)
    assert authority.debug_digest_snapshot() == ()


@pytest.mark.unit
def test_t0a_p1_ticket_store_contains_only_digest_and_issued_repr_redacts_token() -> None:
    authority = TicketAuthority()
    issued = _issue(authority)
    snapshot = authority.debug_digest_snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["ticket_digest"].startswith("sha256:")
    assert issued.token not in json.dumps(snapshot, sort_keys=True)
    assert issued.token not in repr(issued)
    assert SESSION_BINDING in repr(issued)
    assert "synthetic-session-secret" not in repr(issued)


@pytest.mark.unit
def test_t0a_p1_ticket_consumption_is_atomic_one_time_and_non_oracle() -> None:
    authority = TicketAuthority()
    issued = _issue(authority)
    grant = _consume(authority, issued.token)
    assert grant.claims.jti == issued.claims.jti
    assert grant.claims.capabilities == ("read", "write")
    messages: list[str] = []
    for candidate in (issued.token, "x" * 43):
        with pytest.raises(TicketRejected) as rejected:
            _consume(authority, candidate)
        messages.append(str(rejected.value))
    assert messages == ["collaboration ticket rejected"] * 2


@pytest.mark.unit
def test_t0a_p1_wrong_origin_burns_recognized_ticket_without_binding_oracle() -> None:
    authority = TicketAuthority()
    issued = _issue(authority)
    messages: list[str] = []
    with pytest.raises(TicketRejected) as wrong_origin:
        _consume(authority, issued.token, origin="https://attacker.example")
    messages.append(str(wrong_origin.value))
    with pytest.raises(TicketRejected) as burned:
        _consume(authority, issued.token)
    messages.append(str(burned.value))
    assert messages == ["collaboration ticket rejected"] * 2
    assert authority.debug_digest_snapshot()[0]["consumed_at_ms"] == 1_001


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject_id", "subject-other"),
        ("workspace_id", "workspace-other"),
        ("page_id", "page-other"),
        ("client_id", "client-browser-b"),
        ("session_binding_sha256", session_binding_sha256("other-session")),
        ("current_workspace_epoch", 5),
        ("current_page_epoch", 8),
        ("now_ms", 11_000),
    ],
)
@pytest.mark.unit
def test_t0a_p1_every_subject_workspace_page_client_session_epoch_and_expiry_drift_rejects(
    field: str,
    value: object,
) -> None:
    authority = TicketAuthority()
    issued = _issue(authority)
    with pytest.raises(TicketRejected, match="^collaboration ticket rejected$"):
        _consume(authority, issued.token, **{field: value})


@pytest.mark.unit
def test_t0a_p1_versioned_envelope_binds_grant_capability_epochs_and_payload_hash() -> None:
    authority = TicketAuthority()
    grant = _consume(authority, _issue(authority).token)
    envelope = ProtocolEnvelope.create(
        grant,
        message_type="typed.command",
        operation_id="op-ticket-envelope-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload="{\"text\":\"hello\"}".encode(),
        capabilities=("write",),
        payload_encoding="application/json",
    )
    envelope.validate(grant, current_workspace_epoch=4, current_page_epoch=7)
    assert envelope.protocol_version == PROTOCOL_VERSION
    assert envelope.payload_length == len(envelope.payload)
    assert envelope.payload_sha256.startswith("sha256:")
    assert "synthetic-session-secret" not in repr(envelope)

    with pytest.raises(EnvelopeRejected, match="integrity"):
        replace(envelope, payload=b"tampered").validate(
            grant, current_workspace_epoch=4, current_page_epoch=7
        )
    with pytest.raises(EnvelopeRejected, match="binding"):
        replace(envelope, protocol_version=2).validate(
            grant, current_workspace_epoch=4, current_page_epoch=7
        )
    with pytest.raises(StaleEpoch):
        envelope.validate(grant, current_workspace_epoch=5, current_page_epoch=7)
    with pytest.raises(EnvelopeRejected, match="required capability"):
        ProtocolEnvelope.create(
            grant,
            message_type="typed.command",
            operation_id="op-ticket-envelope-0002",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"{}",
            capabilities=("read",),
        )
    with pytest.raises(EnvelopeRejected, match="maximum"):
        ProtocolEnvelope.create(
            grant,
            message_type="typed.command",
            operation_id="op-ticket-envelope-0003",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"x" * (MAX_PAYLOAD_BYTES + 1),
            capabilities=("write",),
        )


@pytest.mark.unit
def test_t0a_p1_verifier_rejects_ticket_envelope_sequence_and_false_green_drift(
    tmp_path: Path,
) -> None:
    protocol = copy.deepcopy(load_json(DEFAULT_PROTOCOL))
    protocol["ticket_contract"]["plaintext_ticket_persisted"] = True
    protocol["ticket_contract"]["required_bindings"].remove("page_epoch")
    protocol["envelope_contract"]["payload_hash"] = "none"
    protocol["sequence_ack_replay_contract"]["commit_before_ack"] = False
    protocol["prototype_boundary"]["network_listener_started"] = True
    protocol["claims"]["actual_gateway_transport_implemented"] = True
    path = tmp_path / "protocol-drift.json"
    path.write_text(json.dumps(protocol, ensure_ascii=False), encoding="utf-8")
    report, exit_code = verify_protocol_contract(protocol_path=path)
    assert exit_code == 1
    assert {
        "TICKET_BINDING_DRIFT",
        "TICKET_SECURITY_DRIFT",
        "ENVELOPE_SECURITY_DRIFT",
        "SEQUENCE_ACK_REPLAY_DRIFT",
        "PROTOTYPE_FALSE_PRODUCTION",
        "CONTRACT_FALSE_GREEN",
    }.issubset(_codes(report))
    assert any(code.startswith("PROTOCOL_SCHEMA_") for code in _codes(report))


@pytest.mark.unit
def test_t0a_p1_protocol_verifier_cli_passes_without_starting_gateway() -> None:
    root = Path(__file__).resolve().parents[3]
    run = subprocess.run(
        [
            sys.executable,
            "-E",
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_protocol_contract",
            "--compact",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "pass"
    assert report["summary"]["gateway_transport_implemented"] is False
