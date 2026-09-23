from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from pdf_text_marker.flows.a3_split_flow import discover_split_pdfs
from pdf_text_marker.modules.a3_splitter import (
    A3_PORTRAIT,
    MM_TO_PT,
    effective_rect_from_analysis,
    rotate_normalized_bounds,
    rotate_normalized_point,
    split_effective_rect,
    split_pdf_to_a3,
)
from pdf_text_marker.modules.a3_diagnostics import create_bounds_diagnostic
from pdf_text_marker.modules.local_vision_client import NormalizedBounds, PageLayoutAnalysis, _extract_json


class FakeVisionClient:
    def __init__(self, rotation: int = 0) -> None:
        self.rotation = rotation

    def detect_page_layout(
        self,
        _png_bytes: bytes,
        _page_number: int,
        _top_left_reference_png: bytes | None = None,
        _bottom_right_reference_png: bytes | None = None,
    ) -> PageLayoutAnalysis:
        bounds = NormalizedBounds(0.05, 0.05, 0.95, 0.95)
        return PageLayoutAnalysis(bounds, self.rotation, bounds, bounds)


def create_drawing_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=1190.0, height=842.0)
    frame = fitz.Rect(30, 30, 1160, 812)
    page.draw_rect(frame, width=2)
    page.insert_text((100, 400), "LEFT HALF", fontsize=30)
    page.insert_text((700, 400), "RIGHT HALF", fontsize=30)
    document.save(path)
    document.close()


def test_split_pdf_to_two_a3_pages_preserves_text(tmp_path: Path) -> None:
    source = tmp_path / "drawing.pdf"
    output = tmp_path / "drawing_a3.pdf"
    create_drawing_pdf(source)

    source_pages, output_pages, rotated_pages = split_pdf_to_a3(source, output, FakeVisionClient())

    assert (source_pages, output_pages, rotated_pages) == (1, 2, 0)
    result = fitz.open(output)
    assert result.page_count == 2
    expected_width, expected_height = A3_PORTRAIT
    assert result[0].rect.width == pytest.approx(expected_width, abs=0.1)
    assert result[0].rect.height == pytest.approx(expected_height, abs=0.1)
    assert "LEFT HALF" in result[0].get_text()
    assert "RIGHT HALF" in result[1].get_text()
    result.close()


def test_vertical_split_can_add_overlap() -> None:
    left, right = split_effective_rect(fitz.Rect(0, 0, 1000, 700), 10)

    assert left.x1 > 500
    assert right.x0 < 500
    assert left.x1 - right.x0 == pytest.approx(10 * MM_TO_PT)


def test_extract_json_accepts_markdown_fence() -> None:
    assert _extract_json('```json\n{"bbox":[0.1,0.2,0.9,0.8]}\n```')["bbox"] == [0.1, 0.2, 0.9, 0.8]


def test_extract_json_uses_first_object_when_model_repeats_answer() -> None:
    assert _extract_json('{"left_axis_bbox":[0,0,1,1]}\n{"note":"duplicate"}')["left_axis_bbox"] == [0, 0, 1, 1]


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (0.1, 0.2, 0.8, 0.9)),
        (90, (0.1, 0.1, 0.8, 0.8)),
        (180, (0.2, 0.1, 0.9, 0.8)),
        (270, (0.2, 0.2, 0.9, 0.9)),
    ],
)
def test_rotate_normalized_bounds(rotation: int, expected: tuple[float, ...]) -> None:
    rotated = rotate_normalized_bounds(NormalizedBounds(0.1, 0.2, 0.8, 0.9), rotation)

    assert (rotated.x0, rotated.y0, rotated.x1, rotated.y1) == pytest.approx(expected)


def test_bottom_right_offset_expands_effective_print_range() -> None:
    rect = effective_rect_from_analysis(
        NormalizedBounds(0.1, 0.1, 0.2, 0.2),
        NormalizedBounds(0.65, 0.6, 0.85, 0.8),
        0,
        fitz.Rect(0, 0, 1000, 700),
        top_left_offset_x_mm=0,
        top_left_offset_y_mm=0,
        offset_x_mm=10,
        offset_y_mm=5,
    )

    assert rect.x0 == pytest.approx(100)
    assert rect.y0 == pytest.approx(70)
    assert rect.x1 == pytest.approx(850 + 10 * MM_TO_PT)
    assert rect.y1 == pytest.approx(560 + 5 * MM_TO_PT)


def test_l_and_1_axis_centers_form_top_left_anchor() -> None:
    rect = effective_rect_from_analysis(
        NormalizedBounds(0.01, 0.01, 0.05, 0.05),
        NormalizedBounds(0.7, 0.6, 0.9, 0.9),
        0,
        fitz.Rect(0, 0, 1000, 800),
        0,
        0,
        0,
        0,
        left_axis_bounds=NormalizedBounds(0.02, 0.18, 0.04, 0.22),
        top_axis_bounds=NormalizedBounds(0.13, 0.02, 0.17, 0.06),
    )

    assert rect.x0 == pytest.approx(150)
    assert rect.y0 == pytest.approx(160)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [(0, (0.2, 0.3)), (90, (0.7, 0.2)), (180, (0.8, 0.7)), (270, (0.3, 0.8))],
)
def test_rotate_normalized_point(rotation: int, expected: tuple[float, float]) -> None:
    assert rotate_normalized_point(0.2, 0.3, rotation) == pytest.approx(expected)


def test_portrait_page_is_rotated_before_left_right_split(tmp_path: Path) -> None:
    source = tmp_path / "portrait.pdf"
    output = tmp_path / "portrait_a3.pdf"
    document = fitz.open()
    page = document.new_page(width=842, height=1190)
    page.insert_text((100, 250), "ORIGINAL TOP", fontsize=28)
    page.insert_text((100, 950), "ORIGINAL BOTTOM", fontsize=28)
    document.save(source)
    document.close()

    counts = split_pdf_to_a3(source, output, FakeVisionClient(rotation=90))

    assert counts == (1, 2, 1)
    result = fitz.open(output)
    combined_text = "\n".join(page.get_text() for page in result)
    assert "ORIGINAL TOP" in combined_text
    assert "ORIGINAL BOTTOM" in combined_text
    assert result[0].rect.width == pytest.approx(A3_PORTRAIT[0], abs=0.1)
    assert result[0].rect.height == pytest.approx(A3_PORTRAIT[1], abs=0.1)
    result.close()


def test_discovery_includes_marked_pdfs_but_excludes_a3_outputs(tmp_path: Path) -> None:
    input_dir = tmp_path / "output"
    a3_output = input_dir / "a3_split"
    input_dir.mkdir()
    a3_output.mkdir()
    marked = input_dir / "drawing_marked.pdf"
    plain = input_dir / "drawing.pdf"
    already_split = input_dir / "drawing_a3.pdf"
    generated = a3_output / "generated.pdf"
    for path in (marked, plain, already_split, generated):
        path.write_bytes(b"test")

    discovered = discover_split_pdfs(input_dir, a3_output, recursive=True)

    assert discovered == [plain, marked]


def test_bounds_diagnostic_creates_annotated_rotated_png(tmp_path: Path) -> None:
    source = tmp_path / "portrait.pdf"
    create_drawing_pdf(source)
    with fitz.open(source) as document:
        page = document[0]
        preview = page.get_pixmap(alpha=False).tobytes("png")
        source_rect = fitz.Rect(page.rect)
    analysis = PageLayoutAnalysis(
        NormalizedBounds(0.05, 0.05, 0.95, 0.95),
        90,
        NormalizedBounds(0.05, 0.10, 0.15, 0.20),
        NormalizedBounds(0.70, 0.60, 0.90, 0.90),
        NormalizedBounds(0.05, 0.10, 0.08, 0.14),
        NormalizedBounds(0.12, 0.04, 0.16, 0.08),
    )
    output = tmp_path / "diagnostic.png"

    create_bounds_diagnostic(preview, source_rect, analysis, output)

    assert output.is_file()
    pixmap = fitz.Pixmap(output)
    assert (pixmap.width, pixmap.height) == (842, 1190)
    assert pixmap.samples != fitz.Pixmap(preview).samples
