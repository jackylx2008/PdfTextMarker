"""A3 图框 AI 识别诊断工具。

用途：
  调用本地视觉服务分析单个 PDF 页面，在旋正后的预览图中标出轴网圆圈、
  左上交点、右下图签、最终打印范围和左右 A3 分割线。

配置文件：
  默认读取 config.yaml 和私有 common.env；API Key 只从配置加载，不接受界面输入。

必填参数：
  pdf              需要诊断的 PDF 文件。

可选参数：
  --page           页码，从 1 开始，默认 1。
  --output         PNG 输出路径；默认写入 output/diagnostics/。
  --config-file    自定义公开配置文件。

示例：
  python diagnose_a3_bounds.py "path/to/sample.pdf" --page 1

输出：
  生成带彩色边界和坐标图例的 PNG，并在控制台打印本地模型识别结果。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import fitz

from logging_config import configure_utf8_stdio, setup_logger
from pdf_text_marker.context import bootstrap_context
from pdf_text_marker.modules.a3_diagnostics import create_bounds_diagnostic
from pdf_text_marker.modules.a3_splitter import render_page_preview
from pdf_text_marker.modules.local_vision_client import LocalVisionClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", help="需要诊断的 PDF 文件")
    parser.add_argument("--page", type=int, default=1, help="页码，从 1 开始")
    parser.add_argument("--output", help="输出 PNG；默认放到 output/diagnostics")
    parser.add_argument("--config-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    context = bootstrap_context(__file__, args.config_file)
    setup_logger(context.log_level)
    settings = context.a3_split
    pdf_path = Path(args.pdf).resolve()
    output_path = (
        Path(args.output).resolve()
        if args.output
        else context.project_root / "output" / "diagnostics" / f"{pdf_path.stem}_page_{args.page}_bounds.png"
    )
    if not settings.top_left_reference_image.is_file():
        raise SystemExit(f"左上角参考图不存在：{settings.top_left_reference_image}")
    if not settings.bottom_right_reference_image.is_file():
        raise SystemExit(f"右下角参考图不存在：{settings.bottom_right_reference_image}")

    client = LocalVisionClient(
        settings.ai_base_url,
        settings.ai_model,
        settings.ai_api_key,
    )
    with fitz.open(pdf_path) as document:
        if args.page < 1 or args.page > document.page_count:
            raise SystemExit(f"页码超出范围：1-{document.page_count}")
        page = document[args.page - 1]
        page_rect = fitz.Rect(page.rect)
        preview = render_page_preview(page, settings.preview_max_pixels)
    model = client.check_ready()
    analysis = client.detect_page_layout(
        preview,
        args.page,
        settings.top_left_reference_image.read_bytes(),
        settings.bottom_right_reference_image.read_bytes(),
    )
    create_bounds_diagnostic(
        preview,
        page_rect,
        analysis,
        output_path,
        top_left_offset_x_mm=settings.top_left_offset_x_mm,
        top_left_offset_y_mm=settings.top_left_offset_y_mm,
        bottom_right_offset_x_mm=settings.bottom_right_offset_x_mm,
        bottom_right_offset_y_mm=settings.bottom_right_offset_y_mm,
    )
    print(f"模型：{model}")
    print(f"顺时针旋转：{analysis.rotation}°")
    print(f"左上 L/1：{analysis.top_left_feature_bounds}")
    print(f"L 轴圆圈：{analysis.left_axis_bounds}")
    print(f"1 轴圆圈：{analysis.top_axis_bounds}")
    print(f"右下图签：{analysis.title_block_bounds}")
    print(f"诊断截图：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
