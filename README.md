# PDF Text Marker

从 XLSX 或 CSV 读取关键词，在带文字层的 PDF 中查找全部匹配位置，为关键词添加可配置的底纹和边框，并生成 HTML 汇总报告。原始 PDF 不会被修改。

## 本次更新

- GUI 的输出目录区域新增“一键清空输出”按钮，方便重新执行批量标注任务。
- 清空前展示目标目录、文件数、子目录数和总大小，并要求用户二次确认。
- 清空操作只删除输出目录内部内容，保留输出目录本身。
- 增加危险路径保护，避免误清空磁盘根目录、用户主目录、项目目录或 PDF 输入目录。

## 功能

- 支持 XLSX 和 CSV 关键词文件。
- Excel 可同时读取多个 Sheet 和多个关键词列。
- 自动忽略空值并去重；删除中英文冒号及其后的说明文字。
- 递归扫描 PDF 目录，标注每一处匹配。
- 底纹和边框可独立启用，并可配置颜色、透明度和线宽。
- 以关键词为中心绘制同尺寸的底纹矩形和边框，可配置长宽比、基础大小和放大倍数。
- 只输出有匹配结果的 PDF，文件名增加 `_marked` 后缀。
- 生成包含匹配明细、未命中关键词和失败文件的 HTML 报告。
- 提供 Tkinter 桌面 GUI 和命令行入口。

## 安装

建议在项目根目录创建本地虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS 或 Linux 使用对应虚拟环境中的 `python` 可执行文件。

## GUI 使用

运行：

```powershell
python pdf_text_marker_gui.py
```

窗口提供以下参数：

- XLSX/CSV 关键词文件。
- Excel Sheet、关键词列和可选 PDF 文件名列。
- CSV 关键词列和可选 PDF 文件名列。
- PDF 输入目录、输出目录和递归搜索。
- 底纹启用状态、颜色和透明度；颜色按钮直接显示当前颜色。
- 边框启用状态、颜色和线宽；颜色按钮直接显示当前颜色。
- 标注矩形长宽比、基础大小（PDF 点）和放大倍数。
- “一键清空输出”按钮；确认后删除输出目录中的全部内容，但保留输出目录本身。

“参数预览”只检查配置和文件数量；“开始执行”在后台线程运行；“取消任务”会在安全边界停止，不会强制中断正在写入的 PDF。

“一键清空输出”会先显示目标目录、文件数量、子目录数量和总大小，并要求再次确认。程序拒绝清空磁盘根目录、用户主目录、项目根目录、PDF 输入目录及包含这些位置的上级目录。

## 命令行使用

使用配置文件中的默认值：

```powershell
python mark_pdfs.py
```

临时覆盖路径：

```powershell
python mark_pdfs.py --keyword-file path/to/keywords.xlsx --pdf-dir path/to/pdfs --output-dir path/to/output
```

查看全部参数：

```powershell
python mark_pdfs.py --help
```

## 配置

公开默认值位于 `config.yaml`，该文件可以同步到 GitHub。多 Sheet 示例：

```yaml
flows:
  mark_pdfs:
    keyword_file: "${KEYWORD_FILE:-./input/keywords.xlsx}"
    pdf_dir: "${PDF_DIR:-./input}"
    output_dir: "${OUTPUT_DIR:-./output}"
    recursive: true
    excel_sources:
      - sheet_name: "Sheet1"
        keyword_columns: ["A", "C"]
      - sheet_name: "Sheet2"
        keyword_columns: ["关键词"]
    highlight_enabled: true
    highlight_color: "#FFFF00"
    highlight_opacity: 0.35
    border_enabled: true
    border_color: "#FF0000"
    border_width: 1.25
    box_aspect_ratio: 4.0
    box_size: 40.0
    box_scale: 1.0
```

矩形计算规则：最终宽度为 `box_size × box_scale`，最终高度为“最终宽度 ÷ `box_aspect_ratio`”。底纹和边框使用同一个矩形，并以关键词文字框中心为中心点。

真实机器路径放在不参与 Git 同步的 `common.env`：

```dotenv
KEYWORD_FILE=path/to/keywords.xlsx
PDF_DIR=path/to/pdfs
OUTPUT_DIR=path/to/output
```

配置优先级为：进程环境变量、`common.env`、`config.yaml` 默认值。

## 输出

- 标注 PDF：`output/<原文件名>_marked.pdf`
- HTML 报告：`output/pdf_text_marker_report.html`
- 运行日志：`logs/<入口文件名>.log`

`input/`、`output/`、`logs/`、`common.env` 和本地虚拟环境均被 Git 忽略。

## 测试

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
python -m pytest -q
python -m compileall -q src mark_pdfs.py pdf_text_marker_gui.py
```

测试使用临时数据，不修改真实输入目录。
