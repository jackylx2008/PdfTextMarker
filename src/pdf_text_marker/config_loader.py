"""统一读取环境文件和 YAML 配置。"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import Any

import yaml


ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}")


class ConfigurationError(ValueError):
    """配置内容缺失或无效。"""


def load_config(project_root: Path, config_file: Path | None = None) -> dict[str, Any]:
    """按优先级加载 common.env 和公开 YAML 配置。"""
    root = project_root.resolve()
    load_env_file(root / "common.env")
    _select_cloudstation_root()

    path = config_file or root / "config.yaml"
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise ConfigurationError(f"配置文件不存在: {path}")

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"无法读取配置文件 {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigurationError("config.yaml 的顶层必须是映射")
    return _expand_value(loaded)


def load_env_file(path: Path) -> None:
    """读取简单 dotenv 文件，且不覆盖已有进程环境变量。"""
    if not path.is_file():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"{path.name} 第 {line_number} 行缺少等号")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ConfigurationError(f"{path.name} 第 {line_number} 行变量名无效: {name}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def resolve_path(project_root: Path, value: str | Path) -> Path:
    """展开用户目录并将相对路径解析到项目根目录。"""
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _select_cloudstation_root() -> None:
    if os.environ.get("CLOUDSTATION_ROOT"):
        return
    system_name = platform.system().upper()
    platform_variable = {
        "WINDOWS": "CLOUDSTATION_ROOT_WINDOWS",
        "DARWIN": "CLOUDSTATION_ROOT_MACOS",
        "LINUX": "CLOUDSTATION_ROOT_LINUX",
    }.get(system_name)
    if platform_variable and os.environ.get(platform_variable):
        os.environ["CLOUDSTATION_ROOT"] = os.environ[platform_variable]


def _expand_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_value(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ConfigurationError(f"缺少环境变量: {name}")

    previous = value
    for _ in range(10):
        expanded = ENV_PATTERN.sub(replace, previous)
        if expanded == previous:
            return expanded
        previous = expanded
    raise ConfigurationError(f"环境变量展开层级过深: {value}")
