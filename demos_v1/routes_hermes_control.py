"""
demos_v1/routes_hermes_control.py
====================================================================
Hermes Agent 기동/상태 제어 라우트.

이 파일의 역할 (한 줄):
    demos_v1 UI 의 토글 버튼이 Hermes 의 dashboard / gateway 프로세스를
    켜고 끄고 상태를 확인할 수 있도록 4 개의 REST 라우트를 노출한다.

설계 원칙:
- 기존 demos_v1 코드를 절대 수정하지 않음.
- subprocess.Popen 으로 hermes CLI 를 백그라운드 실행. PID 는 모듈 전역에 보관.
- 외부 의존성 추가 없음 (psutil 등 안 씀, Windows/Linux 모두 stdlib 로 동작).
- 비활성화하려면 demos_v1/__init__.py 의 import / register 두 줄만 제거.

작성일: 2026-05-20
"""
import os
import sys
import time
import shutil
import subprocess

from flask import jsonify, request


# ============================================
# 모듈 전역 상태 — Hermes 자식 프로세스 트래킹
# ============================================
_hermes_procs = {
    "dashboard": None,  # subprocess.Popen 객체 또는 None
    "gateway": None,
}
_hermes_meta = {
    "dashboard": {"port": 9119, "started_at": None},
    "gateway": {"started_at": None},
}


# ============================================
# 헬퍼
# ============================================
def _hermes_binary():
    """PATH 에서 hermes 실행파일을 찾아 절대경로 반환. 없으면 None."""
    # Windows 는 hermes.exe 도 시도
    for name in ("hermes", "hermes.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _proc_alive(proc):
    """Popen 객체가 살아있는지 확인."""
    if proc is None:
        return False
    return proc.poll() is None


def _stop_proc(name):
    """저장된 Popen 자식을 안전하게 종료. 결과 dict 반환."""
    proc = _hermes_procs.get(name)
    if not _proc_alive(proc):
        _hermes_procs[name] = None
        return {"status": "not_running", "name": name}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        _hermes_procs[name] = None
        _hermes_meta[name]["started_at"] = None
        return {"status": "stopped", "name": name}
    except Exception as e:
        return {"status": "error", "name": name, "error": str(e)}


def _get_hermes_version():
    """`hermes --version` 출력을 한 줄 캡처. 실패 시 None."""
    binary = _hermes_binary()
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True, timeout=10,
            # Windows: 콘솔 깜빡임 방지
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            # "Hermes Agent v0.14.0 (...)" 같은 첫 줄만 추출
            first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
            return first_line.strip() or None
    except Exception:
        return None
    return None


def _hermes_home():
    """Hermes 홈 디렉토리(~/.hermes) 경로 반환."""
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _read_external_dirs():
    """~/.hermes/config.yaml 에서 skills.external_dirs 읽어서 리스트 반환.
    파일이 없거나 파싱 실패 시 빈 리스트.
    의도적으로 정규식 파싱 — YAML 의존성 추가 회피."""
    cfg_path = os.path.join(_hermes_home(), "config.yaml")
    if not os.path.exists(cfg_path):
        return []
    try:
        import re
        with open(cfg_path, "r", encoding="utf-8") as f:
            text = f.read()
        # external_dirs: 다음의 - 줄들을 수집
        m = re.search(r"external_dirs:\s*\n((?:\s*-\s*.+\n?)+)", text)
        if not m:
            return []
        block = m.group(1)
        dirs = []
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("-"):
                v = line[1:].strip().strip('"').strip("'")
                if v:
                    dirs.append(v)
        return dirs
    except Exception:
        return []


# ============================================
# 라우트 등록
# ============================================
def register_hermes_control_routes(app):
    """Hermes 기동 제어 라우트들을 Flask 앱에 등록한다."""

    # --------------------------------------------------------
    # GET /api/hermes/status — 종합 상태
    # --------------------------------------------------------
    @app.route("/api/hermes/status", methods=["GET"])
    def hermes_status():
        binary = _hermes_binary()
        version = _get_hermes_version() if binary else None
        cfg_exists = os.path.exists(os.path.join(_hermes_home(), "config.yaml"))
        external_dirs = _read_external_dirs()
        return jsonify({
            "installed": bool(binary),
            "binary_path": binary,
            "version": version,
            "home_dir": _hermes_home(),
            "config_yaml_exists": cfg_exists,
            "external_dirs": external_dirs,
            "dashboard": {
                "running": _proc_alive(_hermes_procs["dashboard"]),
                "pid": _hermes_procs["dashboard"].pid if _proc_alive(_hermes_procs["dashboard"]) else None,
                "port": _hermes_meta["dashboard"]["port"],
                "started_at": _hermes_meta["dashboard"]["started_at"],
                "url": f"http://127.0.0.1:{_hermes_meta['dashboard']['port']}" if _proc_alive(_hermes_procs["dashboard"]) else None,
            },
            "gateway": {
                "running": _proc_alive(_hermes_procs["gateway"]),
                "pid": _hermes_procs["gateway"].pid if _proc_alive(_hermes_procs["gateway"]) else None,
                "started_at": _hermes_meta["gateway"]["started_at"],
            },
        })

    # --------------------------------------------------------
    # POST /api/hermes/dashboard/start
    # --------------------------------------------------------
    @app.route("/api/hermes/dashboard/start", methods=["POST"])
    def hermes_dashboard_start():
        binary = _hermes_binary()
        if not binary:
            return jsonify({"status": "error", "error": "hermes 실행파일을 찾을 수 없음. pip install hermes-agent[web] 확인."}), 500
        if _proc_alive(_hermes_procs["dashboard"]):
            return jsonify({
                "status": "already_running",
                "pid": _hermes_procs["dashboard"].pid,
                "port": _hermes_meta["dashboard"]["port"],
            })
        body = request.get_json(silent=True) or {}
        port = int(body.get("port", 9119))
        try:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                # Windows: 새 콘솔 창 안 띄우고 백그라운드로
                kwargs["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
                )
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                [binary, "dashboard", "--no-open", "--port", str(port)],
                **kwargs,
            )
            _hermes_procs["dashboard"] = proc
            _hermes_meta["dashboard"] = {"port": port, "started_at": time.time()}
            # 살아있는지 짧게 대기 후 확인
            time.sleep(0.5)
            if not _proc_alive(proc):
                _hermes_procs["dashboard"] = None
                return jsonify({"status": "error", "error": "프로세스가 즉시 종료됨. hermes dashboard 수동 실행해서 에러 확인."}), 500
            return jsonify({
                "status": "started",
                "pid": proc.pid,
                "port": port,
                "url": f"http://127.0.0.1:{port}",
            })
        except Exception as e:
            _hermes_procs["dashboard"] = None
            return jsonify({"status": "error", "error": str(e)}), 500

    # --------------------------------------------------------
    # POST /api/hermes/dashboard/stop
    # --------------------------------------------------------
    @app.route("/api/hermes/dashboard/stop", methods=["POST"])
    def hermes_dashboard_stop():
        result = _stop_proc("dashboard")
        status_code = 200 if result.get("status") in ("stopped", "not_running") else 500
        return jsonify(result), status_code

    # --------------------------------------------------------
    # POST /api/hermes/gateway/start
    # --------------------------------------------------------
    @app.route("/api/hermes/gateway/start", methods=["POST"])
    def hermes_gateway_start():
        binary = _hermes_binary()
        if not binary:
            return jsonify({"status": "error", "error": "hermes 실행파일을 찾을 수 없음."}), 500
        if _proc_alive(_hermes_procs["gateway"]):
            return jsonify({
                "status": "already_running",
                "pid": _hermes_procs["gateway"].pid,
            })
        try:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
                )
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen([binary, "gateway", "start"], **kwargs)
            _hermes_procs["gateway"] = proc
            _hermes_meta["gateway"]["started_at"] = time.time()
            time.sleep(0.5)
            if not _proc_alive(proc):
                _hermes_procs["gateway"] = None
                return jsonify({
                    "status": "error",
                    "error": "gateway 가 즉시 종료됨. 메시징 설정(텔레그램 토큰 등)이 필요할 수 있음. hermes setup gateway 먼저 실행하세요.",
                }), 500
            return jsonify({"status": "started", "pid": proc.pid})
        except Exception as e:
            _hermes_procs["gateway"] = None
            return jsonify({"status": "error", "error": str(e)}), 500

    # --------------------------------------------------------
    # POST /api/hermes/gateway/stop
    # --------------------------------------------------------
    @app.route("/api/hermes/gateway/stop", methods=["POST"])
    def hermes_gateway_stop():
        result = _stop_proc("gateway")
        status_code = 200 if result.get("status") in ("stopped", "not_running") else 500
        return jsonify(result), status_code

    # 콘솔에 등록 알림
    print("  ⚕ Hermes 제어 라우트 활성: /api/hermes/{status,dashboard/{start,stop},gateway/{start,stop}}")


# ============================================
# (보조) 종료 핸들러 — demos_v1 종료 시 자식 프로세스도 정리
# ============================================
def _cleanup_on_exit():
    """demos_v1 가 종료될 때 띄워둔 hermes 자식 프로세스들 정리."""
    for name in ("dashboard", "gateway"):
        try:
            _stop_proc(name)
        except Exception:
            pass


import atexit as _atexit
_atexit.register(_cleanup_on_exit)
