"""Generate a read-only source inventory for Repository Walk.

Python imports/definitions use the standard-library AST. JavaScript and
TypeScript imports/definitions use conservative regular expressions and are
explicitly labelled heuristic. An import edge never claims runtime ordering.
Only metadata is emitted; source text, literal values and docstrings are not.
"""
from __future__ import annotations

import argparse
import ast
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tokenize
import warnings

LANGUAGES = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
             ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
             ".cjs": "JavaScript", ".html": "HTML", ".htm": "HTML",
             ".css": "CSS", ".scss": "CSS", ".md": "Markdown",
             ".json": "JSON", ".ps1": "Shell", ".sh": "Shell", ".bat": "Shell"}
SOURCE_ROOTS = {"code_assist_v1", "demos_v1", "harness-mvp", "hermes-web",
                "uio", "beno", "llm-wiki", "concept-erasure-paper-guide"}
EXCLUDED_DIRS = {"rendering", "scientific-skills", "workspace-feature-evidence",
                 "node_modules", "venv", ".venv", "__pycache__", "backups",
                 "uploads", "runtime", "data", "user_data", "user_knowledge",
                 "knowledge", "sessions", "logs", "model_gguf", "models",
                 "images", "img", "sound", "ppt_templates", "saved-prompts",
                 "work", "workspace", "skills", "fixtures", "test_artifacts",
                 "artifacts", "coverage", "dist", "build", "target", "vendor"}
EXCLUDED_FILES = {"token.txt", "api_config.json", "users.json", "package-lock.json",
                  "pnpm-lock.yaml", "yarn.lock", "skills_kr.json"}
DISTRICT_SPECS = [
    ("gateway", "DEMO GATEWAY", "demos_v1", "Flask 데모 앱의 진입점, 대화·모델·도구 라우트"),
    ("assist", "AGENT ENGINE", "code_assist_v1", "에이전트 실행, 모델 호출, 세션과 통합 API"),
    ("knowledge", "KNOWLEDGE CORE", "code_assist_v1/knowledge_core", "지식 저장소, 검색, 조직·공간·리비전 서비스"),
    ("notion", "WORKSPACE", "code_assist_v1/notion_*", "Notion 페이지·워크스페이스·협업 구현과 API"),
    ("frontend", "INTERFACE", "code_assist_v1/static", "브라우저 화면, 스타일과 상호작용 코드"),
    ("verification", "TEST LAB", "code_assist_v1/tests", "서비스와 API 동작을 검증하는 테스트"),
    ("docs", "DESIGN ARCHIVE", "code_assist_v1/docs", "설계 계약, 실행 계획과 프로젝트 설명 문서"),
    ("harness", "TOOL HARNESS", "harness-mvp", "도구 등록, 권한 검사, 프롬프트 라우팅과 턴 실행"),
    ("hermes", "HERMES WEB", "hermes-web", "Hermes 독립 웹 UI, Flask 서버와 게이트웨이 프록시"),
    ("showcase", "WEB GALLERY", "uio · beno", "HTML 데모와 웹 화면 모음"),
]
JS_IMPORT_RE = re.compile(r"\b(?:import|export)\s+(?:[^;'\"]{0,800}?\s+from\s+)?['\"]([^'\"\n]+)['\"]|\b(?:require|import)\s*\(\s*['\"]([^'\"\n]+)['\"]")
JS_SYMBOL_RE = re.compile(r"(?:export\s+(?:default\s+)?)?(?:(?:async\s+)?function\s+|class\s+|(?:const|let)\s+)([A-Za-z_$][\w$]*)")


def skip_directory(name: str) -> bool:
    lower = name.lower()
    return name.startswith(".") or lower.startswith("tmp") or lower in EXCLUDED_DIRS


def include_file(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith(".") or name in EXCLUDED_FILES:
        return False
    if any(token in name for token in (".backup", ".bak", "credentials", "secret", "token.txt", "복사본")):
        return False
    if path.suffix.lower() not in LANGUAGES:
        return False
    if path.suffix.lower() == ".json":
        return name in {"package.json", "tsconfig.json", "jsconfig.json", "manifest.json"}
    return True


def source_paths(root: Path):
    # Root files are supported, but user-data trees never enter traversal.
    for candidate in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if unsafe_link(candidate, root):
            continue
        # Standalone root HTML files are generated output reports. Application
        # templates and HTML inside source projects remain part of the map.
        if candidate.is_file() and candidate.suffix.lower() not in {".html", ".htm"} and include_file(candidate):
            yield candidate
        elif candidate.is_dir() and candidate.name in SOURCE_ROOTS:
            for directory, dirs, files in os.walk(candidate, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not skip_directory(d) and not unsafe_link(Path(directory, d), root))
                for name in sorted(files):
                    path = Path(directory, name)
                    if include_file(path) and not unsafe_link(path, root):
                        yield path


def unsafe_link(path: Path, root: Path) -> bool:
    """Do not follow symbolic links, Windows junctions, or root escapes."""
    try:
        return (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                or not path.resolve().is_relative_to(root))
    except OSError:
        return True


def district_for(relative: str) -> str:
    parts = relative.split("/")
    top = parts[0]
    if top == "code_assist_v1":
        child = parts[1] if len(parts) > 1 else ""
        if child == "tests" or child.startswith("test_"):
            return "verification"
        if child == "docs" or child.endswith(".md"):
            return "docs"
        if child in {"static", "notion_static"}:
            return "frontend"
        if child == "knowledge_core" or "second_brain" in child or child in {"knowledge_store.py", "routes_knowledge.py"}:
            return "knowledge"
        if child.startswith(("notion", "routes_notion")):
            return "notion"
        return "assist"
    if top in {"llm-wiki", "concept-erasure-paper-guide"}:
        return "docs"
    return {"harness-mvp": "harness", "hermes-web": "hermes", "uio": "showcase", "beno": "showcase"}.get(top, "gateway")


def role_for(relative: str, language: str) -> str:
    lower = relative.lower()
    name = Path(relative).name.lower()
    if "/tests/" in lower or name.startswith("test_") or name.endswith((".test.ts", ".spec.ts", ".test.js")):
        return "test"
    if language == "Markdown":
        return "docs"
    if language in {"HTML", "CSS"} or "/static/" in lower or "/notion_static/" in lower:
        return "ui"
    if name.startswith(("routes_", "router", "blueprint", "server")) or "/api/" in lower:
        return "api"
    if language == "Shell" or "/scripts/" in lower or "/ops/" in lower:
        return "script"
    if language == "JSON" or name.startswith(("config", "requirements")):
        return "config"
    return "core"


def python_metadata(text: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(text)
    symbols = []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function", "line": node.lineno})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"specifier": alias.name, "line": node.lineno, "confidence": "parsed", "level": 0, "names": []})
        elif isinstance(node, ast.ImportFrom):
            imports.append({"specifier": "." * node.level + (node.module or ""), "line": node.lineno, "confidence": "parsed", "level": node.level, "names": [a.name for a in node.names]})
    return sorted(symbols, key=lambda s: (s["line"], s["name"])), sorted(imports, key=lambda i: (i["line"], i["specifier"]))


def strip_js_comments(text: str) -> str:
    # Keep newline offsets so displayed source locations remain accurate.
    # This conservative adapter is labelled heuristic, including matches in literals.
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", lambda m: "\n" * m.group().count("\n"), text)


def js_metadata(text: str):
    cleaned = strip_js_comments(text)
    newlines = [m.start() for m in re.finditer("\n", cleaned)]
    symbols = [{"name": m.group(1), "kind": "declaration", "line": bisect_right(newlines, m.start()) + 1} for m in JS_SYMBOL_RE.finditer(cleaned)]
    imports = [{"specifier": m.group(1) or m.group(2), "line": bisect_right(newlines, m.start()) + 1, "confidence": "heuristic"} for m in JS_IMPORT_RE.finditer(cleaned)]
    return symbols, imports


def describe(relative: str, language: str, role: str, symbols: list) -> str:
    basename = Path(relative).stem
    role_text = {"ui": "화면과 상호작용", "api": "요청 처리와 라우트", "core": "서비스 로직", "test": "동작 검증", "docs": "설계·사용 문서", "config": "프로젝트 구성", "script": "실행·운영 작업"}[role]
    if language == "Markdown":
        return f"{basename}에 관한 문서. 문서 파일명과 경로를 기준으로 분류했습니다."
    domains = [("second_brain", "지식 공간"), ("knowledge", "지식 저장·검색"),
               ("model", "모델 관리"), ("llm", "모델 호출"), ("chat", "채팅"),
               ("search", "검색"), ("auth", "인증·권한"), ("iam", "멤버 권한"),
               ("database", "데이터베이스"), ("publication", "지식 발행"),
               ("workspace", "워크스페이스"), ("page", "페이지"), ("session", "세션"),
               ("harness", "도구 실행"), ("registry", "도구 등록"), ("router", "라우팅"),
               ("outbox", "이벤트 전달"), ("revision", "리비전"), ("memory", "메모리"),
               ("document", "문서 처리"), ("gguf", "로컬 GGUF 모델"), ("migration", "스키마 이전")]
    domain = next((label for token, label in domains if token in basename.lower()), "")
    names = [s["name"] for s in symbols if not s["name"].startswith("_")][:3]
    declarations = f" {', '.join(names)} 등 {len(symbols)}개 선언을 포함합니다." if symbols else ""
    return f"{domain + ' · ' if domain else ''}{language} {role_text} 파일.{declarations}"


def normalize_relative(parts: list[str]) -> str | None:
    normalized = []
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized:
                return None
            normalized.pop()
        else:
            normalized.append(part)
    return "/".join(normalized)


def resolve_python(source: str, imp: dict, ids: set[str]):
    source_parts = source.split("/")
    package = source_parts[:-1]
    level = imp.get("level", 0)
    module = imp["specifier"].lstrip(".").replace(".", "/")
    if level:
        if level > len(package):
            return []
        base = "/".join(package[:len(package) - level + 1])
        module_path = "/".join(p for p in (base, module) if p)
        candidates = [module_path]
    else:
        # Script-relative imports are common in hermes-web and small demos.
        # harness_bridge.py and code_assist_v1/harness_setup.py explicitly
        # add this repository's harness-mvp directory to sys.path. Its package
        # is consequently reachable from DEMOS routes as well as harness files.
        candidates = [module, "/".join(package + [module]), "harness-mvp/" + module]
        if source_parts[0] == "harness-mvp":
            candidates.insert(0, "harness-mvp/" + module)
    results = []
    for candidate in candidates:
        for name in imp.get("names", []):
            if name != "*":
                submodule = candidate + "/" + name
                for target in (submodule + ".py", submodule + "/__init__.py"):
                    if target in ids and target != source:
                        results.append(target)
        for target in (candidate + ".py", candidate + "/__init__.py"):
            if target in ids and target != source:
                results.append(target)
        if results:
            break
    return sorted(set(results))


def resolve_js(source: str, imp: dict, ids: set[str]):
    spec = imp["specifier"]
    if not spec.startswith("."):
        return []
    target = normalize_relative(source.split("/")[:-1] + spec.split("/"))
    if target is None:
        return []
    candidates = [target] + [target + ext for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".json")]
    candidates += [target + "/index" + ext for ext in (".ts", ".tsx", ".js", ".jsx")]
    return [candidate for candidate in candidates if candidate in ids and candidate != source][:1]


def scan(root: Path) -> dict:
    root = root.resolve()
    files = []
    read_errors = []
    for path in source_paths(root):
        relative = path.relative_to(root).as_posix()
        language = LANGUAGES[path.suffix.lower()]
        try:
            if language == "Python":
                with tokenize.open(path) as handle:
                    text = handle.read()
            else:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            symbols, imports = ([], [])
            parse_status = "metadata"
            if language == "Python":
                try:
                    symbols, imports = python_metadata(text)
                    parse_status = "parsed"
                except (SyntaxError, ValueError):
                    parse_status = "syntax-unavailable"
            elif language in {"JavaScript", "TypeScript"}:
                symbols, imports = js_metadata(text)
                parse_status = "heuristic"
            lines = text.splitlines()
            role = role_for(relative, language)
            files.append({"id": relative, "path": relative, "name": path.name,
                          "district": district_for(relative), "language": language,
                          "lines": len(lines), "nonEmptyLines": sum(bool(line.strip()) for line in lines),
                          "bytes": path.stat().st_size, "role": role, "symbols": symbols,
                          "description": describe(relative, language, role, symbols),
                          "imports": imports, "analysis": parse_status})
        except (OSError, UnicodeError, SyntaxError):
            read_errors.append(relative)
    files.sort(key=lambda f: f["id"])
    ids = {file["id"] for file in files}
    edges = []
    unresolved = []
    for file in files:
        for imp in file["imports"]:
            targets = resolve_python(file["id"], imp, ids) if file["language"] == "Python" else resolve_js(file["id"], imp, ids)
            imp["targets"] = targets
            imp.pop("names", None)
            imp.pop("level", None)
            if not targets:
                unresolved.append({"source": file["id"], "specifier": imp["specifier"], "line": imp["line"]})
            for target in targets:
                edges.append({"source": file["id"], "target": target, "kind": "import", "line": imp["line"], "confidence": imp["confidence"]})
    edges.sort(key=lambda e: (e["source"], e["line"], e["target"]))
    active = {file["district"] for file in files}
    districts = [{"id": ident, "label": label, "path": path, "description": description} for ident, label, path, description in DISTRICT_SPECS if ident in active]
    return {"schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rootName": root.name, "summary": {"fileCount": len(files), "totalLines": sum(f["lines"] for f in files),
            "languageCounts": dict(sorted(Counter(f["language"] for f in files).items())), "districtCount": len(districts),
            "excluded": ["숨김 폴더와 Git", "의존성·가상환경·빌드 결과", "임시·백업·런타임·사용자 데이터", "scientific-skills · workspace-feature-evidence", "TOKEN.TXT · api_config.json · users.json · 비밀 설정", "이미지·모델·바이너리", "저장소 루트의 HTML 출력 보고서", "rendering 자체"],
            "readErrors": read_errors, "importEdges": len(edges), "unresolvedImports": len(unresolved),
            "analysisNotes": ["Python: AST 구문 분석", "JavaScript/TypeScript: 정규식 휴리스틱", "연결선은 정적 import 관계이며 실행 순서를 뜻하지 않습니다.", "미확인 참조에는 외부 패키지와 분석 범위 밖 모듈이 함께 포함됩니다."]},
            "districts": districts, "files": files, "edges": edges, "unresolved": unresolved, "tours": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "public" / "repo-map.json")
    args = parser.parse_args()
    result = scan(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
