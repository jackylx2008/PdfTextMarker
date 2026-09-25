# PDF Text Marker

从 XLSX 或 CSV 读取关键词，在带文字层的 PDF 中查找全部匹配位置，为关键词添加可配置的底纹和边框，并生成 HTML 汇总报告。原始 PDF 不会被修改。

项目还提供“AI 图框拆分 A3”工作流：调用本地多模态模型识别建筑图纸的正式图框范围和阅读方向，先把页面旋正为横向，再左右切成两半并分别排版为标准 A3 页面，方便本地打印。

## 本次更新

- 新增“AI 图框拆分 A3”GUI 选项卡及独立命令行入口。
- 使用左上轴网参考图，以及包含右侧字母轴、下方数字轴和相邻图签的右下参考图，辅助本地 AI 确定有效打印范围。
- 左上、右下均由两个轴网圆圈的中心线交点计算，不再把单个圆圈中心直接作为边界点。
- 右侧字母轴与下方数字轴分开识别；额外生成去彩黑线裁图，排除红章、蓝章和其他彩色图块。
- 增加左侧局部二次识别、图签区域校验和双轴相对位置校验，阻止明显错误的边界进入拆分流程。
- 诊断截图同时标出两组轴号、交点、图签、最终范围和分割线；诊断日志放在页面正上方中央，避免遮挡左上定位点。
- API Key 仅从私有 `common.env` 读取，不在 GUI 中显示。
- 本地 AI 服务由用户启动和管理；本项目只检查并调用已配置的服务，不会自行启动或重启运行时。
- AI 只分析页面预览图，A3 文件直接裁切原 PDF，保留矢量线条和可搜索文字。

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
- 支持本地 OpenAI 兼容多模态服务识别建筑图框和页面方向，每个源页面旋正后输出两张 A3 页面。

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

### AI 图框拆分 A3

切换到“AI 图框拆分 A3”选项卡，可设置：

- 待处理 PDF 目录、A3 输出目录和递归搜索。
- 本地 AI API 地址和模型名；模型名留空时自动选择服务返回的第一个模型。
- API Key 不在 GUI 中显示或编辑，统一从私有 `common.env` 的 `LLAMACPP_API_KEY` 读取。
- AI 自动判断页面需要顺时针旋转 `0/90/180/270°`，旋正为横向后固定左右拆分。
- 默认从 `output/` 读取关键词标注生成的 `_marked.pdf`；已生成的 `_a3.pdf` 和 A3 输出子目录会自动排除。
- 左上角轴网参考截图、右下双轴交点与图签组合参考截图，以及两端独立的 X/Y 偏移量。
- 接缝重叠、A3 打印边距和发给 AI 的预览图最长边像素。

处理流程如下：

1. 将每个源页面渲染为受限尺寸的 PNG 预览图。
2. 同时发送左上角轴网参考图、右下组合参考图和待处理整页。
3. AI 返回左上两个轴网圆圈、右侧字母轴圆圈、下方数字轴圆圈、相邻图签及旋正角度，程序同步旋转页面和特征框。
4. 左上锚点取上方数字轴中心 X 与左侧字母轴中心 Y；右下锚点取下方数字轴中心 X 与右侧字母轴中心 Y。两个交点分别叠加 X/Y 偏移得到最终打印范围，圆圈内的具体轴号不作为硬性判断条件。
5. 源 PDF 不修改，输出名增加 `_a3` 后缀。

参考截图默认路径分别为 `input/识别图纸左上角.png` 和 `input/识别图纸右下角.png`。`input/` 被 Git 忽略，避免把实际项目图纸同步到远端；也可以在 GUI 中选择其他 PNG 文件。

预览图仅用于 AI 定位，最终 A3 页面通过 PyMuPDF 直接引用源 PDF 页面区域，因此不会因为预览分辨率而栅格化。单个文件识别或处理失败时会记录错误并继续处理其他文件。

完整识别规则、颜色图例、诊断方式和故障处理见 [A3 图框识别与拆分指南](docs/A3_SPLIT_GUIDE.md)。

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

A3 拆分也提供薄命令行入口：

```powershell
python split_a3_pdfs.py --help
python split_a3_pdfs.py --input-dir path/to/pdfs --output-dir path/to/a3_output
```

正式拆分前可对任意一页执行 AI 边界诊断：

```powershell
python diagnose_a3_bounds.py "output/示例_marked.pdf"
python diagnose_a3_bounds.py "output/示例_marked.pdf" --page 2 --output "output/diagnostics/page2.png"
```

诊断 PNG 会把 AI 结果直接画在旋正后的页面上：绿色为左上两个轴网圆圈、辅助虚线及其交点；红色分别框出右侧字母轴、下方数字轴，并以虚线连接二者交点；橙色虚线为辅助定位的图签；紫色为最终打印范围；蓝色虚线为左右 A3 分割线。坐标日志位于页面正上方中央，不遮挡左侧轴号。校验不通过时不会生成错误的拆分结果。

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

  a3_split:
    input_dir: "${PDF_SPLIT_INPUT_DIR:-./output}"
    output_dir: "${PDF_SPLIT_OUTPUT_DIR:-./output/a3_split}"
    recursive: true
    ai_base_url: "${LLAMACPP_BASE_URL:-http://127.0.0.1:8080/v1}"
    ai_model: "${LLAMACPP_MODEL:-}"
    ai_api_key: "${LLAMACPP_API_KEY:-}"
    top_left_reference_image: "${A3_TOP_LEFT_REFERENCE_IMAGE:-./input/识别图纸左上角.png}"
    bottom_right_reference_image: "${A3_BOTTOM_RIGHT_REFERENCE_IMAGE:-./input/识别图纸右下角.png}"
    preview_max_pixels: 2400
    top_left_offset_x_mm: 0.0
    top_left_offset_y_mm: 0.0
    bottom_right_offset_x_mm: 0.0
    bottom_right_offset_y_mm: 0.0
    overlap_mm: 0.0
    margin_mm: 5.0
```

矩形计算规则：最终宽度为 `box_size × box_scale`，最终高度为“最终宽度 ÷ `box_aspect_ratio`”。底纹和边框使用同一个矩形，并以关键词文字框中心为中心点。

真实机器路径放在不参与 Git 同步的 `common.env`：

```dotenv
KEYWORD_FILE=path/to/keywords.xlsx
PDF_DIR=path/to/pdfs
OUTPUT_DIR=path/to/output
PDF_SPLIT_INPUT_DIR=./output
PDF_SPLIT_OUTPUT_DIR=path/to/a3_output
LLAMACPP_BASE_URL=http://127.0.0.1:8080/v1
LLAMACPP_MODEL=local-vision-model
LLAMACPP_API_KEY=your-private-api-key
A3_TOP_LEFT_REFERENCE_IMAGE=./input/识别图纸左上角.png
A3_BOTTOM_RIGHT_REFERENCE_IMAGE=./input/识别图纸右下角.png
```

配置优先级为：进程环境变量、`common.env`、`config.yaml` 默认值。

## 输出

- 标注 PDF：`output/<原文件名>_marked.pdf`
- HTML 报告：`output/pdf_text_marker_report.html`
- A3 拆分 PDF：`output/a3_split/<原文件名>_a3.pdf`
- AI 边界诊断截图：`output/diagnostics/<原文件名>_page_<页码>_bounds.png`
- 运行日志：`logs/<入口文件名>.log`

`input/`、`output/`、`logs/`、`common.env` 和本地虚拟环境均被 Git 忽略。

## 测试

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
python -m pytest -q
python -m compileall -q src mark_pdfs.py split_a3_pdfs.py diagnose_a3_bounds.py pdf_text_marker_gui.py
```

测试使用临时数据，不修改真实输入目录。
