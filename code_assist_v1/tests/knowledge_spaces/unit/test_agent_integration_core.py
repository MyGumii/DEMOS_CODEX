"""AIK-015: real canonical adapter, isolated migrated SQLite and real services."""
from dataclasses import replace
from uuid import uuid4
import multiprocessing

import pytest

from code_assist_v1.agent_integrations.bridge import KnowledgeBridge
from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.services.publication import PublicationApiService, PublicationPreviewService, PublicationService
from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import wiki

pytestmark = pytest.mark.unit


def durable_service(core_path,ledger_path):
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
    from code_assist_v1.agent_integrations.store import IntegrationStore
    from code_assist_v1.agent_integrations.service import IntegrationService
    from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import Stager
    from code_assist_v1.tests.knowledge_spaces.unit.test_agent_integrations import model_output
    import json
    factory=ConnectionFactory._for_isolated_tests(core_path,CANONICAL_MIGRATION_CATALOG)
    flags=FeatureFlags(core_enabled=True,personal_reads=True,personal_writes=True,
        multi_wiki_schema=True,multi_wiki_reads=True,multi_wiki_writes=True,conversation_wiki_binding=True)
    runtime=compose_canonical_agent_runtime(factory,flags=flags,principal_resolver=lambda:None,
        personal_wiki_cursor_secret=b"a"*32,source_artifact_stager=Stager())
    store=IntegrationStore(ledger_path,b"x"*32);store.initialize()
    return IntegrationService(store,KnowledgeBridge(runtime.services,runtime.route_bindings),
        runner=lambda *a,**kw:(json.dumps(model_output(),ensure_ascii=False),{}))


def publication_process(core_path,ledger_path,rid,output):
    try:
        result=durable_service(core_path,ledger_path).publish("member01",rid)
        output.put(result["publication"]["results"][0]["item_id"])
    except Exception as exc:
        output.put(type(exc).__name__+": "+str(exc))
        raise


@pytest.mark.parametrize("crash_after_commit",[False,True])
def test_aik_024_cross_process_publication_and_commit_receipt_recovery(wiki,tmp_path,crash_after_commit):
    from code_assist_v1.tests.knowledge_spaces.unit.test_agent_integrations import submission
    factory,ids,_,_,_=wiki
    ledger=tmp_path/"integration-ledger.db"
    service=durable_service(factory.database_path,ledger)
    space=str(ids["member_space"])
    conn=service.create_connection("member01",dict(operation_id=str(uuid4()),name="Process fixture",
        read_space_id=space,write_space_id=space,scopes=["knowledge:read","results:write"]))
    rid=service.submit("member01",conn["id"],submission())["id"]
    run=service.process("member01",rid)
    assert not run["draft"]["blockers"]
    service.store.review("member01",rid,str(uuid4()),run["row_version"],run["draft_hash"],"approve",lambda:None)
    c=factory.open(read_only=True);before=c.execute("SELECT COUNT(*) FROM knowledge_item").fetchone()[0];c.close()
    if crash_after_commit:
        service.store.record_publication=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError("simulated lost receipt"))
        with pytest.raises(RuntimeError):
            service.publish("member01",rid)
        assert service.store.run("member01",rid)["state"]=="publishing"
    ctx=multiprocessing.get_context("spawn");output=ctx.Queue()
    processes=[ctx.Process(target=publication_process,args=(factory.database_path,ledger,rid,output)) for _ in range(2)]
    try:
        for p in processes:p.start()
        for p in processes:
            p.join(30);assert p.exitcode==0
        one,two=output.get(timeout=5),output.get(timeout=5)
        assert one==two
        c=factory.open(read_only=True)
        assert c.execute("SELECT COUNT(*) FROM knowledge_item").fetchone()[0]==before+1
        c.close()
        assert service.store.run("member01",rid)["state"]=="published"
    finally:
        for p in processes:
            if p.is_alive():p.terminate();p.join(5)
        output.close()


def test_aik_015_real_core_search_read_ask_and_publication_replay(wiki):
    factory, ids, source, management, _ = wiki
    flags = FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
                         multi_wiki_schema=True, multi_wiki_reads=True, multi_wiki_writes=True,
                         conversation_wiki_binding=True)
    runtime = compose_canonical_agent_runtime(factory, flags=flags,
        principal_resolver=lambda: {"id": "member01", "scope_epoch": 1},
        personal_wiki_cursor_secret=b"a"*32, model_runner=lambda *a, **k: ("Fixture answer", {}))
    services = replace(runtime.services, source_artifacts=source, management_reads=management,
        publication=PublicationApiService(PublicationPreviewService(factory, runtime.scope_resolver, source._stager),
                                         PublicationService(factory, runtime.scope_resolver)))
    bridge = KnowledgeBridge(services, runtime.route_bindings)
    actor, space = bridge.actor("member01"), str(ids["member_space"])
    assert space in [r["space_id"] for r in bridge.wikis(actor)["items"]]
    document = bridge.get(actor, space, str(ids["item_id"]))
    assert document["revision_id"] == str(ids["revision_id"])
    assert bridge.search(actor, space, document["title"])["items"]
    operation = str(uuid4())
    answer = bridge.ask(actor, space, operation, "What is known?", lambda: None)
    assert answer["state"] == "completed"
    assert answer["answer"] == "Fixture answer"
    assert bridge.ask(actor, space, operation, "What is known?", lambda: None) == answer
    with pytest.raises(Exception):
        bridge.ask(actor, space, operation, "Changed question", lambda: None)
    publication = str(uuid4())
    args = (actor, publication, space, "Integration evidence", "# Integration evidence\n\nAttributed source only.\n", lambda: None)
    result = bridge.publish(*args)
    assert bridge.publish(*args) == result
    assert len(result["results"]) == 1
    assert result["results"][0]["space_id"] == space
    with pytest.raises(Exception):
        bridge.get(bridge.actor("other01"), space, str(ids["item_id"]))
