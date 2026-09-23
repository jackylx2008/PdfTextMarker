# A3 图框识别与拆分指南

## 功能范围

本工作流面向建筑图纸 PDF。程序调用本地 OpenAI 兼容多模态服务识别页面方向、左上轴网锚点和右下图签边界，将有效范围左右等分，并排版到两个标准 A3 页面。

原始 PDF 不会被修改。模型只分析受限尺寸的 PNG 预览，正式输出通过 PyMuPDF 引用源 PDF 的矢量页面区域，因此文字、线条和可搜索文本会尽量保留。

首版不包含扫描件 OCR。无可靠文字层不影响视觉模型分析，但模型能否识别低清扫描图仍取决于图片质量。

## 参考图片

默认读取：

- `input/识别图纸左上角.png`：包含左上定位所需的字母轴圆圈与数字轴圆圈。
- `input/识别图纸右下角.png`：包含完整右下图签外框。

`input/` 不参与 Git 同步。其他电脑首次运行时，应自行放入参考图，或者在 GUI 中选择对应文件。参考图中的红框可以保留，它有助于模型理解目标特征。

左上参考图中的字符只是视觉示例。不同图纸可能从其他数字轴开始，例如主平面图从 12 轴开始时，模型应识别该主图最左侧数字轴，而不是强制寻找字符 1。

## 坐标计算

模型返回以下原始页面标准化坐标，取值范围均为 0 到 1：

- 页面正式图框 `bbox`。
- 字母轴圆圈 `left_axis_bbox`。
- 数字轴圆圈 `top_axis_bbox`。
- 兼容核对框 `top_left_feature_bbox`。
- 右下图签 `title_block_bbox`。
- 顺时针旋转角度 `rotation_clockwise`，只能为 `0`、`90`、`180`、`270`。

程序先把所有坐标换算到旋正后的页面，再按以下规则计算打印范围：

```text
左上 X = 数字轴圆圈中心 X + 左上 X 偏移
左上 Y = 字母轴圆圈中心 Y + 左上 Y 偏移
右下 X = 图签框右边界 X + 右下 X 偏移
右下 Y = 图签框下边界 Y + 右下 Y 偏移
```

最终矩形会被限制在页面范围内，然后沿水平方向等分。接缝重叠量会平均扩展到左右两块。

## 防误识别逻辑

整页中可能出现大量数字、详图编号、日期和图签文字。程序采用以下保护：

1. 提示模型分别识别字母轴圆圈和主轴序列最左端的数字轴圆圈。
2. 若计算出的左上交点落到右下图签区域，自动截取页面左侧 55%，放大后进行第二次专用轴网识别。
3. 第二次结果会换算回整页标准化坐标。
4. 如果交点仍不满足几何约束，该页识别失败，不生成可能错误的 A3 页面；批处理继续处理其他文件。

几何校验只负责拦截明显错误。正式批量拆分前，建议先生成诊断截图进行人工确认。

## GUI 使用

运行：

```powershell
python pdf_text_marker_gui.py
```

打开“AI 图框拆分 A3”选项卡，设置 PDF 输入输出目录、递归搜索、本地 AI 地址与模型、两张参考图片、四个边界偏移、接缝重叠、打印边距和预览图最大像素。

API Key 不显示在 GUI 中，只从 `common.env` 的 `LLAMACPP_API_KEY` 读取。

## 命令行拆分

```powershell
python split_a3_pdfs.py
python split_a3_pdfs.py --input-dir path/to/pdfs --output-dir path/to/a3_output
```

输出文件名为 `<原文件名>_a3.pdf`。输入目录中的 `_a3.pdf` 和 A3 输出目录会自动排除，避免重复处理。

## AI 边界诊断

```powershell
python diagnose_a3_bounds.py "path/to/sample_marked.pdf"
python diagnose_a3_bounds.py "path/to/sample_marked.pdf" --page 2 --output "output/diagnostics/page2.png"
```

颜色含义：

- 绿色框：字母轴与数字轴圆圈。
- 绿色十字圆点：两个轴网中心线形成的左上锚点。
- 红色框：右下图签。
- 红色十字圆点：图签右下锚点。
- 紫色框：应用旋转和偏移后的最终打印范围。
- 蓝色虚线：左右两张 A3 的分割位置。

截图左上角同时显示旋转角度、标准化坐标和图例。诊断文件默认写入 `output/diagnostics/`，该目录不参与 Git 同步。

## 配置

公开默认值写入 `config.yaml`；真实路径和 API Key 写入不提交的 `common.env`。配置优先级为：进程环境变量、`common.env`、`config.yaml` 默认值。

关键变量包括：

- `PDF_SPLIT_INPUT_DIR`、`PDF_SPLIT_OUTPUT_DIR`
- `LLAMACPP_BASE_URL`、`LLAMACPP_MODEL`、`LLAMACPP_API_KEY`
- `A3_TOP_LEFT_REFERENCE_IMAGE`、`A3_BOTTOM_RIGHT_REFERENCE_IMAGE`

## 测试

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
python -m pytest -q
python -m compileall -q src split_a3_pdfs.py diagnose_a3_bounds.py pdf_text_marker_gui.py
```

单元测试使用临时 PDF 和模拟视觉结果，不读取或修改真实业务文件。真实模型效果通过 `diagnose_a3_bounds.py` 生成的本地截图人工复核。

## 常见问题

- 提示参考图不存在：检查 GUI 路径，或在 `common.env` 设置两个 `A3_*_REFERENCE_IMAGE` 变量。
- 鉴权失败：检查私有 `LLAMACPP_API_KEY`，不要把真实 Key 写入公开配置或 Git。
- 模型选择错误数字轴：先检查诊断截图；明显落入图签区时程序会自动二次识别，其他误差可通过更清晰的参考图或偏移量修正。
- 页面方向错误：确认模型支持图片输入，并适当提高 `preview_max_pixels`。
- 单个 PDF 损坏或加密：该文件会记录为失败，批处理继续执行。
