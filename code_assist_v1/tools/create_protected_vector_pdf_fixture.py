"""Create a fictional encrypted PDF with a vector-only directional diagram.

This is a standard password-protected PDF, not a company DRM document. The
diagram's arrows deliberately have no equivalent prose, so text-only extraction
cannot establish the direction of the relationships.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


OPEN_PASSWORD = "vector-open-10521"
OWNER_PASSWORD = "vector-owner-10521"


def _arrow(page: canvas.Canvas, left: float, right: float, y: float) -> None:
    page.setStrokeColor(colors.HexColor("#087e8b"))
    page.setFillColor(colors.HexColor("#087e8b"))
    page.setLineWidth(3)
    page.line(left, y, right - 11, y)
    path = page.beginPath()
    path.moveTo(right, y)
    path.lineTo(right - 13, y + 7)
    path.lineTo(right - 13, y - 7)
    path.close()
    page.drawPath(path, fill=1, stroke=0)


def _box(page: canvas.Canvas, x: float, y: float, label: str) -> None:
    page.setFillColor(colors.HexColor("#e6f1f4"))
    page.setStrokeColor(colors.HexColor("#215269"))
    page.roundRect(x, y, 118, 54, 7, fill=1, stroke=1)
    page.setFillColor(colors.HexColor("#193348"))
    page.setFont("Helvetica-Bold", 11)
    page.drawCentredString(x + 59, y + 22, label)


def _source_pdf() -> bytes:
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    page.setTitle("Synthetic AMHS vector relationship fixture")

    page.setFillColor(colors.HexColor("#193348"))
    page.setFont("Helvetica-Bold", 18)
    page.drawString(48, height - 62, "AMHS recovery source - synthetic")
    page.setFont("Helvetica", 10)
    page.drawString(48, height - 88, "This fixture is not operating guidance or company DRM.")
    page.drawString(48, height - 124, "Controller C17 enters isolation at 09:31 after fault F09.")
    page.drawString(48, height - 144, "Use snapshot S42 verified at 09:35; release requires 90 s of aligned telemetry.")
    page.setFont("Helvetica-Bold", 12)
    page.drawString(48, height - 189, "Release table")
    columns = (50, 218, 354)
    for x, label in zip(columns, ("Condition", "Limit", "Action")):
        page.drawString(x, height - 218, label)
    page.setFont("Helvetica", 10)
    for index, row in enumerate((
        ("Motor current", "below 14.2 A", "keep lane B isolated"),
        ("Vibration", "below 3.8 mm/s", "inspect bearing"),
        ("Telemetry gap", "under 5 s", "repeat comparison"),
    )):
        y = height - 244 - index * 27
        for x, value in zip(columns, row):
            page.drawString(x, y, value)
    page.setStrokeColor(colors.HexColor("#adbec7"))
    page.line(48, height - 225, width - 48, height - 225)
    page.line(48, height - 306, width - 48, height - 306)
    page.setFont("Helvetica", 9)
    page.drawString(48, 48, "VECTOR-CANARY-10521 / source page 1 of 2")
    page.showPage()

    page.setFillColor(colors.HexColor("#193348"))
    page.setFont("Helvetica-Bold", 18)
    page.drawString(48, height - 62, "Directional control diagram")
    page.setFont("Helvetica", 10)
    page.drawString(48, height - 85, "The arrow directions are drawn; they are not described in the text.")
    base_y = height - 280
    for x, label in ((55, "Sensor S4"), (240, "PLC P2"), (425, "CV-04")):
        _box(page, x, base_y, label)
    _arrow(page, 173, 240, base_y + 27)
    _arrow(page, 358, 425, base_y + 27)
    page.setFont("Helvetica", 9)
    page.drawString(48, 48, "VECTOR-CANARY-10521 / source page 2 of 2")
    page.showPage()
    page.save()
    return buffer.getvalue()


def create(output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(BytesIO(_source_pdf())))
    writer.add_metadata({"/Title": "Synthetic AMHS vector protected surrogate"})
    writer.encrypt(
        user_password=OPEN_PASSWORD,
        owner_password=OWNER_PASSWORD,
        permissions_flag=0,
        algorithm="AES-256",
    )
    with output.open("wb") as stream:
        writer.write(stream)
    check = PdfReader(output)
    if not check.is_encrypted or check.decrypt(OWNER_PASSWORD) == 0 or len(check.pages) != 2:
        raise RuntimeError("Synthetic vector PDF protection or coverage changed.")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(create(args.output))


if __name__ == "__main__":
    main()
