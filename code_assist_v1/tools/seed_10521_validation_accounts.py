"""Seed four isolated 10521 validation accounts and the preview hierarchy once.

Passwords are printed only on successful creation, never persisted as plaintext.
This is not an operator/user import and cannot target another data root.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import time

from werkzeug.security import generate_password_hash

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import (
    SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG as CATALOG,
)
from code_assist_v1.tools.clean_10521_runtime import DATA_ROOT, is_clean_root, selection_for
from code_assist_v1.tools.full_second_brain_preview import seed_canonical_administration


ACCOUNTS = (
    ("admin", "10521 Validation Admin", "admin"),
    ("user01", "10521 Part B Leader", "user"),
    ("preview-team-leader", "10521 Team Leader", "user"),
    ("preview-responsibility-executive", "10521 Responsibility Executive", "user"),
)


def _factory(root: Path) -> ConnectionFactory:
    factory = ConnectionFactory(root / "core" / "knowledge_core.db", CATALOG)
    probe = factory.open(read_only=True)
    probe.close()
    return factory


def _counts(factory: ConnectionFactory) -> dict[str, int]:
    connection = factory.open(read_only=True)
    try:
        return {
            name: int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            for name in ("app_user", "org_unit", "knowledge_space", "org_membership")
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = DATA_ROOT.resolve(strict=True)
    if not is_clean_root(root) or selection_for(root) is None:
        raise ValueError("The isolated 10521 runtime is not activated.")
    factory = _factory(root)
    registry = root / ".runtime" / "users.json"
    secret = root / ".runtime" / "secret.key"
    if args.check:
        counts = _counts(factory)
        if not registry.is_file() or not secret.is_file() or counts["app_user"] != len(ACCOUNTS):
            raise ValueError("The 10521 validation account seed is incomplete.")
        print(json.dumps({"status": "verified", "data_root": str(root), "counts": counts}, sort_keys=True))
        return 0
    if registry.exists() or secret.exists() or _counts(factory)["app_user"]:
        raise ValueError("The 10521 account target is not empty; refuse to replace credentials.")
    now = int(time.time())
    passwords = {user_id: secrets.token_urlsafe(21) for user_id, _, _ in ACCOUNTS}
    users = {
        user_id: {
            "id": user_id,
            "display_name": display_name,
            "role": role,
            "disabled": False,
            "active": True,
            "password_hash": generate_password_hash(passwords[user_id]),
            "must_change_password": False,
            "password_changed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        for user_id, display_name, role in ACCOUNTS
    }
    with secret.open("x", encoding="utf-8", newline="\n") as output:
        output.write(secrets.token_hex(32) + "\n")
    with registry.open("x", encoding="utf-8", newline="\n") as output:
        json.dump({"users": users}, output, ensure_ascii=False, sort_keys=True)
        output.write("\n")
    # Make the one-time credentials visible even if the transactional service
    # seed reports a recoverable issue after the authentication files exist.
    print(json.dumps({"one_time_validation_credentials": passwords}, sort_keys=True), flush=True)
    seed_canonical_administration(factory, root)
    counts = _counts(factory)
    if counts["app_user"] != len(ACCOUNTS):
        raise ValueError("The isolated canonical account seed is incomplete.")
    print(json.dumps({"status": "seeded", "data_root": str(root), "counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
