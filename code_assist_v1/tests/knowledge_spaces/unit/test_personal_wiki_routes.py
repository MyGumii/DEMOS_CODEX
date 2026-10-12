from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid5

from flask import Flask
import pytest

from code_assist_v1.knowledge_core import (
    CanonicalRouteBindings,
    HostKind,
    ServiceRegistry,
    register_knowledge_core,
)
from code_assist_v1.knowledge_core.personal_wiki_routes import (
    PERSONAL_WIKI_ROUTE_PREFIX,
    register_personal_wiki_routes,
)
from code_assist_v1.knowledge_core.repositories.personal_wikis import (
    PersonalWikiProfileRecord,
)
from code_assist_v1.knowledge_core.services.personal_wikis import (
    PersonalWikiConcealedError,
    PersonalWikiPageResult,
)
from code_assist_v1.knowledge_core.services.personal_wiki_search import (
    PersonalWikiManagementSearchItem,
    PersonalWikiManagementSearchResult,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("2638dc6d-3b7b-47a6-91b8-af5916633088")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


class _WikiService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail: Exception | None = None

    def _mutation(self, name: str, command: object):
        self.calls.append((name, command))
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(
            operation_id=getattr(command, "operation_id"),
            resource_id=getattr(command, "space_id", _uuid("created-space")),
            row_version=2,
            status={
                "archive": "archived",
                "create": "created",
                "rename": "renamed",
                "restore": "restored",
                "set_default": "default_set",
            }[name],
        )

    def list_profiles(self, query: object):
        self.calls.append(("list_profiles", query))
        if self.fail is not None:
            raise self.fail
        profile = PersonalWikiProfileRecord(
            space_id=_uuid("profile"),
            owner_user_id="owner",
            display_name="내 지식",
            name_key="내 지식",
            lifecycle_status="active",
            lifecycle_generation=1,
            is_default=True,
            archived_at_ms=None,
            archived_by_user_id=None,
            archive_reason=None,
            created_at_ms=1000,
            updated_at_ms=1000,
            row_version=1,
        )
        return PersonalWikiPageResult(
            request_id=getattr(query, "request_id"),
            items=(profile,),
            next_cursor="signed.cursor",
        )

    def archive(self, command: object):
        return self._mutation("archive", command)

    def create(self, command: object):
        return self._mutation("create", command)

    def rename(self, command: object):
        return self._mutation("rename", command)

    def restore(self, command: object):
        return self._mutation("restore", command)

    def set_default(self, command: object):
        return self._mutation("set_default", command)


def _app(*, writes_enabled: bool = True):
    app = Flask("sbe-2.1-personal-wiki-routes", static_folder=None)
    service = _WikiService()
    register_personal_wiki_routes(
        app,
        service=service,
        principal_resolver=lambda: {"id": "owner", "scope_epoch": 1},
        writes_enabled=writes_enabled,
    )
    return app, service


class _SearchService:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def search(self, request: object):
        self.calls.append(request)
        return PersonalWikiManagementSearchResult(
            request_id=getattr(request, "request_id"),
            normalized_query="scopealpha",
            items=(
                PersonalWikiManagementSearchItem(
                    item_id=_uuid("search-item"),
                    revision_id=_uuid("search-revision"),
                    source_space_id=_uuid("search-space"),
                    wiki_display_name="Project Wiki",
                    title="Scopealpha",
                    excerpt_md="# scopealpha",
                    score_ppm=900_000,
                ),
            ),
            next_cursor="signed.search.cursor",
        )


@pytest.mark.scenario("RANK-002")
def test_sbe_5_1_personal_wiki_management_search_route_is_strict_and_separate() -> None:
    app = Flask("sbe-5.1-personal-wiki-search", static_folder=None)
    lifecycle = _WikiService()
    search = _SearchService()
    register_personal_wiki_routes(
        app,
        service=lifecycle,
        search_service=search,
        principal_resolver=lambda: {"id": "owner", "scope_epoch": 1},
        writes_enabled=False,
    )
    client = app.test_client()
    response = client.post(
        PERSONAL_WIKI_ROUTE_PREFIX + "/search",
        json={"query": "scopealpha", "limit": 10, "cursor": None},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.json["next_cursor"] == "signed.search.cursor"
    assert response.json["items"] == [
        {
            "item_id": _uuid("search-item"),
            "provenance": ["fts"],
            "revision_id": _uuid("search-revision"),
            "score": 0.9,
            "snippet": "# scopealpha",
            "source_space_id": _uuid("search-space"),
            "title": "Scopealpha",
            "wiki_display_name": "Project Wiki",
        }
    ]
    request = search.calls[-1]
    assert request.actor_user_id == "owner"
    assert request.actor_scope_epoch == 1
    assert request.limit == 10
    assert client.post(
        PERSONAL_WIKI_ROUTE_PREFIX + "/search", json={"query": "x", "limit": 11}
    ).status_code == 400
    duplicate = client.post(
        PERSONAL_WIKI_ROUTE_PREFIX + "/search",
        data=b'{"query":"x","query":"y"}',
        content_type="application/json",
    )
    assert duplicate.status_code == 400


@pytest.mark.scenario("WIKI-006")
def test_wiki_006_route_list_is_strict_bounded_and_read_only_flag_separable() -> None:
    app, service = _app(writes_enabled=False)
    client = app.test_client()
    response = client.get(
        PERSONAL_WIKI_ROUTE_PREFIX
        + "?lifecycle_status=archived&query=Wiki&limit=25&cursor=signed.cursor"
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.json["next_cursor"] == "signed.cursor"
    assert response.json["items"][0]["space_id"] == _uuid("profile")
    query = service.calls[-1][1]
    assert query.actor_user_id == "owner"
    assert query.lifecycle_status == "archived"
    assert query.limit == 25
    assert client.post(PERSONAL_WIKI_ROUTE_PREFIX, json={}).status_code == 405

    assert client.get(PERSONAL_WIKI_ROUTE_PREFIX).status_code == 200
    assert service.calls[-1][1].limit == 50

    assert client.get(PERSONAL_WIKI_ROUTE_PREFIX + "?limit=0").status_code == 400
    assert client.get(PERSONAL_WIKI_ROUTE_PREFIX + "?limit=101").status_code == 400
    assert client.get(PERSONAL_WIKI_ROUTE_PREFIX + "?limit=1&limit=2").status_code == 400
    assert client.get(PERSONAL_WIKI_ROUTE_PREFIX + "?unknown=1").status_code == 400


@pytest.mark.scenario("WIKI-005", "WIKI-007", "WIKI-008", "WIKI-009", "WIKI-011")
def test_wiki_mutation_routes_bind_principal_operation_cas_and_exact_paths() -> None:
    app, service = _app(writes_enabled=True)
    client = app.test_client()
    space_id = _uuid("target")
    cases = (
        (
            "post",
            PERSONAL_WIKI_ROUTE_PREFIX,
            {"display_name": "Project", "expected_row_version": 1},
            "create",
            201,
        ),
        (
            "patch",
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/{space_id}",
            {"display_name": "Renamed", "expected_row_version": 2},
            "rename",
            200,
        ),
        (
            "put",
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/{space_id}/default",
            {"expected_row_version": 3},
            "set_default",
            200,
        ),
        (
            "post",
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/{space_id}/archive",
            {"expected_row_version": 4, "reason": "Owner choice"},
            "archive",
            200,
        ),
        (
            "post",
            f"{PERSONAL_WIKI_ROUTE_PREFIX}/{space_id}/restore",
            {"expected_row_version": 5, "make_default": True},
            "restore",
            200,
        ),
    )
    for index, (method, path, body, expected_call, status) in enumerate(cases):
        operation_id = _uuid(f"operation:{index}")
        response = getattr(client, method)(
            path,
            json={**body, "operation_id": operation_id},
            headers={"Idempotency-Key": operation_id},
        )
        assert response.status_code == status, response.json
        assert response.json["operation_id"] == operation_id
        assert response.json["status"]
        name, command = service.calls[-1]
        assert name == expected_call
        assert command.actor_user_id == "owner"
        assert command.actor_scope_epoch == 1
        assert command.operation_id == operation_id
        if expected_call != "create":
            assert command.space_id == space_id

    bad = client.post(
        PERSONAL_WIKI_ROUTE_PREFIX,
        json={
            "display_name": "Bad header",
            "expected_row_version": 1,
            "operation_id": _uuid("body-operation"),
        },
        headers={"Idempotency-Key": _uuid("other-operation")},
    )
    assert bad.status_code == 409
    assert bad.json["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    service.fail = PersonalWikiConcealedError("The requested personal Wiki was not found.")
    concealed = client.patch(
        f"{PERSONAL_WIKI_ROUTE_PREFIX}/{space_id}",
        json={
            "display_name": "Hidden",
            "expected_row_version": 1,
            "operation_id": _uuid("concealed-operation"),
        },
    )
    assert concealed.status_code == 404
    assert concealed.json["error"]["code"] == "NOT_FOUND"


class _AllMethods:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class _Executor:
    def complete(self, _context: object):
        return None


class _Summarizer:
    def summarize(self, **_kwargs: object):
        return None


@pytest.mark.scenario("WIKI-005", "WIKI-006")
def test_sbe_2_1_composition_registers_v2_routes_only_with_exact_flags() -> None:
    app = Flask("sbe-2.1-composition", static_folder=None)
    service = _AllMethods()
    container = register_knowledge_core(
        app,
        HostKind.CODE,
        environ={
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": "1",
            "KNOWLEDGE_CORE_MULTI_WIKI_READS": "1",
            "KNOWLEDGE_CORE_MULTI_WIKI_WRITES": "1",
            "KNOWLEDGE_CORE_PERSONAL_WRITES": "1",
            "KNOWLEDGE_CORE_READS": "1",
        },
        services=ServiceRegistry(
            agent_context=service,
            conversations=service,
            editing=service,
            lifecycle=service,
            management_reads=service,
            personal_wikis=service,
            publication=service,
            retrieval=service,
            scope_selection=service,
        ),
        canonical_route_bindings=CanonicalRouteBindings(
            turn_executor=_Executor(),
            conversation_summarizer=_Summarizer(),
            principal_resolver=lambda: {"id": "owner", "scope_epoch": 1},
        ),
    )
    v2_rules = sorted(
        (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith(PERSONAL_WIKI_ROUTE_PREFIX)
    )
    assert len(v2_rules) == 6
    assert container.registered_route_groups == (
        "canonical_knowledge_v1",
        "personal_wiki_v2_reads",
        "personal_wiki_v2_writes",
        "canonical_publication_v1",
        "canonical_editing_v1",
        "canonical_lifecycle_v1",
    )
