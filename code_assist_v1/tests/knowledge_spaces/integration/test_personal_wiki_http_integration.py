from __future__ import annotations

from flask import Flask
import pytest

from code_assist_v1.knowledge_core.personal_wiki_routes import (
    PERSONAL_WIKI_ROUTE_PREFIX,
    register_personal_wiki_routes,
)
from code_assist_v1.knowledge_core.services.personal_wikis import (
    PersonalWikiLifecycleService,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_personal_wiki_lifecycle_service import (
    _CURSOR_SECRET,
    _migrate_current,
    _seed,
    _uuid,
)


pytestmark = pytest.mark.integration


def _operation(label: str, **values: object) -> dict[str, object]:
    return {"operation_id": _uuid(f"http:{label}"), **values}


@pytest.mark.scenario(
    "WIKI-005",
    "WIKI-006",
    "WIKI-007",
    "WIKI-008",
    "WIKI-009",
    "WIKI-010",
    "WIKI-011",
)
def test_personal_wiki_http_routes_execute_real_lifecycle_transactions(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    ids = _seed(factory, ("owner", "user"))
    app = Flask("sbe-2.1-http-integration", static_folder=None)
    register_personal_wiki_routes(
        app,
        service=PersonalWikiLifecycleService(
            factory,
            cursor_secret=_CURSOR_SECRET,
        ),
        principal_resolver=lambda: {"id": "owner", "scope_epoch": 1},
        writes_enabled=True,
    )
    client = app.test_client()

    initial = client.get(PERSONAL_WIKI_ROUTE_PREFIX)
    assert initial.status_code == 200
    assert [item["space_id"] for item in initial.json["items"]] == [ids["owner"]]

    created = client.post(
        PERSONAL_WIKI_ROUTE_PREFIX,
        json=_operation(
            "create",
            display_name="HTTP Wiki",
            expected_row_version=1,
        ),
    )
    assert created.status_code == 201
    wiki_id = created.json["resource_id"]
    assert created.json["row_version"] == 1

    renamed = client.patch(
        f"{PERSONAL_WIKI_ROUTE_PREFIX}/{wiki_id}",
        json=_operation(
            "rename",
            display_name="HTTP Wiki renamed",
            expected_row_version=1,
        ),
    )
    assert renamed.status_code == 200
    assert renamed.json["row_version"] == 2

    selected = client.put(
        f"{PERSONAL_WIKI_ROUTE_PREFIX}/{wiki_id}/default",
        json=_operation("select", expected_row_version=2),
    )
    assert selected.status_code == 200
    assert selected.json["row_version"] == 3
    returned = client.put(
        f"{PERSONAL_WIKI_ROUTE_PREFIX}/{ids['owner']}/default",
        json=_operation("return", expected_row_version=2),
    )
    assert returned.status_code == 200

    archived = client.post(
        f"{PERSONAL_WIKI_ROUTE_PREFIX}/{wiki_id}/archive",
        json=_operation(
            "archive",
            expected_row_version=4,
            reason="HTTP integration",
        ),
    )
    assert archived.status_code == 200
    assert archived.json["row_version"] == 5
    active = client.get(PERSONAL_WIKI_ROUTE_PREFIX).json["items"]
    archived_items = client.get(
        PERSONAL_WIKI_ROUTE_PREFIX + "?lifecycle_status=archived"
    ).json["items"]
    assert wiki_id not in {item["space_id"] for item in active}
    assert wiki_id in {item["space_id"] for item in archived_items}

    restored = client.post(
        f"{PERSONAL_WIKI_ROUTE_PREFIX}/{wiki_id}/restore",
        json=_operation(
            "restore",
            expected_row_version=5,
            make_default=False,
        ),
    )
    assert restored.status_code == 200
    assert restored.json["row_version"] == 6
    active = client.get(PERSONAL_WIKI_ROUTE_PREFIX).json["items"]
    restored_profile = next(item for item in active if item["space_id"] == wiki_id)
    assert restored_profile["lifecycle_generation"] == 3
    assert restored_profile["is_default"] is False

    assert not any(
        rule.rule == PERSONAL_WIKI_ROUTE_PREFIX + "/search"
        for rule in app.url_map.iter_rules()
    )
    assert client.post(PERSONAL_WIKI_ROUTE_PREFIX + "/search", json={}).status_code == 405
