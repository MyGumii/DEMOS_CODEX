"""Pure, source-owned extraction appendix; never equation reconstruction.

Selection is deliberately lexical, not semantic verification. A numbered
expression's nearby physical source lines, definition-like wording and numeric
parameter prose are retained verbatim. Neither a model response nor another
document can supply text. The public collector checks the actual admitted plan;
the private scanner is not a source capability. No files, providers or DBs are
read, and any declared work/output limit fails rather than selecting a prefix.
"""
from dataclasses import dataclass
from hashlib import sha256
from html import escape
import json
import re

from .wiki_authoring import AuthoringSourceError, AuthoringWorkLimitError

VERSION = 'pdf-source-literal-appendix-v1'
RANGE_DOMAIN = 'source_chunk_unicode_code_points'
_KINDS = {'numbered_equation_context', 'explicit_definition_context', 'mixed_literal_context'}
_FIELDS = {'kind', 'page', 'chunk_id', 'block_id', 'start', 'end', 'text',
    'text_sha256', 'source_text_sha256', 'range_domain'}
_IDENTIFIER = re.compile(r'[A-Za-z0-9_.-]{1,160}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_LABEL = re.compile(r'\((?:[A-Z]\.)?\d{1,3}(?:\.\d{1,3})*[a-z]?\)\.?[ \t]*$')
_OPERATOR = re.compile(r'[=≠≈≤≥<>→↔∝∈∉∑∫]|\\(?:frac|int|sum|leq?|geq?|neq|approx|in)\b')
_MATH_SYMBOL = re.compile(r'[α-ωΑ-Ω∇∂∞∏√]|\(cid:\d+\)|\\[A-Za-z]+')
_PROSE = re.compile(r'^(?:we|the|this|these|our|it|as|for|from|see|using|by|in|at|'
    r'if|when|because|therefore|hence|then|results?|tables?|figures?|fig\.?|'
    r'equations?|eq\.?|sections?|appendix|reference)\b', re.I)
_REFERENCE = re.compile(r'^\s*\[\d{1,4}\]')
_ACRONYM = re.compile(r'(?<![A-Za-z0-9])(?:[A-Z](?:\.[A-Z]){1,5}\.?|'
    r'[A-Z][A-Z0-9-]{1,11})(?![A-Za-z0-9])')
_DEFINITION = re.compile(r'\b(?:represents?|denotes?|means|stands?\s+for|'
    r'known\s+as|defined\s+as|refers?\s+to|respectively)\b|'
    r'(?:의미|정의|나타내|가리키|각각)', re.I)
_SYMBOL_DEFINITION = re.compile(r'^\s*(?:where|here[, ]|let\b)|'
    r'(?:여기서|이때|이라\s*정의|으로\s*정의)', re.I)
_PARAMETER = re.compile(r'\b(?:optimizer|learning\s+rate|weight\s+decay|'
    r'momentum|hyperparameters?|initial\s+value|batch\s+size|decay\s+factor)\b|'
    r'(?:학습률|가중치\s*감쇠|초기값|옵티마이저|하이퍼파라미터)', re.I)
_LAYER_PARAMETER = re.compile(r'\b(?:(?:input|output|hidden)\s+layers?|'
    r'activation(?:\s+functions?)?)\b|(?:입력|출력|은닉)\s*층|활성화\s*함수', re.I)
_LAYER_DEFINITION = re.compile(r'\b(?:utilizes?|employs?|uses?|'
    r'(?:is|are)\s+(?:set|used)|for\s+activation)\b|(?:사용|이용|적용)', re.I)
_NUMBER = re.compile(r'\d|[βγ]')
_REFERENCE_HEADING = re.compile(r'^\s*(?:[IVX]+[.)]\s*)?'
    r'(?:references|bibliography|참고\s*문헌)\s*$', re.I)
_MERGED_REFERENCE_HEADING = re.compile(r'(?:^|[ \t])(?:REFERENCES|BIBLIOGRAPHY)[ \t]*$')
_APPENDIX_HEADING = re.compile(r'^\s*(?:appendix|annex|부록)(?:\s+[A-Z0-9])?\s*$', re.I)
_NOTICE = ('## 원문 추출 보존: 수식·정의문 후보\n\n'
    '아래는 출처가 결속된 추출 텍스트 그대로입니다. 수식·약어·기호를 새로 복원하거나 '
    '의미·정확성을 검증한 결과가 아닙니다. 추출기의 줄 배치나 기호 손상이 남을 수 있으며, '
    '후보 탐색이 모든 수식·정의문을 발견했다는 보장은 없습니다.')


@dataclass(frozen=True, slots=True)
class LiteralAppendixLimits:
    source_bytes: int = 8_388_608
    chunks: int = 4096
    records: int = 2048
    output_bytes: int = 262_144

    def __post_init__(self):
        ceilings = {'source_bytes': 8_388_608, 'chunks': 4096,
            'records': 2048, 'output_bytes': 262_144}
        if any(type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= cap
                for name, cap in ceilings.items()):
            raise AuthoringWorkLimitError('Invalid literal appendix work limits.')


def _hash(text):
    return sha256(text.encode('utf-8')).hexdigest()


def _lines(text):
    result, cursor = [], 0
    for line in text.splitlines(keepends=True):
        result.append((cursor, cursor + len(line), line.rstrip('\n')))
        cursor += len(line)
    return result


def _expression(line):
    clean = line.strip()
    return bool(clean and not _PROSE.match(clean) and not _REFERENCE.match(clean)
        and _OPERATOR.search(clean))


def _math_continuation(line):
    clean = line.strip()
    return bool(clean and not _PROSE.match(clean) and not _REFERENCE.match(clean)
        and (_OPERATOR.search(clean) or _MATH_SYMBOL.search(clean)
            or re.fullmatch(r'[A-Za-z0-9_{}()[\].,+*/^| -]{1,80}', clean)
                and len(re.findall(r'[A-Za-z]{3,}', clean)) <= 1))


def _context_end(lines, index, allowed):
    """Keep wrapped source context, without traversing unrelated paragraphs."""
    last = index
    for following in range(index + 1, min(len(lines), index + 1 + allowed)):
        line = lines[following][2]
        if (not line.strip() or _REFERENCE.match(line) or _REFERENCE_HEADING.fullmatch(line)
                or _MERGED_REFERENCE_HEADING.search(line)
                or _LABEL.search(line) and _expression(_LABEL.sub('', line))):
            break
        last = following
        if line.rstrip().endswith(('.', '。', '다.')):
            break
    if (last == index + allowed and last + 1 < len(lines)
            and lines[last + 1][2].strip()
            and not lines[last][2].rstrip().endswith(('.', '。', '다.'))
            and not _REFERENCE.match(lines[last + 1][2])):
        raise AuthoringWorkLimitError('Complete source literal context exceeds its line bound.')
    return last


def _equation_tail(lines, index, allowed=8):
    """Preserve adjacent extracted subscripts/continuation, never rebuild them."""
    last = index
    for following in range(index + 1, min(len(lines), index + 1 + allowed)):
        line = lines[following][2]
        if _LABEL.search(line) or not _math_continuation(line):
            break
        last = following
    if (last == index + allowed and last + 1 < len(lines)
            and not _LABEL.search(lines[last + 1][2])
            and _math_continuation(lines[last + 1][2])):
        raise AuthoringWorkLimitError('Complete extracted equation continuation exceeds its line bound.')
    return last


def _literal_ranges(text, *, in_references=False):
    """Conservative physical-line candidates; exact source offsets only."""
    rows, candidates = _lines(text), []
    for index, (start, end, line) in enumerate(rows):
        if _REFERENCE_HEADING.fullmatch(line) or _MERGED_REFERENCE_HEADING.search(line):
            in_references = True
            continue
        if _APPENDIX_HEADING.fullmatch(line):
            in_references = False
        if in_references or _REFERENCE.match(line):
            continue
        label = _LABEL.search(line)
        if label:
            expression_at = index if _expression(line[:label.start()]) else None
            if not line[:label.start()].strip():
                # A standalone label needs a nearby actual expression, not
                # merely an equation mention or a parenthesized reference.
                for preceding in range(index - 1, max(-1, index - 9), -1):
                    candidate = rows[preceding][2]
                    if not candidate.strip() or _REFERENCE.match(candidate):
                        break
                    if _expression(candidate):
                        expression_at = preceding
                        break
                    if not _math_continuation(candidate):
                        break
            if expression_at is not None:
                first = expression_at
                for preceding in range(first - 1, max(-1, first - 5), -1):
                    if not _math_continuation(rows[preceding][2]):
                        break
                    first = preceding
                last = _equation_tail(rows, index)
                # Symbol/parameter explanations commonly follow the label.
                # Only explicit nearby wording opens this continuation window.
                for following in range(index + 1, min(len(rows), index + 5)):
                    candidate = rows[following][2]
                    if not candidate.strip() or _REFERENCE.match(candidate):
                        break
                    if _SYMBOL_DEFINITION.search(candidate) or _PARAMETER.search(candidate):
                        last = _context_end(rows, following, 32)
                        break
                    if _LABEL.search(candidate):
                        break
                candidates.append((rows[first][0], rows[last][1], 'numbered_equation_context'))
        # Explicit acronym wording is retained as a literal, not expanded into
        # a guessed glossary. Parenthetical uppercase abbreviations require
        # preceding alphabetic wording rather than a citation/year alone.
        nearby = '\n'.join(row[2] for row in rows[max(0, index - 1):min(len(rows), index + 3)])
        parenthetical = any(match.start() > 0 and match.end() < len(line)
            and line[match.start() - 1] == '(' and line[match.end()] == ')'
            and re.search(r'[A-Za-z가-힣]{3,}', line[max(0, match.start() - 120):match.start() - 1])
            for match in _ACRONYM.finditer(line))
        acronym_definition = bool((_DEFINITION.search(line) and _ACRONYM.search(nearby))
            or parenthetical)
        parameter_definition = bool(_PARAMETER.search(nearby) and _NUMBER.search(nearby)
            and (_PARAMETER.search(line) or _NUMBER.search(line)))
        layer_definition = bool(_LAYER_PARAMETER.search(nearby) and _LAYER_DEFINITION.search(nearby)
            and (_LAYER_PARAMETER.search(line) or _LAYER_DEFINITION.search(line)))
        symbol_definition = bool(_SYMBOL_DEFINITION.search(line)
            and (_DEFINITION.search(nearby) or _OPERATOR.search(nearby)))
        if acronym_definition or parameter_definition or layer_definition or symbol_definition:
            first = index
            if (index and rows[index - 1][2].strip() and not _REFERENCE.match(rows[index - 1][2])
                    and not rows[index - 1][2].rstrip().endswith('.')):
                first -= 1
            last = _context_end(rows, index, 32)
            candidates.append((rows[first][0], rows[last][1], 'explicit_definition_context'))
    merged = []
    for start, end, kind in sorted(candidates):
        if merged and start <= merged[-1][1]:
            previous = merged.pop()
            merged.append((previous[0], max(end, previous[1]),
                previous[2] if previous[2] == kind else 'mixed_literal_context'))
        else:
            merged.append((start, end, kind))
    return merged, in_references


def _collect_owned(catalog, chunks, *, limits=LiteralAppendixLimits()):
    """Private pure scanner for already-attested exact source partitions."""
    if type(limits) is not LiteralAppendixLimits or type(catalog) is not dict:
        raise AuthoringSourceError('Exact source literal collection inputs are required.')
    chunks = tuple(chunks)
    if len(chunks) > limits.chunks:
        raise AuthoringWorkLimitError('Complete source literal chunk inventory exceeds its bound.')
    if any(type(key) is not str or not _IDENTIFIER.fullmatch(key)
            or type(owner) is not dict or owner.get('kind') not in ('prose', 'inferred_grid')
            or owner.get('block_id') != key or owner.get('source_id') != key
            or type(owner.get('page')) is not int or owner['page'] < 1
            or type(owner.get('text')) is not str for key, owner in catalog.items()):
        raise AuthoringSourceError('Exact original source block catalog is required.')
    ids, groups, total = set(), {}, 0
    for index, chunk in enumerate(chunks, 1):
        if (type(chunk.ordinal) is not int or chunk.ordinal != index
                or type(chunk.page) is not int or chunk.page < 1
                or type(chunk.chunk_id) is not str or not _IDENTIFIER.fullmatch(chunk.chunk_id)
                or type(chunk.block_id) is not str or not _IDENTIFIER.fullmatch(chunk.block_id)
                or chunk.chunk_id in ids or type(chunk.text) is not str
                or type(chunk.start) is not int or type(chunk.end) is not int):
            raise AuthoringSourceError('Original source chunk identity is unavailable or ambiguous.')
        ids.add(chunk.chunk_id)
        owner = catalog.get(chunk.block_id)
        if (type(owner) is not dict or owner.get('kind') != 'prose'
                or owner.get('source_id') != chunk.block_id or owner.get('block_id') != chunk.block_id
                or owner.get('page') != chunk.page or owner.get('range_domain') != 'pdf_prose_codepoints'
                or type(owner.get('text')) is not str
                or owner.get('range') != [0, len(owner['text'])]
                or not 0 <= chunk.start <= chunk.end <= len(owner['text'])
                or owner['text'][chunk.start:chunk.end] != chunk.text):
            raise AuthoringSourceError('Literal appendix text differs from its actual owned source.')
        if any(ord(char) < 32 and char not in '\n\t' for char in chunk.text):
            raise AuthoringSourceError('Unchanged source literals contain unsupported display controls.')
        total += len(chunk.text.encode('utf-8'))
        if total > limits.source_bytes:
            raise AuthoringWorkLimitError('Complete source literal scan exceeds its byte bound.')
        groups.setdefault(chunk.block_id, []).append(chunk)
    # Do not borrow the unowned remainder of a split source block. The actual
    # plan owns an exact complete partition, including its blank source ranges.
    for key, owner in catalog.items():
        if owner.get('kind') != 'prose':
            continue
        parts, cursor = groups.get(key, []), 0
        for chunk in parts:
            if chunk.start != cursor:
                raise AuthoringSourceError('Literal source ranges are incomplete, overlapping or reordered.')
            cursor = chunk.end
        if cursor != len(owner['text']):
            raise AuthoringSourceError('Literal source block has an unowned remainder.')
    result, in_references = [], False
    for key, parts in groups.items():
        owner = catalog[key]
        ranges, in_references = _literal_ranges(owner['text'], in_references=in_references)
        for first, last, kind in ranges:
            for chunk in parts:
                start, end = max(first, chunk.start), min(last, chunk.end)
                if start >= end:
                    continue
                start, end = start - chunk.start, end - chunk.start
                literal = chunk.text[start:end]
                result.append({'kind': kind, 'page': chunk.page, 'chunk_id': chunk.chunk_id,
                    'block_id': chunk.block_id, 'start': start, 'end': end, 'text': literal,
                    'text_sha256': _hash(literal), 'source_text_sha256': _hash(chunk.text),
                    'range_domain': RANGE_DOMAIN})
                if len(result) > limits.records:
                    raise AuthoringWorkLimitError('Complete literal appendix inventory exceeds its bound.')
    # Rendering is part of admission: never return a prefix that fits while
    # silently losing a later definition, condition or numbered expression.
    render_literal_appendix(result, limits=limits)
    return result


def collect_literal_records(plan, *, limits=LiteralAppendixLimits()):
    """Collect exact literals only from the freshly checked actual PDF plan."""
    from .pdf_layout_retention import checked_admitted_plan
    original = checked_admitted_plan(plan)
    return _collect_owned(original._catalog, original.chunks, limits=limits)


def render_literal_appendix(records, *, limits=LiteralAppendixLimits()):
    """Render escaped source text; no normalization or invented math markup."""
    if type(limits) is not LiteralAppendixLimits or type(records) is not list:
        raise AuthoringSourceError('Exact source literal appendix records are required.')
    if len(records) > limits.records:
        raise AuthoringWorkLimitError('Complete literal appendix inventory exceeds its bound.')
    if not records:
        return ''
    lines, seen = [_NOTICE], set()
    for row in records:
        if (type(row) is not dict or set(row) != _FIELDS or row['kind'] not in _KINDS
                or type(row['page']) is not int or row['page'] < 1
                or type(row['start']) is not int or type(row['end']) is not int
                or not 0 <= row['start'] < row['end'] or type(row['text']) is not str
                or row['end'] - row['start'] != len(row['text'])
                or row['range_domain'] != RANGE_DOMAIN
                or not all(type(row[name]) is str and _IDENTIFIER.fullmatch(row[name])
                    for name in ('chunk_id', 'block_id'))
                or not all(type(row[name]) is str and _SHA.fullmatch(row[name])
                    for name in ('text_sha256', 'source_text_sha256'))
                or _hash(row['text']) != row['text_sha256']
                or any(ord(char) < 32 and char not in '\n\t' for char in row['text'])):
            raise AuthoringSourceError('Source literal appendix record changed.')
        identity = (row['chunk_id'], row['start'], row['end'])
        if identity in seen:
            raise AuthoringSourceError('Duplicate literal appendix source range.')
        seen.add(identity)
        lines.append('### 원문 추출 · p. ' + str(row['page']) + '\n\n'
            '소스: `' + row['chunk_id'] + '` · 문자 범위 [' + str(row['start']) + ', '
            + str(row['end']) + ')\n\n<pre>' + escape(row['text']) + '</pre>')
    result = '\n\n'.join(lines) + '\n'
    if len(result.encode('utf-8')) > limits.output_bytes:
        raise AuthoringWorkLimitError('Complete literal appendix output exceeds its byte bound.')
    return result


def canonical_records(records):
    """The exact stable JSON used by a forward wrapper's records digest."""
    return json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
