"""建筑图纸 AI 识别与 A3 拆分工具。

用途：
  调用本地 OpenAI 兼容视觉服务识别 PDF 页面方向、左上轴网锚点和右下图签，
  旋正有效图框后左右拆分，并排版为两个标准 A3 页面。

配置文件：
  默认读取 config.yaml 的 flows.a3_split；common.env 保存本机路径和 API Key。
  命令行参数可临时覆盖输入输出目录、模型、参考图、偏移量和打印参数。

示例：
  python split_a3_pdfs.py
  python split_a3_pdfs.py --input-dir path/to/pdfs --output-dir path/to/a3_output

输出：
  在输出目录生成 <原文件名>_a3.pdf，并在控制台和统一日志中汇总处理结果。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from logging_config import configure_utf8_stdio, setup_logger
from pdf_text_marker.config_loader import resolve_path
from pdf_text_marker.context import bootstrap_context
from pdf_text_marker.flows.a3_split_flow import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-file")
    parser.add_argument("--input-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--model")
    parser.add_argument("--api-key")
    parser.add_argument("--base-url")
    parser.add_argument("--top-left-reference-image")
    parser.add_argument("--bottom-right-reference-image")
    parser.add_argument("--top-left-offset-x-mm", type=float)
    parser.add_argument("--top-left-offset-y-mm", type=float)
    parser.add_argument("--bottom-right-offset-x-mm", type=float)
    parser.add_argument("--bottom-right-offset-y-mm", type=float)
    parser.add_argument("--overlap-mm", type=float)
    parser.add_argument("--margin-mm", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    context = bootstrap_context(__file__, args.config_file)
    setup_logger(context.log_level)
    settings = context.a3_split.with_overrides(
        input_dir=resolve_path(context.project_root, args.input_dir) if args.input_dir else None,
        output_dir=resolve_path(context.project_root, args.output_dir) if args.output_dir else None,
        ai_model=args.model,
        ai_api_key=args.api_key,
        ai_base_url=args.base_url,
        top_left_reference_image=(
            resolve_path(context.project_root, args.top_left_reference_image)
            if args.top_left_reference_image
            else None
        ),
        bottom_right_reference_image=(
            resolve_path(context.project_root, args.bottom_right_reference_image)
            if args.bottom_right_reference_image
            else None
        ),
        top_left_offset_x_mm=args.top_left_offset_x_mm,
        top_left_offset_y_mm=args.top_left_offset_y_mm,
        bottom_right_offset_x_mm=args.bottom_right_offset_x_mm,
        bottom_right_offset_y_mm=args.bottom_right_offset_y_mm,
        overlap_mm=args.overlap_mm,
        margin_mm=args.margin_mm,
    )
    result = run(
        settings,
        on_message=lambda level, text: print(f"[{level}] {text}"),
        on_progress=lambda current, total, name: print(f"[{current}/{total}] {name}"),
    )
    print(
        f"完成：输出 {result.output_pdf_count} 个 PDF，{result.output_page_count} 张 A3，"
        f"失败 {len(result.failures)} 个"
    )
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
