"""Validated external links stored in Files & media database cells."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlsplit


def clean_external_file_link(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or not set(value) <= {"url", "name"}:
        raise ValueError("file link has unsupported fields")
    url = value.get("url")
    if (not isinstance(url, str) or not 0 < len(url) <= 2048
            or url != url.strip() or "\\" in url
            or any(character.isspace() or ord(character) < 32 for character in url)):
        raise ValueError("file link requires a safe URL")
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        valid = valid and not parsed.username and not parsed.password
        parsed.port
    except ValueError as exc:
        raise ValueError("file link requires a safe URL") from exc
    if not valid:
        raise ValueError("file link requires a safe URL")
    name = value.get("name") or parsed.hostname
    if (not isinstance(name, str) or not 0 < len(name.strip()) <= 255
            or any(ord(character) < 32 for character in name)):
        raise ValueError("file link name is invalid")
    return {"url": url, "name": name.strip()}
