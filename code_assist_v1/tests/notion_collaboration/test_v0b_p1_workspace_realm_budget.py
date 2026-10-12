from __future__ import annotations

import pytest

from code_assist_v1.notion_collaboration.approved_dev_pair import ApprovedDevPairError
from code_assist_v1.notion_collaboration.v0b_p1_iam_browser import (
    V0B_WORKSPACE_REALM_ROTATION_THRESHOLD,
    _require_workspace_realm_budget,
)


class _Page:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def evaluate(self, expression: str) -> object:
        assert expression == "async () => await api('/api/notion/workspaces')"
        return self.payload


def test_v0b_workspace_realm_budget_accepts_bounded_exact_count() -> None:
    items = [{"id": f"ws_{index}"} for index in range(V0B_WORKSPACE_REALM_ROTATION_THRESHOLD)]
    assert _require_workspace_realm_budget(_Page({"items": items, "count": len(items)})) == len(items)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"items": [], "count": True},
        {"items": [], "count": 1},
        {"items": "not-a-list", "count": 0},
        {"items": [], "count": -1},
    ],
)
def test_v0b_workspace_realm_budget_rejects_invalid_payload(payload: object) -> None:
    with pytest.raises(ApprovedDevPairError, match="V0B_WORKSPACE_REALM_COUNT_INVALID"):
        _require_workspace_realm_budget(_Page(payload))


def test_v0b_workspace_realm_budget_requires_rotation_before_more_fixtures() -> None:
    count = V0B_WORKSPACE_REALM_ROTATION_THRESHOLD + 1
    items = [{"id": f"ws_{index}"} for index in range(count)]
    with pytest.raises(
        ApprovedDevPairError,
        match=rf"V0B_WORKSPACE_REALM_ROTATION_REQUIRED:{count}>{V0B_WORKSPACE_REALM_ROTATION_THRESHOLD}",
    ):
        _require_workspace_realm_budget(_Page({"items": items, "count": count}))
