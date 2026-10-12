"""Notion consumer tests: current authority, one view, closure and source fences."""
from types import SimpleNamespace

import pytest

from code_assist_v1 import auth, knowledge_core, notion_ai_chat_knowledge as module
from code_assist_v1.agent_integrations.contracts import IntegrationError
from code_assist_v1.notion_ai_notes import NotesUnavailable
from code_assist_v1.notion_notes_knowledge import KnowledgeReader


class ReadBridge:
    def __init__(self):
        self.calls = []
        self.revoked = False
        self.closure = "closure-v1"
        self.revision = "revision-v1"
        self.source = "part"
        self.items = [{"item_id": "doc", "revision_id": self.revision,
                       "source_space_id": self.source, "title": "합성 자료", "excerpt": "금요일 검토"}]

    def actor(self, user):
        if user != "owner" or self.revoked:
            raise IntegrationError("denied", 403)
        return {"id": user}

    def view(self, actor, space):
        self.actor(actor["id"])
        if space not in {"personal", "part", "team", "responsibility", "foreign-personal"}:
            raise IntegrationError("not found", 404)
        return {"space_id": space, "kind": "personal" if space == "foreign-personal" else space,
                "closure_sha256": self.closure}

    def authorize(self, actor, space):
        self.calls.append(("authorize", space))
        if space != "personal":
            raise IntegrationError("not own Wiki", 404)

    def wikis(self, actor, cursor):
        self.calls.append(("wikis", cursor))
        return {"items": [{"space_id": "personal", "display_name": "내 Wiki", "is_default": True}],
                "next_cursor": "next" if not cursor else None}

    def organization_views(self, actor):
        self.calls.append(("organization_views",))
        return [{"space_id": kind, "kind": kind, "display_name": kind,
                 "org_path": ["담당", "팀", "파트"], "can_publish": False}
                for kind in ["part", "team", "responsibility"]]

    def search_view(self, actor, space, query):
        self.calls.append(("search_view", space, query))
        return {"space_id": space, "items": self.items, "truncated": False,
                "corpus_closure_sha256": self.closure}

    def get_view(self, actor, space, item):
        self.calls.append(("get_view", space, item))
        return {"revision_id": self.revision, "source_space_id": self.source}


@pytest.fixture
def reader(monkeypatch):
    flags = SimpleNamespace(part_reads=True, higher_spaces=True)
    monkeypatch.setattr(module, "get_knowledge_core", lambda app: SimpleNamespace(flags=flags))
    bridge = ReadBridge()
    reader = module.ChatKnowledgeReader(None)
    monkeypatch.setattr(reader, "_bridge", lambda: bridge)
    return reader, bridge, flags


@pytest.mark.parametrize("part,higher,kinds", [(False, False, ["personal"]),
    (True, False, ["personal", "part"]), (True, True, ["personal", "part", "team", "responsibility"])])
def test_current_rollout_only_selectable_views_and_personal_pagination(reader, part, higher, kinds):
    adapter, bridge, flags = reader
    flags.part_reads, flags.higher_spaces = part, higher
    result = adapter.scopes("owner")
    assert [item["kind"] for item in result["items"]] == kinds
    if not part:
        assert ("organization_views",) not in bridge.calls
    bridge.calls.clear()
    assert adapter.scopes("owner", "next")["next_cursor"] is None
    assert bridge.calls == [("wikis", "next")]
    for denied in set(["personal", "part", "team", "responsibility"]) - set(kinds):
        with pytest.raises(PermissionError):
            adapter.authorize("owner", denied)


@pytest.mark.parametrize("space", ["part", "team", "responsibility"])
def test_one_selected_view_preserves_physical_source_and_no_writes(reader, space):
    adapter, bridge, flags = reader
    result = adapter.retrieve("owner", space, "금요일")
    assert result["mode"] == "canonical-" + space
    assert result["corpus_closure_sha256"] == "closure-v1"
    assert result["items"][0]["space_id"] == "part"
    assert [call for call in bridge.calls if call[0] == "search_view"] == [("search_view", space, "금요일")]
    assert ("get_view", space, "doc") in bridge.calls
    assert all(call[0] in {"get_view", "search_view"} for call in bridge.calls)


def test_no_admin_or_foreign_personal_or_unrelated_part_expansion(reader):
    adapter, bridge, flags = reader
    adapter.authorize("owner", "personal")
    assert ("authorize", "personal") in bridge.calls
    for space in ["foreign-personal", "other-part", "all", "../../knowledge"]:
        with pytest.raises((PermissionError, ValueError)):
            adapter.retrieve("owner", space, "query")
    assert not any(call[0] == "search_view" for call in bridge.calls)


@pytest.mark.parametrize("change", ["revoked", "revision", "closure", "source"])
def test_snapshot_revalidates_account_closure_revision_and_real_source(reader, change):
    adapter, bridge, flags = reader
    snapshot = adapter.retrieve("owner", "team", "금요일")
    setattr(bridge, change, True if change == "revoked" else "changed")
    with pytest.raises(PermissionError):
        adapter.revalidate("owner", "team", snapshot)


def test_retrieval_midflight_closure_race_fails_closed(reader, monkeypatch):
    adapter, bridge, flags = reader
    search = bridge.search_view
    def change(*args):
        result = search(*args)
        bridge.closure = "changed"
        return result
    monkeypatch.setattr(bridge, "search_view", change)
    with pytest.raises(PermissionError):
        adapter.retrieve("owner", "team", "금요일")


def test_no_document_count_cap_and_exact_utf8_budget(reader):
    adapter, bridge, flags = reader
    bridge.items = [{**bridge.items[0], "item_id": f"doc{n}"} for n in range(35)]
    assert len(adapter.retrieve("owner", "team", "금요일")["items"]) == 35
    for item in bridge.items:
        item["excerpt"] = "한글" * 20000
    result = adapter.retrieve("owner", "team", "금요일")
    assert result["truncated"] and result["excerpt_bytes"] <= 98304
    assert result["excerpt_bytes"] == sum(len(item["excerpt"].encode()) for item in result["items"])


def test_legacy_only_and_canonical_disabled_never_falls_back(monkeypatch):
    monkeypatch.setattr(auth, "_load_users", lambda: {"users": {"owner": {"id": "owner"}}})
    adapter = module.ChatKnowledgeReader(None)
    monkeypatch.setattr(knowledge_core, "get_knowledge_core", lambda app: SimpleNamespace(flags=SimpleNamespace(core_enabled=False)))
    result = adapter.scopes("owner")
    assert result["mode"] == "legacy-personal" and len(result["items"]) == 1
    with pytest.raises(PermissionError):
        adapter.authorize("owner", "team")
    monkeypatch.setattr(knowledge_core, "get_knowledge_core", lambda app: SimpleNamespace(flags=SimpleNamespace(core_enabled=True, personal_reads=False)))
    with pytest.raises(NotesUnavailable):
        adapter.scopes("owner")


def test_ai_notes_comparison_remains_personal_only(reader, monkeypatch):
    _adapter, bridge, flags = reader
    notes_reader = KnowledgeReader(None)
    monkeypatch.setattr(notes_reader, "_bridge", lambda: bridge)
    with pytest.raises(IntegrationError):
        notes_reader.authorize("owner", "part")


def test_real_canonical_bridge_selects_authorized_hierarchy_and_keeps_source(isolated_knowledge_layout, monkeypatch):
    """Existing canonical fixture/services, not live 10421 data or mocked scopes."""
    from dataclasses import replace
    from code_assist_v1.agent_integrations.bridge import KnowledgeBridge
    from code_assist_v1.knowledge_core.scope import ScopeResolver
    from code_assist_v1.knowledge_core.services.retrieval import CanonicalRetrievalService
    from code_assist_v1.knowledge_core.services.management_reads import CanonicalManagementReadService
    from code_assist_v1.knowledge_core.services.personal_wikis import PersonalWikiLifecycleService
    from code_assist_v1.tests.knowledge_spaces.unit.test_aggregate_graph_service import _factory, _seed, _flags

    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    flags = replace(_flags(), multi_wiki_reads=True)
    scopes = ScopeResolver(flags)
    def no_staging(*args, **kwargs):
        raise AssertionError("AI chat must never stage or publish Wiki content")
    services = SimpleNamespace(
        source_artifacts=SimpleNamespace(_factory=factory, _scopes=scopes,
            _stager=SimpleNamespace(stage_and_verify=no_staging)),
        personal_wikis=PersonalWikiLifecycleService(factory, cursor_secret=b"synthetic-only-key" * 3),
        retrieval=CanonicalRetrievalService(factory, scope_resolver=scopes),
        management_reads=CanonicalManagementReadService(factory, scope_resolver=scopes))
    core = SimpleNamespace(flags=flags, services=services, canonical_route_bindings=None)
    monkeypatch.setattr(module, "get_knowledge_core", lambda app: core)
    monkeypatch.setattr(knowledge_core, "get_knowledge_core", lambda app: core)
    reader = module.ChatKnowledgeReader(None)
    result = reader.scopes("partmember")
    kinds = {item["kind"] for item in result["items"]}
    assert kinds == {"personal", "part", "team", "responsibility"}
    assert ids["part_space_b"] not in {item["space_id"] for item in result["items"]}
    team = next(item for item in result["items"] if item["kind"] == "team")
    assert team["space_id"] == ids["team_space_a"] and "Team A" in team["org_path"]
    found = reader.retrieve("partmember", team["space_id"], "body")
    assert found["mode"] == "canonical-team" and found["corpus_closure_sha256"]
    assert found["items"] and {item["space_id"] for item in found["items"]} <= {ids["part_space_a"], ids["part_space_a2"]}
    bridge = KnowledgeBridge(services, None)
    item = bridge.get_view(bridge.actor("partmember"), team["space_id"], ids["item_a1"])
    assert item["source_space_id"] == ids["part_space_a"]
    snapshot = {"corpus_closure_sha256": found["corpus_closure_sha256"], "items": [
        {"id": ids["item_a1"], "revision_id": item["revision_id"], "space_id": item["source_space_id"]}]}
    reader.revalidate("partmember", team["space_id"], snapshot)
    for denied in [ids["part_space_b"], ids["team_space_b"]]:
        with pytest.raises(PermissionError):
            reader.authorize("partmember", denied)
