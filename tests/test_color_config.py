"""标注颜色配置测试。"""

import pytest

from pdf_text_marker.config_loader import ConfigurationError
from pdf_text_marker.context import color_to_hex, parse_hex_color


def test_hex_color_round_trip() -> None:
    assert color_to_hex(parse_hex_color("#12ABEF")) == "#12ABEF"
    assert color_to_hex(parse_hex_color("#ffff00")) == "#FFFF00"


@pytest.mark.parametrize("value", ["FFFF00", "#FFF", "#GG0000", ""])
def test_invalid_hex_color(value: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_hex_color(value)
