"""Canonical answer API-completion policy, independent of routes/providers.

These are generous byte limits on a returned HTTP entity and final text, not
input-document or model-token limits. The existing 3072 output-token request is
unchanged. A partial result is never silently relabeled as a complete answer.
"""
from code_assist_v1.llm_response import ChatResponsePolicy


ANSWER_API_RESPONSE_POLICY = ChatResponsePolicy(envelope_bytes=1_048_576, text_bytes=262_144)
ANSWER_API_FAILURE_CODES = frozenset({
    "MODEL_RESPONSE_HTTP_STATUS", "MODEL_RESPONSE_ENCODING_UNSUPPORTED",
    "MODEL_RESPONSE_TOO_LARGE", "MODEL_RESPONSE_INVALID", "MODEL_RESPONSE_INCOMPLETE",
    "MODEL_EMPTY_OUTPUT", "MODEL_EXEC_TIMEOUT", "MODEL_EXEC_FAILED",
    "MODEL_API_AUTH_FAILED", "MODEL_API_INPUT_TOO_LARGE",
    "MODEL_API_RATE_LIMITED", "MODEL_API_SERVER_ERROR",
})
# This describes a later explicit user attempt. It does not schedule retries or
# alter fallback order; replaying an existing operation remains idempotent.
ANSWER_API_RETRYABLE_CODES = frozenset({
    "MODEL_EXEC_TIMEOUT", "MODEL_EXEC_FAILED", "MODEL_EMPTY_OUTPUT",
    "MODEL_API_RATE_LIMITED", "MODEL_API_SERVER_ERROR",
})
