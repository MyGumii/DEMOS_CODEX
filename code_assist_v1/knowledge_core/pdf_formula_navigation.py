"""Source-owned numbered-formula hints and honest omission diagnostics.

Label mentions alone are NOT proof that a formula or its meaning survived.
This inventory neither fabricates formulas nor chooses a subset of source text.
"""
import re


_LABEL = r"(?:[A-Z]|\d+)(?:\.\d+)+"
_SOURCE_TAG = re.compile(r"\(("+_LABEL+r")\)\.?[ \t]*(?:\n|$)")
_OPERATOR = re.compile(r"[=<>≤≥≈≠∈⊂⊆]|\\(?:leq?|geq?|neq|approx|in|subseteq?)\b")
_MENTION = re.compile(r"(?<![\w.])("+_LABEL+r")(?![\w.])")
_FIRST_WORD = re.compile(r"^[ \t]*([A-Za-z]{3,})\b")
_MATH_NAMES = {"min","max","sup","inf","argmin","argmax","range","log","exp","sin","cos","tan","lim"}


def source_formula_positions(source_sections):
    labels, seen = [], set()
    for index,section in enumerate(source_sections):
        text = section["text"]
        for match in _SOURCE_TAG.finditer(text):
            start = text.rfind("\n",0,match.start())+1
            # A trailing reference in prose is not automatically an equation.
            # Only its actual physical source line with an operator is indexed.
            line = text[start:match.start()]
            label = match[1]
            first = _FIRST_WORD.match(line)
            # A sentence ending with a cited equation is not that equation's
            # definition, even if its prose contains '='. Keep math functions;
            # do not reconstruct a cross-reference from a source label hint.
            remainder=line[first.end():].lstrip() if first else ""
            mathematical_name=first and (first[1].lower() in _MATH_NAMES
                or remainder[:1] in "(=<>≤≥≈≠∈⊂⊆")
            prose_start = first and not mathematical_name
            if not _OPERATOR.search(line) or label in seen or prose_start:
                continue
            labels.append({"source_section_index":index,"page":section["page"],
                "offset":match.start(),"label":label})
            seen.add(label)
    return labels


def source_formula_labels(source_sections):
    return [{"page":row['page'],"label":row['label']}
        for row in source_formula_positions(source_sections)]


def label_diagnostics(source_sections, authored):
    inventory = source_formula_labels(source_sections)
    mentioned = set(_MENTION.findall(authored))
    return {"inventory":inventory,
        "missing_label_mentions":[row for row in inventory if row["label"] not in mentioned],
        "formula_content_verified":False,"semantic_preservation_proven":False}
