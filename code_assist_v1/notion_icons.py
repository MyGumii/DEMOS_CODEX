"""Render the same compact icon values in authenticated/public HTML exports."""
from __future__ import annotations

import json
import re
import base64
import hashlib
from functools import lru_cache
from html import escape
from pathlib import Path


@lru_cache(maxsize=1)
def _catalog() -> dict:
    return json.loads((Path(__file__).parent / 'notion_static/assets/icons/catalog.json').read_text(encoding='utf-8'))


def icon_html(value: object, *, file_urls: dict[str, str] | None = None, embed_emoji: bool = False) -> str:
    """file_urls is supplied only from files already authorized by the caller."""
    raw = str(value or '').strip()
    if not raw:
        return ''
    catalog = _catalog()
    vectors = {item['id']: item for item in catalog['vectors']}
    colors = {item['id']: item for item in catalog['colors']}
    if raw == 'list':
        raw = 'ni:list:default'
    vector = re.fullmatch(r'ni:([a-z][a-z0-9-]{0,32}):([a-z]+)', raw)
    if vector and vector[1] in vectors and vector[2] in colors:
        path, color = vectors[vector[1]]['path'], colors[vector[2]]['light']
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="72" height="72" '
                f'fill="none" stroke="{escape(color)}" stroke-width="1.65" stroke-linecap="round" '
                f'stroke-linejoin="round" aria-hidden="true"><path d="{escape(path, quote=True)}"/></svg>')
    file = re.fullmatch(r'nf:((?:f_|file_)[a-zA-Z0-9_-]{1,56})', raw)
    if file:
        url = (file_urls or {}).get(file[1], '')
        if url.startswith(('/api/notion/', '/public/notion/')):
            return f'<img src="{escape(url, quote=True)}" width="124" height="124" style="width:124.32px;height:124.32px;object-fit:cover;object-position:center;border-radius:4px" alt="">'
        return icon_html('ni:doc:default')
    if raw.startswith(('ni:', 'nf:', 'nw:', 'np:', 'ne:')):
        return icon_html('ni:doc:default')
    emoji = next((item for item in catalog['emojis'] if item['value'] == raw), None)
    if emoji:
        url = f'/notion_static/assets/emoji/{emoji["asset"]}.svg'
        if embed_emoji:
            # Only bundled catalog assets may become SVG data URLs; uploads are raster-only.
            asset = Path(__file__).parent / 'notion_static/assets/emoji' / f'{emoji["asset"]}.svg'
            url = 'data:image/svg+xml;base64,' + base64.b64encode(asset.read_bytes()).decode('ascii')
        return (f'<img src="{escape(url, quote=True)}" '
                'width="72" height="72" alt="">')
    return escape(raw)


def icon_attribution_html(value: object) -> str:
    if not any(item['value'] == str(value or '').strip() for item in _catalog()['emojis']):
        return ''
    return ('<small class="icon-credit">Twemoji graphics by Twitter, Inc. and other contributors · '
            '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a></small>')


def export_icon_html(value: object, *, page_id: str, resolve_file,
                     resolve_custom_emoji=None) -> str:
    """Read one authorized, page-owned raster outside the caller's DB transaction."""
    raw = str(value or '').strip()
    custom = re.fullmatch(r'ne:(we_[a-f0-9]{40})', raw)
    if custom:
        if resolve_custom_emoji is None:
            return icon_html(raw)
        try:
            item = resolve_custom_emoji(custom[1])
        except FileNotFoundError:
            return icon_html(raw)
        payload = item.get('raw')
        mime = item.get('mime_type')
        digest = item.get('content_sha256')
        if not isinstance(payload, bytes) or mime not in ('image/png', 'image/jpeg', 'image/webp'):
            return icon_html(raw)
        valid = ((mime == 'image/png' and payload.startswith(b'\x89PNG\r\n\x1a\n'))
                 or (mime == 'image/jpeg' and payload.startswith(b'\xff\xd8\xff'))
                 or (mime == 'image/webp' and payload[:4] == b'RIFF' and payload[8:12] == b'WEBP'))
        if not valid or not 0 < len(payload) <= 2 * 1024 * 1024 \
                or digest != 'sha256:' + hashlib.sha256(payload).hexdigest():
            return icon_html(raw)
        encoded = base64.b64encode(payload).decode('ascii')
        return f'<img src="data:{mime};base64,{encoded}" width="124" height="124" style="width:124.32px;height:124.32px;object-fit:cover;object-position:center;border-radius:4px" alt="">'
    match = re.fullmatch(r'nf:((?:f_|file_)[a-zA-Z0-9_-]{1,56})', raw)
    if not match:
        return icon_html(raw, embed_emoji=True)
    item = resolve_file(match[1])
    if item.get('page_id', item.get('document_id')) != page_id:
        return icon_html(raw)
    mime = item.get('mime_type')
    size = item.get('size_bytes', item.get('size', 0))
    if mime not in ('image/png', 'image/jpeg', 'image/webp') or not 0 < size <= 2 * 1024 * 1024:
        return icon_html(raw)
    with Path(item['path']).open('rb') as stream:
        payload = stream.read(2 * 1024 * 1024 + 1)
    valid = ((mime == 'image/png' and payload.startswith(b'\x89PNG\r\n\x1a\n'))
             or (mime == 'image/jpeg' and payload.startswith(b'\xff\xd8\xff'))
             or (mime == 'image/webp' and payload[:4] == b'RIFF' and payload[8:12] == b'WEBP'))
    digest = 'sha256:' + hashlib.sha256(payload).hexdigest()
    if not valid or len(payload) != size or item.get('content_sha256', digest) != digest:
        return icon_html(raw)
    encoded = base64.b64encode(payload).decode('ascii')
    return f'<img src="data:{mime};base64,{encoded}" width="124" height="124" style="width:124.32px;height:124.32px;object-fit:cover;object-position:center;border-radius:4px" alt="">'
