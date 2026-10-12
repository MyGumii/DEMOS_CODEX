"""Workspace icons use the workspace catalog's existing mutation contract."""
from __future__ import annotations

from code_assist_v1 import notion_icons
from code_assist_v1 import notion_workspace_store as nws


CONTRACT = "code_assist.notion.workspace_appearance.v1"


def update_workspace_icon(store, actor_subject_id, workspace_id, *, icon, base_version, operation_id):
    actor = nws._subject(actor_subject_id, field="actor_subject_id")
    workspace = nws._identifier(workspace_id, prefix="ws", field="workspace_id")
    version = nws._expected_version(base_version)
    if not isinstance(icon, str) or len(icon) > 64:
        raise ValueError("icon must be a string of at most 64 characters")
    value = icon.strip()
    if value.startswith(("nf:", "nw:")):
        raise ValueError("workspace image references must be assigned by the workspace upload service")
    if value.startswith("ni:"):
        catalog = notion_icons._catalog()
        allowed = {f"ni:{item['id']}:{color['id']}" for item in catalog["vectors"] for color in catalog["colors"]}
        if value not in allowed:
            raise ValueError("unknown icon or color")
    request = {"workspace_id": workspace, "icon": value, "base_version": version}

    def handler(con):
        membership = store._require_workspace_admin(con, workspace, actor)
        store._assert_version(con, workspace, version)
        con.execute("UPDATE workspaces SET icon=? WHERE id=?", (value, workspace))
        row = store._bump_workspace(con, workspace, security_change=False)
        result = store._workspace_public(row)
        result["access"] = {"role": str(membership["role"]), "epoch": int(membership["membership_epoch"])}
        return nws._MutationEffect(
            result={"contract": CONTRACT, "workspace": result},
            workspace_id=workspace, target_type="workspace", target_id=workspace,
            event_payload={"workspace_id": workspace, "icon": value},
            authorization=store._authorization("workspace.admin", workspace),
        )

    def authorize_replay(con, _previous):
        store._require_workspace_admin(con, workspace, actor)

    return store._mutate(
        actor_subject_id=actor, operation_id=operation_id, action="workspace.appearance.updated",
        request=request, handler=handler, replay_authorizer=authorize_replay,
    )
