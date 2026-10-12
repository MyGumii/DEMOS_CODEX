"""End-to-end bounded P2.3, P2.4, and session/job P2.5 orchestration.

The service validates the exact reviewed artifact, re-establishes every source
root/high-water boundary, holds source handles across each canonical commit,
    and composes the identity, personal-content, graph/finding, labelled
    historical-data, conversation, and never-replayed legacy-job importers in
    deterministic resume-safe batches.  It never migrates schema or interprets
    later P2 phases.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import ntpath
from pathlib import Path

from ..db import ConnectionFactory
from ..errors import KnowledgeCoreError
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from .blob_stager import FilesystemLegacyBlobStager
from .content_importer import (
    LegacyContentImportResult,
    LegacyPersonalContentImporter,
    MAX_CONTENT_EFFECTS_PER_TRANSACTION,
    MAX_CONTENT_SOURCE_BYTES,
)
from .importer import (
    LegacyIdentityImportResult,
    LegacyIdentityImporter,
    USERS_REGISTRY_MEDIA_TYPE,
    _reviewed_plan,
)
from .p24_importer import LegacyP24ImportResult, LegacyP24Importer
from .p24_history_importer import (
    LegacyP24HistoryImportResult,
    LegacyP24HistoryImporter,
)
from .p25_importer import (
    LegacyP25ImportResult,
    LegacyP25Importer,
    MAX_P25_EFFECTS_PER_TRANSACTION,
    MAX_P25_SOURCE_BYTES,
)
from .p25_notion_importer import (
    LegacyP25NotionImportResult,
    LegacyP25NotionImporter,
)
from .source_reader import BoundSourceObservation, ReviewedLegacySourceReader


class LegacyMigrationApplyError(KnowledgeCoreError):
    code = "LEGACY_MIGRATION_APPLY_FAILED"


class LegacyMigrationApplyPlanError(LegacyMigrationApplyError):
    code = "LEGACY_MIGRATION_APPLY_PLAN_INVALID"


class LegacyMigrationApplyLayoutError(LegacyMigrationApplyError):
    code = "LEGACY_MIGRATION_APPLY_LAYOUT_INVALID"


@dataclass(frozen=True, slots=True)
class LegacyMigrationApplyResult:
    plan_sha256: str
    identity: LegacyIdentityImportResult
    content_batches: tuple[LegacyContentImportResult, ...]
    p24_batches: tuple[LegacyP24ImportResult, ...] = ()
    p24_history_batches: tuple[LegacyP24HistoryImportResult, ...] = ()
    p25_batches: tuple[LegacyP25ImportResult, ...] = ()
    p25_notion_batches: tuple[LegacyP25NotionImportResult, ...] = ()

    @property
    def imported_record_count(self) -> int:
        return sum(len(batch.records) for batch in self.content_batches) + sum(
            len(batch.records) for batch in self.p24_batches
        ) + sum(len(batch.records) for batch in self.p24_history_batches) + sum(
            len(batch.records) for batch in self.p25_batches
        ) + sum(
            len(batch.records) for batch in self.p25_notion_batches
        )

    @property
    def imported_relationship_count(self) -> int:
        return sum(len(batch.relationships) for batch in self.content_batches) + sum(
            len(batch.relationships) for batch in self.p24_batches
        ) + sum(
            len(batch.relationships) for batch in self.p25_notion_batches
        )

    @property
    def inserted_mapping_count(self) -> int:
        return self.identity.inserted_mapping_count + sum(
            batch.inserted_mapping_count for batch in self.content_batches
        ) + sum(batch.inserted_mapping_count for batch in self.p24_batches) + sum(
            batch.inserted_mapping_count for batch in self.p24_history_batches
        ) + sum(
            batch.inserted_mapping_count for batch in self.p25_batches
        ) + sum(
            batch.inserted_mapping_count for batch in self.p25_notion_batches
        )


def _norm(value: str | Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))


def _overlap(left: str, right: str) -> bool:
    left = _norm(left)
    right = _norm(right)
    try:
        common = ntpath.commonpath((left, right))
    except ValueError:
        return False
    return common in {left, right}


def _assert_storage_outside_sources(
    draft: Mapping[str, object], boundary: MigrationStorageBoundary
) -> None:
    snapshot = draft.get("source_snapshot")
    roots = snapshot.get("roots") if isinstance(snapshot, Mapping) else None
    if not isinstance(roots, list):
        raise LegacyMigrationApplyPlanError("Reviewed source roots are invalid.")
    storage_paths = (
        boundary.data_root.resolved_path,
        boundary.database_parent.resolved_path,
        boundary.database.resolved_path,
        boundary.blob_root.resolved_path,
        boundary.projection_root.resolved_path,
    )
    for row in roots:
        source = row.get("resolved_path") if isinstance(row, Mapping) else None
        if not isinstance(source, str):
            raise LegacyMigrationApplyPlanError("Reviewed source roots are invalid.")
        if any(_overlap(source, target) for target in storage_paths):
            raise LegacyMigrationApplyLayoutError(
                "Canonical migration storage overlaps a reviewed source root."
            )


def _plan_rows(
    draft: Mapping[str, object], field: str
) -> tuple[Mapping[str, object], ...]:
    value = draft.get(field)
    if not isinstance(value, list) or not all(isinstance(row, Mapping) for row in value):
        raise LegacyMigrationApplyPlanError(f"Reviewed {field} are invalid.")
    return tuple(value)  # type: ignore[return-value]


def _source(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("source_record")
    if not isinstance(value, Mapping):
        raise LegacyMigrationApplyPlanError("Reviewed source record is invalid.")
    return value


def _source_size(row: Mapping[str, object]) -> int:
    value = _source(row).get("size_bytes")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LegacyMigrationApplyPlanError("Reviewed source size is invalid.")
    return value


def _chunk_records(
    rows: Sequence[Mapping[str, object]], *, batch_size: int
) -> tuple[tuple[str, ...], ...]:
    batches: list[tuple[str, ...]] = []
    current: list[str] = []
    current_bytes = 0
    for row in sorted(rows, key=lambda value: str(value.get("record_id"))):
        record_id = row.get("record_id")
        size = _source_size(row)
        if not isinstance(record_id, str) or size > MAX_CONTENT_SOURCE_BYTES:
            raise LegacyMigrationApplyPlanError(
                "A reviewed content record exceeds the bounded source limit."
            )
        if current and (
            len(current) >= batch_size
            or current_bytes + size > MAX_CONTENT_SOURCE_BYTES
        ):
            batches.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(record_id)
        current_bytes += size
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _relationship_endpoints(row: Mapping[str, object]) -> tuple[str, ...]:
    source = row.get("source_relationship")
    if not isinstance(source, Mapping):
        raise LegacyMigrationApplyPlanError(
            "Reviewed relationship source evidence is invalid."
        )
    endpoints = tuple(
        sorted(
            {
                str(source[key])
                for key in ("from_record_id", "to_record_id")
                if isinstance(source.get(key), str)
            }
        )
    )
    if len(endpoints) != 2:
        raise LegacyMigrationApplyPlanError(
            "A P2.3 asset relationship requires two physical endpoints."
        )
    return endpoints


def _chunk_relationships(
    rows: Sequence[Mapping[str, object]],
    records_by_id: Mapping[str, Mapping[str, object]],
    *,
    batch_size: int,
) -> tuple[tuple[str, ...], ...]:
    batches: list[tuple[str, ...]] = []
    current_ids: list[str] = []
    current_endpoints: set[str] = set()
    for row in sorted(rows, key=lambda value: str(value.get("relationship_id"))):
        relationship_id = row.get("relationship_id")
        if not isinstance(relationship_id, str):
            raise LegacyMigrationApplyPlanError(
                "Reviewed relationship identity is invalid."
            )
        endpoints = set(_relationship_endpoints(row))
        if not endpoints.issubset(records_by_id):
            raise LegacyMigrationApplyPlanError(
                "Reviewed relationship endpoints are absent from the plan."
            )
        candidate = current_endpoints | endpoints
        source_bytes = sum(_source_size(records_by_id[value]) for value in candidate)
        if source_bytes > MAX_CONTENT_SOURCE_BYTES:
            if current_ids:
                batches.append(tuple(current_ids))
                current_ids = []
                current_endpoints = set()
                candidate = endpoints
                source_bytes = sum(
                    _source_size(records_by_id[value]) for value in candidate
                )
            if source_bytes > MAX_CONTENT_SOURCE_BYTES:
                raise LegacyMigrationApplyPlanError(
                    "A reviewed relationship exceeds the bounded source limit."
                )
        if current_ids and len(current_ids) >= batch_size:
            batches.append(tuple(current_ids))
            current_ids = []
            current_endpoints = set()
        current_ids.append(relationship_id)
        current_endpoints |= endpoints
    if current_ids:
        batches.append(tuple(current_ids))
    return tuple(batches)


def _close_observations(
    observations: Iterable[BoundSourceObservation],
) -> None:
    first_error: OSError | None = None
    for observation in observations:
        try:
            observation.close()
        except OSError as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error


class LegacyMigrationApplyService:
    """Apply reviewed P2.3/P2.4 and supported P2.5 effects in dependency order."""

    __slots__ = (
        "_boundary",
        "_content",
        "_factory",
        "_failpoint",
        "_identity",
        "_layout",
        "_p24",
        "_p24_history",
        "_p25",
        "_p25_notion",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        storage_layout: MigrationStorageLayout,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyMigrationApplyService requires a ConnectionFactory.")
        if not isinstance(storage_layout, MigrationStorageLayout):
            raise TypeError("storage_layout must be MigrationStorageLayout.")
        boundary = verify_migration_storage_layout(
            storage_layout, require_database=True
        )
        if _norm(factory.database_path) != _norm(boundary.database.resolved_path):
            raise LegacyMigrationApplyLayoutError(
                "ConnectionFactory and migration storage bind different databases."
            )
        stager = FilesystemLegacyBlobStager(storage_layout)
        self._factory = factory
        self._failpoint = failpoint
        self._layout = storage_layout
        self._boundary = boundary
        self._identity = LegacyIdentityImporter(factory, failpoint=failpoint)
        self._content = LegacyPersonalContentImporter(
            factory, stager, failpoint=failpoint
        )
        self._p24 = LegacyP24Importer(factory, failpoint=failpoint)
        self._p24_history = LegacyP24HistoryImporter(
            factory, stager, failpoint=failpoint
        )
        self._p25 = LegacyP25Importer(factory, stager, failpoint=failpoint)
        self._p25_notion = LegacyP25NotionImporter(
            factory,
            Path(boundary.data_root.resolved_path),
            failpoint=failpoint,
        )

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def apply(
        self,
        reviewed_plan_bytes: bytes,
        *,
        expected_plan_sha256: str,
        batch_size: int = MAX_CONTENT_EFFECTS_PER_TRANSACTION,
    ) -> LegacyMigrationApplyResult:
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= MAX_CONTENT_EFFECTS_PER_TRANSACTION
        ):
            raise LegacyMigrationApplyPlanError(
                "batch_size must be an integer from 1 through 100."
            )
        if not isinstance(reviewed_plan_bytes, bytes):
            raise TypeError("reviewed_plan_bytes must be canonical artifact bytes.")
        plan = _reviewed_plan(
            reviewed_plan_bytes, expected_plan_sha256=expected_plan_sha256
        )
        draft = plan.get("draft")
        if not isinstance(draft, Mapping):
            raise LegacyMigrationApplyPlanError("Reviewed plan draft is invalid.")
        current_boundary = verify_migration_storage_layout(
            self._layout, require_database=True, expected=self._boundary
        )
        _assert_storage_outside_sources(draft, current_boundary)
        records = _plan_rows(draft, "records")
        relationships = _plan_rows(draft, "relationships")
        imported_p23 = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.3"
        )
        allowed_record_modes = {
            "source_container",
            "user_account",
            "personal_asset",
            "personal_item",
        }
        if any(row.get("import_mode") not in allowed_record_modes for row in imported_p23):
            raise LegacyMigrationApplyPlanError(
                "Reviewed P2.3 records contain an unsupported import mode."
            )
        containers = tuple(
            row
            for row in imported_p23
            if row.get("import_mode") == "source_container"
            and _source(row).get("category") == "identity.users_registry"
            and _source(row).get("record_kind") == "physical_file"
        )
        if len(containers) != 1:
            raise LegacyMigrationApplyPlanError(
                "P2.3 requires exactly one imported users-registry container."
            )
        p23_relationships = tuple(
            row
            for row in relationships
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.3"
        )
        if any(
            row.get("import_mode") != "personal_asset"
            or not isinstance(row.get("source_relationship"), Mapping)
            or row["source_relationship"].get("relationship_type")
            != "asset_reference"
            for row in p23_relationships
        ):
            raise LegacyMigrationApplyPlanError(
                "Reviewed P2.3 relationships contain an unsupported import mode."
            )

        source_reader = ReviewedLegacySourceReader(
            reviewed_plan_bytes, expected_plan_sha256=expected_plan_sha256
        )
        container_id = str(containers[0]["record_id"])
        source_reader.verify_snapshot_high_water()
        registry = source_reader.read_record(container_id)
        try:
            identity_result = self._identity.apply(
                reviewed_plan_bytes,
                registry.content,
                expected_plan_sha256=expected_plan_sha256,
                verified_blob_evidence=registry.verified_blob_evidence(
                    media_type=USERS_REGISTRY_MEDIA_TYPE
                ),
            )
            self._hit("migration.after_batch_commit")
        finally:
            registry.close()

        records_by_id = {str(row["record_id"]): row for row in records}
        content_results: list[LegacyContentImportResult] = []
        for mode in ("personal_asset", "personal_item"):
            selected = tuple(
                row for row in imported_p23 if row.get("import_mode") == mode
            )
            for record_ids in _chunk_records(selected, batch_size=batch_size):
                verify_migration_storage_layout(
                    self._layout, require_database=True, expected=self._boundary
                )
                source_reader.verify_snapshot_high_water()
                observations = source_reader.read_records(record_ids)
                try:
                    content_results.append(
                        self._content.apply_batch(
                            reviewed_plan_bytes,
                            {
                                record_id: observation.content
                                for record_id, observation in observations.items()
                            },
                            selected_record_ids=record_ids,
                            expected_plan_sha256=expected_plan_sha256,
                        )
                    )
                    self._hit("migration.after_batch_commit")
                finally:
                    _close_observations(observations.values())

        relationship_by_id = {
            str(row["relationship_id"]): row for row in p23_relationships
        }
        for relationship_ids in _chunk_relationships(
            p23_relationships, records_by_id, batch_size=batch_size
        ):
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            source_reader.verify_snapshot_high_water()
            dependency_ids = tuple(
                sorted(
                    {
                        endpoint
                        for relationship_id in relationship_ids
                        for endpoint in _relationship_endpoints(
                            relationship_by_id[relationship_id]
                        )
                    }
                )
            )
            observations = source_reader.read_records(dependency_ids)
            try:
                content_results.append(
                    self._content.apply_batch(
                        reviewed_plan_bytes,
                        {
                            record_id: observation.content
                            for record_id, observation in observations.items()
                        },
                        selected_record_ids=(),
                        selected_relationship_ids=relationship_ids,
                        expected_plan_sha256=expected_plan_sha256,
                    )
                )
                self._hit("migration.after_batch_commit")
            finally:
                _close_observations(observations.values())

        p24_records = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.4"
            and row.get("import_mode") in {"graph_node", "graph_edge", "finding"}
            and _source(row).get("record_kind") == "logical_record"
        )
        p24_relationships = tuple(
            row
            for row in relationships
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.4"
            and row.get("import_mode")
            in {"graph_node", "graph_edge", "finding", "legacy_history"}
        )
        p24_containers = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.4"
            and row.get("import_mode") == "source_container"
            and _source(row).get("record_kind") == "physical_file"
            and _source(row).get("category")
            in {
                "knowledge.wiki_graph_edges",
                "knowledge.wiki_candidates",
                "knowledge.legacy_edges",
            }
        )
        p24_history_logical = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.4"
            and row.get("import_mode") == "legacy_history"
            and _source(row).get("record_kind") == "logical_record"
            and _source(row).get("category")
            in {"identity.auth_audit", "knowledge.tombstones"}
        )
        p24_history_physical = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.4"
            and _source(row).get("record_kind") == "physical_file"
            and (
                (
                    row.get("import_mode") == "legacy_history"
                    and _source(row).get("category")
                    in {"knowledge.wiki_log", "knowledge.tombstones"}
                )
                or (
                    row.get("import_mode") == "source_container"
                    and _source(row).get("category") == "identity.auth_audit"
                )
            )
        )
        all_imported_p24_record_ids = {
            str(row.get("record_id"))
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.4"
        }
        supported_p24_record_ids = {
            str(row.get("record_id"))
            for row in (
                p24_records
                + p24_containers
                + p24_history_logical
                + p24_history_physical
            )
        }
        if all_imported_p24_record_ids != supported_p24_record_ids:
            raise LegacyMigrationApplyPlanError(
                "Reviewed P2.4 records contain an unsupported or unaccounted effect."
            )
        all_imported_p24_relationship_ids = {
            str(row.get("relationship_id"))
            for row in relationships
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.4"
        }
        supported_p24_relationship_ids = {
            str(row.get("relationship_id")) for row in p24_relationships
        }
        if all_imported_p24_relationship_ids != supported_p24_relationship_ids:
            raise LegacyMigrationApplyPlanError(
                "Reviewed P2.4 relationships contain an unsupported effect."
            )
        p24_results: list[LegacyP24ImportResult] = []
        for offset in range(0, len(p24_records), batch_size):
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            source_reader.verify_snapshot_high_water()
            p24_results.append(
                self._p24.apply_batch(
                    reviewed_plan_bytes,
                    selected_record_ids=tuple(
                        str(row["record_id"])
                        for row in sorted(
                            p24_records[offset : offset + batch_size],
                            key=lambda value: str(value.get("record_id")),
                        )
                    ),
                    expected_plan_sha256=expected_plan_sha256,
                )
            )
            self._hit("migration.after_batch_commit")
        for offset in range(0, len(p24_relationships), batch_size):
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            source_reader.verify_snapshot_high_water()
            p24_results.append(
                self._p24.apply_batch(
                    reviewed_plan_bytes,
                    selected_relationship_ids=tuple(
                        str(row["relationship_id"])
                        for row in sorted(
                            p24_relationships[offset : offset + batch_size],
                            key=lambda value: str(value.get("relationship_id")),
                        )
                    ),
                    expected_plan_sha256=expected_plan_sha256,
                )
            )
            self._hit("migration.after_batch_commit")
        for offset in range(0, len(p24_containers), batch_size):
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            source_reader.verify_snapshot_high_water()
            p24_results.append(
                self._p24.apply_batch(
                    reviewed_plan_bytes,
                    selected_record_ids=tuple(
                        str(row["record_id"])
                        for row in sorted(
                            p24_containers[offset : offset + batch_size],
                            key=lambda value: str(value.get("record_id")),
                        )
                    ),
                    expected_plan_sha256=expected_plan_sha256,
                )
            )
            self._hit("migration.after_batch_commit")

        p24_history_results: list[LegacyP24HistoryImportResult] = []
        for selected_history in (p24_history_logical, p24_history_physical):
            for offset in range(0, len(selected_history), batch_size):
                batch_rows = tuple(
                    sorted(
                        selected_history[offset : offset + batch_size],
                        key=lambda value: str(value.get("record_id")),
                    )
                )
                verify_migration_storage_layout(
                    self._layout, require_database=True, expected=self._boundary
                )
                source_reader.verify_snapshot_high_water()
                payload_record_ids = tuple(
                    sorted(
                        {
                            str(
                                _source(row).get("parent_record_id")
                                if _source(row).get("record_kind")
                                == "logical_record"
                                else row.get("record_id")
                            )
                            for row in batch_rows
                        }
                    )
                )
                if any(value not in records_by_id for value in payload_record_ids):
                    raise LegacyMigrationApplyPlanError(
                        "A P2.4 history parent is absent from the reviewed plan."
                    )
                observations = source_reader.read_records(payload_record_ids)
                try:
                    p24_history_results.append(
                        self._p24_history.apply_batch(
                            reviewed_plan_bytes,
                            {
                                record_id: observation.content
                                for record_id, observation in observations.items()
                            },
                            selected_record_ids=tuple(
                                str(row["record_id"]) for row in batch_rows
                            ),
                            expected_plan_sha256=expected_plan_sha256,
                        )
                    )
                    self._hit("migration.after_batch_commit")
                finally:
                    _close_observations(observations.values())

        p25_session_parents = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.5"
            and row.get("import_mode") == "source_container"
            and _source(row).get("record_kind") == "physical_file"
            and _source(row).get("category")
            in {"conversation.user_sessions", "conversation.global_sessions"}
        )
        p25_session_children = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.5"
            and row.get("import_mode") == "conversation_child"
            and _source(row).get("record_kind") == "logical_record"
            and _source(row).get("category")
            in {"conversation.user_sessions", "conversation.global_sessions"}
        )
        p25_jobs = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.5"
            and row.get("import_mode") in {"job_history", "abandoned_legacy_job"}
            and _source(row).get("record_kind") == "physical_file"
            and _source(row).get("category") == "operations.jobs"
        )
        p25_notion_links = tuple(
            row
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.5"
            and row.get("import_mode") == "notion_session_link"
            and _source(row).get("record_kind") == "logical_record"
            and _source(row).get("category") == "external.notion_session_links"
        )
        all_imported_p25_record_ids = {
            str(row.get("record_id"))
            for row in records
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.5"
        }
        supported_p25_record_ids = {
            str(row.get("record_id"))
            for row in (
                p25_session_parents
                + p25_session_children
                + p25_jobs
                + p25_notion_links
            )
        }
        if all_imported_p25_record_ids != supported_p25_record_ids:
            raise LegacyMigrationApplyPlanError(
                "Reviewed P2.5 records contain an unsupported or unaccounted effect."
            )
        imported_p25_relationships = tuple(
            row
            for row in relationships
            if row.get("action") == "import"
            and row.get("execution_phase") == "P2.5"
        )
        if any(
            row.get("import_mode") != "notion_session_link"
            or not isinstance(row.get("source_relationship"), Mapping)
            or row["source_relationship"].get("relationship_type")
            != "notion_session_page"
            for row in imported_p25_relationships
        ):
            raise LegacyMigrationApplyPlanError(
                "Reviewed P2.5 relationships contain an unsupported effect."
            )

        children_by_parent: dict[str, list[Mapping[str, object]]] = {
            str(parent["record_id"]): [] for parent in p25_session_parents
        }
        for child in p25_session_children:
            parent_id = _source(child).get("parent_record_id")
            if not isinstance(parent_id, str) or parent_id not in children_by_parent:
                raise LegacyMigrationApplyPlanError(
                    "A reviewed P2.5 session child has no imported parent."
                )
            children_by_parent[parent_id].append(child)

        p25_results: list[LegacyP25ImportResult] = []
        for parent in sorted(
            p25_session_parents, key=lambda value: str(value.get("record_id"))
        ):
            parent_id = str(parent["record_id"])
            parent_size = _source_size(parent)
            if parent_size > MAX_P25_SOURCE_BYTES:
                raise LegacyMigrationApplyPlanError(
                    "A reviewed P2.5 session exceeds the bounded source limit."
                )
            children = tuple(
                sorted(
                    children_by_parent[parent_id],
                    key=lambda value: int(_source(value).get("source_ordinal", 0)),
                )
            )
            for offset in range(0, len(children), batch_size):
                verify_migration_storage_layout(
                    self._layout, require_database=True, expected=self._boundary
                )
                source_reader.verify_snapshot_high_water()
                observation = source_reader.read_record(parent_id)
                try:
                    p25_results.append(
                        self._p25.apply_batch(
                            reviewed_plan_bytes,
                            {parent_id: observation.content},
                            selected_record_ids=tuple(
                                sorted(
                                    str(row["record_id"])
                                    for row in children[offset : offset + batch_size]
                                )
                            ),
                            expected_plan_sha256=expected_plan_sha256,
                        )
                    )
                    self._hit("migration.after_batch_commit")
                finally:
                    observation.close()
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            source_reader.verify_snapshot_high_water()
            observation = source_reader.read_record(parent_id)
            try:
                p25_results.append(
                    self._p25.apply_batch(
                        reviewed_plan_bytes,
                        {parent_id: observation.content},
                        selected_record_ids=(parent_id,),
                        expected_plan_sha256=expected_plan_sha256,
                    )
                )
                self._hit("migration.after_batch_commit")
            finally:
                observation.close()

        for job_ids in _chunk_records(p25_jobs, batch_size=batch_size):
            if len(job_ids) > MAX_P25_EFFECTS_PER_TRANSACTION:
                raise LegacyMigrationApplyPlanError(
                    "A reviewed P2.5 job batch exceeds the effect limit."
                )
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            source_reader.verify_snapshot_high_water()
            observations = source_reader.read_records(job_ids)
            try:
                p25_results.append(
                    self._p25.apply_batch(
                        reviewed_plan_bytes,
                        {
                            record_id: observation.content
                            for record_id, observation in observations.items()
                        },
                        selected_record_ids=job_ids,
                        expected_plan_sha256=expected_plan_sha256,
                    )
                )
                self._hit("migration.after_batch_commit")
            finally:
                _close_observations(observations.values())

        notion_relationship_by_source = {
            str(row["source_relationship"]["source_record_id"]): row
            for row in imported_p25_relationships
        }
        if (
            len(notion_relationship_by_source) != len(imported_p25_relationships)
            or set(notion_relationship_by_source)
            != {str(row["record_id"]) for row in p25_notion_links}
        ):
            raise LegacyMigrationApplyPlanError(
                "Every P2.5 Notion link requires exactly one reviewed relationship."
            )
        notion_links_by_parent: dict[str, list[Mapping[str, object]]] = {}
        for row in p25_notion_links:
            parent_id = _source(row).get("parent_record_id")
            if not isinstance(parent_id, str) or parent_id not in records_by_id:
                raise LegacyMigrationApplyPlanError(
                    "A P2.5 Notion link has no retained database parent."
                )
            notion_links_by_parent.setdefault(parent_id, []).append(row)
        p25_notion_results: list[LegacyP25NotionImportResult] = []
        notion_pair_batch_size = max(1, min(50, batch_size // 2))
        for parent_id in sorted(notion_links_by_parent):
            parent_source = _source(records_by_id[parent_id])
            main_relative = str(parent_source.get("relative_path"))
            component_ids = tuple(
                sorted(
                    str(row["record_id"])
                    for row in records
                    if _source(row).get("record_kind") == "physical_file"
                    and _source(row).get("root_id") == parent_source.get("root_id")
                    and str(_source(row).get("relative_path"))
                    in {
                        main_relative,
                        main_relative + "-wal",
                        main_relative + "-shm",
                        main_relative + "-journal",
                    }
                )
            )
            if not component_ids:
                raise LegacyMigrationApplyPlanError(
                    "A P2.5 Notion snapshot has no physical components."
                )
            parent_links = tuple(
                sorted(
                    notion_links_by_parent[parent_id],
                    key=lambda value: str(value.get("record_id")),
                )
            )
            for offset in range(0, len(parent_links), notion_pair_batch_size):
                batch_links = parent_links[offset : offset + notion_pair_batch_size]
                record_ids = tuple(str(row["record_id"]) for row in batch_links)
                relationship_ids = tuple(
                    sorted(
                        str(notion_relationship_by_source[record_id]["relationship_id"])
                        for record_id in record_ids
                    )
                )
                verify_migration_storage_layout(
                    self._layout, require_database=True, expected=self._boundary
                )
                source_reader.verify_snapshot_high_water()
                observations = source_reader.read_records(component_ids)
                try:
                    p25_notion_results.append(
                        self._p25_notion.apply_batch(
                            reviewed_plan_bytes,
                            {
                                record_id: observation.content
                                for record_id, observation in observations.items()
                            },
                            selected_record_ids=record_ids,
                            selected_relationship_ids=relationship_ids,
                            expected_plan_sha256=expected_plan_sha256,
                        )
                    )
                    self._hit("migration.after_batch_commit")
                finally:
                    _close_observations(observations.values())

        verify_migration_storage_layout(
            self._layout, require_database=True, expected=self._boundary
        )
        source_reader.verify_snapshot_high_water()

        return LegacyMigrationApplyResult(
            plan_sha256=expected_plan_sha256,
            identity=identity_result,
            content_batches=tuple(content_results),
            p24_batches=tuple(p24_results),
            p24_history_batches=tuple(p24_history_results),
            p25_batches=tuple(p25_results),
            p25_notion_batches=tuple(p25_notion_results),
        )


__all__ = [
    "LegacyMigrationApplyError",
    "LegacyMigrationApplyLayoutError",
    "LegacyMigrationApplyPlanError",
    "LegacyMigrationApplyResult",
    "LegacyMigrationApplyService",
]
