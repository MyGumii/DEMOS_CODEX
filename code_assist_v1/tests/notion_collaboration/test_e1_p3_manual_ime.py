from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import sys
import types
from typing import Any

import pytest

from code_assist_v1.notion_collaboration.approved_dev_pair import ApprovedDevPairError
from code_assist_v1.notion_collaboration.e1_p3_manual_ime import (
    EXPECTED_CONFIRMATION,
    EXPECTED_INPUT,
    REQUEST_PATH,
    run_finalization,
    validate_confirmation,
    validate_request,
)


def _request() -> dict[str, object]:
    value = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_e1_p3_native_ime_request_is_exactly_pending_and_scope_bound() -> None:
    request = _request()
    validated = validate_request()

    assert validated == {
        "workspace_id": request["workspace_id"],
        "page_id": request["page_id"],
        "required_input": EXPECTED_INPUT,
    }
    assert request["status"] == "ready_for_single_manual_input"
    assert request["claims"] == {
        "native_ime_verified": False,
        "E1_P3_gate_passed": False,
        "E1_phase_gate_closed": False,
        "product_complete": False,
    }


def test_e1_p3_native_ime_confirmation_must_match_exactly() -> None:
    expected = "sha256:" + hashlib.sha256(EXPECTED_CONFIRMATION.encode("utf-8")).hexdigest()

    assert validate_confirmation(EXPECTED_CONFIRMATION) == expected
    for invalid in ("", "PASS", EXPECTED_CONFIRMATION + " ", "E1-P3 IME 입력 완료"):
        with pytest.raises(
            ApprovedDevPairError,
            match="E1_P3_NATIVE_IME_CONFIRMATION_REQUIRED",
        ):
            validate_confirmation(invalid)


def test_e1_p3_native_ime_request_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    target = tmp_path / "duplicate.json"
    target.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    with pytest.raises(ApprovedDevPairError, match="E1_P3_IME_REQUEST_INVALID"):
        validate_request(target)


def test_e1_p3_native_ime_request_rejects_nonfinite_json(tmp_path: Path) -> None:
    target = tmp_path / "nonfinite.json"
    request = _request()
    raw = json.dumps(request, ensure_ascii=False).replace(
        '"schema_version": 1',
        '"schema_version": NaN',
        1,
    )
    target.write_text(raw, encoding="utf-8")

    with pytest.raises(ApprovedDevPairError, match="E1_P3_IME_REQUEST_INVALID"):
        validate_request(target)


def test_e1_p3_native_ime_request_rejects_false_green_claim(tmp_path: Path) -> None:
    target = tmp_path / "false-green.json"
    request = _request()
    claims = request["claims"]
    assert isinstance(claims, dict)
    claims["native_ime_verified"] = True
    target.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ApprovedDevPairError, match="E1_P3_IME_REQUEST_FALSE_GREEN"):
        validate_request(target)


def test_e1_p3_native_ime_finalizer_contains_no_automated_input_call() -> None:
    tree = ast.parse(inspect.getsource(run_finalization))
    forbidden_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in {
            "fill",
            "insert_text",
            "press",
            "type",
        }:
            forbidden_calls.append(function.attr)

    assert forbidden_calls == []


def test_e1_p3_native_ime_finalizer_trashes_only_after_two_client_convergence() -> None:
    source = inspect.getsource(run_finalization)

    convergence_check = source.index('if len(set(texts)) != 1:')
    occurrence_check = source.index('if final_text.count(request["required_input"]) != 1:')
    page_error_check = source.index("if page_errors:")
    trash_mutation = source.index('f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/trash"')
    assert convergence_check < occurrence_check < page_error_check < trash_mutation


class _FakePage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.page_error_callback: Any = None

    def on(self, event: str, callback: Any) -> None:
        assert event == "pageerror"
        self.page_error_callback = callback

    def wait_for_function(self, _source: str, *, arg: str) -> None:
        assert "join('\\n')" in _source
        assert arg == EXPECTED_INPUT
        if arg not in self.text:
            raise AssertionError("required native IME text is not visible")

    def evaluate(self, _source: str) -> dict[str, object]:
        assert "join('\\n')" in _source
        return {
            "text": self.text,
            "descriptor": {"ready": True},
            "connected": True,
            "crdt_ready": True,
            "unsynced_count": 0,
            "recovery_only": False,
            "offline_policy_blocked": False,
        }


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    def set_default_timeout(self, timeout: int) -> None:
        assert timeout == 20_000

    def new_page(self) -> _FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    version = "fake-chrome-e1-p3"

    def __init__(self, texts: list[str]) -> None:
        self.contexts = [_FakeContext(_FakePage(text)) for text in texts]
        self.closed = False

    def new_context(self, **options: object) -> _FakeContext:
        assert options == {
            "locale": "ko-KR",
            "service_workers": "block",
            "viewport": {"width": 1440, "height": 900},
        }
        return self.contexts.pop(0)

    def close(self) -> None:
        self.closed = True

    def is_connected(self) -> bool:
        return not self.closed


class _FakePlaywrightManager:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = types.SimpleNamespace(
            launch=lambda **options: self._launch(options)
        )
        self.browser = browser

    def _launch(self, options: dict[str, object]) -> _FakeBrowser:
        assert options == {"channel": "chrome", "headless": True}
        return self.browser

    def __enter__(self) -> "_FakePlaywrightManager":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    browser: _FakeBrowser,
) -> None:
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _FakePlaywrightManager(browser)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


def test_e1_p3_native_ime_finalizer_verifies_then_shares_and_trashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import code_assist_v1.notion_collaboration.e1_p3_manual_ime as target

    browser = _FakeBrowser([EXPECTED_INPUT, EXPECTED_INPUT])
    _install_fake_playwright(monkeypatch, browser)
    monkeypatch.setattr(target, "_login", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(target, "_open_collaborative_page", lambda *_args: None)
    operations: list[tuple[str, str]] = []

    def fake_api(
        _page: object,
        path: str,
        *,
        method: str = "GET",
        body: object = None,
    ) -> dict[str, object]:
        operations.append((method, path))
        if path.endswith("/collaboration"):
            return {"page": {"workspace_epoch": 3, "document_epoch": 4}}
        if path.endswith("/shares") and method == "GET":
            return {"items": []}
        if path.endswith("/shares") and method == "POST":
            assert isinstance(body, dict) and body["principal_id"] == "user01"
            return {"status": "created"}
        if path.endswith("/trash"):
            assert isinstance(body, dict) and body["base_version"] == 7
            return {"status": "trashed"}
        if path.endswith("ws_b766f073426e43578863ce9448f42034"):
            return {"workspace": {"version": 7}}
        raise AssertionError((method, path))

    monkeypatch.setattr(target, "_api", fake_api)

    receipt = target.run_finalization(
        user_confirmation=EXPECTED_CONFIRMATION,
        credentials={"admin": "synthetic-admin", "user01": "synthetic-user"},
    )

    assert receipt["status"] == "pass"
    assert receipt["required_text_occurrence_count"] == 1
    assert receipt["same_projection_hash"] is True
    assert receipt["manual_request_sha256"] == (
        "sha256:" + hashlib.sha256(REQUEST_PATH.read_bytes()).hexdigest()
    )
    request = validate_request()
    assert receipt["test_page_locator_sha256"] == (
        "sha256:"
        + hashlib.sha256(
            (request["workspace_id"] + "\n" + request["page_id"]).encode("utf-8")
        ).hexdigest()
    )
    assert receipt["test_page_moved_to_trash"] is True
    assert receipt["automated_text_input_performed"] is False
    assert receipt["product_complete"] is False
    assert operations[-1][0] == "POST" and operations[-1][1].endswith("/trash")
    assert any(method == "POST" and path.endswith("/shares") for method, path in operations)
    assert browser.closed is True


def test_e1_p3_native_ime_finalizer_rejects_projection_mismatch_without_trash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import code_assist_v1.notion_collaboration.e1_p3_manual_ime as target

    browser = _FakeBrowser([EXPECTED_INPUT, EXPECTED_INPUT + " 불일치"])
    _install_fake_playwright(monkeypatch, browser)
    monkeypatch.setattr(target, "_login", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(target, "_open_collaborative_page", lambda *_args: None)
    operations: list[tuple[str, str]] = []

    def fake_api(
        _page: object,
        path: str,
        *,
        method: str = "GET",
        body: object = None,
    ) -> dict[str, object]:
        del body
        operations.append((method, path))
        if path.endswith("/collaboration"):
            return {"page": {"workspace_epoch": 3, "document_epoch": 4}}
        if path.endswith("/shares"):
            return {
                "items": [
                    {
                        "principal_type": "subject",
                        "principal_id": "user01",
                        "role": "edit",
                    }
                ]
            }
        raise AssertionError((method, path))

    monkeypatch.setattr(target, "_api", fake_api)

    with pytest.raises(
        ApprovedDevPairError,
        match="E1_P3_NATIVE_IME_CONVERGENCE_FAILED",
    ):
        target.run_finalization(
            user_confirmation=EXPECTED_CONFIRMATION,
            credentials={"admin": "synthetic-admin", "user01": "synthetic-user"},
        )

    assert not any(path.endswith("/trash") for _method, path in operations)
    assert browser.closed is True
