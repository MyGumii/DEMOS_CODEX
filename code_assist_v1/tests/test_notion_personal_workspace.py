"""UI-PERSONAL-APPEARANCE: ownership, durable retries, migration and HTTP."""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from flask import Flask
from PIL import Image
import pytest

from code_assist_v1 import notion_store as ns, notion_personal_workspace as service
from code_assist_v1 import notion_personal_workspace_schema as schema
from code_assist_v1 import routes_notion_personal_workspace as routes


@pytest.fixture
def environment(monkeypatch, isolated_knowledge_layout):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(isolated_knowledge_layout.user_data_dir))
    users = [{"id": name, "display_name": name, "disabled": False} for name in ("owner", "guest", "outsider")]
    monkeypatch.setattr(service.auth, "list_public_users", lambda: users)
    for name in ("CODE_ASSIST_I0_COLLABORATIVE_SHARING", "CODE_ASSIST_NP2B_NAVIGATION_UI"):
        monkeypatch.delenv(name, raising=False)
    return users, isolated_knowledge_layout


def png(color="blue"):
    stream = io.BytesIO(); Image.new("RGB", (8, 8), color).save(stream, format="PNG")
    return stream.getvalue()


def snapshot():
    with ns._connection("owner") as con:
        return schema.snapshot_sha(con)


def save(icon="🌱", operation="icon", version=1, **kwargs):
    return service.update("owner", "owner", icon=icon, operation_id=operation, base_version=version, **kwargs)


def test_ui_personal_read_has_no_bootstrap_and_creation_installs_schema(environment):
    assert service.read("owner", "owner")["capabilities"]["workspace_appearance"] is False
    assert not Path(ns._db_path("owner")).exists()
    with pytest.raises(FileNotFoundError): service.read("outsider", "owner")
    ns.bootstrap_workspace("owner")
    before = snapshot()
    assert service.read("owner", "owner")["workspace"]["version"] == 1
    assert service.read("owner", "owner")["capabilities"]["workspace_image"] is True
    assert snapshot() == before
    assert schema.CHECKSUM == "sha256:" + hashlib.sha256("\n".join(schema.DDL).encode()).hexdigest()


def test_ui_personal_explicit_migration_backup_and_no_request_ddl(environment, tmp_path):
    ns._workspace_root("owner", create=True)
    with sqlite3.connect(ns._db_path("owner")) as con:
        ns._configure_connection(con, query_only=False)
        ns._ensure_schema(con, bootstrap_atomic=True)
    page = ns.create_page("owner", title="Keep this page")["page"]
    before = snapshot()
    plan = schema.build_plan("owner")
    assert plan["state"] == "migration_required"
    assert service.read("owner", "owner")["capabilities"]["workspace_appearance"] is False
    with pytest.raises(ns.AtomicSaveUnavailable): save()
    assert snapshot() == before
    with pytest.raises(ValueError): schema.apply_plan(plan, backup_path=tmp_path / "no.db")
    backup = tmp_path / "before.db"
    result = schema.apply_plan(plan, backup_path=backup, quiesced_confirmed=True)
    assert result["state"] == "already_current"
    with sqlite3.connect(backup) as con:
        assert schema.snapshot_sha(con) == before
    assert ns.get_page("owner", page["id"])["page"]["title"] == "Keep this page"
    assert schema.apply_plan(result, backup_path=backup, quiesced_confirmed=True) == result
    with pytest.raises(RuntimeError): schema.apply_plan(plan, backup_path=tmp_path / "stale.db", quiesced_confirmed=True)
    assert not (tmp_path / "stale.db").exists()


def test_ui_personal_exact_retry_cas_and_current_owner(environment):
    ns.bootstrap_workspace("owner")
    first = save()
    second = save("ni:rocket:purple", "second", 2)
    before = snapshot()
    assert save() == first and snapshot() == before
    assert service.read("owner", "owner")["workspace"]["icon"] == second["workspace"]["icon"]
    with pytest.raises(service.AppearanceConflict): save("🐈")
    with pytest.raises(service.AppearanceConflict): save("🐈", "stale", 1)
    with pytest.raises(PermissionError): service.update("outsider", "owner", icon="", base_version=3, operation_id="admin-cannot-own")
    environment[0][0]["disabled"] = True
    with pytest.raises(FileNotFoundError): save()
    environment[0][0]["disabled"] = False
    with ns._connection("owner") as con:
        for table in ("personal_workspace_operations", "personal_workspace_audit", "personal_workspace_outbox"):
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 2


def test_ui_personal_images_shared_visibility_revoke_and_page_preservation(environment):
    page = ns.create_page("owner", title="Independent body")["page"]
    before_page = ns.get_page("owner", page["id"])
    first = save(None, raw=png(), mime_type="image/png")
    image_id = first["workspace"]["icon"].rsplit(":", 1)[1]
    assert service.read_image("owner", "owner", image_id)["raw"] == png()
    with pytest.raises(FileNotFoundError): service.read_image("guest", "owner", image_id)
    ns.share_page("owner", page["id"], "guest", role="view")
    assert service.read_image("guest", "owner", image_id)["raw"] == png()
    assert service.read("guest", "owner")["workspace"]["access"]["role"] == "guest"
    with pytest.raises(PermissionError): service.update("guest", "owner", icon="", base_version=2, operation_id="guest-write")
    ns.revoke_share("owner", page["id"], "guest")
    with pytest.raises(FileNotFoundError): service.read_image("guest", "owner", image_id)
    save("", "clear", 2)
    with pytest.raises(FileNotFoundError): service.read_image("owner", "owner", image_id)
    with ns._connection("owner") as con:
        assert con.execute("SELECT image_bytes FROM personal_workspace_appearance").fetchone()[0] is None
    assert ns.get_page("owner", page["id"])["blocks"] == before_page["blocks"]
    assert ns.get_page("owner", page["id"])["revision"] == before_page["revision"]
    for invalid in ("nf:f_fake", "nw:wi_fake", first["workspace"]["icon"], "ni:not-an-icon:blue"):
        with pytest.raises(ValueError): save(invalid, "invalid", 3)


def test_ui_personal_bytes_receipt_audit_rollback_and_validation(environment):
    ns.bootstrap_workspace("owner")
    with ns._write_connection("owner") as con:
        con.execute("CREATE TRIGGER stop_outbox BEFORE INSERT ON personal_workspace_outbox BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        con.commit()
    before = snapshot()
    with pytest.raises(sqlite3.IntegrityError): save(None, raw=png(), mime_type="image/png")
    assert snapshot() == before
    for raw, mime in ((b"bad", "image/png"), (png(), "image/jpeg"), (b"x" * (service.MAX_BYTES + 1), "image/png")):
        with pytest.raises(ValueError): save(None, raw=raw, mime_type=mime)
    assert snapshot() == before


def test_ui_personal_http_ownership_headers_and_mode_boundary(environment, monkeypatch):
    ns.bootstrap_workspace("owner")
    actor = ["owner"]
    monkeypatch.setattr(routes, "current_user_id", lambda: actor[0])
    app = Flask(__name__); app.config.update(TESTING=True)
    routes.register_personal_workspace_routes(app)
    client = app.test_client(); base = "/api/notion/personal-workspaces/owner"
    assert client.get(base + "/appearance").json["capabilities"]["workspace_image"] is True
    response = client.post(base + "/icon-image", data=png(), content_type="image/png", headers={"X-Notion-Base-Version": "1", "X-Notion-Operation-Id": "http-image"})
    assert response.status_code == 200
    image_id = response.json["workspace"]["icon"].rsplit(":", 1)[1]
    image = client.get(base + "/images/" + image_id)
    assert image.data == png() and image.mimetype == "image/png"
    assert "no-store" in image.headers["Cache-Control"] and image.headers["X-Content-Type-Options"] == "nosniff"
    assert client.patch(base + "/appearance", json={"icon": "", "base_version": 1, "operation_id": "stale"}).status_code == 409
    assert client.patch(base + "/appearance", json=[]).status_code == 400
    actor[0] = "outsider"
    assert client.get(base + "/appearance").status_code == 404
    assert client.patch(base + "/appearance", json={"icon": "", "base_version": 2, "operation_id": "no"}).status_code == 403
    actor[0] = None
    assert client.get(base + "/images/" + image_id).status_code == 403
    actor[0] = "owner"
    monkeypatch.setenv("CODE_ASSIST_I0_COLLABORATIVE_SHARING", "1")
    assert client.get(base + "/appearance").status_code == 404


def test_ui_personal_same_operation_across_processes(environment):
    ns.bootstrap_workspace("owner")
    root = Path(ns.USER_DATA_DIR).resolve()
    worker = """
import json, os, sys
from pathlib import Path
from code_assist_v1 import notion_store as ns, notion_personal_workspace as service
root = Path(sys.argv[1]).resolve()
assert root.is_relative_to(Path(os.environ['CODE_ASSIST_TEST_DATA_ROOT']).resolve())
ns.USER_DATA_DIR = str(root)
service.auth.list_public_users = lambda: [{'id':'owner','disabled':False}]
print('READY', flush=True)
sys.stdin.readline()
result = service.update('owner','owner',icon='ni:rocket:purple',base_version=1,operation_id='same-cross-process')
print(json.dumps(result), flush=True)
"""
    env = dict(os.environ, CODE_ASSIST_TEST_MODE="1", CODE_ASSIST_TEST_DATA_ROOT=str(root.parent))
    workers = [subprocess.Popen([sys.executable, "-c", worker, str(root)], cwd=Path(__file__).resolve().parents[2], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for _ in range(2)]
    try:
        for process in workers:
            while True:
                line = process.stdout.readline()
                assert line, process.stderr.read()
                if line.strip() == "READY": break
        for process in workers:
            process.stdin.write("apply\n"); process.stdin.flush()
        results = []
        for process in workers:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, error
            results.append(json.loads(output.strip().splitlines()[-1]))
        assert results[0] == results[1]
        with ns._connection("owner") as con:
            assert con.execute("SELECT version FROM personal_workspace_appearance").fetchone()[0] == 2
            assert con.execute("SELECT count(*) FROM personal_workspace_operations").fetchone()[0] == 1
            assert con.execute("SELECT count(*) FROM personal_workspace_outbox").fetchone()[0] == 1
    finally:
        for process in workers:
            if process.poll() is None: process.kill(); process.wait(timeout=10)
