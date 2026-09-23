"""生成 AI 图框识别结果的可视化诊断截图。"""

from __future__ import annotations

from pathlib import Path

import fitz

from pdf_text_marker.modules.a3_splitter import (
    effective_rect_from_analysis,
    rotate_normalized_bounds,
)
from pdf_text_marker.modules.local_vision_client import NormalizedBounds, PageLayoutAnalysis


TOP_LEFT_COLOR = (0.0, 0.75, 0.2)
TITLE_BLOCK_COLOR = (0.95, 0.1, 0.1)
FINAL_RECT_COLOR = (0.8, 0.0, 0.85)
SPLIT_COLOR = (0.0, 0.55, 0.9)


def create_bounds_diagnostic(
    preview_png: bytes,
    source_page_rect: fitz.Rect,
    analysis: PageLayoutAnalysis,
    output_path: Path,
    *,
    top_left_offset_x_mm: float = 0.0,
    top_left_offset_y_mm: float = 0.0,
    bottom_right_offset_x_mm: float = 0.0,
    bottom_right_offset_y_mm: float = 0.0,
) -> Path:
    """在旋正后的页面预览上标出两个 AI 特征框与最终打印范围。"""
    pixmap = fitz.Pixmap(preview_png)
    width, height = float(pixmap.width), float(pixmap.height)
    if analysis.rotation in {90, 270}:
        width, height = height, width

    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.insert_image(page.rect, stream=preview_png, rotate=analysis.rotation, keep_proportion=False)

    top_left = rotate_normalized_bounds(analysis.top_left_feature_bounds, analysis.rotation)
    title_block = rotate_normalized_bounds(analysis.title_block_bounds, analysis.rotation)
    top_left_rect = _normalized_to_rect(top_left, page.rect)
    title_block_rect = _normalized_to_rect(title_block, page.rect)
    left_axis_rect = (
        _normalized_to_rect(rotate_normalized_bounds(analysis.left_axis_bounds, analysis.rotation), page.rect)
        if analysis.left_axis_bounds is not None
        else None
    )
    top_axis_rect = (
        _normalized_to_rect(rotate_normalized_bounds(analysis.top_axis_bounds, analysis.rotation), page.rect)
        if analysis.top_axis_bounds is not None
        else None
    )

    oriented_source_rect = fitz.Rect(source_page_rect)
    if analysis.rotation in {90, 270}:
        oriented_source_rect = fitz.Rect(0, 0, source_page_rect.height, source_page_rect.width)
    else:
        oriented_source_rect = fitz.Rect(0, 0, source_page_rect.width, source_page_rect.height)
    effective = effective_rect_from_analysis(
        analysis.top_left_feature_bounds,
        analysis.title_block_bounds,
        analysis.rotation,
        oriented_source_rect,
        top_left_offset_x_mm,
        top_left_offset_y_mm,
        bottom_right_offset_x_mm,
        bottom_right_offset_y_mm,
        analysis.left_axis_bounds,
        analysis.top_axis_bounds,
    )
    final_rect = fitz.Rect(
        effective.x0 / oriented_source_rect.width * page.rect.width,
        effective.y0 / oriented_source_rect.height * page.rect.height,
        effective.x1 / oriented_source_rect.width * page.rect.width,
        effective.y1 / oriented_source_rect.height * page.rect.height,
    )

    line_width = max(3.0, min(width, height) / 260.0)
    if left_axis_rect is not None and top_axis_rect is not None:
        page.draw_rect(left_axis_rect, color=TOP_LEFT_COLOR, width=line_width, overlay=True)
        page.draw_rect(top_axis_rect, color=TOP_LEFT_COLOR, width=line_width, overlay=True)
        anchor = fitz.Point(
            (top_axis_rect.x0 + top_axis_rect.x1) / 2,
            (left_axis_rect.y0 + left_axis_rect.y1) / 2,
        )
    else:
        page.draw_rect(top_left_rect, color=TOP_LEFT_COLOR, width=line_width, overlay=True)
        anchor = fitz.Point(top_left_rect.x0, top_left_rect.y0)
    page.draw_rect(title_block_rect, color=TITLE_BLOCK_COLOR, width=line_width, overlay=True)
    page.draw_rect(final_rect, color=FINAL_RECT_COLOR, width=line_width, overlay=True)
    middle_x = (final_rect.x0 + final_rect.x1) / 2
    page.draw_line(
        fitz.Point(middle_x, final_rect.y0),
        fitz.Point(middle_x, final_rect.y1),
        color=SPLIT_COLOR,
        width=line_width,
        dashes="8 6",
        overlay=True,
    )
    _draw_anchor(page, anchor, TOP_LEFT_COLOR, line_width)
    _draw_anchor(page, fitz.Point(title_block_rect.x1, title_block_rect.y1), TITLE_BLOCK_COLOR, line_width)
    _draw_legend(page, analysis, top_left, title_block, final_rect, line_width)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = page.get_pixmap(alpha=False)
    result.save(output_path)
    document.close()
    return output_path


def _normalized_to_rect(bounds: NormalizedBounds, page_rect: fitz.Rect) -> fitz.Rect:
    return fitz.Rect(
        bounds.x0 * page_rect.width,
        bounds.y0 * page_rect.height,
        bounds.x1 * page_rect.width,
        bounds.y1 * page_rect.height,
    )


def _draw_anchor(page: fitz.Page, point: fitz.Point, color: tuple[float, float, float], width: float) -> None:
    radius = max(8.0, width * 2.5)
    page.draw_circle(point, radius, color=color, fill=(1.0, 1.0, 1.0), width=width, overlay=True)
    page.draw_line(point - (radius, 0), point + (radius, 0), color=color, width=width, overlay=True)
    page.draw_line(point - (0, radius), point + (0, radius), color=color, width=width, overlay=True)


def _draw_legend(
    page: fitz.Page,
    analysis: PageLayoutAnalysis,
    top_left: NormalizedBounds,
    title_block: NormalizedBounds,
    final_rect: fitz.Rect,
    line_width: float,
) -> None:
    font_size = max(11.0, min(page.rect.width, page.rect.height) / 55.0)
    panel = fitz.Rect(10, 10, min(page.rect.width - 10, 700), 20 + font_size * 5.2)
    page.draw_rect(panel, color=(0.15, 0.15, 0.15), fill=(1.0, 1.0, 1.0), fill_opacity=0.88, width=1)
    rows = (
        (f"AI BOUNDS DIAGNOSTIC | clockwise rotation: {analysis.rotation} deg", (0.1, 0.1, 0.1)),
        (f"GREEN  top-left L/1: {_format_bounds(top_left)}", TOP_LEFT_COLOR),
        (f"RED    bottom-right title block: {_format_bounds(title_block)}", TITLE_BLOCK_COLOR),
        (
            "MAGENTA final print area: "
            f"({final_rect.x0/page.rect.width:.4f}, {final_rect.y0/page.rect.height:.4f}, "
            f"{final_rect.x1/page.rect.width:.4f}, {final_rect.y1/page.rect.height:.4f})",
            FINAL_RECT_COLOR,
        ),
        ("BLUE DASH  left/right A3 split line", SPLIT_COLOR),
    )
    y = 10 + font_size * 1.15
    for text, color in rows:
        page.insert_text((18, y), text, fontsize=font_size, fontname="helv", color=color, overlay=True)
        y += font_size


def _format_bounds(bounds: NormalizedBounds) -> str:
    return f"({bounds.x0:.4f}, {bounds.y0:.4f}, {bounds.x1:.4f}, {bounds.y1:.4f})"
