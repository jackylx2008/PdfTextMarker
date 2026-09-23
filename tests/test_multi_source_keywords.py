"""多工作表关键词来源测试。"""

from pathlib import Path

from pdf_text_marker.context import _parse_excel_sources
from pdf_text_marker.models import ExcelKeywordSource, KeywordSpec
from pdf_text_marker.modules import keyword_reader


def test_parse_multiple_excel_sources() -> None:
    sources = _parse_excel_sources(
        [
            {"sheet_name": "PAU", "keyword_columns": ["C", "E"]},
            {"sheet_name": "AHU", "keyword_columns": "C"},
        ]
    )

    assert sources == (
        ExcelKeywordSource("PAU", ("C", "E")),
        ExcelKeywordSource("AHU", ("C",)),
    )


def test_read_excel_sources_merges_and_deduplicates(monkeypatch) -> None:
    calls: list[tuple[str | None, str | None]] = []

    def fake_read_keywords(path, sheet_name=None, keyword_column=None, pdf_name_column=None):
        calls.append((sheet_name, keyword_column))
        if sheet_name == "PAU" and keyword_column == "C":
            return [KeywordSpec("ROOM-01"), KeywordSpec("SHARED")]
        if sheet_name == "PAU" and keyword_column == "E":
            return [KeywordSpec("ROOM-02")]
        return [KeywordSpec("SHARED"), KeywordSpec("ROOM-03")]

    monkeypatch.setattr(keyword_reader, "read_keywords", fake_read_keywords)
    sources = (
        ExcelKeywordSource("PAU", ("C", "E")),
        ExcelKeywordSource("AHU", ("C",)),
    )

    result = keyword_reader.read_excel_keywords(Path("sample.xlsx"), sources)

    assert calls == [("PAU", "C"), ("PAU", "E"), ("AHU", "C")]
    assert [item.keyword for item in result] == ["ROOM-01", "SHARED", "ROOM-02", "ROOM-03"]


def test_keyword_removes_colon_suffix_for_csv(tmp_path) -> None:
    source = tmp_path / "keywords.csv"
    source.write_text(
        "keyword\n"
        "BOH_F2(夹)_M003:RC-N轴/2轴\n"
        "ROOM-01：补充说明\n"
        "BOH_F2(夹)_M003\n"
        ":无有效关键词\n",
        encoding="utf-8",
    )

    result = keyword_reader.read_keywords(source)

    assert [item.keyword for item in result] == ["BOH_F2(夹)_M003", "ROOM-01"]
