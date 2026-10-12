"""Cross-process worker for the S0A-P2 publication recovery contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from code_assist_v1.notion_collaboration.second_brain_publication import (
    ApprovePublicationIntentCommand,
)
from code_assist_v1.tests.notion_collaboration.second_brain_publication_fixture import (
    build_services,
)


def _payload() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("a JSON object is required")
    return value


def _crash_after_sbe_commit(name: str) -> None:
    if name == "after_sbe_commit_before_notion_record":
        os._exit(23)


def main() -> int:
    value = _payload()
    mode = str(value["mode"])
    failpoint = _crash_after_sbe_commit if mode == "approve_exit" else None
    _provider, _factory, coordinator = build_services(
        Path(str(value["notion_catalog"])),
        Path(str(value["notion_root"])),
        Path(str(value["sbe_database"])),
        failpoint=failpoint,
    )
    if mode == "approve_exit":
        command = value["approval"]
        if not isinstance(command, dict):
            raise ValueError("approval must be an object")
        record = coordinator.approve(
            ApprovePublicationIntentCommand(
                request_id=str(command["request_id"]),
                operation_id=str(command["operation_id"]),
                actor_user_id=str(command["actor_user_id"]),
                actor_scope_epoch=int(command["actor_scope_epoch"]),
                intent_id=str(command["intent_id"]),
                target_mode=str(command["target_mode"]),  # type: ignore[arg-type]
                personal_space_id=(
                    None
                    if command.get("personal_space_id") is None
                    else str(command["personal_space_id"])
                ),
            )
        )
        print(json.dumps(record.to_mapping(), ensure_ascii=False, sort_keys=True))
        return 91
    if mode == "recover":
        record = coordinator.recover(
            actor_user_id=str(value["actor_user_id"]),
            intent_id=str(value["intent_id"]),
        )
        print(json.dumps(record.to_mapping(), ensure_ascii=False, sort_keys=True))
        return 0
    raise ValueError("unsupported worker mode")


if __name__ == "__main__":
    raise SystemExit(main())
