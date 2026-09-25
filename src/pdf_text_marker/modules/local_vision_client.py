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
    bottom_right_axis_bounds: NormalizedBounds | None = None
    bottom_axis_bounds: NormalizedBounds | None = None

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
            "右下裁切锚点也由两个轴网圆圈的轴线交点确定，不能直接使用任一圆圈中心。"
            "right_axis_bbox 是主图右侧最下方的字母轴网圆圈（参考图黄色框内类似 A 的圆圈），"
            "其黑色水平轴线向左延伸，圆圈中心只提供最终交点的 y 坐标。"
            "bottom_axis_bbox 是主图下方最右侧的数字轴网圆圈（参考图中类似 20 的圆圈），"
            "其黑色竖直轴线向上延伸，圆圈中心只提供最终交点的 x 坐标。"
            "两个圆圈的具体字符可能变化，不要拘泥于 A 或 20。"
            "目标必须由黑线圆形轮廓和黑色字符组成，并连接对应的黑色长轴线。"
            "红色印章、蓝色或其他彩色图块即使外形近似也绝不是轴网标记。"
            "二维码和印章不是圆形轴网标记；不要选择签字栏、图名、日期、二维码、印章、方框、页边或普通文字。"
            "最终右下锚点是 bottom_axis_bbox 圆心 x 与 right_axis_bbox 圆心 y 的交点。"
            "同时返回相邻图签的完整外框 title_block_bbox，用于组合定位和几何校验。"
            "只返回 JSON，不要解释，格式必须是："
            '{"bbox":[x0,y0,x1,y1],"left_axis_bbox":[x0,y0,x1,y1],'
            '"top_axis_bbox":[x0,y0,x1,y1],"top_left_feature_bbox":[x0,y0,x1,y1],'
            '"right_axis_bbox":[x0,y0,x1,y1],"bottom_axis_bbox":[x0,y0,x1,y1],'
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
                    {
                        "type": "text",
                        "text": (
                            "下面第二张图片是右下组合参考图：黄色框圈住右侧字母轴号；"
                            "图纸下方还能看到数字轴号序列。需要用两个轴号的轴线交点定位。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{reference_encoded}"}},
                )
            )
        content.append({"type": "text", "text": "下面最后一张图片是需要识别的完整 PDF 页面："})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 500,
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
            raw_right_axis = data.get("right_axis_bbox", data.get("bottom_right_axis_bbox"))
            raw_bottom_axis = data.get("bottom_axis_bbox")
            bottom_right_axis_bounds = _optional_bounds(raw_right_axis, "right_axis_bbox")
            bottom_axis_bounds = _optional_bounds(raw_bottom_axis, "bottom_axis_bbox")
            if bottom_right_reference_png is not None and rotation == 0:
                # 整页识别容易把红章、二维码或图签文字误当成右下轴网。
                # 对横向页面固定使用右下放大裁图和去彩色图识别两个轴号。
                bottom_right_axis_bounds, bottom_axis_bounds = self._detect_bottom_right_axes_in_crop(
                    png_bytes,
                    bottom_right_reference_png,
                )
            elif bottom_right_reference_png is not None and (
                bottom_right_axis_bounds is None or bottom_axis_bounds is None
            ):
                raise ValueError("缺少 right_axis_bbox 或 bottom_axis_bbox")
            if bottom_right_axis_bounds is not None and not _bottom_right_axis_is_valid(
                bottom_right_axis_bounds, rotation, title_block_bounds
            ):
                raise ValueError(f"右侧字母轴网圆圈落在异常区域：{bottom_right_axis_bounds}")
            if (
                bottom_right_axis_bounds is not None
                and bottom_axis_bounds is not None
                and not _bottom_axis_pair_is_valid(
                    bottom_right_axis_bounds,
                    bottom_axis_bounds,
                    rotation,
                )
            ):
                raise ValueError(
                    f"右侧/下方轴网圆圈无法形成右下交点：{bottom_right_axis_bounds}, {bottom_axis_bounds}"
                )
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
            if (
                left_axis_bounds is not None
                and top_axis_bounds is not None
                and bottom_right_axis_bounds is not None
                and not _anchors_form_valid_rect(
                    left_axis_bounds,
                    top_axis_bounds,
                    bottom_right_axis_bounds,
                    rotation,
                    bottom_axis_bounds,
                )
            ):
                left_axis_bounds, top_axis_bounds = self._detect_axis_markers_in_left_crop(
                    png_bytes,
                    top_left_reference_png,
                )
                if not _anchors_form_valid_rect(
                    left_axis_bounds,
                    top_axis_bounds,
                    bottom_right_axis_bounds,
                    rotation,
                    bottom_axis_bounds,
                ):
                    raise ValueError("左上与右下锚点无法形成有效打印范围")
            if left_axis_bounds is not None and top_axis_bounds is not None:
                top_left_feature_bounds = NormalizedBounds(
                    min(left_axis_bounds.x0, top_axis_bounds.x0),
                    min(left_axis_bounds.y0, top_axis_bounds.y0),
                    max(left_axis_bounds.x1, top_axis_bounds.x1),
                    max(left_axis_bounds.y1, top_axis_bounds.y1),
                )
            return PageLayoutAnalysis(
                bounds,
                rotation,
                top_left_feature_bounds,
                title_block_bounds,
                left_axis_bounds,
                top_axis_bounds,
                bottom_right_axis_bounds,
                bottom_axis_bounds,
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
        left_axis = _image_relative_bounds(data.get("left_axis_bbox"), "left_axis_bbox", crop)
        top_axis = _image_relative_bounds(data.get("top_axis_bbox"), "top_axis_bbox", crop)
        if left_axis is None or top_axis is None:
            raise ValueError(f"左侧裁图识别未返回 L/1 坐标，返回字段：{sorted(data)}")
        return _scale_bounds_x(left_axis, crop_ratio), _scale_bounds_x(top_axis, crop_ratio)

    def _detect_bottom_right_axes_in_crop(
        self,
        png_bytes: bytes,
        reference_png: bytes | None,
        crop_ratio: float = 0.40,
        height_ratio: float = 0.94,
    ) -> tuple[NormalizedBounds, NormalizedBounds]:
        """分别识别右侧字母轴和下方数字轴，避免模型混淆两者角色。"""
        right_axis = self._detect_right_axis_in_crop(png_bytes, reference_png, crop_ratio)
        bottom_axis = self._detect_bottom_axis_in_crop(png_bytes, reference_png)
        return right_axis, bottom_axis

    def _detect_right_axis_in_crop(
        self,
        png_bytes: bytes,
        reference_png: bytes | None,
        crop_ratio: float = 0.40,
        height_ratio: float = 0.88,
    ) -> NormalizedBounds:
        """在右侧裁图中只识别提供 Y 坐标的字母轴号圆圈。"""
        crop = _crop_right(png_bytes, crop_ratio, height_ratio)
        black_ink_crop = _suppress_colored_pixels(crop)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    "你是建筑轴网标记识别器。待识别图是完整图纸右侧 40%、上部 88% 的裁图。"
                    "本次只找提供最终交点 y 坐标的右侧字母轴号圆圈。"
                    "目标位于主图右侧字母轴号列中，是最下方且仍属于主图的黑色圆圈，"
                    "通常类似参考图黄色框中的 A，并连接一条向左进入主图的黑色长水平轴线。"
                    "具体字母不是硬性要求。不要选择下方数字轴号行中的圆圈。"
                    "参考图中的黄色框只是人工标注，待识别图中不要求存在黄色框。"
                    "红章、蓝章及任何彩色图案都不是轴网圆圈；绝对不要选择签字栏、图名、日期、"
                    "印章、二维码、方框、页边或普通文字。随后提供两张坐标完全相同的待识别裁图："
                    "第一张保留原色用于理解版面，第二张已去除彩色像素、只保留黑色和灰色线字。"
                    "候选圆圈必须在第二张去彩图中仍清楚存在；只在原彩图出现的彩色候选必须排除。"
                    "坐标相对两张待识别裁图归一化到 0-1。只返回 JSON："
                    "{\"right_axis_bbox\":[x0,y0,x1,y1]}。"
                ),
            }
        ]
        if reference_png:
            encoded_reference = base64.b64encode(reference_png).decode("ascii")
            content.extend(
                (
                    {
                        "type": "text",
                        "text": (
                            "参考图：黄色框只标出本次要识别的右侧字母轴号圆圈。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_reference}"}},
                )
            )
        encoded_crop = base64.b64encode(crop).decode("ascii")
        encoded_black_ink_crop = base64.b64encode(black_ink_crop).decode("ascii")
        content.extend(
            (
                {"type": "text", "text": "待识别的右侧原色裁图（用于确认主图和图签位置）："},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_crop}"}},
                {"type": "text", "text": "同一裁图的去彩黑线版本（最终候选必须在此图中存在）："},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded_black_ink_crop}"},
                },
            )
        )
        response = self._request_json(
            self.base_url + "/chat/completions",
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": content}],
            },
        )
        raw = response["choices"][0]["message"]["content"]
        data = _extract_json(str(raw))
        right_axis = _image_relative_bounds(
            data.get("right_axis_bbox", data.get("bottom_right_axis_bbox")),
            "right_axis_bbox",
            crop,
        )
        if right_axis is None:
            raise ValueError(f"右侧裁图识别未返回字母轴号，返回字段：{sorted(data)}")
        return _scale_right_crop_bounds(right_axis, crop_ratio, height_ratio)

    def _detect_bottom_axis_in_crop(
        self,
        png_bytes: bytes,
        _reference_png: bytes | None,
        crop_box: tuple[float, float, float, float] = (0.50, 0.70, 0.94, 0.98),
    ) -> NormalizedBounds:
        """在底部裁图中只识别提供 X 坐标的数字轴号圆圈。"""
        crop = _crop_region(png_bytes, crop_box)
        black_ink_crop = _suppress_colored_pixels(crop)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    "你是建筑轴网标记识别器。待识别图是完整图纸右下区域的裁图。"
                    "本次只找提供最终交点 x 坐标的下方数字轴号圆圈。"
                    "目标属于主图下方从左到右排列的数字轴号行，是最右侧且仍属于主图的黑色圆圈，"
                    "通常是序列末端的较大数字，并连接一条向上进入主图的黑色长竖直轴线。"
                    "沿这条竖直轴线向上，应能与主图右侧最低字母轴号的长水平轴线相交。"
                    "如果圆圈被印章局部遮挡，应根据仍可见的黑色竖直轴线和相邻数字轴号序列推断。"
                    "具体数字不是硬性要求。不要选择右侧字母轴号列中连接水平线的圆圈，"
                    "也不要选择只有短尺寸线、没有向上贯入主图的尺寸数字。"
                    "即使目标圆圈被红章部分遮挡，也只依据去彩图中保留下来的黑色圆圈、黑色字符和竖线判断。"
                    "排除红章、蓝章、二维码、图签表格、尺寸数字、详图编号、普通文字、页边和方框。"
                    "随后提供原色裁图和坐标相同的去彩黑线裁图；候选必须在去彩图中存在。"
                    "坐标相对两张待识别裁图归一化到 0-1。只返回 JSON："
                    "{\"bottom_axis_bbox\":[x0,y0,x1,y1]}。"
                ),
            }
        ]
        encoded_crop = base64.b64encode(crop).decode("ascii")
        encoded_black_ink_crop = base64.b64encode(black_ink_crop).decode("ascii")
        content.extend(
            (
                {"type": "text", "text": "待识别的底部原色裁图："},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_crop}"}},
                {"type": "text", "text": "同一裁图的去彩黑线版本："},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded_black_ink_crop}"},
                },
            )
        )
        response = self._request_json(
            self.base_url + "/chat/completions",
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": content}],
            },
        )
        raw = response["choices"][0]["message"]["content"]
        data = _extract_json(str(raw))
        bottom_axis = _image_relative_bounds(data.get("bottom_axis_bbox"), "bottom_axis_bbox", crop)
        if bottom_axis is None:
            raise ValueError(f"底部裁图识别未返回数字轴号，返回字段：{sorted(data)}")
        return _scale_region_bounds(bottom_axis, crop_box)

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
    numbers = tuple(float(item) for item in value)
    if any(item < -0.05 or item > 1.05 for item in numbers):
        raise ValueError(f"{name} 坐标超出可修正范围：{numbers}")
    clamped = tuple(max(0.0, min(1.0, item)) for item in numbers)
    return NormalizedBounds(*clamped)


def _image_relative_bounds(value: object, name: str, png_bytes: bytes) -> NormalizedBounds | None:
    """同时接受裁图归一化坐标和明确落在裁图内的像素坐标。"""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{name} 必须包含四个坐标")
    numbers = tuple(float(item) for item in value)
    if all(-0.05 <= item <= 1.05 for item in numbers):
        return _optional_bounds(value, name)
    pixmap = fitz.Pixmap(png_bytes)
    x0, y0, x1, y1 = numbers
    if not (0 <= x0 < x1 <= pixmap.width and 0 <= y0 < y1 <= pixmap.height):
        raise ValueError(f"{name} 像素坐标超出裁图范围：{numbers}")
    return NormalizedBounds(
        x0 / pixmap.width,
        y0 / pixmap.height,
        x1 / pixmap.width,
        y1 / pixmap.height,
    )


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


def _scale_right_crop_bounds(
    bounds: NormalizedBounds,
    ratio: float,
    height_ratio: float = 1.0,
) -> NormalizedBounds:
    offset = 1 - ratio
    return NormalizedBounds(
        offset + bounds.x0 * ratio,
        bounds.y0 * height_ratio,
        offset + bounds.x1 * ratio,
        bounds.y1 * height_ratio,
    )


def _scale_region_bounds(
    bounds: NormalizedBounds,
    crop_box: tuple[float, float, float, float],
) -> NormalizedBounds:
    x0, y0, x1, y1 = crop_box
    width = x1 - x0
    height = y1 - y0
    return NormalizedBounds(
        x0 + bounds.x0 * width,
        y0 + bounds.y0 * height,
        x0 + bounds.x1 * width,
        y0 + bounds.y1 * height,
    )


def _bottom_right_axis_is_valid(
    bounds: NormalizedBounds,
    rotation: int,
    title_block: NormalizedBounds | None = None,
) -> bool:
    x = (bounds.x0 + bounds.x1) / 2
    y = (bounds.y0 + bounds.y1) / 2
    if rotation == 90:
        x, y = 1 - y, x
    elif rotation == 180:
        x, y = 1 - x, 1 - y
    elif rotation == 270:
        x, y = y, 1 - x
    maximum_y = 0.97
    if title_block is not None:
        rotated_title = _rotate_bounds_for_validation(title_block, rotation)
        maximum_y = min(maximum_y, rotated_title.y1 - 0.12)
    return x > 0.5 and y < maximum_y


def _bottom_axis_pair_is_valid(
    right_axis: NormalizedBounds,
    bottom_axis: NormalizedBounds,
    rotation: int,
) -> bool:
    """校验下方数字轴号位于右侧字母轴号的左下方。"""
    right_x = (right_axis.x0 + right_axis.x1) / 2
    right_y = (right_axis.y0 + right_axis.y1) / 2
    bottom_x = (bottom_axis.x0 + bottom_axis.x1) / 2
    bottom_y = (bottom_axis.y0 + bottom_axis.y1) / 2
    right_x, right_y = _rotate_point_for_validation(right_x, right_y, rotation)
    bottom_x, bottom_y = _rotate_point_for_validation(bottom_x, bottom_y, rotation)
    return bottom_x > 0.5 and bottom_x < right_x + 0.02 and bottom_y > right_y + 0.02


def _anchors_form_valid_rect(
    left_axis: NormalizedBounds,
    top_axis: NormalizedBounds,
    bottom_right_axis: NormalizedBounds,
    rotation: int,
    bottom_axis: NormalizedBounds | None = None,
) -> bool:
    left_x = (top_axis.x0 + top_axis.x1) / 2
    left_y = (left_axis.y0 + left_axis.y1) / 2
    right_x_source = bottom_axis if bottom_axis is not None else bottom_right_axis
    right_x = (right_x_source.x0 + right_x_source.x1) / 2
    right_y = (bottom_right_axis.y0 + bottom_right_axis.y1) / 2
    left_x, left_y = _rotate_point_for_validation(left_x, left_y, rotation)
    right_x, right_y = _rotate_point_for_validation(right_x, right_y, rotation)
    return left_x + 0.05 < right_x and left_y + 0.05 < right_y


def _rotate_point_for_validation(x: float, y: float, rotation: int) -> tuple[float, float]:
    if rotation == 90:
        return 1 - y, x
    if rotation == 180:
        return 1 - x, 1 - y
    if rotation == 270:
        return y, 1 - x
    return x, y


def _rotate_bounds_for_validation(bounds: NormalizedBounds, rotation: int) -> NormalizedBounds:
    if rotation == 0:
        return bounds
    if rotation == 90:
        return NormalizedBounds(1 - bounds.y1, bounds.x0, 1 - bounds.y0, bounds.x1)
    if rotation == 180:
        return NormalizedBounds(1 - bounds.x1, 1 - bounds.y1, 1 - bounds.x0, 1 - bounds.y0)
    if rotation == 270:
        return NormalizedBounds(bounds.y0, 1 - bounds.x1, bounds.y1, 1 - bounds.x0)
    return bounds


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


def _crop_right(png_bytes: bytes, ratio: float, height_ratio: float) -> bytes:
    image = fitz.open(stream=png_bytes, filetype="png")
    pdf = fitz.open("pdf", image.convert_to_pdf())
    page = pdf[0]
    clip = fitz.Rect(
        page.rect.width * (1 - ratio),
        0,
        page.rect.width,
        page.rect.height * height_ratio,
    )
    pixmap = page.get_pixmap(clip=clip, alpha=False)
    result = pixmap.tobytes("png")
    pdf.close()
    image.close()
    return result


def _crop_region(png_bytes: bytes, crop_box: tuple[float, float, float, float]) -> bytes:
    image = fitz.open(stream=png_bytes, filetype="png")
    pdf = fitz.open("pdf", image.convert_to_pdf())
    page = pdf[0]
    x0, y0, x1, y1 = crop_box
    clip = fitz.Rect(
        page.rect.width * x0,
        page.rect.height * y0,
        page.rect.width * x1,
        page.rect.height * y1,
    )
    pixmap = page.get_pixmap(clip=clip, alpha=False)
    result = pixmap.tobytes("png")
    pdf.close()
    image.close()
    return result


def _suppress_colored_pixels(png_bytes: bytes, chroma_threshold: int = 24) -> bytes:
    """移除红章等彩色像素，保留黑色、灰色轴线和文字。"""
    source = fitz.Pixmap(png_bytes)
    rgb = fitz.Pixmap(fitz.csRGB, source) if source.colorspace != fitz.csRGB or source.alpha else source
    samples = bytearray(rgb.samples)
    for offset in range(0, len(samples), 3):
        red, green, blue = samples[offset : offset + 3]
        if max(red, green, blue) - min(red, green, blue) >= chroma_threshold:
            samples[offset : offset + 3] = b"\xff\xff\xff"
            continue
        gray = round((int(red) + int(green) + int(blue)) / 3)
        samples[offset : offset + 3] = bytes((gray, gray, gray))
    filtered = fitz.Pixmap(fitz.csRGB, rgb.width, rgb.height, bytes(samples), False)
    return filtered.tobytes("png")
