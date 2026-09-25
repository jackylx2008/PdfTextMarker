# 本地 AI 服务配置

## 接口要求

A3 图框识别使用 OpenAI 兼容的多模态 HTTP 接口。服务必须提供：

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `image_url` 图片输入能力

默认地址为 `http://127.0.0.1:8080/v1`。可使用支持视觉模型的 `llama.cpp` 服务或其他兼容实现。

本地 AI 服务的生命周期由用户管理。项目只连接配置中的现有服务并执行只读健康检查；服务不可用时会报告错误，不会自行启动、停止、重启、替换或升级 LM Studio、`llama-server` 或备用运行时。

## 私有配置

在项目根目录的 `common.env` 中配置本机参数：

```dotenv
LLAMACPP_BASE_URL=http://127.0.0.1:8080/v1
LLAMACPP_MODEL=local-vision-model
LLAMACPP_API_KEY=your-private-api-key
A3_TOP_LEFT_REFERENCE_IMAGE=./input/识别图纸左上角.png
A3_BOTTOM_RIGHT_REFERENCE_IMAGE=./input/识别图纸右下角.png
```

注意：

- `common.env` 已被 Git 忽略。
- API Key 不在 GUI 中显示或编辑。
- 不要把真实 API Key、模型绝对路径或本机目录写入 `config.yaml`、`common.env.example` 或项目文档。
- 模型名留空时，程序会选择 `/v1/models` 返回的第一个模型。

## 服务检查

启动本地服务后，可在 PowerShell 中检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

随后使用项目诊断入口进行一次真实图片请求：

```powershell
python diagnose_a3_bounds.py "path/to/sample.pdf"
```

成功时控制台会显示模型名、旋转角度、左右锚点轴网坐标、图签坐标和诊断 PNG 路径。

## 视觉模型输入

整页布局识别发送三张图片：

1. 左上轴网参考图。
2. 右侧字母轴、下方数字轴与相邻图签组合参考图。
3. 当前 PDF 页的受限尺寸 PNG 预览。

若整页识别的左上交点异常，程序会追加一次页面左侧局部识别。右下角固定拆成两个独立请求：右侧裁图识别连接水平轴线的字母轴圆圈，底部裁图识别连接向上竖线的数字轴圆圈；两个请求均可使用原色图和去彩黑线图排除印章干扰，最后组合成轴线交点。正式 A3 输出不会使用模型看到的低分辨率图片，而是直接裁切源 PDF 页面。

## 常见错误

- `/health` 不可用：确认服务进程、端口和防火墙设置。
- 模型列表为空：检查视觉模型是否已经加载。
- HTTP 401：检查 `common.env` 中的 `LLAMACPP_API_KEY`。
- 返回内容不是 JSON：确认模型服从结构化输出提示；客户端可以提取 Markdown 代码块以及回复中的第一个有效 JSON 对象。
- 无法识别图片：确认模型加载了视觉投影组件，并支持 OpenAI `image_url` 内容格式。
- 中文控制台乱码：通过项目根目录入口运行；入口会调用统一 UTF-8 输出配置。

图框识别、坐标规则和诊断截图说明见 [A3 图框识别与拆分指南](A3_SPLIT_GUIDE.md)。
