"""项目内共享的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class KeywordSpec:
    """一个关键词及其可选 PDF 目标。"""

    keyword: str
    pdf_name: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return (self.keyword.casefold(), (self.pdf_name or "").casefold())


@dataclass(frozen=True, slots=True)
class ExcelKeywordSource:
    """一个 Excel 工作表及其中需要读取的关键词列。"""

    sheet_name: str
    keyword_columns: tuple[str, ...]
    pdf_name_column: str | None = None


@dataclass(frozen=True, slots=True)
class MatchRecord:
    """某关键词在 PDF 某页上的匹配汇总。"""

    keyword: str
    pdf_name: str
    relative_pdf: str
    page_number: int
    match_count: int
    status: str = "已标注"


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """无法完成处理的 PDF 及原因。"""

    pdf_name: str
    relative_pdf: str
    reason: str


@dataclass(slots=True)
class ProcessingResult:
    """完整工作流的结构化结果。"""

    keyword_count: int = 0
    pdf_count: int = 0
    processed_pdf_count: int = 0
    output_pdf_count: int = 0
    total_match_count: int = 0
    matches: list[MatchRecord] = field(default_factory=list)
    unmatched_keywords: list[KeywordSpec] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)
    skipped_image_only_pages: int = 0
    output_files: list[Path] = field(default_factory=list)
    report_path: Path | None = None
    cancelled: bool = False

    def to_dict(self) -> dict[str, object]:
        """转换成适合 JSON 输出的字典。"""
        return {
            "keyword_count": self.keyword_count,
            "pdf_count": self.pdf_count,
            "processed_pdf_count": self.processed_pdf_count,
            "output_pdf_count": self.output_pdf_count,
            "total_match_count": self.total_match_count,
            "unmatched_keyword_count": len(self.unmatched_keywords),
            "failure_count": len(self.failures),
            "skipped_image_only_pages": self.skipped_image_only_pages,
            "output_files": [str(path) for path in self.output_files],
            "report_path": str(self.report_path) if self.report_path else None,
            "cancelled": self.cancelled,
        }


@dataclass(frozen=True, slots=True)
class A3SplitFailure:
    """无法完成 A3 分页处理的 PDF 及原因。"""

    pdf_name: str
    relative_pdf: str
    reason: str


@dataclass(slots=True)
class A3SplitResult:
    """AI 图框识别与 A3 分页工作流结果。"""

    pdf_count: int = 0
    processed_pdf_count: int = 0
    output_pdf_count: int = 0
    source_page_count: int = 0
    output_page_count: int = 0
    rotated_page_count: int = 0
    output_files: list[Path] = field(default_factory=list)
    failures: list[A3SplitFailure] = field(default_factory=list)
    cancelled: bool = False
