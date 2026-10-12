"""Read-only binding of sealed R3 upload intents to current canonical scope.

This is validation-runner support, not an authorization grant or publication API.
Callers supply an authenticated principal (or explicitly label a read-only
inventory principal), own the transaction, and rebind at execution. No model,
file upload, database mutation, credentials, or evaluation answer is involved.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from code_assist_v1.knowledge_core.repositories.publication_authority import (
    HigherPublicationTargetDescriptor,
)
from code_assist_v1.knowledge_core.scope import Capability, ScopeResolver, SessionPrincipal


class MatrixBindingError(ValueError):
    """The sealed intent and current scope cannot be safely combined."""


@dataclass(frozen=True)
class BoundUploadIntent:
    fixture_id: str
    actor_user_id: str
    captured_scope_epoch: int
    organization_code: str
    slot: str
    mode: str
    personal_space_id: str | None
    target_space_ids: tuple[str, ...]
    target_kinds: tuple[str, ...]
    higher_target: HigherPublicationTargetDescriptor | None
    authority: tuple[dict[str, Any], ...]


def bind_upload_intent(connection, resolver: ScopeResolver, principal: SessionPrincipal,
        record: Mapping[str, Any]) -> BoundUploadIntent:
    """Re-resolve every physical target inside the caller's read transaction.

    Never infer the target from a name, switch to the default Wiki, manufacture
    Part membership for higher writers, or accept a readable inherited source
    as a native writable target.
    """
    actor, slot = record.get("writer"), record.get("slot")
    if actor != principal.user_id or slot not in {f"F{i:02d}" for i in range(1, 11)}:
        raise MatrixBindingError("Actor or slot does not match the upload intent.")
    if record.get("fixture_id") != f"R3-{actor}-{slot}":
        raise MatrixBindingError("Fixture identity does not match actor/slot.")
    target = record.get("target")
    if not isinstance(target, dict):
        raise MatrixBindingError("A typed target is required.")
    mode = target.get("mode")
    expected = {
        "personal": ("personal",), "part": ("part",),
        "both": ("personal", "part"),
    }.get(mode)
    if mode == "native":
        if set(target) != {"mode", "level"} or target["level"] not in {"team", "responsibility"}:
            raise MatrixBindingError("Native uploads require an exact higher level.")
        expected = (target["level"],)
    elif mode in {"personal", "both"}:
        if set(target) != {"mode", "wiki_alias"} or target["wiki_alias"] not in {"WA", "WB"}:
            raise MatrixBindingError("Personal uploads require an explicit Wiki alias.")
    elif mode == "part":
        if set(target) != {"mode"}:
            raise MatrixBindingError("Part target shape is invalid.")
    else:
        raise MatrixBindingError("Unknown upload mode.")
    spaces = record.get("planned_target_space_ids")
    if not isinstance(spaces, list) or len(spaces) != len(expected) or len(set(spaces)) != len(spaces):
        raise MatrixBindingError("Target cardinality/identity is invalid.")
    resolved = [resolver.resolve_in_transaction(connection, principal, sid, Capability.PUBLISH)
        for sid in spaces]
    if Counter(row.space.kind for row in resolved) != Counter(expected):
        raise MatrixBindingError("The actual target kinds disagree with the sealed upload mode.")
    authority, higher = [], None
    for row in resolved:
        plan = row.access_plan
        if (plan is None or plan.native_mutation_space_id != row.space.id
                or Capability.PUBLISH not in plan.native_capabilities):
            raise MatrixBindingError("A physical native publication target is required.")
        if row.space.kind in {"team", "responsibility"}:
            if row.current_part_id is not None or plan.role_assignment_id is None or plan.role_assignment_generation is None:
                raise MatrixBindingError("Direct higher writer must have its own role and no Part membership.")
            higher = HigherPublicationTargetDescriptor(kind=row.space.kind, space_id=row.space.id, capability="publish",
                role_assignment_id=plan.role_assignment_id,
                role_assignment_generation=plan.role_assignment_generation)
        authority.append({"space_id": row.space.id, "kind": row.space.kind, "name": row.space.name,
            "org_path": list(row.space.org_path), "actor_row_version": row.actor_row_version,
            "space_row_version": row.space_row_version, "current_part_id": row.current_part_id,
            "role_assignment_id": plan.role_assignment_id,
            "role_assignment_generation": plan.role_assignment_generation,
            "closure": asdict(plan.corpus_closure)})
    return BoundUploadIntent(fixture_id=record["fixture_id"], actor_user_id=actor,
        captured_scope_epoch=principal.captured_scope_epoch, organization_code=record["organization_code"],
        slot=slot, mode=mode, personal_space_id=next((r.space.id for r in resolved if r.space.kind == "personal"), None),
        target_space_ids=tuple(spaces), target_kinds=tuple(r.space.kind for r in resolved),
        higher_target=higher, authority=tuple(authority))


def compile_batches(records: list[Mapping[str, Any]]) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    """Arrange the frozen full workload, rejecting an incomplete pilot subset."""
    if len(records) != 180 or len({r["fixture_id"] for r in records}) != 180:
        raise MatrixBindingError("The full workload requires 180 unique originals.")
    organizations, writers = defaultdict(list), defaultdict(list)
    for row in records:
        organizations[row["organization_code"]].append(row)
        writers[row["writer"]].append(row)
    expected_orgs = {**{f"P{i:02d}": 20 for i in range(1, 8)},
        **{f"T{i:02d}": 10 for i in range(1, 4)}, "R01": 10}
    if {key: len(rows) for key, rows in organizations.items()} != expected_orgs or len(writers) != 18:
        raise MatrixBindingError("The organization/writer workload is incomplete.")
    for actor, rows in writers.items():
        if (len(rows) != 10 or {r["slot"] for r in rows} != {f"F{i:02d}" for i in range(1, 11)}
                or len({r["organization_code"] for r in rows}) != 1
                or any(r["fixture_id"] != f"R3-{actor}-{r['slot']}" for r in rows)):
            raise MatrixBindingError("Every writer requires exactly ten distinct slots in one organization.")
        aliases = {r["target"].get("wiki_alias") for r in rows if "wiki_alias" in r["target"]}
        if aliases != {"WA", "WB"}:
            raise MatrixBindingError("Every writer must exercise both personal Wikis.")
        part_writer = rows[0]["organization_code"].startswith("P")
        alias_spaces = defaultdict(set)
        for row in rows:
            slot = row["slot"]
            format_kind = "docx" if slot in {"F01", "F04", "F05", "F09"} else (
                "pdf" if slot in {"F02", "F06", "F10"} else "pptx")
            if row.get("format") != format_kind:
                raise MatrixBindingError("Original format differs from the approved slot.")
            if part_writer:
                mode = "personal" if slot in {"F01", "F02", "F07"} else (
                    "part" if slot in {"F03", "F04", "F08"} else "both")
            else:
                mode = "native" if slot in {"F03", "F04", "F09", "F10"} else "personal"
            if row["target"].get("mode") != mode:
                raise MatrixBindingError("Publication mode differs from the approved slot.")
            if mode in {"personal", "both"}:
                alias = "WA" if slot in {"F01", "F05", "F07", "F09"} else "WB"
                if row["target"].get("wiki_alias") != alias:
                    raise MatrixBindingError("Personal Wiki alias differs from the approved slot.")
                alias_spaces[alias].add(row["planned_target_space_ids"][0])
        if any(len(values) != 1 for values in alias_spaces.values()) or len(set.union(*alias_spaces.values())) != 2:
            raise MatrixBindingError("WA/WB must name two distinct consistent physical personal Wikis.")
    batches = []
    for org in expected_orgs:
        rows = organizations[org]
        if len({r["writer"] for r in rows}) != (2 if org.startswith("P") else 1):
            raise MatrixBindingError("Unexpected writer count in organization.")
        for start in (1, 6):
            slots = {f"F{i:02d}" for i in range(start, start + 5)}
            batches.append(tuple(sorted((r for r in rows if r["slot"] in slots),
                key=lambda r: (r["writer"], r["slot"]))))
    if len(batches) != 22 or sum(map(len, batches)) != 180:
        raise MatrixBindingError("The complete 22-batch partition is invalid.")
    return tuple(batches)
