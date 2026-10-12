"""Read-only Notion revision boundary for canonical Second Brain publication.

The collaboration database and the Second Brain database remain independent
canonical authorities.  This module owns only the Notion-side half: it captures
one fully checkpointed document revision under current access epochs, renders a
deterministic timestamp-free Markdown body, and can revalidate that exact source
binding immediately before a Second Brain service-layer commit.

It intentionally does not write Second Brain tables or projection files and it
does not claim to close the cross-database commit/revocation protocol.  The
durable publication-intent state machine that applies this binding is a later
packet concern.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from code_assist_v1.knowledge_core.services.publication import (
    AuthorizedNotionPageSnapshot,
)

from .canonical_authority import EpochConflict, NotFoundOrDenied
from .editing_authority import CollaborativeEditingAuthority


BOUNDARY_CONTRACT = "code_assist.notion.second_brain_revision_boundary.v1"
EMPTY_ASSET_MANIFEST_SHA256 = hashlib.sha256(b"[]").hexdigest()


class PublicationSourceConflict(RuntimeError):
    """The selected committed revision is no longer the current live source."""


class PublicationSourceNotCheckpointed(PublicationSourceConflict):
    """Current document events are not represented by a revision checkpoint."""


class PublicationSourceUnsupported(ValueError):
    """The revision uses a source feature not yet safe at this boundary."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prefixed_sha256(value: str) -> str:
    return "sha256:" + _sha256_text(value)


def _escape_table_cell(value: object) -> str:
    if value is None:
        rendered = ""
    elif isinstance(value, str):
        rendered = value
    else:
        rendered = _canonical_json(value)
    return rendered.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _block_markdown(block: Mapping[str, Any]) -> str:
    kind = str(block.get("type") or "paragraph")
    content = str(block.get("content") or "")
    props = block.get("props") if isinstance(block.get("props"), Mapping) else {}
    if kind in {"file", "image", "video", "audio"}:
        raise PublicationSourceUnsupported(
            "attachment-bearing collaborative revisions require the F0 asset boundary"
        )
    if kind == "heading_1":
        return "## " + content
    if kind == "heading_2":
        return "### " + content
    if kind == "heading_3":
        return "#### " + content
    if kind in {"bulleted_list", "bulleted_list_item"}:
        return "- " + content
    if kind in {"numbered_list", "numbered_list_item"}:
        return "1. " + content
    if kind in {"to_do", "todo"}:
        return f"- [{'x' if props.get('checked') is True else ' '}] {content}"
    if kind == "quote":
        return "> " + content.replace("\n", "\n> ")
    if kind == "code":
        language = str(props.get("language") or "").strip()
        return f"```{language}\n{content}\n```"
    if kind == "divider":
        return "---"
    return content


def render_committed_revision_markdown(snapshot: Mapping[str, Any]) -> str:
    """Render the canonical revision without projection/UI timestamps."""

    if not isinstance(snapshot, Mapping):
        raise PublicationSourceConflict("revision snapshot must be an object")
    title = str(snapshot.get("title") or "Untitled").strip() or "Untitled"
    raw_blocks = snapshot.get("blocks")
    if not isinstance(raw_blocks, Sequence) or isinstance(
        raw_blocks, (str, bytes, bytearray)
    ):
        raise PublicationSourceConflict("revision blocks must be a sequence")
    blocks: list[Mapping[str, Any]] = []
    for item in raw_blocks:
        if not isinstance(item, Mapping):
            raise PublicationSourceConflict("revision block must be an object")
        blocks.append(item)
    blocks.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("id") or "")))

    sections = ["# " + title]
    sections.extend(_block_markdown(block) for block in blocks)

    raw_databases = snapshot.get("databases", [])
    if not isinstance(raw_databases, Sequence) or isinstance(
        raw_databases, (str, bytes, bytearray)
    ):
        raise PublicationSourceConflict("revision databases must be a sequence")
    databases = sorted(
        (item for item in raw_databases if isinstance(item, Mapping)),
        key=lambda item: str(item.get("id") or ""),
    )
    if len(databases) != len(raw_databases):
        raise PublicationSourceConflict("revision database must be an object")
    for database in databases:
        sections.append("## " + (str(database.get("title") or "Database").strip() or "Database"))
        raw_properties = database.get("properties", [])
        raw_rows = database.get("rows", [])
        if (
            not isinstance(raw_properties, Sequence)
            or isinstance(raw_properties, (str, bytes, bytearray))
            or not isinstance(raw_rows, Sequence)
            or isinstance(raw_rows, (str, bytes, bytearray))
        ):
            raise PublicationSourceConflict("database snapshot shape is invalid")
        properties = sorted(
            (item for item in raw_properties if isinstance(item, Mapping)),
            key=lambda item: (int(item.get("position") or 0), str(item.get("id") or "")),
        )
        rows = sorted(
            (item for item in raw_rows if isinstance(item, Mapping)),
            key=lambda item: (int(item.get("position") or 0), str(item.get("id") or "")),
        )
        if len(properties) != len(raw_properties) or len(rows) != len(raw_rows):
            raise PublicationSourceConflict("database snapshot row shape is invalid")
        if not properties:
            sections.append("_Empty database schema_" if not rows else "_No properties_")
            continue
        headings = [_escape_table_cell(item.get("name") or "Property") for item in properties]
        sections.append("| " + " | ".join(headings) + " |")
        sections.append("| " + " | ".join("---" for _ in properties) + " |")
        for row in rows:
            cells = row.get("cells") if isinstance(row.get("cells"), Mapping) else {}
            sections.append(
                "| "
                + " | ".join(
                    _escape_table_cell(cells.get(str(item.get("id") or "")))
                    for item in properties
                )
                + " |"
            )
    return "\n\n".join(sections).rstrip() + "\n"


@dataclass(frozen=True, slots=True)
class BoundNotionRevisionSnapshot:
    actor_user_id: str
    owner_user_id: str
    workspace_id: str
    page_id: str
    title: str
    revision: int
    document_sequence: int
    workspace_epoch: int
    document_epoch: int
    history_checkpoint_id: str
    canonical_content_sha256: str
    markdown: str
    content_sha256: str
    asset_manifest_sha256: str
    source_snapshot_sha256: str
    source_fence_sha256: str

    def as_sbe_snapshot(self) -> AuthorizedNotionPageSnapshot:
        """Return the existing protected SBE service API value without mutation."""

        return AuthorizedNotionPageSnapshot(
            actor_user_id=self.actor_user_id,
            page_id=self.page_id,
            owner_user_id=self.owner_user_id,
            title=self.title,
            revision=self.revision,
            updated_at=None,
            markdown=self.markdown,
            content_sha256=self.content_sha256,
            asset_manifest_sha256=self.asset_manifest_sha256,
            source_snapshot_sha256=self.source_snapshot_sha256,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "contract": BOUNDARY_CONTRACT,
            "workspace_id": self.workspace_id,
            "page_id": self.page_id,
            "revision": self.revision,
            "document_sequence": self.document_sequence,
            "workspace_epoch": self.workspace_epoch,
            "document_epoch": self.document_epoch,
            "history_checkpoint_id": self.history_checkpoint_id,
            "canonical_content_sha256": self.canonical_content_sha256,
            "content_sha256": self.content_sha256,
            "asset_manifest_sha256": self.asset_manifest_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "source_fence_sha256": self.source_fence_sha256,
            "timestamp_fields_in_publication_identity": [],
        }


class CollaborativeNotionSnapshotProvider(CollaborativeEditingAuthority):
    """SBE-compatible provider backed by exact 2405 revision checkpoints."""

    @staticmethod
    def _source_hashes(
        *,
        page_id: str,
        owner_user_id: str,
        revision: int,
        markdown: str,
        workspace_id: str,
        document_sequence: int,
        workspace_epoch: int,
        document_epoch: int,
        history_checkpoint_id: str,
        canonical_content_sha256: str,
    ) -> tuple[str, str, str]:
        content_sha256 = _sha256_text(markdown)
        source_identity = {
            "asset_manifest_sha256": EMPTY_ASSET_MANIFEST_SHA256,
            "content_sha256": content_sha256,
            "owner_user_id": owner_user_id,
            "page_id": page_id,
            "revision": revision,
            "schema_version": 1,
            "updated_at": None,
        }
        source_snapshot_sha256 = _sha256_text(_canonical_json(source_identity))
        fence_identity = {
            "canonical_content_sha256": canonical_content_sha256,
            "document_epoch": document_epoch,
            "document_sequence": document_sequence,
            "history_checkpoint_id": history_checkpoint_id,
            "page_id": page_id,
            "revision": revision,
            "schema_version": 1,
            "source_snapshot_sha256": source_snapshot_sha256,
            "workspace_epoch": workspace_epoch,
            "workspace_id": workspace_id,
        }
        return (
            content_sha256,
            source_snapshot_sha256,
            _prefixed_sha256(_canonical_json(fence_identity)),
        )

    def capture_bound(
        self,
        *,
        actor_user_id: str,
        page_id: str,
        expected_revision: int | None = None,
        expected_workspace_epoch: int | None = None,
        expected_document_epoch: int | None = None,
    ) -> BoundNotionRevisionSnapshot:
        actor = str(actor_user_id or "").strip()
        document_id = str(page_id or "").strip()
        if not actor or not document_id:
            raise ValueError("actor_user_id and page_id are required")
        with self._reader() as con:
            con.execute("BEGIN")
            try:
                subject = self._require_active(actor)
                document = self._document(con, document_id)
                workspace = self._workspace(con, str(document["workspace_id"]))
                workspace_epoch = int(workspace["security_epoch"])
                document_epoch = int(document["access_epoch"])
                self._exact_access(
                    con,
                    subject,
                    document_id,
                    minimum_role="edit_content",
                    expected_workspace_epoch=(
                        workspace_epoch
                        if expected_workspace_epoch is None
                        else int(expected_workspace_epoch)
                    ),
                    expected_document_epoch=(
                        document_epoch
                        if expected_document_epoch is None
                        else int(expected_document_epoch)
                    ),
                )
                revision = int(document["revision"])
                sequence = int(document["document_sequence"])
                if expected_revision is not None and int(expected_revision) != revision:
                    raise PublicationSourceConflict("the requested revision is stale")
                checkpoint = con.execute(
                    "SELECT * FROM workspace_collaboration_revision_checkpoints "
                    "WHERE document_id=? AND revision=?",
                    (document_id, revision),
                ).fetchone()
                revision_row = con.execute(
                    "SELECT * FROM workspace_collaboration_revisions "
                    "WHERE document_id=? AND revision=?",
                    (document_id, revision),
                ).fetchone()
                if checkpoint is None or revision_row is None:
                    raise PublicationSourceNotCheckpointed(
                        "the current revision has no immutable checkpoint"
                    )
                if (
                    int(checkpoint["document_sequence"]) != sequence
                    or int(revision_row["document_sequence"]) != sequence
                ):
                    raise PublicationSourceNotCheckpointed(
                        "current document events are not checkpointed"
                    )
                snapshot_json = str(revision_row["snapshot_json"])
                try:
                    snapshot = json.loads(snapshot_json)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise PublicationSourceConflict(
                        "the committed revision snapshot is invalid"
                    ) from exc
                canonical_snapshot_json = _canonical_json(snapshot)
                canonical_hash = _prefixed_sha256(canonical_snapshot_json)
                stored_hashes = {
                    str(document["content_sha256"]),
                    str(checkpoint["content_sha256"]),
                    str(revision_row["content_sha256"]),
                }
                if stored_hashes != {canonical_hash}:
                    raise PublicationSourceConflict(
                        "the committed revision hash chain is inconsistent"
                    )
                markdown = render_committed_revision_markdown(snapshot)
                title = str(snapshot.get("title") or "Untitled").strip() or "Untitled"
                content_hash, source_hash, fence_hash = self._source_hashes(
                    page_id=document_id,
                    owner_user_id=str(document["legacy_owner_subject_id"]),
                    revision=revision,
                    markdown=markdown,
                    workspace_id=str(workspace["id"]),
                    document_sequence=sequence,
                    workspace_epoch=workspace_epoch,
                    document_epoch=document_epoch,
                    history_checkpoint_id=str(checkpoint["id"]),
                    canonical_content_sha256=canonical_hash,
                )
                return BoundNotionRevisionSnapshot(
                    actor_user_id=actor,
                    owner_user_id=str(document["legacy_owner_subject_id"]),
                    workspace_id=str(workspace["id"]),
                    page_id=document_id,
                    title=title,
                    revision=revision,
                    document_sequence=sequence,
                    workspace_epoch=workspace_epoch,
                    document_epoch=document_epoch,
                    history_checkpoint_id=str(checkpoint["id"]),
                    canonical_content_sha256=canonical_hash,
                    markdown=markdown,
                    content_sha256=content_hash,
                    asset_manifest_sha256=EMPTY_ASSET_MANIFEST_SHA256,
                    source_snapshot_sha256=source_hash,
                    source_fence_sha256=fence_hash,
                )
            finally:
                con.rollback()

    def capture(
        self, *, actor_user_id: str, page_id: str
    ) -> AuthorizedNotionPageSnapshot:
        return self.capture_bound(
            actor_user_id=actor_user_id,
            page_id=page_id,
        ).as_sbe_snapshot()

    def revalidate(self, binding: BoundNotionRevisionSnapshot) -> None:
        if not isinstance(binding, BoundNotionRevisionSnapshot):
            raise TypeError("a BoundNotionRevisionSnapshot is required")
        try:
            current = self.capture_bound(
                actor_user_id=binding.actor_user_id,
                page_id=binding.page_id,
                expected_revision=binding.revision,
                expected_workspace_epoch=binding.workspace_epoch,
                expected_document_epoch=binding.document_epoch,
            )
        except (EpochConflict, NotFoundOrDenied) as exc:
            raise PublicationSourceConflict(
                "current source authorization no longer matches the preview"
            ) from exc
        if current != binding:
            raise PublicationSourceConflict(
                "current committed source no longer matches the preview"
            )


def canonical_publication_request_sha256(
    *,
    actor_user_id: str,
    actor_scope_epoch: int,
    operation_id: str,
    source: BoundNotionRevisionSnapshot,
    target_mode: str,
    personal_space_id: str | None = None,
    target_descriptor: Mapping[str, object] | None = None,
) -> str:
    """Hash the caller-owned replay identity before invoking the SBE service."""

    if target_mode not in {"personal", "part", "both", "explicit"}:
        raise ValueError("target_mode is invalid")
    if (target_mode == "explicit") != (target_descriptor is not None):
        raise ValueError("target_descriptor is required exactly for explicit mode")
    payload: dict[str, object] = {
        "actor_scope_epoch": int(actor_scope_epoch),
        "actor_user_id": str(actor_user_id),
        "operation_id": str(operation_id),
        "operation_type": "notion.second_brain.publish",
        "schema_version": 1,
        "source_fence_sha256": source.source_fence_sha256,
        "source_snapshot_sha256": source.source_snapshot_sha256,
        "target_mode": target_mode,
    }
    if personal_space_id is not None:
        payload["personal_space_id"] = str(personal_space_id)
    if target_descriptor is not None:
        payload["target"] = dict(target_descriptor)
    return "sha256:" + _sha256_text(_canonical_json(payload))
