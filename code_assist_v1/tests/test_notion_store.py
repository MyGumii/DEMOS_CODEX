from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
import json
import os
import sqlite3

from code_assist_v1 import notion_store as ns


class NotionStoreParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_user_data_dir = ns.USER_DATA_DIR
        ns.USER_DATA_DIR = self.temp_dir.name
        self.user_id = "tester"

    def tearDown(self) -> None:
        ns.USER_DATA_DIR = self.original_user_data_dir
        self.temp_dir.cleanup()

    def test_page_settings_lock_versions_and_rich_blocks(self) -> None:
        created = ns.create_page(self.user_id, title="Parity page", icon="📝")
        page_id = created["page"]["id"]

        updated = ns.update_page(
            self.user_id,
            page_id,
            {
                "cover": "linear-gradient(120deg,#fff,#ddd)",
                "settings": {"font": "serif", "full_width": True, "small_text": True},
            },
        )
        self.assertEqual(updated["page"]["settings"]["font"], "serif")
        self.assertTrue(updated["page"]["settings"]["full_width"])

        blocks = [
            {"type": "toggle", "content": "Details", "props": {"open": False}},
            {"type": "toggle_heading1", "content": "Collapsible section", "props": {"open": True}},
            {"type": "heading4", "content": "Fourth section", "props": {}},
            {"type": "toggle_heading4", "content": "Nested fourth section", "props": {"open": False}},
            {"type": "callout", "content": "Important", "props": {"icon": "💡"}},
            {"type": "equation", "content": "x^2", "props": {}},
            {"type": "board", "content": "Tasks", "props": {"view": "board", "rows": []}},
            {"type": "timeline", "content": "Roadmap", "props": {"view": "timeline", "rows": []}},
            {"type": "chart", "content": "Metrics", "props": {"view": "chart", "rows": []}},
            {"type": "table_of_contents", "content": "", "props": {}},
            {"type": "breadcrumb", "content": "", "props": {}},
            {"type": "link_to_page", "content": "Parity page", "props": {"page_id": page_id}},
            {"type": "simple_table", "content": "", "props": {"rows": [["A", "B"]]}},
            {"type": "button", "content": "Add", "props": {"label": "Add", "action": "insert_block"}},
            {"id": "tabs_main", "type": "tabs", "content": "", "props": {"tabs": [{"id": "tab_one", "name": "One"}, {"id": "tab_two", "name": "Two"}], "active_tab_id": "tab_one"}},
            {"type": "paragraph", "content": "Inside tab", "props": {"tab_group": "tabs_main", "tab_id": "tab_one"}},
        ]
        saved = ns.replace_blocks(self.user_id, page_id, blocks)
        self.assertEqual([item["type"] for item in saved["blocks"]], [item["type"] for item in blocks])
        markdown = ns.blocks_to_markdown(saved["page"], saved["blocks"])
        round_trip_types = [item["type"] for item in ns.markdown_to_blocks(markdown)]
        self.assertTrue({"heading4", "toggle_heading1", "toggle_heading4", "breadcrumb", "link_to_page", "simple_table", "button", "tabs"}.issubset(round_trip_types))
        round_trip = ns.markdown_to_blocks(markdown)
        round_trip_tabs = next(item for item in round_trip if item["type"] == "tabs")
        tab_member = next(item for item in round_trip if item.get("props", {}).get("tab_group"))
        self.assertEqual(tab_member["content"], "Inside tab")
        self.assertEqual(tab_member["props"]["tab_group"], round_trip_tabs["id"])

        ns.update_page(self.user_id, page_id, {"settings": {"locked": True}})
        with self.assertRaises(PermissionError):
            ns.replace_blocks(self.user_id, page_id, [{"type": "paragraph", "content": "blocked", "props": {}}])

        unlocked = ns.update_page(self.user_id, page_id, {"settings": {"locked": False}})
        self.assertFalse(unlocked["page"]["settings"]["locked"])

        versions = ns.list_page_versions(self.user_id, page_id)
        self.assertGreaterEqual(len(versions), 3)
        oldest = versions[-1]
        restored = ns.restore_page_version(
            self.user_id, page_id, oldest["id"],
            expected_revision=ns.get_page(self.user_id, page_id)["page"]["revision"],
        )
        self.assertEqual(restored["page"]["title"], "Parity page")

    def test_version_restore_rejects_another_tabs_newer_edit(self) -> None:
        created = ns.create_page(self.user_id, title="Original")
        page_id = created["page"]["id"]
        old_version = ns.list_page_versions(self.user_id, page_id)[0]["id"]
        first_tab_revision = created["page"]["revision"]

        second_tab = ns.update_page(self.user_id, page_id, {"title": "Newer edit"})
        with self.assertRaises(ns.PageRevisionConflict) as conflict:
            ns.restore_page_version(
                self.user_id, page_id, old_version,
                expected_revision=first_tab_revision,
            )
        self.assertEqual(conflict.exception.current_revision, second_tab["page"]["revision"])
        self.assertEqual(ns.get_page(self.user_id, page_id)["page"]["title"], "Newer edit")

        restored = ns.restore_page_version(
            self.user_id, page_id, old_version,
            expected_revision=second_tab["page"]["revision"],
        )
        self.assertEqual(restored["page"]["title"], "Original")

    def test_nested_trash_and_comments(self) -> None:
        parent = ns.create_page(self.user_id, title="Parent")["page"]
        child = ns.create_page(self.user_id, title="Child", parent_id=parent["id"])["page"]

        archived = ns.archive_page(self.user_id, parent["id"])
        self.assertEqual(archived["archived_count"], 2)
        trashed_ids = {item["id"] for item in ns.list_archived_pages(self.user_id)}
        self.assertEqual(trashed_ids, {parent["id"], child["id"]})

        ns.restore_page(self.user_id, parent["id"])
        visible_ids = {item["id"] for item in ns.list_pages(self.user_id)}
        self.assertTrue({parent["id"], child["id"]}.issubset(visible_ids))

        ns.archive_page(self.user_id, parent["id"])
        ns.restore_page(self.user_id, child["id"])
        visible_ids = {item["id"] for item in ns.list_pages(self.user_id)}
        self.assertTrue({parent["id"], child["id"]}.issubset(visible_ids))

        comment = ns.add_comment(
            self.user_id,
            parent["id"],
            content="Review this",
            block_id="b_test",
            anchor={"start": 2, "end": 8, "text": "quoted", "context_before": "앞 문맥", "context_after": "뒤 문맥"},
        )
        self.assertEqual(comment["author_user_id"], self.user_id)
        stored_anchor = ns.list_comments(self.user_id, parent["id"])[0]["anchor"]
        self.assertEqual(stored_anchor["text"], "quoted")
        self.assertEqual(stored_anchor["context_before"], "앞 문맥")
        self.assertEqual(stored_anchor["context_after"], "뒤 문맥")
        reply = ns.add_comment(
            self.user_id,
            parent["id"],
            content="Reply",
            parent_id=comment["id"],
        )
        self.assertEqual(reply["parent_id"], comment["id"])
        self.assertEqual(reply["anchor"], stored_anchor)
        reacted = ns.toggle_comment_reaction(self.user_id, parent["id"], reply["id"], "👍")
        self.assertEqual(reacted["reactions"]["👍"], [self.user_id])
        comments = ns.list_comments(self.user_id, parent["id"])
        self.assertEqual(comments[0]["reply_count"], 1)
        self.assertEqual(comments[1]["id"], reply["id"])
        resolved = ns.update_comment(self.user_id, parent["id"], comment["id"], {"resolved": True})
        self.assertTrue(resolved["resolved"])
        self.assertEqual(len(ns.list_comments(self.user_id, parent["id"])), 2)
        ns.delete_comment(self.user_id, parent["id"], comment["id"])
        self.assertEqual(ns.list_comments(self.user_id, parent["id"]), [])

    def test_autosave_version_is_atomic_and_lock_blocks_children_and_uploads(self) -> None:
        created = ns.create_page(self.user_id, title="Before")
        page_id = created["page"]["id"]
        self.assertEqual(len(ns.list_page_versions(self.user_id, page_id)), 1)

        ns.update_page(self.user_id, page_id, {"title": "After", "_defer_version": True})
        self.assertEqual(len(ns.list_page_versions(self.user_id, page_id)), 1)
        ns.replace_blocks(
            self.user_id,
            page_id,
            [{"type": "paragraph", "content": "same autosave", "props": {}}],
        )
        versions = ns.list_page_versions(self.user_id, page_id)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["title"], "After")

        ns.update_page(self.user_id, page_id, {"settings": {"locked": True}})
        with self.assertRaises(PermissionError):
            ns.create_page(self.user_id, title="Blocked child", parent_id=page_id)
        with self.assertRaises(PermissionError):
            ns.upload_file(self.user_id, page_id, filename="blocked.txt", raw=b"blocked")

    def test_atomic_page_save_cas_idempotency_and_combined_version(self) -> None:
        created = ns.create_page(self.user_id, title="Before")
        page_id = created["page"]["id"]
        self.assertTrue(created["capabilities"]["atomic_save"])
        self.assertEqual(created["revision"], 1)
        self.assertEqual(created["page"]["revision"], 1)

        request = {
            "base_revision": 1,
            "operation_id": "save_once",
            "page": {
                "title": "After",
                "settings": {"font": "serif", "ignored": "not-persisted"},
            },
            "blocks": [{"id": "b_atomic", "type": "paragraph", "content": "Atomic", "props": {}}],
        }
        saved = ns.save_page_atomic(self.user_id, page_id, **request)
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["page"]["title"], "After")
        self.assertEqual(saved["page"]["settings"], {"font": "serif"})
        self.assertEqual(saved["blocks"][0]["content"], "Atomic")
        self.assertEqual(len(ns.list_page_versions(self.user_id, page_id)), 2)

        replay = ns.save_page_atomic(self.user_id, page_id, **request)
        self.assertEqual(replay, saved)
        self.assertEqual(ns.get_page(self.user_id, page_id)["revision"], 2)
        self.assertEqual(len(ns.list_page_versions(self.user_id, page_id)), 2)

        with self.assertRaises(ns.OperationIdConflict):
            ns.save_page_atomic(
                self.user_id,
                page_id,
                **{**request, "page": {"title": "Different"}},
            )
        with self.assertRaises(ns.PageRevisionConflict) as stale:
            ns.save_page_atomic(
                self.user_id,
                page_id,
                base_revision=1,
                operation_id="stale_save",
                page={"title": "Stale"},
                blocks=request["blocks"],
            )
        self.assertEqual(stale.exception.current_revision, 2)

    def test_atomic_save_validation_locking_and_legacy_revision_bumps(self) -> None:
        first = ns.create_page(self.user_id, title="First")
        second = ns.create_page(self.user_id, title="Second")
        first_id = first["page"]["id"]
        second_id = second["page"]["id"]
        with self.assertRaises(ValueError):
            ns.save_page_atomic(
                self.user_id, first_id, base_revision=True, operation_id="bad", page={}, blocks=[]
            )
        with self.assertRaises(ValueError):
            ns.save_page_atomic(
                self.user_id, first_id, base_revision=1, operation_id="", page={}, blocks=[]
            )

        updated = ns.update_page(self.user_id, first_id, {"title": "Changed"})
        self.assertEqual(updated["revision"], 2)
        same = ns.update_page(self.user_id, first_id, {"title": "Changed"})
        self.assertEqual(same["revision"], 2)
        synced_first = ns.replace_blocks(
            self.user_id,
            first_id,
            [{"id": "sync_a", "type": "synced_block", "content": "One", "props": {"sync_id": "shared"}}],
        )
        self.assertEqual(synced_first["revision"], 3)
        ns.replace_blocks(
            self.user_id,
            second_id,
            [{"id": "sync_b", "type": "synced_block", "content": "Two", "props": {"sync_id": "shared"}}],
        )
        self.assertEqual(ns.get_page(self.user_id, first_id)["revision"], 4)

        locked = ns.update_page(self.user_id, first_id, {"settings": {"locked": True}})
        with self.assertRaises(PermissionError):
            ns.save_page_atomic(
                self.user_id,
                first_id,
                base_revision=locked["revision"],
                operation_id="locked_save",
                page={"title": "Blocked"},
                blocks=locked["blocks"],
            )
        unlocked = ns.save_page_atomic(
            self.user_id,
            first_id,
            base_revision=locked["revision"],
            operation_id="unlock_save",
            page={"settings": {"locked": False}},
            blocks=locked["blocks"],
        )
        self.assertFalse(unlocked["page"]["settings"]["locked"])

    def test_existing_workspace_requires_explicit_atomic_save_migration(self) -> None:
        missing_user = "missing-legacy-user"
        missing_root = os.path.join(self.temp_dir.name, missing_user)
        with self.assertRaises(FileNotFoundError):
            ns.migrate_atomic_save_schema(missing_user)
        self.assertFalse(os.path.exists(missing_root))

        legacy_user = "legacy-user"
        legacy_root = os.path.join(self.temp_dir.name, legacy_user, "notion")
        os.makedirs(legacy_root, exist_ok=True)
        legacy_db = os.path.join(legacy_root, "notion.db")
        con = sqlite3.connect(legacy_db)
        con.execute(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, parent_id TEXT,
              title TEXT NOT NULL, icon TEXT, cover TEXT, settings_json TEXT,
              position INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
              archived_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )
            """
        )
        con.commit()
        con.close()

        page = ns.create_page(legacy_user, title="Legacy")
        page_id = page["page"]["id"]
        self.assertEqual(page["capabilities"], {"atomic_save": False, "personal_linked_database": True})
        self.assertNotIn("revision", page)
        self.assertNotIn("revision", page["page"])
        con = sqlite3.connect(legacy_db)
        try:
            self.assertNotIn("revision", {row[1] for row in con.execute("PRAGMA table_info(pages)")})
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("page_save_operations", tables)
            self.assertNotIn("notion_schema_migrations", tables)
        finally:
            con.close()
        with self.assertRaises(ns.AtomicSaveUnavailable):
            ns.save_page_atomic(
                legacy_user,
                page_id,
                base_revision=1,
                operation_id="before_migration",
                page={"title": "No"},
                blocks=page["blocks"],
            )
        con = sqlite3.connect(legacy_db)
        try:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("revision", {row[1] for row in con.execute("PRAGMA table_info(pages)")})
            self.assertNotIn("page_save_operations", tables)
        finally:
            con.close()

        migrated = ns.migrate_atomic_save_schema(legacy_user)
        self.assertTrue(migrated["atomic_save"])
        ready = ns.get_page(legacy_user, page_id)
        self.assertTrue(ready["capabilities"]["atomic_save"])
        self.assertEqual(ready["revision"], 1)
        self.assertEqual(ns.migrate_atomic_save_schema(legacy_user), migrated)

    def test_atomic_operation_ids_are_requester_scoped_and_replay_rechecks_access(self) -> None:
        owner_page = ns.create_page(self.user_id, title="Shared atomic")
        page_id = owner_page["page"]["id"]
        blocks = owner_page["blocks"]
        ns.share_page(self.user_id, page_id, "editor", role="edit")

        owner_result = ns.save_page_atomic(
            self.user_id,
            page_id,
            base_revision=1,
            operation_id="same_key",
            page={"title": "Owner saved"},
            blocks=blocks,
        )
        editor_result = ns.save_page_atomic(
            "editor",
            page_id,
            base_revision=owner_result["revision"],
            operation_id="same_key",
            page={"title": "Owner saved"},
            blocks=blocks,
        )
        self.assertEqual(editor_result["page"]["role"], "edit")
        self.assertEqual(editor_result["revision"], owner_result["revision"])

        ns.revoke_share(self.user_id, page_id, "editor")
        with self.assertRaises(FileNotFoundError):
            ns.save_page_atomic(
                "editor",
                page_id,
                base_revision=owner_result["revision"],
                operation_id="same_key",
                page={"title": "Owner saved"},
                blocks=blocks,
            )

    def test_subpages_inherit_the_nearest_share_permission(self) -> None:
        parent = ns.create_page(self.user_id, title="Shared parent")["page"]
        child = ns.create_page(self.user_id, title="Inherited child", parent_id=parent["id"])["page"]
        grandchild = ns.create_page(self.user_id, title="Inherited grandchild", parent_id=child["id"])["page"]

        ns.share_page(self.user_id, parent["id"], "viewer", role="edit")
        shared = {item["id"]: item for item in ns.list_pages("viewer")}
        self.assertEqual(set(shared), {parent["id"], child["id"], grandchild["id"]})
        self.assertTrue(shared[grandchild["id"]]["can_edit"])
        self.assertEqual(ns.get_page("viewer", grandchild["id"])["page"]["role"], "edit")

        editor_child = ns.create_page(
            "viewer",
            title="Created by editor",
            parent_id=parent["id"],
            add_to_parent_blocks=True,
        )["page"]
        self.assertEqual(editor_child["owner_user_id"], self.user_id)
        self.assertEqual(editor_child["role"], "edit")
        parent_blocks = ns.get_page("viewer", parent["id"])["blocks"]
        self.assertEqual(parent_blocks[-1]["type"], "child_page")
        self.assertEqual(parent_blocks[-1]["props"]["page_id"], editor_child["id"])

        ns.share_page(self.user_id, child["id"], "viewer", role="view")
        shared = {item["id"]: item for item in ns.list_pages("viewer")}
        self.assertEqual(shared[parent["id"]]["role"], "edit")
        self.assertEqual(shared[child["id"]]["role"], "view")
        self.assertEqual(shared[grandchild["id"]]["role"], "view")
        self.assertEqual(shared[editor_child["id"]]["role"], "edit")

    def test_moving_subpage_updates_parent_block_references(self) -> None:
        first = ns.create_page(self.user_id, title="First parent")["page"]
        second = ns.create_page(self.user_id, title="Second parent")["page"]
        child = ns.create_page(
            self.user_id,
            title="Child",
            parent_id=first["id"],
            add_to_parent_blocks=True,
        )["page"]

        ns.move_page(self.user_id, child["id"], parent_id=second["id"])
        first_refs = [
            block for block in ns.get_page(self.user_id, first["id"])["blocks"]
            if block["type"] == "child_page" and block["props"].get("page_id") == child["id"]
        ]
        second_refs = [
            block for block in ns.get_page(self.user_id, second["id"])["blocks"]
            if block["type"] == "child_page" and block["props"].get("page_id") == child["id"]
        ]
        self.assertEqual(first_refs, [])
        self.assertEqual(len(second_refs), 1)

        sibling = ns.create_page(
            self.user_id,
            title="Sibling",
            parent_id=second["id"],
            add_to_parent_blocks=True,
        )["page"]
        ns.move_page(self.user_id, child["id"], after_id=sibling["id"])
        ordered_refs = [
            block["props"].get("page_id") for block in ns.get_page(self.user_id, second["id"])["blocks"]
            if block["type"] == "child_page"
        ]
        self.assertEqual(ordered_refs, [sibling["id"], child["id"]])

    def test_archived_and_custom_database_page_references_are_hydrated_per_block(self) -> None:
        parent = ns.create_page(self.user_id, title="Databases")["page"]
        first = ns.create_page(self.user_id, title="First row", parent_id=parent["id"])["page"]
        second = ns.create_page(self.user_id, title="Second row", parent_id=parent["id"])["page"]
        ns.replace_blocks(
            self.user_id,
            parent["id"],
            [
                {
                    "type": "table",
                    "content": "One",
                    "props": {
                        "properties": [{"id": "name_a", "name": "Name A", "type": "title"}],
                        "rows": [{"name_a": "stale", "page_id": first["id"]}],
                    },
                },
                {
                    "type": "chart",
                    "content": "Two",
                    "props": {
                        "properties": [{"id": "name_b", "name": "Name B", "type": "title"}],
                        "rows": [{"name_b": "stale", "page_id": second["id"]}],
                    },
                },
                {"type": "child_page", "content": "First row", "props": {"page_id": first["id"]}},
            ],
        )
        ns.archive_page(self.user_id, first["id"])
        blocks = ns.get_page(self.user_id, parent["id"])["blocks"]
        self.assertEqual(blocks[0]["props"]["rows"][0]["name_a"], "First row")
        self.assertTrue(blocks[0]["props"]["rows"][0]["archived"])
        self.assertEqual(blocks[1]["props"]["rows"][0]["name_b"], "Second row")
        self.assertFalse(blocks[1]["props"]["rows"][0]["archived"])
        self.assertTrue(blocks[2]["props"]["archived"])

    def test_mentions_and_inline_or_database_reminders_feed_access_scoped_inbox(self) -> None:
        page = ns.create_page(self.user_id, title="Notifications")["page"]
        due_at = (datetime.now() - timedelta(minutes=2)).isoformat(timespec="minutes")
        upcoming_at = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
        ns.replace_blocks(
            self.user_id,
            page["id"],
            [
                {
                    "type": "paragraph",
                    "content": "Viewer please review tomorrow",
                    "props": {
                        "rich_text_html": (
                            '<span data-notion-user="viewer" data-notion-mention-id="mention_one">@Viewer</span> '
                            f'<span data-notion-reminder="{due_at}" data-notion-reminder-id="inline_one" '
                            'data-notion-reminder-user="viewer" data-notion-reminder-lead="0">reminder</span>'
                        ),
                    },
                },
                {
                    "type": "table",
                    "content": "Tasks",
                    "props": {
                        "properties": [
                            {"id": "name", "name": "Name", "type": "title"},
                            {"id": "due", "name": "Due", "type": "date"},
                        ],
                        "rows": [{
                            "name": "Upcoming task",
                            "due": upcoming_at[:10],
                            "due_reminder": {
                                "id": "database_one",
                                "at": upcoming_at,
                                "lead": 0,
                                "user_id": "viewer",
                            },
                        }],
                    },
                },
            ],
        )
        ns.share_page(self.user_id, page["id"], "viewer", role="view")
        inbox = ns.list_inbox_items("viewer")
        self.assertEqual({item["id"] for item in inbox}, {
            "mention_mention_one", "reminder_inline_one", "reminder_database_one",
        })
        self.assertTrue(next(item for item in inbox if item["id"] == "reminder_inline_one")["due"])
        self.assertFalse(next(item for item in inbox if item["id"] == "reminder_database_one")["due"])
        self.assertEqual(ns.list_inbox_items("unshared-user"), [])

    def test_duplicate_copies_subtree_and_remaps_page_and_file_references(self) -> None:
        parent = ns.create_page(self.user_id, title="Original")["page"]
        child = ns.create_page(self.user_id, title="Child", parent_id=parent["id"])["page"]
        uploaded = ns.upload_file(
            self.user_id,
            parent["id"],
            filename="note.txt",
            raw=b"duplicate me",
            insert_block=False,
        )
        ns.replace_blocks(
            self.user_id,
            parent["id"],
            [
                {"type": "child_page", "content": "Child", "props": {"page_id": child["id"]}},
                {"type": "file", "content": "note.txt", "props": {"file_id": uploaded["id"]}},
            ],
        )

        duplicate = ns.duplicate_page(self.user_id, parent["id"])
        duplicate_id = duplicate["page"]["id"]
        copied_children = [item for item in ns.list_pages(self.user_id) if item["parent_id"] == duplicate_id]
        self.assertEqual(len(copied_children), 1)
        self.assertNotEqual(copied_children[0]["id"], child["id"])
        self.assertEqual(duplicate["blocks"][0]["props"]["page_id"], copied_children[0]["id"])
        copied_file_id = duplicate["blocks"][1]["props"]["file_id"]
        self.assertNotEqual(copied_file_id, uploaded["id"])
        with open(ns.get_file(self.user_id, copied_file_id)["path"], "rb") as copied_file:
            self.assertEqual(copied_file.read(), b"duplicate me")

    def test_calendar_events_are_aggregated_from_database_views(self) -> None:
        page = ns.create_page(self.user_id, title="Team calendar")["page"]
        event_date = (date.today() + timedelta(days=2)).isoformat()
        ns.replace_blocks(
            self.user_id,
            page["id"],
            [{
                "type": "calendar",
                "content": "Schedule",
                "props": {
                    "properties": [
                        {"id": "name_custom", "name": "Name", "type": "title"},
                        {"id": "due_custom", "name": "Due", "type": "date"},
                        {"id": "state_custom", "name": "State", "type": "status"},
                    ],
                    "rows": [
                        {"name_custom": "Demo", "due_custom": event_date, "state_custom": "Planned"},
                        {"name_custom": "Range", "due_custom": {"start": event_date, "end": (date.today() + timedelta(days=4)).isoformat()}, "state_custom": "Active"},
                    ],
                },
            }],
        )
        events = ns.list_calendar_events(self.user_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "Demo")
        self.assertEqual(events[0]["status"], "Planned")
        self.assertEqual(events[0]["source_page_id"], page["id"])
        self.assertEqual(events[1]["title"], "Range")

    def test_permanent_delete_removes_attachment_bytes(self) -> None:
        page = ns.create_page(self.user_id, title="Delete me")["page"]
        uploaded = ns.upload_file(self.user_id, page["id"], filename="secret.bin", raw=b"gone")
        file_path = ns.get_file(self.user_id, uploaded["id"])["path"]
        self.assertTrue(os.path.isfile(file_path))
        ns.archive_page(self.user_id, page["id"])
        result = ns.delete_page_permanent(self.user_id, page["id"])
        self.assertEqual(result["deleted_file_count"], 1)
        self.assertFalse(os.path.exists(file_path))

    def test_page_mentions_produce_access_scoped_backlinks(self) -> None:
        target = ns.create_page(self.user_id, title="Target")["page"]
        source = ns.create_page(self.user_id, title="Source")["page"]
        ns.replace_blocks(
            self.user_id,
            source["id"],
            [{
                "type": "paragraph",
                "content": "Target",
                "props": {"rich_text_html": f'<a href="/page/{target["id"]}">Target</a>'},
            }],
        )
        backlinks = ns.list_backlinks(self.user_id, target["id"])
        self.assertEqual(len(backlinks), 1)
        self.assertEqual(backlinks[0]["id"], source["id"])
        self.assertEqual(backlinks[0]["match_count"], 1)

    def test_synced_blocks_propagate_across_pages(self) -> None:
        first = ns.create_page(self.user_id, title="First")["page"]
        second = ns.create_page(self.user_id, title="Second")["page"]
        ns.replace_blocks(
            self.user_id,
            first["id"],
            [{"type": "synced_block", "content": "Initial", "props": {"sync_id": "sync_shared"}}],
        )
        ns.replace_blocks(
            self.user_id,
            second["id"],
            [{"type": "synced_block", "content": "Second wins", "props": {"sync_id": "sync_shared"}}],
        )
        self.assertEqual(ns.get_page(self.user_id, first["id"])["blocks"][0]["content"], "Second wins")
        ns.replace_blocks(
            self.user_id,
            first["id"],
            [{"type": "synced_block", "content": "Updated everywhere", "props": {"sync_id": "sync_shared"}}],
        )
        self.assertEqual(ns.get_page(self.user_id, second["id"])["blocks"][0]["content"], "Updated everywhere")

    def test_authenticated_and_public_form_submissions_create_database_pages(self) -> None:
        page = ns.create_page(self.user_id, title="Responses")["page"]
        saved = ns.replace_blocks(
            self.user_id,
            page["id"],
            [{
                "type": "form",
                "content": "Contact form",
                "props": {
                    "view": "form",
                    "form_title": "Contact us",
                    "form_required_properties": ["name", "email"],
                    "form_success_message": "Thanks",
                    "properties": [
                        {"id": "name", "name": "Name", "type": "title"},
                        {"id": "email", "name": "Email", "type": "email"},
                        {"id": "status", "name": "Status", "type": "status"},
                        {"id": "assignee", "name": "Assignee", "type": "person"},
                    ],
                    "property_visibility": {"email": True, "status": True, "assignee": True},
                    "rows": [],
                },
            }],
        )
        block_id = saved["blocks"][0]["id"]
        submitted = ns.submit_form(
            self.user_id,
            page["id"],
            block_id,
            {"name": "Alice", "email": "alice@example.com", "status": "진행 중", "assignee": self.user_id},
        )
        self.assertEqual(submitted["message"], "Thanks")
        self.assertEqual(submitted["row"]["name"], "Alice")
        self.assertEqual(submitted["row"]["assignee"], self.user_id)
        child = ns.get_page(self.user_id, submitted["database_page_id"])["page"]
        self.assertEqual(child["parent_id"], page["id"])
        with self.assertRaises(ValueError):
            ns.submit_form(self.user_id, page["id"], block_id, {"name": "Missing email"})

        public = ns.enable_public_link(self.user_id, page["id"])
        public_submission = ns.submit_public_form(
            public["token"],
            block_id,
            {"name": "Public", "email": "public@example.com", "status": "완료", "assignee": "forged"},
        )
        self.assertNotIn("assignee", public_submission["row"])
        rows = ns.get_page(self.user_id, page["id"])["blocks"][0]["props"]["rows"]
        self.assertEqual([item["name"] for item in rows], ["Alice", "Public"])

    def test_public_page_duplicate_copies_private_tree_files_and_internal_refs(self) -> None:
        source_owner = "publisher"
        target_owner = "reader"
        root = ns.create_page(source_owner, title="Template")["page"]
        child = ns.create_page(source_owner, title="Nested", parent_id=root["id"])["page"]
        uploaded = ns.upload_file(
            source_owner,
            root["id"],
            filename="brief.txt",
            raw=b"template attachment",
            mime_type="text/plain",
            insert_block=False,
        )
        ns.replace_blocks(source_owner, child["id"], [{"type": "paragraph", "content": "Nested content", "props": {}}])
        ns.replace_blocks(
            source_owner,
            root["id"],
            [
                {
                    "id": "db_source",
                    "type": "table",
                    "content": "Projects",
                    "props": {
                        "view": "table",
                        "properties": [
                            {"id": "title", "name": "Name", "type": "title"},
                            {"id": "files", "name": "Files", "type": "files"},
                        ],
                        "rows": [{"title": "Nested", "page_id": child["id"], "files": [{"file_id": uploaded["id"], "name": "brief.txt"}]}],
                    },
                },
                {
                    "id": "db_relation",
                    "type": "table",
                    "content": "Relations",
                    "props": {
                        "view": "table",
                        "properties": [
                            {"id": "title", "name": "Name", "type": "title"},
                            {
                                "id": "related",
                                "name": "Related",
                                "type": "relation",
                                "relation_database_block_id": "db_source",
                                "relation_multiple": True,
                            },
                        ],
                        "rows": [{"title": "Link", "related": [child["id"]]}],
                    },
                },
                {"type": "child_page", "content": "Nested", "props": {"page_id": child["id"]}},
            ],
        )
        ns.update_page(source_owner, root["id"], {"settings": {"locked": True}})
        public_link = ns.enable_public_link(source_owner, root["id"])
        ns.update_public_link_options(source_owner, root["id"], allow_duplicate=False)
        with self.assertRaises(PermissionError):
            ns.duplicate_public_page(target_owner, public_link["token"])

        ns.update_public_link_options(source_owner, root["id"], allow_duplicate=True)
        duplicated = ns.duplicate_public_page(target_owner, public_link["token"])
        copied_root = duplicated["page"]
        self.assertEqual(copied_root["owner_user_id"], target_owner)
        self.assertEqual(copied_root["title"], "Template")
        self.assertFalse(copied_root["settings"]["locked"])
        self.assertFalse(copied_root["parent_id"])
        copied_child = next(item for item in ns.list_pages(target_owner) if item["parent_id"] == copied_root["id"])
        self.assertEqual(copied_child["title"], "Nested")

        copied_source = next(block for block in duplicated["blocks"] if block["content"] == "Projects")
        copied_relation = next(block for block in duplicated["blocks"] if block["content"] == "Relations")
        relation_property = next(item for item in copied_relation["props"]["properties"] if item["id"] == "related")
        self.assertEqual(relation_property["relation_database_block_id"], copied_source["id"])
        self.assertEqual(copied_relation["props"]["rows"][0]["related"], [copied_child["id"]])
        self.assertEqual(copied_source["props"]["rows"][0]["page_id"], copied_child["id"])
        copied_file_id = copied_source["props"]["rows"][0]["files"][0]["file_id"]
        self.assertNotEqual(copied_file_id, uploaded["id"])
        with open(ns.get_file(target_owner, copied_file_id)["path"], "rb") as copied_file:
            self.assertEqual(copied_file.read(), b"template attachment")

    def test_shared_and_public_reference_hydration_never_leaks_private_sibling_metadata(self) -> None:
        owner = "reference-owner"
        viewer = "reference-viewer"
        root = ns.create_page(owner, title="Shared root")["page"]
        child = ns.create_page(owner, title="Visible child", parent_id=root["id"])["page"]
        secret = ns.create_page(owner, title="TOP SECRET sibling", icon="🔒")["page"]
        ns.replace_blocks(
            owner,
            root["id"],
            [
                {
                    "type": "link_to_page",
                    "content": "TOP SECRET sibling",
                    "props": {
                        "page_id": secret["id"],
                        "title": "TOP SECRET sibling",
                        "icon": "🔒",
                    },
                },
                {
                    "type": "child_page",
                    "content": "Visible child",
                    "props": {"page_id": child["id"]},
                },
                {
                    "type": "table",
                    "content": "References",
                    "props": {
                        "properties": [{"id": "title", "name": "Name", "type": "title"}],
                        "rows": [
                            {
                                "page_id": secret["id"],
                                "title": "TOP SECRET sibling",
                                "icon": "🔒",
                            },
                            {"page_id": child["id"], "title": "stale child title"},
                        ],
                    },
                },
                {
                    "type": "table",
                    "content": "Relations",
                    "props": {
                        "properties": [
                            {"id": "title", "name": "Name", "type": "title"},
                            {"id": "related", "name": "Related", "type": "relation"},
                        ],
                        "rows": [{"title": "External", "related": [secret["id"]]}],
                    },
                },
            ],
        )

        ns.share_page(owner, root["id"], viewer, role="view")
        shared = ns.get_page(viewer, root["id"])
        shared_text = json.dumps(shared, ensure_ascii=False)
        self.assertNotIn("TOP SECRET", shared_text)
        self.assertNotIn("🔒", shared_text)
        self.assertIn("Unavailable page", shared_text)
        self.assertIn("Visible child", shared_text)

        public_link = ns.enable_public_link(owner, root["id"])
        public = ns.resolve_public_page(public_link["token"])
        public_text = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("TOP SECRET", public_text)
        self.assertNotIn("🔒", public_text)
        self.assertIn("Unavailable page", public_text)
        self.assertIn("Visible child", public_text)

        duplicated = ns.duplicate_public_page(viewer, public_link["token"])
        duplicated_text = json.dumps(duplicated, ensure_ascii=False)
        self.assertNotIn("TOP SECRET", duplicated_text)
        self.assertNotIn(secret["id"], duplicated_text)
        duplicated_relation = next(
            block for block in duplicated["blocks"] if block["content"] == "Relations"
        )
        self.assertEqual(duplicated_relation["props"]["rows"][0]["related"], [])

        ns.share_page(owner, secret["id"], viewer, role="view")
        now_accessible = ns.get_page(viewer, root["id"])
        rows = next(
            block for block in now_accessible["blocks"] if block["type"] == "table"
        )["props"]["rows"]
        self.assertEqual(rows[0]["title"], "TOP SECRET sibling")
        self.assertFalse(rows[0].get("inaccessible", False))


if __name__ == "__main__":
    unittest.main()
