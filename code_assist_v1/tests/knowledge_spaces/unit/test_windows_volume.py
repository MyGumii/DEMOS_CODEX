from __future__ import annotations

import json
import ntpath
from pathlib import Path
import sys
from typing import Any

import pytest

from code_assist_v1.tools import windows_volume


class _FakeWin32Api:
    def __init__(
        self,
        *,
        final_path: str,
        volume_root: str,
        drive_type: int = 3,
        filesystem: str = "NTFS",
        reparse_components: tuple[str, ...] = (),
    ) -> None:
        self._final_path = final_path
        self._volume_root = volume_root
        self._drive_type = drive_type
        self._filesystem = filesystem
        self._reparse_components = {
            ntpath.normcase(ntpath.normpath(value)) for value in reparse_components
        }
        self.attribute_calls: list[str] = []

    def file_attributes(self, path: str) -> int:
        self.attribute_calls.append(path)
        if ntpath.normcase(ntpath.normpath(path)) in self._reparse_components:
            return windows_volume._FILE_ATTRIBUTE_REPARSE_POINT
        return 0

    def final_path(self, path: str) -> str:
        return self._final_path

    def volume_root(self, path: str) -> str:
        return self._volume_root

    def drive_type(self, volume_root: str) -> int:
        return self._drive_type

    def filesystem(self, volume_root: str) -> str:
        return self._filesystem


def _install_fake(monkeypatch: pytest.MonkeyPatch, api: _FakeWin32Api) -> None:
    monkeypatch.setattr(windows_volume, "_IS_WINDOWS", True)
    monkeypatch.setattr(windows_volume, "_make_win32_api", lambda: api)


def _assert_sorted_dict_keys(value: Any) -> None:
    if isinstance(value, dict):
        assert list(value) == sorted(value)
        for nested in value.values():
            _assert_sorted_dict_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_sorted_dict_keys(nested)


@pytest.mark.unit
def test_fixed_local_ntfs_is_eligible_and_every_component_is_inspected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeWin32Api(
        final_path=r"C:\data\knowledge\core.db",
        volume_root="C:\\",
    )
    _install_fake(monkeypatch, api)

    result = windows_volume.probe_path(r"C:\data\knowledge\core.db")

    assert result["eligible"] is True
    assert result["exists"] is True
    assert result["reason_codes"] == []
    assert result["reparse"] == {"components": [], "present": False}
    assert result["volume"] == {
        "drive_type": "fixed",
        "drive_type_code": 3,
        "filesystem": "NTFS",
        "root": "C:\\",
    }
    assert api.attribute_calls == [
        "C:\\",
        r"C:\data",
        r"C:\data\knowledge",
        r"C:\data\knowledge\core.db",
    ]


@pytest.mark.unit
def test_local_reparse_to_fixed_ntfs_is_reported_and_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeWin32Api(
        final_path=r"D:\canonical\core.db",
        volume_root="D:\\",
        reparse_components=(r"C:\data\junction",),
    )
    _install_fake(monkeypatch, api)

    result = windows_volume.probe_path(r"C:\data\junction\core.db")

    assert result["eligible"] is True
    assert result["resolved_path"] == r"D:\canonical\core.db"
    assert result["reparse"] == {
        "components": [r"C:\data\junction"],
        "present": True,
    }
    assert result["reason_codes"] == []


@pytest.mark.unit
def test_syntactic_unc_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(windows_volume, "_IS_WINDOWS", True)

    def unexpected_api() -> Any:
        raise AssertionError("syntactic UNC rejection must not touch Win32/SMB")

    monkeypatch.setattr(windows_volume, "_make_win32_api", unexpected_api)

    result = windows_volume.probe_path(r"\\server\share\core.db")

    assert result["syntactic_unc"] is True
    assert result["eligible"] is False
    assert result["reason_codes"] == ["syntactic_unc"]
    assert result["exists"] is False


@pytest.mark.unit
def test_public_unc_check_is_lexical_and_does_not_require_windows() -> None:
    assert windows_volume.is_syntactic_unc_path(r"\\server\share\core.db") is True
    assert windows_volume.is_syntactic_unc_path(r"\\?\UNC\server\share\core.db") is True
    assert windows_volume.is_syntactic_unc_path(r"C:\data\core.db") is False


@pytest.mark.unit
def test_mapped_drive_with_remote_final_target_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeWin32Api(
        final_path=r"\\server\share\data\core.db",
        volume_root="\\\\server\\share\\",
        drive_type=4,
        filesystem="NTFS",
    )
    _install_fake(monkeypatch, api)

    result = windows_volume.probe_path(r"Z:\data\core.db")

    assert result["syntactic_unc"] is False
    assert result["eligible"] is False
    assert "final_target_unc" in result["reason_codes"]
    assert "drive_type_remote" in result["reason_codes"]


@pytest.mark.unit
def test_removable_volume_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeWin32Api(
        final_path=r"E:\data\core.db",
        volume_root="E:\\",
        drive_type=2,
        filesystem="NTFS",
    )
    _install_fake(monkeypatch, api)

    result = windows_volume.probe_path(r"E:\data\core.db")

    assert result["eligible"] is False
    assert result["volume"]["drive_type"] == "removable"
    assert "drive_type_removable" in result["reason_codes"]


@pytest.mark.unit
@pytest.mark.parametrize("filesystem", ["ReFS", "FAT32"])
def test_non_ntfs_filesystems_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    filesystem: str,
) -> None:
    api = _FakeWin32Api(
        final_path=r"D:\data\core.db",
        volume_root="D:\\",
        filesystem=filesystem,
    )
    _install_fake(monkeypatch, api)

    result = windows_volume.probe_path(r"D:\data\core.db")

    assert result["eligible"] is False
    assert result["volume"]["filesystem"] == filesystem
    assert "filesystem_not_ntfs" in result["reason_codes"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("final_path", "volume_root", "drive_type", "filesystem", "reason"),
    [
        (
            r"\\server\share\core.db",
            "\\\\server\\share\\",
            4,
            "NTFS",
            "final_target_unc",
        ),
        (r"D:\canonical\core.db", "D:\\", 3, "ReFS", "filesystem_not_ntfs"),
    ],
)
def test_reparse_target_must_itself_be_local_fixed_ntfs(
    monkeypatch: pytest.MonkeyPatch,
    final_path: str,
    volume_root: str,
    drive_type: int,
    filesystem: str,
    reason: str,
) -> None:
    api = _FakeWin32Api(
        final_path=final_path,
        volume_root=volume_root,
        drive_type=drive_type,
        filesystem=filesystem,
        reparse_components=(r"C:\data\junction",),
    )
    _install_fake(monkeypatch, api)

    result = windows_volume.probe_path(r"C:\data\junction\core.db")

    assert result["reparse"]["present"] is True
    assert result["eligible"] is False
    assert reason in result["reason_codes"]


@pytest.mark.unit
def test_report_and_reason_order_are_stable_and_json_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeWin32Api(
        final_path=r"\\server\share\core.db",
        volume_root="\\\\server\\share\\",
        drive_type=4,
        filesystem="FAT32",
        reparse_components=(r"C:\z", r"C:\a"),
    )
    _install_fake(monkeypatch, api)

    first = windows_volume.probe_path(r"C:\z\a\core.db")
    second = windows_volume.probe_path(r"C:\z\a\core.db")

    assert first == second
    assert first["reason_codes"] == sorted(first["reason_codes"])
    assert first["reparse"]["components"] == sorted(
        first["reparse"]["components"], key=lambda value: (value.casefold(), value)
    )
    _assert_sorted_dict_keys(first)
    assert json.loads(json.dumps(first, sort_keys=False)) == first


@pytest.mark.unit
def test_non_windows_returns_safe_unsupported_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(windows_volume, "_IS_WINDOWS", False)

    result = windows_volume.probe_path(tmp_path)

    assert result["eligible"] is False
    assert result["reason_codes"] == ["unsupported_platform"]
    assert result["volume"]["drive_type"] == "unsupported"
    _assert_sorted_dict_keys(result)


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="requires the real Win32 APIs")
def test_real_isolated_windows_path_is_fixed_local_ntfs(tmp_path: Path) -> None:
    target = tmp_path / "volume-probe"
    target.mkdir()

    result = windows_volume.probe_path(target)

    assert result["requested_path"] == str(target)
    assert result["exists"] is True
    assert result["eligible"] is True
    assert result["reason_codes"] == []
    assert result["volume"]["drive_type"] == "fixed"
    assert result["volume"]["drive_type_code"] == 3
    assert result["volume"]["filesystem"].casefold() == "ntfs"
    assert result["volume"]["root"]
    assert Path(result["resolved_path"]).samefile(target)
    _assert_sorted_dict_keys(result)
