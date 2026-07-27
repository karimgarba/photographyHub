from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# Extensions OpenCV cannot reliably decode (raw sensor formats). For these we
# pull the camera's own embedded JPEG preview via rawpy/libraw instead of
# handing raw bytes to cv2.imdecode -- CR2/CR3 etc. are TIFF-based containers
# and OpenCV's TIFF codec chokes on the legacy JPEG compression Canon uses
# for the embedded preview (see analyze_saved_file docstring below).
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}

try:
    import rawpy
except ImportError:  # pragma: no cover - exercised only if rawpy isn't installed
    rawpy = None


@dataclass(slots=True)
class ImageAnalysis:
    sharpness: float
    edge_density: float
    edge_pixel_count: int
    width: int
    height: int


@dataclass(slots=True)
class HistogramData:
    red: list[int]
    green: list[int]
    blue: list[int]
    luma: list[int]


@dataclass(slots=True)
class PreviewMetrics:
    analysis: ImageAnalysis
    histogram: HistogramData | None = None
    roi_sharpness: float | None = None


def _decode_bgr(image_data: bytes, *, max_side: int | None = None) -> np.ndarray:
    array = np.frombuffer(image_data, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("Could not decode camera preview image")
    if max_side is not None:
        height, width = decoded.shape[:2]
        longest = max(height, width)
        if longest > max_side:
            scale = max_side / float(longest)
            decoded = cv2.resize(
                decoded,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
    return decoded


def _crop_roi(image: np.ndarray, roi: tuple[float, float, float, float] | None) -> np.ndarray:
    if roi is None:
        return image
    x, y, w, h = roi
    height, width = image.shape[:2]
    x0 = max(0, min(width - 1, int(x * width)))
    y0 = max(0, min(height - 1, int(y * height)))
    x1 = max(x0 + 1, min(width, int((x + w) * width)))
    y1 = max(y0 + 1, min(height, int((y + h) * height)))
    return image[y0:y1, x0:x1]


def sharpness_of(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_raw_preview_bytes(path: Path) -> bytes | None:
    """Pull the embedded JPEG preview out of a camera raw file (CR2/CR3/NEF/...)
    via rawpy/libraw. Returns None (never raises) if rawpy isn't installed, the
    file has no usable embedded preview, or extraction fails for any reason --
    callers should treat None as "no sharpness data available for this frame"
    rather than crashing or falling back to feeding raw bytes into cv2.
    """
    if rawpy is None:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
    except Exception:
        return None
    if thumb.format == rawpy.ThumbFormat.JPEG:
        return thumb.data
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        # thumb.data is an RGB numpy array here; re-encode as JPEG in-memory
        # so downstream code (which all works on bytes) doesn't need a second path.
        try:
            bgr = cv2.cvtColor(thumb.data, cv2.COLOR_RGB2BGR)
            ok, encoded = cv2.imencode(".jpg", bgr)
            if ok:
                return encoded.tobytes()
        except Exception:
            return None
    return None


def analyze_saved_file(
    path: Path,
    *,
    roi: tuple[float, float, float, float] | None = None,
    max_side: int | None = 1280,
) -> ImageAnalysis | None:
    """Analyze sharpness/edge data for a just-captured file on disk, whatever
    its format. JPEGs go straight through OpenCV; known raw extensions go
    through rawpy's embedded-preview extraction first. Returns None (never
    raises) when no analyzable image data could be obtained -- e.g. a
    RAW-only capture on a system without rawpy installed -- so callers can
    report "no sharpness data" instead of silently showing a misleading 0.0.
    """
    suffix = path.suffix.lower()
    if suffix in RAW_EXTENSIONS:
        preview_bytes = extract_raw_preview_bytes(path)
        if preview_bytes is None:
            return None
        try:
            return analyze_image_bytes(preview_bytes, roi=roi, max_side=max_side)
        except ValueError:
            return None
    try:
        return analyze_image_bytes(path.read_bytes(), roi=roi, max_side=max_side)
    except ValueError:
        return None


def analyze_image_bytes(
    image_data: bytes,
    *,
    roi: tuple[float, float, float, float] | None = None,
    max_side: int | None = 960,
) -> ImageAnalysis:
    decoded_full = _decode_bgr(image_data, max_side=None)
    full_height, full_width = decoded_full.shape[:2]
    decoded = decoded_full
    if max_side is not None:
        longest = max(full_height, full_width)
        if longest > max_side:
            scale = max_side / float(longest)
            decoded = cv2.resize(
                decoded_full,
                (max(1, int(full_width * scale)), max(1, int(full_height * scale))),
                interpolation=cv2.INTER_AREA,
            )
    region = _crop_roi(decoded, roi)
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, 80, 160)
    edge_pixel_count = int(np.count_nonzero(edges))
    total_pixels = int(edges.size)
    edge_density = edge_pixel_count / total_pixels if total_pixels else 0.0
    sharpness = sharpness_of(gray)
    return ImageAnalysis(
        sharpness=sharpness,
        edge_density=edge_density,
        edge_pixel_count=edge_pixel_count,
        width=full_width,
        height=full_height,
    )


def compute_histograms(image_data: bytes, *, roi: tuple[float, float, float, float] | None = None) -> HistogramData:
    decoded = _decode_bgr(image_data, max_side=640)
    region = _crop_roi(decoded, roi)
    channels = cv2.split(region)
    blue = cv2.calcHist([channels[0]], [0], None, [64], [0, 256]).flatten()
    green = cv2.calcHist([channels[1]], [0], None, [64], [0, 256]).flatten()
    red = cv2.calcHist([channels[2]], [0], None, [64], [0, 256]).flatten()
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    luma = cv2.calcHist([gray], [0], None, [64], [0, 256]).flatten()
    return HistogramData(
        red=[int(v) for v in red],
        green=[int(v) for v in green],
        blue=[int(v) for v in blue],
        luma=[int(v) for v in luma],
    )


def preview_frame_delta(previous: bytes | None, current: bytes) -> float:
    """Mean absolute difference of downscaled grayscale frames (0–255)."""
    if previous is None:
        return 255.0
    try:
        a = _decode_bgr(previous, max_side=64)
        b = _decode_bgr(current, max_side=64)
    except ValueError:
        return 255.0
    a_small = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (64, 64))
    b_small = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (64, 64))
    return float(np.mean(np.abs(a_small.astype(np.float32) - b_small.astype(np.float32))))


def analyze_preview(
    image_data: bytes,
    *,
    roi: tuple[float, float, float, float] | None = None,
    want_histogram: bool = False,
    want_roi_sharpness: bool = False,
) -> PreviewMetrics:
    analysis = analyze_image_bytes(image_data, roi=None, max_side=640)
    histogram = compute_histograms(image_data, roi=roi) if want_histogram else None
    roi_sharpness = None
    if want_roi_sharpness and roi is not None:
        roi_analysis = analyze_image_bytes(image_data, roi=roi, max_side=640)
        roi_sharpness = roi_analysis.sharpness
    return PreviewMetrics(analysis=analysis, histogram=histogram, roi_sharpness=roi_sharpness)
