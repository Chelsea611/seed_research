"""读取二维灰度 DICOM，并生成独立的显示副本。"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pydicom
from pydicom.pixels import apply_modality_lut, apply_voi_lut


@dataclass
class DicomImage:
    raw_pixels: np.ndarray
    default_pixels: np.ndarray | None
    auto_pixels: np.ndarray
    bits_allocated: int
    bits_stored: int
    photometric: str
    presentation_inverse: bool
    modality: str
    window_description: str
    note: str = ""


def _display_pixels(values, low, high, inverse, valid):
    """使用指定输出范围映射至 8 位，不再拉伸文件窗口的实际像素范围。"""
    if not np.isfinite([low, high]).all():
        raise ValueError("灰度显示范围不是有效数值。")
    if high == low:
        normalized = np.zeros(values.shape, dtype=np.float64)
    else:
        normalized = (values.astype(np.float64) - low) / (high - low)
    normalized = np.clip(np.nan_to_num(normalized), 0.0, 1.0)
    if inverse:
        normalized = 1.0 - normalized
    normalized[~valid] = 0.0
    return np.ascontiguousarray(np.rint(normalized * 255), dtype=np.uint8)


def _window_output_range(dataset):
    """取得 pydicom 窗口变换的理论输出范围，包括有符号值与 rescale。"""
    if dataset.get("ModalityLUTSequence"):
        bits = int(dataset.ModalityLUTSequence[0].LUTDescriptor[2])
        low, high = 0, 2**bits - 1
    elif int(dataset.PixelRepresentation) == 0:
        low, high = 0, 2 ** int(dataset.BitsStored) - 1
    else:
        bits = int(dataset.BitsStored)
        low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
    if "RescaleSlope" in dataset and "RescaleIntercept" in dataset:
        slope = float(dataset.RescaleSlope)
        intercept = float(dataset.RescaleIntercept)
        low, high = low * slope + intercept, high * slope + intercept
    return low, high


def load_dicom(path: str | Path) -> DicomImage:
    dataset = pydicom.dcmread(path)
    photometric = str(dataset.get("PhotometricInterpretation", "")).strip()
    if int(dataset.get("NumberOfFrames", 1) or 1) != 1:
        raise ValueError("当前版本仅支持单帧二维 DICOM，多帧浏览将在后续加入。")
    if int(dataset.get("SamplesPerPixel", 1)) != 1 or photometric not in (
        "MONOCHROME1", "MONOCHROME2"
    ):
        raise ValueError("当前版本仅支持 MONOCHROME1 / MONOCHROME2 灰度 DICOM。")
    if dataset.get("PresentationLUTSequence"):
        raise ValueError("当前版本暂不支持带独立 Presentation LUT 的 DICOM。")

    raw_pixels = dataset.pixel_array
    if raw_pixels.ndim == 3 and raw_pixels.shape[0] == 1:
        raw_pixels = raw_pixels[0]
    if raw_pixels.ndim != 2:
        raise ValueError("文件的像素结构不是二维灰度图像。")
    values = np.asarray(apply_modality_lut(raw_pixels, dataset), dtype=np.float64)
    valid = np.isfinite(values)
    if "PixelPaddingValue" in dataset:
        padding = float(dataset.PixelPaddingValue)
        limit = float(dataset.get("PixelPaddingRangeLimit", padding))
        low_padding, high_padding = sorted((padding, limit))
        valid &= ~((raw_pixels >= low_padding) & (raw_pixels <= high_padding))
    if not valid.any():
        raise ValueError("文件中没有可显示的有效像素。")

    # PresentationLUTShape 与 MONOCHROME1 在本批样本中描述同一次反相。
    shape = str(dataset.get("PresentationLUTShape", "")).strip().upper()
    if shape and shape not in ("IDENTITY", "INVERSE"):
        raise ValueError(f"暂不支持显示变换：{shape}")
    inverse = shape == "INVERSE" if shape else photometric == "MONOCHROME1"

    low, high = np.percentile(values[valid], [0.5, 99.5])
    if high == low:
        low, high = values[valid].min(), values[valid].max()
    auto_pixels = _display_pixels(values, low, high, inverse, valid)

    default_pixels = None
    description = "无文件窗口，使用自动对比度"
    note = ""
    has_lut = bool(dataset.get("VOILUTSequence"))
    has_window = "WindowCenter" in dataset and "WindowWidth" in dataset
    if has_lut or has_window:
        try:
            if has_lut:
                bits = int(dataset.VOILUTSequence[0].LUTDescriptor[2])
                low, high = 0, 2**bits - 1
                description = "文件 VOI LUT"
            else:
                low, high = _window_output_range(dataset)
                center = dataset["WindowCenter"]
                width = dataset["WindowWidth"]
                center = center.value[0] if center.VM > 1 else center.value
                width = width.value[0] if width.VM > 1 else width.value
                if not np.isfinite([float(center), float(width)]).all():
                    raise ValueError("窗位或窗宽不是有效数值。")
                description = f"窗位 {float(center):g}，窗宽 {float(width):g}"
            displayed = apply_voi_lut(values, dataset)
            if not np.isfinite(displayed[valid]).all() or high == low:
                raise ValueError("文件窗口无法产生有效显示范围。")
            default_pixels = _display_pixels(displayed, low, high, inverse, valid)
        except (ValueError, TypeError, KeyError, IndexError):
            note = "文件灰度参数无效，已使用自动对比度。"
            description = "自动对比度"

    return DicomImage(
        raw_pixels=raw_pixels,
        default_pixels=default_pixels,
        auto_pixels=auto_pixels,
        bits_allocated=int(dataset.BitsAllocated),
        bits_stored=int(dataset.BitsStored),
        photometric=photometric,
        presentation_inverse=inverse,
        modality=str(dataset.get("Modality", "未提供")),
        window_description=description,
        note=note,
    )
