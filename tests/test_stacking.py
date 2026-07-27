from camera_engine.stacking import FocusStackPlan, FocusStepPreset, estimate_step_count, preset_for_step_mm, should_stop_adaptive_stack, step_mm_for_preset
from camera_engine.analysis import analyze_image_bytes


def test_analysis_rejects_bad_bytes() -> None:
    try:
        analyze_image_bytes(b"not-an-image")
    except ValueError:
        assert True
    else:
        assert False


def test_estimate_step_count_rounds_reasonably() -> None:
    assert estimate_step_count(8.0, 0.25) == 32


def test_focus_positions_interpolate_between_bounds() -> None:
    plan = FocusStackPlan(start_position=10, end_position=20, step_count=3)
    assert plan.focus_positions == [10, 15, 20]


def test_should_stop_adaptive_stack_honors_patience() -> None:
    assert should_stop_adaptive_stack(100.0, 99.5, 1.0, 1, 2) is False
    assert should_stop_adaptive_stack(100.0, 99.5, 1.0, 2, 2) is True


def test_step_preset_mapping_round_trips() -> None:
    assert step_mm_for_preset(FocusStepPreset.SMALL) == 0.25
    assert preset_for_step_mm(0.25) == FocusStepPreset.SMALL
