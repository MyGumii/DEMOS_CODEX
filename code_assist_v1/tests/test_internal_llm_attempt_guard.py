"""Provider fallback must never swallow an invocation's authority failure."""
import pytest

from code_assist_v1 import internal_llm
from code_assist_v1.knowledge_core.agent_execution import CanonicalAgentTurnExecutor
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_agent_execution import _context


class AuthorityChanged(RuntimeError):
    code = "SCOPE_STALE"


def _providers(monkeypatch, call):
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *a, **kw: [
        ("first", {"model": "first"}), ("second", {"model": "second"}),
    ])
    for name in ("_call_api_text", "_call_gguf_text", "_call_codex_text"):
        monkeypatch.setattr(internal_llm, name, call)


@pytest.mark.parametrize("provider", ["hynix_api", "gguf", "codex"])
@pytest.mark.parametrize("first_result", ["error", "empty", "invalid"])
def test_each_actual_fallback_attempt_is_fenced_before_output_validation(monkeypatch, provider, first_result):
    events = []

    def call(cfg, *args, **kwargs):
        assert callable(kwargs["attempt_guard"])  # Python callback, never a wire field.
        events.append(cfg["model"])
        if cfg["model"] == "first":
            if first_result == "error":
                raise TimeoutError("fixture")
            return "" if first_result == "empty" else "invalid"
        return "valid"

    def validate(text):
        events.append("validate:" + text)
        return "retry" if text == "invalid" else ""

    _providers(monkeypatch, call)
    answer, meta = internal_llm.run_internal_text(
        "fixture", {}, provider_order=(provider,), attempt_guard=lambda: events.append("guard"),
        validate_output=validate, max_attempts=2,
    )
    expected = ["guard", "first", "guard"]
    if first_result == "invalid":
        expected.append("validate:invalid")
    assert events == expected + ["guard", "second", "guard", "validate:valid"]
    assert answer == "valid" and meta["attempt_count"] == 2


@pytest.mark.parametrize("provider", ["hynix_api", "gguf", "codex"])
@pytest.mark.parametrize("failure_at", [1, 2, 3])
def test_guard_failure_is_terminal_before_send_after_return_or_before_retry(monkeypatch, provider, failure_at):
    sends = []
    guard_calls = 0
    failure = AuthorityChanged("the accepted scope changed")

    def guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == failure_at:
            raise failure

    def call(cfg, *args, **kwargs):
        sends.append(cfg["model"])
        return "invalid"

    _providers(monkeypatch, call)
    with pytest.raises(AuthorityChanged) as raised:
        internal_llm.run_internal_text(
            "fixture", {}, provider_order=(provider,), attempt_guard=guard,
            validate_output=lambda _: "invalid fixture",
        )
    assert raised.value is failure
    assert sends == ([] if failure_at == 1 else ["first"])
    assert guard_calls == failure_at


@pytest.mark.parametrize("provider_failure", [TimeoutError, KeyboardInterrupt])
def test_post_guard_runs_on_provider_failure_and_abort_without_fallback(monkeypatch, provider_failure):
    events = []
    failure = AuthorityChanged("revoked during provider IO")

    def guard():
        events.append("guard")
        if len(events) > 1:
            raise failure

    def call(*args, **kwargs):
        events.append("call")
        raise provider_failure("fixture")

    _providers(monkeypatch, call)
    with pytest.raises(AuthorityChanged) as raised:
        internal_llm.run_internal_text("fixture", {}, provider_order=("hynix_api", "codex"), attempt_guard=guard)
    assert raised.value is failure
    assert events == ["guard", "call", "guard"]


@pytest.mark.parametrize("invalid", [False, 0, "guard", {}])
def test_invalid_guard_is_rejected_before_discovery(monkeypatch, invalid):
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *a, **kw: pytest.fail("no discovery"))
    with pytest.raises(TypeError):
        internal_llm.run_internal_text("fixture", {}, attempt_guard=invalid)


def test_executor_preserves_original_guard_error_even_if_custom_runner_swallows_it():
    failure = AuthorityChanged("original authority failure")
    checks = []

    def guard():
        checks.append(True)
        if len(checks) == 2:
            raise failure
        if len(checks) > 2:
            raise RuntimeError("reader is now closed")

    def runner(*args, **kwargs):
        try:
            kwargs["attempt_guard"]()
        except AuthorityChanged:
            pass
        return "must not be accepted", {}

    with pytest.raises(AuthorityChanged) as raised:
        CanonicalAgentTurnExecutor(runner=runner).complete(_context(), attempt_guard=guard)
    assert raised.value is failure and len(checks) == 2


@pytest.mark.parametrize("custom_failure", [False, True])
def test_executor_fences_custom_runner_that_ignores_callback(custom_failure):
    changed = False

    def guard():
        if changed:
            raise AuthorityChanged("changed while custom runner was executing")

    def runner(*args, **kwargs):
        nonlocal changed
        changed = True
        if custom_failure:
            raise TimeoutError("provider failed too")
        return "must not be accepted", {}

    with pytest.raises(AuthorityChanged):
        CanonicalAgentTurnExecutor(runner=runner).complete(_context(), attempt_guard=guard)
