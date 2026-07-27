from camera_engine.camera import destination_with_camera_suffix, prefer_raw_jpeg_choice
from camera_engine.dof import drive_unit_for_step_mm, plan_shot_count, recommended_step_mm
from camera_engine.stacking import (
    fine_fill_offsets,
    roi_sharpness_bounds,
    sort_capture_offsets,
)
from camera_engine.workflow import CaptureOptions, FocusStackController, merger_command


def test_roi_bounds_from_curve() -> None:
    curve = {i: 10.0 for i in range(-20, 21)}
    for i in range(-5, 6):
        curve[i] = 100.0
    curve[0] = 200.0
    near, far = roi_sharpness_bounds(curve, threshold=0.35)
    # Contiguous band around peak + margin must cover subject shoulders
    assert near <= -5
    assert far >= 5


def test_roi_bounds_include_soft_shoulders_and_margin() -> None:
    """Deep subject: peak is sharp, shoulders are ~25% — must not clip them."""
    curve = {i: 5.0 for i in range(-40, 41)}
    for i in range(-18, 19):
        curve[i] = 40.0 + (18 - abs(i))  # rises to ~58
    curve[0] = 200.0
    # At ±18 score ~40 = 0.2 of peak — with threshold 0.2 + margin should span wide
    near, far = roi_sharpness_bounds(curve, threshold=0.20, margin=6)
    assert near <= -20
    assert far >= 20
    assert far - near >= 40


def test_roi_bounds_contiguous_ignores_distant_noise_spike() -> None:
    curve = {i: 8.0 for i in range(-30, 31)}
    for i in range(-4, 5):
        curve[i] = 120.0
    curve[0] = 200.0
    curve[28] = 150.0  # noise spike far away — must not stretch bounds to +28
    near, far = roi_sharpness_bounds(curve, threshold=0.35, margin=2)
    assert far < 20
    assert near > -20


def test_fine_fill_covers_bounds() -> None:
    offsets = fine_fill_offsets(-10, 10, unit=2)
    assert offsets[0] == -10
    assert offsets[-1] == 10
    assert all(offsets[i] < offsets[i + 1] for i in range(len(offsets) - 1))


def test_sort_capture_offsets_directions() -> None:
    pts = [0, 2, 5]
    assert sort_capture_offsets(pts, near_to_far=True) == [0, 2, 5]
    assert sort_capture_offsets(pts, near_to_far=False) == [5, 2, 0]


def test_destination_keeps_camera_suffix() -> None:
    dest = destination_with_camera_suffix("/tmp/stack_1.jpg", "IMG_0001.CR3")
    assert str(dest).endswith(".CR3")
    dest2 = destination_with_camera_suffix("/tmp/stack_1.jpg", "IMG_0001.JPG")
    assert dest2.suffix.lower() in {".jpg", ".jpeg"}


def test_prefer_raw_jpeg_choice() -> None:
    choices = ["RAW", "Large Fine JPEG", "RAW + Large Fine JPEG"]
    picked = prefer_raw_jpeg_choice(choices)
    assert picked == "RAW + Large Fine JPEG"
    assert prefer_raw_jpeg_choice(["RAW", "Large Fine JPEG"]) == "RAW"
    assert prefer_raw_jpeg_choice(["Large Fine JPEG"]) == "Large Fine JPEG"


def test_plan_shot_count_from_dof() -> None:
    shots = plan_shot_count(0, 40, magnification=0.8, aperture=5.6)
    assert shots >= 2


def test_dof_unit_sane_for_macro() -> None:
    step = recommended_step_mm(0.8, 5.6)
    unit = drive_unit_for_step_mm(step)
    assert 0.05 <= step <= 2.0
    assert unit in (1, 2, 3)


def test_merger_command_helicon() -> None:
    cmd = merger_command("helicon", ["/a/1.CR3", "/a/2.CR3"])
    assert "1.CR3" in cmd
    assert len(cmd) > 10


def test_adaptive_respects_plan_reject(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "camera_engine.workflow.analyze_image_bytes",
        lambda *_a, **_k: SimpleNamespace(sharpness=50.0, edge_density=0.2),
    )
    monkeypatch.setattr("camera_engine.workflow.sleep", lambda *_a, **_k: None)

    class Cam:
        def prefer_raw_jpeg(self):
            return SimpleNamespace(success=True)

        def snapshot_exposure(self):
            return {}

        def restore_exposure(self, locked):
            return None

        def set_capture_target(self, target: str):
            return SimpleNamespace(success=True)

        def capture_image(self, destination, *, fetch_companions=True):
            raise AssertionError("should not capture after plan reject")

        def capture_preview(self):
            return SimpleNamespace(success=True, preview_data=b"P")

        def focus_step(self, direction, size=1):
            return SimpleNamespace(success=True)

    controller = FocusStackController(Cam())  # type: ignore[arg-type]
    monkeypatch.setattr(
        controller,
        "plan_adaptive_offsets",
        lambda **_k: ([0, 2, 4], {0: 10.0, 2: 80.0, 4: 40.0}),
    )
    session = controller.capture_adaptive_session(
        tmp_path,
        max_frames=10,
        current_offset=0,
        drive_to_offset=lambda t: t,
        options=CaptureOptions(settle_ms=0, stillness=False, prefer_raw_jpeg=False, roi=(0.4, 0.4, 0.2, 0.2)),
        await_plan=lambda offsets, curve: False,
    )
    assert session.capture_offsets == [0, 2, 4]
    assert session.steps[0].error == "Plan rejected"
    assert not any(s.success for s in session.steps)

    """
    Macro live-view often barely changes between focus steps (false stall).
    Scan must still traverse a deep subject, not stop after 2 tiny deltas.
    """
    from types import SimpleNamespace

    # Synthetic subject: sharp only in [-20, +20], peak at 0
    def sharpness_at(pos: int) -> float:
        if abs(pos) > 24:
            return 5.0
        return max(5.0, 200.0 - abs(pos) * 8.0)

    class ScanCam:
        def __init__(self) -> None:
            self.pos = 0
            self.focus_calls = 0

        def capture_preview(self):
            # Identical frames → would trigger old stall detector every step
            return SimpleNamespace(success=True, preview_data=b"SAME", stderr="")

        def focus_step(self, direction: str, size: int = 1):
            self.focus_calls += 1
            self.pos += size if direction == "far" else -size
            # Hard limit only past ±50
            if abs(self.pos) > 50:
                self.pos = 50 if self.pos > 0 else -50
                return SimpleNamespace(success=False)
            return SimpleNamespace(success=True)

        def set_capture_target(self, target: str):
            return SimpleNamespace(success=True)

    cam = ScanCam()

    def fake_analyze(data, roi=None, max_side=640):
        return SimpleNamespace(sharpness=sharpness_at(cam.pos), edge_density=0.1)

    monkeypatch.setattr("camera_engine.workflow.analyze_image_bytes", fake_analyze)
    monkeypatch.setattr("camera_engine.workflow.preview_frame_delta", lambda *_a, **_k: 0.3)
    monkeypatch.setattr("camera_engine.workflow.sleep", lambda *_a, **_k: None)

    controller = FocusStackController(cam)  # type: ignore[arg-type]
    offsets, curve = controller.plan_adaptive_offsets(
        current_offset=0,
        bump_offset=lambda d, s: None,
        options=CaptureOptions(
            settle_ms=0,
            stillness=False,
            prefer_raw_jpeg=False,
            roi=(0.4, 0.4, 0.2, 0.2),
            coarse_step=2,
            peak_threshold=0.20,
            magnification=0.8,
            aperture=5.6,
        ),
    )
    assert min(curve) <= -18
    assert max(curve) >= 18
    assert offsets[0] <= -18
    assert offsets[-1] >= 18
    assert len(offsets) >= 15
