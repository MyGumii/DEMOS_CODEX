"""Database item comment anchors use the existing transactional comment store."""
from .canonical_authority import AuthorityUnavailable, NotFoundOrDenied, _identifier
from .database_row_content import installed


def target(anchor):
    """Return an exact item identity; ordinary document/range anchors stay intact."""
    if anchor.get('kind') != 'database_row':
        return None
    if set(anchor) != {'kind', 'database_id', 'row_id'}:
        raise ValueError('database row comment anchor requires exactly kind, database_id and row_id')
    if any(not isinstance(anchor[key], str) or anchor[key] != _identifier(anchor[key], field=key)
           for key in ('database_id', 'row_id')):
        raise ValueError('database item identifiers must be canonical strings')
    return {
        'kind': 'database_row',
        'database_id': _identifier(anchor['database_id'], field='database_id'),
        'row_id': _identifier(anchor['row_id'], field='row_id'),
    }


def require_binding(con, document_id, anchor, *, block_id=None):
    value = target(anchor)
    if value is None:
        return None
    if block_id is not None:
        raise ValueError('database item comments cannot use a source document block anchor')
    if not installed(con):
        raise AuthorityUnavailable('database item comments are not installed')
    from . import database_row_trash, database_trash
    database_trash.require_active(con, value['database_id'])
    row = con.execute(
        'SELECT r.id FROM workspace_collaboration_database_rows r '
        'JOIN workspace_collaboration_databases d ON d.id=r.database_id '
        'WHERE r.id=? AND r.database_id=? AND d.document_id=?',
        (value['row_id'], value['database_id'], document_id),
    ).fetchone()
    if row is None:
        raise NotFoundOrDenied('not found or permission denied')
    database_row_trash.require_active(con, value['database_id'], value['row_id'])
    return value


def retire_row_comments(con, document_id, database_id, row_id, *, now):
    """A removed row must not revive old threads if its ID is used again."""
    if not installed(con):
        return
    ids = [row[0] for row in con.execute(
        "SELECT id FROM workspace_collaboration_comments WHERE document_id=? AND deleted_at IS NULL "
        "AND json_extract(anchor_json,'$.kind')='database_row' "
        "AND json_extract(anchor_json,'$.database_id')=? AND json_extract(anchor_json,'$.row_id')=?",
        (document_id, database_id, row_id),
    )]
    for comment_id in ids:
        con.execute('UPDATE workspace_collaboration_comments SET deleted_at=?,updated_at=?,row_version=row_version+1 WHERE id=?',
                    (now, now, comment_id))
        con.execute('UPDATE workspace_collaboration_notifications SET redacted_at=?,updated_at=?,notification_epoch=notification_epoch+1 '
                    'WHERE comment_id=? AND redacted_at IS NULL', (now, now, comment_id))
