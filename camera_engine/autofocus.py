from __future__ import annotations

from dataclasses import dataclass

from camera_engine.analysis import analyze_image_bytes
from camera_engine.camera import GPhoto2Camera


@dataclass(slots=True)
class AutofocusResult:
    success: bool
    method: str
    message: str
    peak_sharpness: float = 0.0
    steps: int = 0
    # Net manual-focus steps: positive = far, negative = near (software hunt only).
    net_offset: int = 0


# Values that actually *drive* AF — not AF-mode On/Off toggles.
_AF_DRIVE_VALUES = (
    "1",
    "2",
    "3",
    "Focus In",
    "Focus Out",
    "Immediate",
    "Press Half",
    "Press Full",
    "Drive Lens",
)


def try_camera_autofocus(camera: GPhoto2Camera, roi: tuple[float, float, float, float] | None) -> AutofocusResult | None:
    """Attempt native camera AF drive. Returns None if nothing actually drove."""
    if roi is not None:
        cx = roi[0] + roi[2] / 2.0
        cy = roi[1] + roi[3] / 2.0
        for names in (("touchafposition",), ("touchaf",)):
            for value in (f"{cx:.3f},{cy:.3f}", f"{int(cx * 255)},{int(cy * 255)}"):
                positioned = camera.set_widget_choice(names, value)
                if positioned.success:
                    break

    for names in (("autofocusdrive",), ("eosremoterelease",)):
        for value in _AF_DRIVE_VALUES:
            result = camera.set_widget_choice(names, value)
            if result.success:
                return AutofocusResult(
                    success=True,
                    method="camera-drive",
                    message=f"Camera AF drive via {names[0]}={value}",
                )
    return None


def software_contrast_hunt(
    camera: GPhoto2Camera,
    *,
    roi: tuple[float, float, float, float] | None,
    max_steps: int = 14,
    coarse_size: int = 2,
    fine_size: int = 1,
) -> AutofocusResult:
    """Climb sharpness inside the AF box — coarse then fine, few previews."""

    def measure() -> float:
        preview = camera.capture_preview()
        if not preview.success or preview.preview_data is None:
            return -1.0
        return analyze_image_bytes(preview.preview_data, roi=roi, max_side=320).sharpness

    current = measure()
    if current < 0:
        return AutofocusResult(False, "software", "Could not read preview for AF")

    best = current
    steps = 0
    net = 0  # +far / -near
    moved = False

    def step(direction: str, size: int) -> bool:
        nonlocal steps, net, moved
        drive = camera.focus_step(direction, size)
        steps += 1
        if not drive.success:
            return False
        moved = True
        net += size if direction == "far" else -size
        return True

    # One-step probe each way (coarse), undo, pick better climb direction
    probe: dict[str, float] = {"far": current, "near": current}
    for direction in ("far", "near"):
        if not step(direction, coarse_size):
            continue
        score = measure()
        if score >= 0:
            probe[direction] = score
        back = "near" if direction == "far" else "far"
        step(back, coarse_size)

    direction = "far" if probe["far"] >= probe["near"] else "near"

    # Coarse climb
    worsening = 0
    coarse_budget = max(4, max_steps // 2)
    for _ in range(coarse_budget):
        if not step(direction, coarse_size):
            break
        score = measure()
        if score < 0:
            break
        if score > best * 1.008:
            best = score
            worsening = 0
        else:
            worsening += 1
            if worsening >= 1:
                back = "near" if direction == "far" else "far"
                step(back, coarse_size)
                break

    # Fine polish around peak
    fine_budget = max(3, max_steps - coarse_budget)
    worsening = 0
    for _ in range(fine_budget):
        if not step(direction, fine_size):
            break
        score = measure()
        if score < 0:
            break
        if score > best * 1.005:
            best = score
            worsening = 0
        else:
            worsening += 1
            if worsening >= 1:
                back = "near" if direction == "far" else "far"
                step(back, fine_size)
                break

    return AutofocusResult(
        success=moved,
        method="software",
        message=f"Box AF peak {best:.1f} (was {current:.1f}) in {steps} steps",
        peak_sharpness=best,
        steps=steps,
        net_offset=net,
    )


def run_autofocus(
    camera: GPhoto2Camera,
    *,
    roi: tuple[float, float, float, float] | None,
    prefer_camera: bool = False,
) -> AutofocusResult:
    """
    Focus on the AF box.

    When prefer_camera=True we try the camera's native AF drive first and fall
    back to software contrast hunting only if native AF is unavailable or fails.
    """
    if prefer_camera:
        native = try_camera_autofocus(camera, roi)
        if native is not None and native.success:
            return native

    # Software hunt is the fallback when native AF cannot actually drive focus.
    software = software_contrast_hunt(camera, roi=roi)
    if software.success:
        return software

    native = try_camera_autofocus(camera, roi)
    if native is not None and native.success:
        return native
    return software
