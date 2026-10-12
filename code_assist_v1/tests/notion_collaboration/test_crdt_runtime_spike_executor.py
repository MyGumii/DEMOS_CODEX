from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from code_assist_v1.tools.notion_parity.crdt_runtime_spike_executor import (
    MARKER_NAME,
    SpikeExecutionError,
    cleanup_owned_run_root,
    copy_adapter,
    file_sha256,
    initialize_owned_root,
    load_owned_marker,
    run_crash_restart_probe,
    run_graceful_probe,
    summarize_npm_lock,
    validate_run_root,
    write_receipt,
)
from code_assist_v1.tools.notion_parity.crdt_runtime_spike_executor import (
    _child_environment,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
APPROVAL_RECEIPT = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "checkpoints"
    / "ui-internal-collaboration-ap-runtime-spike-approval-receipt-20260824.json"
)
ADAPTER_ROOT = PACKAGE_ROOT / "tools" / "notion_parity" / "spike_adapters"
CHECKPOINT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "checkpoints"


def _new_root(tmp_path: Path, suffix: str = "0" * 32) -> Path:
    root = tmp_path / f"code-assist-e0-p2-20260824-{suffix}"
    root.mkdir()
    (root / "receipts").mkdir()
    return root


def _mark(root: Path, tmp_path: Path, run_id: str = "E0P2-20260824-999") -> None:
    initialize_owned_root(
        root,
        approval_receipt_path=APPROVAL_RECEIPT,
        run_id=run_id,
        created_at="2026-08-23T17:00:00Z",
        temp_base=tmp_path,
    )


@pytest.mark.unit
def test_e0_p2_root_requires_exact_new_temp_child_shape(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    assert validate_run_root(root, temp_base=tmp_path) == root.resolve()

    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(SpikeExecutionError, match="direct OS-temp child"):
        validate_run_root(nested, temp_base=tmp_path)

    bad = tmp_path / "other-task"
    bad.mkdir()
    with pytest.raises(SpikeExecutionError, match="name"):
        validate_run_root(bad, temp_base=tmp_path)


@pytest.mark.unit
def test_e0_p2_marker_binds_explicit_approval_and_rejects_tamper(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    _mark(root, tmp_path)
    marker = load_owned_marker(root)
    assert marker["run_id"] == "E0P2-20260824-999"
    assert marker["scope_sha256"] == "sha256:15d04969e31156df32f1ebd801ecb3a4ab6cec1f0df9748a74d3850f82293c4d"

    marker_path = root / MARKER_NAME
    marker["root_name"] = "other-root"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(SpikeExecutionError, match="binding"):
        load_owned_marker(root)


@pytest.mark.unit
def test_e0_p2_adapter_copy_cannot_escape_owned_root(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    _mark(root, tmp_path)
    source = ADAPTER_ROOT / "py_native_pycrdt_adapter.py"
    inside = root / "inside.py"
    digest = copy_adapter(source, inside, root=root, temp_base=tmp_path)
    assert digest.startswith("sha256:")
    assert inside.read_bytes() == source.read_bytes()

    with pytest.raises(SpikeExecutionError, match="escapes"):
        copy_adapter(source, tmp_path / "outside.py", root=root, temp_base=tmp_path)
    with pytest.raises(SpikeExecutionError, match="new file"):
        copy_adapter(source, inside, root=root, temp_base=tmp_path)


@pytest.mark.unit
def test_e0_p2_npm_lock_requires_v3_exact_direct_and_every_registry_integrity(
    tmp_path: Path,
) -> None:
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"yjs": "file:../../../downloads/npm/yjs-13.6.32.tgz"}},
            "node_modules/yjs": {"resolved": "file:../../../downloads/npm/yjs-13.6.32.tgz"},
            "node_modules/lib0": {
                "resolved": "https://registry.npmjs.org/lib0/-/lib0-0.2.117.tgz",
                "integrity": "sha512-abc",
            },
        },
    }
    path = tmp_path / "package-lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    report = summarize_npm_lock(
        path,
        expected_direct={"yjs": "file:../../../downloads/npm/yjs-13.6.32.tgz"},
    )
    assert report["registry_entry_count"] == 1
    assert report["missing_registry_integrity_count"] == 0

    lock["packages"]["node_modules/lib0"].pop("integrity")
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(SpikeExecutionError, match="integrity missing"):
        summarize_npm_lock(
            path,
            expected_direct={"yjs": "file:../../../downloads/npm/yjs-13.6.32.tgz"},
        )


def _dummy_adapter(path: Path) -> None:
    path.write_text(
        """
import json
from pathlib import Path
import sys

state = Path(sys.argv[1])
text = state.read_text(encoding="utf-8") if state.exists() else ""

def emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)

emit({"adapter_id":"DUMMY","event":"ready","network_listener":False,"port_bound":False,"text":text})
for line in sys.stdin:
    command = json.loads(line)
    if command["op"] == "insert":
        text = text[:command["index"]] + command["text"] + text[command["index"]:]
        state.write_text(text, encoding="utf-8")
        emit({"adapter_id":"DUMMY","event":"inserted","text":text})
    elif command["op"] == "state":
        emit({"adapter_id":"DUMMY","event":"state","text":text})
    elif command["op"] == "shutdown":
        state.write_text(text, encoding="utf-8")
        emit({"adapter_id":"DUMMY","event":"stopped","text":text})
        raise SystemExit(0)
""".lstrip(),
        encoding="utf-8",
    )


@pytest.mark.unit
def test_e0_p2_graceful_lifecycle_is_direct_argv_no_listener(tmp_path: Path) -> None:
    adapter = tmp_path / "dummy.py"
    state = tmp_path / "state.txt"
    _dummy_adapter(adapter)
    result = run_graceful_probe(
        [sys.executable, "-E", "-X", "utf8", str(adapter), str(state)],
        cwd=tmp_path,
        commands=[
            {"op": "insert", "index": 0, "text": "한글"},
            {"op": "state"},
            {"op": "shutdown"},
        ],
        expected_adapter_id="DUMMY",
    )
    assert result["returncode"] == 0
    assert result["records"][0]["network_listener"] is False
    assert result["records"][-1] == {
        "adapter_id": "DUMMY",
        "event": "stopped",
        "text": "한글",
    }


@pytest.mark.unit
def test_e0_p2_forced_crash_restarts_and_recovers_acknowledged_state(tmp_path: Path) -> None:
    adapter = tmp_path / "dummy.py"
    state = tmp_path / "state.txt"
    _dummy_adapter(adapter)
    result = run_crash_restart_probe(
        [sys.executable, "-E", "-X", "utf8", str(adapter), str(state)],
        cwd=tmp_path,
        mutation={"op": "insert", "index": 0, "text": "한글 조합"},
        restart_commands=[{"op": "state"}, {"op": "shutdown"}],
        expected_adapter_id="DUMMY",
        expected_recovered_text="한글 조합",
    )
    assert result["forced_crash_returncode"] != 0
    assert result["state_recovered"] is True
    assert result["process_cleanup_passed"] is True


@pytest.mark.unit
def test_e0_p2_receipt_is_new_exact_owned_file(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    _mark(root, tmp_path)
    path, digest = write_receipt(
        root,
        "lifecycle.json",
        {"status": "pass"},
        temp_base=tmp_path,
    )
    assert path.parent == root / "receipts"
    assert digest.startswith("sha256:")
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["run_id"] == "E0P2-20260824-999"
    with pytest.raises(SpikeExecutionError, match="already exists"):
        write_receipt(
            root,
            "lifecycle.json",
            {"status": "pass"},
            temp_base=tmp_path,
        )


@pytest.mark.unit
def test_e0_p2_cleanup_requires_matching_marker_and_exact_root(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    _mark(root, tmp_path)
    with pytest.raises(SpikeExecutionError, match="run id"):
        cleanup_owned_run_root(
            root,
            expected_run_id="E0P2-20260824-998",
            temp_base=tmp_path,
        )
    assert root.exists()
    cleanup_owned_run_root(
        root,
        expected_run_id="E0P2-20260824-999",
        temp_base=tmp_path,
    )
    assert not root.exists()


@pytest.mark.unit
def test_e0_p2_checked_in_adapters_have_no_network_start_surface() -> None:
    native = (ADAPTER_ROOT / "py_native_pycrdt_adapter.py").read_text(encoding="utf-8")
    opaque = (ADAPTER_ROOT / "py_opaque_relay_adapter.py").read_text(encoding="utf-8")
    yjs = (ADAPTER_ROOT / "yjs_adapter.cjs").read_text(encoding="utf-8")
    hocuspocus = (ADAPTER_ROOT / "node_hocuspocus_adapter.cjs").read_text(encoding="utf-8")

    for source in (native, opaque):
        assert "socket." not in source
        assert ".run(" not in source
    for source in (yjs, hocuspocus):
        assert ".listen(" not in source
        assert "createServer(" not in source
    assert "server_listen_called: false" in hocuspocus


@pytest.mark.unit
def test_e0_p2_child_environment_preserves_windows_os_bootstrap_case_insensitively() -> None:
    environment = _child_environment()
    for key in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH"):
        assert environment[key] == os.environ[key]
    assert "CODEX_SESSION_ID" not in environment
    assert "APPDATA" not in environment
    assert environment["PYTHONUTF8"] == "1"


@pytest.mark.unit
def test_e0_p2_preserved_runtime_receipts_prove_exact_denominators_and_no_live_path() -> None:
    receipts = {
        "artifact": (
            "ui-internal-collaboration-e0-p2-artifact-and-lock-receipt-20260824.json",
            "sha256:fb1c59bcf024ccdb766ce3d11477df2ca130d76c35dfa60e3daa7e5c8b8403eb",
        ),
        "lifecycle": (
            "ui-internal-collaboration-e0-p2-windows-lifecycle-receipt-20260824.json",
            "sha256:04a5461db08ed13f7b98558796c15b573d9c963f481da320fe98d37501191605",
        ),
        "repro": (
            "ui-internal-collaboration-e0-p2-install-reproducibility-receipt-20260824.json",
            "sha256:d413aa5c51ceba815c987695336735dda6f0436591dd3780d1f702d354506183",
        ),
    }
    values: dict[str, dict[str, object]] = {}
    for key, (name, expected_hash) in receipts.items():
        path = CHECKPOINT_ROOT / name
        assert file_sha256(path) == expected_hash
        raw = path.read_text(encoding="utf-8")
        assert "C:\\\\Users" not in raw
        assert "G:\\\\AI_Agent" not in raw
        value = json.loads(raw)
        assert value["contract"] == "code_assist.notion.crdt_runtime_spike_execution_receipt.v1"
        assert value["run_id"] == "E0P2-20260824-001"
        values[key] = value["payload"]

    artifact = values["artifact"]
    assert artifact["direct_artifacts"]["direct_artifact_count"] == 6
    assert artifact["direct_artifacts"]["integrity_passed_count"] == 6
    assert artifact["python_wheel_lock"]["artifact_count"] == 12
    assert sum(item["missing_registry_integrity_count"] for item in artifact["npm_locks"]) == 0

    lifecycle = values["lifecycle"]
    assert lifecycle["component_count"] == 4
    assert lifecycle["graceful_passed"] == 4
    assert lifecycle["forced_crash_restart_passed"] == 4
    assert lifecycle["parallel_execution_passed"] is True
    assert all(item["crash_restart"]["state_recovered"] for item in lifecycle["components"])
    assert all(item["crash_restart"]["process_cleanup_passed"] for item in lifecycle["components"])

    repro = values["repro"]
    assert repro["clean_reinstall_count"] == 4
    assert repro["adapter_start_stop_passed"] == 4
    assert all(item["equal"] for item in repro["npm_lock_byte_identity"])
    assert all(item["returncode"] == 0 for item in repro["dependency_checks"])
