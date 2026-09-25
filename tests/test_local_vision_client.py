from __future__ import annotations

import json

import fitz
import pytest

from pdf_text_marker.modules.local_vision_client import (
    LocalVisionClient,
    NormalizedBounds,
    _axis_anchor_is_valid,
    _anchors_form_valid_rect,
    _bottom_axis_pair_is_valid,
    _bottom_right_axis_is_valid,
    _image_relative_bounds,
    _optional_bounds,
    _scale_region_bounds,
    _scale_right_crop_bounds,
    _suppress_colored_pixels,
)


def test_axis_anchor_inside_title_block_is_rejected() -> None:
    valid = _axis_anchor_is_valid(
        NormalizedBounds(0.04, 0.10, 0.06, 0.13),
        NormalizedBounds(0.92, 0.10, 0.94, 0.13),
        NormalizedBounds(0.89, 0.02, 0.99, 0.98),
    )

    assert not valid


def test_bottom_right_axis_rejects_page_bottom_false_positive() -> None:
    title = NormalizedBounds(0.90, 0.02, 0.99, 0.99)
    assert _bottom_right_axis_is_valid(NormalizedBounds(0.92, 0.80, 0.94, 0.84), 0, title)
    assert not _bottom_right_axis_is_valid(NormalizedBounds(0.92, 0.88, 0.94, 0.90), 0, title)
    assert not _bottom_right_axis_is_valid(NormalizedBounds(0.93, 0.96, 0.95, 0.99), 0, title)


def test_bottom_numeric_axis_must_be_left_and_below_right_letter_axis() -> None:
    right_axis = NormalizedBounds(0.92, 0.80, 0.94, 0.84)

    assert _bottom_axis_pair_is_valid(right_axis, NormalizedBounds(0.86, 0.88, 0.88, 0.92), 0)
    assert not _bottom_axis_pair_is_valid(right_axis, NormalizedBounds(0.95, 0.88, 0.97, 0.92), 0)
    assert not _bottom_axis_pair_is_valid(right_axis, NormalizedBounds(0.86, 0.74, 0.88, 0.78), 0)


def test_right_crop_coordinates_are_mapped_back_to_full_page() -> None:
    bounds = _scale_right_crop_bounds(NormalizedBounds(0.80, 0.70, 0.90, 0.75), 0.4, 0.88)

    assert (bounds.x0, bounds.y0, bounds.x1, bounds.y1) == (0.92, 0.616, 0.96, 0.66)


def test_bottom_region_coordinates_are_mapped_back_to_full_page() -> None:
    bounds = _scale_region_bounds(NormalizedBounds(0.5, 0.5, 0.75, 0.75), (0.5, 0.7, 0.94, 0.98))

    assert (bounds.x0, bounds.y0, bounds.x1, bounds.y1) == pytest.approx((0.72, 0.84, 0.83, 0.91))


def test_left_anchor_must_be_above_and_left_of_right_anchor() -> None:
    right = NormalizedBounds(0.90, 0.70, 0.92, 0.74)
    assert _anchors_form_valid_rect(
        NormalizedBounds(0.03, 0.10, 0.05, 0.13),
        NormalizedBounds(0.12, 0.01, 0.14, 0.04),
        right,
        0,
    )
    assert not _anchors_form_valid_rect(
        NormalizedBounds(0.03, 0.93, 0.05, 0.96),
        NormalizedBounds(0.12, 0.01, 0.14, 0.04),
        right,
        0,
    )


def test_slight_ai_coordinate_overshoot_is_clamped() -> None:
    bounds = _optional_bounds([-0.01, 0.2, 1.02, 0.8], "bbox")

    assert bounds == NormalizedBounds(0.0, 0.2, 1.0, 0.8)


def test_crop_pixel_coordinates_are_normalized_when_inside_image() -> None:
    crop = fitz.Pixmap(fitz.csRGB, 200, 100, bytes((255, 255, 255)) * 200 * 100, False).tobytes("png")

    bounds = _image_relative_bounds([20, 10, 40, 30], "axis_bbox", crop)

    assert bounds == NormalizedBounds(0.1, 0.1, 0.2, 0.3)


def test_colored_pixels_are_removed_but_black_ink_is_preserved() -> None:
    source = fitz.Pixmap(
        fitz.csRGB,
        3,
        1,
        bytes((0, 0, 0, 230, 20, 20, 128, 128, 128)),
        False,
    ).tobytes("png")

    filtered = fitz.Pixmap(_suppress_colored_pixels(source))

    assert tuple(filtered.samples[0:3]) == (0, 0, 0)
    assert tuple(filtered.samples[3:6]) == (255, 255, 255)
    assert tuple(filtered.samples[6:9]) == (128, 128, 128)


def test_full_page_axes_are_refined_as_two_axis_intersection() -> None:
    class RecordingClient(LocalVisionClient):
        refined = False

        def _request_json(self, _url: str, _payload: dict[str, object] | None = None) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "bbox": [0.02, 0.02, 0.98, 0.98],
                                    "left_axis_bbox": [0.03, 0.10, 0.05, 0.13],
                                    "top_axis_bbox": [0.12, 0.02, 0.14, 0.05],
                                    "top_left_feature_bbox": [0.03, 0.02, 0.14, 0.13],
                                    "bottom_right_axis_bbox": [0.90, 0.70, 0.92, 0.74],
                                    "title_block_bbox": [0.88, 0.02, 0.99, 0.99],
                                    "rotation_clockwise": 0,
                                }
                            )
                        }
                    }
                ]
            }

        def _detect_bottom_right_axes_in_crop(
            self,
            _png_bytes: bytes,
            _reference_png: bytes | None,
            crop_ratio: float = 0.40,
            height_ratio: float = 0.94,
        ) -> tuple[NormalizedBounds, NormalizedBounds]:
            self.refined = True
            return (
                NormalizedBounds(0.90, 0.68, 0.92, 0.72),
                NormalizedBounds(0.84, 0.80, 0.86, 0.84),
            )

    client = RecordingClient("http://127.0.0.1:8080/v1", model="test")
    analysis = client.detect_page_layout(b"png", 1, b"left", b"right")

    assert client.refined
    assert analysis.bottom_right_axis_bounds == NormalizedBounds(0.90, 0.68, 0.92, 0.72)
    assert analysis.bottom_axis_bounds == NormalizedBounds(0.84, 0.80, 0.86, 0.84)
