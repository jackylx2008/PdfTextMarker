"""PDF 关键词批量标注工具

用途：
  从 XLSX 或 CSV 读取关键词，在指定目录的可搜索 PDF 中查找全部匹配位置，
  添加黄色底纹和红色边框，并生成 HTML 汇总报告。

配置文件：
  默认读取根目录 config.yaml；common.env 保存本机私有路径并覆盖 YAML 默认值。

可选参数：
  --keyword-file      XLSX 或 CSV 关键词文件。
  --pdf-dir           PDF 输入目录。
  --output-dir        标注 PDF 和 HTML 报告的输出目录。
  --sheet             Excel 工作表名。
  --keyword-column    关键词列名。
  --pdf-name-column   可选的目标 PDF 文件名列。
  --recursive         递归搜索 PDF，使用 --no-recursive 可关闭。

示例：
  python mark_pdfs.py --keyword-file input/keywords.xlsx --pdf-dir input

输出：
  仅为有匹配结果的 PDF 生成 *_marked.pdf，并输出 pdf_text_marker_report.html。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from logging_config import configure_utf8_stdio, get_logger, setup_logger
from pdf_text_marker.config_loader import resolve_path
from pdf_text_marker.context import bootstrap_context
from pdf_text_marker.flows.mark_pdfs_flow import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-file", help="公开 YAML 配置文件，默认使用根目录 config.yaml")
    parser.add_argument("--keyword-file", help="XLSX 或 CSV 关键词文件")
    parser.add_argument("--pdf-dir", help="PDF 输入目录")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--sheet", help="Excel 工作表名")
    parser.add_argument("--keyword-column", help="关键词列名")
    parser.add_argument("--pdf-name-column", help="目标 PDF 文件名列")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=None, help="是否递归搜索 PDF")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        context = bootstrap_context(__file__, args.config_file)
        setup_logger(context.log_level)
        logger = get_logger(__name__)
        settings = context.mark_pdfs.with_overrides(
            keyword_file=resolve_path(PROJECT_ROOT, args.keyword_file) if args.keyword_file else None,
            pdf_dir=resolve_path(PROJECT_ROOT, args.pdf_dir) if args.pdf_dir else None,
            output_dir=resolve_path(PROJECT_ROOT, args.output_dir) if args.output_dir else None,
            sheet_name=args.sheet,
            keyword_column=args.keyword_column,
            pdf_name_column=args.pdf_name_column,
            recursive=args.recursive,
        )
        result = run(
            settings,
            on_progress=lambda current, total, name: logger.info("进度 %d/%d：%s", current, total, name),
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if result.cancelled:
            return 130
        return 1 if result.failures else 0
    except Exception as exc:
        if "logger" in locals():
            logger.exception("任务执行失败")
        else:
            print(f"任务执行失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
