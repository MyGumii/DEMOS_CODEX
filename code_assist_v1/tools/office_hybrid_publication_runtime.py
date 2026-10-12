"""Strict Office page-visual model adapter for an explicitly selected runtime.

No Office file is opened here. The upload route supplies a separately approved
renderer; this adapter only prevents a retained Office visual task from
silently switching provider, model, image policy or response contract.
"""

from __future__ import annotations

from code_assist_v1.codex_response import CodexResponsePolicy
from code_assist_v1.llm_response import ChatResponsePolicy
from code_assist_v1.knowledge_core.office_hybrid_visual_authoring import (
    PROMPT, TASK, VERSION, OfficeHybridVisualAuthor,
)
from code_assist_v1.knowledge_core.services.office_hybrid_preview import OfficeHybridUploadPolicy
from code_assist_v1.tools.pdf_visual_publication_runtime import (
    PdfVisualRuntimeSelection, _config_digest, resolved_visual_config,
)


def office_visual_author_for(selection, *, policy: OfficeHybridUploadPolicy,
        execute_models: bool) -> OfficeHybridVisualAuthor:
    if (type(selection) is not PdfVisualRuntimeSelection
            or type(policy) is not OfficeHybridUploadPolicy
            or type(execute_models) is not bool
            or policy.visual_model != selection.model_policy()):
        raise ValueError("Office visual model requires one exact selected policy.")
    config_digest = _config_digest(resolved_visual_config(selection))

    def runner(task, payload, **kwargs):
        from code_assist_v1 import internal_llm
        if not execute_models:
            raise RuntimeError("Office visual execution is reserved for the durable worker.")
        budget = kwargs.get("request_budget_bytes")
        messages = kwargs.get("messages")
        if (task != TASK or type(payload) is not dict
                or payload.get("policy_version") != VERSION
                or type(messages) is not list or not messages
                or messages[0] != {"role": "system", "content": PROMPT}
                or kwargs.get("provider_order") != [selection.provider]
                or kwargs.get("model_ids") != [selection.model_id]
                or kwargs.get("max_attempts") != 1
                or type(budget) is not int
                or not 0 < budget <= policy.visual_model.request_budget_bytes
                or kwargs.get("image_policy") != policy.render.image_policy(1)
                or type(kwargs.get("image_inputs")) is not tuple
                or len(kwargs["image_inputs"]) != 1
                or kwargs.get("preserve_codex_input") is not True
                or kwargs.get("capture_request_receipt") is not True
                or type(kwargs.get("image_attempt_receipt")) is not dict
                or kwargs.get("output_format") != "json"):
            raise ValueError("Office visual request differs from its frozen image/model contract.")
        response_key = ("codex_response_policy" if selection.provider == "codex"
            else "response_policy")
        expected_response = (CodexResponsePolicy(budget, 4_194_304,
            1_048_576, 524_288, 4096, 65_536)
            if selection.provider == "codex"
            else ChatResponsePolicy(1_048_576, 65_536))
        if (kwargs.get(response_key) != expected_response
                or any(key in kwargs for key in {
                    "codex_response_policy", "response_policy", "api_response_policy"
                    } - {response_key})):
            raise ValueError("Office visual response policy differs from its frozen plan.")
        guard = kwargs.get("attempt_guard")
        if not callable(guard):
            raise ValueError("Office visual model requires a current invocation guard.")

        def guarded():
            guard()
            if _config_digest(resolved_visual_config(selection)) != config_digest:
                raise ValueError("Office visual model configuration changed during execution.")

        guarded()
        kwargs["attempt_guard"] = guarded
        return internal_llm.run_internal_text(task, payload, **kwargs)

    return OfficeHybridVisualAuthor(runner=runner)


__all__ = ["office_visual_author_for"]
