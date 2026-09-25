"""根据 AI 页面方向旋正图纸，并把有效图框左右拆成两张 A3 页面。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import fitz

from pdf_text_marker.modules.local_vision_client import LocalVisionClient, NormalizedBounds


MM_TO_PT = 72.0 / 25.4
A3_PORTRAIT = (297.0 * MM_TO_PT, 420.0 * MM_TO_PT)


class A3SplitError(RuntimeError):
    """单个 PDF 无法完成 A3 拆分。"""


class A3SplitCancelled(RuntimeError):
    """在安全边界取消拆分任务。"""


PageCallback = Callable[[int, int, int], None]
CancelCallback = Callable[[], bool]


def split_pdf_to_a3(
    input_path: Path,
    output_path: Path,
    client: LocalVisionClient,
    *,
    top_left_reference_png: bytes | None = None,
    bottom_right_reference_png: bytes | None = None,
    preview_max_pixels: int = 1600,
    top_left_offset_x_mm: float = 0.0,
    top_left_offset_y_mm: float = 0.0,
    bottom_right_offset_x_mm: float = 0.0,
    bottom_right_offset_y_mm: float = 0.0,
    overlap_mm: float = 0.0,
    margin_mm: float = 5.0,
    on_page: PageCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[int, int, int]:
    """每个源页面经 AI 旋正后左右二分，并写为两个 A3 页面。"""
    page_callback = on_page or (lambda _current, _total, _rotation: None)
    cancel_check = is_cancelled or (lambda: False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".part")
    try:
        source = fitz.open(input_path)
    except Exception as exc:
        raise A3SplitError(f"无法打开 PDF：{exc}") from exc
    output = fitz.open()
    rotated_pages = 0
    try:
        if source.needs_pass:
            raise A3SplitError("PDF 已加密，需要密码")
        if source.page_count == 0:
            raise A3SplitError("PDF 没有页面")
        for page_index, page in enumerate(source):
            if cancel_check():
                raise A3SplitCancelled("任务已取消")
            preview = render_page_preview(page, preview_max_pixels)
            analysis = client.detect_page_layout(
                preview,
                page_index + 1,
                top_left_reference_png,
                bottom_right_reference_png,
            )
            page_callback(page_index + 1, source.page_count, analysis.rotation)
            if analysis.rotation:
                rotated_pages += 1
            oriented = create_oriented_page_document(source, page_index, analysis.rotation)
            try:
                effective = effective_rect_from_analysis(
                    analysis.top_left_feature_bounds,
                    analysis.title_block_bounds,
                    analysis.rotation,
                    oriented[0].rect,
                    top_left_offset_x_mm,
                    top_left_offset_y_mm,
                    bottom_right_offset_x_mm,
                    bottom_right_offset_y_mm,
                    analysis.left_axis_bounds,
                    analysis.top_axis_bounds,
                    analysis.bottom_right_axis_bounds,
                    analysis.bottom_axis_bounds,
                )
                clips = split_effective_rect(effective, overlap_mm)
                for clip in clips:
                    width, height = a3_page_size_for(clip)
                    target_page = output.new_page(width=width, height=height)
                    margin = margin_mm * MM_TO_PT
                    target = fitz.Rect(margin, margin, width - margin, height - margin)
                    target_page.show_pdf_page(target, oriented, 0, clip=clip, keep_proportion=True)
            finally:
                oriented.close()
        if cancel_check():
            raise A3SplitCancelled("任务已取消")
        output.save(temporary, garbage=4, deflate=True)
        output.close()
        source.close()
        temporary.replace(output_path)
        return page_index + 1, (page_index + 1) * 2, rotated_pages
    except A3SplitCancelled:
        raise
    except A3SplitError:
        raise
    except Exception as exc:
        raise A3SplitError(str(exc)) from exc
    finally:
        if not output.is_closed:
            output.close()
        if not source.is_closed:
            source.close()
        if temporary.exists():
            temporary.unlink()


def render_page_preview(page: fitz.Page, max_pixels: int) -> bytes:
    """把页面渲染为供视觉模型分析的 PNG，限制最长边像素。"""
    longest = max(page.rect.width, page.rect.height)
    scale = max_pixels / longest if longest else 1.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
    return pixmap.tobytes("png")


def bounds_to_page_rect(bounds: NormalizedBounds, page_rect: fitz.Rect) -> fitz.Rect:
    return fitz.Rect(
        page_rect.x0 + bounds.x0 * page_rect.width,
        page_rect.y0 + bounds.y0 * page_rect.height,
        page_rect.x0 + bounds.x1 * page_rect.width,
        page_rect.y0 + bounds.y1 * page_rect.height,
    )


def rotate_normalized_bounds(bounds: NormalizedBounds, rotation: int) -> NormalizedBounds:
    """把原图标准化边界框换算到顺时针旋转后的页面坐标。"""
    if rotation == 0:
        return bounds
    if rotation == 90:
        return NormalizedBounds(1 - bounds.y1, bounds.x0, 1 - bounds.y0, bounds.x1)
    if rotation == 180:
        return NormalizedBounds(1 - bounds.x1, 1 - bounds.y1, 1 - bounds.x0, 1 - bounds.y0)
    if rotation == 270:
        return NormalizedBounds(bounds.y0, 1 - bounds.x1, bounds.y1, 1 - bounds.x0)
    raise ValueError(f"不支持的旋转角度：{rotation}")


def effective_rect_from_analysis(
    top_left_feature_bounds: NormalizedBounds,
    title_block_bounds: NormalizedBounds,
    rotation: int,
    page_rect: fitz.Rect,
    top_left_offset_x_mm: float,
    top_left_offset_y_mm: float,
    offset_x_mm: float,
    offset_y_mm: float,
    left_axis_bounds: NormalizedBounds | None = None,
    top_axis_bounds: NormalizedBounds | None = None,
    bottom_right_axis_bounds: NormalizedBounds | None = None,
    bottom_axis_bounds: NormalizedBounds | None = None,
) -> fitz.Rect:
    """用左上与右下的两个轴网交点构造最终打印范围。"""
    rotated_top_left = rotate_normalized_bounds(top_left_feature_bounds, rotation)
    rotated_title_block = rotate_normalized_bounds(title_block_bounds, rotation)
    if left_axis_bounds is not None and top_axis_bounds is not None:
        anchor_x = (top_axis_bounds.x0 + top_axis_bounds.x1) / 2
        anchor_y = (left_axis_bounds.y0 + left_axis_bounds.y1) / 2
        anchor_x, anchor_y = rotate_normalized_point(anchor_x, anchor_y, rotation)
    else:
        anchor_x, anchor_y = rotated_top_left.x0, rotated_top_left.y0
    x0 = page_rect.x0 + anchor_x * page_rect.width + top_left_offset_x_mm * MM_TO_PT
    y0 = page_rect.y0 + anchor_y * page_rect.height + top_left_offset_y_mm * MM_TO_PT
    if bottom_right_axis_bounds is not None:
        rotated_bottom_right = rotate_normalized_bounds(bottom_right_axis_bounds, rotation)
        rotated_bottom_axis = (
            rotate_normalized_bounds(bottom_axis_bounds, rotation)
            if bottom_axis_bounds is not None
            else rotated_bottom_right
        )
        right_anchor_x = (rotated_bottom_axis.x0 + rotated_bottom_axis.x1) / 2
        right_anchor_y = (rotated_bottom_right.y0 + rotated_bottom_right.y1) / 2
    else:
        right_anchor_x, right_anchor_y = rotated_title_block.x1, rotated_title_block.y1
    x1 = page_rect.x0 + right_anchor_x * page_rect.width + offset_x_mm * MM_TO_PT
    y1 = page_rect.y0 + right_anchor_y * page_rect.height + offset_y_mm * MM_TO_PT
    x0 = max(page_rect.x0, min(page_rect.x1 - 1, x0))
    y0 = max(page_rect.y0, min(page_rect.y1 - 1, y0))
    x1 = min(page_rect.x1, max(x0 + 1, x1))
    y1 = min(page_rect.y1, max(y0 + 1, y1))
    return fitz.Rect(x0, y0, x1, y1)


def rotate_normalized_point(x: float, y: float, rotation: int) -> tuple[float, float]:
    """把原图中的标准化点换算到顺时针旋转后的页面。"""
    if rotation == 0:
        return x, y
    if rotation == 90:
        return 1 - y, x
    if rotation == 180:
        return 1 - x, 1 - y
    if rotation == 270:
        return y, 1 - x
    raise ValueError(f"不支持的旋转角度：{rotation}")


def create_oriented_page_document(source: fitz.Document, page_index: int, rotation: int) -> fitz.Document:
    """创建只含一个已旋正矢量页面的内存 PDF。"""
    source_rect = source[page_index].rect
    width, height = source_rect.width, source_rect.height
    if rotation in {90, 270}:
        width, height = height, width
    oriented = fitz.open()
    page = oriented.new_page(width=width, height=height)
    page.show_pdf_page(page.rect, source, page_index, rotate=rotation, keep_proportion=True)
    return oriented


def split_effective_rect(rect: fitz.Rect, overlap_mm: float) -> tuple[fitz.Rect, fitz.Rect]:
    """把旋正后的横向有效图框拆成左右两个区域。"""
    overlap = overlap_mm * MM_TO_PT
    middle = (rect.x0 + rect.x1) / 2
    half_overlap = min(overlap / 2, rect.width / 4)
    return (
        fitz.Rect(rect.x0, rect.y0, middle + half_overlap, rect.y1),
        fitz.Rect(middle - half_overlap, rect.y0, rect.x1, rect.y1),
    )


def a3_page_size_for(clip: fitz.Rect) -> tuple[float, float]:
    portrait_width, portrait_height = A3_PORTRAIT
    return (portrait_height, portrait_width) if clip.width >= clip.height else A3_PORTRAIT
