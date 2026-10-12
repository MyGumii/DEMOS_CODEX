"""Bind and independently verify all five P7.8 profile reports."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from code_assist_v1.tools.performance_load import verify_gate_main


if __name__ == "__main__":
    raise SystemExit(verify_gate_main())
