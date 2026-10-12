"""Select one original's body hierarchy without copying unrelated page blocks."""
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite


def number(value, maximum=32):
    try:
        value = float(value)
        return max(0, min(maximum, value)) if isfinite(value) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


@dataclass(frozen=True)
class ContentHierarchy:
    indexed: dict
    order: tuple
    parents: dict
    children: dict

    def ancestors(self, identity):
        """Return the outer-to-inner owners, including flat tab controllers."""
        path, seen = [], {identity}
        if identity not in self.indexed:
            raise ValueError('unresolved synced child block')
        parent = self.parents[identity]
        while parent is not None:
            if parent in seen or len(path) >= 64:
                raise ValueError('cyclic or deep synced child hierarchy')
            seen.add(parent); path.append(parent)
            parent = self.parents[parent]
        return tuple(reversed(path))

    def tree(self, root_id):
        visiting = set()

        def build(identity, depth=0):
            if depth > 64 or identity in visiting or identity not in self.indexed:
                raise ValueError('cyclic or deep synced child hierarchy')
            visiting.add(identity)
            block = deepcopy(self.indexed[identity]); block['props'] = dict(block.get('props') or {})
            block['props'].pop('children', None)
            block['children'] = [build(child, depth + 1) for child in self.children[identity]]
            visiting.remove(identity)
            return block

        return build(root_id)


def content_hierarchy(blocks):
    """Index the existing inline/identifier/indent/tab formats without mutation.

    This is a preparation-time view, not a new persisted block representation.
    Callers must not modify its borrowed block dictionaries.
    """
    indexed, order, parents, explicit = {}, [], {}, {}

    def region(items, owner=None, depth=0):
        if depth > 64 or not isinstance(items, list):
            raise ValueError('invalid synced child hierarchy')
        scopes = {}
        for block in items:
            if not isinstance(block, dict) or not isinstance(block.get('id'), str):
                raise ValueError('invalid synced child block')
            identity = block['id']
            if identity in indexed:
                raise ValueError('duplicate synced child block')
            indexed[identity] = block; order.append(identity)
            p = block.get('props') or {}
            if not isinstance(p, dict):
                raise ValueError('invalid synced child properties')
            scope = (str(p.get('layout_group') or ''), number(p.get('layout_column'), 4) if p.get('layout_group') else 0,
                     str(p.get('tab_group') or ''), str(p.get('tab_id') or ''))
            stack = scopes.setdefault(scope, [])
            indent = number(p.get('indent'))
            while stack and stack[-1][1] >= indent:
                stack.pop()
            parents[identity] = stack[-1][0] if stack else owner
            stack.append((identity, indent))
            children = p.get('children') or []
            if not isinstance(children, list):
                raise ValueError('invalid synced children')
            if children and all(isinstance(child, dict) for child in children):
                explicit[identity] = [child.get('id') for child in children]
                region(children, identity, depth + 1)
            elif all(isinstance(child, str) for child in children):
                explicit[identity] = children
            else:
                raise ValueError('mixed synced child representation')

    region(blocks)
    # A tab's flat members use a separate indentation scope. Attach only its
    # top-level members; their own descendants retain the indentation parent.
    for identity in order:
        p = indexed[identity].get('props') or {}
        tab = indexed.get(p.get('tab_group'))
        if tab and tab.get('type') == 'tabs':
            parent = indexed.get(parents[identity])
            if not parent or (parent.get('props') or {}).get('tab_group') != tab['id']:
                parents[identity] = tab['id']
    owned = {}
    for parent, children in explicit.items():
        for child in children:
            if child not in indexed or child in owned:
                raise ValueError('unresolved or shared synced child')
            owned[child] = parent; parents[child] = parent
    children = {identity: [] for identity in order}
    for identity in order:
        parent = parents[identity]
        if parent is not None:
            children[parent].append(identity)
    for identity, preferred in explicit.items():
        children[identity] = preferred + [child for child in children[identity] if child not in preferred]
    return ContentHierarchy(indexed, tuple(order), parents, children)


def content_tree(blocks, root_id):
    return content_hierarchy(blocks).tree(root_id)
