from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(slots=True)
class FocusStackPlan:
    start_position: int
    end_position: int
    step_count: int

    @property
    def focus_positions(self) -> list[int]:
        if self.step_count <= 1:
            return [self.start_position]

        step_size = (self.end_position - self.start_position) / (self.step_count - 1)
        return [round(self.start_position + index * step_size) for index in range(self.step_count)]


class FocusStepPreset(str, Enum):
    TINY = "Tiny"
    SMALL = "Small"
    MEDIUM = "Medium"
    LARGE = "Large"


STEP_PRESET_MM: dict[FocusStepPreset, float] = {
    FocusStepPreset.TINY: 0.10,
    FocusStepPreset.SMALL: 0.25,
    FocusStepPreset.MEDIUM: 0.50,
    FocusStepPreset.LARGE: 1.00,
}


def step_mm_for_preset(preset: FocusStepPreset) -> float:
    return STEP_PRESET_MM[preset]


def preset_for_step_mm(step_mm: float) -> FocusStepPreset:
    if step_mm <= 0.12:
        return FocusStepPreset.TINY
    if step_mm <= 0.375:
        return FocusStepPreset.SMALL
    if step_mm <= 0.75:
        return FocusStepPreset.MEDIUM
    return FocusStepPreset.LARGE


def estimate_step_count(depth_mm: float, step_mm: float) -> int:
    if depth_mm <= 0 or step_mm <= 0:
        raise ValueError("depth_mm and step_mm must be positive")

    return max(1, int((depth_mm / step_mm) + 0.5))


def should_stop_adaptive_stack(
    best_sharpness: float,
    current_sharpness: float,
    sharpness_gain_threshold: float,
    worsening_frames: int,
    patience: int,
) -> bool:
    if patience <= 0:
        raise ValueError("patience must be positive")

    return current_sharpness <= best_sharpness + sharpness_gain_threshold and worsening_frames >= patience


def roi_sharpness_bounds(
    curve: dict[int, float],
    *,
    threshold: float = 0.20,
    fallback_radius: int = 16,
    margin: int = 6,
) -> tuple[int, int]:
    """
    Contiguous band around the peak where ROI sharpness stays >= peak * threshold,
    then expand by margin (clamped to scanned extent).
    Falls back to a window around the peak if the band collapses.
    """
    if not curve:
        return 0, 0
    keys = sorted(curve)
    scanned_lo, scanned_hi = keys[0], keys[-1]
    peak_offset = max(curve, key=lambda k: curve[k])
    peak = curve[peak_offset]
    if peak <= 0:
        return peak_offset, peak_offset
    floor = peak * threshold

    near = peak_offset
    while near - 1 in curve and curve[near - 1] >= floor:
        near -= 1
    far = peak_offset
    while far + 1 in curve and curve[far + 1] >= floor:
        far += 1

    if near == far:
        near = peak_offset - fallback_radius
        far = peak_offset + fallback_radius

    pad = max(0, int(margin))
    near -= pad
    far += pad
    near = max(scanned_lo, near)
    far = min(scanned_hi, far)
    if near > far:
        near, far = far, near
    return near, far


def fine_fill_offsets(near_bound: int, far_bound: int, *, unit: int = 1) -> list[int]:
    """Inclusive offsets from near→far stepped by unit (focus-drive units)."""
    lo, hi = (near_bound, far_bound) if near_bound <= far_bound else (far_bound, near_bound)
    step = max(1, int(unit))
    offsets = list(range(lo, hi + 1, step))
    if not offsets or offsets[-1] != hi:
        offsets.append(hi)
    # de-dupe while preserving order
    out: list[int] = []
    for value in offsets:
        if not out or out[-1] != value:
            out.append(value)
    return out


def sort_capture_offsets(offsets: list[int], *, near_to_far: bool = True) -> list[int]:
    ordered = sorted(offsets)
    return ordered if near_to_far else list(reversed(ordered))


def densify_where_steep(
    coarse: dict[int, float],
    filled: list[int],
    *,
    steep_delta: float = 40.0,
) -> list[int]:
    """Insert midpoints where consecutive coarse samples jumped a lot."""
    if len(coarse) < 2:
        return filled
    keys = sorted(coarse)
    extras: list[int] = []
    for left, right in zip(keys, keys[1:]):
        if abs(coarse[right] - coarse[left]) >= steep_delta and right - left > 1:
            mid = (left + right) // 2
            extras.append(mid)
    merged = sort_capture_offsets([*filled, *extras], near_to_far=True)
    return merged
