from __future__ import annotations

from code_assist_v1.tools.full_second_brain_preview import _preview_feature_flags
from code_assist_v1.tools.full_second_brain_preview_workers import (
    preview_semantic_provider_policy,
)


def test_sbeops_012_preview_codex_is_an_explicit_validation_lane() -> None:
    policy = preview_semantic_provider_policy(codex_validation=True)

    assert policy.mode == "codex-validation"
    assert policy.provider_order == ("codex",)
    assert policy.model_ids == ("codex-local",)
    assert policy.model_provider == "codex_bridge_validation"


def test_sbeops_012_preview_primary_contract_is_openai_compatible_only() -> None:
    policy = preview_semantic_provider_policy(codex_validation=False)

    assert policy.mode == "openai-compatible"
    assert policy.provider_order == ("hynix_api",)
    assert policy.model_ids is None
    assert "gguf" not in policy.provider_order
    assert "codex" not in policy.provider_order


def test_sbeops_014_preview_projection_and_temporal_workers_are_enabled() -> None:
    flags = _preview_feature_flags()

    assert flags.projections is True
    assert flags.projection_footer_v2 is True
    assert flags.aggregate_graph_reads is True
    assert flags.auto_semantic_edges_local is True
    assert flags.auto_semantic_edges_aggregate is True
