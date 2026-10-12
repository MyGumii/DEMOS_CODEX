"""Portable, single-snapshot database exports."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import tempfile
import zipfile
from collections.abc import Mapping

from . import notion_html_database


class ExportAttachmentChanged(RuntimeError):
    """An attachment disappeared or changed while building an export."""


def database_csv(model: Mapping, *, current_view: bool) -> str:
    """Render the same rows and properties selected for a database HTML export."""
    database = model["database"]
    config = model.get("config", {}) if current_view else {}
    properties = list(database.get("properties", []))
    rows = list(database.get("rows", []))
    if current_view:
        properties = notion_html_database.visible_properties(properties, config)
        rows = notion_html_database.visible_rows(rows, config)
    labels = database.get("relation_labels", {})
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([str(prop.get("name") or prop["id"]) for prop in properties])
    for row in rows:
        values = row.get("values") if current_view else None
        if not isinstance(values, Mapping):
            values = {**row.get("cells", {}), **row.get("computed", {})}
        fields = []
        for prop in properties:
            prop_id = str(prop["id"])
            value = values.get(prop_id)
            relation = labels.get(prop_id, {}) if isinstance(labels, Mapping) else {}
            if prop.get("type") == "relation" and isinstance(value, list) and relation:
                fields.append(", ".join(relation.get(str(identity), "연결된 페이지") for identity in value))
            else:
                fields.append(notion_html_database.display(value))
        writer.writerow(fields)
    return output.getvalue()


def _archive(title: str, files: tuple[tuple[str, str], ...], *, identity: str = "") -> bytes:
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", str(title)).strip(" .")[:80] or "Database"
    safe_identity = re.sub(r'[^A-Za-z0-9_-]+', "-", identity).strip("-")[:64]
    stem = f"{safe_title} {safe_identity}" if safe_identity else safe_title
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for ending, contents in files:
            item = zipfile.ZipInfo(f"{stem}{ending}", date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            encoded = contents.removeprefix("\ufeff").encode("utf-8-sig") \
                if ending.endswith(".csv") else contents.encode("utf-8")
            archive.writestr(item, encoded)
    return output.getvalue()


def html_archive(title: str, html: str, csv_text: str, *, identity: str = "") -> bytes:
    """Notion's HTML database download contains both HTML and CSV."""
    return _archive(title, ((".html", html), (".csv", csv_text)), identity=identity)


def html_archive_with_assets(title: str, html: str, csv_text: str, *,
                             identity: str = "", assets=()):
    """Build a seekable HTML ZIP without loading attachment bytes into memory."""
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", str(title)).strip(" .")[:80] or "Database"
    safe_identity = re.sub(r'[^A-Za-z0-9_-]+', "-", identity).strip("-")[:64]
    stem = f"{safe_title} {safe_identity}" if safe_identity else safe_title
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                             allowZip64=True) as archive:
            for ending, contents in ((".html", html), (".csv", csv_text)):
                item = zipfile.ZipInfo(f"{stem}{ending}", date_time=(1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                encoded = contents.removeprefix("\ufeff").encode("utf-8-sig") \
                    if ending == ".csv" else contents.encode("utf-8")
                archive.writestr(item, encoded)
            for asset in assets:
                path = str(asset["zip_path"])
                if (not path.startswith("assets/") or path.count("/") != 1
                        or path.endswith("/") or "\\" in path
                        or path.split("/", 1)[1] in {".", ".."}):
                    raise ValueError("invalid exported asset path")
                item = zipfile.ZipInfo(asset["zip_path"], date_time=(1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                digest, size = hashlib.sha256(), 0
                try:
                    with open(asset["path"], "rb") as source, archive.open(item, "w", force_zip64=True) as target:
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                            size += len(chunk)
                            target.write(chunk)
                except OSError as exc:
                    raise ExportAttachmentChanged("exported attachment is unavailable") from exc
                if ("sha256:" + digest.hexdigest(), size) != (
                        asset["content_sha256"], asset["size_bytes"]):
                    raise ExportAttachmentChanged("exported attachment changed while packaging")
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise


def markdown_csv_archive(title: str, current_csv: str, all_csv: str, *, identity: str = "") -> bytes:
    """A database's Markdown & CSV download has view and all-row CSV files."""
    return _archive(title, ((".csv", current_csv), ("_all.csv", all_csv)), identity=identity)


def empty_archive() -> bytes:
    """Notion downloads an empty ZIP for an archived database with no rows."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED):
        pass
    return output.getvalue()
