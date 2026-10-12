"""Native team placement and its default inherited grant share one transaction."""
from code_assist_v1 import notion_workspace_native_team_migration as migration
from code_assist_v1 import notion_workspace_row_body_migration as row_body_migration
from code_assist_v1 import notion_workspace_row_appearance_migration as row_appearance_migration
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
from .canonical_authority import AuthorityUnavailable, NotFoundOrDenied


def installed(con):
    marker = con.execute("SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, row_body_migration, row_appearance_migration, same_file_migration, activity_migration, trash_migration, row_trash_migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def root_record(con, workspace, document):
    if not installed(con):
        return None
    return con.execute("SELECT * FROM workspace_collaboration_native_team_roots WHERE workspace_id=? AND document_id=?",
                       (workspace, document)).fetchone()


def inherited_root(con, workspace, document):
    if not installed(con):
        return None
    rows = con.execute("WITH RECURSIVE lineage(id,parent_document_id,depth) AS ("
                       "SELECT id,parent_document_id,0 FROM workspace_collaboration_documents WHERE workspace_id=? AND id=? "
                       "UNION ALL SELECT d.id,d.parent_document_id,l.depth+1 FROM workspace_collaboration_documents d "
                       "JOIN lineage l ON d.id=l.parent_document_id WHERE d.workspace_id=? AND l.depth<100) "
                       "SELECT * FROM lineage ORDER BY depth", (workspace, document, workspace)).fetchall()
    if len({row["id"] for row in rows}) != len(rows) or (rows and rows[-1]["parent_document_id"]):
        raise AuthorityUnavailable("native team hierarchy is cyclic, broken or too deep")
    if not rows:
        return None
    return root_record(con, workspace, rows[-1]["id"])


def require_creator(authority, con, actor, workspace, teamspace):
    authority._require_active(actor)
    if not installed(con):
        raise AuthorityUnavailable("native team page creation is not installed")
    current = authority._workspace(con, workspace)
    role = authority._workspace_role(con, workspace, actor)
    team = con.execute("SELECT * FROM teamspaces WHERE workspace_id=? AND id=? AND archived_at IS NULL",
                       (workspace, teamspace)).fetchone()
    member = con.execute("SELECT role FROM teamspace_memberships WHERE workspace_id=? AND teamspace_id=? "
                         "AND subject_id=? AND revoked_at IS NULL", (workspace, teamspace, actor)).fetchone()
    if not team or role is None or (role != "owner" and (not member or role == "restricted_member")):
        raise NotFoundOrDenied("not found or permission denied")
    return current


def replace_root(con, workspace, document, teamspace, actor, now):
    """Caller authorizes destination and bumps affected access epochs on moves.

    Only the recorded location's default team grant is revoked. Independent
    subject/group/other-team shares keep their existing semantics.
    """
    if not installed(con):
        if teamspace:
            raise AuthorityUnavailable("native team placement is not installed")
        return
    previous = root_record(con, workspace, document)
    if previous and previous["teamspace_id"] == teamspace:
        return
    if previous:
        con.execute("UPDATE workspace_collaboration_access_grants SET active=0,grant_epoch=grant_epoch+1,"
                    "updated_at=?,revoked_at=? WHERE workspace_id=? AND document_id=? AND principal_type='teamspace' "
                    "AND principal_id=? AND active=1", (now, now, workspace, document, previous["teamspace_id"]))
        con.execute("DELETE FROM workspace_collaboration_native_team_roots WHERE document_id=?", (document,))
    if not teamspace:
        return
    con.execute("INSERT INTO workspace_collaboration_native_team_roots(document_id,workspace_id,teamspace_id,created_by_subject_id,created_at) "
                "VALUES(?,?,?,?,?)", (document, workspace, teamspace, actor, now))
    con.execute("INSERT INTO workspace_collaboration_access_grants(workspace_id,document_id,principal_type,principal_id,role,"
                "inherit_to_descendants,active,grant_epoch,granted_by_subject_id,created_at,updated_at,revoked_at) "
                "VALUES(?,?,'teamspace',?,'full_access',1,1,1,?,?,?,NULL) "
                "ON CONFLICT(document_id,principal_type,principal_id) DO UPDATE SET role='full_access',inherit_to_descendants=1,"
                "active=1,grant_epoch=workspace_collaboration_access_grants.grant_epoch+1,granted_by_subject_id=excluded.granted_by_subject_id,"
                "updated_at=excluded.updated_at,revoked_at=NULL", (workspace, document, teamspace, actor, now, now))
