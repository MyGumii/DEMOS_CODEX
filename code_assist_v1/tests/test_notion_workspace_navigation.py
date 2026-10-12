from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.notion_workspace_store import (
    LegacyPageSourceProof,
    NAVIGATION_SECTIONS,
    WorkspaceOperationConflict,
    WorkspaceRevisionConflict,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(catalog_path_for_user_data_root(tmp_path / "user-data"))


def _workspace(store: WorkspaceStore) -> tuple[str, int]:
    created = store.create_workspace(
        "owner", name="Navigation", operation_id="create-navigation-workspace"
    )
    return created["workspace"]["id"], created["workspace"]["version"]


def _sections(*, first_collapsed: bool = False):
    return [
        {
            "section": section,
            "display_order": index,
            "hidden": False,
            "collapsed": first_collapsed and index == 0,
            "item_limit": 10,
        }
        for index, section in enumerate(NAVIGATION_SECTIONS)
    ]


def _bind(
    store: WorkspaceStore,
    workspace_id: str,
    *,
    actor: str,
    owner: str,
    page_id: str,
    base_version: int,
    operation_prefix: str,
    teamspace_id: str = "",
) -> int:
    proof = LegacyPageSourceProof(
        actor,
        owner,
        page_id,
        1,
        100,
        "sha256:" + operation_prefix[0].encode().hex()[0:1] * 64,
    )
    staged = store.stage_page_source_binding(
        actor,
        workspace_id,
        proof,
        teamspace_id=teamspace_id,
        base_version=base_version,
        operation_id=f"{operation_prefix}-stage",
    )
    activated = store.activate_page_source_binding(
        actor,
        workspace_id,
        proof,
        source_revalidator=lambda _proof: True,
        expected_binding_version=staged["binding"]["version"],
        teamspace_id=teamspace_id,
        base_version=staged["workspace"]["version"],
        operation_id=f"{operation_prefix}-activate",
    )
    return activated["workspace"]["version"]


def test_navigation_default_read_is_member_only_and_creates_no_rows(tmp_path) -> None:
    store = _store(tmp_path)
    workspace_id, _version = _workspace(store)
    before = store.audit_counts()

    snapshot = store.get_navigation_state("owner", workspace_id)
    assert snapshot == {
        "workspace_id": workspace_id,
        "subject_id": "owner",
        "membership_epoch": 1,
        "preference_version": 0,
        "favorite_version": 0,
        "sidebar": {"width_px": 270, "collapsed": False},
        "sections": _sections(),
        "favorites": [],
    }
    assert store.audit_counts() == before
    con = store._read_connection()
    try:
        assert con.execute("SELECT COUNT(*) FROM workspace_navigation_states").fetchone()[0] == 0
    finally:
        con.close()
    with pytest.raises(FileNotFoundError):
        store.get_navigation_state("outsider", workspace_id)


def test_sidebar_preferences_are_cas_replay_safe_and_do_not_bump_workspace(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    workspace_id, workspace_version = _workspace(store)
    request_sections = _sections(first_collapsed=True)

    first = store.replace_sidebar_preferences(
        "owner",
        workspace_id,
        expected_preference_version=0,
        sidebar_width_px=320,
        sidebar_collapsed=True,
        sections=request_sections,
        operation_id="preferences-1",
    )
    replay = store.replace_sidebar_preferences(
        "owner",
        workspace_id,
        expected_preference_version=0,
        sidebar_width_px=320,
        sidebar_collapsed=True,
        sections=request_sections,
        operation_id="preferences-1",
    )
    assert replay == first
    assert first["preference_version"] == 1
    assert first["favorite_version"] == 0
    assert store.get_workspace("owner", workspace_id)["version"] == workspace_version
    assert store.get_navigation_state("owner", workspace_id)["sidebar"] == {
        "width_px": 320,
        "collapsed": True,
    }
    with pytest.raises(WorkspaceRevisionConflict) as stale:
        store.replace_sidebar_preferences(
            "owner",
            workspace_id,
            expected_preference_version=0,
            sidebar_width_px=300,
            sidebar_collapsed=False,
            sections=_sections(),
            operation_id="preferences-stale",
        )
    assert stale.value.current_version == 1
    with pytest.raises(WorkspaceOperationConflict):
        store.replace_sidebar_preferences(
            "owner",
            workspace_id,
            expected_preference_version=0,
            sidebar_width_px=321,
            sidebar_collapsed=True,
            sections=request_sections,
            operation_id="preferences-1",
        )
    assert store.verify_deep_health()["navigation_counts"] == {
        "state_count": 1,
        "section_count": 5,
        "favorite_count": 0,
    }


@pytest.mark.parametrize(
    ("width", "sections"),
    [
        (179, _sections()),
        (601, _sections()),
        (True, _sections()),
        (240, _sections()[:-1]),
        (
            240,
            [
                {**item, "display_order": 0}
                if item["section"] == "recents"
                else item
                for item in _sections()
            ],
        ),
        (
            240,
            [
                {**item, "item_limit": 101}
                if item["section"] == "favorites"
                else item
                for item in _sections()
            ],
        ),
    ],
)
def test_navigation_preferences_reject_malformed_input_before_writes(
    tmp_path, width, sections
) -> None:
    store = _store(tmp_path)
    workspace_id, _version = _workspace(store)
    before = store.audit_counts()

    with pytest.raises(ValueError):
        store.replace_sidebar_preferences(
            "owner",
            workspace_id,
            expected_preference_version=0,
            sidebar_width_px=width,
            sidebar_collapsed=False,
            sections=sections,
            operation_id="preferences-invalid",
        )

    assert store.audit_counts() == before
    assert store.get_navigation_state("owner", workspace_id)["preference_version"] == 0


def test_navigation_replay_rechecks_current_membership(tmp_path) -> None:
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)
    added = store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=version,
        operation_id="add-navigation-member",
    )
    first = store.replace_sidebar_preferences(
        "member_one",
        workspace_id,
        expected_preference_version=0,
        sidebar_width_px=300,
        sidebar_collapsed=False,
        sections=_sections(),
        operation_id="member-preferences",
    )
    revoked = store.revoke_member(
        "owner",
        workspace_id,
        "member_one",
        base_version=added["workspace"]["version"],
        operation_id="revoke-navigation-member",
    )

    with pytest.raises(FileNotFoundError):
        store.replace_sidebar_preferences(
            "member_one",
            workspace_id,
            expected_preference_version=0,
            sidebar_width_px=300,
            sidebar_collapsed=False,
            sections=_sections(),
            operation_id="member-preferences",
        )
    with pytest.raises(FileNotFoundError):
        store.get_navigation_state("member_one", workspace_id)
    assert revoked["workspace"]["version"] == added["workspace"]["version"] + 1
    assert first["preference_version"] == 1


def test_favorite_versions_are_independent_and_duplicate_add_is_noop(tmp_path) -> None:
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)
    version = _bind(
        store,
        workspace_id,
        actor="owner",
        owner="owner",
        page_id="p_private",
        base_version=version,
        operation_prefix="a",
    )
    workspace_before = store.get_workspace("owner", workspace_id)

    added = store.add_page_favorite(
        "owner",
        workspace_id,
        source_owner_subject_id="owner",
        source_page_id="p_private",
        expected_favorite_version=0,
        operation_id="favorite-add",
    )
    duplicate = store.add_page_favorite(
        "owner",
        workspace_id,
        source_owner_subject_id="owner",
        source_page_id="p_private",
        expected_favorite_version=1,
        operation_id="favorite-add-duplicate",
    )
    assert added["changed"] is True and added["favorite_version"] == 1
    assert duplicate["changed"] is False and duplicate["favorite_version"] == 1
    assert added["preference_version"] == duplicate["preference_version"] == 0
    assert store.get_workspace("owner", workspace_id) == workspace_before
    snapshot = store.get_navigation_state("owner", workspace_id)
    assert snapshot["sidebar"] == {"width_px": 270, "collapsed": False}
    assert snapshot["favorites"] == [
        {
            "source_owner_subject_id": "owner",
            "source_page_id": "p_private",
            "position": 0,
        }
    ]

    removed = store.remove_page_favorite(
        "owner",
        workspace_id,
        source_owner_subject_id="owner",
        source_page_id="p_private",
        expected_favorite_version=1,
        operation_id="favorite-remove",
    )
    missing = store.remove_page_favorite(
        "owner",
        workspace_id,
        source_owner_subject_id="owner",
        source_page_id="p_private",
        expected_favorite_version=2,
        operation_id="favorite-remove-missing",
    )
    assert removed["changed"] is True and removed["favorite_version"] == 2
    assert missing["changed"] is False and missing["favorite_version"] == 2
    assert store.get_navigation_state("owner", workspace_id)["favorites"] == []
    assert store.verify_deep_health()["healthy"] is True


def test_hidden_teamspace_favorite_is_omitted_dense_and_removable_without_lookup(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)
    member = store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=version,
        operation_id="add-member",
    )
    version = member["workspace"]["version"]
    teamspace = store.create_teamspace(
        "owner",
        workspace_id,
        name="Private team",
        visibility="private",
        base_version=version,
        operation_id="create-teamspace",
    )
    teamspace_id = teamspace["teamspace"]["id"]
    version = teamspace["workspace"]["version"]
    version = store.set_teamspace_member(
        "owner",
        workspace_id,
        teamspace_id,
        "member_one",
        role="member",
        base_version=version,
        operation_id="add-teamspace-member",
    )["workspace"]["version"]
    version = _bind(
        store,
        workspace_id,
        actor="member_one",
        owner="member_one",
        page_id="p_member_private",
        base_version=version,
        operation_prefix="b",
    )
    version = _bind(
        store,
        workspace_id,
        actor="owner",
        owner="owner",
        page_id="p_teamspace",
        base_version=version,
        operation_prefix="c",
        teamspace_id=teamspace_id,
    )
    store.add_page_favorite(
        "member_one",
        workspace_id,
        source_owner_subject_id="member_one",
        source_page_id="p_member_private",
        expected_favorite_version=0,
        operation_id="favorite-private",
    )
    store.add_page_favorite(
        "member_one",
        workspace_id,
        source_owner_subject_id="owner",
        source_page_id="p_teamspace",
        expected_favorite_version=1,
        operation_id="favorite-teamspace",
    )
    version = store.remove_teamspace_member(
        "owner",
        workspace_id,
        teamspace_id,
        "member_one",
        base_version=version,
        operation_id="remove-teamspace-member",
    )["workspace"]["version"]
    snapshot = store.get_navigation_state("member_one", workspace_id)
    assert snapshot["favorites"] == [
        {
            "source_owner_subject_id": "member_one",
            "source_page_id": "p_member_private",
            "position": 0,
        }
    ]
    con = store._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_favorites WHERE subject_id='member_one'"
        ).fetchone()[0] == 2
    finally:
        con.close()
    removed = store.remove_page_favorite(
        "member_one",
        workspace_id,
        source_owner_subject_id="owner",
        source_page_id="p_teamspace",
        expected_favorite_version=2,
        operation_id="remove-hidden-favorite",
    )
    assert removed["changed"] is True
    assert store.get_workspace("owner", workspace_id)["version"] == version


def test_navigation_raw_sql_requires_service_context(tmp_path) -> None:
    store = _store(tmp_path)
    workspace_id, _version = _workspace(store)
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(store.db_path), query_only=False
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="invalid workspace navigation"):
            con.execute(
                """
                INSERT INTO workspace_navigation_states VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (workspace_id, "owner", 1, 0, 240, 0, 100, 100, "owner", "raw"),
            )
        con.rollback()
    finally:
        con.close()


def test_navigation_raw_sql_null_context_is_fail_closed(tmp_path) -> None:
    store = _store(tmp_path)
    workspace_id, _version = _workspace(store)
    store.replace_sidebar_preferences(
        "owner",
        workspace_id,
        expected_preference_version=0,
        sidebar_width_px=240,
        sidebar_collapsed=False,
        sections=_sections(),
        operation_id="preferences-seed",
    )
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(store.db_path), query_only=False
    )
    try:
        con.create_function("workspace_navigation_actor", 0, lambda: None)
        con.create_function("workspace_navigation_action", 0, lambda: None)
        con.create_function(
            "workspace_navigation_operation_id", 0, lambda: "preferences-seed"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid workspace sidebar"):
            con.execute(
                """
                DELETE FROM workspace_sidebar_section_preferences
                WHERE workspace_id=? AND subject_id='owner'
                  AND section_key='favorites'
                """,
                (workspace_id,),
            )
        con.rollback()
    finally:
        con.close()
    assert len(store.get_navigation_state("owner", workspace_id)["sections"]) == 5


def test_navigation_state_insert_or_replace_cannot_reset_versions(tmp_path) -> None:
    store = _store(tmp_path)
    workspace_id, _version = _workspace(store)
    store.replace_sidebar_preferences(
        "owner",
        workspace_id,
        expected_preference_version=0,
        sidebar_width_px=300,
        sidebar_collapsed=True,
        sections=_sections(),
        operation_id="preferences-before-replace",
    )
    before = store.get_navigation_state("owner", workspace_id)
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(store.db_path), query_only=False
    )
    try:
        con.create_function("workspace_navigation_actor", 0, lambda: "owner")
        con.create_function(
            "workspace_navigation_action",
            0,
            lambda: store_module.NAVIGATION_PREFERENCES_ACTION,
        )
        con.create_function(
            "workspace_navigation_operation_id", 0, lambda: "raw-replace"
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="workspace navigation state already exists"
        ):
            con.execute(
                """
                INSERT OR REPLACE INTO workspace_navigation_states(
                  workspace_id,subject_id,preference_version,favorite_version,
                  sidebar_width_px,sidebar_collapsed,created_at,updated_at,
                  last_actor_subject_id,last_operation_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    workspace_id,
                    "owner",
                    1,
                    0,
                    240,
                    0,
                    101,
                    101,
                    "owner",
                    "raw-replace",
                ),
            )
        con.rollback()
    finally:
        con.close()
    assert store.get_navigation_state("owner", workspace_id) == before


def test_navigation_outbox_fault_rolls_back_domain_and_ledger(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    workspace_id, _version = _workspace(store)
    before = store.audit_counts()
    original = WorkspaceStore._set_navigation_context

    def faulting_context(con, *, actor_subject_id, operation_id, action):
        original(
            con,
            actor_subject_id=actor_subject_id,
            operation_id=operation_id,
            action=action,
        )
        con.execute(
            """
            CREATE TEMP TRIGGER fail_navigation_outbox
            BEFORE INSERT ON workspace_outbox
            WHEN NEW.event_type='workspace.navigation_preferences_updated'
            BEGIN SELECT RAISE(ABORT,'navigation outbox fault'); END
            """
        )

    monkeypatch.setattr(
        WorkspaceStore, "_set_navigation_context", staticmethod(faulting_context)
    )
    with pytest.raises(sqlite3.IntegrityError, match="navigation outbox fault"):
        store.replace_sidebar_preferences(
            "owner",
            workspace_id,
            expected_preference_version=0,
            sidebar_width_px=300,
            sidebar_collapsed=False,
            sections=_sections(),
            operation_id="preferences-fault",
        )
    assert store.audit_counts() == before
    assert store.get_navigation_state("owner", workspace_id)["preference_version"] == 0
