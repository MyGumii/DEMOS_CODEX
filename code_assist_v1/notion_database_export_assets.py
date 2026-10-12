"""File references included in an authorized database HTML export."""

from __future__ import annotations

import re
import hashlib
from collections.abc import Mapping
from pathlib import Path

from . import notion_html_database


def referenced_file_ids(model: Mapping) -> tuple[str, ...]:
    """Only collect files actually shown in this view's rows and properties."""
    config = model.get("config", {})
    database = model["database"]
    properties = notion_html_database.visible_properties(database.get("properties", []), config)
    file_properties = [item["id"] for item in properties if item.get("type") == "files"]
    references: set[str] = set()
    for row in notion_html_database.visible_rows(database.get("rows", []), config):
        for block in notion_html_database._flat_item(row.get("body"), include_media=True):
            if block.get("type") not in notion_html_database.EXPORT_MEDIA_TYPES:
                continue
            file_id = (block.get("props") or {}).get("file_id")
            if isinstance(file_id, str) and file_id:
                references.add(file_id)
        for property_id in file_properties:
            value = row["values"].get(property_id)
            if isinstance(value, list):
                references.update(str(item["file_id"]) for item in value
                    if isinstance(item, Mapping) and isinstance(item.get("file_id"), str)
                    and item["file_id"])
    return tuple(sorted(references))


def zip_path(file_id: str, original_name: str) -> str:
    """Give every referenced file one collision-free, relative ZIP path."""
    identity = re.sub(r"[^A-Za-z0-9_-]+", "-", file_id).strip("-")[:100]
    if not identity:
        raise ValueError("invalid exported file identity")
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", original_name).strip(" .")[:100]
    return f"assets/{identity}-{name or 'attachment'}"


def authorized_manifest(authority, actor: str, model: Mapping) -> tuple[dict, ...]:
    manifest = []
    for file_id in referenced_file_ids(model):
        file = authority.get_file(actor, file_id)
        manifest.append({"file_id": file_id, "zip_path": zip_path(file_id, file["original_name"]),
            "path": file["path"], "size_bytes": file["size_bytes"],
            "content_sha256": file["content_sha256"], "document_id": file["document_id"]})
    if len({item["zip_path"].casefold() for item in manifest}) != len(manifest):
        raise ValueError("exported file paths collide")
    return tuple(manifest)


def verify_manifest(authority, actor: str, manifest: tuple[dict, ...]) -> None:
    for expected in manifest:
        current = authority.get_file(actor, expected["file_id"])
        if any(current[key] != expected[key] for key in
               ("path", "size_bytes", "content_sha256", "document_id")):
            from .notion_collaboration.canonical_authority import AuthorityUnavailable
            raise AuthorityUnavailable("exported file changed during download; retry")


def archived_owned_manifest(authority, actor: str, workspace_id: str,
                            document_id: str, database_id: str, model: Mapping) -> tuple[dict, ...]:
    """Owner-only access to files retained by one archived database snapshot."""
    from .notion_collaboration import database_trash
    from .notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied

    references = referenced_file_ids(model)
    if not references:
        return ()
    with authority._reader() as con:
        con.execute("BEGIN")
        database_trash._owner(authority, con, actor, workspace_id, document_id)
        if not database_trash.is_trashed(con, database_id):
            raise NotFoundOrDenied("not found or permission denied")
        retained = database_trash.trashed_file_ids(con, document_id)
        if not set(references).issubset(retained):
            raise AuthorityUnavailable("archived database file references changed")
        root = authority.storage_root.resolve(strict=True)
        manifest = []
        for file_id in references:
            row = con.execute("SELECT * FROM workspace_collaboration_files "
                "WHERE id=? AND workspace_id=? AND document_id=? AND state='active'",
                (file_id, workspace_id, document_id)).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            try:
                path = (root / str(row["blob_relative_path"])).resolve(strict=True)
                path.relative_to(root)
            except (OSError, ValueError) as exc:
                raise AuthorityUnavailable("archived database file is unavailable") from exc
            manifest.append({"file_id": file_id,
                "zip_path": zip_path(file_id, str(row["original_name"])),
                "path": path, "size_bytes": int(row["size_bytes"]),
                "content_sha256": str(row["content_sha256"]),
                "document_id": str(row["document_id"])})
    if len({item["zip_path"].casefold() for item in manifest}) != len(manifest):
        raise AuthorityUnavailable("exported file paths collide")
    return tuple(manifest)


def personal_manifest(store, actor: str, model: Mapping) -> tuple[dict, ...]:
    """Resolve visible personal DB file references with normal file permission checks."""
    manifest = []
    for file_id in referenced_file_ids(model):
        item = store.get_file(actor, file_id)
        try:
            root = Path(store._files_root(item["owner_user_id"])).resolve(strict=True)
            path = Path(item["path"]).resolve(strict=True)
            path.relative_to(root)
            digest, size = hashlib.sha256(), 0
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
        except (OSError, ValueError) as exc:
            raise FileNotFoundError("authorized file content is unavailable") from exc
        if size != item["size"]:
            raise FileNotFoundError("authorized file content changed")
        manifest.append({"file_id": file_id,
            "zip_path": zip_path(file_id, item["original_name"]),
            "path": path, "size_bytes": size,
            "content_sha256": "sha256:" + digest.hexdigest(),
            "page_id": str(item["page_id"]),
            "document_id": str(item["page_id"]),
            "owner_user_id": str(item["owner_user_id"]),
            "stored_name": str(item["stored_name"])})
    if len({item["zip_path"].casefold() for item in manifest}) != len(manifest):
        raise ValueError("exported file paths collide")
    return tuple(manifest)


def verify_personal_manifest(store, actor: str, manifest: tuple[dict, ...]) -> None:
    for expected in manifest:
        current = store.get_file(actor, expected["file_id"])
        if (any(str(current[key]) != str(expected[key]) for key in
                    ("page_id", "owner_user_id", "stored_name")) or
                int(current["size"]) != expected["size_bytes"] or
                Path(current["path"]).resolve(strict=True) != expected["path"]):
            raise FileNotFoundError("authorized file content changed")
