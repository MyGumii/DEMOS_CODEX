"""One-shot actual Codex writer audit for a fictional 10521 PPTX source.

This does not upload or publish. Writer requests contain only the source and
policy; independent evaluation criteria are applied after the Wiki is saved.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import uuid4


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _ROOT / "output/pptx/synthetic-food-allergen-rich-10521.pptx"
_SOURCE_SHA256 = "9169a47108a740c26a4f5623d13d10f5102d885511c6c9e37941ba4e7cff7ced"
_AUDIT_PARENT = Path("D:/CodexTestTemp/SecondBrain10521PptxActual")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    if _digest(_SOURCE) != _SOURCE_SHA256:
        raise ValueError("The fictional PowerPoint source changed.")
    # compose() must run before importing the app's model-routing modules.
    from code_assist_v1.tools.serve_clean_10521 import compose

    compose()
    from code_assist_v1.internal_llm import run_internal_text
    from code_assist_v1.knowledge_core import pptx_role_authoring as role
    from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy
    from code_assist_v1.tests.knowledge_spaces.unit.test_pptx_role_authoring import _source

    source = _source(_SOURCE.read_bytes(), _SOURCE.name)
    plan = role.PptxRoleAuthoringPlan(
        source,
        model_policy=AuthoringModelPolicy(
            provider_order=("codex",), model_ids=("codex-local",), max_attempts=1,
        ),
    )
    _AUDIT_PARENT.mkdir(parents=True, exist_ok=True)
    folder = _AUDIT_PARENT / str(uuid4())
    folder.mkdir(exist_ok=False)
    report: dict[str, object] = {
        "kind": "synthetic_10521_actual_pptx_writer_audit",
        "completed": False,
        "source": str(_SOURCE),
        "source_sha256": _SOURCE_SHA256,
        "plan_sha256": plan.plan_sha256,
        "policy_version": plan.policy_version,
        "batches": len(plan.batches),
        "calls": [],
        "company_api_calls": 0,
        "http_publication_executed": False,
        "semantic_completeness_proven": False,
    }
    _save_json(folder / "report.json", report)
    print("AUDIT_FOLDER=" + str(folder), flush=True)
    began = time.monotonic()

    def guard() -> None:
        if _digest(_SOURCE) != _SOURCE_SHA256:
            raise RuntimeError("The fictional PowerPoint source changed during authoring.")

    def observe(task, payload, **kwargs):
        guard()
        if task != role.ROLE_TASK or kwargs.get("max_attempts") != 1:
            raise RuntimeError("The actual writer call changed its task or attempt policy.")
        calls = report["calls"]
        assert isinstance(calls, list)
        ordinal = len(calls) + 1
        call: dict[str, object] = {
            "ordinal": ordinal, "batch_id": payload["batch_id"], "completed": False,
        }
        calls.append(call)
        _save_json(folder / f"call-{ordinal:02d}-input.json", {
            "task": task, "payload": payload, "messages": kwargs.get("messages"),
        })
        _save_json(folder / "report.json", report)
        print(f"WRITER_CALL_STARTED={ordinal}", flush=True)
        started = time.monotonic()
        try:
            answer, metadata = run_internal_text(task, payload, **kwargs)
            _save_json(folder / f"call-{ordinal:02d}-output.json", {
                "text": answer, "metadata": metadata,
            })
            call.update({
                "completed": isinstance(answer, str),
                "response_sha256": sha256(answer.encode()).hexdigest() if isinstance(answer, str) else None,
                "model_kind": metadata.get("model_kind") if isinstance(metadata, dict) else None,
                "model_id": metadata.get("model_id") if isinstance(metadata, dict) else None,
            })
            return answer, metadata
        except BaseException as exc:
            call["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            call["seconds"] = round(time.monotonic() - started, 3)
            _save_json(folder / "report.json", report)
            print(f"WRITER_CALL_FINISHED={ordinal}", flush=True)

    receipts = []
    try:
        author = role.PptxRoleAuthor(runner=observe)
        for batch in plan.batches:
            receipt = author.author_batch(plan, batch.ordinal, attempt_guard=guard)
            receipts.append(receipt)
            _save_json(folder / f"batch-{batch.ordinal:02d}-validated.json", asdict(receipt))
        result = role.assemble_pptx_role_wiki(plan, receipts)
        (folder / "wiki.md").write_text(result.markdown, encoding="utf-8", newline="\n")
        (folder / "manifest.json").write_text(result.manifest_json, encoding="utf-8", newline="\n")
        report.update({
            "completed": True,
            "wiki_sha256": sha256(result.markdown.encode()).hexdigest(),
            "wiki_bytes": len(result.markdown.encode()),
            "all_batches_validated": len(receipts) == len(plan.batches),
            "literal_markers": {
                marker: marker in result.markdown
                for marker in (
                    "RICH-PPTX-CANARY-10521", "J01", "J02", "J03", "J04", "J05", "J06",
                    "L-D09", "supplier lot unapproved", "retroactive", "AMHS",
                )
            },
        })
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - began, 3)
        report["source_unchanged"] = _digest(_SOURCE) == _SOURCE_SHA256
        _save_json(folder / "report.json", report)
        print("AUDIT_COMPLETED=" + str(report["completed"]), flush=True)


if __name__ == "__main__":
    main()
