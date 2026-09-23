"""OpenAI 兼容本地多模态服务的建筑图框识别客户端。"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

import fitz


class VisionServiceError(RuntimeError):
    """本地视觉模型不可用或返回内容无效。"""


@dataclass(frozen=True, slots=True)
class NormalizedBounds:
    """以页面左上角为原点的 0-1 标准化边界框。"""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        values = (self.x0, self.y0, self.x1, self.y1)
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("图框坐标必须在 0 到 1 之间")
        if self.x1 - self.x0 < 0.001 or self.y1 - self.y0 < 0.001:
            raise ValueError("AI 返回的坐标范围过小")


@dataclass(frozen=True, slots=True)
class PageLayoutAnalysis:
    """AI 对原始页面图框和顺时针纠正角度的判断。"""

    bounds: NormalizedBounds
    rotation: int
    top_left_feature_bounds: NormalizedBounds
    title_block_bounds: NormalizedBounds
    left_axis_bounds: NormalizedBounds | None = None
    top_axis_bounds: NormalizedBounds | None = None

    def __post_init__(self) -> None:
        if self.rotation not in {0, 90, 180, 270}:
            raise ValueError("页面旋转角度只能是 0、90、180 或 270")
        if self.bounds.x1 - self.bounds.x0 < 0.1 or self.bounds.y1 - self.bounds.y0 < 0.1:
            raise ValueError("AI 返回的整体图框范围过小")


class LocalVisionClient:
    """调用 llama.cpp 的 OpenAI 兼容视觉接口。"""

    def __init__(self, base_url: str, model: str = "", api_key: str = "", timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.timeout = timeout

    def check_ready(self) -> str:
        """检查服务并返回将要使用的模型名。"""
        health_url = self.base_url.removesuffix("/v1") + "/health"
        self._request_json(health_url)
        models = self._request_json(self.base_url + "/models")
        available = [str(item.get("id", "")).strip() for item in models.get("data", []) if isinstance(item, dict)]
        available = [item for item in available if item]
        if self.model:
            if available and self.model not in available:
                raise VisionServiceError(f"配置模型不在服务模型列表中：{self.model}")
            return self.model
        if not available:
            raise VisionServiceError("本地 AI 服务没有返回可用模型")
        self.model = available[0]
        return self.model

    def detect_page_layout(
        self,
        png_bytes: bytes,
        page_number: int,
        top_left_reference_png: bytes | None = None,
        bottom_right_reference_png: bytes | None = None,
    ) -> PageLayoutAnalysis:
        """识别原始页面图框范围及将图纸旋正所需的顺时针角度。"""
        if not self.model:
            self.check_ready()
        encoded = base64.b64encode(png_bytes).decode("ascii")
        prompt = (
            "你是建筑施工图版面识别器。识别图片中建筑图纸的有效图框范围："
            "以最外层正式图框线（包含图签栏）为边界，排除扫描黑边、白边、装订边、阴影和页面外背景。"
            "若图框线不完整，根据主要图形、图签和边界线推断。"
            "同时判断要让图纸文字和图签朝上、有效图框呈横向，需要将整张原图顺时针旋转多少度。"
            "rotation_clockwise 只能是 0、90、180、270 之一；竖向页面通常需要旋转 90 或 270 度。"
            "坐标以整张图片左上角为原点，宽高均归一化到 0 到 1。"
            "bbox 必须基于尚未旋转的原始图片坐标。"
            "左上角参考图有两个红框：字母 L 圆圈属于横向轴线，数字 1 圆圈属于竖向轴线。"
            "请在待识别整页中分别找到 L 圆圈和 1 圆圈，返回紧贴圆圈的 left_axis_bbox 和 top_axis_bbox。"
            "数字 1 必须是建筑主轴编号序列 1、2、3、4... 最左端的 1 轴圆圈，通常位于主平面图下边或上边；"
            "不要选择图名、详图编号、日期、图签栏或靠近右侧图签的数字 1。"
            "字母 L 必须是字母轴网序列中与主平面图上边相对应的 L 轴圆圈；不要选择普通文字里的 L。"
            "真正的左上裁切锚点不是页面角，也不是两个圆圈的包围框，而是："
            "数字 1 圆圈中心的 x 坐标与字母 L 圆圈中心的 y 坐标之交点。"
            "top_left_feature_bbox 返回同时包含这两个圆圈的范围，仅用于兼容和核对。"
            "右下角参考图展示了目标建筑图纸右下角的典型图签外框。"
            "请在待识别整页中找到同类图签外框，返回其完整最外层边框 title_block_bbox。"
            "只返回 JSON，不要解释，格式必须是："
            '{"bbox":[x0,y0,x1,y1],"left_axis_bbox":[x0,y0,x1,y1],'
            '"top_axis_bbox":[x0,y0,x1,y1],"top_left_feature_bbox":[x0,y0,x1,y1],'
            '"title_block_bbox":[x0,y0,x1,y1],'
            '"rotation_clockwise":0,"confidence":0.0}。'
            "bbox 必须满足 0<=x0<x1<=1、0<=y0<y1<=1。"
        )
        content: list[dict[str, object]] = [{"type": "text", "text": f"第 {page_number} 页。{prompt}"}]
        if top_left_reference_png:
            reference_encoded = base64.b64encode(top_left_reference_png).decode("ascii")
            content.extend(
                (
                    {"type": "text", "text": "下面第一张图片是左上角 L/1 轴网定位参考图："},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{reference_encoded}"}},
                )
            )
        if bottom_right_reference_png:
            reference_encoded = base64.b64encode(bottom_right_reference_png).decode("ascii")
            content.extend(
                (
                    {"type": "text", "text": "下面第二张图片是右下角图签定位参考图："},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{reference_encoded}"}},
                )
            )
        content.append({"type": "text", "text": "下面最后一张图片是需要识别的完整 PDF 页面："})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }
        response = self._request_json(self.base_url + "/chat/completions", payload)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionServiceError("本地 AI 返回中缺少 message.content") from exc
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        data = _extract_json(str(content))
        bbox = data.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise VisionServiceError("本地 AI 未返回四个 bbox 坐标")
        try:
            bounds = NormalizedBounds(*(float(value) for value in bbox))
            rotation = int(data.get("rotation_clockwise", -1))
            raw_top_left = data.get("top_left_feature_bbox", bbox)
            if not isinstance(raw_top_left, list) or len(raw_top_left) != 4:
                raise ValueError("top_left_feature_bbox 必须包含四个坐标")
            top_left_feature_bounds = NormalizedBounds(*(float(value) for value in raw_top_left))
            raw_title_block = data.get("title_block_bbox", bbox)
            if not isinstance(raw_title_block, list) or len(raw_title_block) != 4:
                raise ValueError("title_block_bbox 必须包含四个坐标")
            title_block_bounds = NormalizedBounds(*(float(value) for value in raw_title_block))
            raw_left_axis = data.get("left_axis_bbox")
            raw_top_axis = data.get("top_axis_bbox")
            left_axis_bounds = _optional_bounds(raw_left_axis, "left_axis_bbox")
            top_axis_bounds = _optional_bounds(raw_top_axis, "top_axis_bbox")
            if left_axis_bounds is not None and top_axis_bounds is not None:
                if not _axis_anchor_is_valid(left_axis_bounds, top_axis_bounds, title_block_bounds):
                    left_axis_bounds, top_axis_bounds = self._detect_axis_markers_in_left_crop(
                        png_bytes,
                        top_left_reference_png,
                    )
                if not _axis_anchor_is_valid(left_axis_bounds, top_axis_bounds, title_block_bounds):
                    raise ValueError("L/1 交点落入右下图签区域")
            return PageLayoutAnalysis(
                bounds,
                rotation,
                top_left_feature_bounds,
                title_block_bounds,
                left_axis_bounds,
                top_axis_bounds,
            )
        except (TypeError, ValueError) as exc:
            raise VisionServiceError(
                f"本地 AI 返回的页面布局无效：bbox={bbox}, rotation={data.get('rotation_clockwise')}"
            ) from exc

    def _detect_axis_markers_in_left_crop(
        self,
        png_bytes: bytes,
        reference_png: bytes | None,
        crop_ratio: float = 0.55,
    ) -> tuple[NormalizedBounds, NormalizedBounds]:
        """当整页误选右侧数字时，在页面左部做第二次专用识别。"""
        crop = _crop_left(png_bytes, crop_ratio)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    "你是建筑轴网标记识别器。最后一张图是完整图纸左侧 55% 的裁图。"
                    "找到字母 L 的轴网圆圈，以及数字轴序列 1、2、3... 中最左端的数字 1 圆圈。"
                    "忽略尺寸数字、普通文字、详图编号和日期。坐标相对最后一张裁图归一化到 0-1。"
                    "只返回 JSON：{\"left_axis_bbox\":[x0,y0,x1,y1],"
                    "\"top_axis_bbox\":[x0,y0,x1,y1]}。"
                ),
            }
        ]
        if reference_png:
            encoded_reference = base64.b64encode(reference_png).decode("ascii")
            content.extend(
                (
                    {"type": "text", "text": "参考图（红框标出目标 L 和 1 圆圈）："},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_reference}"}},
                )
            )
        encoded_crop = base64.b64encode(crop).decode("ascii")
        content.extend(
            (
                {"type": "text", "text": "待识别的左侧裁图："},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_crop}"}},
            )
        )
        response = self._request_json(
            self.base_url + "/chat/completions",
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 150,
                "messages": [{"role": "user", "content": content}],
            },
        )
        raw = response["choices"][0]["message"]["content"]
        data = _extract_json(str(raw))
        left_axis = _optional_bounds(data.get("left_axis_bbox"), "left_axis_bbox")
        top_axis = _optional_bounds(data.get("top_axis_bbox"), "top_axis_bbox")
        if left_axis is None or top_axis is None:
            raise ValueError("左侧裁图识别未返回 L/1 坐标")
        return _scale_bounds_x(left_axis, crop_ratio), _scale_bounds_x(top_axis, crop_ratio)

    def _request_json(self, url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise VisionServiceError("本地 AI 鉴权失败，请在 common.env 配置 LLAMACPP_API_KEY") from exc
            raise VisionServiceError(f"本地 AI HTTP {exc.code}：{detail[:300]}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise VisionServiceError(f"无法调用本地 AI：{exc}") from exc
        if not isinstance(result, dict):
            raise VisionServiceError("本地 AI 返回格式不是 JSON 对象")
        return result


def _extract_json(text: str) -> dict[str, object]:
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.IGNORECASE)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            data, _end = decoder.raw_decode(cleaned[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    if "{" not in cleaned:
        raise VisionServiceError("本地 AI 返回中没有 JSON 对象")
    raise VisionServiceError("本地 AI 返回的 JSON 无法解析")


def _optional_bounds(value: object, name: str) -> NormalizedBounds | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{name} 必须包含四个坐标")
    return NormalizedBounds(*(float(item) for item in value))


def _axis_anchor_is_valid(
    left_axis: NormalizedBounds,
    top_axis: NormalizedBounds,
    title_block: NormalizedBounds,
) -> bool:
    anchor_x = (top_axis.x0 + top_axis.x1) / 2
    anchor_y = (left_axis.y0 + left_axis.y1) / 2
    return anchor_x < title_block.x0 and anchor_y < title_block.y1


def _scale_bounds_x(bounds: NormalizedBounds, ratio: float) -> NormalizedBounds:
    return NormalizedBounds(bounds.x0 * ratio, bounds.y0, bounds.x1 * ratio, bounds.y1)


def _crop_left(png_bytes: bytes, ratio: float) -> bytes:
    image = fitz.open(stream=png_bytes, filetype="png")
    pdf = fitz.open("pdf", image.convert_to_pdf())
    page = pdf[0]
    clip = fitz.Rect(0, 0, page.rect.width * ratio, page.rect.height)
    pixmap = page.get_pixmap(clip=clip, alpha=False)
    result = pixmap.tobytes("png")
    pdf.close()
    image.close()
    return result
