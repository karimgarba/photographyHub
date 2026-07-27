# Macro Stack Quality — Implementation Plan

> **For agentic workers:** Use TDD. Steps = write failing test → implement → verify.

**Goal:** RAW pipeline, 800ms settle + stillness, DOF apply, Adaptive Approach A (ROI scan → fine fill → ordered capture), merge handoff.

**Tech:** `camera_engine/*`, `desktop_ui/*`, pytest.

## File map

| File | Role |
|------|------|
| `camera_engine/camera.py` | imageformat aliases; capture with real extension; optional shutter mode |
| `camera_engine/dof.py` | `plan_offsets_from_bounds`, keep adaptive_step_size |
| `camera_engine/stacking.py` | AdaptiveScanPlan helpers / threshold bounds |
| `camera_engine/workflow.py` | settle, stillness, Adaptive A scan+capture, RAW paths |
| `desktop_ui/camera_worker.py` | wire options, ROI into adaptive |
| `desktop_ui/main_window.py` | Stack controls + handoff buttons |
| `tests/test_macro_stack_quality.py` | unit tests for bounds, fill, extensions |

## Tasks

1. Tests for ROI bounds + fine fill offsets + extension from camera name
2. Implement stacking/dof helpers
3. Implement camera capture real suffix + imageformat setting
4. Implement workflow Adaptive A + settle/stillness
5. Wire worker + UI
6. Run full pytest
