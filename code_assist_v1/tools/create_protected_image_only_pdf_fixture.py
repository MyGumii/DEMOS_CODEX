"""Create a synthetic owner-encrypted, image-only PDF for visual-route tests.

The input is a rendered *synthetic* page PNG. The result has no embedded text
and is not an enterprise DRM document or an authorization-bypass example.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
from collections.abc import Sequence

from pypdf import PdfReader, PdfWriter, PasswordType
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


OPEN_PASSWORD = "fixture-scan-open-2026"
OWNER_PASSWORD = "fixture-scan-owner-2026"


def create(source_png: Path, output: Path) -> Path:
    return create_pages((source_png,), output)


def create_pages(source_pngs: Sequence[Path], output: Path) -> Path:
    if not source_pngs:
        raise ValueError("At least one synthetic page PNG is required.")
    sources = tuple(Path(path).resolve(strict=True) for path in source_pngs)
    output = Path(output).resolve()
    if any(path.suffix.lower() != ".png" for path in sources) or output.suffix.lower() != ".pdf":
        raise ValueError("PNG inputs and a PDF output are required.")
    clear = BytesIO()
    page = canvas.Canvas(clear, pagesize=A4)
    page.setTitle("Synthetic image-only AMHS evidence - not enterprise DRM")
    for source_png in sources:
        page.drawImage(ImageReader(str(source_png)), 0, 0,
            width=A4[0], height=A4[1], preserveAspectRatio=False, mask="auto")
        page.showPage()
    page.save()
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(BytesIO(clear.getvalue()), strict=True))
    writer.add_metadata({"/Title": "Synthetic image-only protected surrogate"})
    writer.encrypt(user_password=OPEN_PASSWORD, owner_password=OWNER_PASSWORD,
        permissions_flag=0, algorithm="AES-256")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)
    reader = PdfReader(output, strict=True)
    if (not reader.is_encrypted or reader.decrypt(OWNER_PASSWORD)
            is not PasswordType.OWNER_PASSWORD or len(reader.pages) != len(sources)
            or any((page.extract_text() or "").strip() for page in reader.pages)):
        raise RuntimeError("The image-only encrypted PDF failed verification.")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_png", type=Path, nargs="+")
    parser.add_argument("output_pdf", type=Path)
    args = parser.parse_args()
    print(create_pages(args.source_png, args.output_pdf))


if __name__ == "__main__":
    main()
