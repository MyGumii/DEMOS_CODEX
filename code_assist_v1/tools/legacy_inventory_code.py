"""Read-only, deterministic legacy code-coverage inventory.

The scanner intentionally uses only Python's standard library.  Python source is
parsed with :mod:`ast`; application modules are never imported.  JavaScript is
handled by a conservative source scanner that masks comments while preserving
string/template contents needed to identify API paths and compatibility keys.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat as stat_module
from typing import Any, Iterable, Mapping, Sequence


REPORT_SCHEMA = "code_assist.legacy_inventory.code_coverage.v1"
MAPPING_SCHEMA_VERSION = 1
DEFAULT_MAPPING_RELATIVE_PATH = Path(
    "docs/knowledge_spaces/CURRENT_SYSTEM_INVENTORY_MAP.json"
)
EXPECTED_JOB_SUBMITTERS = 8

_TARGET_MODULE_NAMES = (
    "knowledge_store",
    "second_brain_store",
    "memory_store",
    "project_context_store",
    "notion_store",
    "job_manager",
)
_TARGET_MODULES = tuple(f"code_assist_v1.{name}" for name in _TARGET_MODULE_NAMES)
_COMPOSITION_FILES = {"app_code.py", "app_notion.py", "blueprint.py"}
_EXPLICIT_ROUTE_FILES = _COMPOSITION_FILES | {"auth.py"}
_JS_DIRECTORIES = ("static", "notion_static")
_CONTEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("compact_summary", re.compile(r"compact[_A-Z]?summary", re.IGNORECASE)),
    ("pinned_thoughts", re.compile(r"pinned(?:_|)thoughts", re.IGNORECASE)),
    ("project_context", re.compile(r"project[_A-Z]?context", re.IGNORECASE)),
    ("session_pages", re.compile(r"session[_A-Z]?pages", re.IGNORECASE)),
)
_RELEVANT_UI_PATH_MARKERS = (
    "/api/",
)
_ACTION_DISPOSITIONS = {"migrate", "rebuild", "replace"}
_RETAINED_DISPOSITIONS = {
    "exclude_non_knowledge",
    "retain_compatibility",
    "retain_external",
}
_ALL_DISPOSITIONS = _ACTION_DISPOSITIONS | _RETAINED_DISPOSITIONS
_SELECTOR_KEYS = {"callees", "callee_prefixes", "files", "kinds", "symbols"}
_READ_CALL_PREFIXES = (
    "build_",
    "export_",
    "find_",
    "get_",
    "has_",
    "is_",
    "list_",
    "load_",
    "resolve_",
    "search_",
)
_WRITE_CALL_PREFIXES = (
    "add_",
    "archive_",
    "attach_",
    "cancel_",
    "clear_",
    "convert_",
    "create_",
    "delete_",
    "detach_",
    "disable_",
    "enable_",
    "import_",
    "move_",
    "purge_",
    "rebuild_",
    "reindex_",
    "repair_",
    "replace_",
    "restore_",
    "revoke_",
    "save_",
    "set_",
    "share_",
    "submit_",
    "toggle_",
    "update_",
    "upload_",
)
_READ_CALL_NAMES = {
    "candidate_counts",
    "capture_legacy_page_source_proof",
    "curation_generate_title",
    "graph_data",
    "search",
    "self_owned_page_lifecycle_scope",
    "view_file",
}
_WRITE_CALL_NAMES = {
    "apply_candidate_action",
    "curation_review_candidate",
    "duplicate_page",
    "duplicate_page_idempotent",
    "duplicate_public_page",
    "invalidate_cache",
    "promote_conversation_source",
    "propose_from_user_message",
}
_REFERENCE_CALL_NAMES = {
    "chat_messages_to_markdown",
    "markdown_to_blocks",
    "page_id_for_operation",
    "redact_sensitive",
}


@dataclass(frozen=True)
class _RawCallsite:
    file: str
    file_sha256: str
    symbol: str
    callee: str
    kind: str
    line: int
    column: int
    query_present: bool | None = None
    fragment_present: bool | None = None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError("non_finite_json_number")


def _normalized_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


class _UnsafeSourceError(OSError):
    pass


def _is_reparse(path: Path, value: os.stat_result) -> bool:
    attributes = int(getattr(value, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if attributes & reparse_flag:
        return True
    if path.is_symlink():
        return True
    checker = getattr(os.path, "isjunction", None)
    try:
        return bool(checker and checker(path))
    except OSError:
        return True


def _contains(root: Path, child: Path) -> bool:
    try:
        return os.path.normcase(os.fspath(os.path.commonpath((root, child)))) == os.path.normcase(
            os.fspath(root)
        )
    except ValueError:
        return False


def _validated_directory(path: Path, root: Path) -> None:
    value = path.stat(follow_symlinks=False)
    if _is_reparse(path, value) or not path.is_dir():
        raise _UnsafeSourceError("unsafe_reparse_source")
    resolved = path.resolve(strict=True)
    if not _contains(root, resolved):
        raise _UnsafeSourceError("source_escaped_root")


def _read_source_bytes(path: Path, root: Path) -> bytes:
    before = path.stat(follow_symlinks=False)
    if _is_reparse(path, before):
        raise _UnsafeSourceError("unsafe_reparse_source")
    resolved_before = path.resolve(strict=True)
    if not _contains(root, resolved_before):
        raise _UnsafeSourceError("source_escaped_root")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise _UnsafeSourceError("source_identity_changed")
        raw = stream.read()
    after = path.stat(follow_symlinks=False)
    resolved_after = path.resolve(strict=True)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or resolved_after != resolved_before
        or not _contains(root, resolved_after)
    ):
        raise _UnsafeSourceError("source_identity_changed")
    return raw


def _is_target_module(name: str) -> bool:
    return any(name == target or name.startswith(target + ".") for target in _TARGET_MODULES)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _context_markers(value: str) -> list[str]:
    return [name for name, pattern in _CONTEXT_PATTERNS if pattern.search(value)]


class _PythonInventoryVisitor(ast.NodeVisitor):
    def __init__(self, relative_file: str, file_hash: str) -> None:
        self.relative_file = relative_file
        self.file_hash = file_hash
        self.aliases: dict[str, str] = {}
        self.symbols: list[str] = []
        self.callsites: list[_RawCallsite] = []

    @property
    def symbol(self) -> str:
        return ".".join(self.symbols) if self.symbols else "<module>"

    def _add(
        self,
        *,
        callee: str,
        kind: str,
        node: ast.AST,
        symbol: str | None = None,
    ) -> None:
        self.callsites.append(
            _RawCallsite(
                file=self.relative_file,
                file_sha256=self.file_hash,
                symbol=symbol or self.symbol,
                callee=callee,
                kind=kind,
                line=int(getattr(node, "lineno", 0) or 0),
                column=int(getattr(node, "col_offset", 0) or 0),
            )
        )

    def _record_context(self, value: str, node: ast.AST) -> None:
        for marker in _context_markers(value):
            self._add(callee=f"context:{marker}", kind="context_reference", node=node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".")[0]
            self.aliases[bound_name] = alias.name
            if _is_target_module(alias.name):
                self._add(
                    callee=f"import:{alias.name}",
                    kind="legacy_store_import",
                    node=node,
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            imported = f"{module}.{alias.name}" if module else alias.name
            self.aliases[alias.asname or alias.name] = imported
            if _is_target_module(imported):
                self._add(
                    callee=f"import:{imported}",
                    kind="legacy_store_import",
                    node=node,
                )

    def _qualified_symbol(self, name: str) -> str:
        return ".".join((*self.symbols, name)) if self.symbols else name

    def _record_routes(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not (
            self.relative_file in _EXPLICIT_ROUTE_FILES
            or (
                self.relative_file.startswith("routes_")
                and self.relative_file.endswith(".py")
            )
        ):
            return
        symbol = self._qualified_symbol(node.name)
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            dotted = _dotted_name(decorator.func)
            if not dotted or dotted.split(".")[-1] != "route":
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            route_path = decorator.args[0].value
            if not isinstance(route_path, str):
                continue
            methods = ["GET"]
            for keyword in decorator.keywords:
                if keyword.arg != "methods" or not isinstance(
                    keyword.value, (ast.List, ast.Tuple, ast.Set)
                ):
                    continue
                values = [
                    str(item.value).upper()
                    for item in keyword.value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
                if values:
                    methods = sorted(set(values))
            self._add(
                callee=f"route:{route_path}|{','.join(methods)}",
                kind="route_decorator",
                node=decorator,
                symbol=symbol,
            )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_routes(node)
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_routes(node)
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def _resolved_callee(self, node: ast.AST) -> str | None:
        dotted = _dotted_name(node)
        if not dotted:
            return None
        parts = dotted.split(".")
        replacement = self.aliases.get(parts[0])
        if replacement:
            return ".".join((replacement, *parts[1:]))
        return dotted

    def visit_Call(self, node: ast.Call) -> None:
        dotted = _dotted_name(node.func)
        if self.relative_file in _COMPOSITION_FILES and dotted:
            final_name = (self._resolved_callee(node.func) or dotted).split(".")[-1]
            if re.fullmatch(r"register_[A-Za-z0-9_]+_routes", final_name):
                self._add(
                    callee=f"register:{final_name}",
                    kind="route_registration",
                    node=node,
                )
            elif final_name == "register_blueprint":
                self._add(
                    callee="register:register_blueprint",
                    kind="route_registration",
                    node=node,
                )

        resolved = self._resolved_callee(node.func)
        if resolved and _is_target_module(resolved):
            kind = (
                "job_submit"
                if ".job_manager" in resolved and resolved.endswith(".submit")
                else "legacy_store_call"
            )
            self._add(callee=resolved, kind=kind, node=node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self._record_context(node.id, node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record_context(node.attr, node)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._record_context(node.value, node)


def _mask_js_comments(source: str) -> str:
    """Mask JS comments without altering offsets or string/template contents."""
    chars = list(source)
    state = "normal"
    index = 0
    while index < len(chars):
        char = chars[index]
        following = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "line_comment":
            if char in "\r\n":
                state = "normal"
            else:
                chars[index] = " "
        elif state == "block_comment":
            if char == "*" and following == "/":
                chars[index] = chars[index + 1] = " "
                index += 1
                state = "normal"
            elif char not in "\r\n":
                chars[index] = " "
        elif state in {"single", "double", "template"}:
            terminator = {"single": "'", "double": '"', "template": "`"}[state]
            if char == "\\":
                index += 1
            elif char == terminator:
                state = "normal"
        else:
            if char == "/" and following == "/":
                chars[index] = chars[index + 1] = " "
                index += 1
                state = "line_comment"
            elif char == "/" and following == "*":
                chars[index] = chars[index + 1] = " "
                index += 1
                state = "block_comment"
            elif char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "`":
                state = "template"
        index += 1
    return "".join(chars)


_JS_API_CALL = re.compile(
    r"\b(?:api|fetch)\s*\(\s*(?P<literal>"
    r'"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'"
    r"|`(?:\\.|[^`\\])*`)",
    re.DOTALL,
)
_JS_API_OR_FETCH_CALL = re.compile(r"\b(?:api|fetch)\s*\(")


def _normalize_js_url(literal: str) -> tuple[str, bool, bool]:
    value = literal[1:-1]
    value = re.sub(r"\$\{.*?\}", "${}", value, flags=re.DOTALL)
    query_index = value.find("?")
    fragment_index = value.find("#")
    query_present = query_index >= 0 and (
        fragment_index < 0 or query_index < fragment_index
    )
    fragment_present = fragment_index >= 0
    delimiters = [index for index in (query_index, fragment_index) if index >= 0]
    if delimiters:
        value = value[: min(delimiters)]
    path = re.sub(r"\s+", " ", value).strip()
    return path, query_present, fragment_present


def _scan_javascript(relative_file: str, file_hash: str, source: str) -> list[_RawCallsite]:
    masked = _mask_js_comments(source)
    callsites: list[_RawCallsite] = []

    def add(
        callee: str,
        kind: str,
        offset: int,
        *,
        query_present: bool | None = None,
        fragment_present: bool | None = None,
    ) -> None:
        line_start = masked.rfind("\n", 0, offset) + 1
        callsites.append(
            _RawCallsite(
                file=relative_file,
                file_sha256=file_hash,
                symbol="<javascript>",
                callee=callee,
                kind=kind,
                line=masked.count("\n", 0, offset) + 1,
                column=offset - line_start,
                query_present=query_present,
                fragment_present=fragment_present,
            )
        )

    literal_call_offsets: set[int] = set()
    for match in _JS_API_CALL.finditer(masked):
        literal_call_offsets.add(match.start())
        path, query_present, fragment_present = _normalize_js_url(
            match.group("literal")
        )
        normalized_path = path.casefold().lstrip("/")
        if any(
            marker.casefold().lstrip("/") in normalized_path
            for marker in _RELEVANT_UI_PATH_MARKERS
        ):
            add(
                f"ui_api:{path}",
                "ui_api_call",
                match.start(),
                query_present=query_present,
                fragment_present=fragment_present,
            )

    # Non-literal targets are deliberately recorded, not guessed.  This catches
    # conditional path expressions and the shared wrappers themselves while
    # keeping the mapping ledger responsible for an explicit disposition.
    for match in _JS_API_OR_FETCH_CALL.finditer(masked):
        if match.start() not in literal_call_offsets:
            add("ui_api:<dynamic>", "ui_api_call", match.start())

    for marker, pattern in _CONTEXT_PATTERNS:
        for match in pattern.finditer(masked):
            add(f"ui_context:{marker}", "ui_context_reference", match.start())
    return callsites


def _source_paths(root: Path) -> list[tuple[Path, str]]:
    _validated_directory(root, root)
    paths: list[tuple[Path, str]] = []
    with os.scandir(root) as entries:
        for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
            if not entry.name.casefold().endswith(".py"):
                continue
            path = Path(entry.path)
            value = entry.stat(follow_symlinks=False)
            if _is_reparse(path, value) or not entry.is_file(follow_symlinks=False):
                raise _UnsafeSourceError("unsafe_reparse_source")
            paths.append((path, "python"))
    for directory_name in _JS_DIRECTORIES:
        directory = root / directory_name
        if directory.exists() or directory.is_symlink():
            _validated_directory(directory, root)
            with os.scandir(directory) as entries:
                for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
                    if not entry.name.casefold().endswith(".js"):
                        continue
                    path = Path(entry.path)
                    value = entry.stat(follow_symlinks=False)
                    if _is_reparse(path, value) or not entry.is_file(follow_symlinks=False):
                        raise _UnsafeSourceError("unsafe_reparse_source")
                    paths.append((path, "javascript"))
    return sorted(paths, key=lambda item: _normalized_relative(item[0], root))


def _discover(root: Path) -> tuple[list[dict[str, str]], list[_RawCallsite], list[dict[str, str]]]:
    files: list[dict[str, str]] = []
    callsites: list[_RawCallsite] = []
    errors: list[dict[str, str]] = []
    for path, language in _source_paths(root):
        relative = _normalized_relative(path, root)
        try:
            raw = _read_source_bytes(path, root)
            source = raw.decode("utf-8-sig")
        except _UnsafeSourceError:
            raise
        except (OSError, UnicodeError):
            errors.append({"code": "source_read_failed", "file": relative})
            continue
        file_hash = _sha256(raw)
        files.append({"language": language, "path": relative, "sha256": file_hash})
        if language == "javascript":
            callsites.extend(_scan_javascript(relative, file_hash, source))
            continue
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError:
            errors.append({"code": "python_parse_failed", "file": relative})
            continue
        visitor = _PythonInventoryVisitor(relative, file_hash)
        visitor.visit(tree)
        callsites.extend(visitor.callsites)

    deduplicated = {
        (
            item.file,
            item.file_sha256,
            item.symbol,
            item.callee,
            item.kind,
            item.line,
            item.column,
        ): item
        for item in callsites
    }
    return files, list(deduplicated.values()), errors


def _finalize_callsites(raw_callsites: Iterable[_RawCallsite]) -> list[dict[str, Any]]:
    ordered = sorted(
        raw_callsites,
        key=lambda item: (
            item.file,
            item.symbol,
            item.callee,
            item.line,
            item.column,
            item.kind,
        ),
    )
    ordinals: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    result: list[dict[str, Any]] = []
    for item in ordered:
        identity_group = (item.file, item.symbol, item.callee)
        ordinals[identity_group] += 1
        ordinal = ordinals[identity_group]
        identity = _canonical_json([item.file, item.symbol, item.callee, ordinal])
        callsite = {
                "access": _infer_access(item),
                "callee": item.callee,
                "callsite_id": "callsite-" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "column": item.column,
                "file": item.file,
                "file_sha256": item.file_sha256,
                "kind": item.kind,
                "line": item.line,
                "ordinal": ordinal,
                "symbol": item.symbol,
            }
        if item.query_present is not None:
            callsite["query_present"] = item.query_present
        if item.fragment_present is not None:
            callsite["fragment_present"] = item.fragment_present
        result.append(callsite)
    return result


def _infer_access(item: _RawCallsite) -> str:
    if item.kind == "job_submit":
        return "submit"
    if item.kind.startswith("ui_"):
        return "ui"
    if item.kind == "route_registration":
        return "registration"
    if item.kind in {"context_reference", "legacy_store_import"}:
        return "reference"
    if item.kind == "route_decorator":
        methods = item.callee.rsplit("|", 1)[-1].split(",")
        return "read" if set(methods).issubset({"GET", "HEAD", "OPTIONS"}) else "write"
    if item.kind == "legacy_store_call":
        function_name = item.callee.rsplit(".", 1)[-1].casefold()
        if function_name in _REFERENCE_CALL_NAMES:
            return "reference"
        if function_name in _READ_CALL_NAMES or function_name.startswith(
            _READ_CALL_PREFIXES
        ):
            return "read"
        if function_name in _WRITE_CALL_NAMES or function_name.startswith(
            _WRITE_CALL_PREFIXES
        ):
            return "write"
        return "unknown"
    return "reference"


def _validate_mapping(raw: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    if not isinstance(raw, Mapping):
        return {
            "expected_callsite_ids_sha256": None,
            "expected_submitter_count": EXPECTED_JOB_SUBMITTERS,
            "rules": [],
            "schema_version": MAPPING_SCHEMA_VERSION,
        }, [
            {"code": "mapping_not_object", "file": "<mapping>"}
        ]
    if raw.get("schema_version") != MAPPING_SCHEMA_VERSION:
        errors.append({"code": "mapping_schema_version_invalid", "file": "<mapping>"})
    expected_callsite_ids_sha256 = raw.get("expected_callsite_ids_sha256")
    if not isinstance(expected_callsite_ids_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_callsite_ids_sha256
    ):
        errors.append({"code": "mapping_callsite_seal_invalid", "file": "<mapping>"})
        expected_callsite_ids_sha256 = None
    expected = raw.get("expected_submitter_count", EXPECTED_JOB_SUBMITTERS)
    if expected != EXPECTED_JOB_SUBMITTERS:
        errors.append({"code": "mapping_expected_submitter_count_invalid", "file": "<mapping>"})
        expected = EXPECTED_JOB_SUBMITTERS
    raw_rules = raw.get("mapping_rules")
    if not isinstance(raw_rules, list):
        errors.append({"code": "mapping_rules_invalid", "file": "<mapping>"})
        raw_rules = []
    rules: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(raw_rules):
        marker = f"<mapping:{index}>"
        if not isinstance(value, Mapping):
            errors.append({"code": "mapping_rule_invalid", "file": marker})
            continue
        mapping_id = value.get("mapping_id")
        selector = value.get("selector")
        disposition = value.get("disposition")
        if not isinstance(mapping_id, str) or not mapping_id or mapping_id in seen_ids:
            errors.append({"code": "mapping_id_invalid_or_duplicate", "file": marker})
            continue
        seen_ids.add(mapping_id)
        if not isinstance(selector, Mapping) or not set(selector).issubset(_SELECTOR_KEYS):
            errors.append({"code": "mapping_selector_invalid", "file": marker})
            continue
        normalized_selector: dict[str, list[str]] = {}
        selector_invalid = False
        for key, selected in selector.items():
            if not isinstance(selected, list) or not selected or not all(
                isinstance(item, str) and item for item in selected
            ):
                selector_invalid = True
                break
            normalized_selector[key] = sorted(set(selected))
        files = normalized_selector.get("files", [])
        if (
            selector_invalid
            or not files
            or any(any(token in path for token in "*?[]") for path in files)
        ):
            errors.append({"code": "mapping_selector_not_explicit", "file": marker})
            continue
        if disposition not in _ALL_DISPOSITIONS:
            errors.append({"code": "mapping_disposition_invalid", "file": marker})
            continue
        scope_owner = value.get("scope_owner")
        replacement_phase = value.get("replacement_phase")
        reason = value.get("reason")
        if not isinstance(scope_owner, str) or not scope_owner:
            errors.append({"code": "mapping_scope_owner_invalid", "file": marker})
            continue
        if not isinstance(replacement_phase, str) or not replacement_phase:
            errors.append({"code": "mapping_replacement_phase_invalid", "file": marker})
            continue
        if not isinstance(reason, str) or not reason:
            errors.append({"code": "mapping_reason_invalid", "file": marker})
            continue
        priority = value.get("priority", 0)
        if not isinstance(priority, int):
            errors.append({"code": "mapping_priority_invalid", "file": marker})
            continue
        rules.append(
            {
                "disposition": disposition,
                "mapping_id": mapping_id,
                "priority": priority,
                "replacement_phase": replacement_phase,
                "reason": reason,
                "scope_owner": scope_owner,
                "selector": normalized_selector,
            }
        )
    return {
        "expected_submitter_count": expected,
        "expected_callsite_ids_sha256": expected_callsite_ids_sha256,
        "rules": rules,
        "schema_version": MAPPING_SCHEMA_VERSION,
    }, errors


def _matches(rule: Mapping[str, Any], callsite: Mapping[str, Any]) -> bool:
    selector = rule["selector"]
    if callsite["file"] not in selector["files"]:
        return False
    for selector_key, callsite_key in (
        ("kinds", "kind"),
        ("symbols", "symbol"),
        ("callees", "callee"),
    ):
        selected = selector.get(selector_key)
        if selected is not None and callsite[callsite_key] not in selected:
            return False
    prefixes = selector.get("callee_prefixes")
    if prefixes is not None and not any(callsite["callee"].startswith(value) for value in prefixes):
        return False
    return True


def _classify(
    callsites: list[dict[str, Any]], rules: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, str]], set[str]]:
    classified: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    used: set[str] = set()
    for callsite in callsites:
        matches = [rule for rule in rules if _matches(rule, callsite)]
        if not matches:
            classified.append(dict(callsite))
            continue
        highest = max(rule["priority"] for rule in matches)
        winners = [rule for rule in matches if rule["priority"] == highest]
        if len(winners) != 1:
            errors.append(
                {"code": "mapping_rule_ambiguous", "file": str(callsite["file"])}
            )
            classified.append(dict(callsite))
            continue
        winner = winners[0]
        used.add(winner["mapping_id"])
        classified.append(
            {
                **callsite,
                "disposition": winner["disposition"],
                "mapping_id": winner["mapping_id"],
                "replacement_phase": winner["replacement_phase"],
                "reason": winner["reason"],
                "scope_owner": winner["scope_owner"],
            }
        )
    return classified, errors, used


def scan_code_coverage(
    legacy_root: str | Path,
    mapping_path: str | Path | None = None,
) -> dict[str, Any]:
    """Scan application source and return a deterministic JSON-safe coverage report.

    ``legacy_root`` is the ``code_assist_v1`` package/application root.  The
    function reads source and the mapping ledger only; it never imports scanned
    modules and never writes to the source tree.
    """
    configured_root = Path(legacy_root)
    root_stat = configured_root.stat(follow_symlinks=False)
    if _is_reparse(configured_root, root_stat):
        return _failure_report("unsafe_reparse_source")
    root = configured_root.resolve(strict=True)
    selected_mapping = (
        Path(mapping_path)
        if mapping_path is not None
        else root / DEFAULT_MAPPING_RELATIVE_PATH
    )
    try:
        mapping_bytes = _read_source_bytes(selected_mapping, root)
    except _UnsafeSourceError:
        return _failure_report("unsafe_reparse_source")
    try:
        mapping_raw = json.loads(
            mapping_bytes.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        mapping_raw = None
    mapping, mapping_errors = _validate_mapping(mapping_raw)
    try:
        files, raw_callsites, discovery_errors = _discover(root)
    except _UnsafeSourceError:
        return _failure_report("unsafe_reparse_source")
    finalized = _finalize_callsites(raw_callsites)
    callsite_ids_sha256 = _sha256(
        _canonical_json(sorted(item["callsite_id"] for item in finalized)).encode("utf-8")
    )
    if mapping["expected_callsite_ids_sha256"] != callsite_ids_sha256:
        mapping_errors.append(
            {"code": "mapping_callsite_set_mismatch", "file": "<mapping>"}
        )
    for item in finalized:
        if item["access"] == "unknown":
            discovery_errors.append(
                {"code": "legacy_store_access_unclassified", "file": item["file"]}
            )
    classified, classification_errors, used_mapping_ids = _classify(
        finalized, mapping["rules"]
    )

    known_mapping_ids = {rule["mapping_id"] for rule in mapping["rules"]}
    unused_mapping_ids = sorted(known_mapping_ids - used_mapping_ids)
    unclassified_items = [item for item in classified if "mapping_id" not in item]
    mapped = sum(
        1 for item in classified if item.get("disposition") in _ACTION_DISPOSITIONS
    )
    retained_excluded = sum(
        1 for item in classified if item.get("disposition") in _RETAINED_DISPOSITIONS
    )
    submitters = [item for item in classified if item["kind"] == "job_submit"]
    unmapped_submitter_ids = sorted(
        item["callsite_id"] for item in submitters if "mapping_id" not in item
    )
    errors = sorted(
        [*mapping_errors, *discovery_errors, *classification_errors],
        key=lambda item: (item["code"], item["file"]),
    )
    discovered = len(classified)
    unclassified = len(unclassified_items)
    expected_submitters = int(mapping["expected_submitter_count"])
    passed = (
        not errors
        and not unused_mapping_ids
        and unclassified == 0
        and discovered == mapped + retained_excluded
        and len(submitters) == expected_submitters
        and not unmapped_submitter_ids
    )
    return {
        "callsites": sorted(classified, key=lambda item: item["callsite_id"]),
        "errors": errors,
        "files": sorted(files, key=lambda item: item["path"]),
        "mapping": {
            "callsite_ids_sha256": callsite_ids_sha256,
            "expected_callsite_ids_sha256": mapping[
                "expected_callsite_ids_sha256"
            ],
            "expected_submitter_count": expected_submitters,
            "path": _normalized_relative(selected_mapping, root)
            if selected_mapping.is_relative_to(root)
            else "<external>",
            "sha256": _sha256(mapping_bytes),
            "schema_version": mapping["schema_version"],
            "unused_mapping_ids": unused_mapping_ids,
        },
        "report_schema": REPORT_SCHEMA,
        "source_fingerprint_sha256": _sha256(
            _canonical_json(
                [[item["path"], item["sha256"]] for item in files]
            ).encode("utf-8")
        ),
        "summary": {
            "discovered": discovered,
            "mapped": mapped,
            "pass": passed,
            "retained_excluded": retained_excluded,
            "submitter_count": len(submitters),
            "unclassified": unclassified,
            "unmapped_submitters": len(unmapped_submitter_ids),
        },
        "unclassified_callsite_ids": sorted(
            item["callsite_id"] for item in unclassified_items
        ),
        "unmapped_submitter_ids": unmapped_submitter_ids,
    }


class _NonExitingArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid_cli_arguments")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _NonExitingArgumentParser(
        description="Read-only legacy code-coverage inventory"
    )
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--mapping")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
    except ValueError:
        print(_canonical_json(_failure_report("invalid_cli_arguments")))
        return 2
    try:
        report = scan_code_coverage(args.legacy_root, args.mapping)
    except Exception:
        # The CLI is a machine gate: never leak environment-specific exception
        # messages or emit a traceback.  The library API remains exception-aware
        # so its caller can apply its own provider policy.
        print(_canonical_json(_failure_report("inventory_internal_error")))
        return 2
    print(_canonical_json(report))
    return 0 if report["summary"]["pass"] else 2


def _failure_report(code: str) -> dict[str, Any]:
    return {
        "callsites": [],
        "errors": [{"code": code, "file": "<command>"}],
        "files": [],
        "mapping": {
            "callsite_ids_sha256": None,
            "expected_callsite_ids_sha256": None,
            "expected_submitter_count": EXPECTED_JOB_SUBMITTERS,
            "path": "<unavailable>",
            "sha256": None,
            "schema_version": MAPPING_SCHEMA_VERSION,
            "unused_mapping_ids": [],
        },
        "report_schema": REPORT_SCHEMA,
        "source_fingerprint_sha256": _sha256(_canonical_json([]).encode("utf-8")),
        "summary": {
            "discovered": 0,
            "mapped": 0,
            "pass": False,
            "retained_excluded": 0,
            "submitter_count": 0,
            "unclassified": 0,
            "unmapped_submitters": 0,
        },
        "unclassified_callsite_ids": [],
        "unmapped_submitter_ids": [],
    }


__all__ = ["REPORT_SCHEMA", "scan_code_coverage"]


if __name__ == "__main__":
    raise SystemExit(main())
