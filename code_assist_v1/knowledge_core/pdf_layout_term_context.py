"""V13-only, bounded same-source literal terminology context, not a glossary.

The caller must freshly admit/seal-check its plan and owned job first. This
helper grants no capability: it reads only those plan chunks and original job
wire fields. It never reads files, another Wiki, model output, or external data.
Selection proves an exact source location, NOT an acronym's semantic meaning.
"""
from hashlib import sha256
import json
import re

VERSION = 'pdf-layout-source-term-context-v1'
MAXIMUM_BYTES = 1024
MAXIMUM_SCAN_CHARACTERS = 4_194_304
MAXIMUM_SEED_CHARACTERS = 262_144
MAXIMUM_TERMS = 32
_ACRONYM = re.compile(r'(?<![A-Za-z0-9])(?:[A-Z](?:\.[A-Z]){1,5}\.?|[A-Z][A-Z0-9-]{1,11})(?![A-Za-z0-9])')
_DEFINITION = re.compile(r'\b(?:represents?|denotes?|means|respectively|stands?\s+for|known\s+as|defined\s+as)\b|(?:의미|정의|나타내|가리키|각각)',re.I)


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _tokens(text):
    return {m.group().rstrip('.') for m in _ACRONYM.finditer(text)}


def _original_fields(scope,job):
    messages=json.loads(job.messages_json)
    if (type(messages) is not list or len(messages)<2 or type(messages[1]) is not dict
            or messages[1].get('role')!='user' or type(messages[1].get('content')) is not str):
        raise ValueError('An original source wire message is required.')
    wire=json.loads(messages[1]['content'])
    if type(scope) is not dict or type(wire) is not dict:
        raise ValueError('An exact original job source scope is required.')
    names=('source_sections','visual_context','visual_inventory')
    if any(type(wire.get(n)) is not list or wire[n]!=scope.get(n) for n in names):
        raise ValueError('Original source wire and current scope differ.')
    return wire


def collect_term_contexts(plan,scope,job):
    """Return <=1 KiB canonical JSON of exact, source-chunk-relative excerpts.

    Undotted/dotted acronym matches plus nearby definition-like source wording
    are only selection hints. No expansion, normalization, reconstructed table,
    arbitrary first-definition fallback, or interpreted source fact is returned.
    Missing/ambiguous source context can legitimately return an empty list.
    """
    wire=_original_fields(scope,job)
    chunks=tuple(plan.chunks)
    source_by_id={}
    for chunk in chunks:
        if (type(chunk.page) is not int or type(chunk.text) is not str
                or type(chunk.chunk_id) is not str or not chunk.chunk_id
                or chunk.chunk_id in source_by_id):
            raise ValueError('Source chunk identity changed or is ambiguous.')
        source_by_id[chunk.chunk_id]=chunk
    source_rows={(c.page,c.text) for c in chunks}
    seeds,pages=[],set()
    for name in ('source_sections','visual_context'):
        for row in wire[name]:
            if (type(row) is not dict or type(row.get('page')) is not int
                    or type(row.get('text')) is not str or (row['page'],row['text']) not in source_rows):
                raise ValueError('Seed text must be an exact current source chunk.')
            seeds.append(row['text']); pages.add(row['page'])
    for row in wire['visual_inventory']:
        if (type(row) is not dict or type(row.get('page')) is not int
                or type(row.get('caption')) is not str):
            raise ValueError('A checked original visual caption is required.')
        seeds.append(row['caption']); pages.add(row['page'])
    if sum(map(len,seeds))>MAXIMUM_SEED_CHARACTERS:
        return []  # Supplementary context only; owned input is never truncated.
    relevant=set().union(*(_tokens(text) for text in seeds)) if seeds else set()
    if not relevant or not pages:
        return []
    relevant=set(sorted(relevant)[:MAXIMUM_TERMS])
    candidates=[]; scanned=0
    ordered=sorted(enumerate(chunks),key=lambda item:(min(abs(item[1].page-p) for p in pages),item[1].page,item[0]))
    for ordinal,chunk in ordered:
        if (type(chunk.page) is not int or type(chunk.text) is not str
                or type(chunk.chunk_id) is not str or not chunk.chunk_id):
            raise ValueError('Source chunk identity changed.')
        if scanned+len(chunk.text)>MAXIMUM_SCAN_CHARACTERS:
            continue
        scanned+=len(chunk.text)
        count=0
        for match in _ACRONYM.finditer(chunk.text):
            term=match.group().rstrip('.')
            if term not in relevant:
                continue
            count+=1
            if count>64: break
            start=max(0,match.start()-120); end=min(len(chunk.text),match.end()+240)
            excerpt=chunk.text[start:end]
            parenthesized=(match.start()>0 and chunk.text[match.start()-1]=='('
                and match.end()<len(chunk.text) and chunk.text[match.end()]==')')
            if not parenthesized and not _DEFINITION.search(excerpt):
                continue
            terms=sorted(_tokens(excerpt)&relevant)
            row={'terms':terms,'page':chunk.page,'chunk_id':chunk.chunk_id,
                'range_domain':'source_chunk_unicode_code_points','start':start,'end':end,
                'text':excerpt,'text_sha256':sha256(excerpt.encode('utf-8')).hexdigest(),
                'selection_reason':'matched_term_with_definition_like_literal_context',
                'definition_proven':False}
            distance=min(abs(chunk.page-p) for p in pages)
            candidates.append((distance,chunk.page,ordinal,start,row))
    result=[];covered=set();ranges=set()
    for _,_,_,_,row in sorted(candidates,key=lambda c:c[:4]):
        identity=(row['chunk_id'],row['start'],row['end'])
        if identity in ranges or set(row['terms'])<=covered:
            continue
        if len(_canonical(result+[row]).encode('utf-8'))>MAXIMUM_BYTES:
            continue
        # Reassert exact literal location before returning supplementary input.
        source=source_by_id[row['chunk_id']]
        if source.page!=row['page'] or source.text[row['start']:row['end']]!=row['text']:
            raise ValueError('Supplementary source identity is ambiguous or changed.')
        result.append(row);ranges.add(identity);covered.update(row['terms'])
    return result


SOURCE_DEFINITIONS_VERSION = 'pdf-layout-source-definition-context-v2'
MODEL_CONTEXT_MAXIMUM_BYTES = 1024
_NEAR_DEFINITION = re.compile(
    r'\b(?:represents?|denotes?|means|stands?\s+for|known\s+as|defined\s+as)\b', re.I)
_LITERAL_DOTTED = re.compile(
    r'(?<![A-Za-z0-9])[A-Za-z](?:\.[A-Za-z]){1,5}\.?(?![A-Za-z0-9])')


def project_source_definition_contexts(rows):
    """Model-only literal projection; full source provenance stays with rows."""
    if type(rows) is not list or len(rows) > MAXIMUM_TERMS:
        raise ValueError('Bounded source definition rows are required.')
    projected = []
    for row in rows:
        if (type(row) is not dict or type(row.get('page')) is not int
                or row['page'] < 1 or type(row.get('text')) is not str
                or type(row.get('start')) is not int or type(row.get('end')) is not int
                or not 0 <= row['start'] <= row['end']
                or row['end']-row['start'] != len(row['text'])
                or row.get('range_domain') != 'source_chunk_unicode_code_points'
                or row.get('text_sha256') != sha256(row['text'].encode('utf-8')).hexdigest()
                or row.get('definition_proven') is not False):
            raise ValueError('Literal unverified source definition rows are required.')
        projected.append([row['page'], row['text']])
    if len(_canonical(projected).encode('utf-8')) > MODEL_CONTEXT_MAXIMUM_BYTES:
        raise ValueError('Complete literal definition projection exceeds its budget.')
    return projected


def _definition_subjects(text):
    rest = _ACRONYM.sub('', text)
    rest = re.sub(r'\b(?:and|or)\b', '', rest, flags=re.I)
    return not rest.strip(' \t\r\n,;/&().-')


def _definition_sentence_end(text, start, limit):
    acronyms = (tuple(_ACRONYM.finditer(text, max(0, start-16), limit))
        + tuple(_LITERAL_DOTTED.finditer(text, max(0, start-16), limit)))
    for closing in re.finditer(r'[.!?](?=\s|$|[A-Z])', text[start:limit]):
        position = start+closing.start()
        following = text[position+1:position+2]
        if ((not following or following.isspace() or 'A' <= following <= 'Z')
                and not any(word.start() <= position < word.end() for word in acronyms)):
            return position+1
    return None


def _definition_range(text, match):
    """Selection hints only: an exact range never certifies an expansion."""
    near = text[match.end():match.end()+80]
    verb = _NEAR_DEFINITION.search(near)
    if verb is not None:
        if _definition_subjects(near[:verb.start()]):
            end = _definition_sentence_end(text, match.end()+verb.end(),
                min(len(text), match.end()+240))
            if end is not None:
                start = match.start()
                for prior in reversed(tuple(_ACRONYM.finditer(text, max(0, start-80), start))):
                    if not _definition_subjects(text[prior.end():start]):
                        break
                    start = prior.start()
                subjects = sorted(_tokens(text[start:match.end()+verb.start()]))
                return 0, start, end, subjects
    if (match.start() > 0 and text[match.start()-1] == '('
            and match.end() < len(text) and text[match.end()] == ')'):
        start = max(0, match.start()-120)
        while (start > 0 and (text[start-1].isalnum() or text[start-1] in '_-')
                and (text[start].isalnum() or text[start] in '_-')):
            start -= 1
            if start < max(0, match.start()-240):
                return None
        end = _definition_sentence_end(text, match.end()+1, min(len(text), match.end()+240))
        if end is not None and re.search(r'[a-z]', text[start:match.start()-1]):
            return 1, start, end, [match.group().rstrip('.')]
    return None


def collect_source_definition_contexts(plan, source_sections, *, visual_context=(), visual_inventory=()):
    """Bounded same-source literal definitions, without a fabricated job wire.

    Complete owned text is never modified. Strong literal selection hints rank
    before page proximity. Already-owned spans are not duplicated. Distinct
    external variants sharing a definition subject are admitted together or
    omitted together, never resolved by choosing a convenient source statement.
    The <=1 KiB budget governs only the compact model projection, not retained
    exact range/hash provenance. No selected row is a verified definition.
    """
    chunks = tuple(plan.chunks)
    original_chunks = tuple((c.chunk_id, c.page, c.text) for c in chunks)
    source_by_id = {}
    for chunk in chunks:
        if (type(chunk.page) is not int or chunk.page < 1 or type(chunk.text) is not str
                or type(chunk.chunk_id) is not str or not chunk.chunk_id
                or chunk.chunk_id in source_by_id):
            raise ValueError('Source chunk identity changed or is ambiguous.')
        source_by_id[chunk.chunk_id] = chunk
    source_rows = {(c.page, c.text) for c in chunks}
    owned_rows = set()
    seeds, pages = [], set()
    for rows in (source_sections, visual_context):
        if type(rows) not in (list, tuple):
            raise ValueError('Exact current source chunk rows are required.')
        for row in rows:
            if (type(row) is not dict or type(row.get('page')) is not int
                    or type(row.get('text')) is not str
                    or (row['page'], row['text']) not in source_rows):
                raise ValueError('Seed text must be an exact current source chunk.')
            seeds.append(row['text']); pages.add(row['page'])
            owned_rows.add((row['page'], row['text']))
    if type(visual_inventory) not in (list, tuple):
        raise ValueError('Checked original visual captions are required.')
    for row in visual_inventory:
        if (type(row) is not dict or type(row.get('page')) is not int or row['page'] < 1
                or type(row.get('caption')) is not str):
            raise ValueError('A checked original visual caption is required.')
        seeds.append(row['caption']); pages.add(row['page'])
    if sum(map(len, seeds)) > MAXIMUM_SEED_CHARACTERS or not pages:
        return []
    relevant = set().union(*(_tokens(text) for text in seeds))
    if not relevant:
        return []
    candidates, scanned = [], 0
    ordered = sorted(enumerate(chunks), key=lambda item:
        (min(abs(item[1].page-p) for p in pages), item[1].page, item[0]))
    for ordinal, chunk in ordered:
        if scanned+len(chunk.text) > MAXIMUM_SCAN_CHARACTERS:
            continue
        scanned += len(chunk.text)
        count = 0
        for match in _ACRONYM.finditer(chunk.text):
            if match.group().rstrip('.') not in relevant:
                continue
            count += 1
            if count > 64:
                break
            selected = _definition_range(chunk.text, match)
            if selected is None:
                continue
            priority, start, end, subjects = selected
            if (chunk.page, chunk.text) in owned_rows:
                continue  # The complete exact statement is already supplied.
            excerpt = chunk.text[start:end]
            row = dict(terms=sorted(_tokens(excerpt)&relevant), page=chunk.page,
                definition_terms=sorted(set(subjects)&relevant),
                chunk_id=chunk.chunk_id, range_domain='source_chunk_unicode_code_points',
                start=start, end=end, text=excerpt,
                text_sha256=sha256(excerpt.encode('utf-8')).hexdigest(),
                selection_reason='definition_like_literal_span', definition_proven=False)
            candidates.append((priority, min(abs(chunk.page-p) for p in pages),
                chunk.page, ordinal, start, row))
    # Connected literal subject groups preserve all conflicting observations.
    distinct = {}
    for candidate in sorted(candidates, key=lambda item: item[:5]):
        row = candidate[-1]
        identity = (row['chunk_id'], row['start'], row['end'])
        if identity in distinct:
            prior = distinct[identity][-1]
            prior['definition_terms'] = sorted(set(prior['definition_terms']) | set(row['definition_terms']))
        else:
            distinct[identity] = candidate
    candidates = tuple(distinct.values())
    parents = {term: term for term in relevant}
    def root(term):
        while parents[term] != term:
            term = parents[term]
        return term
    for *_, row in candidates:
        subjects = row['definition_terms']
        for term in subjects[1:]:
            parents[root(term)] = root(subjects[0])
    groups, ranges = {}, set()
    for candidate in sorted(candidates, key=lambda item: item[:5]):
        row = candidate[-1]
        identity = (row['chunk_id'], row['start'], row['end'])
        if identity in ranges:
            continue
        groups.setdefault(root(row['definition_terms'][0]), []).append(candidate)
        ranges.add(identity)
    result = []
    for group in sorted(groups.values(), key=lambda items: items[0][:5]):
        rows = [candidate[-1] for candidate in group]
        if len(result)+len(rows) > MAXIMUM_TERMS:
            continue
        projection = [[item['page'], item['text']] for item in (*result, *rows)]
        if len(_canonical(projection).encode('utf-8')) > MODEL_CONTEXT_MAXIMUM_BYTES:
            continue
        for row in rows:
            source = source_by_id[row['chunk_id']]
            if source.page != row['page'] or source.text[row['start']:row['end']] != row['text']:
                raise ValueError('Supplementary source identity is ambiguous or changed.')
        result.extend(rows)
    project_source_definition_contexts(result)
    if tuple((c.chunk_id, c.page, c.text) for c in chunks) != original_chunks:
        raise ValueError('Complete source chunks changed during literal selection.')
    return result
