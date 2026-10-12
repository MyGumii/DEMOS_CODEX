"""Evidence writer tests; no provider, PDF creation, upload or live data."""
import importlib.util
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest


pytestmark = pytest.mark.unit
_PATH = Path(__file__).resolve().parents[4] / "workspace-feature-evidence/long-document-optimization-20260908/authoring_probe.py"
_SPEC = importlib.util.spec_from_file_location("authoring_probe_artifacts", _PATH)
_PROBE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROBE)


@pytest.mark.parametrize("text", ["한글\n다음\n", "한글\r\n다음\r\n", "🙂\r\n\n마지막"])
def test_generated_probe_bytes_are_exact_without_newline_translation(tmp_path, text):
    path = tmp_path / "evidence.txt"
    _PROBE.write_exact(path, text)
    assert path.read_bytes() == text.encode("utf-8")
    assert _PROBE.read_exact(path) == text
    _PROBE.frozen_text(path, text)


def test_resume_cannot_hide_crlf_drift_or_overwrite_existing_evidence(tmp_path):
    path = tmp_path / "translated.txt"
    _PROBE.write_exact(path, "한글\r\n")
    with pytest.raises(ValueError, match="never rewrite"):
        _PROBE.frozen_text(path, "한글\n")
    assert path.read_bytes() == "한글\r\n".encode()


def test_json_report_is_utf8_without_platform_newline_conversion(tmp_path):
    path = tmp_path / "report.json"
    value = {"body": "원문\r\n", "count": 2}
    _PROBE.save(path, value)
    assert path.read_bytes() == json.dumps(value, ensure_ascii=False, indent=2).encode()


def _kwargs():
    return {"messages": [{"role": "user", "content": "원문\n"}], "provider_order": ["codex"],
        "model_ids": ["fixture"], "max_tokens": 128, "max_attempts": 2, "request_budget_bytes": 4096,
        "temperature": 0.1, "timeout_s": 300, "api_timeout_s": 300, "output_format": "json",
        "validate_output": lambda value: "" if value == "검증 결과\n" else "invalid"}


def test_recorded_provider_replay_verifies_raw_bytes_without_calling_again(tmp_path):
    calls = []
    def run(task, payload, **kwargs):
        calls.append(task)
        assert kwargs["validate_output"]("검증 결과\n") == ""
        return "검증 결과\n", {"model_kind": "codex", "model_id": "fixture", "secret": "not-recorded"}
    prompt = lambda messages: "원문\n"
    for _ in range(2):
        response, _ = _PROBE.recorded_run(tmp_path, "batch", run, prompt, "test", {}, _kwargs())
        assert response == "검증 결과\n"
    assert calls == ["test"]
    assert "not-recorded" not in _PROBE.read_exact(tmp_path / "batch-provider-receipt.json")
    _PROBE.write_exact(tmp_path / "batch-provider-output.txt", "검증 결과\r\n")
    with pytest.raises(ValueError, match="frozen call"):
        _PROBE.recorded_run(tmp_path, "batch", run, prompt, "test", {}, _kwargs())
    assert calls == ["test"]


def test_failed_reserved_provider_call_is_not_restarted_by_resume(tmp_path):
    calls = []
    def run(task, payload, **kwargs):
        calls.append(task)
        return None, {"failure_codes": ["timeout"]}
    prompt = lambda messages: "원문\n"
    with pytest.raises(ValueError, match="complete validated"):
        _PROBE.recorded_run(tmp_path, "batch", run, prompt, "test", {}, _kwargs())
    with pytest.raises(ValueError, match="do not restart"):
        _PROBE.recorded_run(tmp_path, "batch", run, prompt, "test", {}, _kwargs())
    assert calls == ["test"]


def test_truncated_bridge_prompt_is_rejected_before_provider_budget_is_reserved(tmp_path):
    with pytest.raises(ValueError, match="truncate"):
        _PROBE.recorded_run(tmp_path, "batch", lambda *a, **kw: pytest.fail("no call"),
            lambda messages: "[... context truncated for Codex CLI prompt budget ...]", "test", {}, _kwargs())
    assert not (tmp_path / "batch-provider-reservation.json").exists()


def _reusable_fixture(folder):
    from code_assist_v1.knowledge_core.wiki_authoring import (
        AuthoringModelPolicy, ExtractionSource, WikiChunkAuthor, plan_authoring, authoring_request_descriptor,
    )
    text = "# 원본\r\n\r\n서로 다른 조건을 동일하다고 간주하지 않는다.\r\n"
    source = ExtractionSource(str(uuid4()), sha256(text.encode()).hexdigest(), text)
    plan = plan_authoring(source, model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
    chunk = plan.chunks[0]
    plan_record = {"source_snapshot_id": source.snapshot_id, "source_sha256": source.content_sha256,
        "raw_sha256": sha256(b"synthetic-source").hexdigest(), "source_bytes": len(text.encode()),
        "model_policy": asdict(plan.model_policy), "work_policy": asdict(plan.work_policy),
        "chunks": [{"ordinal": row.ordinal, "chunk_id": row.chunk_id, "range": [row.start, row.end],
                    "source_sha256": row.content_sha256} for row in plan.chunks]}
    stem = "chunk-001"
    descriptor = authoring_request_descriptor(plan, chunk)
    prompt = lambda messages: json.dumps(messages, ensure_ascii=False)
    _PROBE.write_exact(folder / "extraction.md", text)
    _PROBE.save(folder / "plan.json", plan_record)
    _PROBE.save(folder / (stem + "-input.json"), descriptor)
    _PROBE.write_exact(folder / (stem + "-bridge-input.txt"), prompt(descriptor["messages"]))
    calls = []
    def run(task, payload, **kwargs):
        calls.append(task)
        response = json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
            "sections": [{"heading": "검증용", "claims": [{"text": "조건별로 구분한다.", "quotes": [payload["source"]]}]}],
            "warnings": []}, ensure_ascii=False)
        assert kwargs["validate_output"](response) == ""
        return response, {"model_kind": "codex", "model_id": "fixture"}
    def runner(task, payload, **kwargs):
        return _PROBE.recorded_run(folder, stem, run, prompt, task, payload, kwargs)
    result = WikiChunkAuthor(runner=runner).author(plan, 1)
    _PROBE.save(folder / (stem + "-receipt.json"), {"request_sha256": result.request_sha256,
        "response_sha256": sha256(result.response.encode()).hexdigest(),
        "bridge_prompt_sha256": sha256(prompt(descriptor["messages"]).encode()).hexdigest(), "model": dict(result.model)})
    return plan_record, plan, chunk, prompt, calls, result


def test_map_reuse_is_read_only_current_validated_and_makes_no_new_provider_call(tmp_path):
    plan_record, plan, chunk, prompt, calls, original = _reusable_fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    for _ in range(2):
        result, origin = _PROBE.reuse_map_chunk(tmp_path, plan_record, plan, chunk, prompt)
        assert result == original
        assert origin["source_snapshot_id"] == plan.source.snapshot_id
        assert origin["response_sha256"] == sha256(result.response.encode()).hexdigest()
    assert len(calls) == 1
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("target", ["extraction", "plan", "descriptor", "provider-input", "response", "receipt", "missing"])
def test_map_reuse_rejects_source_request_response_or_receipt_drift_without_writing(tmp_path, target):
    from code_assist_v1.knowledge_core.wiki_authoring import AuthoringError
    plan_record, plan, chunk, prompt, calls, _ = _reusable_fixture(tmp_path)
    names = {"extraction": "extraction.md", "plan": "plan.json", "descriptor": "chunk-001-input.json",
        "provider-input": "chunk-001-provider-input.json", "response": "chunk-001-provider-output.txt",
        "receipt": "chunk-001-receipt.json", "missing": "chunk-001-provider-receipt.json"}
    path = tmp_path / names[target]
    if target == "missing":
        path.unlink()
    elif target == "receipt":
        receipt = json.loads(_PROBE.read_exact(path))
        receipt["response_sha256"] = "0" * 64
        _PROBE.save(path, receipt)
    else:
        _PROBE.write_exact(path, _PROBE.read_exact(path) + "\r\n")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises((AuthoringError, ValueError, FileNotFoundError)):
        _PROBE.reuse_map_chunk(tmp_path, plan_record, plan, chunk, prompt)
    assert len(calls) == 1
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("mutation", ["absent", "duplicate", "failed", "bytes", "chunk-count", "incomplete", "reused", "boolean"])
def test_reuse_requires_document_membership_and_completion_in_hashed_origin(mutation):
    plan_record = {"source_bytes": 123, "chunks": [{"ordinal": 1}, {"ordinal": 2}]}
    row = {"filename": "sample.pdf", "status": "authored_ungraded", "source_bytes": 123,
           "chunk_count": 2, "completed_chunks": 2}
    report = {"documents": [row]}
    _PROBE.validate_reuse_document(report, "sample.pdf", plan_record)
    if mutation == "absent":
        report["documents"] = []
    elif mutation == "duplicate":
        report["documents"].append(dict(row))
    elif mutation == "failed":
        row["status"] = "authoring_failed"
    elif mutation == "bytes":
        row["source_bytes"] += 1
    elif mutation == "chunk-count":
        row["chunk_count"] += 1
    elif mutation == "incomplete":
        row["completed_chunks"] -= 1
    elif mutation == "reused":
        row["reused_chunks"] = 2
    else:
        row["source_bytes"] = True
    with pytest.raises(ValueError):
        _PROBE.validate_reuse_document(report, "sample.pdf", plan_record)
