# EOS Utility MVP Implementation Plan

> **For agentic workers:** Execute task-by-task. User asked to start immediately after spec approval.

**Goal:** Remote-first Qt UI (live preview, AF steps, dynamic exposure, shoot) with stacking secondary; fix preview crash.

**Architecture:** Extend `GPhoto2Camera` with config introspection (omit missing settings). Rebuild `MainWindow` as preview + right rail + collapsible stack. Coerce preview to `bytes`.

**Tech Stack:** Python 3.14, PySide6, gphoto2, pytest

## Global Constraints

- Only show exposure controls the camera exposes (omit missing; do not grey out)
- AF: Near/Far × Small/Medium/Large
- Stacking stays secondary/collapsible
- No engine rewrite

---

### Task 1: Camera config API + preview bytes

**Files:** `camera_engine/camera.py`, `tests/test_camera_config.py`

- Fix widget find/set to use the same config tree (recursive name lookup)
- Add `list_setting_choices`, `get_setting_value`, `set_setting_value` for iso/shutter/aperture/wb
- Ensure `capture_preview` always returns `bytes`
- Unit tests with fake widgets / mocked camera where practical

### Task 2: Rebuild desktop UI

**Files:** `desktop_ui/main_window.py`, `desktop_ui/styles.py`

- Viewfinder-forward layout + dark camera-body stylesheet
- Dynamic settings combos, AF pad, shoot, connect
- Pause preview during capture/stack
- Collapsible Focus Stack panel with existing logic

### Task 3: Verify

- `pytest`
- Import/launch smoke (camera optional)
