from __future__ import annotations

from pdf_text_marker.modules.local_vision_client import (
    NormalizedBounds,
    _axis_anchor_is_valid,
)


def test_axis_anchor_inside_title_block_is_rejected() -> None:
    valid = _axis_anchor_is_valid(
        NormalizedBounds(0.04, 0.10, 0.06, 0.13),
        NormalizedBounds(0.92, 0.10, 0.94, 0.13),
        NormalizedBounds(0.89, 0.02, 0.99, 0.98),
    )

    assert not valid
