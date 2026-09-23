"""关键词中心矩形的几何计算测试。"""

import fitz
import pytest

from pdf_text_marker.modules.pdf_marker import (
    PdfProcessingError,
    centered_annotation_rect,
    centered_page_annotation_rect,
)


def test_centered_rectangle_uses_size_ratio_and_scale() -> None:
    keyword = fitz.Rect(90, 45, 110, 55)
    page = fitz.Rect(0, 0, 200, 100)

    box = centered_annotation_rect(keyword, page, aspect_ratio=4, size=40, scale=2)

    assert box.width == pytest.approx(80)
    assert box.height == pytest.approx(20)
    assert (box.x0 + box.x1) / 2 == pytest.approx(100)
    assert (box.y0 + box.y1) / 2 == pytest.approx(50)


def test_centered_rectangle_moves_inside_page_without_resizing() -> None:
    keyword = fitz.Rect(0, 0, 4, 4)
    page = fitz.Rect(0, 0, 200, 100)

    box = centered_annotation_rect(keyword, page, aspect_ratio=2, size=40, scale=1)

    assert box == fitz.Rect(0, 0, 40, 20)


def test_centered_rectangle_rejects_page_overflow() -> None:
    with pytest.raises(PdfProcessingError):
        centered_annotation_rect(
            fitz.Rect(10, 10, 20, 20),
            fitz.Rect(0, 0, 100, 100),
            aspect_ratio=1,
            size=120,
            scale=1,
        )


def test_rotated_page_keeps_visual_aspect_ratio() -> None:
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    page.set_rotation(90)

    annotation_box = centered_page_annotation_rect(
        page,
        fitz.Rect(90, 45, 110, 55),
        aspect_ratio=4,
        size=40,
        scale=2,
    )
    visible_box = annotation_box * page.rotation_matrix

    assert visible_box.width == pytest.approx(80)
    assert visible_box.height == pytest.approx(20)
    document.close()
