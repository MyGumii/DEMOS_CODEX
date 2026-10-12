"""Build one long, wholly fictional AMHS PDF for isolated 10521 fidelity tests.

The document is not company operating guidance and contains no company data.
Numerical claims and decisions are generated from the same case table so the
source has a deterministic truth set for later upload/Wiki comparison.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas


CANARY = "RICH-PDF-CANARY-10521"
CASES = (
    ("C17", "F09", "S42", "lane B", 13.5, 3.9, 4, 90, "hold", "bearing vibration exceeded its limit"),
    ("C18", "F12", "S43", "lane C", 13.6, 3.4, 3, 120, "release", "all three measurements stayed within limits"),
    ("C19", "F04", "S44", "lane A", 15.1, 3.7, 4, 90, "hold", "motor current exceeded its limit"),
    ("C20", "F11", "S45", "lane D", 13.9, 3.2, 6, 120, "hold", "the telemetry gap exceeded five seconds"),
    ("C21", "F03", "S46", "lane B", 13.8, 3.0, 2, 90, "release", "all three measurements stayed within limits"),
    ("C22", "F07", "S47", "lane C", 14.8, 4.1, 2, 90, "hold", "motor current and vibration exceeded their limits"),
    ("C23", "F10", "S48", "lane A", 12.7, 3.6, 5, 120, "hold", "a five-second telemetry gap is not under five seconds"),
    ("C24", "F06", "S49", "lane D", 13.4, 3.5, 1, 90, "release", "all three measurements stayed within limits"),
    ("C25", "F02", "S50", "lane B", 14.4, 3.1, 3, 120, "hold", "motor current exceeded its limit"),
    ("C26", "F08", "S51", "lane C", 13.2, 3.9, 3, 90, "hold", "bearing vibration exceeded its limit"),
    ("C27", "F05", "S52", "lane A", 12.9, 2.8, 2, 120, "release", "all three measurements stayed within limits"),
    ("C28", "F13", "S53", "lane D", 13.7, 3.3, 4, 90, "release", "all three measurements stayed within limits"),
)


def _paragraph(page: canvas.Canvas, text: str, x: float, y: float, *, bold: bool = False) -> float:
    font = "Helvetica-Bold" if bold else "Helvetica"
    size = 10.2 if bold else 9.6
    page.setFont(font, size)
    page.setFillColor(colors.HexColor("#24313e"))
    for line in simpleSplit(text, font, size, A4[0] - 104):
        page.drawString(x, y, line)
        y -= 14.2
    return y - (9 if bold else 7)


def _header(page: canvas.Canvas, number: int) -> float:
    page.setFillColor(colors.HexColor("#193348"))
    page.setFont("Helvetica-Bold", 10)
    page.drawString(52, A4[1] - 47, "AMHS recovery evidence procedure")
    page.setStrokeColor(colors.HexColor("#b5c3cc"))
    page.line(52, A4[1] - 56, A4[0] - 52, A4[1] - 56)
    page.setFont("Helvetica", 8)
    page.drawString(52, 38, f"{CANARY} | synthetic source | page {number}")
    return A4[1] - 83


def _render() -> bytes:
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    page.setTitle("Synthetic AMHS recovery evidence procedure 10521")
    number = 1
    y = _header(page, number)

    def paragraph(text: str, *, bold: bool = False) -> None:
        nonlocal y, number
        font = "Helvetica-Bold" if bold else "Helvetica"
        size = 10.2 if bold else 9.6
        lines = simpleSplit(text, font, size, A4[0] - 104)
        required = len(lines) * 14.2 + (9 if bold else 7)
        if y - required < 65:
            page.showPage()
            number += 1
            y = _header(page, number)
        y = _paragraph(page, text, 52, y, bold=bold)

    paragraph("Synthetic AMHS recovery evidence procedure", bold=True)
    paragraph("Purpose and boundary", bold=True)
    paragraph(
        "This source exists only to test whether Second Brain preserves linked facts from a long "
        "uploaded PDF. It is fictional and must not be used to operate equipment. A controller "
        "fault never authorizes immediate conveyor restart. The responsible operator first "
        "isolates the named lane, compares its state with a verified snapshot, and checks a "
        "continuous telemetry window before recording a release decision."
    )
    paragraph("Decision rule and evidence hierarchy", bold=True)
    paragraph(
        "A release requires motor current strictly below 14.2 A, bearing vibration strictly "
        "below 3.8 mm/s, and every telemetry gap strictly below 5 s throughout the stated "
        "observation window. Equality does not pass. The snapshot identifier proves which "
        "reference state was compared; it does not prove the machinery is safe. A release "
        "decision also requires the named lane to remain isolated until an authorized "
        "operator records the measurements. The later knowledge summary must keep these "
        "conditions separate from its explanation and must not turn a hold into a release."
    )
    paragraph(
        "Evidence priority is: the signed incident record, the verified snapshot, the "
        "time-aligned telemetry series, and finally the operator's explanatory note. If a "
        "note conflicts with a measured value, the measurement controls the hold decision "
        "until the conflict is resolved. Missing samples are not treated as zero. A broken "
        "timebase makes the observation window invalid even when the displayed current and "
        "vibration values look acceptable. This distinction is intentionally present for "
        "citation and uncertainty tests."
    )
    paragraph("Case register", bold=True)
    for index, (controller, fault, snapshot, lane, amps, vibration, gap, window, decision, reason) in enumerate(CASES, 1):
        # Keep the heading, measurements, and decision for one case together.
        if y < 290:
            page.showPage()
            number += 1
            y = _header(page, number)
        paragraph(f"Case {index:02d}: controller {controller}, fault {fault}, verified snapshot {snapshot}", bold=True)
        paragraph(
            f"At detection, controller {controller} stopped routing into {lane}. The operator isolated "
            f"{lane} and selected snapshot {snapshot} only after its timestamp and checksum were "
            "checked against the incident record. The comparison was limited to this lane; "
            "adjacent lanes did not inherit its release decision."
        )
        paragraph(
            f"The aligned trace for {controller} measured motor current {amps:.1f} A, bearing "
            f"vibration {vibration:.1f} mm/s, and largest telemetry gap {gap} s during a "
            f"{window} s observation window. The current, vibration, and gap must each be "
            "evaluated against their own strict limit. An apparently normal snapshot is not "
            "a substitute for a complete telemetry window."
        )
        paragraph(
            f"Recorded outcome: {decision.upper()} for {lane}, because {reason}. "
            f"The operator retained the {controller}/{fault}/{snapshot} trace bundle with the "
            "decision. If the trace is later corrected, the original decision remains in the "
            "audit log and a new decision must cite the corrected trace. A knowledge page "
            "should report both the observed values and the condition that determined the "
            "outcome, rather than collapsing them into a generic recovery statement."
        )
    paragraph("Cross-case interpretation", bold=True)
    paragraph(
        "Cases 02, 05, 08, 11, and 12 meet every strict threshold and were released after "
        "their full observation windows. Case 07 has a gap of exactly 5 s and remains held: "
        "the rule is under 5 s, not at most 5 s. Cases 01 and 10 illustrate vibration-only "
        "holds; cases 03 and 09 illustrate current-only holds. Case 06 has two independent "
        "limit breaches. Case 04 is held because its telemetry gap is too long even though "
        "the other two values are within limits. These contrasts are deliberate holdout facts "
        "for the Wiki writer and later question-answering checks."
    )
    paragraph(
        "A user asking why a specific case was held needs its actual values and strict "
        "threshold, not just the section title. A user comparing two cases needs the "
        "distinguishing condition and should not infer a causal link between unrelated lanes. "
        "The case register is synthetic, but the facts inside this PDF are the source of truth "
        "for this isolated software test."
    )
    page.showPage()
    page.save()
    return buffer.getvalue()


def create(output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    raw = _render()
    reader = PdfReader(BytesIO(raw))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    expected = (CANARY, "Case 01", "Case 12", "strictly below 14.2 A",
        "exactly 5 s", "Cross-case interpretation")
    if len(reader.pages) < 4 or len(text.encode("utf-8")) < 10_000 or any(token not in text for token in expected):
        raise RuntimeError("The rich PDF lacks its expected pages or source facts.")
    with output.open("xb") as target:
        target.write(raw)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(create(args.output))


if __name__ == "__main__":
    main()
