"""Focused scanner contracts: exclusion, real import edges and stable metadata."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = spec_from_file_location("repository_scanner", Path(__file__).resolve().parents[1] / "scripts" / "scan_repository.py")
scanner = module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


class ScannerContracts(unittest.TestCase):
    def fixture(self, root, name, content):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_sensitive_runtime_dependency_and_self_output_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, "demos_v1/core.py", "def public_function():\n    return 1\n")
            self.fixture(root, "demos_v1/templates/index.html", "<html><body>App</body></html>\n")
            for hidden in ("report.html", "report.htm", "TOKEN.TXT", "api_config.json", "users.json", "rendering/evil.py", "scientific-skills/tool.py", "demos_v1/user_data/private.py", "demos_v1/.private/key.py", "demos_v1/tmp_test/temp.py", "demos_v1/node_modules/vendor.js"):
                self.fixture(root, hidden, "THIS_MUST_NEVER_BE_EMITTED")
            result = scanner.scan(root)
            self.assertEqual([f["id"] for f in result["files"]], ["demos_v1/core.py", "demos_v1/templates/index.html"])
            self.assertNotIn("THIS_MUST_NEVER_BE_EMITTED", json.dumps(result))

    def test_python_absolute_relative_and_package_imports_resolve_with_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, "demos_v1/__init__.py", "from . import child\n")
            self.fixture(root, "demos_v1/child.py", "class Engine:\n    pass\n")
            self.fixture(root, "demos_v1/entry.py", "from demos_v1 import child\nimport pathlib\nfrom .child import Engine\n")
            result = scanner.scan(root)
            edges = result["edges"]
            self.assertIn({"source": "demos_v1/entry.py", "target": "demos_v1/child.py", "kind": "import", "line": 3, "confidence": "parsed"}, edges)
            self.assertIn({"source": "demos_v1/__init__.py", "target": "demos_v1/child.py", "kind": "import", "line": 1, "confidence": "parsed"}, edges)
            self.assertIn({"source": "demos_v1/entry.py", "specifier": "pathlib", "line": 2}, result["unresolved"])
            child = next(f for f in result["files"] if f["name"] == "child.py")
            self.assertEqual(child["symbols"], [{"name": "Engine", "kind": "class", "line": 1}])

    def test_js_relative_imports_are_heuristic_and_comments_keep_line_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, "hermes-web/static/index.js", "/* comment\ncomment */\nimport { go } from './control.js';\nimport x from 'external';\n")
            self.fixture(root, "hermes-web/static/control.js", "export function go() {}\n")
            result = scanner.scan(root)
            self.assertEqual(result["edges"], [{"source": "hermes-web/static/index.js", "target": "hermes-web/static/control.js", "kind": "import", "line": 3, "confidence": "heuristic"}])
            self.assertEqual(result["unresolved"][0]["specifier"], "external")

    def test_harness_import_root_resolves_across_repository_systems(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, "harness-mvp/harness/__init__.py", "class Tool:\n    pass\n")
            for source in ("harness_bridge.py", "code_assist_v1/harness_setup.py", "demos_v1/routes_chat.py"):
                self.fixture(root, source, "from harness import Tool\n")
            result = scanner.scan(root)
            target = "harness-mvp/harness/__init__.py"
            self.assertEqual({e["source"] for e in result["edges"] if e["target"] == target},
                             {"harness_bridge.py", "code_assist_v1/harness_setup.py", "demos_v1/routes_chat.py"})
            self.assertFalse(any(u["specifier"] == "harness" for u in result["unresolved"]))

    def test_top_level_and_nested_links_do_not_traverse_outside_root(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root, external = Path(directory), Path(outside)
            self.fixture(external, "private.py", "OUTSIDE_CONTENT_MUST_NEVER_APPEAR")
            self.fixture(root, "demos_v1/ordinary.py", "pass\n")
            try:
                (root / "app.py").symlink_to(external / "private.py")
                (root / "code_assist_v1").symlink_to(external, target_is_directory=True)
                (root / "demos_v1" / "linked.py").symlink_to(external / "private.py")
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Host does not permit creating symbolic links: {error}")
            result = scanner.scan(root)
            self.assertEqual([f["id"] for f in result["files"]], ["demos_v1/ordinary.py"])
            self.assertNotIn("OUTSIDE_CONTENT_MUST_NEVER_APPEAR", json.dumps(result))

    def test_platform_classified_links_are_excluded_before_reading(self):
        # Exercise traversal on Windows hosts where creating a real symlink
        # needs administrator privilege. The files exist; only the filesystem
        # link classification is substituted, so normal traversal still runs.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in ("app.py", "code_assist_v1/private.py", "demos_v1/linked.py"):
                self.fixture(root, source, "CONTENT_BEHIND_LINK_MUST_NOT_BE_READ")
            self.fixture(root, "demos_v1/ordinary.py", "pass\n")
            with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda p: p.name in {"app.py", "linked.py"}), \
                 patch.object(Path, "is_junction", autospec=True, side_effect=lambda p: p.name == "code_assist_v1"):
                result = scanner.scan(root)
            self.assertEqual([f["id"] for f in result["files"]], ["demos_v1/ordinary.py"])
            self.assertEqual(result["summary"]["readErrors"], [])

    def test_repeated_scan_order_counts_and_paths_are_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, "app.py", "import demos_v1\n\n")
            self.fixture(root, "demos_v1/__init__.py", "# package\n")
            a, b = scanner.scan(root), scanner.scan(root)
            a.pop("generatedAt")
            b.pop("generatedAt")
            self.assertEqual(a, b)
            self.assertEqual(a["summary"]["totalLines"], 3)
            ids = {f["id"] for f in a["files"]}
            self.assertTrue(all(e["source"] in ids and e["target"] in ids for e in a["edges"]))


if __name__ == "__main__":
    unittest.main()
