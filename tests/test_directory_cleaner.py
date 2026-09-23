"""输出目录清理测试。"""

from pathlib import Path

import pytest

from pdf_text_marker.modules.directory_cleaner import (
    UnsafeDirectoryError,
    clear_directory_contents,
    inspect_directory_contents,
)


def test_clear_directory_contents_keeps_root_directory(tmp_path: Path) -> None:
    output = tmp_path / "output"
    nested = output / "nested"
    nested.mkdir(parents=True)
    (output / "report.html").write_text("report", encoding="utf-8")
    (nested / "marked.pdf").write_bytes(b"pdf")

    before = inspect_directory_contents(output)
    removed = clear_directory_contents(output, protected_paths=(tmp_path / "input",))

    assert before == removed
    assert removed.file_count == 2
    assert removed.directory_count == 1
    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_clear_rejects_directory_containing_protected_path(tmp_path: Path) -> None:
    protected = tmp_path / "project" / "input"
    protected.mkdir(parents=True)

    with pytest.raises(UnsafeDirectoryError):
        clear_directory_contents(tmp_path, protected_paths=(protected,))
