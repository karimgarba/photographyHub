from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DofEstimate:
    magnification: float
    aperture: float
    step_mm: float
    diffraction: str
    circle_of_confusion_mm: float = 0.019


def parse_aperture(text: str) -> float | None:
    cleaned = text.strip().lower().replace(" ", "")
    for prefix in ("f/", "f", "ƒ/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def recommended_step_mm(magnification: float, aperture: float, coc_mm: float = 0.019) -> float:
    """Approximate focus step for stacked macro (effective DOF slice)."""
    mag = max(0.01, magnification)
    # Simplified DOF at subject: 2 * N * c * (m+1) / m^2  — use half as step
    dof = 2.0 * aperture * coc_mm * (mag + 1.0) / (mag * mag)
    return max(0.02, dof / 2.0)


def diffraction_quality(aperture: float) -> str:
    if aperture <= 5.6:
        return "Excellent"
    if aperture <= 8:
        return "Good"
    if aperture <= 11:
        return "Softening"
    return "Diffraction limited"


def estimate_dof(magnification: float, aperture: float, coc_mm: float = 0.019) -> DofEstimate:
    step = recommended_step_mm(magnification, aperture, coc_mm)
    return DofEstimate(
        magnification=magnification,
        aperture=aperture,
        step_mm=step,
        diffraction=diffraction_quality(aperture),
        circle_of_confusion_mm=coc_mm,
    )

def estimate_duration_seconds(
    shot_count: int,
    seconds_per_shot: float | None = None,
    *,
    settle_ms: int = 800,
    overhead_seconds: float = 0.8,
) -> float:
    """Estimate total capture time. If seconds_per_shot is given explicitly,
    it's used as-is (back-compat for old callers). Otherwise the rate is
    derived from the real settle_ms in effect, so raising/lowering settle
    time in the UI actually moves the estimate: overhead_seconds covers
    shutter/save/drive, settle_ms covers the deliberate settle wait. The
    defaults (settle_ms=800, overhead_seconds=0.8) reproduce the old flat
    1.6s/shot baseline exactly.
    """
    if seconds_per_shot is None:
        seconds_per_shot = overhead_seconds + settle_ms / 1000.0
    return max(0.0, shot_count * seconds_per_shot)


def estimate_remaining_seconds(
    shots_remaining: int,
    *,
    elapsed_seconds: float = 0.0,
    shots_done: int = 0,
    fallback_seconds_per_shot: float = 1.6,
) -> float:
    """Remaining-time estimate that self-corrects against the pace actually
    observed so far this run. Uses elapsed_seconds / shots_done once at
    least one shot has completed; before that, falls back to a flat
    per-shot guess since there's no real data yet."""
    if shots_done > 0 and elapsed_seconds > 0:
        observed_rate = elapsed_seconds / shots_done
        return max(0.0, shots_remaining * observed_rate)
    return max(0.0, shots_remaining * fallback_seconds_per_shot)

def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes <= 0:
        return f"{secs}s"
    return f"{minutes}m {secs:02d}s"


def adaptive_step_size(
    previous_sharpness: float,
    current_sharpness: float,
    current_size: int,
    *,
    small_change: float = 15.0,
    large_change: float = 80.0,
) -> int:
    """Larger steps when sharpness barely changes; smaller when it jumps."""
    change = abs(current_sharpness - previous_sharpness)
    size = max(1, min(3, current_size))
    if change < small_change:
        return min(3, size + 1)
    if change > large_change:
        return max(1, size - 1)
    return size


def smooth_focus_path(offsets: list[int], min_gap: int = 1) -> list[int]:
    if not offsets:
        return []
    smoothed = [offsets[0]]
    for value in offsets[1:]:
        if abs(value - smoothed[-1]) >= min_gap:
            smoothed.append(value)
    if smoothed[-1] != offsets[-1]:
        smoothed.append(offsets[-1])
    return smoothed


def drive_unit_for_step_mm(step_mm: float) -> int:
    """Map DOF mm recommendation to Near1/Far1–3 unit size."""
    from camera_engine.stacking import preset_for_step_mm

    preset = preset_for_step_mm(step_mm)
    return {"Tiny": 1, "Small": 1, "Medium": 2, "Large": 3}[preset.value]


def plan_shot_count(
    start_position: int,
    end_position: int,
    *,
    magnification: float,
    aperture: float,
) -> int:
    distance = abs(end_position - start_position)
    unit = drive_unit_for_step_mm(recommended_step_mm(magnification, aperture))
    return max(2, distance // unit + 1)
