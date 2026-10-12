"""Deterministic bootstrap seed definitions for canonical schema version 1."""

from __future__ import annotations


RESPONSIBILITY_ROOT_ID = "fb90d31e-9576-5823-84ef-241b7552f214"
RESPONSIBILITY_ROOT_NAME = "AMHS담당"
RESPONSIBILITY_ROOT_NAME_KEY = "AMHS담당"

SEED_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "org.responsibility_root",
        """
INSERT INTO org_unit(
    id,kind,parent_id,name,name_key,status,created_by,updated_by,row_version
) VALUES (
    'fb90d31e-9576-5823-84ef-241b7552f214',
    'responsibility',
    NULL,
    'AMHS담당',
    'AMHS담당',
    'active',
    NULL,
    NULL,
    1
)
""".strip(),
    ),
)

SEED_NAMES: tuple[str, ...] = tuple(name for name, _sql in SEED_DEFINITIONS)
SEED_STATEMENTS: tuple[str, ...] = tuple(sql for _name, sql in SEED_DEFINITIONS)


__all__ = [
    "RESPONSIBILITY_ROOT_ID",
    "RESPONSIBILITY_ROOT_NAME",
    "RESPONSIBILITY_ROOT_NAME_KEY",
    "SEED_DEFINITIONS",
    "SEED_NAMES",
    "SEED_STATEMENTS",
]
