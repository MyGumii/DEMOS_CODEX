"""Immutable conversation-turn binding fences for SBE-2.2.

``conversation.space_id`` remains the only authoritative selected Wiki/view.
This module binds one accepted turn to the server-resolved authorization and
corpus snapshot that was current at acceptance.  It performs no I/O and is
shared by conversation acceptance, Agent context composition, and completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Literal, Mapping

from .repositories.personal_wikis import PersonalWikiProfileRecord
from .scope import AccessPlan


ConversationViewKind = Literal["personal", "part", "team", "responsibility"]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)


class ConversationBindingIntegrityError(ValueError):
    """Raised when a stored or derived binding fence is malformed."""


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ConversationBindingIntegrityError(f"{field} is invalid.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ConversationBindingIntegrityError("actor_user_id is invalid.")
    return value


def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConversationBindingIntegrityError(f"{field} is invalid.")
    return value


def _nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConversationBindingIntegrityError(f"{field} is invalid.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ConversationBindingIntegrityError(f"{field} is invalid.")
    return value


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class ConversationBindingFence:
    actor_user_id: str
    actor_scope_epoch: int
    actor_row_version: int
    selected_view_space_id: str
    selected_view_kind: ConversationViewKind
    corpus_closure_generation: int
    corpus_closure_sha256: str
    organization_snapshot_version: int
    wiki_lifecycle_generation: int
    role_assignment_id: str | None
    role_assignment_generation: int | None
    schema_version: Literal[1] = 1

    def __post_init__(self) -> None:
        _user(self.actor_user_id)
        _positive(self.actor_scope_epoch, "actor_scope_epoch")
        _positive(self.actor_row_version, "actor_row_version")
        _uuid(self.selected_view_space_id, "selected_view_space_id")
        if self.selected_view_kind not in {
            "personal",
            "part",
            "team",
            "responsibility",
        }:
            raise ConversationBindingIntegrityError(
                "selected_view_kind is invalid."
            )
        _positive(
            self.corpus_closure_generation, "corpus_closure_generation"
        )
        _sha(self.corpus_closure_sha256, "corpus_closure_sha256")
        _positive(
            self.organization_snapshot_version,
            "organization_snapshot_version",
        )
        _nonnegative(
            self.wiki_lifecycle_generation, "wiki_lifecycle_generation"
        )
        if self.selected_view_kind == "personal":
            if self.wiki_lifecycle_generation < 1:
                raise ConversationBindingIntegrityError(
                    "A personal binding requires Wiki lifecycle identity."
                )
        elif self.wiki_lifecycle_generation:
            raise ConversationBindingIntegrityError(
                "An organization binding cannot carry personal Wiki identity."
            )
        if (self.role_assignment_id is None) != (
            self.role_assignment_generation is None
        ):
            raise ConversationBindingIntegrityError(
                "The role-assignment fence is incomplete."
            )
        if self.role_assignment_id is not None:
            _uuid(self.role_assignment_id, "role_assignment_id")
            _positive(
                self.role_assignment_generation, "role_assignment_generation"
            )
        if self.schema_version != 1:
            raise ConversationBindingIntegrityError(
                "The conversation binding schema is unsupported."
            )

    @classmethod
    def from_access_plan(
        cls,
        plan: AccessPlan,
        *,
        personal_wiki: PersonalWikiProfileRecord | None,
    ) -> "ConversationBindingFence":
        if not isinstance(plan, AccessPlan):
            raise TypeError("An AccessPlan is required.")
        kind = plan.selected_view.kind
        if kind == "personal":
            if (
                not isinstance(personal_wiki, PersonalWikiProfileRecord)
                or personal_wiki.space_id != plan.selected_view.id
                or personal_wiki.owner_user_id != plan.actor_user_id
                or personal_wiki.lifecycle_status != "active"
            ):
                raise ConversationBindingIntegrityError(
                    "The active personal Wiki binding is unavailable."
                )
            wiki_generation = personal_wiki.lifecycle_generation
        else:
            if personal_wiki is not None:
                raise ConversationBindingIntegrityError(
                    "An organization view cannot carry a personal Wiki profile."
                )
            wiki_generation = 0
        return cls(
            actor_user_id=plan.actor_user_id,
            actor_scope_epoch=plan.scope_epoch,
            actor_row_version=plan.actor_row_version,
            selected_view_space_id=plan.selected_view.id,
            selected_view_kind=kind,
            corpus_closure_generation=plan.corpus_closure.generation,
            corpus_closure_sha256=plan.corpus_closure.sha256,
            organization_snapshot_version=(
                plan.corpus_closure.organization_snapshot_version
            ),
            wiki_lifecycle_generation=wiki_generation,
            role_assignment_id=plan.role_assignment_id,
            role_assignment_generation=plan.role_assignment_generation,
        )

    @classmethod
    def from_mapping(cls, value: object) -> "ConversationBindingFence":
        expected = {
            "actor_row_version",
            "actor_scope_epoch",
            "actor_user_id",
            "corpus_closure_generation",
            "corpus_closure_sha256",
            "organization_snapshot_version",
            "role_assignment_generation",
            "role_assignment_id",
            "schema_version",
            "selected_view_kind",
            "selected_view_space_id",
            "wiki_lifecycle_generation",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ConversationBindingIntegrityError(
                "The stored conversation binding shape is invalid."
            )
        return cls(
            actor_user_id=_user(value.get("actor_user_id")),
            actor_scope_epoch=_positive(
                value.get("actor_scope_epoch"), "actor_scope_epoch"
            ),
            actor_row_version=_positive(
                value.get("actor_row_version"), "actor_row_version"
            ),
            selected_view_space_id=_uuid(
                value.get("selected_view_space_id"), "selected_view_space_id"
            ),
            selected_view_kind=value.get("selected_view_kind"),  # type: ignore[arg-type]
            corpus_closure_generation=_positive(
                value.get("corpus_closure_generation"),
                "corpus_closure_generation",
            ),
            corpus_closure_sha256=_sha(
                value.get("corpus_closure_sha256"),
                "corpus_closure_sha256",
            ),
            organization_snapshot_version=_positive(
                value.get("organization_snapshot_version"),
                "organization_snapshot_version",
            ),
            wiki_lifecycle_generation=_nonnegative(
                value.get("wiki_lifecycle_generation"),
                "wiki_lifecycle_generation",
            ),
            role_assignment_id=(
                None
                if value.get("role_assignment_id") is None
                else _uuid(value.get("role_assignment_id"), "role_assignment_id")
            ),
            role_assignment_generation=(
                None
                if value.get("role_assignment_generation") is None
                else _positive(
                    value.get("role_assignment_generation"),
                    "role_assignment_generation",
                )
            ),
            schema_version=value.get("schema_version"),  # type: ignore[arg-type]
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "actor_row_version": self.actor_row_version,
            "actor_scope_epoch": self.actor_scope_epoch,
            "actor_user_id": self.actor_user_id,
            "corpus_closure_generation": self.corpus_closure_generation,
            "corpus_closure_sha256": self.corpus_closure_sha256,
            "organization_snapshot_version": self.organization_snapshot_version,
            "role_assignment_generation": self.role_assignment_generation,
            "role_assignment_id": self.role_assignment_id,
            "schema_version": self.schema_version,
            "selected_view_kind": self.selected_view_kind,
            "selected_view_space_id": self.selected_view_space_id,
            "wiki_lifecycle_generation": self.wiki_lifecycle_generation,
        }

    @property
    def cache_identity_sha256(self) -> str:
        """Stable authorization/cache prefix for the accepted turn snapshot."""

        return sha256(_canonical(self.as_mapping()).encode("utf-8")).hexdigest()

    def matches(
        self,
        plan: AccessPlan,
        *,
        personal_wiki: PersonalWikiProfileRecord | None,
    ) -> bool:
        try:
            current = type(self).from_access_plan(
                plan, personal_wiki=personal_wiki
            )
        except (ConversationBindingIntegrityError, TypeError, ValueError):
            return False
        return current == self


__all__ = [
    "ConversationBindingFence",
    "ConversationBindingIntegrityError",
    "ConversationViewKind",
]
