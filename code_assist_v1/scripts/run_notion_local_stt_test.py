"""Run local STT + Notion 10322; leave SBE/login 10011 untouched.

Requires the optional isolated venv and pre-downloaded base model in .tools/notion-stt.
Refuses occupied ports. Stop this launcher to stop only the children it started.
Secrets are generated in memory and shared through child environments, never files.
"""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request


def stop_child(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def app_command(app_python, *, codex=False):
    command = [str(app_python), "-u", "-m", "code_assist_v1.app_notion", "--port", "10322"]
    if codex:
        command.append("--codex")
    return command


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparisons", action="store_true", help="Enable opt-in encrypted host-only AI note comparisons")
    parser.add_argument("--codex", action="store_true", help="Explicitly enable the authenticated Codex fallback (default: disabled)")
    parser.add_argument("--app-python", type=Path, default=root / "venv/Scripts/python.exe",
                        help="Python interpreter for the Notion application")
    parser.add_argument("--stt-python", type=Path, default=root / ".tools/notion-stt/venv/Scripts/python.exe",
                        help="Python interpreter for the isolated speech service")
    args = parser.parse_args()
    local_python = args.stt_python.resolve()
    app_python = args.app_python.resolve()
    model_dir = root / ".tools/notion-stt/models/base"
    for path in (local_python, app_python, model_dir / "model.bin"):
        if not path.is_file():
            raise SystemExit("Missing local STT dependency/model; see NOTION_LOCAL_STT_TEST_20260911.md")
    for port in (10322, 10323):
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Port {port} is occupied; inspect its exact process before stopping it")

    token = secrets.token_urlsafe(40)
    env = os.environ.copy()
    speech_env = {**env, "NOTION_LOCAL_STT_TOKEN": token, "HF_HUB_OFFLINE": "1",
                  "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
    children = []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        stt = subprocess.Popen([str(local_python), "-u", str(root / "code_assist_v1/notion_local_stt.py"),
                                "--model-dir", str(model_dir), "--port", "10323"],
                               env=speech_env, cwd=root, creationflags=flags)
        children.append(stt)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 60
        while True:
            if stt.poll() is not None or time.monotonic() > deadline:
                raise SystemExit("Local STT did not become ready; Notion was not started")
            try:
                request = urllib.request.Request("http://127.0.0.1:10323/healthz",
                                                 headers={"Authorization": "Bearer " + token})
                with opener.open(request, timeout=1) as response:
                    health = json.loads(response.read(4096))
                if health.get("ready") and health.get("service") == "notion_local_stt":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.3)
        env.update({
            "CODE_ASSIST_STANDALONE": "1",
            "CODE_ASSIST_NOTION_STT_MODE": "local_test",
            "CODE_ASSIST_NOTION_STT_LOCAL_URL": "http://127.0.0.1:10323/v1/audio/transcriptions",
            "CODE_ASSIST_NOTION_STT_LOCAL_MODEL": "faster-whisper-base",
            "CODE_ASSIST_NOTION_STT_LOCAL_PROTOCOL": "multipart",
            "CODE_ASSIST_NOTION_STT_LOCAL_API_KEY": token,
        })
        if args.comparisons:
            env.update({"CODE_ASSIST_NOTION_NOTES_COMPARISONS": "1",
                        "CODE_ASSIST_NOTION_NOTES_COMPARISON_DB": str(root / ".private/notion-notes-comparisons/comparisons.db")})
        required_origins = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (10011, 10322)]
        origins = [part.strip() for part in env.get("CODE_ASSIST_ALLOWED_ORIGINS", "").split(",") if part.strip()]
        env["CODE_ASSIST_ALLOWED_ORIGINS"] = ",".join(dict.fromkeys(origins + required_origins))
        check = subprocess.run([str(app_python), "-c",
            "from code_assist_v1.notion_audio import capabilities; "
            "c=capabilities(); assert c.get('enabled') and c.get('local_only'), c.get('reason'); "
            "print('Notion local speech capability verified; automatic summaries default OFF')"],
            env=env, cwd=root, timeout=15, creationflags=flags)
        if check.returncode:
            raise SystemExit("Notion could not verify local STT; application was not started")
        app = subprocess.Popen(app_command(app_python, codex=args.codex),
                               env=env, cwd=root, creationflags=flags)
        children.append(app)
        print("Local STT connected. Notion: http://127.0.0.1:10322 ; automatic summaries default OFF.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            stop_child(child)


if __name__ == "__main__":
    main()
