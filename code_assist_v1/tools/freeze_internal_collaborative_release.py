"""Create or verify an immutable Internal Collaborative Notion release manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from code_assist_v1.notion_collaboration.realm_promotion import DEFAULT_PROMOTION_ROOT
from code_assist_v1.notion_collaboration.release_candidate import (
    REPOSITORY_ROOT,
    build_release_candidate_manifest,
    sha256_file,
    verify_release_candidate_manifest,
    write_release_candidate_manifest_create_only,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("freeze", "verify"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--deployment-generation-id")
    parser.add_argument("--compact", action="store_true")
    return parser


def _failure(code: str, message: str) -> dict:
    return {
        "report_schema_version": 1,
        "contract": "code_assist.notion.internal_collaborative_release_freezer.v1",
        "status": "fail",
        "error_count": 1,
        "errors": [{"code": code, "subject": "manifest", "message": message}],
        "release_frozen": False,
        "artifact_archive_created": False,
        "service_started": False,
        "production_or_runtime_root_accessed": False,
        "deployment_authorized": False,
        "canary_authorized": False,
        "product_complete": False,
    }


def _safe_manifest_path(relative: Path) -> Path:
    if relative.is_absolute():
        raise ValueError("manifest path must be repository-relative")
    target = (REPOSITORY_ROOT / relative).resolve(strict=False)
    allowed = DEFAULT_PROMOTION_ROOT.resolve(strict=False)
    if not target.is_relative_to(allowed) or target.suffix.casefold() != ".json":
        raise ValueError("manifest must be a JSON file inside the promotion evidence root")
    return target


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        target = _safe_manifest_path(args.manifest)
    except ValueError as exc:
        report, exit_code = _failure("RELEASE_MANIFEST_PATH_INVALID", str(exc)), 2
    else:
        if args.mode == "freeze":
            if not args.deployment_generation_id:
                report, exit_code = _failure(
                    "DEPLOYMENT_GENERATION_REQUIRED",
                    "freeze mode requires --deployment-generation-id",
                ), 2
            else:
                try:
                    manifest = build_release_candidate_manifest(
                        deployment_generation_id=args.deployment_generation_id
                    )
                    write_release_candidate_manifest_create_only(target, manifest)
                    verified, verify_exit = verify_release_candidate_manifest(target)
                    if verify_exit != 0:
                        report, exit_code = verified, verify_exit
                    else:
                        report = {
                            **verified,
                            "contract": "code_assist.notion.internal_collaborative_release_freezer.v1",
                            "release_frozen": True,
                            "create_only": True,
                            "manifest_path": target.relative_to(REPOSITORY_ROOT).as_posix(),
                            "manifest_sha256": sha256_file(target),
                        }
                        exit_code = 0
                except FileExistsError:
                    report, exit_code = _failure(
                        "RELEASE_MANIFEST_ALREADY_EXISTS",
                        "release manifests are create-only",
                    ), 1
                except (OSError, TypeError, ValueError) as exc:
                    report, exit_code = _failure(
                        "RELEASE_FREEZE_FAILED",
                        str(exc),
                    ), 1
        elif args.deployment_generation_id is not None:
            report, exit_code = _failure(
                "VERIFY_GENERATION_ARGUMENT_FORBIDDEN",
                "verify mode reads the frozen deployment generation from the manifest",
            ), 2
        elif not target.is_file():
            report, exit_code = _failure(
                "RELEASE_MANIFEST_MISSING",
                "the selected release manifest does not exist",
            ), 1
        else:
            report, exit_code = verify_release_candidate_manifest(target)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
        )
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
