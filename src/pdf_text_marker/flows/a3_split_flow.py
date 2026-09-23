"""AI 图框识别和 A3 分页工作流。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from logging_config import get_logger
from pdf_text_marker.context import A3SplitSettings
from pdf_text_marker.models import A3SplitFailure, A3SplitResult
from pdf_text_marker.modules.a3_splitter import A3SplitCancelled, A3SplitError, split_pdf_to_a3
from pdf_text_marker.modules.local_vision_client import LocalVisionClient, VisionServiceError


logger = get_logger(__name__)
ProgressCallback = Callable[[int, int, str], None]
MessageCallback = Callable[[str, str], None]
CancelCallback = Callable[[], bool]


def run(
    settings: A3SplitSettings,
    *,
    on_progress: ProgressCallback | None = None,
    on_message: MessageCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    client: LocalVisionClient | None = None,
) -> A3SplitResult:
    progress = on_progress or (lambda _current, _total, _name: None)
    message = on_message or (lambda _level, _text: None)
    cancel_check = is_cancelled or (lambda: False)
    if not settings.input_dir.is_dir():
        raise NotADirectoryError(f"PDF 输入目录不存在：{settings.input_dir}")
    references = (
        ("左上角 L/1 轴网", settings.top_left_reference_image),
        ("右下角图签", settings.bottom_right_reference_image),
    )
    for label, path in references:
        if not path.is_file():
            raise FileNotFoundError(f"{label}定位参考图不存在：{path}")
        if path.suffix.casefold() != ".png":
            raise ValueError(f"{label}定位参考图必须是 PNG 文件")
    top_left_reference_png = settings.top_left_reference_image.read_bytes()
    bottom_right_reference_png = settings.bottom_right_reference_image.read_bytes()
    pdfs = discover_split_pdfs(settings.input_dir, settings.output_dir, settings.recursive)
    result = A3SplitResult(pdf_count=len(pdfs))
    vision = client or LocalVisionClient(
        settings.ai_base_url,
        settings.ai_model,
        settings.ai_api_key,
    )
    model = vision.check_ready()
    message("SUCCESS", f"本地 AI 已连接，使用模型：{model}")
    message(
        "INFO",
        f"特征参考图：左上角 {settings.top_left_reference_image.name}；"
        f"右下角 {settings.bottom_right_reference_image.name}",
    )
    message("INFO", f"发现 {len(pdfs)} 个待拆分 PDF")
    for index, pdf_path in enumerate(pdfs, start=1):
        if cancel_check():
            result.cancelled = True
            break
        relative = pdf_path.relative_to(settings.input_dir)
        relative_text = relative.as_posix()
        output_path = settings.output_dir / relative.parent / f"{relative.stem}_a3.pdf"
        progress(index - 1, len(pdfs), relative_text)
        message("INFO", f"正在识别并拆分：{relative_text}")
        try:
            source_pages, output_pages, rotated_pages = split_pdf_to_a3(
                pdf_path,
                output_path,
                vision,
                top_left_reference_png=top_left_reference_png,
                bottom_right_reference_png=bottom_right_reference_png,
                preview_max_pixels=settings.preview_max_pixels,
                top_left_offset_x_mm=settings.top_left_offset_x_mm,
                top_left_offset_y_mm=settings.top_left_offset_y_mm,
                bottom_right_offset_x_mm=settings.bottom_right_offset_x_mm,
                bottom_right_offset_y_mm=settings.bottom_right_offset_y_mm,
                overlap_mm=settings.overlap_mm,
                margin_mm=settings.margin_mm,
                on_page=lambda current, total, rotation, name=relative_text: message(
                    "INFO", f"{name}：第 {current}/{total} 页，AI 判断顺时针旋转 {rotation}° 后左右拆分"
                ),
                is_cancelled=cancel_check,
            )
        except A3SplitCancelled:
            result.cancelled = True
            message("WARNING", "正在安全取消 A3 分页任务")
            break
        except (A3SplitError, VisionServiceError, OSError) as exc:
            reason = str(exc)
            result.failures.append(A3SplitFailure(pdf_path.name, relative_text, reason))
            result.processed_pdf_count += 1
            message("ERROR", f"处理失败：{relative_text}：{reason}")
            logger.exception("A3 分页失败：%s", relative_text)
            progress(index, len(pdfs), relative_text)
            continue
        result.processed_pdf_count += 1
        result.output_pdf_count += 1
        result.source_page_count += source_pages
        result.output_page_count += output_pages
        result.rotated_page_count += rotated_pages
        result.output_files.append(output_path)
        message(
            "SUCCESS",
            f"已输出：{output_path.name}（{source_pages} 页转为 {output_pages} 张 A3，旋正 {rotated_pages} 页）",
        )
        progress(index, len(pdfs), relative_text)
    return result


def discover_split_pdfs(input_dir: Path, output_dir: Path, recursive: bool) -> list[Path]:
    iterator = input_dir.rglob("*.pdf") if recursive else input_dir.glob("*.pdf")
    output_resolved = output_dir.resolve()
    files = []
    for path in iterator:
        resolved = path.resolve()
        if resolved == output_resolved or output_resolved in resolved.parents:
            continue
        if path.stem.casefold().endswith("_a3"):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix().casefold())
