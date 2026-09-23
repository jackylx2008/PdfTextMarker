"""Excel/CSV 关键词标注 PDF 的工作流编排。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from logging_config import get_logger

from pdf_text_marker.context import MarkPdfsSettings
from pdf_text_marker.models import FailureRecord, KeywordSpec, ProcessingResult
from pdf_text_marker.modules.html_report import write_html_report
from pdf_text_marker.modules.keyword_reader import read_excel_keywords, read_keywords
from pdf_text_marker.modules.pdf_marker import PdfProcessingError, ProcessingCancelled, mark_pdf


logger = get_logger(__name__)
ProgressCallback = Callable[[int, int, str], None]
MessageCallback = Callable[[str, str], None]
CancelCallback = Callable[[], bool]


def run(
    settings: MarkPdfsSettings,
    *,
    on_progress: ProgressCallback | None = None,
    on_message: MessageCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> ProcessingResult:
    """执行完整标注流程，并始终尝试生成 HTML 报告。"""
    progress = on_progress or (lambda _current, _total, _name: None)
    message = on_message or (lambda _level, _text: None)
    cancel_check = is_cancelled or (lambda: False)
    _validate_inputs(settings)

    started_at = datetime.now()
    keywords = _load_keywords(settings)
    pdf_files = discover_pdfs(settings.pdf_dir, settings.output_dir, settings.recursive)
    result = ProcessingResult(keyword_count=len(keywords), pdf_count=len(pdf_files))
    matched_identities: set[tuple[str, str]] = set()
    message("INFO", f"读取到 {len(keywords)} 个有效关键词，发现 {len(pdf_files)} 个 PDF")
    logger.info("读取到 %d 个有效关键词，发现 %d 个 PDF", len(keywords), len(pdf_files))

    for index, pdf_path in enumerate(pdf_files, start=1):
        if cancel_check():
            result.cancelled = True
            break
        relative = pdf_path.relative_to(settings.pdf_dir).as_posix()
        progress(index - 1, len(pdf_files), relative)
        active_specs = [item for item in keywords if _applies_to_pdf(item, pdf_path, relative)]
        if not active_specs:
            logger.debug("PDF 没有对应关键词，跳过: %s", relative)
            continue
        unique_text = list(dict.fromkeys(item.keyword for item in active_specs))
        output_path = _output_path(settings.output_dir, Path(relative))
        message("INFO", f"正在处理：{relative}")
        try:
            marked = mark_pdf(
                pdf_path,
                output_path,
                relative,
                unique_text,
                highlight_enabled=settings.highlight_enabled,
                highlight_color=settings.highlight_color,
                highlight_opacity=settings.highlight_opacity,
                border_enabled=settings.border_enabled,
                border_color=settings.border_color,
                border_width=settings.border_width,
                box_aspect_ratio=settings.box_aspect_ratio,
                box_size=settings.box_size,
                box_scale=settings.box_scale,
                is_cancelled=cancel_check,
            )
        except ProcessingCancelled:
            result.cancelled = True
            message("WARNING", "正在安全取消任务")
            break
        except PdfProcessingError as exc:
            reason = str(exc)
            result.failures.append(FailureRecord(pdf_path.name, relative, reason))
            message("ERROR", f"处理失败：{relative}：{reason}")
            logger.exception("处理 PDF 失败: %s", relative)
            result.processed_pdf_count += 1
            progress(index, len(pdf_files), relative)
            continue

        result.processed_pdf_count += 1
        result.matches.extend(marked.matches)
        result.total_match_count += marked.total_matches
        result.skipped_image_only_pages += marked.image_only_pages
        if marked.output_path:
            result.output_files.append(marked.output_path)
            result.output_pdf_count += 1
            matched_words = {record.keyword.casefold() for record in marked.matches}
            matched_identities.update(item.identity for item in active_specs if item.keyword.casefold() in matched_words)
            message("SUCCESS", f"已输出：{marked.output_path.name}，匹配 {marked.total_matches} 处")
        else:
            message("WARNING", f"未找到关键词：{relative}")
        progress(index, len(pdf_files), relative)

    result.unmatched_keywords = [item for item in keywords if item.identity not in matched_identities]
    report_path = settings.output_dir / settings.report_filename
    result.report_path = write_html_report(result, report_path, started_at, datetime.now())
    final_state = "已取消" if result.cancelled else "处理完成"
    message(
        "INFO",
        f"{final_state}：输出 {result.output_pdf_count} 个 PDF，匹配 {result.total_match_count} 处，"
        f"失败 {len(result.failures)} 个",
    )
    logger.info(
        "%s：输出 %d 个 PDF，匹配 %d 处，失败 %d 个",
        final_state,
        result.output_pdf_count,
        result.total_match_count,
        len(result.failures),
    )
    return result


def _validate_inputs(settings: MarkPdfsSettings) -> None:
    if not settings.keyword_file.is_file():
        raise FileNotFoundError(f"关键词文件不存在: {settings.keyword_file}")
    if not settings.pdf_dir.is_dir():
        raise NotADirectoryError(f"PDF 目录不存在: {settings.pdf_dir}")


def _load_keywords(settings: MarkPdfsSettings) -> list[KeywordSpec]:
    is_excel = settings.keyword_file.suffix.casefold() == ".xlsx"
    has_single_source_override = bool(settings.sheet_name or settings.keyword_column)
    if is_excel and settings.excel_sources and not has_single_source_override:
        return read_excel_keywords(
            settings.keyword_file,
            settings.excel_sources,
            default_pdf_name_column=settings.pdf_name_column,
        )
    return read_keywords(
        settings.keyword_file,
        sheet_name=settings.sheet_name,
        keyword_column=settings.keyword_column,
        pdf_name_column=settings.pdf_name_column,
    )


def discover_pdfs(pdf_dir: Path, output_dir: Path, recursive: bool) -> list[Path]:
    iterator = pdf_dir.rglob("*.pdf") if recursive else pdf_dir.glob("*.pdf")
    output_resolved = output_dir.resolve()
    files = []
    for path in iterator:
        resolved = path.resolve()
        if resolved == output_resolved or output_resolved in resolved.parents:
            continue
        if path.stem.casefold().endswith("_marked"):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix().casefold())


def _applies_to_pdf(keyword: KeywordSpec, pdf_path: Path, relative_pdf: str) -> bool:
    if not keyword.pdf_name:
        return True
    target = keyword.pdf_name.replace("\\", "/").strip().casefold()
    relative = relative_pdf.casefold()
    relative_without_suffix = str(Path(relative_pdf).with_suffix("")).replace("\\", "/").casefold()
    target_name = Path(target).name.casefold()
    candidates = {
        pdf_path.name.casefold(),
        pdf_path.stem.casefold(),
        relative,
        relative_without_suffix,
    }
    return target in candidates or target_name in {pdf_path.name.casefold(), pdf_path.stem.casefold()}


def _output_path(output_dir: Path, relative_pdf: Path) -> Path:
    return output_dir / relative_pdf.parent / f"{relative_pdf.stem}_marked.pdf"
