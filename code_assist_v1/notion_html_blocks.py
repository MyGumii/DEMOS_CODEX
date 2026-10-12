"""Passive HTML block presentation shared by exports and public pages.

Callers own authorization and resource resolution. This module neither fetches
references nor modifies its input. Only known inline markup/styles are emitted.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import math
import re
from urllib.parse import urlsplit


COLORS = {
    'gray': ('#787774', '#f1f1ef'), 'brown': ('#9f6b53', '#f4eeee'),
    'orange': ('#d9730d', '#faeadc'), 'yellow': ('#cb912f', '#fbf3db'),
    'green': ('#448361', '#edf3ec'), 'blue': ('#337ea9', '#e7f3f8'),
    'purple': ('#9065b0', '#f4f0f7'), 'pink': ('#c14c8a', '#f9eef3'),
    'red': ('#d44c47', '#fdebec'),
}
CONTAINERS = {'callout', 'toggle', 'toggle_heading1', 'toggle_heading2', 'toggle_heading3', 'toggle_heading4'}


def text(value):
    return escape('' if value is None else str(value)).replace('\n', '<br>')


def number(value, maximum):
    try:
        value = float(value)
        return max(0, min(maximum, value)) if math.isfinite(value) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def props(block):
    return block.get('props') if isinstance(block.get('props'), dict) else {}


def safe_link(value):
    value = str(value or '').strip()
    if re.search(r'[\x00-\x20\\]', value):
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
            return value
        if parsed.scheme.lower() == 'mailto' and parsed.path:
            return value
    except ValueError:
        pass
    return ''


def inline_style(value):
    result = []
    for declaration in str(value or '').split(';'):
        key, _, val = declaration.partition(':')
        key, val = key.strip().lower(), val.strip().lower()
        if key in {'color', 'background-color'} and (
            re.fullmatch(r'#[0-9a-f]{3}(?:[0-9a-f]{3})?', val)
            or re.fullmatch(r'rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:0(?:\.\d+)?|1(?:\.0+)?))?\s*\)', val)
        ):
            result.append(f'{key}:{val}')
        elif key == 'font-weight' and val in {'bold', '600', '650', '700', '800'}:
            result.append('font-weight:700')
        elif key == 'font-style' and val == 'italic':
            result.append('font-style:italic')
        elif key in {'text-decoration', 'text-decoration-line'} and val in {'underline', 'line-through', 'underline line-through'}:
            result.append(f'text-decoration:{val}')
    return ';'.join(result)


class _Inline(HTMLParser):
    allowed = {'strong', 'b', 'em', 'i', 'u', 's', 'del', 'code', 'mark', 'span', 'sub', 'sup', 'a'}
    excluded = {'script', 'style', 'iframe', 'object', 'svg', 'math', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.stack, self.hidden = [], [], []

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            if tag in self.excluded:
                self.hidden.append(tag)
            return
        if tag in self.excluded:
            self.hidden.append(tag)
            return
        if tag == 'br':
            self.output.append('<br>')
        elif tag in self.allowed and len(self.stack) < 64:
            attrs = dict(attrs)
            style = inline_style(attrs.get('style'))
            if tag == 'span':
                color = attrs.get('data-notion-inline-color')
                background = attrs.get('data-notion-inline-background')
                if color in COLORS or color == 'default':
                    style += (';' if style else '') + 'color:' + (COLORS[color][0] if color in COLORS else '#37352f')
                if background in COLORS or background == 'default':
                    style += (';' if style else '') + 'background-color:' + (COLORS[background][1] if background in COLORS else 'transparent')
            extra = f' style="{escape(style, quote=True)}"' if style else ''
            if tag == 'a':
                href = safe_link(attrs.get('href'))
                if href:
                    extra += f' href="{escape(href, quote=True)}" rel="noopener noreferrer"'
            self.output.append(f'<{tag}{extra}>')
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in self.stack:
            while self.stack:
                opened = self.stack.pop()
                self.output.append(f'</{opened}>')
                if opened == tag:
                    break
        elif tag in {'p', 'div'}:
            self.output.append('<br>')

    def handle_data(self, value):
        if not self.hidden:
            self.output.append(text(value))

    def finish(self, value):
        self.feed(value[:100_000])
        self.close()
        self.output.extend(f'</{tag}>' for tag in reversed(self.stack))
        return ''.join(self.output)


def rich(block):
    html = props(block).get('rich_text_html')
    return _Inline().finish(html) if isinstance(html, str) and html else text(block.get('content'))


def content(block, children='', ordinal=1):
    """Return common content, or None for caller-owned resource/DB types."""
    kind, p = block.get('type') or 'paragraph', props(block)
    body = rich(block)
    if kind == 'callout':
        return f'<aside class="html-callout"><span class="html-callout-icon" aria-hidden="true">{text(p.get("icon") or "💡")}</span><div class="html-callout-body"><div>{body}</div>{children}</div></aside>'
    if kind in CONTAINERS:
        opened = '' if p.get('open') is False else ' open'
        level = kind[-1] if kind.startswith('toggle_heading') else ''
        return f'<details class="html-toggle html-toggle-{level}"{opened}><summary>{body}</summary><div class="html-toggle-children">{children}</div></details>'
    if kind in {'heading1', 'heading2', 'heading3', 'heading4'}:
        return f'<h{kind[-1]}>{body}</h{kind[-1]}>'
    if kind == 'bullet':
        return f'<ul><li>{body}</li></ul>'
    if kind == 'numbered':
        return f'<ol start="{ordinal}"><li>{body}</li></ol>'
    if kind == 'todo':
        checked = ' checked' if p.get('checked') else ''
        return f'<label class="html-todo"><input type="checkbox" disabled{checked}><span>{body}</span></label>'
    if kind == 'quote':
        return f'<blockquote>{body}</blockquote>'
    if kind == 'code':
        caption = f'<figcaption>{text(p.get("caption"))}</figcaption>' if p.get('caption') else ''
        return f'<figure><pre><code>{escape(str(block.get("content") or ""))}</code></pre>{caption}</figure>'
    if kind == 'divider':
        return '<hr>'
    if kind == 'simple_table':
        rows = p.get('rows') if isinstance(p.get('rows'), list) else []
        body_rows = []
        for row_index, row in enumerate(rows):
            if not isinstance(row, list):
                continue
            cells = []
            for column_index, value in enumerate(row):
                header = bool(p.get('header_row') and row_index == 0 or p.get('header_column') and column_index == 0)
                tag = 'th' if header else 'td'
                scope = ' scope="col"' if header and row_index == 0 and p.get('header_row') else ' scope="row"' if header else ''
                cells.append(f'<{tag}{scope}>{text(value)}</{tag}>')
            body_rows.append('<tr>' + ''.join(cells) + '</tr>')
        return '<div class="html-table-scroll"><table><tbody>' + ''.join(body_rows) + '</tbody></table></div>'
    if kind == 'paragraph':
        return f'<p>{body or "<br>"}</p>'
    return None


def render(blocks, render_fallback=None):
    """Render actual ownership, including explicit columns and nested tab bodies."""
    from .notion_collaboration.synced_tree import content_hierarchy
    from copy import deepcopy

    source = deepcopy([b for b in blocks if isinstance(b, dict)])
    inline_ids, used = set(), set()

    def identities(items, path=()):
        if len(path) > 64:
            raise ValueError('too many nested HTML blocks')
        for index, block in enumerate(items):
            if not block.get('id'):
                block['id'] = 'html-anonymous-' + '-'.join(map(str, (*path, index)))
            if block['id'] in used:
                raise ValueError('duplicate HTML block identity')
            used.add(block['id'])
            children = props(block).get('children') or []
            if children and all(isinstance(child, dict) for child in children):
                identities(children, (*path, index))
                inline_ids.update(child['id'] for child in children)

    identities(source)
    hierarchy = content_hierarchy(source)
    for identity in hierarchy.order:
        hierarchy.ancestors(identity)  # Reject disconnected cycles as well.
    panel_serial = 0

    def container(block):
        p = props(block)
        return (block.get('type') == 'paragraph' and p.get('layout_container') is True
                and not block.get('content') and not p.get('rich_text_html')
                and bool(hierarchy.children[block['id']]))

    def region(identities, parent=None, depth=0):
        if depth > 64:
            raise ValueError('too many nested HTML blocks')
        output, layout, counters = [], None, {}
        parent_props = props(parent) if parent else {}

        def flush():
            nonlocal layout
            if layout is not None:
                columns = ''.join('<div class="public-column">' + ''.join(items) + '</div>' for items in layout['items'])
                output.append(f'<div class="public-columns" style="--public-columns:{len(layout["items"])}">{columns}</div>')
            layout = None

        for identity in identities:
            block = hierarchy.indexed[identity]
            p = props(block)
            group = str(p.get('layout_group') or '')
            count = max(2, int(number(p.get('layout_columns') or 2, 5)))
            column = min(count - 1, int(number(p.get('layout_column'), count - 1)))
            scope = (group, column, str(p.get('tab_group') or ''), str(p.get('tab_id') or ''))
            counters[scope] = counters.get(scope, 0) + 1 if block.get('type') == 'numbered' else 0
            body = draw(block, parent, depth, counters[scope])
            inherited = group and group == parent_props.get('layout_group') and column == int(number(parent_props.get('layout_column'), 4))
            if group and not inherited:
                key = (group, count, scope[2], scope[3])
                if layout is None or layout['key'] != key:
                    flush(); layout = {'key': key, 'items': [[] for _ in range(count)]}
                layout['items'][column].append(body)
            else:
                flush(); output.append(body)
        flush()
        return ''.join(output)

    def draw(block, parent, depth, ordinal):
        nonlocal panel_serial
        p, kind = props(block), str(block.get('type') or 'paragraph')
        child_ids = hierarchy.children[block['id']]
        if container(block):
            body = region(child_ids, block, depth + 1)
        elif kind == 'tabs':
            tabs = [tab for tab in p.get('tabs', []) if isinstance(tab, dict) and tab.get('id')] if isinstance(p.get('tabs'), list) else []
            tabs = tabs[:20] or [{'id': 'default', 'name': '탭 1'}]
            active = p.get('active_tab_id')
            if active not in [tab['id'] for tab in tabs]:
                active = tabs[0]['id']
            labels, panels, assigned = [], [], set()
            for tab in tabs:
                panel_serial += 1
                panel = f'html-tab-{panel_serial}'
                selected = tab['id'] == active
                labels.append(f'<button type="button" role="tab" aria-selected="{str(selected).lower()}" aria-controls="{panel}" class="{"active" if selected else ""}" data-public-tab-target="{panel}">{text(tab.get("name") or "탭")}</button>')
                members = [identity for identity in child_ids if props(hierarchy.indexed[identity]).get('tab_id') == tab['id']]
                assigned.update(members)
                panels.append(f'<section role="tabpanel" id="{panel}" class="public-tab-panel"{" hidden" if not selected else ""}>{region(members, block, depth + 1)}</section>')
            body = '<div class="public-tabs"><div class="public-tab-list" role="tablist">' + ''.join(labels) + '</div>' + ''.join(panels) + '</div>'
            body += region([identity for identity in child_ids if identity not in assigned], block, depth + 1)
        else:
            children = region(child_ids, block, depth + 1)
            body = content(block, children, ordinal)
            if body is None:
                body = render_fallback(block) if render_fallback else f'<p>{rich(block)}</p>'
            if children and kind not in CONTAINERS:
                body += f'<div class="html-block-children">{children}</div>'
        if parent:
            step = 0 if parent.get('type') == 'tabs' or container(parent) else 1
            gap = (number(p.get('indent'), 32) if block['id'] in inline_ids else
                   max(0, number(p.get('indent'), 32) - number(props(parent).get('indent'), 32) - step))
        else:
            gap = number(p.get('indent'), 32)
        gap += number(p.get('visual_indent'), 8)
        styles = [f'margin-left:{gap * 24:g}px']
        color, background = COLORS.get(str(p.get('text_color'))), COLORS.get(str(p.get('background_color')))
        if color:
            styles.append('color:' + color[0])
        if background:
            styles.append('--html-block-bg:' + background[1])
        extra = ' html-layout-container' if container(block) else ''
        return f'<div class="html-block{extra}" data-block-type="{escape(kind, quote=True)}" data-html-block="{escape(str(block["id"]), quote=True)}" style="{";".join(styles)}">{body}</div>'

    roots = [identity for identity in hierarchy.order if hierarchy.parents[identity] is None]
    return region(roots)


CSS = '''
.html-block{min-width:0;margin-top:6px;margin-bottom:6px;overflow-wrap:anywhere;background:var(--html-block-bg,transparent);--html-block-bg:transparent}
.html-block>p,.html-block>ul,.html-block>ol,.html-block>blockquote,.html-block>figure{margin-top:0;margin-bottom:0}
.html-block>h1{font-size:30px;line-height:1.25;margin:24px 0 8px}.html-block>h2{font-size:24px;line-height:1.3;margin:22px 0 8px}.html-block>h3{font-size:20px;line-height:1.3;margin:16px 0 8px}.html-block>h4{font-size:18px;line-height:1.3;font-weight:600;margin:13px 0 8px}
.html-block blockquote{color:inherit;border-left:3px solid currentColor;padding-left:14px}
.html-callout{display:flex;gap:10px;padding:12px;border-radius:6px;background:var(--html-block-bg,#f1f1ef)}
.html-block[data-block-type=callout]{background:transparent;--html-block-bg:#f1f1ef}
.html-callout-icon{flex:0 0 24px;width:24px;line-height:24px}.html-callout-body{min-width:0;flex:1}
.html-callout-body>.html-block:last-child{margin-bottom:0}.html-toggle-children{padding-left:24px}
.html-block-children{padding-left:24px}.html-layout-container{margin-top:0;margin-bottom:0}
.html-toggle>summary{cursor:pointer}.html-toggle-1>summary{font-size:30px;font-weight:700}.html-toggle-2>summary{font-size:24px;font-weight:650}.html-toggle-3>summary{font-size:20px;font-weight:650}.html-toggle-4>summary{font-size:18px;font-weight:600;line-height:1.3}
.html-todo{display:flex;align-items:baseline;gap:8px}.html-todo input:checked+span{text-decoration:line-through;opacity:.65}
.html-table-scroll{max-width:100%;overflow:auto}.html-table-scroll table{border-collapse:collapse;width:100%}.html-table-scroll td,.html-table-scroll th{border:1px solid #e9e9e7;padding:7px 9px;text-align:left}.html-table-scroll th{background:#f7f6f3}
.html-block pre{white-space:pre;overflow:auto;padding:14px;background:#f7f6f3;border-radius:4px}.html-block figure{max-width:100%}.html-block figcaption{font-size:13px;color:#787774}.html-block code{font-family:Consolas,monospace}
.public-columns{display:grid;grid-template-columns:repeat(var(--public-columns,2),minmax(0,1fr));gap:28px;align-items:start}.public-column{min-width:0}
.public-tab-list{display:flex;overflow:auto;border-bottom:1px solid #e9e9e7}.public-tab-list button{padding:8px 11px;border:0;border-bottom:2px solid transparent;background:transparent;color:inherit;cursor:pointer}.public-tab-list button.active{border-bottom-color:currentColor;font-weight:600}.public-tab-panel{padding:10px 2px 4px}
@media(max-width:720px){.public-columns{grid-template-columns:1fr;gap:8px}}
'''

TAB_SCRIPT = '''document.addEventListener('click',function(event){
var button=event.target.closest('[data-public-tab-target]');if(!button)return;
var tabs=button.closest('.public-tabs');if(!tabs)return;
tabs.querySelectorAll('[data-public-tab-target]').forEach(function(item){if(item.closest('.public-tabs')!==tabs)return;var active=item===button;item.classList.toggle('active',active);item.setAttribute('aria-selected',String(active));});
tabs.querySelectorAll('.public-tab-panel').forEach(function(panel){if(panel.closest('.public-tabs')===tabs)panel.hidden=panel.id!==button.dataset.publicTabTarget;});
});'''
