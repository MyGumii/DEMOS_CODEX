"""Preserve existing synced endpoints without extending a scoped DB grant."""
from .database_access import _template_body_has_unscoped_reference


def retained_body(body, current_body):
    """Return (allowed, needs_parent_read) for an exact protected-tree retention.

    Every synced subtree must keep its full canonical value and its position.
    New, removed, edited or moved endpoints are rejected. Ordinary fields still
    pass the existing unscoped page-reference guard. Callers validate parent
    read authority and template/document fences inside the same transaction.
    """
    def mask(value, path, protected):
        if isinstance(value, dict):
            if value.get('type') == 'synced_block':
                protected[path] = value
                return {'type': 'paragraph', 'content': '', 'props': {}}
            return {key: mask(item, path + (key,), protected) for key, item in value.items()}
        if isinstance(value, list):
            return [mask(item, path + (index,), protected) for index, item in enumerate(value)]
        return value

    proposed, canonical = {}, {}
    plain_body = mask(body, (), proposed)
    plain_current = mask(current_body, (), canonical)
    allowed = (proposed == canonical
        and not _template_body_has_unscoped_reference(plain_body)
        and not _template_body_has_unscoped_reference(plain_current))
    return allowed, bool(canonical)
