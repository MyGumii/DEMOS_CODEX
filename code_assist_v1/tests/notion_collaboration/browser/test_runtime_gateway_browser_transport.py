from __future__ import annotations

import asyncio

from code_assist_v1.notion_collaboration.gateway_service import RuntimeGatewayASGI


def test_t0b_p3_browser_probe_document_is_origin_bound_and_inert() -> None:
    class StubGateway:
        metrics = type("Metrics", (), {"snapshot": staticmethod(lambda: {"contract": "metrics"})})()

        @staticmethod
        def health():
            return {"state": "READY"}

    application = RuntimeGatewayASGI(StubGateway(), compactor_handshake_matches=True)  # type: ignore[arg-type]
    received = iter([{"type": "http.request", "body": b"", "more_body": False}])
    sent: list[dict] = []

    async def receive():
        return next(received)

    async def send(message):
        sent.append(message)

    asyncio.run(application({"type": "http", "method": "GET", "path": "/test-client"}, receive, send))
    assert sent[0]["status"] == 200
    headers = dict(sent[0]["headers"])
    assert headers[b"content-type"] == b"text/html; charset=utf-8"
    assert headers[b"cache-control"] == b"no-store"
    assert b"connect-src 'self' ws:" in headers[b"content-security-policy"]
    assert b"<script" not in sent[1]["body"]
    assert b"ready" in sent[1]["body"]
