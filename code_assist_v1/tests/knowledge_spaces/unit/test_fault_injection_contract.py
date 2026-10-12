from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[3]


def _required_failpoints() -> tuple[str, ...]:
    contract = (
        _ROOT / "docs" / "knowledge_spaces" / "TEST_OPERATIONS.md"
    ).read_text(encoding="utf-8")
    marker = "Required failpoints:\n\n```text\n"
    block = contract.split(marker, 1)[1].split("\n```", 1)[0]
    return tuple(line.strip() for line in block.splitlines() if line.strip())


def test_required_failpoint_vocabulary_is_implemented_and_exercised() -> None:
    required = _required_failpoints()
    assert len(required) == 54
    assert len(set(required)) == len(required)

    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((_ROOT / "knowledge_core").rglob("*.py"))
    )
    tests = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((_ROOT / "tests" / "knowledge_spaces").rglob("*.py"))
        if path.resolve() != Path(__file__).resolve()
    )
    assert [name for name in required if name not in production] == []
    assert [name for name in required if name not in tests] == []
