"""Create a deterministic code-only 10521 candidate, excluding runtime data.

The deployable allowlist is shared with the full service recovery bundle.
This archive is not a deploy authorization or a substitute for an offline
code+data backup and independent restore.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import zipfile

from code_assist_v1.tools.service_recovery_bundle import code_inventory


_PREFIX = "code_assist_v1/"
_HANDOFF_EXCLUDED_DEVELOPMENT_SCRIPTS = frozenset({
    # These are browser-capture/receipt harnesses, not service entrypoints.
    # In particular, v0b_p1_capture.py embeds captured image data; keep the
    # whole direct-import chain out of a company handoff candidate.
    "notion_collaboration/v0b_p1_capture.py",
    "notion_collaboration/c0_p2_browser.py",
    "notion_collaboration/v0b_p1_iam_browser.py",
    "notion_collaboration/v0b_p1_product_capture.py",
    "notion_collaboration/v0b_p1_browser.py",
    "notion_collaboration/approved_dev_pair_browser.py",
    "notion_collaboration/e1_p2_browser.py",
    "notion_collaboration/e1_p3_manual_ime.py",
    "notion_collaboration/f0_p5_browser.py",
    "tools/run_v0b_p1_i0_semantic_diagnostic_20260830.py",
    "tools/verify_internal_collaborative_native_ime_receipt.py",
    # Local synthetic DRM generators include fixed fixture passwords. They
    # are useful in the development tree, not in a company handoff archive.
    "tools/create_protected_image_only_pdf_fixture.py",
    "tools/create_protected_pdf_fixture.py",
    "tools/create_protected_text_pdf_fixture.py",
    "tools/create_protected_vector_pdf_fixture.py",
    # These one-off audits and the 10421-only server are tied to local D:
    # scratch locations. The legacy runtime module itself is still needed.
    "tools/audit_actual_blind_docx_wiki_10521.py",
    "tools/audit_actual_pptx_wiki_10521.py",
    "tools/serve_preservation_preview.py",
})


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def create_candidate(*, output: Path, source: Path | None = None,
                     profile: str = "handoff") -> dict[str, object]:
    if profile not in {"full", "handoff"}:
        raise ValueError("Unknown code candidate profile.")
    source = Path(__file__).resolve().parents[1] if source is None else Path(source)
    source = source.resolve(strict=True)
    output = Path(output).absolute()
    if (output.suffix.lower() != ".zip" or not output.parent.is_dir()
            or output.exists() or output.with_suffix(".manifest.json").exists()
            or output.resolve().is_relative_to(source)):
        raise ValueError("Use a new code-only ZIP path outside the source tree.")
    before = code_inventory(source)
    excluded = (
        sorted(_HANDOFF_EXCLUDED_DEVELOPMENT_SCRIPTS & set(before["files"]))
        if profile == "handoff" else []
    )
    files = {relative: fact for relative, fact in before["files"].items()
             if relative not in excluded}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for relative in sorted(files):
            raw = (source / relative).read_bytes()
            fact = files[relative]
            if len(raw) != fact["size_bytes"] or sha256(raw).hexdigest() != fact["sha256"]:
                raise ValueError("A source file changed during packaging.")
            info = zipfile.ZipInfo(_PREFIX + relative,
                                   date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)
    if code_inventory(source) != before:
        raise ValueError("The code inventory changed during packaging.")
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        if names != [_PREFIX + relative for relative in sorted(files)]:
            raise ValueError("The archive entry inventory differs.")
        for relative, fact in files.items():
            raw = archive.read(_PREFIX + relative)
            if len(raw) != fact["size_bytes"] or sha256(raw).hexdigest() != fact["sha256"]:
                raise ValueError("An archived source file differs.")
    digest = sha256(output.read_bytes()).hexdigest()
    result: dict[str, object] = {
        "schema_version": 1,
        "kind": "second-brain-10521-code-only-candidate",
        # The handoff manifest travels with the ZIP. Its content identity is
        # the inventory digest, not a workstation-specific absolute path.
        "source_code_root": "code_assist_v1" if profile == "handoff" else str(source),
        "archive": output.name if profile == "handoff" else str(output),
        "archive_sha256": digest,
        "file_count": len(files),
        "code_inventory_sha256": sha256(_json(before)).hexdigest(),
        "archive_inventory_sha256": sha256(_json(files)).hexdigest(),
        "profile": profile,
        "excluded_development_scripts": excluded,
        "runtime_data_included": False,
        "private_content_reviewed": False,
        "deployment_approved": False,
    }
    with output.with_suffix(".manifest.json").open("xb") as handle:
        handle.write(_json(result))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", choices=("handoff", "full"), default="handoff")
    args = parser.parse_args(argv)
    print(_json(create_candidate(output=args.output, profile=args.profile)).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
