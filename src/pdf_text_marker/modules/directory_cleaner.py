"""安全检查并清空指定输出目录。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class UnsafeDirectoryError(ValueError):
    """目标目录过于宽泛或包含受保护路径。"""


@dataclass(frozen=True, slots=True)
class DirectoryContents:
    """目录内容统计。"""

    file_count: int
    directory_count: int
    total_bytes: int


def inspect_directory_contents(directory: Path) -> DirectoryContents:
    """递归统计目录内容；目录不存在时返回零。"""
    target = directory.expanduser().resolve()
    if not target.exists():
        return DirectoryContents(0, 0, 0)
    if not target.is_dir():
        raise NotADirectoryError(f"输出路径不是目录: {target}")
    files = 0
    directories = 0
    total_bytes = 0
    for item in target.rglob("*"):
        if item.is_symlink() or item.is_file():
            files += 1
            try:
                total_bytes += item.stat().st_size
            except OSError:
                pass
        elif item.is_dir():
            directories += 1
    return DirectoryContents(files, directories, total_bytes)


def clear_directory_contents(directory: Path, protected_paths: Iterable[Path]) -> DirectoryContents:
    """删除目录内全部内容但保留目录本身，并拒绝宽泛危险目标。"""
    target = directory.expanduser().resolve()
    validate_clear_target(target, protected_paths)
    summary = inspect_directory_contents(target)
    if not target.exists():
        return summary
    for child in target.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
    return summary


def validate_clear_target(target: Path, protected_paths: Iterable[Path]) -> None:
    """确认清理目标不是根目录，也不等于或包含任何受保护路径。"""
    target = target.expanduser().resolve()
    if target == Path(target.anchor):
        raise UnsafeDirectoryError("禁止清空磁盘根目录")
    for protected in protected_paths:
        resolved = protected.expanduser().resolve()
        if target == resolved or target in resolved.parents:
            raise UnsafeDirectoryError(f"输出目录包含受保护路径，禁止清空: {resolved}")
