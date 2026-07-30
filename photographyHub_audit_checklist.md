# photographyHub — Consolidated Audit Checklist

Scope: `camera_engine/{workflow,stacking,dof,autofocus,analysis,camera}.py` and the `desktop_ui` wiring around planning, capture, and progress reporting. No code changes made — this is a list to work through one item at a time. File:line references are accurate as of this audit; re-check them if the file has since been edited.

---

## 🔴 1. Plan-making logic — why "Auto" keeps giving the same result

- [ ] **`camera_engine/stacking.py:93-102` (`roi_sharpness_bounds`) — the band-narrowing loop almost never runs; every adaptive plan falls back to the same fixed window.**
  The loop checks `curve[peak_offset - 1]` / `curve[peak_offset + 1]` — neighbors exactly **1 unit** away — to decide how wide the in-focus band is. But the scan that builds `curve` (`workflow.py::_coarse_scan_direction`) only records a point every `coarse_step` units, **2 units apart by default**. So the ±1 check is false on the first try, `near`/`far` never move, and the code falls into the `near == far` fallback: a hardcoded `peak_offset ± 16` (32 units), every time, regardless of the scene's actual measured sharpness curve. This value isn't wired to any setting.
  → The real scan data gets computed and then discarded. This is the primary reason Auto plans feel repetitive.
  **Fix:** make the neighbor check step by the same `coarse_step` the scan actually used (or store denser curve samples), so the band follows the real measured curve instead of collapsing to the fallback almost every run.

- [ ] **`camera_engine/dof.py::drive_unit_for_step_mm` only has 3 possible outputs (1, 2, or 3).** Combined with the bug above, frame spacing is one of three values regardless of magnification/aperture. Not a bug by itself, but it caps how much variety plans can show even after the bound bug is fixed.

- [ ] **`camera_engine/workflow.py::_measure_roi` scores the *whole frame* when no ROI has been drawn** (`opts.roi is None`). The entire adaptive scan/plan can be driven by whole-frame Laplacian-variance sharpness rather than the actual macro subject, if the user hasn't manually selected an ROI first. Worth deciding whether Auto mode should require an ROI before running, and warn/block if one isn't set.

- [ ] **`camera_engine/workflow.py:411-412` — `capture_adaptive_session`'s `sharpness_gain_threshold` and `patience` parameters are accepted but never used anywhere in the function body.** Dead parameters — consistent with the previously-flagged unused `should_stop_adaptive_stack()`/`adaptive_step_size()` helpers (see AUDIT_REPORT.md #5a). The "smart adaptive stopping" implied by these names isn't implemented; stopping is handled entirely by a separate ad-hoc stall counter inside `_coarse_scan_direction`. Not currently exposed in the UI, so no control is silently doing nothing — but worth wiring in or removing so the signature doesn't imply behavior that doesn't exist.

- [ ] **Inconsistent sharpness measurement scale across paths.** `_measure_roi` uses `max_side=640` (scan-time), `analyze_saved_file` defaults to `1280` (post-capture graph value), `analyze_image_bytes`'s own default is `960`. Thresholds are relative-to-peak within a single scan so this isn't broken today, but the numbers aren't on a common scale — a problem if scan-time and post-capture sharpness are ever compared directly (e.g. if the dead `sharpness_gain_threshold` above gets wired in later).

- [ ] **`camera_engine/workflow.py` (`_coarse_scan_direction` stall check) mixes an absolute floor with a relative one:** `low = ... after_score <= max(3.0, peak * 0.12)`. On a low-texture/low-contrast ROI, the whole curve could sit under the hardcoded `3.0` floor the entire time, making the scan look "stalled" immediately and consistently, independent of actual focus state.

- ✅ **Checked, not a bug:** magnification/aperture from the UI do reach the `CaptureOptions` used for planning (`mag_spin`/`aperture_edit` → `_update_dof()` → `_push_capture_options()` → `cmd_set_capture_options` → `camera_worker.set_capture_options()`). Wiring is intact.

- [ ] **`desktop_ui/main_window.py:960` — Adaptive mode silently defaults `max_frames` to 80 when "Images" is left at Auto (0).** Not currently the culprit (plans are usually well under 80), but worth knowing once the bound bug above is fixed and plans start varying more — it's a hidden ceiling.

---

## 🔴 2. Speed — the actual bottleneck

- [ ] **`camera_engine/camera.py` — every widget read/write (`set_widget_choice`, `focus_step`, `has_widget`, AF Press/Release, etc.) does its own full `get_config()` + `set_config()` USB round trip, with no caching.** Fires on every focus nudge (`_drive_to_offset`'s loop, up to 400 iterations), every coarse-scan step (up to 180), and every AF press/release — including up to 4 extra calls just for touch-AF positioning. This is the dominant real-world time cost of the whole app; likely bigger than every deliberate `sleep()` combined.
  **Fix:** fetch the config tree once per logical operation and reuse it across get/set calls, instead of re-fetching per individual nudge.

---

## 🟠 3. Time-left accuracy

- [ ] **`desktop_ui/main_window.py:910`/`:917` vs `:732` — two different duration formulas for the same number.** Pre-flight estimate adds `+ shots * 0.8`; the live countdown during a running stack doesn't. The number visibly jumps the moment a stack starts.
- [ ] **`camera_engine/dof.py:57` (`estimate_duration_seconds`) — flat 1.6s/shot constant, disconnected from `settle_ms`, stillness timeout, or AF settle.** Changing any of those settings doesn't move the ETA at all.
- [ ] **No elapsed-time tracking anywhere.** Remaining time is `shots_left × constant`, never corrected against how long shots already taken *this run* actually took — it can't self-correct if real shots are running faster or slower than assumed.
- [ ] **`camera_engine/workflow.py:302` — during scanning, progress denominator is `max_scan_steps` (180)**, even though scans almost always stall out and stop far earlier (`scan_stall_patience=5`, `scan_min_steps=12`). Time-left is wildly overestimated during scanning, then snaps down hard once the real (much smaller) plan is built.
- [ ] **`desktop_ui/main_window.py:910` — Adaptive pre-flight estimate guesses `shots = 80` when Auto**, unrelated to what the scan will actually produce.

---

## 🟡 4. Per-frame execution ("just: picture → focus → picture")

- [ ] **Loop shape is already correct.** `workflow.py`'s `capture_basic_stack` / adaptive-plan capture loop / `capture_focus_path` are genuinely just `drive → capture → next` once a plan is confirmed — no AF, no re-scanning. The slowness lives *inside* "drive" and "capture," not the loop structure.
- [ ] **"Change focus" = several small nudges**, each paying the full config round-trip from item 2 (`desktop_ui/camera_worker.py::_drive_to_offset`, max 3 units per nudge, looped).
- [ ] **"Take picture" = settle 800ms + up to 2.5s of stillness polling + save + synchronous RAW/JPEG sharpness analysis, every single frame** (`camera_engine/workflow.py::_settle`, `_save_capture`) — even during confirmed-plan execution, where the rig was already characterized during the scan phase.
  **Fix candidates:** shorten/skip redundant stillness re-checking once a plan is confirmed; consider not blocking the next focus drive on post-capture analysis during confirmed-plan execution.

---

## 🟡 5. Secondary / contributing (smaller, lower priority)

- [ ] `desktop_ui/camera_worker.py::_drive_to_offset` — a single failed `focus_step()` breaks the retry loop immediately instead of using its 400-attempt budget; each failure still pays the full config round-trip from item 2 for nothing.
- [ ] `camera_engine/workflow.py::_settle` — always sleeps the full `settle_ms` first, *then* runs stillness detection on top, rather than letting stillness detection alone decide when it's safe to shoot.

---

## ✅ Already fixed (from prior session — for context, not action items)

These were addressed before this audit and are listed here only so the picture is complete:

- Confirm/Cancel deadlock on the worker thread (`main_window.py` Qt connection type)
- RAW-only captures silently scoring sharpness = 0.0 (`analysis.py` rawpy preview extraction)
- Basic-stack reported offsets not matching what was actually driven (`workflow.py::capture_basic_stack`)
- AF reporting success before the camera had actually focused (`autofocus.py::try_camera_autofocus`)

---

### Suggested order of attack
1. Item 1's `roi_sharpness_bounds` fallback bug (plan variety) — single highest-value fix, self-contained.
2. Item 2's config caching (speed) — biggest overall win, touches the most code paths.
3. Item 3's ETA formula unification + elapsed-time tracking (accuracy).
4. Item 4's settle/stillness trimming on confirmed-plan execution.
5. Item 5, opportunistically while touching the same files.
