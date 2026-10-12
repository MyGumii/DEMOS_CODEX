"""Actual writer-only audit against an instruction-free fictional Word source.

The author receives only canonical source tasks and the frozen 10521 prompt.
Evaluation remains outside the model request; this does not publish or chat.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "output/docx/conveyor-controller-return-review-10521.docx"
SOURCE_SHA256 = "a764264c7a469c5ffb8d76e67e4953a2a7554d319142a12964934050075d11e5"
AUDIT_PARENT = Path("D:/CodexTestTemp/SecondBrain10521BlindDocxActual")
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _save_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def _checkpoint(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    if _digest(SOURCE) != SOURCE_SHA256:
        raise ValueError("The fictional Word source changed.")
    # Register the isolated 10521 model route before importing the runner.
    from code_assist_v1.tools.serve_clean_10521 import compose

    compose()
    from code_assist_v1.codex_response import CodexResponsePolicy
    from code_assist_v1.document_source_tables import render_native_tables
    from code_assist_v1.internal_llm import run_internal_text
    from code_assist_v1.knowledge_core.native_wiki_authoring import NativeWikiPlan, NativeWikiSource
    from code_assist_v1.knowledge_core.native_wiki_batches import (
        NativeBatchPlan, NativeWikiBatchAuthor, assemble_native_batches,
    )
    from code_assist_v1.knowledge_core.services.native_rich_publication import CanonicalNativePublicationExtractor
    from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy

    native = CanonicalNativePublicationExtractor().extract_native_source(
        SOURCE.read_bytes(), media_type=MEDIA_TYPE, original_filename=SOURCE.name,
    )
    extracted = native.extraction
    if extracted.failure_code or not extracted.markdown or extracted.assets:
        raise RuntimeError("The blind source did not produce a text-and-table native extraction.")
    for forbidden in ("wiki", "test", "preserve", "model", "canary"):
        if forbidden in extracted.markdown.lower():
            raise RuntimeError("The source contains an authoring or audit hint.")
    structure_json = native.document.canonical_json()
    source = NativeWikiSource(
        str(uuid4()), sha256(extracted.markdown.encode()).hexdigest(),
        structure_json, sha256(structure_json.encode()).hexdigest(), native.asset_references,
    )
    plan = NativeBatchPlan(NativeWikiPlan(
        source, model_policy=AuthoringModelPolicy(
            provider_order=("codex",), model_ids=("codex-local",), max_attempts=1,
        ),
    ))
    AUDIT_PARENT.mkdir(parents=True, exist_ok=True)
    folder = AUDIT_PARENT / str(uuid4())
    folder.mkdir(exist_ok=False)
    (folder / "extracted.md").write_text(extracted.markdown, encoding="utf-8", newline="\n")
    (folder / "source-structure.json").write_text(structure_json, encoding="utf-8", newline="\n")
    report: dict[str, object] = {
        "kind": "actual_blind_docx_native_writer_audit",
        "source": str(SOURCE), "source_sha256": SOURCE_SHA256,
        "source_has_authoring_hints": False,
        "extracted_bytes": len(extracted.markdown.encode()),
        "slots": len(plan.source_plan.slots), "prose_chunks": len(plan.source_plan.chunks),
        "batches": len(plan.batches), "reservation_json": plan.reservation_json,
        "plan_sha256": plan.source_plan.plan_sha256,
        "company_api_calls": 0, "http_publication_executed": False,
        "chat_executed": False, "semantic_acceptance": "pending_independent_review",
        "calls": [], "completed": False,
    }
    _checkpoint(folder / "report.json", report)
    print("AUDIT_FOLDER=" + str(folder), flush=True)
    began = time.monotonic()

    def guard() -> None:
        if _digest(SOURCE) != SOURCE_SHA256:
            raise RuntimeError("The fictional Word source changed during authoring.")

    response_policy = CodexResponsePolicy(
        prompt_bytes=65_536, stdout_bytes=2_097_152, stderr_bytes=262_144,
        line_bytes=262_144, event_count=4_096, text_bytes=65_536,
    )

    def observe(task, payload, **kwargs):
        guard()
        if task != "canonical_native_wiki_batch_authoring" or kwargs.get("max_attempts") != 1:
            raise RuntimeError("The frozen writer task or attempt count changed.")
        calls = report["calls"]
        assert isinstance(calls, list)
        ordinal = len(calls) + 1
        call: dict[str, object] = {"ordinal": ordinal, "batch_id": payload["batch_id"], "completed": False}
        calls.append(call)
        _save_json(folder / f"call-{ordinal:02d}-input.json", {
            "task": task, "payload": payload, "messages": kwargs.get("messages"),
        })
        _checkpoint(folder / "report.json", report)
        print(f"WRITER_CALL_STARTED={ordinal}", flush=True)
        started = time.monotonic()
        try:
            response, metadata = run_internal_text(
                task, payload, **kwargs, codex_response_policy=response_policy,
            )
            _save_json(folder / f"call-{ordinal:02d}-output.json", {
                "text": response, "metadata": metadata,
            })
            call.update(
                completed=isinstance(response, str),
                response_sha256=sha256(response.encode()).hexdigest() if isinstance(response, str) else None,
                model_kind=metadata.get("model_kind") if isinstance(metadata, dict) else None,
                model_id=metadata.get("model_id") if isinstance(metadata, dict) else None,
            )
            return response, metadata
        except BaseException as exc:
            call["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            call["seconds"] = round(time.monotonic() - started, 3)
            _checkpoint(folder / "report.json", report)
            print(f"WRITER_CALL_FINISHED={ordinal}", flush=True)

    receipts = []
    try:
        author = NativeWikiBatchAuthor(runner=observe)
        for batch in plan.batches:
            receipt = author.author_batch(plan, batch.ordinal, attempt_guard=guard)
            receipts.append(receipt)
            _save_json(folder / f"batch-{batch.ordinal:02d}-validated.json", asdict(receipt))
        wiki = assemble_native_batches(plan, receipts)
        (folder / "wiki.md").write_text(wiki.markdown, encoding="utf-8", newline="\n")
        (folder / "manifest.json").write_text(wiki.manifest_json, encoding="utf-8", newline="\n")
        tables = dict(render_native_tables(native.document))
        table_counts = {table_id: wiki.markdown.count(markup) for table_id, markup in tables.items()}
        report.update(
            completed=True, wiki_sha256=sha256(wiki.markdown.encode()).hexdigest(),
            wiki_bytes=len(wiki.markdown.encode()),
            exact_native_table_count=len(tables), exact_table_occurrences=table_counts,
            all_exact_tables_once=all(count == 1 for count in table_counts.values()),
            source_unchanged=_digest(SOURCE) == SOURCE_SHA256,
        )
        if not report["all_exact_tables_once"]:
            raise AssertionError("The native Word tables were not retained once each.")
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - began, 3)
        report["source_unchanged"] = _digest(SOURCE) == SOURCE_SHA256
        _checkpoint(folder / "report.json", report)
        print("AUDIT_COMPLETED=" + str(report["completed"]), flush=True)


if __name__ == "__main__":
    main()
