"""Password-encrypt an existing fictional PowerPoint fixture for local tests.

This creates a standard PowerPoint open-password file, not company DRM. It
never edits the source deck and is intentionally pinned to one synthetic file.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
SOURCE = _ROOT / "output/pptx/synthetic-food-allergen-rich-10521.pptx"
OUTPUT = _ROOT / "output/pptx/synthetic-food-allergen-protected-10521.pptx"
EXPECTED_SOURCE_SHA256 = "9169a47108a740c26a4f5623d13d10f5102d885511c6c9e37941ba4e7cff7ced"


def main() -> None:
    password = os.environ.get("SB_TEST_PROTECTED_PPTX_PASSWORD", "")
    if not password or len(password) < 12:
        raise ValueError("Set a synthetic test password of at least 12 characters.")
    if not SOURCE.is_file() or sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise ValueError("The pinned synthetic source deck is missing or changed.")
    if OUTPUT.exists():
        raise FileExistsError("The encrypted fixture already exists; never overwrite it.")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    presentation = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        # Untitled opens a distinct copy; WithWindow=0 avoids a visible editor.
        presentation = app.Presentations.Open(str(SOURCE), 0, -1, 0)
        presentation.Password = password
        presentation.SaveAs(str(OUTPUT), 24)  # ppSaveAsOpenXMLPresentation
        if not OUTPUT.is_file() or OUTPUT.stat().st_size == 0:
            raise RuntimeError("PowerPoint did not produce an encrypted fixture.")
        print("source_sha256", EXPECTED_SOURCE_SHA256)
        print("encrypted_sha256", sha256(OUTPUT.read_bytes()).hexdigest())
        print("encrypted_bytes", OUTPUT.stat().st_size)
    finally:
        try:
            if presentation is not None:
                presentation.Close()
        finally:
            if app is not None:
                app.Quit()
        presentation = None
        app = None
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
