"""Versioned display-only decoding of malformed JSON TeX carriage returns.

Provider bytes are never changed. Only a complete, non-code math segment and
an unambiguous, whitelisted r-command tail permit recovering its backslash.
Unknown carriage returns fail; they are not silently converted to newlines.
"""
import re

from .wiki_authoring import AuthoringOutputError

VERSION = 'pdf-layout-math-format-v1'
_R_COMMAND = re.compile(r'\r(?=(?:ho|ight|angle|ceil|floor|brace|vert)(?![A-Za-z]))')


def _escaped(text, index):
    previous = index - 1
    while previous >= 0 and text[previous] == '\\':
        previous -= 1
    return (index - previous - 1) % 2 == 1


def _code_mask(text):
    """Protect real-length fences, indented lines, and exact-length code spans."""
    mask = bytearray(len(text))
    fence = None
    for line in re.finditer(r'[^\n]*(?:\n|$)', text):
        if not line.group():
            continue
        marker = re.match(r' {0,3}(`{3,}|~{3,})(.*)', line.group().rstrip('\n'))
        if fence is not None:
            mask[line.start():line.end()] = b'\1' * (line.end() - line.start())
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
            mask[line.start():line.end()] = b'\1' * (line.end() - line.start())
        elif line.group().startswith(('    ', '\t')):
            mask[line.start():line.end()] = b'\1' * (line.end() - line.start())
    runs = list(re.finditer(r'`+', text))
    index = 0
    while index < len(runs):
        first = runs[index]
        if mask[first.start()] or _escaped(text, first.start()):
            index += 1
            continue
        last = next((n for n in range(index + 1, len(runs))
            if not mask[runs[n].start()] and len(runs[n].group()) == len(first.group())), None)
        end = len(text) if last is None else runs[last].end()
        mask[first.start():end] = b'\1' * (end - first.start())
        index = len(runs) if last is None else last + 1
    return mask


def _math_segments(text, mask):
    # Repeated backslashes must not cause quadratic escape-parity scans.
    escapes = bytearray(len(text))
    slash_count = 0
    for position, char in enumerate(text):
        escapes[position] = slash_count % 2
        slash_count = slash_count + 1 if char == '\\' else 0
    index = 0
    remaining_work = 4 * len(text)
    while index < len(text):
        if mask[index] or escapes[index]:
            index += 1
            continue
        dollar = text[index] == '$'
        if dollar:
            end_run = index + 1
            while end_run < len(text) and text[end_run] == '$':
                end_run += 1
            opening = text[index:end_run]
            if len(opening) not in (1, 2):
                index = end_run
                continue
            closing = opening
        elif text[index:index + 2] in ('\\(', '\\['):
            opening = text[index:index + 2]
            closing = '\\)' if opening == '\\(' else '\\]'
        else:
            index += 1
            continue
        cursor = index + len(opening)
        end = None
        while cursor < len(text) and not mask[cursor]:
            remaining_work -= 1
            if remaining_work < 0:
                raise AuthoringOutputError('Ambiguous math delimiters exceed the formatting work budget.')
            if not escapes[cursor] and text.startswith(closing, cursor):
                if dollar:
                    after = cursor + len(closing)
                    if after < len(text) and text[after] == '$':
                        break
                end = cursor + len(closing)
                break
            # A mismatched dollar run is not a complete math delimiter pair.
            if dollar and text[cursor] == '$' and not escapes[cursor]:
                break
            cursor += 1
        if end is None:
            index += len(opening)
            continue
        yield index, end
        index = end


def normalize_markdown(text):
    """Normalize CRLF; restore only recognized CR-decoded TeX in math.

    This is formatting, not mathematical validation or a source-value repair.
    All ordinary text, numbers, native captions, and valid TeX stay unchanged.
    """
    if type(text) is not str:
        raise AuthoringOutputError('A Markdown string is required for math formatting.')
    value = text.replace('\r\n', '\n')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise AuthoringOutputError('Unsupported control character in Wiki Markdown.')
    if '\r' not in value:
        return value

    parts, previous = [], 0
    for start, end in _math_segments(value, _code_mask(value)):
        parts.append(value[previous:start])
        parts.append(_R_COMMAND.sub(lambda _: '\\r', value[start:end]))
        previous = end
    parts.append(value[previous:])
    value = ''.join(parts)
    if '\r' in value or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise AuthoringOutputError('Unsupported control character in Wiki Markdown.')
    return value
