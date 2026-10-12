from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core.projection import materializer as materializer_module
from code_assist_v1.knowledge_core.projection.materializer import (
    FullProjectionMaterializer,
    ProjectionCanonicalDriftError,
    ProjectionMaterializationError,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _factory,
    _reviewed_fixture,
    _storage_layout,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_verifier import (
    _publish_first_migrated_item_without_search,
    _uuid,
)


pytestmark = pytest.mark.unit


def _raw_connection(factory: Any) -> sqlite3.Connection:
    connection = sqlite3.connect(factory.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA recursive_triggers=ON")
    return connection


def _applied_fixture(tmp_path: Path, isolated_knowledge_layout: Any) -> tuple[Any, Any]:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    return factory, layout


def _admin_space(factory: Any) -> str:
    connection = _raw_connection(factory)
    try:
        row = connection.execute(
            "SELECT id FROM knowledge_space WHERE owner_user_id='admin'"
        ).fetchone()
        assert row is not None
        return str(row["id"])
    finally:
        connection.close()


def _higher_space(factory: Any, space_kind: str) -> str:
    connection = _raw_connection(factory)
    try:
        row = connection.execute(
            "SELECT id FROM knowledge_space WHERE space_kind=?",
            (space_kind,),
        ).fetchone()
        assert row is not None
        return str(row["id"])
    finally:
        connection.close()


def _add_other_personal_space(factory: Any) -> str:
    space_id = _uuid(10_100)
    connection = _raw_connection(factory)
    try:
        connection.execute(
            """INSERT INTO app_user(
                 id,display_name,password_hash,system_role,account_status,
                 current_part_id,scope_epoch,created_at,updated_at,disabled_at,row_version
               ) VALUES ('other','Other','hash','user','active',NULL,1,1,1,NULL,1)"""
        )
        connection.execute(
            """INSERT INTO knowledge_space(
                 id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
                 generation,created_at,updated_at,row_version
               ) VALUES (?,'personal','other',NULL,'active',0,0,1,1,1)""",
            (space_id,),
        )
        connection.execute(
            """INSERT INTO personal_wiki_profile(
                 space_id,owner_user_id,display_name,name_key,lifecycle_status,
                 lifecycle_generation,is_default,archived_at,
                 archived_by_user_id,archive_reason,created_at,updated_at,row_version
               ) VALUES (?,'other','Other Wiki','other wiki','active',1,1,
                         NULL,NULL,NULL,1,1,1)""",
            (space_id,),
        )
        connection.commit()
        return space_id
    finally:
        connection.close()


def _insert_manual_edge(
    factory: Any,
    *,
    space_id: str,
    source_item_id: str,
    source_revision_id: str,
    target_item_id: str,
    target_revision_id: str,
) -> str:
    edge_id = _uuid(10_150)
    connection = _raw_connection(factory)
    try:
        connection.execute(
            """INSERT INTO knowledge_edge(
                 id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                 origin,source_evidence_revision_id,target_evidence_revision_id,
                 approval_finding_id,evidence_json,needs_revalidation,
                 created_by,updated_by,created_at,updated_at,row_version
               ) VALUES (?,?,?,?,'related','active','manual',?,?,NULL,'{}',0,
                         'admin','admin',1,1,1)""",
            (
                edge_id,
                space_id,
                source_item_id,
                target_item_id,
                source_revision_id,
                target_revision_id,
            ),
        )
        connection.commit()
        return edge_id
    finally:
        connection.close()


def _projection_bytes(root: Path) -> dict[str, bytes]:
    return {
        value.relative_to(root).as_posix(): value.read_bytes()
        for value in sorted(root.rglob("*"))
        if value.is_file()
    }


def _manifest(layout: Any, space_id: str) -> dict[str, object]:
    return json.loads((layout.projection_root / space_id / "manifest.json").read_bytes())


def _advance_generation(factory: Any, space_id: str) -> None:
    connection = _raw_connection(factory)
    try:
        space = connection.execute(
            "SELECT generation,updated_at,row_version FROM knowledge_space WHERE id=?",
            (space_id,),
        ).fetchone()
        state = connection.execute(
            """SELECT desired_generation,updated_at,row_version
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        assert space is not None and state is not None
        generation = int(space["generation"]) + 1
        connection.execute(
            """UPDATE knowledge_space
                  SET generation=?,updated_at=?,row_version=row_version+1
                WHERE id=? AND row_version=?""",
            (
                generation,
                int(space["updated_at"]) + 1,
                space_id,
                int(space["row_version"]),
            ),
        )
        connection.execute(
            """UPDATE projection_state
                  SET desired_generation=?,status='idle',updated_at=?,
                      row_version=row_version+1
                WHERE projection_name='markdown' AND space_id=? AND row_version=?""",
            (
                generation,
                int(state["updated_at"]) + 1,
                space_id,
                int(state["row_version"]),
            ),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.scenario("PROJ-003")
@pytest.mark.scenario("TIME-003")
def test_full_projection_rebuild_is_deterministic_complete_and_space_isolated(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    factory, layout = _applied_fixture(tmp_path, isolated_knowledge_layout)
    admin_space = _admin_space(factory)
    responsibility_space = _higher_space(factory, "responsibility")
    other_space = _add_other_personal_space(factory)
    first_item = _publish_first_migrated_item_without_search(
        factory,
        body="alpha projection body\n",
        id_base=10_200,
        owner_user_id="admin",
        space_id=admin_space,
    )
    second_item = _publish_first_migrated_item_without_search(
        factory,
        body="second projection body",
        id_base=10_220,
        owner_user_id="admin",
        space_id=admin_space,
    )
    other_item = _publish_first_migrated_item_without_search(
        factory,
        body="beta projection body",
        id_base=10_240,
        owner_user_id="other",
        space_id=other_space,
    )
    edge_id = _insert_manual_edge(
        factory,
        space_id=admin_space,
        source_item_id=first_item,
        source_revision_id=_uuid(10_205),
        target_item_id=second_item,
        target_revision_id=_uuid(10_225),
    )
    connection = _raw_connection(factory)
    try:
        canonical_before = {
            "items": [
                tuple(row)
                for row in connection.execute(
                    """SELECT id,space_id,lifecycle_status,curation_status,
                              current_revision_id,row_version
                         FROM knowledge_item ORDER BY id"""
                )
            ],
            "edges": [
                tuple(row)
                for row in connection.execute(
                    """SELECT id,space_id,source_item_id,target_item_id,
                              edge_type,edge_status,needs_revalidation,row_version
                         FROM knowledge_edge ORDER BY id"""
                )
            ],
        }
    finally:
        connection.close()

    first = FullProjectionMaterializer(factory, layout).rebuild_all()
    assert first.space_count == 3
    assert first.entry_count == 15
    before = _projection_bytes(layout.projection_root)
    admin_manifest = _manifest(layout, admin_space)
    responsibility_manifest = _manifest(layout, responsibility_space)
    other_manifest = _manifest(layout, other_space)
    assert admin_manifest["projection_name"] == "markdown"
    assert admin_manifest["generation"] == 0
    assert admin_manifest["entries"] == sorted(
        admin_manifest["entries"], key=lambda row: row["relative_path"]
    )
    assert {row["relative_path"] for row in admin_manifest["entries"]} == {
        "graph/backlinks.json",
        "graph/graph.json",
        "index.md",
        "log.md",
        f"sources/{first_item}.md",
        f"sources/{second_item}.md",
    }
    assert {row["relative_path"] for row in other_manifest["entries"]} == {
        "graph/backlinks.json",
        "graph/graph.json",
        "index.md",
        "log.md",
        f"sources/{other_item}.md",
    }
    assert {row["relative_path"] for row in responsibility_manifest["entries"]} == {
        "graph/backlinks.json",
        "graph/graph.json",
        "index.md",
        "log.md",
    }
    admin_generation = layout.projection_root / admin_space / "generations" / "0"
    responsibility_generation = (
        layout.projection_root / responsibility_space / "generations" / "0"
    )
    other_generation = layout.projection_root / other_space / "generations" / "0"
    assert (admin_generation / "sources" / f"{first_item}.md").read_bytes() == (
        b"alpha projection body\n"
    )
    assert not (admin_generation / "sources" / f"{other_item}.md").exists()
    assert not (other_generation / "sources" / f"{first_item}.md").exists()
    graph = json.loads((admin_generation / "graph" / "graph.json").read_bytes())
    assert graph["edges"] == [
        {
            "edge_id": edge_id,
            "edge_type": "related",
            "needs_revalidation": False,
            "origin": "manual",
            "source_item_id": first_item,
            "target_item_id": second_item,
        }
    ]
    backlinks = json.loads(
        (admin_generation / "graph" / "backlinks.json").read_bytes()
    )
    target = next(
        row for row in backlinks["backlinks"] if row["item_id"] == second_item
    )
    assert target["incoming"] == [
        {
            "edge_id": edge_id,
            "edge_type": "related",
            "source_item_id": first_item,
        }
    ]
    for manifest, generation_root in (
        (admin_manifest, admin_generation),
        (responsibility_manifest, responsibility_generation),
        (other_manifest, other_generation),
    ):
        for entry in manifest["entries"]:
            content = (generation_root / entry["relative_path"]).read_bytes()
            assert len(content) == entry["size_bytes"]
            assert hashlib.sha256(content).hexdigest() == entry["sha256"]

    connection = _raw_connection(factory)
    try:
        state_before = [
            tuple(row)
            for row in connection.execute(
                """SELECT projection_name,space_id,desired_generation,
                          built_generation,checksum_sha256,status,row_version
                     FROM projection_state ORDER BY space_id"""
            )
        ]
    finally:
        connection.close()
    second = FullProjectionMaterializer(factory, layout).rebuild_all()
    assert second == first
    assert _projection_bytes(layout.projection_root) == before
    connection = _raw_connection(factory)
    try:
        state_after = [
            tuple(row)
            for row in connection.execute(
                """SELECT projection_name,space_id,desired_generation,
                          built_generation,checksum_sha256,status,row_version
                     FROM projection_state ORDER BY space_id"""
            )
        ]
    finally:
        connection.close()
    assert state_after == state_before
    connection = _raw_connection(factory)
    try:
        canonical_after = {
            "items": [
                tuple(row)
                for row in connection.execute(
                    """SELECT id,space_id,lifecycle_status,curation_status,
                              current_revision_id,row_version
                         FROM knowledge_item ORDER BY id"""
                )
            ],
            "edges": [
                tuple(row)
                for row in connection.execute(
                    """SELECT id,space_id,source_item_id,target_item_id,
                              edge_type,edge_status,needs_revalidation,row_version
                         FROM knowledge_edge ORDER BY id"""
                )
            ],
        }
    finally:
        connection.close()
    assert canonical_after == canonical_before


def test_manifest_publish_failure_preserves_previous_generation_pointer_and_state(
    tmp_path: Path,
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, layout = _applied_fixture(tmp_path, isolated_knowledge_layout)
    space_id = _admin_space(factory)
    _publish_first_migrated_item_without_search(
        factory, body="stable", id_base=10_300, owner_user_id="admin", space_id=space_id
    )
    FullProjectionMaterializer(factory, layout).rebuild_all()
    previous = (layout.projection_root / space_id / "manifest.json").read_bytes()
    _advance_generation(factory, space_id)

    def fail_publish(
        _self: object, _prepared: object, _pinned: object | None = None
    ) -> None:
        raise ProjectionMaterializationError("injected publication failure")

    monkeypatch.setattr(
        materializer_module._ProjectionWriter,
        "publish_manifest",
        fail_publish,
    )
    with pytest.raises(ProjectionMaterializationError):
        FullProjectionMaterializer(factory, layout).rebuild_all()
    assert (layout.projection_root / space_id / "manifest.json").read_bytes() == previous
    assert (layout.projection_root / space_id / "generations" / "1").is_dir()
    connection = _raw_connection(factory)
    try:
        row = connection.execute(
            """SELECT desired_generation,built_generation,status
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        assert tuple(row) == (1, 0, "idle")
    finally:
        connection.close()


def test_generation_files_remain_write_pinned_through_manifest_and_state_publication(
    tmp_path: Path,
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, layout = _applied_fixture(tmp_path, isolated_knowledge_layout)
    space_id = _admin_space(factory)
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="write-pinned projection",
        id_base=10_350,
        owner_user_id="admin",
        space_id=space_id,
    )
    original = materializer_module._ProjectionWriter.publish_manifest
    original_update = materializer_module._update_projection_state
    manifest_write_was_blocked = False
    state_write_was_blocked = False

    def assert_write_is_blocked(target: Path) -> None:
        def open_for_write() -> None:
            descriptor = os.open(target, os.O_RDWR)
            os.close(descriptor)

        with pytest.raises(OSError) as caught:
            open_for_write()
        assert (
            getattr(caught.value, "winerror", None) in {5, 32}
            or caught.value.errno == 13
        )

    def publish_while_generation_is_pinned(
        self: object, prepared: object, pinned: object | None = None
    ) -> None:
        nonlocal manifest_write_was_blocked
        if prepared.snapshot.space_id == space_id:
            target = (
                layout.projection_root
                / prepared.snapshot.space_id
                / "generations"
                / str(prepared.snapshot.generation)
                / "sources"
                / f"{item_id}.md"
            )
            assert_write_is_blocked(target)
            manifest_write_was_blocked = True
        original(self, prepared, pinned)

    def update_while_generation_is_pinned(*args: object, **kwargs: object) -> None:
        nonlocal state_write_was_blocked
        target = (
            layout.projection_root
            / space_id
            / "generations"
            / "0"
            / "sources"
            / f"{item_id}.md"
        )
        assert_write_is_blocked(target)
        state_write_was_blocked = True
        original_update(*args, **kwargs)

    monkeypatch.setattr(
        materializer_module._ProjectionWriter,
        "publish_manifest",
        publish_while_generation_is_pinned,
    )
    monkeypatch.setattr(
        materializer_module,
        "_update_projection_state",
        update_while_generation_is_pinned,
    )
    FullProjectionMaterializer(factory, layout).rebuild_all()
    assert manifest_write_was_blocked
    assert state_write_was_blocked


@pytest.mark.scenario("PROJ-006")
def test_canonical_drift_after_staging_never_publishes_mixed_generation(
    tmp_path: Path,
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, layout = _applied_fixture(tmp_path, isolated_knowledge_layout)
    space_id = _admin_space(factory)
    _publish_first_migrated_item_without_search(
        factory, body="drift", id_base=10_400, owner_user_id="admin", space_id=space_id
    )
    FullProjectionMaterializer(factory, layout).rebuild_all()
    previous = (layout.projection_root / space_id / "manifest.json").read_bytes()
    _advance_generation(factory, space_id)
    original = materializer_module._ProjectionWriter.stage_generation
    changed = False

    def stage_then_change(self: object, prepared: object) -> None:
        nonlocal changed
        original(self, prepared)
        if not changed and prepared.snapshot.space_id == space_id:
            changed = True
            _advance_generation(factory, space_id)

    monkeypatch.setattr(
        materializer_module._ProjectionWriter,
        "stage_generation",
        stage_then_change,
    )
    with pytest.raises(ProjectionCanonicalDriftError):
        FullProjectionMaterializer(factory, layout).rebuild_all()
    assert (layout.projection_root / space_id / "manifest.json").read_bytes() == previous
    assert json.loads(previous)["generation"] == 0
    assert (layout.projection_root / space_id / "generations" / "1").is_dir()
    connection = _raw_connection(factory)
    try:
        row = connection.execute(
            """SELECT desired_generation,built_generation,status
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        assert tuple(row) == (2, 0, "idle")
    finally:
        connection.close()


def test_existing_generation_rejects_multiply_linked_projection_file(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    factory, layout = _applied_fixture(tmp_path, isolated_knowledge_layout)
    space_id = _admin_space(factory)
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="hard-link boundary",
        id_base=10_500,
        owner_user_id="admin",
        space_id=space_id,
    )
    FullProjectionMaterializer(factory, layout).rebuild_all()
    source = (
        layout.projection_root
        / space_id
        / "generations"
        / "0"
        / "sources"
        / f"{item_id}.md"
    )
    alias = tmp_path / "projection-hardlink-canary.md"
    os.link(source, alias)
    try:
        with pytest.raises(
            ProjectionMaterializationError,
            match="multiply linked",
        ):
            FullProjectionMaterializer(factory, layout).rebuild_all()
    finally:
        alias.unlink()


def test_invalid_normalized_body_fails_before_any_projection_publication(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    factory, layout = _applied_fixture(tmp_path, isolated_knowledge_layout)
    space_id = _admin_space(factory)
    # This helper intentionally records raw and normalized SHA as equal.  CRLF
    # therefore creates a canonical-row contradiction that the full rebuild
    # must reject before publishing or advancing the producer-created idle
    # projection-state marker.
    _publish_first_migrated_item_without_search(
        factory,
        body="invalid normalized body\r\n",
        id_base=10_600,
        owner_user_id="admin",
        space_id=space_id,
    )
    with pytest.raises(
        ProjectionMaterializationError,
        match="normalized digest",
    ):
        FullProjectionMaterializer(factory, layout).rebuild_all()
    assert not (layout.projection_root / space_id / "manifest.json").exists()
    connection = _raw_connection(factory)
    try:
        row = connection.execute(
            """SELECT desired_generation,built_generation,checksum_sha256,
                      status,built_at
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        assert tuple(row) == (0, 0, None, "idle", None)
    finally:
        connection.close()
