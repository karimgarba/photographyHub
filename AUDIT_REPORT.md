# photographyHub — Focus Plan Logic Audit

Scope: `camera_engine/{workflow,stacking,dof,autofocus,analysis}.py` and the
`desktop_ui` wiring around the stack Confirm/Cancel flow. Full test suite run
after every change; 48/48 pass.

---

## 1. FIXED — Confirm/Cancel deadlock (the "nothing happens" bug)

**Symptom:** after a stack plan was built ("Focus plan ready — 41 frames
-26→+18. Confirm to capture."), clicking Confirm Capture or Cancel did
nothing. Single "Shoot" was unaffected.

**Root cause:** `run_stack()` executes entirely on the worker `QThread`.
Inside it, `_await_plan()` busy-waits on a `threading.Event`. The Confirm and
Cancel buttons emit signals (`cmd_resolve_plan`, `cmd_cancel`) connected with
Qt's default connection type, which becomes a **queued cross-thread call**.
A queued call can only be dispatched when the target thread's event loop is
free — but the worker thread was stuck deep inside `_await_plan`'s wait
loop, so the click could never be delivered. The thread was waiting on the
exact event that only its own free event loop could deliver: a deadlock.

**Fix:** `desktop_ui/main_window.py` — both connections now use
`Qt.ConnectionType.DirectConnection`:
```python
self.cmd_cancel.connect(self.worker.request_cancel, Qt.ConnectionType.DirectConnection)
self.cmd_resolve_plan.connect(self.worker.resolve_stack_plan, Qt.ConnectionType.DirectConnection)
```
Safe because both target slots only touch a `threading.Event` and plain
bools — inherently thread-safe, no Qt/GUI state involved.

**Tests:** `tests/test_stack_confirm_deadlock.py` — one test reproduces the
original deadlock against the real `CameraWorker._await_plan`/
`resolve_stack_plan` on a real `QThread` (proves it genuinely hangs), one
proves the fix resolves in well under a second.

---

## 2. FIXED — RAW-only captures silently got sharpness = 0.0

**Symptom:** console spam (`Old-style JPEG compression support is not
configured`, `TIFFReadDirectory` warnings) during/after captures when
shooting RAW-only.

**Root cause:** `_save_capture()` in `workflow.py` looked for a JPEG among
the saved file and its companions to score sharpness. With Format → RAW (no
JPEG companion), none is found, so it fell back to reading the **raw CR2
bytes** and handing them straight to `cv2.imdecode`. CR2 is a TIFF-based
container, so OpenCV's TIFF codec tries to decode it and hits Canon's
legacy-JPEG-compressed embedded preview, which the installed libtiff build
doesn't support. The exception was caught, so nothing crashed — but two
things were silently wrong: (1) OpenCV's C++ layer prints those TIFF errors
to stderr regardless of the Python `except`, and (2) **every frame's
`sharpness`/`edge_density` stayed 0.0** whenever shooting RAW-only, which
also flattened the live sharpness graph during stacking.

**Fix:** `camera_engine/analysis.py` gained:
- `extract_raw_preview_bytes(path)` — uses `rawpy`/libraw (added as a
  dependency; ships a bundled libraw, no system packages needed) to pull the
  camera's own embedded JPEG preview out of the raw file. Never raises;
  returns `None` on any failure.
- `analyze_saved_file(path, ...)` — routes `.cr2/.cr3/.nef/.arw/.dng/.raf/
  .orf/.rw2/.pef/.srw` through the rawpy path and everything else through
  the existing `cv2.imdecode` path. Returns `None` (not a fake 0.0) when no
  analyzable data could be obtained.

`workflow.py`'s `_save_capture()` now calls `analyze_saved_file()` instead of
feeding raw bytes to `cv2.imdecode` directly.

**Tests:** `tests/test_raw_sharpness_and_offsets.py` covers: rawpy failing
gracefully on a non-raw file with a raw extension (returns `None`, never
raises), `analyze_saved_file` returning `None` rather than a misleading
0.0, and an end-to-end `_save_capture()` call on a `.cr2`-suffixed file.

**Caveat — please validate on real hardware:** no CR2 sample from your
4000D was available in the sandbox this was built in, so the rawpy
extraction path itself was validated against synthetic/invalid raw data
(proving the failure path is clean) rather than a genuine Canon CR2. rawpy/
libraw is the standard library for this exact operation and should handle
4000D CR2s without issue, but it's worth confirming sharpness values show up
correctly for a real RAW-only stack before relying on it.

---

## 3. FIXED — Basic-stack reported offsets didn't match what was driven

**Symptom (latent, not yet visibly reported by you, but real):** for
Basic-mode stacks where `(end − start)` isn't an exact multiple of the
preset's unit size (e.g. start=0, end=10, Large preset → unit=3), the
UI/session's `capture_offsets` — used for any downstream summary/label —
reported the evenly-spaced *idealized* positions (`[0, 3, 7, 10]`), while
the code that actually drives the lens moves in fixed unit steps from the
start (`[0, 3, 6, 9]`). Capture itself was correct; the reported metadata
about where frames were taken was not.

**Fix:** `capture_basic_stack()` now computes `capture_offsets` the same way
positions are actually driven (`start + i·direction·unit`), so what's
reported always matches what was captured.

**Tests:** `test_basic_stack_reports_actually_driven_offsets_not_idealized_ones`
asserts both the reported offsets and each step's real `focus_position`
equal `[0, 3, 6, 9]` for the 0→10/Large example above.

---

## 4. FIXED — AF "just loops / racking forever, never focuses"

**Symptom:** Box AF appears to "succeed" almost instantly and repeatedly,
but the lens keeps racking focus (and live view visibly hunts/"zooms") and
never actually settles — matches the earlier log's ~12 back-to-back
"Box AF… / AF ok — camera-drive" cycles.

**Root cause:** `try_camera_autofocus()` in `autofocus.py` declared
`success=True` the instant `camera.set_widget_choice(("eosremoterelease",),
"Press Half")` returned success — but that only means gPhoto2's PTP command
was *accepted*, not that the camera actually achieved focus. Live View
contrast-detect AF on a body like the 4000D runs asynchronously and commonly
takes several hundred ms to over a second to settle. Two consequences:

- The app reported "AF ok" well before the lens had actually finished (or
  sometimes even started) racking, so clicking Box AF again — reasonably,
  since the UI said it was done — **interrupted the in-flight AF hunt and
  restarted it from scratch**, over and over.
- The code never sent `Release Half` to close the half-press cycle it
  opened, which can leave the camera in an ambiguous held-half-press state
  that interferes with the next AF/shoot command.

**Fix:** `try_camera_autofocus()` now, after a drive command is accepted:
waits a settle period (0.9s, configurable via a new `settle_seconds`
parameter for testing), re-measures ROI sharpness, and only reports success
if sharpness held or improved (allowing up to a 5% dip) — otherwise returns
`None` so the existing fallback to `software_contrast_hunt` (which already
did this correctly) kicks in. It also now always sends `Release Half` after
a successful `Press Half` to properly close the cycle. If the preview can't
be measured (inconclusive), it still trusts the native "success" rather than
blocking indefinitely.

**Tests:** `tests/test_af_verify_before_success.py` — sharpness holding →
success; sharpness dropping after settle → falls back (`None`); Press Half
is always paired with Release Half.

**Trade-off worth knowing:** every native AF attempt now takes ~0.9s longer
than before (previously near-instant, but that instant response was the bug
— it was reporting success before the camera had actually focused). This is
a deliberate correctness-over-speed trade for a body where Live View AF
genuinely isn't instant.

---

## 5. Plan-generation logic — design review (not changed, flagging for awareness)

The core adaptive-plan pipeline (`plan_adaptive_offsets` → coarse scan both
directions → `roi_sharpness_bounds` → `fine_fill_offsets` →
`densify_where_steep` → `sort_capture_offsets`) is sound and reasonably
well-tested (`test_macro_stack_quality.py`). Two things worth knowing about:

**a) Two "smart" helpers exist but are never called.**
`stacking.should_stop_adaptive_stack()` and `dof.adaptive_step_size()` are
both implemented and unit-tested, but the real scan loop
(`_coarse_scan_direction`) doesn't call either — it uses a **fixed** step
size (`options.coarse_step`, default 2) for the whole scan, and re-implements
its own ad-hoc stall detection (`frame_delta`, `score_delta`, a `low`
threshold, and a stall counter) rather than the tested
`should_stop_adaptive_stack`. This isn't broken — the ad-hoc version works —
but it means the "grow the step when sharpness is flat, shrink it when it's
changing fast" behavior implied by `adaptive_step_size`'s docstring isn't
actually happening during scans. Worth deciding whether to wire these in or
remove them so the code doesn't suggest capability that isn't there.

**b) A single failed focus-drive command aborts `_drive_to_offset` outright.**
In `camera_worker.py`, `_drive_to_offset()` allows up to 400 retry
iterations to reach a target — but `_drive_focus()` only updates
`self._focus_offset` when `camera.focus_step(...)` succeeds; if it fails
even once, `_drive_to_offset` sees `self._focus_offset == before` and
**breaks immediately**, without using any of its retry budget. gPhoto2
focus-step commands over USB are a known source of occasional transient
failures, so a single hiccup mid-stack could end a drive-to-target attempt
early, landing frames at the wrong offset (or short of the plan) with no
retry. Not changed here since it touches live hardware-driving behavior I
can't validate without your 4000D connected — flagging so you can decide
whether to add a bounded retry (e.g. 2–3 attempts) before giving up.

Neither (a) nor (b) explains a symptom you've reported — they're proactive
findings from reading the logic closely.

---

## Files changed

| File | Change |
|---|---|
| `desktop_ui/main_window.py` | Confirm/Cancel connections → `DirectConnection` |
| `camera_engine/analysis.py` | Added `extract_raw_preview_bytes`, `analyze_saved_file`, `RAW_EXTENSIONS` |
| `camera_engine/workflow.py` | `_save_capture` uses `analyze_saved_file`; `capture_basic_stack` reports real driven offsets |
| `camera_engine/autofocus.py` | `try_camera_autofocus` verifies AF actually settled before reporting success; sends `Release Half` |
| `pyproject.toml` | Added `rawpy>=0.21` dependency |
| `tests/test_stack_confirm_deadlock.py` | New — deadlock regression test |
| `tests/test_raw_sharpness_and_offsets.py` | New — RAW-sharpness + offset-reporting regression tests |
| `tests/test_af_verify_before_success.py` | New — AF verify-before-success regression tests |

**Before running the app again:** install the new dependency —
```bash
.venv/bin/pip install -e .
```
or just `.venv/bin/pip install rawpy`.

## Test results

```
51 passed in 4.79s
```
(43 pre-existing tests unchanged and passing, 8 new across this pass and the previous one.)
