"""Narrow source-order contract for explicitly numbered topic headings.

No formulas/answers are supplied to the model, and no provider text is moved or
repaired. This detects only numbered relations visibly moved after a following
numbered source heading. Unnumbered/renamed topics and semantic equivalence are
not proved by these diagnostics.
"""
import re

from .pdf_formula_navigation import source_formula_positions

_HEADING_NUMBER=re.compile(r'^(\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)(?:[.)])?\s+')


def topic_order_diagnostics(scope,authored):
    formulas=source_formula_positions(scope.get('source_sections',[]))
    violations=[]
    missing=[]
    for heading in scope.get('source_heading_positions',[]):
        number=_HEADING_NUMBER.match(heading['heading'])
        if not number:
            continue
        number=number[1]
        visible=re.search(r'^\s*#{2,6}\s+'+re.escape(number)+r'(?=\s|[.)]\s)',authored,re.M)
        if not visible:
            missing.append(number)
            continue
        for formula in formulas:
            if (formula['page']!=heading['page'] or
                    (formula['source_section_index'],formula['offset'])>=
                    (heading['source_section_index'],heading['offset'])):
                continue
            mention=re.search(r'(?<![\w.])'+re.escape(formula['label'])+r'(?![\w.])',authored)
            if mention and mention.start()>visible.start():
                violations.append({'source_page':heading['page'],'heading_number':number,
                    'leading_relation_label':formula['label']})
    return {'leading_relations_after_following_heading':violations,
        'unmatched_heading_numbers':missing,'topic_assignment_proven':False,
        'semantic_preservation_proven':False}
