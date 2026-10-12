"""Create a text-only encrypted PDF surrogate for the isolated 10521 tests.

This password-protected PDF is not an enterprise DRM policy or credential test.
Its page stream intentionally has no drawn lines, images, or annotations so
the authorized opener should report an empty visual-page inventory.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


OPEN_PASSWORD = "text-open-10521"
OWNER_PASSWORD = "text-owner-10521"


def _source_pdf() -> bytes:
    output = BytesIO()
    page = canvas.Canvas(output, pagesize=A4)
    page.setTitle("Synthetic AMHS text-only recovery note")
    lines = (
        "SYNTHETIC AMHS TEXT-ONLY RECOVERY NOTE - NOT COMPANY DRM",
        "Controller C-17 stopped conveyor CV-04 at 09:31.",
        "Snapshot S-42 was verified at 09:35.",
        "Recovery order: isolate C-17, verify S-42, restore, validate telemetry.",
        "Release condition: motor current below 8.0 A and vibration below 2.0 mm/s.",
        "Observed values were 7.4 A and 1.6 mm/s, so both release gates passed.",
        "Escalate to operations if either release gate fails.",
        "This source contains no diagram, raster image, drawn table, or annotation.",
    )
    y = A4[1] - 72
    for ordinal, line in enumerate(lines):
        page.setFont("Helvetica-Bold" if ordinal == 0 else "Helvetica", 10)
        page.drawString(54, y, line)
        y -= 32
    page.showPage()
    page.save()
    return output.getvalue()


def create(output: Path) -> Path:
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(BytesIO(_source_pdf())))
    writer.add_metadata({"/Title": "Synthetic AMHS text-only protected surrogate"})
    writer.encrypt(user_password=OPEN_PASSWORD, owner_password=OWNER_PASSWORD,
                   permissions_flag=0, algorithm="AES-256")
    with output.open("wb") as destination:
        writer.write(destination)
    reader = PdfReader(output, strict=True)
    if not reader.is_encrypted or reader.decrypt(OWNER_PASSWORD) == 0 or len(reader.pages) != 1:
        raise RuntimeError("The synthetic protected PDF could not be verified.")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(create(parser.parse_args().output))


if __name__ == "__main__":
    main()
