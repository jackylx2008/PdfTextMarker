"""生成独立、可离线查看的 HTML 处理报告。"""

from __future__ import annotations

import html
import os
import tempfile
from datetime import datetime
from pathlib import Path

from pdf_text_marker.models import ProcessingResult


def write_html_report(result: ProcessingResult, report_path: Path, started_at: datetime, finished_at: datetime) -> Path:
    """写入包含匹配、未命中和失败信息的 HTML 报告。"""
    status = "已取消" if result.cancelled else ("完成（有错误）" if result.failures else "完成")
    duration = finished_at - started_at
    match_rows = "".join(
        "<tr>"
        f"<td>{_e(record.keyword)}</td><td>{_e(record.relative_pdf)}</td>"
        f"<td>{record.page_number}</td><td>{record.match_count}</td><td>{_e(record.status)}</td>"
        "</tr>"
        for record in result.matches
    ) or '<tr><td colspan="5" class="empty">没有匹配记录</td></tr>'
    unmatched_rows = "".join(
        f"<tr><td>{_e(item.keyword)}</td><td>{_e(item.pdf_name or '全部 PDF')}</td><td>未命中</td></tr>"
        for item in result.unmatched_keywords
    ) or '<tr><td colspan="3" class="empty">所有关键词均已命中</td></tr>'
    failure_rows = "".join(
        f"<tr><td>{_e(item.relative_pdf)}</td><td>{_e(item.reason)}</td><td>失败</td></tr>"
        for item in result.failures
    ) or '<tr><td colspan="3" class="empty">没有处理失败的 PDF</td></tr>'

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF 关键词标注报告</title>
<style>
:root {{ color-scheme: light; --red:#c62828; --ink:#1f2937; --muted:#667085; --line:#d0d5dd; --panel:#f8fafc; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#eef2f6; color:var(--ink); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:32px auto; padding:0 20px 48px; }}
h1 {{ margin:0 0 4px; font-size:28px; }}
h2 {{ margin:32px 0 12px; font-size:19px; }}
.meta {{ color:var(--muted); }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:24px 0; }}
.card {{ padding:16px; background:white; border:1px solid var(--line); border-radius:8px; }}
.card strong {{ display:block; font-size:24px; color:var(--red); }}
.card span {{ color:var(--muted); }}
.table-wrap {{ overflow:auto; background:white; border:1px solid var(--line); border-radius:8px; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{ padding:10px 12px; border-bottom:1px solid #e7eaf0; text-align:left; vertical-align:top; }}
th {{ background:var(--panel); white-space:nowrap; }}
tr:last-child td {{ border-bottom:0; }}
.empty {{ color:var(--muted); text-align:center; }}
</style>
</head>
<body><main>
<h1>PDF 关键词标注报告</h1>
<div class="meta">状态：{_e(status)}　开始：{started_at:%Y-%m-%d %H:%M:%S}　耗时：{_format_duration(duration.total_seconds())}</div>
<section class="cards">
<div class="card"><strong>{result.keyword_count}</strong><span>有效关键词</span></div>
<div class="card"><strong>{result.pdf_count}</strong><span>发现 PDF</span></div>
<div class="card"><strong>{result.output_pdf_count}</strong><span>输出 PDF</span></div>
<div class="card"><strong>{result.total_match_count}</strong><span>匹配位置</span></div>
<div class="card"><strong>{len(result.unmatched_keywords)}</strong><span>未命中关键词</span></div>
<div class="card"><strong>{len(result.failures)}</strong><span>失败文件</span></div>
</section>
<h2>匹配明细</h2><div class="table-wrap"><table><thead><tr><th>关键词</th><th>PDF 文件</th><th>页码</th><th>匹配次数</th><th>状态</th></tr></thead><tbody>{match_rows}</tbody></table></div>
<h2>未命中关键词</h2><div class="table-wrap"><table><thead><tr><th>关键词</th><th>指定 PDF</th><th>状态</th></tr></thead><tbody>{unmatched_rows}</tbody></table></div>
<h2>处理失败</h2><div class="table-wrap"><table><thead><tr><th>PDF 文件</th><th>原因</th><th>状态</th></tr></thead><tbody>{failure_rows}</tbody></table></div>
</main></body></html>
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".report_", suffix=".html.tmp", dir=report_path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.write_text(document, encoding="utf-8")
        os.replace(temporary_path, report_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return report_path


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _format_duration(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
