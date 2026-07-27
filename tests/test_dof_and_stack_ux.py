from camera_engine.dof import (
    adaptive_step_size,
    estimate_dof,
    format_duration,
    parse_aperture,
    recommended_step_mm,
    smooth_focus_path,
)
from camera_engine.workflow import FocusStackController
from camera_engine.stacking import FocusStepPreset


def test_parse_aperture() -> None:
    assert parse_aperture("f/5.6") == 5.6
    assert parse_aperture("F8") == 8.0


def test_dof_estimate_reasonable() -> None:
    estimate = estimate_dof(0.8, 5.6)
    assert estimate.step_mm > 0
    assert estimate.diffraction == "Excellent"
    assert recommended_step_mm(1.0, 8.0) > 0


def test_adaptive_step_grows_when_flat() -> None:
    assert adaptive_step_size(100.0, 102.0, 1) == 2
    assert adaptive_step_size(100.0, 200.0, 3) == 2


def test_smooth_focus_path() -> None:
    assert smooth_focus_path([0, 0, 1, 5, 5, 9]) == [0, 1, 5, 9]


def test_format_duration() -> None:
    assert format_duration(65) == "1m 05s"


def test_basic_plan_from_marks() -> None:
    plan = FocusStackController().build_preset_plan(0, 40, FocusStepPreset.SMALL)
    assert plan.step_count >= 2
    assert plan.focus_positions[0] == 0
    assert plan.focus_positions[-1] == 40
