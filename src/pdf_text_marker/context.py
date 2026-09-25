"""应用上下文及工作流配置。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config_loader import ConfigurationError, load_config, resolve_path
from .models import ExcelKeywordSource


@dataclass(frozen=True, slots=True)
class MarkPdfsSettings:
    """PDF 关键词标注工作流设置。"""

    keyword_file: Path
    pdf_dir: Path
    output_dir: Path
    recursive: bool = True
    excel_sources: tuple[ExcelKeywordSource, ...] = ()
    sheet_name: str | None = None
    keyword_column: str | None = None
    pdf_name_column: str | None = None
    report_filename: str = "pdf_text_marker_report.html"
    highlight_enabled: bool = True
    highlight_color: tuple[float, float, float] = (1.0, 1.0, 0.0)
    highlight_opacity: float = 0.35
    border_enabled: bool = True
    border_color: tuple[float, float, float] = (1.0, 0.0, 0.0)
    border_width: float = 1.25
    box_aspect_ratio: float = 4.0
    box_size: float = 40.0
    box_scale: float = 1.0

    def with_overrides(self, **values: object) -> "MarkPdfsSettings":
        """应用入口层传入的非空临时覆盖。"""
        clean = {key: value for key, value in values.items() if value is not None}
        return replace(self, **clean)


@dataclass(frozen=True, slots=True)
class A3SplitSettings:
    """AI 识别有效图框并拆分为 A3 页的工作流设置。"""

    input_dir: Path
    output_dir: Path
    recursive: bool = True
    ai_base_url: str = "http://127.0.0.1:8080/v1"
    ai_model: str = ""
    ai_api_key: str = ""
    top_left_reference_image: Path = Path("input/识别图纸左上角.png")
    bottom_right_reference_image: Path = Path("input/识别图纸右下角.png")
    preview_max_pixels: int = 2400
    top_left_offset_x_mm: float = 0.0
    top_left_offset_y_mm: float = 0.0
    bottom_right_offset_x_mm: float = 0.0
    bottom_right_offset_y_mm: float = 0.0
    overlap_mm: float = 0.0
    margin_mm: float = 5.0

    def with_overrides(self, **values: object) -> "A3SplitSettings":
        clean = {key: value for key, value in values.items() if value is not None}
        return replace(self, **clean)


@dataclass(frozen=True, slots=True)
class AppContext:
    """入口层与工作流共享的项目上下文。"""

    project_root: Path
    log_level: str
    mark_pdfs: MarkPdfsSettings
    a3_split: A3SplitSettings
    config: dict[str, Any]


def bootstrap_context(entry_file: str | Path, config_file: str | Path | None = None) -> AppContext:
    """从入口脚本位置定位项目根目录并构造上下文。"""
    project_root = Path(entry_file).resolve().parent
    config_path = Path(config_file) if config_file else None
    config = load_config(project_root, config_path)
    flow = config.get("flows", {}).get("mark_pdfs", {})
    if not isinstance(flow, dict):
        raise ConfigurationError("flows.mark_pdfs 必须是映射")
    app = config.get("app", {})
    if not isinstance(app, dict):
        raise ConfigurationError("app 必须是映射")

    def optional_text(name: str) -> str | None:
        value = str(flow.get(name, "")).strip()
        return value or None

    settings = MarkPdfsSettings(
        keyword_file=resolve_path(project_root, str(flow.get("keyword_file", "./input/keywords.xlsx"))),
        pdf_dir=resolve_path(project_root, str(flow.get("pdf_dir", "./input"))),
        output_dir=resolve_path(project_root, str(flow.get("output_dir", "./output"))),
        recursive=_as_bool(flow.get("recursive", True), "recursive"),
        excel_sources=_parse_excel_sources(flow.get("excel_sources", [])),
        sheet_name=optional_text("sheet_name"),
        keyword_column=optional_text("keyword_column"),
        pdf_name_column=optional_text("pdf_name_column"),
        report_filename=str(flow.get("report_filename", "pdf_text_marker_report.html")).strip(),
        highlight_enabled=_as_bool(flow.get("highlight_enabled", True), "highlight_enabled"),
        highlight_color=parse_hex_color(str(flow.get("highlight_color", "#FFFF00"))),
        highlight_opacity=float(flow.get("highlight_opacity", 0.35)),
        border_enabled=_as_bool(flow.get("border_enabled", True), "border_enabled"),
        border_color=parse_hex_color(str(flow.get("border_color", "#FF0000"))),
        border_width=float(flow.get("border_width", 1.25)),
        box_aspect_ratio=float(flow.get("box_aspect_ratio", 4.0)),
        box_size=float(flow.get("box_size", 40.0)),
        box_scale=float(flow.get("box_scale", 1.0)),
    )
    _validate_settings(settings)
    a3_flow = config.get("flows", {}).get("a3_split", {})
    if not isinstance(a3_flow, dict):
        raise ConfigurationError("flows.a3_split 必须是映射")
    a3_settings = A3SplitSettings(
        input_dir=resolve_path(project_root, str(a3_flow.get("input_dir", "./output"))),
        output_dir=resolve_path(project_root, str(a3_flow.get("output_dir", "./output/a3_split"))),
        recursive=_as_bool(a3_flow.get("recursive", True), "a3_split.recursive"),
        ai_base_url=str(a3_flow.get("ai_base_url", "http://127.0.0.1:8080/v1")).strip().rstrip("/"),
        ai_model=str(a3_flow.get("ai_model", "")).strip(),
        ai_api_key=str(a3_flow.get("ai_api_key", "")).strip(),
        top_left_reference_image=resolve_path(
            project_root,
            str(a3_flow.get("top_left_reference_image", "./input/识别图纸左上角.png")),
        ),
        bottom_right_reference_image=resolve_path(
            project_root,
            str(
                a3_flow.get(
                    "bottom_right_reference_image",
                    a3_flow.get("reference_image", "./input/识别图纸右下角.png"),
                )
            ),
        ),
        preview_max_pixels=int(a3_flow.get("preview_max_pixels", 2400)),
        top_left_offset_x_mm=float(a3_flow.get("top_left_offset_x_mm", 0.0)),
        top_left_offset_y_mm=float(a3_flow.get("top_left_offset_y_mm", 0.0)),
        bottom_right_offset_x_mm=float(a3_flow.get("bottom_right_offset_x_mm", 0.0)),
        bottom_right_offset_y_mm=float(a3_flow.get("bottom_right_offset_y_mm", 0.0)),
        overlap_mm=float(a3_flow.get("overlap_mm", 0.0)),
        margin_mm=float(a3_flow.get("margin_mm", 5.0)),
    )
    _validate_a3_settings(a3_settings)
    return AppContext(
        project_root=project_root,
        log_level=str(app.get("log_level", "INFO")),
        mark_pdfs=settings,
        a3_split=a3_settings,
        config=config,
    )


def _as_bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必须是布尔值")


def parse_hex_color(value: str) -> tuple[float, float, float]:
    """把 #RRGGBB 颜色转换为 PyMuPDF 使用的 0-1 RGB。"""
    text = value.strip()
    if len(text) != 7 or not text.startswith("#"):
        raise ConfigurationError(f"颜色必须使用 #RRGGBB 格式: {value}")
    try:
        channels = tuple(int(text[index : index + 2], 16) / 255 for index in (1, 3, 5))
    except ValueError as exc:
        raise ConfigurationError(f"颜色值无效: {value}") from exc
    return channels


def color_to_hex(color: tuple[float, float, float]) -> str:
    """把 0-1 RGB 转换为界面使用的 #RRGGBB。"""
    return "#" + "".join(f"{round(max(0, min(1, channel)) * 255):02X}" for channel in color)


def _parse_excel_sources(value: object) -> tuple[ExcelKeywordSource, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ConfigurationError("excel_sources 必须是列表")
    sources: list[ExcelKeywordSource] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ConfigurationError(f"excel_sources 第 {index} 项必须是映射")
        sheet_name = str(item.get("sheet_name", "")).strip()
        raw_columns = item.get("keyword_columns", [])
        if isinstance(raw_columns, str):
            raw_columns = [raw_columns]
        if not sheet_name:
            raise ConfigurationError(f"excel_sources 第 {index} 项缺少 sheet_name")
        if not isinstance(raw_columns, list):
            raise ConfigurationError(f"excel_sources 第 {index} 项的 keyword_columns 必须是列表")
        columns = tuple(str(column).strip() for column in raw_columns if str(column).strip())
        if not columns:
            raise ConfigurationError(f"excel_sources 第 {index} 项至少需要一个关键词列")
        pdf_name_column = str(item.get("pdf_name_column", "")).strip() or None
        sources.append(
            ExcelKeywordSource(
                sheet_name=sheet_name,
                keyword_columns=columns,
                pdf_name_column=pdf_name_column,
            )
        )
    return tuple(sources)


def _validate_settings(settings: MarkPdfsSettings) -> None:
    if not settings.report_filename.lower().endswith(".html"):
        raise ConfigurationError("report_filename 必须以 .html 结尾")
    if not 0 <= settings.highlight_opacity <= 1:
        raise ConfigurationError("highlight_opacity 必须在 0 到 1 之间")
    if settings.border_width <= 0:
        raise ConfigurationError("border_width 必须大于 0")
    if not settings.highlight_enabled and not settings.border_enabled:
        raise ConfigurationError("底纹和边框至少需要启用一项")
    if settings.box_aspect_ratio <= 0:
        raise ConfigurationError("box_aspect_ratio 必须大于 0")
    if settings.box_size <= 0:
        raise ConfigurationError("box_size 必须大于 0")
    if settings.box_scale <= 0:
        raise ConfigurationError("box_scale 必须大于 0")


def _validate_a3_settings(settings: A3SplitSettings) -> None:
    if not settings.ai_base_url.startswith(("http://", "https://")):
        raise ConfigurationError("a3_split.ai_base_url 必须是 HTTP/HTTPS 地址")
    if settings.preview_max_pixels < 512 or settings.preview_max_pixels > 4096:
        raise ConfigurationError("a3_split.preview_max_pixels 必须在 512 到 4096 之间")
    if not -100 <= settings.top_left_offset_x_mm <= 100:
        raise ConfigurationError("a3_split.top_left_offset_x_mm 必须在 -100 到 100 之间")
    if not -100 <= settings.top_left_offset_y_mm <= 100:
        raise ConfigurationError("a3_split.top_left_offset_y_mm 必须在 -100 到 100 之间")
    if not -100 <= settings.bottom_right_offset_x_mm <= 100:
        raise ConfigurationError("a3_split.bottom_right_offset_x_mm 必须在 -100 到 100 之间")
    if not -100 <= settings.bottom_right_offset_y_mm <= 100:
        raise ConfigurationError("a3_split.bottom_right_offset_y_mm 必须在 -100 到 100 之间")
    if settings.overlap_mm < 0 or settings.overlap_mm > 100:
        raise ConfigurationError("a3_split.overlap_mm 必须在 0 到 100 之间")
    if settings.margin_mm < 0 or settings.margin_mm > 50:
        raise ConfigurationError("a3_split.margin_mm 必须在 0 到 50 之间")
