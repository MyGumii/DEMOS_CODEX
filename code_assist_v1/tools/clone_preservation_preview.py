"""Create an exclusive 10421 data copy; never stop or write the source server.

SQLite databases use SQLite online backup, not live DB/WAL file copying.
This is a development snapshot, not an atomic cross-database backup product.
External integration workers/bindings and old login secrets are not copied.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def no_reparse(path):
    value = path.lstat()
    if path.is_symlink() or getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("A clone input contains a link/reparse point: " + str(path))


def create_copy(source: Path, target: Path):
    source = source.resolve(strict=True)
    target = target.absolute()
    # This tool is intentionally exact to the requested development target.
    if (target.name != "SecondBrain-10421-20260911" or target.parent.resolve(strict=True) != Path("D:/CodexTestTemp").resolve(strict=True)
            or target.exists() or source == target or source.is_relative_to(target) or target.is_relative_to(source)):
        raise ValueError("Refusing an existing, overlapping or unexpected target.")
    no_reparse(source)
    marker_path = source / ".runtime/full-second-brain-preview.json"
    old_marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if Path(old_marker["data_root"]).resolve(strict=True) != source or old_marker.get("schema_version") != 3:
        raise ValueError("Source preview binding mismatch.")
    check = dict(old_marker)
    old_hash = check.pop("marker_sha256")
    if sha256(encoded(check)).hexdigest() != old_hash:
        raise ValueError("Source preview marker hash mismatch.")
    target.mkdir()
    records = []
    for parent, directories, files in os.walk(source, followlinks=False):
        base = Path(parent)
        for name in tuple(directories):
            child = base / name
            no_reparse(child)
            if child.relative_to(source).parts[0] in {".runtime", "codex-context"}:
                directories.remove(name)
        for name in sorted(files):
            path = base / name
            no_reparse(path)
            relative = path.relative_to(source)
            if relative.parts[0] in {".runtime", "codex-context"} or name.endswith(("-wal", "-shm", "-journal", ".log", ".lock")):
                continue
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                read = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
                write = sqlite3.connect(dest)
                try:
                    read.backup(write, pages=256, sleep=0.05)
                    if write.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ValueError("Cloned database integrity check failed.")
                    rows = write.execute("PRAGMA foreign_key_check").fetchall()
                    if rows:
                        raise ValueError("Cloned database foreign key check failed.")
                finally:
                    write.close()
                    read.close()
                method = "sqlite_online_backup"
            else:
                before = sha256(path.read_bytes()).hexdigest()
                shutil.copy2(path, dest)
                if before != sha256(path.read_bytes()).hexdigest() or before != sha256(dest.read_bytes()).hexdigest():
                    raise ValueError("A source file changed during its copy: " + str(relative))
                method = "verified_file_copy"
            records.append({"path": relative.as_posix(), "method": method,
                "bytes": dest.stat().st_size, "sha256": sha256(dest.read_bytes()).hexdigest()})
    runtime = target / ".runtime"
    runtime.mkdir(exist_ok=True)
    for name in ("users.json", "api_config.json"):
        origin = source / ".runtime" / name
        no_reparse(origin)
        raw = origin.read_bytes()
        value = json.loads(raw)
        if name == "api_config.json":
            # Rebind configuration paths only. Never rewrite user knowledge.
            def rebind(item):
                if isinstance(item, str):
                    return item.replace(str(source), str(target)).replace(source.as_posix(), target.as_posix())
                if isinstance(item, dict): return {k: rebind(v) for k, v in item.items()}
                if isinstance(item, list): return [rebind(v) for v in item]
                return item
            value = rebind(value)
        (runtime / name).write_bytes(encoded(value))
        if origin.read_bytes() != raw:
            raise ValueError("Source configuration changed during copying.")
    for name in ("core", "blobs", "projections", "codex-context", "sessions", "workspace"):
        (target / name).mkdir(exist_ok=True)
    new_marker = dict(old_marker)
    new_marker.pop("marker_sha256")
    new_marker.update(data_root=str(target), created_at_utc=datetime.now(timezone.utc).isoformat(),
        clone_source={"data_root": str(source), "marker_sha256": old_hash}, clone_port=10421)
    new_marker["marker_sha256"] = sha256(encoded(new_marker)).hexdigest()
    (runtime / "full-second-brain-preview.json").write_bytes(encoded(new_marker))
    receipt = {"schema_version": 1, "source_root": str(source), "target_root": str(target),
        "port": 10421, "source_mutated_by_clone": False, "source_stopped": False,
        "external_bindings_copied": False, "login_secret_copied": False,
        "cross_database_atomic_snapshot": False, "files": records}
    (runtime / "clone-receipt.json").write_bytes(encoded(receipt))
    return {"target": str(target), "files": len(records), "marker_sha256": new_marker["marker_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(create_copy(args.source, args.target)))


if __name__ == "__main__":
    main()
