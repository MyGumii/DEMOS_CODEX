"""Audit or strictly gate internal collaborative Notion completion claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_CHECKPOINT,
    DEFAULT_LADDER,
    DEFAULT_MATRIX,
    DEFAULT_P1_CHECKPOINT,
    DEFAULT_RECEIPTS_ROOT,
    verify_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("audit", "gate"), default="audit")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--p1-checkpoint", type=Path, default=DEFAULT_P1_CHECKPOINT)
    parser.add_argument("--receipts-root", type=Path, default=DEFAULT_RECEIPTS_ROOT)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = verify_bundle(
        mode=args.mode,
        catalog_path=args.catalog,
        ladder_path=args.ladder,
        matrix_path=args.matrix,
        checkpoint_path=args.checkpoint,
        p1_checkpoint_path=args.p1_checkpoint,
        receipts_root=args.receipts_root,
    )
    report = dict(report)
    report["contract"] = "code_assist.notion.internal_collaborative_completion.v1"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
