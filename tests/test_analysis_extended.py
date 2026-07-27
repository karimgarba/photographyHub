import numpy as np
import cv2

from camera_engine.analysis import analyze_image_bytes, compute_histograms, preview_frame_delta, sharpness_of
from camera_engine.camera import focus_drive_label, match_choice


def _jpeg_bytes(width: int = 32, height: int = 32, value: int = 128) -> bytes:
    image = np.full((height, width, 3), value, dtype=np.uint8)
    # add some structure so Laplacian isn't zero
    image[height // 4 : height // 2, width // 4 : width // 2] = min(255, value + 80)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def test_analyze_supports_roi() -> None:
    data = _jpeg_bytes()
    full = analyze_image_bytes(data)
    roi = analyze_image_bytes(data, roi=(0.2, 0.2, 0.4, 0.4))
    assert full.width == 32
    assert roi.sharpness >= 0.0


def test_histograms_have_64_bins() -> None:
    hist = compute_histograms(_jpeg_bytes())
    assert len(hist.red) == 64
    assert len(hist.green) == 64
    assert len(hist.blue) == 64
    assert len(hist.luma) == 64
    assert sum(hist.luma) > 0


def test_preview_frame_delta_detects_change() -> None:
    a = _jpeg_bytes(value=40)
    b = _jpeg_bytes(value=200)
    assert preview_frame_delta(None, a) == 255.0
    assert preview_frame_delta(a, a) < 1.0
    assert preview_frame_delta(a, b) > 5.0


def test_sharpness_of_empty() -> None:
    assert sharpness_of(np.zeros((0, 0), dtype=np.uint8)) == 0.0


def test_focus_helpers_still_work() -> None:
    assert focus_drive_label("near", 2) == "Near2"
    assert match_choice(["Near 1", "Far 1"], "far1") == "Far 1"
