"""Read and validate seed rectangles exported by Labelme.

Coordinates always refer to the current, full-resolution image.  This module
does not rescale annotations or infer labels from batch-level statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path


CLASS_NAMES = ("饱满种子", "半饱满种子", "虫蛀种子", "空瘪种子", "腐烂种子")
CLASS_COLORS = {
    "饱满种子": "#2EAD65",
    "半饱满种子": "#E6B83D",
    "虫蛀种子": "#D98124",
    "空瘪种子": "#6F88A8",
    "腐烂种子": "#CB515E",
}

_DICOM_EXTENSIONS = {".dcm", ".dicom"}
_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif",
    ".pbm", ".pgm", ".ppm", ".pnm",
}


@dataclass(frozen=True)
class SeedAnnotation:
    number: int
    label: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class AnnotationSet:
    annotations: tuple[SeedAnnotation, ...]
    image_name: str
    image_width: int
    image_height: int
    demo_only: bool
    source: Path

    @property
    def counts(self) -> dict[str, int]:
        """Return a new dictionary, including classes with zero seeds."""
        counts = dict.fromkeys(CLASS_NAMES, 0)
        for annotation in self.annotations:
            counts[annotation.label] += 1
        return counts

    @property
    def fullness_rate(self) -> float | None:
        """Percentage of full and partially filled seeds; None if empty."""
        if not self.annotations:
            return None
        counts = self.counts
        return (counts[CLASS_NAMES[0]] + counts[CLASS_NAMES[1]]) / len(
            self.annotations
        ) * 100.0


def _basename(name: str) -> str:
    """Handle a Labelme imagePath from either Windows or POSIX."""
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _positive_integer(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} 必须是正整数。")
    return value


def _coordinate(value: object, shape_number: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"第 {shape_number} 个标注的坐标必须是有限实数，不能是布尔值。")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"第 {shape_number} 个标注的坐标必须是有限实数。") from None
    if not math.isfinite(number):
        raise ValueError(f"第 {shape_number} 个标注的坐标必须是有限实数。")
    return number


def load_annotations(
    path: str | Path,
    image_path: str | Path,
    image_size: tuple[int, int],
) -> AnnotationSet:
    """Load a Labelme JSON file bound to the current image.

    Ordinary images must match ``imagePath`` by basename.  A DICOM image may
    instead match an ordinary image export by stem, provided the dimensions
    agree exactly.  Invalid or unreadable annotations raise a Chinese
    ``ValueError`` rather than returning a partial result.
    """
    source = Path(path)
    if not isinstance(image_size, (tuple, list)) or len(image_size) != 2:
        raise ValueError("当前图像尺寸必须是（宽度，高度）。")
    current_width = _positive_integer(image_size[0], "当前图像宽度")
    current_height = _positive_integer(image_size[1], "当前图像高度")
    current_name = _basename(str(image_path))
    if not current_name:
        raise ValueError("当前图像文件名不能为空。")

    try:
        raw = source.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("标注文件不是有效的 UTF-8 文本，请以 UTF-8 编码重新导出。") from exc
    except OSError as exc:
        raise ValueError(f"无法读取标注文件“{source.name}”：{exc.strerror or '读取失败'}。") from exc
    except ValueError as exc:
        raise ValueError("标注文件路径无效，无法读取。") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"标注文件不是有效的 JSON：第 {exc.lineno} 行、第 {exc.colno} 列格式错误。"
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise ValueError("标注 JSON 的内容过大、嵌套过深或数值无效，无法解析。") from exc

    if not isinstance(data, dict):
        raise ValueError("标注 JSON 的根内容必须是对象。")
    reference = data.get("imagePath")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("标注文件的 imagePath 必须是非空图像路径。")
    reference_name = _basename(reference)
    current_file = Path(current_name)
    reference_file = Path(reference_name)
    if current_file.suffix.lower() in _DICOM_EXTENSIONS:
        if (
            reference_file.suffix.lower() not in _IMAGE_EXTENSIONS
            or reference_file.stem != current_file.stem
        ):
            raise ValueError("标注 imagePath 必须指向与当前 DICOM 同名（不含扩展名）的普通图像。")
    elif reference_name != current_name:
        raise ValueError("标注 imagePath 的文件名与当前图像不一致，请选择对应的标注文件。")

    width = _positive_integer(data.get("imageWidth"), "标注 imageWidth")
    height = _positive_integer(data.get("imageHeight"), "标注 imageHeight")
    if (width, height) != (current_width, current_height):
        raise ValueError(
            f"标注尺寸 {width}×{height} 与当前图像 {current_width}×{current_height} 不一致；"
            "请使用原尺寸图像标注，不能自动缩放坐标。"
        )

    flags = data.get("flags", {})
    if not isinstance(flags, dict):
        raise ValueError("标注文件的 flags 必须是对象。")
    demo_only = flags.get("demo_only", False)
    if type(demo_only) is not bool:
        raise ValueError("flags.demo_only 必须是布尔值 true 或 false。")

    shapes = data.get("shapes")
    if not isinstance(shapes, list):
        raise ValueError("标注文件的 shapes 必须是数组，空标注请使用空数组。")
    annotations = []
    rectangles = set()
    for number, shape in enumerate(shapes, start=1):
        if not isinstance(shape, dict):
            raise ValueError(f"第 {number} 个标注必须是对象。")
        if shape.get("shape_type") != "rectangle":
            raise ValueError(f"第 {number} 个标注必须是 rectangle 矩形，不支持其它形状。")
        label = shape.get("label")
        if label not in CLASS_NAMES:
            raise ValueError(f"第 {number} 个标注的类别无效；只接受：{'、'.join(CLASS_NAMES)}。")
        points = shape.get("points")
        if not isinstance(points, list) or len(points) != 2:
            raise ValueError(f"第 {number} 个矩形必须恰好有两个坐标点。")
        normalized_points = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError(f"第 {number} 个标注的每个点必须恰好包含 x、y 两个坐标。")
            normalized_points.append(tuple(_coordinate(value, number) for value in point))
        (first_x, first_y), (second_x, second_y) = normalized_points
        left, right = sorted((first_x, second_x))
        top, bottom = sorted((first_y, second_y))
        if left == right or top == bottom:
            raise ValueError(f"第 {number} 个矩形的宽度和高度必须大于零。")
        if left < 0 or top < 0 or right > width or bottom > height:
            raise ValueError(f"第 {number} 个矩形超出图像范围，坐标必须位于 0 至图像宽高之间。")
        rectangle = (left, top, right, bottom)
        if rectangle in rectangles:
            raise ValueError(f"第 {number} 个标注与前面的矩形完全重复，请删除重复框。")
        rectangles.add(rectangle)
        annotations.append(SeedAnnotation(number, label, left, top, right - left, bottom - top))

    return AnnotationSet(
        annotations=tuple(annotations),
        image_name=current_name,
        image_width=width,
        image_height=height,
        demo_only=demo_only,
        source=source,
    )
