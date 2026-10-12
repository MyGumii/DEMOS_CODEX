"""Explicit server-owned answer request limits, separate from source/token budgets.

No default numbers or provider discovery: deployment owns both values. The byte
limit applies to complete OpenAI-compatible HTTP bodies, not Codex/GGUF input.
The attempt limit counts runner candidates across all selected provider lanes,
including a candidate rejected before transmission, not only HTTP requests.
"""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AnswerRequestPolicy:
    request_budget_bytes: int
    max_attempts: int

    def __post_init__(self) -> None:
        for value in (self.request_budget_bytes, self.max_attempts):
            if type(value) is not int or value <= 0:
                raise ValueError("Answer request limits must be explicit positive integers.")

    def runner_options(self, *, single_attempt: bool = False) -> dict[str, int]:
        if type(single_attempt) is not bool:
            raise TypeError("single_attempt must be an explicit bool.")
        return {"request_budget_bytes": self.request_budget_bytes,
                "max_attempts": 1 if single_attempt else self.max_attempts}

    def receipt(self, *, single_attempt: bool = False) -> dict[str, object]:
        return {"schema_version": 1, **self.runner_options(single_attempt=single_attempt),
                "byte_scope": "openai_compatible_http_body",
                "attempt_scope": "runner_candidates_all_selected_providers",
                "model_context_tokens": None}
