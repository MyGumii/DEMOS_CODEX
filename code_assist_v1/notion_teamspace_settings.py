"""Team-scoped metadata reads and atomic, authorized name changes."""
from __future__ import annotations

import sqlite3

from code_assist_v1 import notion_workspace_store as nws

CONTRACT = "code_assist.notion.teamspace_settings.v1"


def _scope(actor, workspace, teamspace):
    return (nws._subject(actor, field="actor_subject_id"),
            nws._identifier(workspace, prefix="ws", field="workspace_id"),
            nws._identifier(teamspace, prefix="ts", field="teamspace_id"))


def _access(store, con, actor, workspace, teamspace, *, write=False):
    if getattr(store, "iam", None) is not None:
        store.iam._require_active(actor)
    member = store._require_member(con, workspace, actor)
    team = con.execute("SELECT * FROM teamspaces WHERE workspace_id=? AND id=? AND archived_at IS NULL",
                       (workspace, teamspace)).fetchone()
    membership = con.execute("SELECT role FROM teamspace_memberships WHERE workspace_id=? "
                             "AND teamspace_id=? AND subject_id=? AND revoked_at IS NULL",
                             (workspace, teamspace, actor)).fetchone()
    role = str(membership["role"]) if membership else ""
    can_rename = str(member["role"]) in nws.WORKSPACE_ADMIN_ROLES or role == "owner"
    if not team or not (membership or can_rename):
        raise FileNotFoundError("teamspace not found")
    if write and not can_rename:
        raise PermissionError("teamspace owner permission required")
    return team, role, can_rename


def read_teamspace(store, actor_subject_id, workspace_id, teamspace_id):
    actor, workspace, teamspace = _scope(actor_subject_id, workspace_id, teamspace_id)
    con = store._read_connection()
    try:
        con.execute("BEGIN")
        team, role, can_rename = _access(store, con, actor, workspace, teamspace)
        members = con.execute("SELECT t.subject_id,t.role FROM teamspace_memberships t "
                              "JOIN workspace_memberships w ON w.workspace_id=t.workspace_id "
                              "AND w.subject_id=t.subject_id WHERE t.workspace_id=? AND t.teamspace_id=? "
                              "AND t.revoked_at IS NULL AND w.revoked_at IS NULL ORDER BY t.role DESC,t.subject_id",
                              (workspace, teamspace)).fetchall()
        return {"contract": CONTRACT, "teamspace": store._teamspace_public(team, role),
                "workspace_version": int(store._workspace_row(con, workspace)["row_version"]),
                "members": [dict(row) for row in members], "capabilities": {"rename": can_rename}}
    finally:
        con.close()


def rename_teamspace(store, actor_subject_id, workspace_id, teamspace_id, *, name, base_version, operation_id):
    actor, workspace, teamspace = _scope(actor_subject_id, workspace_id, teamspace_id)
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    name = nws._name(name)
    version = nws._expected_version(base_version)
    request = {"workspace_id": workspace, "teamspace_id": teamspace, "name": name, "base_version": version}

    def handler(con):
        _, role, _ = _access(store, con, actor, workspace, teamspace, write=True)
        store._assert_version(con, workspace, version)
        try:
            con.execute("UPDATE teamspaces SET name=?,name_key=?,row_version=row_version+1,updated_at=? "
                        "WHERE workspace_id=? AND id=?", (name, name.casefold(), nws._now(), workspace, teamspace))
        except sqlite3.IntegrityError as exc:
            raise ValueError("teamspace name already exists") from exc
        updated = store._bump_workspace(con, workspace, security_change=False)
        row = con.execute("SELECT * FROM teamspaces WHERE id=?", (teamspace,)).fetchone()
        return nws._MutationEffect(
            result={"contract": CONTRACT, "teamspace": store._teamspace_public(row, role),
                    "workspace": store._workspace_public(updated)},
            workspace_id=workspace, target_type="teamspace", target_id=teamspace,
            event_payload={"teamspace_id": teamspace, "name": name},
            authorization=store._authorization("teamspace.owner", workspace, teamspace_id=teamspace))

    return store._mutate(actor_subject_id=actor, operation_id=operation_id, action="workspace.teamspace_renamed",
                         request=request, handler=handler,
                         replay_authorizer=lambda con, _: _access(store, con, actor, workspace, teamspace, write=True))
