from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import pytest

from code_assist_v1.notion_collaboration.q0_validation import nearest_rank_distribution
from code_assist_v1.notion_collaboration.runtime_gateway import ReconnectThrottled
from code_assist_v1.tests.notion_collaboration.q0_support import (
    ORIGIN,
    PAGE_ID,
    build_q0_environment,
)


@pytest.mark.integration
@pytest.mark.concurrency
@pytest.mark.load
@pytest.mark.scenario("PERF-001")
def test_q0_reconnect_storm_is_bounded_before_ticket_consumption_and_recovers(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(
        tmp_path / "reconnect-storm",
        registered_users=3,
        reconnect_attempts=5,
    )
    subject = environment.subjects[1]
    tickets = [
        environment.issue_ticket(
            subject,
            client_id="storm-client",
            binding_secret="storm-binding",
            now_ms=1_000 + index,
        )
        for index in range(20)
    ]

    def connect(index: int) -> tuple[str, str | float]:
        ticket, binding = tickets[index]
        started = time.perf_counter()
        try:
            session = environment.gateway.connect(
                ticket.token,
                origin=ORIGIN,
                subject_id=subject,
                workspace_id=environment.workspace_id,
                page_id=PAGE_ID,
                client_id="storm-client",
                session_binding_sha256=binding,
                now_ms=2_000,
            )
        except ReconnectThrottled:
            return "throttled", (time.perf_counter() - started) * 1_000
        return session.connection_id, (time.perf_counter() - started) * 1_000

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(connect, range(20)))
    accepted = [item for item in outcomes if item[0].startswith("conn_")]
    throttled = [item for item in outcomes if item[0] == "throttled"]
    assert len(accepted) == 5
    assert len(throttled) == 15
    assert environment.gateway.active_connection_count() == 1
    active = [
        environment.gateway.session_status(str(item[0]))
        for item in accepted
        if environment.gateway.session_status(str(item[0]))["active"]
    ]
    assert len(active) == 1
    assert all(
        environment.gateway.session_status(str(item[0]))["close_reason"]
        == "superseded_by_reconnect"
        for item in accepted
        if not environment.gateway.session_status(str(item[0]))["active"]
    )

    rows = {item["jti"]: item for item in environment.transport.ticket_rows()}
    assert sum(rows[ticket.claims.jti]["consumed_at_ms"] is not None for ticket, _ in tickets) == 5
    assert sum(rows[ticket.claims.jti]["consumed_at_ms"] is None for ticket, _ in tickets) == 15
    distribution = nearest_rank_distribution(float(item[1]) for item in outcomes)
    assert distribution["count"] == 20
    assert distribution["p50"] <= distribution["p95"] <= distribution["p99"]

    recovered = environment.connect(
        subject,
        client_id="storm-client",
        binding_secret="storm-binding",
        now_ms=12_000,
    )
    assert environment.gateway.session_status(recovered.connection_id)["active"] is True
    assert environment.gateway.active_connection_count() == 1
    snapshot = environment.gateway.metrics_snapshot()
    throttled_metric = next(
        item
        for item in snapshot["counters"]
        if item["name"] == "ticket_admission_total_by_result"
        and item["labels"].get("result_code") == "reconnect_throttled"
    )
    assert throttled_metric["value"] == 15
    environment.gateway.stop()
