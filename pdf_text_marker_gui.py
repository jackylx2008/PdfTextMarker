"""PDF 图纸处理桌面工具

用途：
  通过 Tkinter 图形界面选择 XLSX/CSV 关键词文件、PDF 输入目录和输出目录，
  配置多个 Excel 工作表/列以及黄色底纹和红色边框，后台执行批量标注。

配置文件：
  默认读取根目录 config.yaml；common.env 保存本机私有路径并覆盖公开默认值。
  界面中的修改仅影响当前运行，不会写回配置文件。

可选参数：
  --config-file   指定公开 YAML 配置文件，默认使用根目录 config.yaml。

示例：
  python pdf_text_marker_gui.py

输出：
  有匹配结果的 *_marked.pdf 和 pdf_text_marker_report.html 写入所选输出目录。
"""

from __future__ import annotations

import argparse
import platform
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from logging_config import configure_utf8_stdio, get_logger, setup_logger
from pdf_text_marker.config_loader import resolve_path
from pdf_text_marker.context import (
    A3SplitSettings,
    AppContext,
    MarkPdfsSettings,
    bootstrap_context,
    color_to_hex,
    parse_hex_color,
)
from pdf_text_marker.flows.a3_split_flow import discover_split_pdfs, run as run_a3_split
from pdf_text_marker.flows.mark_pdfs_flow import discover_pdfs, run
from pdf_text_marker.models import A3SplitResult, ExcelKeywordSource, ProcessingResult
from pdf_text_marker.modules.directory_cleaner import (
    clear_directory_contents,
    inspect_directory_contents,
    validate_clear_target,
)
from pdf_text_marker.modules.keyword_reader import inspect_keyword_source, read_excel_keywords, read_keywords


logger = get_logger(__name__)
MAX_LOG_LINES = 3000


class SourceDialog(tk.Toplevel):
    """新增或编辑一个 Excel 关键词来源。"""

    def __init__(
        self,
        parent: tk.Misc,
        sheets: tuple[str, ...],
        initial: ExcelKeywordSource | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Excel 关键词来源")
        self.resizable(False, False)
        self.transient(parent)
        self.result: ExcelKeywordSource | None = None
        self.sheet_var = tk.StringVar(value=initial.sheet_name if initial else (sheets[0] if sheets else ""))
        self.columns_var = tk.StringVar(value=", ".join(initial.keyword_columns) if initial else "A")
        self.pdf_column_var = tk.StringVar(value=initial.pdf_name_column or "" if initial else "")

        frame = ttk.Frame(self, padding=14)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="工作表：").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=5)
        sheet_box = ttk.Combobox(frame, textvariable=self.sheet_var, values=sheets, width=32)
        sheet_box.grid(row=0, column=1, sticky="ew", pady=5)
        if sheets:
            sheet_box.state(["readonly"])
        ttk.Label(frame, text="关键词列：").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=5)
        ttk.Entry(frame, textvariable=self.columns_var, width=35).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="多个列用英文逗号分隔，例如 C, E；也可填写不重复的表头名称。", foreground="#667085").grid(
            row=2, column=1, sticky="w"
        )
        ttk.Label(frame, text="PDF 文件名列：").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=5)
        ttk.Entry(frame, textvariable=self.pdf_column_var, width=35).grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="可留空；填写后仅在对应 PDF 中搜索该行关键词。", foreground="#667085").grid(
            row=4, column=1, sticky="w"
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="确定", command=self._accept).pack(side="left", padx=4)
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        sheet_box.focus_set()

    def _accept(self) -> None:
        sheet = self.sheet_var.get().strip()
        columns = tuple(part.strip() for part in self.columns_var.get().split(",") if part.strip())
        if not sheet:
            messagebox.showerror("参数错误", "请输入或选择工作表名称。", parent=self)
            return
        if not columns:
            messagebox.showerror("参数错误", "至少填写一个关键词列。", parent=self)
            return
        self.result = ExcelKeywordSource(sheet, columns, self.pdf_column_var.get().strip() or None)
        self.destroy()


class PdfTextMarkerApp:
    """单窗口 PDF 关键词标注应用。"""

    def __init__(self, root: tk.Tk, context: AppContext) -> None:
        self.root = root
        self.context = context
        self.events: queue.Queue[tuple[object, ...]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.started_at = 0.0
        self.running = False
        self.closing = False
        self.available_sheets: tuple[str, ...] = ()
        self.sources = list(context.mark_pdfs.excel_sources)
        self.input_widgets: list[tk.Widget] = []
        self.start_buttons: list[ttk.Button] = []
        self.cancel_buttons: list[ttk.Button] = []
        self.active_cancel_button: ttk.Button | None = None

        settings = context.mark_pdfs
        self.keyword_file_var = tk.StringVar(value=str(settings.keyword_file))
        self.pdf_dir_var = tk.StringVar(value=str(settings.pdf_dir))
        self.output_dir_var = tk.StringVar(value=str(settings.output_dir))
        self.recursive_var = tk.BooleanVar(value=settings.recursive)
        self.csv_keyword_var = tk.StringVar(value=settings.keyword_column or "")
        self.csv_pdf_var = tk.StringVar(value=settings.pdf_name_column or "")
        self.highlight_enabled_var = tk.BooleanVar(value=settings.highlight_enabled)
        self.highlight_color_var = tk.StringVar(value=color_to_hex(settings.highlight_color))
        self.highlight_opacity_var = tk.StringVar(value=f"{settings.highlight_opacity:.2f}")
        self.border_enabled_var = tk.BooleanVar(value=settings.border_enabled)
        self.border_color_var = tk.StringVar(value=color_to_hex(settings.border_color))
        self.border_width_var = tk.StringVar(value=f"{settings.border_width:g}")
        self.box_aspect_ratio_var = tk.StringVar(value=f"{settings.box_aspect_ratio:g}")
        self.box_size_var = tk.StringVar(value=f"{settings.box_size:g}")
        self.box_scale_var = tk.StringVar(value=f"{settings.box_scale:g}")
        a3_settings = context.a3_split
        self.a3_input_dir_var = tk.StringVar(value=str(a3_settings.input_dir))
        self.a3_output_dir_var = tk.StringVar(value=str(a3_settings.output_dir))
        self.a3_recursive_var = tk.BooleanVar(value=a3_settings.recursive)
        self.a3_base_url_var = tk.StringVar(value=a3_settings.ai_base_url)
        self.a3_model_var = tk.StringVar(value=a3_settings.ai_model)
        self.a3_top_left_reference_var = tk.StringVar(value=str(a3_settings.top_left_reference_image))
        self.a3_bottom_right_reference_var = tk.StringVar(value=str(a3_settings.bottom_right_reference_image))
        self.a3_preview_pixels_var = tk.StringVar(value=str(a3_settings.preview_max_pixels))
        self.a3_top_left_offset_x_var = tk.StringVar(value=f"{a3_settings.top_left_offset_x_mm:g}")
        self.a3_top_left_offset_y_var = tk.StringVar(value=f"{a3_settings.top_left_offset_y_mm:g}")
        self.a3_bottom_right_offset_x_var = tk.StringVar(value=f"{a3_settings.bottom_right_offset_x_mm:g}")
        self.a3_bottom_right_offset_y_var = tk.StringVar(value=f"{a3_settings.bottom_right_offset_y_mm:g}")
        self.a3_overlap_var = tk.StringVar(value=f"{a3_settings.overlap_mm:g}")
        self.a3_margin_var = tk.StringVar(value=f"{a3_settings.margin_mm:g}")
        self.status_var = tk.StringVar(value="就绪")
        self.current_var = tk.StringVar(value="等待任务")
        self.elapsed_var = tk.StringVar(value="00:00:00")

        self._build_window()
        self._populate_sources()
        self._append_log("INFO", "配置已加载，界面修改仅影响本次运行。")
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_window(self) -> None:
        self.root.title("PDF 图纸处理工具")
        self.root.geometry("1120x1000")
        self.root.minsize(920, 1000)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=3, minsize=690)
        self.root.grid_rowconfigure(1, weight=2, minsize=140)

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        workflow_tab = ttk.Frame(notebook, padding=12)
        a3_tab = ttk.Frame(notebook, padding=12)
        config_tab = ttk.Frame(notebook, padding=18)
        notebook.add(workflow_tab, text="PDF 关键词标注")
        notebook.add(a3_tab, text="AI 图框拆分 A3")
        notebook.add(config_tab, text="配置说明")
        self._build_workflow_tab(workflow_tab)
        self._build_a3_tab(a3_tab)
        self._build_config_tab(config_tab)
        self._build_log_area()
        self._build_status_area()

    def _build_workflow_tab(self, parent: ttk.Frame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        paths = ttk.LabelFrame(parent, text="输入与输出", padding=10)
        paths.grid(row=0, column=0, sticky="ew")
        paths.grid_columnconfigure(1, weight=1)
        self._path_row(paths, 0, "关键词 Excel/CSV：", self.keyword_file_var, self._browse_keyword_file, file_path=True)
        self._path_row(paths, 1, "PDF 输入目录：", self.pdf_dir_var, self._browse_pdf_dir)
        self._path_row(paths, 2, "输出目录：", self.output_dir_var, self._browse_output_dir)
        recursive = ttk.Checkbutton(paths, text="递归搜索子目录中的 PDF", variable=self.recursive_var)
        recursive.grid(row=3, column=1, sticky="w", pady=(5, 0))
        clear_output = ttk.Button(paths, text="一键清空输出", command=self._clear_output_directory)
        clear_output.grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=(5, 0))
        self.input_widgets.extend((recursive, clear_output))

        source_frame = ttk.LabelFrame(parent, text="关键词来源", padding=10)
        source_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        source_frame.grid_columnconfigure(0, weight=1)
        source_frame.grid_rowconfigure(0, weight=1)
        self.source_tree = ttk.Treeview(
            source_frame,
            columns=("sheet", "columns", "pdf_column"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        self.source_tree.heading("sheet", text="Excel Sheet")
        self.source_tree.heading("columns", text="关键词列")
        self.source_tree.heading("pdf_column", text="PDF 文件名列（可选）")
        self.source_tree.column("sheet", width=190, anchor="w")
        self.source_tree.column("columns", width=250, anchor="w")
        self.source_tree.column("pdf_column", width=220, anchor="w")
        self.source_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(source_frame, orient="vertical", command=self.source_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.source_tree.configure(yscrollcommand=tree_scroll.set)
        self.source_tree.bind("<Double-1>", lambda _event: self._edit_source())
        source_buttons = ttk.Frame(source_frame)
        source_buttons.grid(row=1, column=0, sticky="w", pady=(8, 0))
        for text, command in (
            ("读取表结构", self._refresh_structure),
            ("新增来源", self._add_source),
            ("编辑来源", self._edit_source),
            ("删除来源", self._remove_source),
        ):
            button = ttk.Button(source_buttons, text=text, command=command)
            button.pack(side="left", padx=(0, 7))
            self.input_widgets.append(button)

        csv_frame = ttk.Frame(source_frame)
        csv_frame.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        ttk.Label(csv_frame, text="CSV 关键词列：").pack(side="left")
        csv_keyword = ttk.Entry(csv_frame, textvariable=self.csv_keyword_var, width=18)
        csv_keyword.pack(side="left", padx=(0, 16))
        ttk.Label(csv_frame, text="CSV PDF 文件名列：").pack(side="left")
        csv_pdf = ttk.Entry(csv_frame, textvariable=self.csv_pdf_var, width=18)
        csv_pdf.pack(side="left")
        ttk.Label(csv_frame, text="（留空时自动寻找 keyword/关键词）", foreground="#667085").pack(side="left", padx=10)
        self.input_widgets.extend((csv_keyword, csv_pdf))

        appearance = ttk.LabelFrame(parent, text="标注样式", padding=10)
        appearance.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        highlight_check = ttk.Checkbutton(appearance, text="启用底纹", variable=self.highlight_enabled_var)
        highlight_check.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.highlight_color_button = tk.Button(
            appearance,
            textvariable=self.highlight_color_var,
            command=self._choose_highlight_color,
            width=9,
            relief="solid",
            borderwidth=1,
        )
        self.highlight_color_button.grid(row=0, column=1, padx=(0, 20))
        ttk.Label(appearance, text="透明度：").grid(row=0, column=2)
        opacity = ttk.Spinbox(appearance, from_=0.05, to=1.0, increment=0.05, textvariable=self.highlight_opacity_var, width=8)
        opacity.grid(row=0, column=3, padx=(0, 30))
        border_check = ttk.Checkbutton(appearance, text="启用边框", variable=self.border_enabled_var)
        border_check.grid(row=0, column=4, sticky="w", padx=(0, 10))
        self.border_color_button = tk.Button(
            appearance,
            textvariable=self.border_color_var,
            command=self._choose_border_color,
            width=9,
            relief="solid",
            borderwidth=1,
        )
        self.border_color_button.grid(row=0, column=5, padx=(0, 20))
        ttk.Label(appearance, text="线宽：").grid(row=0, column=6)
        border_width = ttk.Spinbox(appearance, from_=0.25, to=10.0, increment=0.25, textvariable=self.border_width_var, width=8)
        border_width.grid(row=0, column=7)
        ttk.Label(appearance, text="长宽比（宽÷高）：").grid(row=1, column=0, sticky="e", pady=(9, 0))
        aspect_ratio = ttk.Spinbox(
            appearance, from_=0.1, to=100.0, increment=0.1, textvariable=self.box_aspect_ratio_var, width=8
        )
        aspect_ratio.grid(row=1, column=1, sticky="w", pady=(9, 0))
        ttk.Label(appearance, text="大小（基础宽度/pt）：").grid(row=1, column=2, sticky="e", pady=(9, 0))
        box_size = ttk.Spinbox(appearance, from_=1.0, to=2000.0, increment=1.0, textvariable=self.box_size_var, width=8)
        box_size.grid(row=1, column=3, sticky="w", pady=(9, 0))
        ttk.Label(appearance, text="放大倍数：").grid(row=1, column=4, sticky="e", pady=(9, 0))
        box_scale = ttk.Spinbox(appearance, from_=0.1, to=100.0, increment=0.1, textvariable=self.box_scale_var, width=8)
        box_scale.grid(row=1, column=5, sticky="w", pady=(9, 0))
        ttk.Label(appearance, text="最终宽度 = 大小 × 放大倍数", foreground="#667085").grid(
            row=1, column=6, columnspan=2, sticky="w", padx=(10, 0), pady=(9, 0)
        )
        self.input_widgets.extend(
            (
                highlight_check,
                self.highlight_color_button,
                opacity,
                border_check,
                self.border_color_button,
                border_width,
                aspect_ratio,
                box_size,
                box_scale,
            )
        )
        self._sync_color_button(self.highlight_color_button, self.highlight_color_var.get())
        self._sync_color_button(self.border_color_button, self.border_color_var.get())

        actions = ttk.Frame(parent)
        actions.grid(row=3, column=0, sticky="e", pady=(12, 0))
        self.preview_button = ttk.Button(actions, text="参数预览", command=self._preview)
        self.start_button = ttk.Button(actions, text="开始执行", command=self._start)
        self.cancel_button = ttk.Button(actions, text="取消任务", command=self._cancel)
        self.preview_button.pack(side="left", padx=5)
        self.start_button.pack(side="left", padx=5)
        self.cancel_button.pack(side="left", padx=5)
        self.cancel_button.state(["disabled"])
        self.start_buttons.append(self.start_button)
        self.cancel_buttons.append(self.cancel_button)
        self.input_widgets.append(self.preview_button)

    def _build_a3_tab(self, parent: ttk.Frame) -> None:
        """构建 AI 图框识别与 A3 拆分页。"""
        parent.grid_columnconfigure(0, weight=1)
        paths = ttk.LabelFrame(parent, text="输入与输出", padding=10)
        paths.grid(row=0, column=0, sticky="ew")
        paths.grid_columnconfigure(1, weight=1)
        self._path_row(paths, 0, "PDF 输入目录：", self.a3_input_dir_var, self._browse_a3_input_dir)
        self._path_row(paths, 1, "A3 输出目录：", self.a3_output_dir_var, self._browse_a3_output_dir)
        recursive = ttk.Checkbutton(paths, text="递归搜索子目录中的 PDF", variable=self.a3_recursive_var)
        recursive.grid(row=2, column=1, sticky="w", pady=(5, 0))
        self.input_widgets.append(recursive)

        ai_frame = ttk.LabelFrame(parent, text="本地 AI 视觉服务", padding=10)
        ai_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ai_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(ai_frame, text="API 地址：").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
        base_url = ttk.Entry(ai_frame, textvariable=self.a3_base_url_var)
        base_url.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(ai_frame, text="模型名：").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        model = ttk.Entry(ai_frame, textvariable=self.a3_model_var)
        model.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(ai_frame, text="留空时自动使用服务返回的第一个模型", foreground="#667085").grid(
            row=1, column=2, sticky="w", padx=(8, 0)
        )
        auth_status = "已从 common.env 读取" if self.context.a3_split.ai_api_key else "common.env 中尚未配置"
        ttk.Label(ai_frame, text="鉴权配置：").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
        ttk.Label(ai_frame, text=auth_status, foreground="#667085").grid(
            row=2, column=1, sticky="w", pady=4
        )
        self.input_widgets.extend((base_url, model))

        reference = ttk.LabelFrame(parent, text="特征定位参考", padding=10)
        reference.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        reference.grid_columnconfigure(1, weight=1)
        self._path_row(
            reference,
            0,
            "左上角 L/1 轴网：",
            self.a3_top_left_reference_var,
            self._browse_a3_top_left_reference,
            file_path=True,
        )
        self._path_row(
            reference,
            1,
            "右下角图签：",
            self.a3_bottom_right_reference_var,
            self._browse_a3_bottom_right_reference,
            file_path=True,
        )
        ttk.Label(reference, text="左上偏移 X/Y（mm）：").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
        top_offset_x = ttk.Spinbox(
            reference, from_=-100, to=100, increment=1, textvariable=self.a3_top_left_offset_x_var, width=10
        )
        top_offset_x.grid(row=2, column=1, sticky="w", pady=4)
        top_offset_y = ttk.Spinbox(
            reference, from_=-100, to=100, increment=1, textvariable=self.a3_top_left_offset_y_var, width=10
        )
        top_offset_y.grid(row=2, column=1, sticky="w", padx=(100, 0), pady=4)
        ttk.Label(reference, text="右下偏移 X/Y（mm）：").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=4)
        bottom_offset_x = ttk.Spinbox(
            reference, from_=-100, to=100, increment=1, textvariable=self.a3_bottom_right_offset_x_var, width=10
        )
        bottom_offset_x.grid(row=3, column=1, sticky="w", pady=4)
        bottom_offset_y = ttk.Spinbox(
            reference, from_=-100, to=100, increment=1, textvariable=self.a3_bottom_right_offset_y_var, width=10
        )
        bottom_offset_y.grid(row=3, column=1, sticky="w", padx=(100, 0), pady=4)
        self.input_widgets.extend((top_offset_x, top_offset_y, bottom_offset_x, bottom_offset_y))

        options = ttk.LabelFrame(parent, text="A3 拆分设置", padding=8)
        options.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(options, text="方向处理：").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
        ttk.Label(
            options,
            text="AI 自动判断 0/90/180/270°，先将图纸旋正为横向，再固定左右拆分",
            foreground="#667085",
        ).grid(
            row=0, column=1, columnspan=5, sticky="w", pady=4
        )
        ttk.Label(options, text="接缝重叠（mm）：").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        overlap = ttk.Spinbox(options, from_=0, to=100, increment=1, textvariable=self.a3_overlap_var, width=10)
        overlap.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(options, text="页面边距（mm）：").grid(row=1, column=2, sticky="e", padx=(25, 8), pady=4)
        margin = ttk.Spinbox(options, from_=0, to=50, increment=1, textvariable=self.a3_margin_var, width=10)
        margin.grid(row=1, column=3, sticky="w", pady=4)
        ttk.Label(options, text="AI 预览最长边：").grid(row=1, column=4, sticky="e", padx=(25, 8), pady=4)
        pixels = ttk.Spinbox(
            options, from_=512, to=4096, increment=128, textvariable=self.a3_preview_pixels_var, width=10
        )
        pixels.grid(row=1, column=5, sticky="w", pady=4)
        self.input_widgets.extend((overlap, margin, pixels))

        actions = ttk.Frame(parent)
        actions.grid(row=4, column=0, sticky="e", pady=(4, 0))
        self.a3_preview_button = ttk.Button(actions, text="参数预览", command=self._preview_a3)
        self.a3_start_button = ttk.Button(actions, text="开始执行", command=self._start_a3)
        self.a3_cancel_button = ttk.Button(actions, text="取消任务", command=self._cancel)
        self.a3_preview_button.pack(side="left", padx=5)
        self.a3_start_button.pack(side="left", padx=5)
        self.a3_cancel_button.pack(side="left", padx=5)
        self.a3_cancel_button.state(["disabled"])
        self.input_widgets.append(self.a3_preview_button)
        self.start_buttons.append(self.a3_start_button)
        self.cancel_buttons.append(self.a3_cancel_button)

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
        *,
        file_path: bool = False,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        button = ttk.Button(parent, text="浏览文件" if file_path else "浏览目录", command=command)
        button.grid(row=row, column=2, padx=(8, 0), pady=4)
        self.input_widgets.extend((entry, button))

    def _build_config_tab(self, parent: ttk.Frame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        text = (
            "配置优先级\n\n"
            "进程环境变量 > common.env > config.yaml 默认值\n\n"
            "config.yaml 是可同步的公开配置，只保存通用默认值。\n"
            "common.env 是本机私有配置，不进行 Git 同步。\n"
            "界面参数只影响当前任务，不会写回配置文件。\n\n"
            "关键词规则\n\n"
            "支持 XLSX 和 CSV；忽略空值并去重。英文和中文冒号及其后的说明会被删除。\n"
            "Excel 可配置多个 Sheet 和多个列；CSV 可按列名读取。\n\n"
            "PDF 规则\n\n"
            "只处理带文字层的 PDF。原文件保持不变，仅为有匹配结果的文件生成 *_marked.pdf。\n"
            "底纹矩形和边框使用完全相同的范围，并以关键词文字中心为中心。\n"
            "最终宽度 = 大小 × 放大倍数；最终高度 = 最终宽度 ÷ 长宽比。\n\n"
            "AI 图框拆分 A3\n\n"
            "用 L/1 轴网参考图定位左上角，用图签参考图定位右下角，并分别应用 X/Y 偏移。\n"
            "识别页面方向后先旋正为横向，再左右拆成两个 A3 页面。\n"
            "AI 只分析预览图；输出直接裁切原 PDF，尽量保留矢量文字与线条。"
        )
        ttk.Label(parent, text=text, justify="left", anchor="nw", wraplength=850).grid(sticky="nw")

    def _build_log_area(self) -> None:
        frame = ttk.LabelFrame(self.root, text="运行日志与实时输出", padding=8)
        frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.log_text = tk.Text(frame, height=12, wrap="word", state="disabled", font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.tag_configure("INFO", foreground="#1f2937")
        self.log_text.tag_configure("WARNING", foreground="#b45309")
        self.log_text.tag_configure("ERROR", foreground="#b42318")
        self.log_text.tag_configure("SUCCESS", foreground="#067647")
        ttk.Button(frame, text="清空日志", command=self._clear_log).grid(row=1, column=0, sticky="e", pady=(6, 0))

    def _build_status_area(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 4, 10, 10))
        frame.grid(row=2, column=0, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        ttk.Label(frame, textvariable=self.status_var, width=12).grid(row=1, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.current_var).grid(row=1, column=1, sticky="w")
        ttk.Label(frame, textvariable=self.elapsed_var, width=10).grid(row=1, column=2, padx=10)
        environment = f"Python {platform.python_version()} / Tk {tk.TkVersion}"
        ttk.Label(frame, text=environment, foreground="#667085").grid(row=1, column=3, sticky="e")

    def _browse_keyword_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择关键词文件",
            filetypes=(("关键词文件", "*.xlsx *.csv"), ("Excel", "*.xlsx"), ("CSV", "*.csv"), ("所有文件", "*.*")),
        )
        if path:
            self.keyword_file_var.set(path)
            self._refresh_structure()

    def _browse_pdf_dir(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="选择 PDF 输入目录")
        if path:
            self.pdf_dir_var.set(path)

    def _browse_output_dir(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="选择输出目录")
        if path:
            self.output_dir_var.set(path)

    def _browse_a3_input_dir(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="选择待拆分 PDF 目录")
        if path:
            self.a3_input_dir_var.set(path)

    def _browse_a3_output_dir(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="选择 A3 输出目录")
        if path:
            self.a3_output_dir_var.set(path)

    def _browse_a3_top_left_reference(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择左上角 L/1 轴网参考截图",
            filetypes=(("PNG 图片", "*.png"), ("所有文件", "*.*")),
        )
        if path:
            self.a3_top_left_reference_var.set(path)

    def _browse_a3_bottom_right_reference(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择右下角图签参考截图",
            filetypes=(("PNG 图片", "*.png"), ("所有文件", "*.*")),
        )
        if path:
            self.a3_bottom_right_reference_var.set(path)

    def _clear_output_directory(self) -> None:
        """经用户确认后清空输出目录，但保留目录本身。"""
        try:
            output_dir = resolve_path(self.context.project_root, self.output_dir_var.get())
            pdf_dir = resolve_path(self.context.project_root, self.pdf_dir_var.get())
            keyword_file = resolve_path(self.context.project_root, self.keyword_file_var.get())
            protected = (self.context.project_root, Path.home(), pdf_dir, keyword_file)
            validate_clear_target(output_dir, protected)
            summary = inspect_directory_contents(output_dir)
            if not output_dir.exists():
                messagebox.showinfo("清空输出目录", f"输出目录不存在，无需清理：\n{output_dir}", parent=self.root)
                return
            if summary.file_count == 0 and summary.directory_count == 0:
                messagebox.showinfo("清空输出目录", f"输出目录已经为空：\n{output_dir}", parent=self.root)
                return
            size_mb = summary.total_bytes / (1024 * 1024)
            confirmed = messagebox.askyesno(
                "确认清空输出目录",
                f"即将永久删除以下目录中的全部内容：\n\n{output_dir}\n\n"
                f"文件：{summary.file_count} 个\n子目录：{summary.directory_count} 个\n"
                f"总大小：{size_mb:.2f} MB\n\n此操作不可撤销，是否继续？",
                icon="warning",
                parent=self.root,
            )
            if not confirmed:
                self._append_log("INFO", "已取消清空输出目录。")
                return
            removed = clear_directory_contents(output_dir, protected)
            self._append_log(
                "SUCCESS",
                f"已清空输出目录：{output_dir}（删除 {removed.file_count} 个文件、{removed.directory_count} 个子目录）",
            )
            messagebox.showinfo("清理完成", f"输出目录已清空：\n{output_dir}", parent=self.root)
        except Exception as exc:
            self._append_log("ERROR", f"清空输出目录失败：{exc}")
            messagebox.showerror("清空失败", str(exc), parent=self.root)

    def _refresh_structure(self) -> None:
        try:
            path = resolve_path(self.context.project_root, self.keyword_file_var.get())
            info = inspect_keyword_source(path)
            self.available_sheets = info.sheets
            if info.sheets:
                self._append_log("SUCCESS", f"Excel 工作表：{', '.join(info.sheets)}")
            else:
                self._append_log("SUCCESS", f"CSV 列：{', '.join(info.columns)}")
        except Exception as exc:
            self._show_parameter_error(exc)

    def _add_source(self) -> None:
        dialog = SourceDialog(self.root, self.available_sheets)
        self.root.wait_window(dialog)
        if dialog.result:
            self.sources.append(dialog.result)
            self._populate_sources()

    def _edit_source(self) -> None:
        selection = self.source_tree.selection()
        if not selection:
            messagebox.showinfo("编辑来源", "请先选择一条 Excel 来源。", parent=self.root)
            return
        index = int(selection[0])
        dialog = SourceDialog(self.root, self.available_sheets, self.sources[index])
        self.root.wait_window(dialog)
        if dialog.result:
            self.sources[index] = dialog.result
            self._populate_sources()

    def _remove_source(self) -> None:
        selection = self.source_tree.selection()
        if not selection:
            return
        del self.sources[int(selection[0])]
        self._populate_sources()

    def _populate_sources(self) -> None:
        for item in self.source_tree.get_children():
            self.source_tree.delete(item)
        for index, source in enumerate(self.sources):
            self.source_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(source.sheet_name, ", ".join(source.keyword_columns), source.pdf_name_column or ""),
            )

    def _choose_highlight_color(self) -> None:
        color = colorchooser.askcolor(self.highlight_color_var.get(), title="选择底纹颜色", parent=self.root)[1]
        if color:
            self.highlight_color_var.set(color.upper())
            self._sync_color_button(self.highlight_color_button, color)

    def _choose_border_color(self) -> None:
        color = colorchooser.askcolor(self.border_color_var.get(), title="选择边框颜色", parent=self.root)[1]
        if color:
            self.border_color_var.set(color.upper())
            self._sync_color_button(self.border_color_button, color)

    @staticmethod
    def _sync_color_button(button: tk.Button, color: str) -> None:
        """让颜色选择按钮显示当前颜色，并自动选择可读的文字颜色。"""
        red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
        foreground = "#000000" if red * 299 + green * 587 + blue * 114 >= 150000 else "#FFFFFF"
        button.configure(
            background=color,
            activebackground=color,
            foreground=foreground,
            activeforeground=foreground,
        )

    def _collect_settings(self) -> MarkPdfsSettings:
        try:
            opacity = float(self.highlight_opacity_var.get())
            width = float(self.border_width_var.get())
            aspect_ratio = float(self.box_aspect_ratio_var.get())
            box_size = float(self.box_size_var.get())
            box_scale = float(self.box_scale_var.get())
        except ValueError as exc:
            raise ValueError("透明度、线宽、长宽比、大小和放大倍数必须是数字") from exc
        if not 0 <= opacity <= 1:
            raise ValueError("底纹透明度必须在 0 到 1 之间")
        if width <= 0:
            raise ValueError("边框线宽必须大于 0")
        if aspect_ratio <= 0 or box_size <= 0 or box_scale <= 0:
            raise ValueError("长宽比、大小和放大倍数必须大于 0")
        highlight_enabled = self.highlight_enabled_var.get()
        border_enabled = self.border_enabled_var.get()
        if not highlight_enabled and not border_enabled:
            raise ValueError("底纹和边框至少启用一项")
        keyword_file = resolve_path(self.context.project_root, self.keyword_file_var.get())
        excel_sources = tuple(self.sources) if keyword_file.suffix.casefold() == ".xlsx" else ()
        if keyword_file.suffix.casefold() == ".xlsx" and not excel_sources:
            raise ValueError("Excel 文件至少需要配置一条 Sheet/关键词列来源")
        return self.context.mark_pdfs.with_overrides(
            keyword_file=keyword_file,
            pdf_dir=resolve_path(self.context.project_root, self.pdf_dir_var.get()),
            output_dir=resolve_path(self.context.project_root, self.output_dir_var.get()),
            recursive=self.recursive_var.get(),
            excel_sources=excel_sources,
            sheet_name="",
            keyword_column="" if excel_sources else self.csv_keyword_var.get().strip(),
            pdf_name_column=self.csv_pdf_var.get().strip() or None,
            highlight_enabled=highlight_enabled,
            highlight_color=parse_hex_color(self.highlight_color_var.get()),
            highlight_opacity=opacity,
            border_enabled=border_enabled,
            border_color=parse_hex_color(self.border_color_var.get()),
            border_width=width,
            box_aspect_ratio=aspect_ratio,
            box_size=box_size,
            box_scale=box_scale,
        )

    def _collect_a3_settings(self) -> A3SplitSettings:
        try:
            preview_pixels = int(self.a3_preview_pixels_var.get())
            top_left_offset_x_mm = float(self.a3_top_left_offset_x_var.get())
            top_left_offset_y_mm = float(self.a3_top_left_offset_y_var.get())
            bottom_right_offset_x_mm = float(self.a3_bottom_right_offset_x_var.get())
            bottom_right_offset_y_mm = float(self.a3_bottom_right_offset_y_var.get())
            overlap_mm = float(self.a3_overlap_var.get())
            margin_mm = float(self.a3_margin_var.get())
        except ValueError as exc:
            raise ValueError("AI 预览像素必须是整数，偏移、重叠和边距必须是数字") from exc
        base_url = self.a3_base_url_var.get().strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("本地 AI API 地址必须以 http:// 或 https:// 开头")
        if preview_pixels < 512 or preview_pixels > 4096:
            raise ValueError("AI 预览最长边必须在 512 到 4096 之间")
        offsets = (
            top_left_offset_x_mm,
            top_left_offset_y_mm,
            bottom_right_offset_x_mm,
            bottom_right_offset_y_mm,
        )
        if any(value < -100 or value > 100 for value in offsets):
            raise ValueError("左上角和右下角的 X/Y 偏移必须在 -100 到 100 mm 之间")
        if overlap_mm < 0 or overlap_mm > 100:
            raise ValueError("接缝重叠必须在 0 到 100 mm 之间")
        if margin_mm < 0 or margin_mm > 50:
            raise ValueError("页面边距必须在 0 到 50 mm 之间")
        input_dir = resolve_path(self.context.project_root, self.a3_input_dir_var.get())
        output_dir = resolve_path(self.context.project_root, self.a3_output_dir_var.get())
        top_left_reference = resolve_path(self.context.project_root, self.a3_top_left_reference_var.get())
        bottom_right_reference = resolve_path(self.context.project_root, self.a3_bottom_right_reference_var.get())
        if input_dir == output_dir:
            raise ValueError("A3 输出目录不能与 PDF 输入目录相同")
        references = (("左上角 L/1 轴网", top_left_reference), ("右下角图签", bottom_right_reference))
        for label, path in references:
            if not path.is_file():
                raise FileNotFoundError(f"{label}参考截图不存在：{path}")
            if path.suffix.casefold() != ".png":
                raise ValueError(f"{label}参考截图必须是 PNG 文件")
        return self.context.a3_split.with_overrides(
            input_dir=input_dir,
            output_dir=output_dir,
            recursive=self.a3_recursive_var.get(),
            ai_base_url=base_url,
            ai_model=self.a3_model_var.get().strip(),
            top_left_reference_image=top_left_reference,
            bottom_right_reference_image=bottom_right_reference,
            preview_max_pixels=preview_pixels,
            top_left_offset_x_mm=top_left_offset_x_mm,
            top_left_offset_y_mm=top_left_offset_y_mm,
            bottom_right_offset_x_mm=bottom_right_offset_x_mm,
            bottom_right_offset_y_mm=bottom_right_offset_y_mm,
            overlap_mm=overlap_mm,
            margin_mm=margin_mm,
        )

    def _preview(self) -> None:
        try:
            settings = self._collect_settings()
            if settings.keyword_file.suffix.casefold() == ".xlsx":
                keywords = read_excel_keywords(settings.keyword_file, settings.excel_sources, settings.pdf_name_column)
            else:
                keywords = read_keywords(
                    settings.keyword_file,
                    keyword_column=settings.keyword_column,
                    pdf_name_column=settings.pdf_name_column,
                )
            pdfs = discover_pdfs(settings.pdf_dir, settings.output_dir, settings.recursive)
            styles = []
            if settings.highlight_enabled:
                styles.append(f"底纹 {color_to_hex(settings.highlight_color)} / 透明度 {settings.highlight_opacity:g}")
            if settings.border_enabled:
                styles.append(f"边框 {color_to_hex(settings.border_color)} / 线宽 {settings.border_width:g}")
            styles.append(
                f"矩形 长宽比 {settings.box_aspect_ratio:g} / 大小 {settings.box_size:g}pt / "
                f"放大 {settings.box_scale:g} 倍"
            )
            self._append_log(
                "SUCCESS",
                f"参数预览：{len(keywords)} 个关键词，{len(pdfs)} 个 PDF；{'；'.join(styles)}；输出到 {settings.output_dir}",
            )
        except Exception as exc:
            self._show_parameter_error(exc)

    def _preview_a3(self) -> None:
        try:
            settings = self._collect_a3_settings()
            pdfs = discover_split_pdfs(settings.input_dir, settings.output_dir, settings.recursive)
            model = settings.ai_model or "自动选择"
            self._append_log(
                "SUCCESS",
                f"A3 参数预览：{len(pdfs)} 个 PDF；AI 自动旋正后左右拆分；重叠 {settings.overlap_mm:g} mm；"
                f"左上偏移 X={settings.top_left_offset_x_mm:g} / Y={settings.top_left_offset_y_mm:g} mm；"
                f"右下角偏移 X={settings.bottom_right_offset_x_mm:g} / Y={settings.bottom_right_offset_y_mm:g} mm；"
                f"参考图 {settings.top_left_reference_image.name} + {settings.bottom_right_reference_image.name}；"
                f"边距 {settings.margin_mm:g} mm；"
                f"模型 {model}；输出到 {settings.output_dir}",
            )
        except Exception as exc:
            self._show_parameter_error(exc)

    def _start(self) -> None:
        try:
            settings = self._collect_settings()
        except Exception as exc:
            self._show_parameter_error(exc)
            return
        self.cancel_event.clear()
        self.started_at = time.monotonic()
        self.elapsed_var.set("00:00:00")
        self.status_var.set("扫描")
        self.current_var.set("正在读取关键词和 PDF")
        self.progress.configure(value=0, maximum=1)
        self._set_running(True, self.cancel_button)
        self.worker = threading.Thread(target=self._worker_run, args=(settings,), daemon=True)
        self.worker.start()
        self._update_elapsed()

    def _worker_run(self, settings: MarkPdfsSettings) -> None:
        try:
            result = run(
                settings,
                on_progress=lambda current, total, name: self.events.put(("progress", current, total, name)),
                on_message=lambda level, text: self.events.put(("log", level, text)),
                is_cancelled=self.cancel_event.is_set,
            )
            self.events.put(("done", result))
        except Exception as exc:
            logger.exception("GUI 任务执行失败")
            self.events.put(("error", exc))

    def _start_a3(self) -> None:
        try:
            settings = self._collect_a3_settings()
        except Exception as exc:
            self._show_parameter_error(exc)
            return
        self.cancel_event.clear()
        self.started_at = time.monotonic()
        self.elapsed_var.set("00:00:00")
        self.status_var.set("扫描")
        self.current_var.set("正在连接本地 AI 并扫描 PDF")
        self.progress.configure(value=0, maximum=1)
        self._set_running(True, self.a3_cancel_button)
        self.worker = threading.Thread(target=self._worker_run_a3, args=(settings,), daemon=True)
        self.worker.start()
        self._update_elapsed()

    def _worker_run_a3(self, settings: A3SplitSettings) -> None:
        try:
            result = run_a3_split(
                settings,
                on_progress=lambda current, total, name: self.events.put(("progress", current, total, name)),
                on_message=lambda level, text: self.events.put(("log", level, text)),
                is_cancelled=self.cancel_event.is_set,
            )
            self.events.put(("done_a3", result))
        except Exception as exc:
            logger.exception("GUI A3 拆分任务执行失败")
            self.events.put(("error", exc))

    def _cancel(self) -> None:
        if self.running:
            self.cancel_event.set()
            self.status_var.set("正在取消")
            self.current_var.set("等待当前文件安全结束")
            if self.active_cancel_button is not None:
                self.active_cancel_button.state(["disabled"])

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._append_log(str(event[1]), str(event[2]))
                elif kind == "progress":
                    current, total, name = int(event[1]), int(event[2]), str(event[3])
                    self.progress.configure(maximum=max(total, 1), value=current)
                    self.status_var.set("运行")
                    self.current_var.set(f"{current}/{total}  {name}")
                elif kind == "done":
                    self._finish_success(event[1])
                elif kind == "done_a3":
                    self._finish_a3_success(event[1])
                elif kind == "error":
                    self._finish_error(event[1])
        except queue.Empty:
            pass
        if self.closing and not self.running:
            self.root.destroy()
            return
        self.root.after(100, self._poll_events)

    def _finish_success(self, result: ProcessingResult) -> None:
        self._set_running(False)
        self._set_elapsed()
        if result.cancelled:
            self.status_var.set("已取消")
            self.current_var.set(f"已输出 {result.output_pdf_count} 个 PDF")
            return
        self.status_var.set("完成")
        self.current_var.set(f"输出 {result.output_pdf_count} 个 PDF，匹配 {result.total_match_count} 处")
        messagebox.showinfo(
            "处理完成",
            f"已输出 {result.output_pdf_count} 个 PDF\n匹配 {result.total_match_count} 处\n"
            f"未命中 {len(result.unmatched_keywords)} 个\n失败 {len(result.failures)} 个\n\n报告：{result.report_path}",
            parent=self.root,
        )

    def _finish_a3_success(self, result: A3SplitResult) -> None:
        self._set_running(False)
        self._set_elapsed()
        if result.cancelled:
            self.status_var.set("已取消")
            self.current_var.set(f"已输出 {result.output_pdf_count} 个 A3 PDF")
            return
        self.status_var.set("完成")
        self.current_var.set(f"输出 {result.output_pdf_count} 个 PDF，共 {result.output_page_count} 张 A3")
        messagebox.showinfo(
            "A3 拆分完成",
            f"已输出 {result.output_pdf_count} 个 PDF\n"
            f"源页面 {result.source_page_count} 页\n旋正页面 {result.rotated_page_count} 页\n"
            f"A3 页面 {result.output_page_count} 页\n"
            f"失败 {len(result.failures)} 个",
            parent=self.root,
        )

    def _finish_error(self, error: object) -> None:
        self._set_running(False)
        self._set_elapsed()
        self.status_var.set("失败")
        self.current_var.set(str(error))
        self._append_log("ERROR", f"任务失败：{error}")
        messagebox.showerror("任务失败", str(error), parent=self.root)

    def _set_running(self, running: bool, active_cancel: ttk.Button | None = None) -> None:
        self.running = running
        self.active_cancel_button = active_cancel if running else None
        for widget in self.input_widgets:
            if isinstance(widget, ttk.Widget):
                widget.state(["disabled"] if running else ["!disabled"])
            else:
                widget.configure(state="disabled" if running else "normal")
        for button in self.start_buttons:
            button.state(["disabled"] if running else ["!disabled"])
        for button in self.cancel_buttons:
            if running and button is active_cancel:
                button.state(["!disabled"])
            else:
                button.state(["disabled"])

    def _update_elapsed(self) -> None:
        if not self.running:
            return
        self._set_elapsed()
        self.root.after(500, self._update_elapsed)

    def _set_elapsed(self) -> None:
        elapsed = max(0, int(time.monotonic() - self.started_at)) if self.started_at else 0
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _append_log(self, level: str, text: str) -> None:
        level = level if level in {"INFO", "WARNING", "ERROR", "SUCCESS"} else "INFO"
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp} [{level}] {text}\n", level)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _show_parameter_error(self, error: Exception) -> None:
        self._append_log("ERROR", f"参数错误：{error}")
        messagebox.showerror("参数错误", str(error), parent=self.root)

    def _on_close(self) -> None:
        if not self.running:
            self.root.destroy()
            return
        if messagebox.askyesno("任务正在运行", "是否安全取消任务并在结束后关闭窗口？", parent=self.root):
            self.closing = True
            self._cancel()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-file", help="公开 YAML 配置文件，默认使用根目录 config.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        context = bootstrap_context(__file__, args.config_file)
        setup_logger(context.log_level)
        root = tk.Tk()
        PdfTextMarkerApp(root, context)
        root.mainloop()
        return 0
    except Exception as exc:
        logger.exception("GUI 启动失败")
        try:
            messagebox.showerror("启动失败", str(exc))
        except tk.TclError:
            print(f"GUI 启动失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
