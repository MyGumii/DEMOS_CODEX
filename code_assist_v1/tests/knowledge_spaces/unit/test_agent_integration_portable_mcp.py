"""AIK-034/035: one copied or downloaded client file and real MCP/HTTP."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.request import urlopen

import pytest
from werkzeug.serving import make_server

from code_assist_v1.tests.knowledge_spaces.unit.test_agent_integrations import (
    service, connect, app_for,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("token_mode", ["file", "environment"])
@pytest.mark.parametrize("delivery", ["copy", "download"])
def test_aik_034_035_single_file_without_server_package(service, tmp_path, token_mode, delivery):
    python = Path("D:/Hermes/hermes-agent/venv/Scripts/python.exe")
    if not python.is_file():
        pytest.skip("Hermes environment not installed on this host")
    client_dir = tmp_path / "client-only"
    client_dir.mkdir()
    adapter = client_dir / "hermes_mcp.py"
    root = Path(__file__).resolve().parents[3]
    if delivery == "copy":
        shutil.copyfile(root / "agent_integrations/hermes_mcp.py", adapter)
    conn = connect(service)
    app = app_for(service)
    app.static_folder = str(root / "static")
    requests = []
    @app.before_request
    def track_request():
        from flask import request
        requests.append(request.path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {k: v for k, v in os.environ.items() if k not in {
        "PYTHONPATH", "PYTHONHOME", "SECOND_BRAIN_TOKEN", "SECOND_BRAIN_TOKEN_FILE",
    }}
    env["SECOND_BRAIN_URL"] = f"http://127.0.0.1:{server.server_port}"
    if token_mode == "file":
        token_file = tmp_path / "fixture.token"
        token_file.write_text(conn["token"] + "\n", encoding="utf-8-sig")
        env["SECOND_BRAIN_TOKEN_FILE"] = str(token_file)
    else:
        env["SECOND_BRAIN_TOKEN"] = conn["token"]
    probe = tmp_path / "portable_probe.py"
    probe.write_text('''import asyncio, importlib.util, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
assert importlib.util.find_spec("code_assist_v1") is None
async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable,
            args=["-I", sys.argv[1]], env=dict(os.environ))) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "second_brain_status", "second_brain_search", "second_brain_read",
                "second_brain_ask", "second_brain_submit", "second_brain_run",
                "second_brain_refine", "second_brain_record_notion",
                "second_brain_outlook_inbox", "second_brain_observe_outlook_reply",
            }
            status = await session.call_tool("second_brain_status", {})
            assert not status.is_error
            result = json.loads(next(c.text for c in status.content if c.type == "text"))
            assert result["enabled"] and result["connection_id"] == sys.argv[2]
            for bad_id in ("../status", "NOT-A-UUID", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"):
                invalid = await session.call_tool("second_brain_read", {"item_id": bad_id})
                assert invalid.is_error
            status = await session.call_tool("second_brain_status", {})
            assert not status.is_error
            print("Portable MCP: all ten tools, authenticated status, UUID rejection and recovery PASS")
asyncio.run(main())
''', encoding="utf-8")
    try:
        if delivery == "download":
            base = env["SECOND_BRAIN_URL"] + "/static/integrations/hermes/"
            with urlopen(base + "manifest.json", timeout=10) as response:
                manifest = json.load(response)
            with urlopen(base + "hermes_mcp.py", timeout=10) as response:
                downloaded = response.read()
            assert manifest["schema_version"] == 1 and manifest["filename"] == adapter.name
            assert len(downloaded) == manifest["size_bytes"]
            assert hashlib.sha256(downloaded).hexdigest() == manifest["sha256"]
            adapter.write_bytes(downloaded)
            assert requests == ["/static/integrations/hermes/manifest.json", "/static/integrations/hermes/hermes_mcp.py"]
            requests.clear()
        assert [p.name for p in client_dir.iterdir()] == ["hermes_mcp.py"]
        # -I excludes the current directory/PYTHONPATH; -S excludes site packages.
        checked = subprocess.run([sys.executable, "-I", "-S", str(adapter), "--check"],
            cwd=client_dir, env=env, capture_output=True, text=True, timeout=30)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert json.loads(checked.stdout)["connection_id"] == conn["id"]
        completed = subprocess.run([str(python), "-I", str(probe), str(adapter), conn["id"]],
            cwd=client_dir, env=env, capture_output=True, text=True, timeout=45)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert conn["token"] not in checked.stdout + checked.stderr + completed.stdout + completed.stderr
        assert requests == ["/api/integrations/v1/status"] * 3
    finally:
        server.shutdown()
        thread.join(10)


def test_aik_035_packaged_assets_current_and_real_static_auth_boundaries(service, tmp_path, monkeypatch):
    from code_assist_v1.tools import package_hermes_client as package
    from code_assist_v1.tests.knowledge_spaces.integration.test_auth_flask_security import _make_app, _ORIGIN
    from code_assist_v1.agent_integrations.routes import register
    expected = package.artifacts()
    assert set(expected) == {"hermes_mcp.py", "install.md", "manifest.json", "second-brain/SKILL.md"}
    for name, data in expected.items():
        assert (package.DESTINATION / name).read_bytes() == data, "Rebuild public Hermes client assets"
    app = _make_app()
    app.static_folder = str(package.ROOT / "static")
    register(app, service, lambda: {"id": "alice"})
    client = app.test_client()
    for name, data in expected.items():
        path = "/static/integrations/hermes/" + name
        response = client.get(path, base_url=_ORIGIN)
        assert response.status_code == 200 and response.data == data
        assert client.get(path, base_url="https://evil.example").status_code == 403
    assert client.get("/api/integrations/v1/status", base_url=_ORIGIN).status_code == 401
    conn = connect(service)
    assert client.get("/api/integrations/v1/status", base_url=_ORIGIN,
        headers={"Authorization": "Bearer " + conn["token"]}).status_code == 200
    # Packaging writes only the explicit code artifacts, and --check catches drift.
    monkeypatch.setattr(package, "DESTINATION", tmp_path / "packaged")
    monkeypatch.setattr(sys, "argv", ["package_hermes_client"])
    package.main()
    monkeypatch.setattr(sys, "argv", ["package_hermes_client", "--check"])
    package.main()
    (package.DESTINATION / "hermes_mcp.py").write_text("stale", encoding="utf-8")
    with pytest.raises(SystemExit) as rejected:
        package.main()
    assert rejected.value.code == 1


def test_aik_037_downloaded_skill_loads_in_native_hermes(tmp_path):
    from code_assist_v1.tools import package_hermes_client as package
    from code_assist_v1.tests.knowledge_spaces.integration.test_auth_flask_security import _make_app, _ORIGIN

    hermes_root = Path("D:/Hermes/hermes-agent")
    python = hermes_root / "venv/Scripts/python.exe"
    if not python.is_file():
        pytest.skip("Hermes environment not installed on this host")
    app = _make_app()
    app.static_folder = str(package.ROOT / "static")
    client = app.test_client()
    base = "/static/integrations/hermes/"
    response = client.get(base + "manifest.json", base_url=_ORIGIN)
    assert response.status_code == 200
    manifest = response.json["skill"]
    assert manifest["name"] == "second-brain" and manifest["filename"] == "second-brain/SKILL.md"
    response = client.get(base + manifest["filename"], base_url=_ORIGIN)
    assert response.status_code == 200
    downloaded = response.data
    assert len(downloaded) == manifest["size_bytes"]
    assert hashlib.sha256(downloaded).hexdigest() == manifest["sha256"]

    # Native loading may write usage/cache metadata: keep all of it in a synthetic profile.
    profile = tmp_path / "hermes-home"
    skill_dir = profile / "skills/second-brain"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_bytes(downloaded)
    (profile / "config.yaml").write_text("{}\n", encoding="utf-8")
    probe = tmp_path / "skill_probe.py"
    probe.write_text('''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_constants import get_hermes_home
from tools.skill_linter import lint_skill, format_findings
from tools.skill_manager_tool import _validate_frontmatter
from tools.skills_tool import skill_view
from agent.skill_commands import scan_skill_commands, build_skill_invocation_message, extract_user_instruction_from_skill_message
skill_path = get_hermes_home() / "skills/second-brain/SKILL.md"
content = skill_path.read_text(encoding="utf-8")
assert _validate_frontmatter(content, new_skill=True) is None
findings = lint_skill(skill_path)
assert not findings, format_findings(findings)
commands = scan_skill_commands()
assert Path(commands["/second-brain"]["skill_md_path"]).resolve() == skill_path.resolve()
loaded = json.loads(skill_view("second-brain", preprocess=False))
assert loaded["success"] and loaded["name"] == "second-brain"
assert loaded["content"] == content
question = "Use the selected Wiki for this project question."
message = build_skill_invocation_message("/second-brain", question)
assert message and question in message
assert extract_user_instruction_from_skill_message(message) == question
print("Native Hermes: downloaded skill validation, discovery, full loading and slash invocation PASS")
''', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {
        "PYTHONPATH", "PYTHONHOME", "SECOND_BRAIN_TOKEN", "SECOND_BRAIN_TOKEN_FILE",
    }}
    env["HERMES_HOME"] = str(profile)
    completed = subprocess.run([str(python), "-I", str(probe), str(hermes_root)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert completed.returncode == 0, completed.stdout + completed.stderr
