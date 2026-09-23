"""在带文字层的 PDF 中查找并标注关键词。"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import fitz

from pdf_text_marker.models import MatchRecord


class PdfProcessingError(RuntimeError):
    """单个 PDF 无法处理。"""


class ProcessingCancelled(RuntimeError):
    """用户请求安全取消当前任务。"""


@dataclass(frozen=True, slots=True)
class PdfMarkResult:
    """单个 PDF 的处理结果。"""

    matches: tuple[MatchRecord, ...]
    total_matches: int
    image_only_pages: int
    output_path: Path | None


def mark_pdf(
    source_path: Path,
    output_path: Path,
    relative_pdf: str,
    keywords: Sequence[str],
    *,
    highlight_enabled: bool = True,
    highlight_color: tuple[float, float, float] = (1.0, 1.0, 0.0),
    highlight_opacity: float = 0.35,
    border_enabled: bool = True,
    border_color: tuple[float, float, float] = (1.0, 0.0, 0.0),
    border_width: float = 1.25,
    box_aspect_ratio: float = 4.0,
    box_size: float = 40.0,
    box_scale: float = 1.0,
    is_cancelled: Callable[[], bool] | None = None,
) -> PdfMarkResult:
    """标注一个 PDF；没有匹配时不生成输出文件。"""
    cancel_check = is_cancelled or (lambda: False)
    records: list[MatchRecord] = []
    total_matches = 0
    image_only_pages = 0
    unique_keywords = list(dict.fromkeys(keyword for keyword in keywords if keyword))
    try:
        document = fitz.open(source_path)
    except Exception as exc:
        raise PdfProcessingError(f"无法打开 PDF: {exc}") from exc

    try:
        if document.needs_pass:
            raise PdfProcessingError("PDF 已加密且需要密码")
        if document.page_count == 0:
            raise PdfProcessingError("PDF 不包含页面")

        for page_index in range(document.page_count):
            if cancel_check():
                raise ProcessingCancelled("任务已取消")
            page = document[page_index]
            if not page.get_text("text").strip():
                image_only_pages += 1
                continue
            for keyword in unique_keywords:
                if cancel_check():
                    raise ProcessingCancelled("任务已取消")
                quads = page.search_for(keyword, quads=True)
                if not quads:
                    continue
                for quad in quads:
                    _add_annotations(
                        page,
                        quad,
                        highlight_enabled=highlight_enabled,
                        highlight_color=highlight_color,
                        highlight_opacity=highlight_opacity,
                        border_enabled=border_enabled,
                        border_color=border_color,
                        border_width=border_width,
                        box_aspect_ratio=box_aspect_ratio,
                        box_size=box_size,
                        box_scale=box_scale,
                    )
                count = len(quads)
                total_matches += count
                records.append(
                    MatchRecord(
                        keyword=keyword,
                        pdf_name=source_path.name,
                        relative_pdf=relative_pdf,
                        page_number=page_index + 1,
                        match_count=count,
                    )
                )

        written_path: Path | None = None
        if total_matches:
            if cancel_check():
                raise ProcessingCancelled("任务已取消")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            written_path = _save_atomically(document, output_path)
        return PdfMarkResult(
            matches=tuple(records),
            total_matches=total_matches,
            image_only_pages=image_only_pages,
            output_path=written_path,
        )
    except (PdfProcessingError, ProcessingCancelled):
        raise
    except Exception as exc:
        raise PdfProcessingError(str(exc)) from exc
    finally:
        document.close()


def _add_annotations(
    page: fitz.Page,
    quad: fitz.Quad,
    *,
    highlight_enabled: bool,
    highlight_color: tuple[float, float, float],
    highlight_opacity: float,
    border_enabled: bool,
    border_color: tuple[float, float, float],
    border_width: float,
    box_aspect_ratio: float,
    box_size: float,
    box_scale: float,
) -> None:
    box = centered_page_annotation_rect(
        page,
        quad.rect,
        aspect_ratio=box_aspect_ratio,
        size=box_size,
        scale=box_scale,
    )
    if highlight_enabled:
        fill = page.add_rect_annot(box)
        fill.set_colors(stroke=highlight_color, fill=highlight_color)
        fill.set_border(width=0)
        fill.set_opacity(highlight_opacity)
        fill.update()

    if not border_enabled:
        return
    border = page.add_rect_annot(box)
    border.set_colors(stroke=border_color)
    border.set_border(width=border_width)
    border.set_opacity(1.0)
    border.update()


def centered_annotation_rect(
    keyword_rect: fitz.Rect,
    page_rect: fitz.Rect,
    *,
    aspect_ratio: float,
    size: float,
    scale: float,
) -> fitz.Rect:
    """以关键词中心生成指定尺寸的矩形，并整体平移到页面范围内。"""
    if aspect_ratio <= 0 or size <= 0 or scale <= 0:
        raise PdfProcessingError("标注长宽比、大小和放大倍数必须大于 0")
    width = size * scale
    height = width / aspect_ratio
    if width > page_rect.width or height > page_rect.height:
        raise PdfProcessingError("标注矩形大于 PDF 页面，请减小大小或放大倍数")
    center = (keyword_rect.tl + keyword_rect.br) / 2
    x0 = min(max(center.x - width / 2, page_rect.x0), page_rect.x1 - width)
    y0 = min(max(center.y - height / 2, page_rect.y0), page_rect.y1 - height)
    box = fitz.Rect(x0, y0, x0 + width, y0 + height)
    if not box.is_valid or box.is_empty or box.is_infinite:
        raise PdfProcessingError("关键词坐标无效，无法绘制标注矩形")
    return box


def centered_page_annotation_rect(
    page: fitz.Page,
    keyword_rect: fitz.Rect,
    *,
    aspect_ratio: float,
    size: float,
    scale: float,
) -> fitz.Rect:
    """按用户看到的页面方向计算矩形，再转换为 PDF 未旋转注释坐标。"""
    visible_keyword_rect = keyword_rect * page.rotation_matrix
    visible_box = centered_annotation_rect(
        visible_keyword_rect,
        page.rect,
        aspect_ratio=aspect_ratio,
        size=size,
        scale=scale,
    )
    return visible_box * page.derotation_matrix


def _save_atomically(document: fitz.Document, output_path: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}_",
        suffix=".pdf.tmp",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        document.save(temporary_path, garbage=4, deflate=True)
        os.replace(temporary_path, output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_path
