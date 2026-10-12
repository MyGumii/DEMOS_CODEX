"""Create a test-only password-protected Word fixture on an interactive host.

This is not a server-side Office opener or a company DRM implementation.  The
caller must supply a synthetic source DOCX and a test password through an
environment variable.  Existing outputs are never overwritten.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path


def encrypt_fixture(source: Path, target: Path, password: str) -> str:
    if not password or len(password) < 12:
        raise ValueError("A synthetic test password of at least 12 characters is required.")
    source = source.resolve(strict=True)
    target = target.resolve(strict=False)
    if source.suffix.lower() != ".docx" or target.suffix.lower() != ".docx":
        raise ValueError("Source and target must be DOCX files.")
    if target.exists() or source == target:
        raise FileExistsError("The encrypted fixture target already exists.")
    target.parent.mkdir(parents=True, exist_ok=True)

    import pythoncom  # local interactive validation runtime only
    from win32com.client import DispatchEx

    application = None
    document = None
    pythoncom.CoInitialize()
    try:
        application = DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        application.AutomationSecurity = 3
        document = application.Documents.Open(
            FileName=str(source), ConfirmConversions=False, ReadOnly=False,
            AddToRecentFiles=False, Visible=False, OpenAndRepair=False,
            NoEncodingDialog=True,
        )
        # Pass the opening password in SaveAs2's fourth positional slot. Some
        # dynamic COM wrappers accepted the named argument but omitted it.
        document.SaveAs2(str(target), 16, False, password, False)
        if not bool(document.HasPassword):
            raise AssertionError("Word did not retain the opening password.")
        document.Saved = True
    finally:
        if document is not None:
            document.Close(False)
        if application is not None:
            application.Quit()
        pythoncom.CoUninitialize()

    raw = target.read_bytes()
    if not raw.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise AssertionError("Word did not emit an encrypted Office container.")
    return sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--password-env", default="SB_TEST_PROTECTED_WORD_PASSWORD")
    args = parser.parse_args()
    password = os.environ.get(args.password_env, "")
    digest = encrypt_fixture(args.source, args.target, password)
    print(f"protected_word_fixture_sha256={digest}")


if __name__ == "__main__":
    main()
