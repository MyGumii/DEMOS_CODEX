"""Static database views for an already authorized export model."""
from calendar import monthrange
from copy import deepcopy
from datetime import date, timedelta
import math
import re

from . import notion_html_blocks as blocks


def display(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return '☑' if value else '☐'
    if isinstance(value, list):
        return ', '.join(display(item.get('name', item.get('original_name', '첨부파일')) if isinstance(item, dict) else item) for item in value)
    if isinstance(value, dict):
        return ' → '.join(str(value[key]) for key in ('start', 'end') if value.get(key))
    return str(value)


def _js(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return ','.join(_js(item) for item in value)
    if isinstance(value, dict):
        return '[object Object]'
    return str(value)


def _numeric(value):
    try:
        return float(value) if value else 0
    except (ValueError, TypeError):
        return math.nan


def _natural(value):
    return [(1, int(part)) if part.isdecimal() else (0, part.casefold())
            for part in re.split(r'(\d+)', _js(value))]


def _ordered_groups(groups, config):
    items = list(groups.items())
    mode = config.get('group_sort', 'manual')
    order = config.get('group_order') if isinstance(config.get('group_order'), list) else []
    if mode in {'asc', 'desc'}:
        items.sort(key=lambda entry: _natural(entry[0]), reverse=mode == 'desc')
        items.sort(key=lambda entry: entry[0] == '비어 있음')
    elif mode in {'count_asc', 'count_desc'}:
        items.sort(key=lambda entry: _natural(entry[0]))
        items.sort(key=lambda entry: len(entry[1]), reverse=mode == 'count_desc')
    elif order:
        positions = {label: index for index, label in enumerate(order)}
        items.sort(key=lambda entry: positions.get(entry[0], len(order)))
    return items


def _matches_filter_rule(row, rule):
    if not isinstance(rule, dict):
        return True
    key, operator = str(rule.get('property') or ''), str(rule.get('operator') or 'contains')
    expected = _js(rule.get('value') if rule.get('value') is not None else '').lower()
    raw = row['values'].get(key)
    if isinstance(raw, dict) and raw.get('start'):
        raw = raw['start']
    actual = _js(raw).lower()
    if operator == 'equals': return actual == expected
    if operator == 'not_equals': return actual != expected
    if operator == 'not_contains': return expected not in actual
    if operator == 'greater_than': return _numeric(actual) > _numeric(expected)
    if operator == 'less_than': return _numeric(actual) < _numeric(expected)
    if operator == 'on_or_before': return bool(actual) and actual <= expected
    if operator == 'on_or_after': return bool(actual) and actual >= expected
    if operator == 'not_empty': return bool(actual)
    if operator == 'empty': return not actual
    return expected in actual


def _matches_filter_group(row, group, depth=1):
    if not isinstance(group, dict) or not isinstance(group.get('children'), list) or depth > 3:
        return True
    children = group['children']
    if not children:
        return True
    def matches(child):
        return (_matches_filter_group(row, child, depth + 1) if isinstance(child, dict)
                and 'children' in child else _matches_filter_rule(row, child))
    return any(map(matches, children)) if group.get('logic') == 'or' else all(map(matches, children))


def visible_rows(rows, config):
    result = [dict(row, values={**row.get('cells', {}), **row.get('computed', {})}) for row in rows]
    for rule in config.get('filters', []) if isinstance(config.get('filters'), list) else []:
        if not isinstance(rule, dict):
            continue
        result = [row for row in result if _matches_filter_rule(row, rule)]
    group = config.get('filter_group')
    if isinstance(group, dict):
        result = [row for row in result if _matches_filter_group(row, group)]
    for rule in reversed(config.get('sorts', []) if isinstance(config.get('sorts'), list) else []):
        if isinstance(rule, dict):
            key = str(rule.get('property') or 'title')
            result.sort(key=lambda row: _natural(row['values'].get(key)), reverse=rule.get('direction') == 'desc')
    return result


def visible_properties(properties, config):
    visibility = config.get('property_visibility', {})
    visibility = visibility if isinstance(visibility, dict) else {}
    selected = [prop for prop in properties
                if prop.get('type') == 'title' or visibility.get(prop['id']) is not False]
    order = config.get('property_order', [])
    order = order if isinstance(order, list) else []
    original = {prop['id']: index for index, prop in enumerate(properties)}
    selected.sort(key=lambda prop: (
        order.index(prop['id']) if prop['id'] in order else len(order) + original[prop['id']],
        original[prop['id']],
    ))
    return selected


EXPORT_MEDIA_TYPES = frozenset({'image', 'file', 'video', 'audio', 'pdf'})


def _flat_item(body, *, include_media=True):
    result = []
    def visit(items, depth):
        if depth > 32:
            raise ValueError('database item hierarchy is too deep')
        for raw in items:
            if not isinstance(raw, dict):
                continue
            if not include_media and raw.get('type') in EXPORT_MEDIA_TYPES:
                continue
            item = deepcopy(raw); props = item.get('props') or {}; item['props'] = props
            children = props.pop('children', [])
            props['visual_indent'] = blocks.number(props.get('indent'), 8)
            props['indent'] = depth
            result.append(item)
            if isinstance(children, list):
                visit(children, depth + 1)
    visit(body.get('blocks', []) if isinstance(body, dict) else [], 0)
    return result


def _date(value):
    value = value.get('start') if isinstance(value, dict) else value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def render(model):
    if model.get('unavailable'):
        return '<p class="html-db-unavailable">접근할 수 없는 데이터베이스</p>'
    database, config = model['database'], model.get('config', {})
    kind = model.get('type') or 'table'
    properties = database.get('properties', [])
    visible = visible_properties(properties, config)
    title_prop = next((p['id'] for p in properties if p['type'] == 'title'), 'title')
    date_key = 'timeline_date_property' if kind == 'timeline' else 'calendar_date_property'
    date_prop = config.get(date_key) or next((p['id'] for p in properties if p['type'] == 'date'), 'date')
    rows = visible_rows(database.get('rows', []), config)
    asset_paths = model.get('asset_paths', {})
    asset_paths = asset_paths if isinstance(asset_paths, dict) else {}

    def value(row, prop):
        raw = row['values'].get(prop['id'])
        if prop['type'] == 'relation':
            labels = database.get('relation_labels', {}).get(prop['id'], {})
            return ', '.join(labels.get(str(identity), '연결된 페이지') for identity in raw) if isinstance(raw, list) else ''
        return display(raw)

    def value_html(row, prop):
        if prop.get('type') == 'files' and asset_paths:
            attachments = row['values'].get(prop['id'])
            if isinstance(attachments, list):
                links = []
                for attachment in attachments:
                    if not isinstance(attachment, dict):
                        continue
                    file_id = str(attachment.get('file_id') or '')
                    path = asset_paths.get(file_id)
                    label = blocks.text(attachment.get('name') or attachment.get('original_name') or '첨부파일')
                    links.append(f'<a href="{blocks.text(path)}" download>{label}</a>' if path else label)
                return ', '.join(links)
        return blocks.text(value(row, prop))

    def media(block):
        kind = block.get('type')
        if kind not in EXPORT_MEDIA_TYPES:
            return f'<p>{blocks.rich(block)}</p>'
        props = block.get('props') or {}
        path = asset_paths.get(str(props.get('file_id') or ''))
        if not path:
            return f'<p>{blocks.rich(block)}</p>'
        url = blocks.text(path)
        name = blocks.text(props.get('name') or block.get('content') or '첨부파일')
        if kind == 'image':
            preview = f'<img src="{url}" alt="{name}">'
        elif kind == 'video':
            preview = f'<video src="{url}" controls preload="metadata"></video>'
        elif kind == 'audio':
            preview = f'<audio src="{url}" controls preload="metadata"></audio>'
        elif kind == 'pdf':
            preview = f'<iframe src="{url}" title="{name}"></iframe>'
        else:
            preview = ''
        return f'<figure class="html-db-media">{preview}<figcaption><a href="{url}" download>{name}</a></figcaption></figure>'

    def title(row):
        label = blocks.text(display(row['values'].get(title_prop)) or '새 페이지')
        body = _flat_item(row.get('body'), include_media=model.get('include_media', True))
        return f'<details class="html-db-item"><summary>{label}</summary><div>{blocks.render(body, render_fallback=media)}</div></details>' if body else label

    def card(row):
        fields = ''.join(f'<div><dt>{blocks.text(prop["name"])}</dt><dd>{value_html(row, prop)}</dd></div>' for prop in visible if prop['id'] != title_prop)
        return f'<article class="html-db-card"><div class="html-db-card-title">{title(row)}</div><dl>{fields}</dl></article>'

    def table(items):
        head = ''.join(f'<th scope="col">{blocks.text(prop["name"])}</th>' for prop in visible)
        body = ''.join('<tr>' + ''.join(f'<td>{title(row) if prop["id"] == title_prop else value_html(row, prop)}</td>' for prop in visible) + '</tr>' for row in items)
        return f'<div class="html-table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    if kind == 'board':
        group = config.get('board_group_property') or next((p['id'] for p in properties if p['type'] in {'status', 'select'}), '')
        groups = {}
        for row in rows:
            groups.setdefault(display(row['values'].get(group)) or '상태 없음', []).append(row)
        body = '<div class="html-db-board">' + ''.join(f'<section><h4>{blocks.text(label)} <small>{len(items)}</small></h4>{"".join(card(row) for row in items)}</section>' for label, items in _ordered_groups(groups, config)) + '</div>'
    elif kind in {'list', 'gallery'}:
        body = f'<div class="html-db-{kind}">' + ''.join(card(row) for row in rows) + '</div>'
    elif kind == 'calendar':
        dates = [(row, _date(row['values'].get(date_prop))) for row in rows]
        anchor = _date(str(config.get('month') or '') + '-01') or next((day for _, day in dates if day), date.today())
        first = anchor.replace(day=1); start = first - timedelta(days=(first.weekday() + 1) % 7)
        weeks = math.ceil(((first.weekday() + 1) % 7 + monthrange(first.year, first.month)[1]) / 7)
        days = [start + timedelta(days=i) for i in range(weeks * 7)]
        cells = ''.join(f'<div class="html-db-day"><time datetime="{day.isoformat()}">{day.day}</time>{"".join(card(row) for row, event in dates if event == day)}</div>' for day in days)
        other = [row for row, event in dates if event not in days]
        body = f'<h4>{first.year}년 {first.month}월</h4><div class="html-db-calendar">{cells}</div>'
        if other:
            body += '<details><summary>다른 날짜 또는 날짜 없는 항목</summary>' + table(other) + '</details>'
    elif kind == 'timeline':
        dates = [(row, _date(row['values'].get(date_prop))) for row in rows]
        starts = [day for _, day in dates if day]
        start = min(starts) if starts else date.today()
        ends = [_date(row['values'].get(date_prop, {}).get('end')) if isinstance(row['values'].get(date_prop), dict) else day for row, day in dates]
        end = max([start + timedelta(days=1), *[day for day in starts + ends if day]])
        span = (end - start).days + 1
        lanes = []
        for (row, day), finish in zip(dates, ends):
            offset = ((day - start).days / span * 100) if day else 0
            length = max(1, ((max(day, finish or day) - day).days + 1) / span * 100) if day else 0
            bar = f'<span class="html-db-bar" style="margin-left:{offset:g}%;width:{length:g}%"></span>' if day else ''
            lanes.append(f'<div class="html-db-lane"><div>{title(row)}<small>{blocks.text(display(row["values"].get(date_prop)) or "날짜 없음")}</small></div><div>{bar}</div></div>')
        body = f'<p>{start.isoformat()} — {end.isoformat()}</p><div class="html-db-timeline">{"".join(lanes)}</div>'
    else:
        group_prop = next((p for p in properties if p['id'] == config.get('group_property')), None)
        if group_prop:
            groups = {}
            for row in rows:
                groups.setdefault(value(row, group_prop) or '비어 있음', []).append(row)
            body = ''.join(f'<h4>{blocks.text(label)} <small>{len(items)}</small></h4>{table(items)}' for label, items in _ordered_groups(groups, config))
        else:
            body = table(rows)
    header = f'<header><h3>{blocks.text(database.get("title") or "데이터베이스")}</h3><span>{blocks.text(model.get("view_name"))} · {len(rows)}개</span></header>'
    return f'<section class="html-database" data-view-type="{blocks.text(kind)}">{header}{body}</section>'


CSS = '''
.html-database{margin:18px 0}.html-database>header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}.html-database>header h3{margin:0;font-size:20px}.html-database>header span,.html-db-card dt,.html-db-lane small{font-size:12px;color:#787774}
.html-db-board{display:flex;gap:16px;overflow:auto;align-items:flex-start}.html-db-board>section{flex:1 0 220px;min-width:0}.html-db-board h4{margin:10px 0}.html-db-card{padding:10px 12px;border:1px solid #e9e9e7;border-radius:6px;background:#fff;margin:8px 0}.html-db-card-title{font-weight:600}.html-db-card dl{margin:8px 0 0;font-weight:400}.html-db-card dl>div{display:grid;grid-template-columns:minmax(60px,1fr) 2fr;gap:8px;margin:4px 0}.html-db-card dd{margin:0;overflow-wrap:anywhere}
.html-db-gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr));gap:12px}.html-db-list{display:grid;gap:4px}.html-db-list .html-db-card{margin:0}.html-db-item>summary{cursor:pointer}.html-db-item>div{font-weight:400;min-width:220px;margin-top:12px}
.html-db-calendar{display:grid;grid-template-columns:repeat(7,minmax(100px,1fr));overflow:auto;border-left:1px solid #e9e9e7;border-top:1px solid #e9e9e7}.html-db-day{min-height:100px;padding:5px;border-right:1px solid #e9e9e7;border-bottom:1px solid #e9e9e7}.html-db-day>time{font-size:12px;color:#787774}.html-db-day .html-db-card{font-size:12px;padding:5px}.html-db-day dl{display:none}
.html-db-timeline{overflow:auto}.html-db-lane{display:grid;grid-template-columns:180px minmax(240px,1fr);border-bottom:1px solid #e9e9e7;align-items:center;min-height:50px}.html-db-lane small{display:block}.html-db-bar{display:block;box-sizing:border-box;min-width:4px;height:22px;background:#e7f3f8;border:1px solid #337ea9;border-radius:4px}.html-db-unavailable{color:#787774;font-style:italic}
.html-db-media{max-width:100%;margin:8px 0}.html-db-media img,.html-db-media video,.html-db-media iframe{display:block;max-width:100%;max-height:560px}.html-db-media iframe{width:100%;height:420px;border:0}.html-db-media figcaption{font-size:13px;color:#787774}
'''
