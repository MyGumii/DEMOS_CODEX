"""Dependency-only P6.7 facade for the disabled canonical curation routes.

The facade adds no authority and owns no transaction.  Each underlying service
continues to reauthorize, validate fixed inputs, and own its read or mutation
transaction.  Keeping this bundle behind the existing ``curation`` service slot
lets route/UI work remain disabled until the host explicitly binds it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .curation_metrics import CurationShadowMetricsRequest
from .findings import ReadFindingRequest, ReviewFindingCommand
from .merges import MergeApplyCommand, MergePreviewCommand
from .proposals import (
    ProposalApplyCommand,
    ProposalPreviewCommand,
    ProposalRebaseCommand,
)


def _method(value: object, name: str, method: str) -> None:
    if not callable(getattr(value, method, None)):
        raise TypeError(f"{name} must expose {method}().")


@dataclass(frozen=True, slots=True)
class CurationReviewFacade:
    findings: object
    proposals: object
    merges: object
    metrics: object

    def __post_init__(self) -> None:
        _method(self.findings, "findings", "read")
        _method(self.findings, "findings", "review")
        _method(self.proposals, "proposals", "preview")
        _method(self.proposals, "proposals", "rebase")
        _method(self.proposals, "proposals", "apply")
        _method(self.merges, "merges", "preview")
        _method(self.merges, "merges", "apply")
        _method(self.metrics, "metrics", "read")

    def get_finding(self, request: ReadFindingRequest):
        return self.findings.read(request)

    def review_finding(self, command: ReviewFindingCommand):
        return self.findings.review(command)

    def preview_proposal(self, command: ProposalPreviewCommand):
        return self.proposals.preview(command)

    def rebase_proposal(self, command: ProposalRebaseCommand):
        return self.proposals.rebase(command)

    def apply_proposal(self, command: ProposalApplyCommand):
        return self.proposals.apply(command)

    def preview_merge(self, command: MergePreviewCommand):
        return self.merges.preview(command)

    def apply_merge(self, command: MergeApplyCommand):
        return self.merges.apply(command)

    def read_shadow_metrics(self, request: CurationShadowMetricsRequest):
        return self.metrics.read(request)


__all__ = ["CurationReviewFacade"]
