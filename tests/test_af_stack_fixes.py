from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from camera_engine.autofocus import (
    _AF_DRIVE_VALUES,
    software_contrast_hunt,
    run_autofocus,
    try_camera_autofocus,
)
from camera_engine.stacking import FocusStepPreset
from camera_engine.workflow import CaptureOptions, FocusStackController
from desktop_ui.compare_preview import ComparePreview


class FakeCamera:
    """Minimal camera stub for AF / stack unit tests."""

    def __init__(self, *, sharpness_curve: list[float] | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self._sharpness = list(sharpness_curve or [10.0, 20.0, 40.0, 80.0, 70.0, 60.0])
        self._idx = 0
        self._pos = 0
        self.widget_results: dict[tuple[str, str], bool] = {}

    def set_widget_choice(self, names, value: str):
        key = (names[0], value)
        self.calls.append(("set_widget", key))
        ok = self.widget_results.get(key, False)
        return SimpleNamespace(success=ok)

    def focus_step(self, direction: str, size: int = 1):
        self.calls.append(("focus_step", (direction, size)))
        self._pos += size if direction == "far" else -size
        return SimpleNamespace(success=True)

    def capture_preview(self):
        score = self._sharpness[min(self._idx, len(self._sharpness) - 1)]
        self._idx += 1
        # Encode sharpness into a tiny synthetic JPEG via analyze path — use raw marker.
        # analyze_image_bytes needs real image bytes; we patch at hunt level by monkeypatching analysis.
        return SimpleNamespace(success=True, preview_data=b"PREVIEW", stderr="")

    def set_capture_target(self, target: str):
        return SimpleNamespace(success=True)

    def capture_image(self, destination: Path, *, fetch_companions: bool = True):
        destination.write_bytes(b"fake-jpeg")
        return SimpleNamespace(success=True, saved_path=destination, stderr="", extra_paths=[])


def test_af_drive_values_exclude_mode_toggles() -> None:
    """On/Off would falsely report AF success without moving the lens."""
    assert "On" not in _AF_DRIVE_VALUES
    assert "Off" not in _AF_DRIVE_VALUES
    assert "1" in _AF_DRIVE_VALUES


def test_try_camera_autofocus_ignores_mode_toggle(monkeypatch) -> None:
    cam = FakeCamera()
    # Only AF-mode On succeeds — must NOT count as a drive
    cam.widget_results[("autofocusdrive", "On")] = True
    cam.widget_results[("autofocusdrive", "1")] = False

    assert try_camera_autofocus(cam, None) is None

    cam.widget_results[("autofocusdrive", "1")] = True
    result = try_camera_autofocus(cam, None)
    assert result is not None
    assert result.success
    assert "1" in result.message


def test_run_autofocus_prefers_native_when_available(monkeypatch) -> None:
    cam = FakeCamera()
    cam.widget_results[("autofocusdrive", "1")] = True

    result = run_autofocus(cam, roi=(0.4, 0.4, 0.2, 0.2), prefer_camera=True)

    assert result.success
    assert result.method == "camera-drive"
    assert not any(call[0] == "focus_step" for call in cam.calls)


def test_software_hunt_tracks_net_offset(monkeypatch) -> None:
    scores = iter([10.0, 12.0, 10.0, 11.0, 30.0, 50.0, 70.0, 65.0, 60.0, 55.0])

    def fake_analyze(_data, roi=None, max_side=640):
        return SimpleNamespace(sharpness=next(scores, 50.0), edge_density=0.1)

    monkeypatch.setattr("camera_engine.autofocus.analyze_image_bytes", fake_analyze)
    cam = FakeCamera()
    result = software_contrast_hunt(cam, roi=(0.4, 0.4, 0.2, 0.2), max_steps=8)
    assert result.success
    assert result.method == "software"
    assert any(c[0] == "focus_step" for c in cam.calls)
    assert isinstance(result.net_offset, int)
    # Fast path: should not thrash (probe + coarse + fine, capped)
    focus_calls = [c for c in cam.calls if c[0] == "focus_step"]
    assert len(focus_calls) <= 20


def test_software_hunt_fails_when_drive_fails(monkeypatch) -> None:
    scores = iter([10.0, 10.0, 10.0])

    def fake_analyze(_data, roi=None, max_side=640):
        return SimpleNamespace(sharpness=next(scores, 10.0), edge_density=0.1)

    class NoMoveCamera(FakeCamera):
        def focus_step(self, direction: str, size: int = 1):
            self.calls.append(("focus_step", (direction, size)))
            return SimpleNamespace(success=False)

    monkeypatch.setattr("camera_engine.autofocus.analyze_image_bytes", fake_analyze)
    cam = NoMoveCamera()
    result = software_contrast_hunt(cam, roi=(0.4, 0.4, 0.2, 0.2), max_steps=4)
    assert not result.success
    assert result.steps > 0


def test_adaptive_capture_unpacks_save_result(tmp_path, monkeypatch) -> None:
    """Regression: _save_capture returns 6 values; adaptive must unpack all."""
    monkeypatch.setattr(
        "camera_engine.workflow.analyze_image_bytes",
        lambda *_a, **_k: SimpleNamespace(sharpness=80.0, edge_density=0.3),
    )

    class AdaptiveCam(FakeCamera):
        def prefer_raw_jpeg(self):
            return SimpleNamespace(success=True)

        def snapshot_exposure(self):
            return {}

        def restore_exposure(self, locked):
            return None

        def focus_step(self, direction: str, size: int = 1):
            return SimpleNamespace(success=True)

        def capture_preview(self):
            return SimpleNamespace(success=True, preview_data=b"PREVIEW", stderr="")

    cam = AdaptiveCam()
    controller = FocusStackController(cam)  # type: ignore[arg-type]

    # Skip the long scan — inject planned offsets via monkeypatch
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
    )
    assert len(session.steps) == 3
    assert all(s.success for s in session.steps)
    assert all(s.image_path and s.image_path.exists() for s in session.steps)


def test_basic_stack_drives_to_marks(tmp_path, monkeypatch) -> None:
    visited: list[int] = []

    def drive_to(target: int) -> int:
        visited.append(target)
        return target

    monkeypatch.setattr(
        "camera_engine.workflow.analyze_image_bytes",
        lambda *_a, **_k: SimpleNamespace(sharpness=50.0, edge_density=0.2),
    )
    cam = FakeCamera()
    controller = FocusStackController(cam)  # type: ignore[arg-type]
    session = controller.capture_basic_stack(
        tmp_path,
        start_position=0,
        end_position=4,
        preset=FocusStepPreset.SMALL,
        current_offset=2,
        drive_to_offset=drive_to,
        options=CaptureOptions(settle_ms=0, stillness=False, prefer_raw_jpeg=False),
    )
    assert visited[0] == 0  # seek start first
    assert len(session.steps) >= 2
    assert all(s.success for s in session.steps)
    assert all(s.image_path and s.image_path.exists() for s in session.steps)


def test_path_capture_follows_offsets(tmp_path, monkeypatch) -> None:
    visited: list[int] = []

    def drive_to(target: int) -> int:
        visited.append(target)
        return target

    monkeypatch.setattr(
        "camera_engine.workflow.analyze_image_bytes",
        lambda *_a, **_k: SimpleNamespace(sharpness=40.0, edge_density=0.2),
    )
    cam = FakeCamera()
    controller = FocusStackController(cam)  # type: ignore[arg-type]
    session = controller.capture_focus_path(
        tmp_path,
        [0, 2, 5],
        drive_to_offset=drive_to,
        options=CaptureOptions(settle_ms=0, stillness=False, prefer_raw_jpeg=False),
    )
    assert visited == [0, 2, 5]
    assert len(session.steps) == 3


def test_compare_preview_load_missing_returns_null() -> None:
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    assert ComparePreview._load("/no/such/before.jpg").isNull()
    assert ComparePreview._load(None).isNull()
    assert _app is not None
