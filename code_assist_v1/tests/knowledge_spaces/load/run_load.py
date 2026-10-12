"""P7.8 target load entry point; see tools.performance_load for the contract."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from code_assist_v1.tools.performance_load import main


if __name__ == "__main__":
    raise SystemExit(main())
