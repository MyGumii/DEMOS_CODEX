"""Item icon, cover and cover crop share their source document's authority."""
import json
import math
from code_assist_v1 import notion_workspace_row_appearance_migration as migration
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file_migration
from code_assist_v1 import notion_workspace_database_activity_migration as activity_migration
from code_assist_v1 import notion_workspace_database_trash_migration as trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from .canonical_authority import AuthorityUnavailable, NotFoundOrDenied, _canonical_json

DEFAULT = {'icon':'','cover':'','cover_position':0.5}

def installed(con):
    marker=con.execute('SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1').fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION,item.TARGET_NAME,item.TARGET_CHECKSUM,item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration,same_file_migration,activity_migration,trash_migration,row_trash_migration,placement_migration,custom_emoji_migration,item_layout_migration,template_default_migration,database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }

def clean_updates(value):
    from .page_appearance import _updates
    if not isinstance(value,dict) or not value or not set(value)<=set(DEFAULT):
        raise ValueError('item appearance requires icon, cover or cover_position')
    clean=_updates({k:v for k,v in value.items() if k!='cover_position'}) if set(value)-{'cover_position'} else {}
    if 'cover_position' in value:
        position=value['cover_position']
        if isinstance(position,bool) or not isinstance(position,(int,float)) or not math.isfinite(position) or not 0<=position<=1:
            raise ValueError('cover position must be a finite number between zero and one')
        clean['cover_position']=float(position)
    return clean

def prepare(authority,actor,document,payload):
    # Blob bytes and catalog validation happen before the transaction.
    from .page_appearance import _image_descriptor
    clean=clean_updates(payload)
    return _image_descriptor(authority,actor,document,clean.get('icon',''))

def snapshot_appearance(con,document_id):
    if not installed(con):return {}
    return {str(row['row_id']):json.loads(row['appearance_json']) for row in con.execute(
        'SELECT a.row_id,a.appearance_json FROM workspace_collaboration_database_row_appearance a '
        'JOIN workspace_collaboration_database_rows r ON r.id=a.row_id '
        'JOIN workspace_collaboration_databases d ON d.id=r.database_id WHERE d.document_id=?',(document_id,))}

def set_appearance(con,row_id,payload,*,document_id,expected_version,now,image=None):
    if not installed(con):raise AuthorityUnavailable('database item appearance is not installed')
    updates=clean_updates(payload)
    if updates.get('icon','').startswith('nf:'):
        if image is None:raise NotFoundOrDenied('not found or permission denied')
        file=con.execute("SELECT * FROM workspace_collaboration_files WHERE id=? AND document_id=? AND state='active'",(image['file_id'],document_id)).fetchone()
        if file is None or updates['icon']!='nf:'+image['file_id'] or any(file[k]!=image[k] for k in ('content_sha256','size_bytes','mime_type')):
            raise NotFoundOrDenied('not found or permission denied')
    if updates.get('icon','').startswith('ne:'):
        marker = con.execute('SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1').fetchone()
        if marker is None or tuple(marker) not in {
                (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM,
                 item.TARGET_ACTUAL_SCHEMA_SHA256)
                for item in (custom_emoji_migration, item_layout_migration, template_default_migration)}:
            raise NotFoundOrDenied('not found or permission denied')
        emoji = con.execute('SELECT 1 FROM workspace_custom_emojis WHERE id=? AND workspace_id='
            '(SELECT workspace_id FROM workspace_collaboration_documents WHERE id=?)',
            (updates['icon'][3:], document_id)).fetchone()
        if emoji is None:
            raise NotFoundOrDenied('not found or permission denied')
    current=con.execute('SELECT appearance_json FROM workspace_collaboration_database_row_appearance WHERE row_id=?',(row_id,)).fetchone()
    appearance={**DEFAULT,**(json.loads(current[0]) if current else {})}
    if 'cover' in updates and updates['cover']!=appearance['cover']:
        appearance['cover_position']=0.5
    appearance.update(updates)
    con.execute('INSERT INTO workspace_collaboration_database_row_appearance(row_id,appearance_json) VALUES(?,?) '
                'ON CONFLICT(row_id) DO UPDATE SET appearance_json=excluded.appearance_json',(row_id,_canonical_json(appearance)))
    con.execute('UPDATE workspace_collaboration_database_rows SET row_version=row_version+1,updated_at=? WHERE id=? AND row_version=?',(now,row_id,expected_version))
    return {'target_version':expected_version+1,'appearance':appearance}

def restore_appearance(con,row_id,before,after):
    """Restore with the body's transaction and one shared row-version increment."""
    if not installed(con):return False
    if before==after:return False
    if after is None:
        con.execute('DELETE FROM workspace_collaboration_database_row_appearance WHERE row_id=?',(row_id,))
    else:
        if not isinstance(after,dict) or set(after)!=set(DEFAULT):raise ValueError('invalid item appearance history')
        clean=clean_updates(after)
        con.execute('INSERT INTO workspace_collaboration_database_row_appearance(row_id,appearance_json) VALUES(?,?) '
                    'ON CONFLICT(row_id) DO UPDATE SET appearance_json=excluded.appearance_json',(row_id,_canonical_json(clean)))
    return True
