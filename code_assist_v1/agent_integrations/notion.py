"""Append-only task snapshots through the active Notion authority.

No legacy fallback when collaborative Notion is active. Recording a later
evidence revision creates a new snapshot; it never overwrites a user's edits.
"""
import os
from contextlib import nullcontext
from pathlib import Path

from .bridge import stable_id
from .contracts import IntegrationError, encoded, digest


def snapshot_blocks(run):
    sections = [("heading1", run["submission"]["title"]),
                ("paragraph", "에이전트 작업 기록 · 지식으로 검증·등록되었다는 의미가 아닙니다."),
                ("paragraph", f"작업 {run['id']} / 버전 {run['row_version']} / 상태 {run['state']}"),
                ("heading2", "에이전트 제안"), ("paragraph", run["submission"]["result"] or "제안 없음")]
    for action in run["submission"]["actions"]:
        sections += [("heading2", "제안 액션: "+action["description"]),
                     ("paragraph", "완료 기준: "+action["completion_condition"]),
                     ("paragraph", "실행 여부: 관측 증거 없이는 미확인")]
    for evidence in run["evidence"]:
        sections += [("paragraph", f"{evidence['kind']} · {evidence['action_id']}: {evidence['body']}")]
    for source in run["submission"]["sources"]:
        sections += [("heading2", "제출 원본: "+source["title"]),
                     ("paragraph", source["text"]),
                     ("paragraph", "출처: "+(source.get("uri") or source.get("external_id") or source["id"]))]
    if run["draft"]:
        sections += [("heading2", "Second Brain 지식 후보"), ("paragraph", encoded(run["draft"]))]
    return [dict(id=stable_id(f"{run['id']}:{run['row_version']}:block:{i}"), type=kind, content=content, props={})
            for i, (kind, content) in enumerate(sections)]


class CollaborativeNotionConnector:
    def __init__(self, store_opener=None):
        from ..notion_collaboration.iam_runtime import open_runtime_store
        self.open_store = store_opener or (lambda actor: open_runtime_store([actor["id"]]))

    def targets(self, actor):
        from ..notion_collaboration.canonical_authority import WORKSPACE_EDIT_ROLES
        store = self.open_store(actor)
        return {"items": [dict(id="workspace:"+w["id"], title=w["name"])
                          for w in store.list_workspaces(actor["id"])
                          if w.get("access", {}).get("role") in WORKSPACE_EDIT_ROLES]}

    def record(self, actor, target, run, revalidate):
        from ..notion_collaboration.canonical_authority import NotFoundOrDenied
        if not target.startswith("workspace:"):
            raise IntegrationError("기록할 Notion Workspace를 선택해 주세요.")
        store, subject, workspace = self.open_store(actor), actor["id"], target[10:]
        # Target authorization and current workspace epoch are rechecked by the
        # canonical Notion creation transaction, including idempotent replays.
        ws = store.get_workspace(subject, workspace)
        identity = stable_id(f"notion:{run['id']}:{run['row_version']}")
        marker = dict(run_id=run["id"],snapshot_version=run["row_version"],snapshot_sha256=digest(snapshot_blocks(run)))
        try:
            existing = store.iam.read_document(subject, identity)
        except NotFoundOrDenied:
            existing = None
        if existing is not None:
            if existing.get("settings",{}).get("agent_integration") != marker:
                raise IntegrationError("Notion 기록 식별자가 다른 내용과 충돌합니다.",409,"IDEMPOTENCY_CONFLICT")
            revalidate()
            return dict(document_id=identity, workspace_id=workspace, snapshot_version=run["row_version"], deduplicated=True)
        revalidate()
        result = store.iam.create_document(subject, workspace, identity,
            title=run["submission"]["title"]+f" · 기록 v{run['row_version']}",
            settings={"agent_integration":marker},
            blocks=snapshot_blocks(run), expected_workspace_epoch=int(ws["security_epoch"]), operation_id=identity)
        return dict(document_id=result["document_id"], workspace_id=workspace, snapshot_version=run["row_version"])


class LegacyNotionConnector:
    def __init__(self, user_data_root=None):
        self.user_data_root = None
        if user_data_root:
            root = Path(user_data_root)
            if not root.is_absolute():
                raise ValueError("Notion user data root must be absolute")
            root = root.resolve(strict=True)
            if not root.is_dir() or root.parent == root:
                raise ValueError("Notion user data root must be an existing directory")
            self.user_data_root = root

    def binding(self):
        from .. import notion_store as ns
        return ns.bind_user_data_root(self.user_data_root) if self.user_data_root else nullcontext()

    def targets(self, actor):
        from .. import notion_store as ns
        with self.binding():
            pages = [p for p in ns.list_pages(actor["id"])
                     if p.get("owner_user_id") == actor["id"] and not p.get("archived")]
        index = {p["id"]: p for p in pages}
        items = []
        for page in pages:
            chain, seen, current = [], set(), page
            while current is not None and current["id"] not in seen:
                seen.add(current["id"])
                chain.append(current["title"] or "제목 없음")
                current = index.get(current.get("parent_id"))
            if current is not None:  # Corrupt cycles are not selectable targets.
                continue
            items.append(dict(id=page["id"], title=page["title"], parent_id=page.get("parent_id"),
                path=" / ".join(reversed(chain)), depth=len(chain)-1))
        return {"items": sorted(items, key=lambda p: (p["path"].casefold(), p["id"]))}

    def record(self, actor, target, run, revalidate):
        with self.binding():
            return self._record(actor, target, run, revalidate)

    def _record(self, actor, target, run, revalidate):
        from .. import notion_store as ns
        from ..notion_collaboration import iam_runtime
        if iam_runtime.runtime_requested():
            raise IntegrationError("협업 Notion에는 기존 저장소로 기록할 수 없습니다.", 503, "NOTION_MODE_MISMATCH")
        operation = stable_id(f"notion:{run['id']}:{run['row_version']}")
        revalidate()
        page = ns.create_page_idempotent(actor["id"], namespace="agent-integrations-v1",
            operation_id=operation, title=run["submission"]["title"]+f" · 기록 v{run['row_version']}", parent_id=target)
        pid = page.get("page", page)["id"]
        revalidate()
        saved = ns.save_page_atomic(actor["id"], pid, base_revision=1,
            operation_id=stable_id(operation+":content"), page=None, blocks=snapshot_blocks(run))
        return dict(document_id=pid, snapshot_version=run["row_version"], revision=saved.get("revision"))


def configured_connector():
    from ..notion_collaboration import iam_runtime
    mode = os.environ.get("CODE_ASSIST_AGENT_NOTION_MODE", "collaborative" if iam_runtime.runtime_requested() else "disabled")
    if mode == "disabled":
        return None
    if mode == "collaborative":
        iam_runtime.configured_paths()  # Fail at composition on a wrong binding.
        return CollaborativeNotionConnector()
    if mode == "legacy" and not iam_runtime.runtime_requested():
        return LegacyNotionConnector(os.environ.get("CODE_ASSIST_AGENT_NOTION_USER_DATA_ROOT"))
    raise ValueError("Invalid CODE_ASSIST_AGENT_NOTION_MODE or conflicting Notion authority")
