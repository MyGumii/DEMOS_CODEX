from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from code_assist_v1 import test_safety
from code_assist_v1.test_safety import (
    UnsafeTestDataPathError,
    assert_isolated_test_path,
    create_isolated_test_layout,
)


class _TempRootsOnlyTestCase(unittest.TestCase):
    """Prevent tests from resolving or inspecting the repository's live roots."""

    def setUp(self) -> None:
        super().setUp()
        protected_patcher = patch.object(test_safety, "_PROTECTED_ROOTS", ())
        protected_patcher.start()
        self.addCleanup(protected_patcher.stop)


class TestIsolatedPathGuard(_TempRootsOnlyTestCase):
    def test_requires_explicit_test_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for value in (None, "", "0", "false", "off"):
                environment = {"CODE_ASSIST_TEST_DATA_ROOT": str(root)}
                if value is not None:
                    environment["CODE_ASSIST_TEST_MODE"] = value
                with self.subTest(value=value), patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(UnsafeTestDataPathError):
                        assert_isolated_test_path(root)

    def test_requires_an_absolute_root_and_candidate(self) -> None:
        with patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "true"}, clear=True):
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(UnsafeTestDataPathError):
                    assert_isolated_test_path(Path(temp).resolve())

            with self.assertRaises(UnsafeTestDataPathError):
                assert_isolated_test_path(Path("relative-child"), test_root=Path("relative-root"))

            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(UnsafeTestDataPathError):
                    assert_isolated_test_path(
                        Path("relative-child"),
                        test_root=Path(temp).resolve(),
                    )

    def test_environment_root_allows_root_and_descendant_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            root = parent / "isolated"
            sibling = parent / "sibling"
            environment = {
                "CODE_ASSIST_TEST_MODE": "YeS",
                "CODE_ASSIST_TEST_DATA_ROOT": str(root),
            }
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(assert_isolated_test_path(root), root)
                self.assertEqual(assert_isolated_test_path(root / "nested" / "file.db"), root / "nested" / "file.db")
                with self.assertRaises(UnsafeTestDataPathError):
                    assert_isolated_test_path(sibling)

    def test_explicit_root_works_without_environment_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "explicit"
            with patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "on"}, clear=True):
                self.assertEqual(
                    assert_isolated_test_path(root / "child", test_root=root),
                    root / "child",
                )

    def test_filesystem_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            anchor = Path(Path(temp).resolve().anchor)
            with patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "1"}, clear=True):
                with self.assertRaises(UnsafeTestDataPathError):
                    assert_isolated_test_path(anchor, test_root=anchor)

    def test_protected_path_overlap_is_rejected_in_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            protected = parent / "simulated-live" / "user_data"
            descendant = protected / "fixture"
            ancestor = parent / "simulated-live"
            with (
                patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "true"}, clear=True),
                patch.object(test_safety, "_PROTECTED_ROOTS", (protected,)),
            ):
                with self.assertRaises(UnsafeTestDataPathError):
                    assert_isolated_test_path(descendant, test_root=descendant)
                with self.assertRaises(UnsafeTestDataPathError):
                    assert_isolated_test_path(ancestor, test_root=ancestor)

    def test_resolved_symlink_or_reparse_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            root = parent / "root"
            outside = parent / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "escape"

            real_resolve = test_safety._resolve

            def simulated_reparse_resolve(path: Path, *, label: str) -> Path:
                if path == link / "child":
                    return outside / "child"
                return real_resolve(path, label=label)

            with (
                patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "true"}, clear=True),
                patch.object(test_safety, "_resolve", side_effect=simulated_reparse_resolve),
            ):
                with self.assertRaises(UnsafeTestDataPathError):
                    assert_isolated_test_path(link / "child", test_root=root)


class TestIsolatedLayout(_TempRootsOnlyTestCase):
    def test_creates_complete_layout_and_no_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "layout"
            with patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "1"}, clear=True):
                layout = create_isolated_test_layout(root)

            expected_directories = {
                layout.root,
                layout.runtime_dir,
                layout.knowledge_dir,
                layout.user_data_dir,
                layout.sessions_dir,
                layout.workspace_dir,
                layout.core_db_parent,
                layout.blobs_dir,
                layout.projections_dir,
                layout.notion_dir,
            }
            self.assertTrue(all(path.is_dir() for path in expected_directories))
            self.assertEqual(layout.notion_dir, root / "user_data" / "fixture-user" / "notion")
            self.assertEqual(layout.core_db_path, root / "core" / "knowledge-core.db")
            self.assertFalse(layout.core_db_path.exists())
            with self.assertRaises(FrozenInstanceError):
                layout.root = root / "other"  # type: ignore[misc]

    def test_rejects_unsafe_user_id_before_creating_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "layout"
            with patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "1"}, clear=True):
                with self.assertRaises(UnsafeTestDataPathError):
                    create_isolated_test_layout(root, user_id="../escape")
            self.assertFalse(root.exists())

    def test_preexisting_reparse_child_cannot_redirect_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            root = parent / "layout"
            outside = parent / "outside"
            outside.mkdir()

            real_resolve = test_safety._resolve

            def simulated_reparse_resolve(path: Path, *, label: str) -> Path:
                if path == root / "user_data":
                    return outside
                if path == root / "user_data" / "fixture-user" / "notion":
                    return outside / "fixture-user" / "notion"
                return real_resolve(path, label=label)

            with (
                patch.dict(os.environ, {"CODE_ASSIST_TEST_MODE": "1"}, clear=True),
                patch.object(test_safety, "_resolve", side_effect=simulated_reparse_resolve),
            ):
                with self.assertRaises(UnsafeTestDataPathError):
                    create_isolated_test_layout(root)
            self.assertFalse(root.exists())
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
