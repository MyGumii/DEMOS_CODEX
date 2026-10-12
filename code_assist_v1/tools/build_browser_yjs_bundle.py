"""Build the pinned Yjs CommonJS graph into one browser-safe global bundle.

The selected collaboration runtime already installs the exact packages from
``collaboration_runtime/node/package-lock.json``.  This builder intentionally
has no npm build-tool dependency: it resolves the package export map, wraps the
static CommonJS graph, and exposes only that graph as ``globalThis.NotionYjs``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Sequence


REQUIRE_RE = re.compile(r"require\((['\"])([^'\"]+)\1\)")
LICENSE_BANNER = """/*!
 * Notion collaboration browser runtime.
 * Yjs 13.6.32 and lib0 0.2.117 are bundled from the repository's pinned
 * collaboration runtime. Both are MIT licensed; see yjs-lib0.LICENSE.txt.
 */
"""


class BundleError(RuntimeError):
    """The pinned module graph cannot be reproduced safely."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_target(package_root: Path, subpath: str) -> Path:
    package = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    exports = package.get("exports", {})
    key = "." if not subpath else f"./{subpath}"
    target = exports.get(key)
    if isinstance(target, dict):
        browser = target.get("browser")
        if isinstance(browser, dict):
            target = browser.get("require") or browser.get("default")
        elif isinstance(browser, str):
            target = browser
        else:
            target = target.get("require") or target.get("default")
    if not isinstance(target, str) or not target.startswith("./"):
        raise BundleError(f"browser CommonJS export is unavailable: {package_root.name}/{subpath}")
    resolved = (package_root / target).resolve(strict=True)
    if package_root.resolve() not in resolved.parents:
        raise BundleError("package export escaped its package root")
    return resolved


def _resolve(request: str, parent: Path, node_modules: Path) -> Path:
    if request.startswith("."):
        candidate = (parent.parent / request).resolve(strict=False)
        for value in (candidate, candidate.with_suffix(".cjs"), candidate / "index.cjs"):
            if value.is_file():
                return value.resolve()
        raise BundleError(f"relative CommonJS dependency is missing: {request}")
    package_name, slash, subpath = request.partition("/")
    if package_name.startswith("@"):
        scope_tail, slash, remainder = subpath.partition("/")
        if not slash:
            raise BundleError(f"scoped package request is invalid: {request}")
        package_name = f"{package_name}/{scope_tail}"
        subpath = remainder
    package_root = (node_modules / package_name).resolve(strict=True)
    if package_root.parent != node_modules.resolve() and package_root.parent.parent != node_modules.resolve():
        raise BundleError("package dependency escaped node_modules")
    return _package_target(package_root, subpath if slash else "")


def _module_id(path: Path, node_modules: Path) -> str:
    return path.resolve().relative_to(node_modules.resolve()).as_posix()


def build_bundle(node_runtime: Path) -> str:
    runtime = node_runtime.resolve(strict=True)
    lock = runtime / "package-lock.json"
    node_modules = runtime / "node_modules"
    entry = node_modules / "yjs" / "dist" / "yjs.cjs"
    if not lock.is_file() or not entry.is_file():
        raise BundleError("pinned node runtime is incomplete")
    lock_document = json.loads(lock.read_text(encoding="utf-8"))
    packages = lock_document.get("packages", {})
    if packages.get("node_modules/yjs", {}).get("version") != "13.6.32":
        raise BundleError("unexpected Yjs version")
    if packages.get("node_modules/lib0", {}).get("version") != "0.2.117":
        raise BundleError("unexpected lib0 version")

    pending = [entry.resolve()]
    sources: dict[str, str] = {}
    dependencies: dict[str, dict[str, str]] = {}
    while pending:
        path = pending.pop()
        module_id = _module_id(path, node_modules)
        if module_id in sources:
            continue
        source = path.read_text(encoding="utf-8")
        mapping: dict[str, str] = {}
        for _quote, request in REQUIRE_RE.findall(source):
            resolved = _resolve(request, path, node_modules)
            dependency_id = _module_id(resolved, node_modules)
            mapping[request] = dependency_id
            pending.append(resolved)
        sources[module_id] = source
        dependencies[module_id] = mapping

    entry_id = _module_id(entry, node_modules)
    modules = []
    for module_id in sorted(sources):
        modules.append(
            f"{json.dumps(module_id)}: function(module, exports, require) {{\n"
            f"{sources[module_id]}\n"
            "}"
        )
    metadata = {
        "contract": "code_assist.notion.browser_yjs_bundle.v1",
        "entry": entry_id,
        "module_count": len(modules),
        "package_lock_sha256": f"sha256:{_sha256(lock)}",
        "yjs_version": "13.6.32",
        "lib0_version": "0.2.117",
    }
    return (
        LICENSE_BANNER
        + "(function (root) {\n"
        + "  'use strict';\n"
        + f"  const metadata = Object.freeze({json.dumps(metadata, sort_keys=True, separators=(',', ':'))});\n"
        + "  const factories = {\n    "
        + ",\n    ".join(modules)
        + "\n  };\n"
        + f"  const dependencyMaps = {json.dumps(dependencies, sort_keys=True, separators=(',', ':'))};\n"
        + "  const cache = Object.create(null);\n"
        + "  function load(id) {\n"
        + "    if (cache[id]) return cache[id].exports;\n"
        + "    const factory = factories[id];\n"
        + "    if (!factory) throw new Error('Unknown bundled module: ' + id);\n"
        + "    const module = { exports: {} };\n"
        + "    cache[id] = module;\n"
        + "    factory(module, module.exports, request => {\n"
        + "      const target = dependencyMaps[id] && dependencyMaps[id][request];\n"
        + "      if (!target) throw new Error('Unknown bundled dependency: ' + request);\n"
        + "      return load(target);\n"
        + "    });\n"
        + "    return module.exports;\n"
        + "  }\n"
        + f"  const api = load({json.dumps(entry_id)});\n"
        + "  Object.defineProperty(api, '__notionBundle', { value: metadata, enumerable: false });\n"
        + "  Object.defineProperty(root, 'NotionYjs', { value: Object.freeze(api), writable: false, configurable: false });\n"
        + "})(typeof globalThis !== 'undefined' ? globalThis : this);\n"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output.resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    value = build_bundle(args.node_runtime)
    output.write_text(value, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "status": "pass",
                "output": str(output),
                "bytes": len(value.encode("utf-8")),
                "sha256": f"sha256:{_sha256(output)}",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
