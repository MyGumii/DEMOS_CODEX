"""Create a synthetic encrypted PDF for isolated extraction-path tests.

This is deliberately *not* a company DRM fixture.  The public test passwords
must never be reused for real documents or interpreted as an access bypass.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import argparse

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


OPEN_PASSWORD = "fixture-open-2026"
OWNER_PASSWORD = "fixture-owner-2026"


def _source_pdf() -> bytes:
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    page.setTitle("Synthetic AMHS recovery evidence - not enterprise DRM")

    def heading(text: str, y: float) -> None:
        page.setFillColor(colors.HexColor("#123047"))
        page.setFont("Helvetica-Bold", 16)
        page.drawString(52, y, text)

    def line(text: str, y: float, *, bold: bool = False, x: float = 54) -> None:
        page.setFillColor(colors.HexColor("#203442"))
        page.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        page.drawString(x, y, text)

    heading("AMHS C-17 recovery evidence", height - 62)
    line("SYNTHETIC TEST DOCUMENT - NOT COMPANY DRM", height - 83, bold=True)
    line("Incident: controller C-17 stopped conveyor CV-04 at 09:31.", height - 119)
    line("Required order: isolate C-17, verify snapshot S-42, restore, then validate telemetry.", height - 137)
    line("Snapshot S-42 was verified at 09:35 and is the only permitted restore point.", height - 155)

    heading("Release gates", height - 204)
    columns = (54, 232, 335, 448)
    headers = ("Gate", "Expected", "Observed", "Decision")
    for x, value in zip(columns, headers):
        line(value, height - 230, bold=True, x=x)
    rows = (
        ("Motor current", "below 8.0 A", "7.4 A", "pass"),
        ("Vibration", "below 2.0 mm/s", "1.6 mm/s", "pass"),
        ("Sensor link", "connected", "connected", "pass"),
    )
    for index, row in enumerate(rows):
        for x, value in zip(columns, row):
            line(value, height - 251 - index * 20, x=x)
    page.setStrokeColor(colors.HexColor("#B8C9D0"))
    page.line(52, height - 236, width - 52, height - 236)
    page.line(52, height - 303, width - 52, height - 303)

    heading("Causal path", height - 358)
    boxes = ((60, "Sensor"), (218, "PLC C-17"), (398, "CV-04"))
    for x, title in boxes:
        page.setFillColor(colors.HexColor("#E5F1F4"))
        page.roundRect(x, height - 410, 110, 36, 6, fill=1, stroke=0)
        page.setFillColor(colors.HexColor("#123047"))
        page.setFont("Helvetica-Bold", 10)
        page.drawCentredString(x + 55, height - 396, title)
    page.setStrokeColor(colors.HexColor("#247A93"))
    page.line(170, height - 392, 218, height - 392)
    page.line(328, height - 392, 398, height - 392)
    line("The sensor reports to C-17; C-17 permits CV-04 restart only after all gates pass.", height - 440)
    line("Restore target: within 7 minutes after snapshot verification (deadline 09:42).", height - 458)
    line("If either current or vibration fails, hold CV-04 stopped and escalate to operations.", height - 476)
    page.setFont("Helvetica", 8)
    page.setFillColor(colors.grey)
    page.drawString(52, 45, "Fixture 10521 / page 1 / synthetic facts for preservation checks")
    page.showPage()
    page.save()
    return buffer.getvalue()


def create(output: Path) -> Path:
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(BytesIO(_source_pdf())))
    writer.add_metadata({"/Title": "Synthetic AMHS recovery evidence - protected surrogate"})
    writer.encrypt(
        user_password=OPEN_PASSWORD,
        owner_password=OWNER_PASSWORD,
        permissions_flag=0,
        algorithm="AES-256",
    )
    with output.open("wb") as stream:
        writer.write(stream)
    reader = PdfReader(output)
    if not reader.is_encrypted or reader.decrypt(OPEN_PASSWORD) == 0:
        raise RuntimeError("Synthetic PDF encryption verification failed.")
    if len(reader.pages) != 1:
        raise RuntimeError("Synthetic PDF page count changed.")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(create(args.output))


if __name__ == "__main__":
    main()
