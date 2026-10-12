from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from flask import Flask

from code_assist_v1.knowledge_core.canonical_routes import (
    register_canonical_routes,
    register_publication_routes,
)
from code_assist_v1.knowledge_core.composition import (
    CanonicalRouteBindings,
    HOST_PROFILES,
    KnowledgeCoreContainer,
    PublicationRouteBindings,
    ServiceRegistry,
    register_knowledge_core,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags, HostKind
from code_assist_v1.knowledge_core.routes import register_routes


def _uuid(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"p4.5-route:{label}"))


class _UnusedDependencies:
    def __getattr__(self, _: str):
        return lambda *args, **kwargs: None


class _TurnExecutor:
    def complete(self, context):
        return context


class _Summarizer:
    def summarize(self, **kwargs):
        return kwargs


class _Publication:
    def __init__(self) -> None:
        self.commands: list[object] = []
        self.preview_id = _uuid("preview")

    def _record(self, command, *, status: str = "ready"):
        self.commands.append(command)
        return SimpleNamespace(
            preview_id=self.preview_id,
            row_version=2,
            status=status,
        )

    def create_manual_preview(self, command):
        return self._record(command)

    def create_chat_preview(self, command):
        return self._record(command)

    def create_notion_preview(self, command):
        return self._record(command)

    def create_upload_preview(self, command):
        record = self._record(command, status="queued")
        return SimpleNamespace(
            preview_id=record.preview_id,
            to_mapping=lambda: {
                "created_at": "2026-08-14T00:00:00.000Z",
                "expires_at": "2026-08-14T01:00:00.000Z",
                "job_id": _uuid("job"),
                "operation_id": command.operation_id,
                "preview_id": record.preview_id,
                "request_id": command.request_id,
                "status": "queued",
                "status_url": f"/api/knowledge/v1/publication-previews/{record.preview_id}",
                "updated_at": "2026-08-14T00:00:00.000Z",
            },
        )

    def get_preview(self, query):
        self.commands.append(query)
        return {
            "available_target_modes": ["personal", "part", "both"],
            "completed_at": "2026-08-14T00:00:00.000Z",
            "created_at": "2026-08-14T00:00:00.000Z",
            "deterministic_findings": [],
            "expires_at": "2026-08-14T01:00:00.000Z",
            "job_id": None,
            "operation_id": _uuid("create"),
            "preview_id": query.preview_id,
            "ready_result_schema_version": 1,
            "request_id": _uuid("stored-request"),
            "size_bytes": 7,
            "source_snapshot_sha256": "a" * 64,
            "status": "ready",
            "title": "Example",
            "updated_at": "2026-08-14T00:00:00.000Z",
            "warnings": [],
        }

    def cancel_preview(self, command):
        return self._record(command, status="cancelled")

    def publish(self, command):
        self.commands.append(command)
        return SimpleNamespace(
            operation_id=command.operation_id,
            request_id=command.request_id,
            targets=(
                {
                    "created_revision_id": _uuid("revision"),
                    "finding_id": None,
                    "item_id": _uuid("item"),
                    "outcome": "created",
                    "resolved_revision_id": _uuid("revision"),
                    "source_artifact_id": _uuid("artifact"),
                    "space_id": _uuid("space"),
                },
            ),
        )


class _Space:
    def as_dict(self) -> dict[str, object]:
        return {
            "capabilities": ["read", "publish"],
            "id": _uuid("personal-space"),
            "kind": "personal",
            "name": "Personal",
            "org_path": [],
            "selected": True,
        }


class _ScopeSelection:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def read(self, request: object) -> object:
        self.requests.append(request)
        return SimpleNamespace(spaces=(_Space(),))


def _app(publication: _Publication | None = None) -> Flask:
    app = Flask(f"p45-publication-{id(publication)}", static_folder=None)
    dependency = _UnusedDependencies()
    register_canonical_routes(
        app,
        scope_selection_service=dependency,
        conversation_service=dependency,
        retrieval_service=dependency,
        management_read_service=dependency,
        agent_context_service=dependency,
        turn_executor=_TurnExecutor(),
        conversation_summarizer=_Summarizer(),
        principal_resolver=lambda: {"id": "publisher01", "scope_epoch": 7},
        publication_service=publication,
    )
    return app


def test_publication_routes_are_optional_and_register_as_one_complete_surface() -> None:
    without = _app()
    with_publication = _app(_Publication())
    assert len(
        [rule for rule in without.url_map.iter_rules() if rule.rule.startswith("/api/knowledge/v1")]
    ) == 21
    publication_rules = {
        (rule.rule, method)
        for rule in with_publication.url_map.iter_rules()
        if "publication" in rule.rule
        for method in rule.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert publication_rules == {
        ("/api/knowledge/v1/publication-previews", "POST"),
        ("/api/knowledge/v1/publication-previews/<preview_id>", "GET"),
        ("/api/knowledge/v1/publication-previews/<preview_id>", "DELETE"),
        ("/api/knowledge/v1/publications", "POST"),
    }


def test_standalone_notion_publication_surface_has_only_target_discovery_and_publication() -> None:
    app = Flask("p45-standalone-notion", static_folder=None)
    publication = _Publication()
    scope = _ScopeSelection()
    registered = register_publication_routes(
        app,
        scope_selection_service=scope,
        publication_service=publication,
        principal_resolver=lambda: {"id": "publisher01", "scope_epoch": 7},
    )
    assert registered == (
        "/api/knowledge/v1/spaces",
        "/api/knowledge/v1/publication-previews",
        "/api/knowledge/v1/publication-previews/<preview_id>",
        "/api/knowledge/v1/publication-previews/<preview_id>",
        "/api/knowledge/v1/publications",
    )
    try:
        register_publication_routes(
            app,
            scope_selection_service=scope,
            publication_service=publication,
            principal_resolver=lambda: {"id": "publisher01", "scope_epoch": 7},
        )
    except RuntimeError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate standalone publication registration was accepted")
    client = app.test_client()
    probe = client.options("/api/knowledge/v1/publication-previews")
    assert probe.status_code == 200
    assert publication.commands == []
    spaces = client.get("/api/knowledge/v1/spaces")
    assert spaces.status_code == 200
    assert spaces.json["items"][0]["name"] == "Personal"
    assert len(scope.requests) == 1
    assert not [
        rule
        for rule in app.url_map.iter_rules()
        if "/conversations" in rule.rule or "/search" in rule.rule
    ]


def test_notion_composition_can_bind_future_publication_without_conversation_dependencies() -> None:
    app = Flask("p45-notion-composition", static_folder=None)
    publication = _Publication()
    scope = _ScopeSelection()
    container = KnowledgeCoreContainer(
        contract_version=1,
        host=HOST_PROFILES[HostKind.NOTION],
        flags=FeatureFlags(core_enabled=True, personal_writes=True),
        services=ServiceRegistry(publication=publication, scope_selection=scope),
        publication_route_bindings=PublicationRouteBindings(
            principal_resolver=lambda: {"id": "publisher01", "scope_epoch": 7}
        ),
    )
    assert register_routes(app, container) == ("canonical_publication_v1",)
    paths = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/knowledge/v1/spaces" in paths
    assert "/api/knowledge/v1/publications" in paths
    assert not any("conversation" in path for path in paths)


def test_composition_keeps_publication_routes_disabled_without_write_flag() -> None:
    app = Flask("p45-disabled-composition", static_folder=None)
    dependency = _UnusedDependencies()
    container = register_knowledge_core(
        app,
        HostKind.CODE,
        environ={"KNOWLEDGE_CORE_ENABLED": "1", "KNOWLEDGE_CORE_READS": "1"},
        services=ServiceRegistry(
            agent_context=dependency,
            conversations=dependency,
            management_reads=dependency,
            publication=_Publication(),
            retrieval=dependency,
            scope_selection=dependency,
        ),
        canonical_route_bindings=CanonicalRouteBindings(
            turn_executor=_TurnExecutor(),
            conversation_summarizer=_Summarizer(),
            principal_resolver=lambda: {"id": "publisher01", "scope_epoch": 7},
        ),
    )
    assert container.registered_route_groups == ("canonical_knowledge_v1",)
    assert not [
        rule for rule in app.url_map.iter_rules() if "publication" in rule.rule
    ]


def test_manual_preview_uses_only_server_principal_and_exact_request_shape() -> None:
    publication = _Publication()
    client = _app(publication).test_client()
    operation_id = _uuid("create")
    response = client.post(
        "/api/knowledge/v1/publication-previews",
        headers={"Idempotency-Key": operation_id},
        json={
            "markdown": "# Example\n",
            "operation_id": operation_id,
            "source_type": "manual",
            "title": "Example",
        },
    )
    assert response.status_code == 200
    assert response.json["preview_id"] == publication.preview_id
    command = publication.commands[0]
    assert command.actor_user_id == "publisher01"
    assert command.actor_scope_epoch == 7
    assert command.markdown == "# Example\n"
    assert command.title == "Example"
    assert "publisher01" not in response.get_data(as_text=True)

    rejected = client.post(
        "/api/knowledge/v1/publication-previews",
        json={
            "actor_user_id": "attacker",
            "markdown": "# Example\n",
            "operation_id": _uuid("override"),
            "source_type": "manual",
        },
    )
    assert rejected.status_code == 400
    assert rejected.json["error"]["code"] == "VALIDATION_ERROR"


def test_notion_chat_status_cancel_and_commit_map_to_exact_public_contracts() -> None:
    publication = _Publication()
    client = _app(publication).test_client()
    notion_operation = _uuid("notion")
    notion = client.post(
        "/api/knowledge/v1/publication-previews",
        headers={"Idempotency-Key": notion_operation},
        json={
            "operation_id": notion_operation,
            "page_id": "page-1",
            "source_type": "notion",
        },
    )
    assert notion.status_code == 200
    assert publication.commands[0].page_id == "page-1"

    chat_operation = _uuid("chat")
    chat = client.post(
        "/api/knowledge/v1/publication-previews",
        headers={"Idempotency-Key": chat_operation},
        json={
            "conversation_id": _uuid("conversation"),
            "message_range": {"from_sequence": 2, "to_sequence": 4},
            "operation_id": chat_operation,
            "source_type": "chat",
        },
    )
    assert chat.status_code == 200
    chat_command = next(value for value in publication.commands if hasattr(value, "first_sequence_no"))
    assert (chat_command.first_sequence_no, chat_command.last_sequence_no) == (2, 4)

    status = client.get(
        f"/api/knowledge/v1/publication-previews/{publication.preview_id}"
    )
    assert status.status_code == 200
    assert status.json["available_target_modes"] == ["personal", "part", "both"]

    cancel_operation = _uuid("cancel")
    cancelled = client.delete(
        f"/api/knowledge/v1/publication-previews/{publication.preview_id}",
        headers={"Idempotency-Key": cancel_operation},
        json={"operation_id": cancel_operation},
    )
    assert cancelled.status_code == 200
    assert set(cancelled.json) == {
        "operation_id", "request_id", "resource_id", "row_version", "status"
    }
    assert cancelled.json["status"] == "cancelled"

    publish_operation = _uuid("publish")
    personal_space_id = _uuid("selected-personal-space")
    committed = client.post(
        "/api/knowledge/v1/publications",
        headers={"Idempotency-Key": publish_operation},
        json={
            "operation_id": publish_operation,
            "personal_space_id": personal_space_id,
            "preview_id": publication.preview_id,
            "source_snapshot_sha256": "a" * 64,
            "target_mode": "both",
        },
    )
    assert committed.status_code == 201
    assert set(committed.json) == {"operation_id", "request_id", "results"}
    assert committed.json["results"][0]["outcome"] == "created"
    publish_command = publication.commands[-1]
    assert publish_command.actor_user_id == "publisher01"
    assert publish_command.personal_space_id == personal_space_id
    assert publish_command.target_mode == "both"

    invalid = client.post(
        "/api/knowledge/v1/publications",
        json={
            "operation_id": _uuid("invalid-part-personal-target"),
            "personal_space_id": personal_space_id,
            "preview_id": publication.preview_id,
            "source_snapshot_sha256": "a" * 64,
            "target_mode": "part",
        },
    )
    assert invalid.status_code == 400
    assert invalid.json["error"]["code"] == "VALIDATION_ERROR"


def test_upload_preview_is_bounded_and_preserves_exact_bytes_and_media_type() -> None:
    publication = _Publication()
    client = _app(publication).test_client()
    operation_id = _uuid("upload")
    response = client.post(
        "/api/knowledge/v1/publication-previews",
        headers={"Idempotency-Key": operation_id},
        data={
            "file": (BytesIO(b"exact upload bytes"), "source.pdf", "application/pdf"),
            "operation_id": operation_id,
            "source_type": "upload",
            "title": "Uploaded source",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 202
    command = publication.commands[0]
    assert command.actor_user_id == "publisher01"
    assert command.content == b"exact upload bytes"
    assert command.original_filename == "source.pdf"
    assert command.media_type == "application/pdf"
    assert response.json["status"] == "queued"


def test_explicit_higher_target_is_parsed_identically_for_preview_and_commit() -> None:
    publication = _Publication()
    client = _app(publication).test_client()
    descriptor = {
        "capability": "publish",
        "kind": "team",
        "role_assignment_generation": 3,
        "role_assignment_id": _uuid("team-role"),
        "space_id": _uuid("team-space"),
    }
    preview_operation = _uuid("explicit-preview")
    created = client.post(
        "/api/knowledge/v1/publication-previews",
        headers={"Idempotency-Key": preview_operation},
        json={
            "markdown": "# Team\n",
            "operation_id": preview_operation,
            "source_type": "manual",
            "target": descriptor,
        },
    )
    assert created.status_code == 200
    assert publication.commands[0].target.to_mapping() == descriptor

    publish_operation = _uuid("explicit-publish")
    committed = client.post(
        "/api/knowledge/v1/publications",
        headers={"Idempotency-Key": publish_operation},
        json={
            "operation_id": publish_operation,
            "preview_id": publication.preview_id,
            "source_snapshot_sha256": "a" * 64,
            "target_mode": "explicit",
            "target": descriptor,
        },
    )
    assert committed.status_code == 201
    assert publication.commands[-1].target_mode == "explicit"
    assert publication.commands[-1].target.to_mapping() == descriptor

    missing = client.post(
        "/api/knowledge/v1/publications",
        json={
            "operation_id": _uuid("explicit-missing"),
            "preview_id": publication.preview_id,
            "source_snapshot_sha256": "a" * 64,
            "target_mode": "explicit",
        },
    )
    assert missing.status_code == 400
    assert missing.json["error"]["code"] == "VALIDATION_ERROR"


def test_preview_and_commit_reject_idempotency_drift_before_service_call() -> None:
    publication = _Publication()
    client = _app(publication).test_client()
    response = client.post(
        "/api/knowledge/v1/publication-previews",
        headers={"Idempotency-Key": _uuid("wrong")},
        json={
            "markdown": "# Example\n",
            "operation_id": _uuid("right"),
            "source_type": "manual",
        },
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert publication.commands == []

    invalid = client.post(
        "/api/knowledge/v1/publications",
        json={
            "operation_id": _uuid("invalid-publish"),
            "preview_id": publication.preview_id,
            "source_snapshot_sha256": "not-a-sha",
            "target_mode": "part",
        },
    )
    assert invalid.status_code == 400
    assert invalid.json["error"]["code"] == "VALIDATION_ERROR"
    assert publication.commands == []
