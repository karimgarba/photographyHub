from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QMutex, QMutexLocker, Signal, Slot

from camera_engine.analysis import analyze_preview, preview_frame_delta
from camera_engine.autofocus import run_autofocus
from camera_engine.camera import CameraInfo, FOCUS_DRIVE_NAMES, GPhoto2Camera
from camera_engine.dof import smooth_focus_path
from camera_engine.stacking import FocusStepPreset
from camera_engine.workflow import CaptureOptions, FocusStackController


class CameraWorker(QObject):
    log = Signal(str)
    connected = Signal(bool, str)
    camera_info = Signal(object)
    settings_ready = Signal(object)
    preview_ready = Signal(bytes, object)
    focus_state = Signal(int, str)
    capture_done = Signal(bool, str, float, float)
    stack_progress = Signal(int, int, float, str)
    stack_scan_sample = Signal(object, int, float)  # curve dict, offset, score
    stack_plan_ready = Signal(object)  # {offsets, curve, message}
    stack_done = Signal(str, object)
    autofocus_done = Signal(bool, str)
    busy_changed = Signal(bool)
    cameras_found = Signal(object)
    path_changed = Signal(object)
    exposure_lock_changed = Signal(object)  # dict or None

    def __init__(self) -> None:
        super().__init__()
        self.camera = GPhoto2Camera()
        self.controller = FocusStackController(self.camera)
        self._mutex = QMutex()
        self._live = False
        self._exclusive = False
        self._histograms = False
        self._roi: tuple[float, float, float, float] | None = None
        self._focus_offset = 0
        self._limit = "OK"
        self._last_preview: bytes | None = None
        self._stall_count = 0
        self._hold_direction: str | None = None
        self._hold_size = 1
        self._preview_pending = False
        self._metric_tick = 0
        self._recording_path = False
        self._focus_path: list[int] = []
        self._capture_options = CaptureOptions()
        self._cancel = False
        self._plan_gate = Event()
        self._plan_accepted = False
        self._awaiting_plan = False

    def _stack_options(self) -> CaptureOptions:
        opts = self._capture_options
        return CaptureOptions(
            settle_ms=opts.settle_ms,
            stillness=opts.stillness,
            stillness_threshold=opts.stillness_threshold,
            stillness_timeout_ms=opts.stillness_timeout_ms,
            prefer_raw_jpeg=opts.prefer_raw_jpeg,
            roi=self._roi,
            near_to_far=opts.near_to_far,
            magnification=opts.magnification,
            aperture=opts.aperture,
            max_scan_steps=opts.max_scan_steps,
            coarse_step=opts.coarse_step,
            peak_threshold=opts.peak_threshold,
            bound_margin=opts.bound_margin,
            scan_stall_patience=opts.scan_stall_patience,
            scan_min_steps=opts.scan_min_steps,
            scan_stall_delta=opts.scan_stall_delta,
            flash_recycle_ms=opts.flash_recycle_ms,
            exposure_lock=opts.exposure_lock,
        )

    @Slot(object)
    def set_capture_options(self, options: object) -> None:
        if isinstance(options, CaptureOptions):
            self._capture_options = options
        elif isinstance(options, dict):
            cur = self._capture_options
            self._capture_options = CaptureOptions(
                settle_ms=int(options.get("settle_ms", cur.settle_ms)),
                stillness=bool(options.get("stillness", cur.stillness)),
                prefer_raw_jpeg=bool(options.get("prefer_raw_jpeg", cur.prefer_raw_jpeg)),
                near_to_far=bool(options.get("near_to_far", cur.near_to_far)),
                magnification=float(options.get("magnification", cur.magnification)),
                aperture=float(options.get("aperture", cur.aperture)),
                flash_recycle_ms=int(options.get("flash_recycle_ms", cur.flash_recycle_ms)),
                exposure_lock=bool(options.get("exposure_lock", cur.exposure_lock)),
                roi=self._roi,
            )

    def _emit_info(self) -> None:
        try:
            info = self.camera.get_camera_info()
            info.manual_focus = self.camera.has_widget(FOCUS_DRIVE_NAMES)
            info.autofocus = info.manual_focus or self.camera.has_widget(
                ("autofocusdrive", "touchaf", "touchafposition")
            )
            self.camera_info.emit(info)
        except Exception:
            self.camera_info.emit(CameraInfo(model=self.camera.model_name or "Camera", live_view=True))

    @Slot()
    def refresh_cameras(self) -> None:
        cameras = self.camera.detect_cameras()
        items = [f"{c.name} ({c.port})" for c in cameras] or ["No camera detected"]
        self.cameras_found.emit(items)

    @Slot(int)
    def connect_camera(self, index: int = 0) -> None:
        with QMutexLocker(self._mutex):
            result = (
                self.camera.connect_first_camera()
                if index < 0
                else self.camera.connect_selected_camera(index)
            )
            if result.success:
                label = self.camera.model_name or "connected"
                if self._capture_options.prefer_raw_jpeg:
                    fmt = self.camera.prefer_raw_jpeg()
                    if fmt.success and fmt.command:
                        self.log.emit(f"Format → {fmt.command[-1]}")
                self.connected.emit(True, label)
                self.settings_ready.emit(self.camera.list_available_settings())
                self._emit_info()
                self._focus_offset = 0
                self._limit = "OK"
                self.focus_state.emit(self._focus_offset, self._limit)
                self._live = True
                self.log.emit(f"Connected to {label}.")
            else:
                self.connected.emit(False, "DISCONNECTED")
                self.settings_ready.emit([])
                self.camera_info.emit(None)
                self.log.emit(result.stderr or "Connect failed.")

    @Slot()
    def disconnect_camera(self) -> None:
        with QMutexLocker(self._mutex):
            self._live = False
            self.camera.close()
            self.connected.emit(False, "DISCONNECTED")
            self.settings_ready.emit([])
            self.camera_info.emit(None)
            self.log.emit("Disconnected.")

    @Slot(bool)
    def set_live_preview(self, enabled: bool) -> None:
        self._live = enabled and self.camera.is_connected()

    @Slot(bool)
    def set_histograms_enabled(self, enabled: bool) -> None:
        self._histograms = enabled

    @Slot(object)
    def set_roi(self, roi: object) -> None:
        self._roi = roi if isinstance(roi, tuple) else None

    @Slot()
    def request_cancel(self) -> None:
        self._cancel = True
        if self._awaiting_plan:
            self._plan_accepted = False
            self._plan_gate.set()
        self.log.emit("Cancel requested…")

    @Slot(bool)
    def resolve_stack_plan(self, accept: bool) -> None:
        self._plan_accepted = bool(accept)
        if not accept:
            self._cancel = True
        self._plan_gate.set()

    def _await_plan(self, offsets: list[int], curve: dict[int, float]) -> bool:
        lo, hi = offsets[0], offsets[-1]
        message = f"{len(offsets)} frames  {lo:+d}→{hi:+d}"
        self._awaiting_plan = True
        self._plan_gate.clear()
        self._plan_accepted = False
        self.stack_plan_ready.emit({"offsets": offsets, "curve": curve, "message": message})
        self.log.emit(f"Focus plan ready — {message}. Confirm to capture.")
        # Wait until UI confirms/cancels (or request_cancel)
        while not self._plan_gate.wait(timeout=0.25):
            if self._cancel:
                break
        self._awaiting_plan = False
        return self._plan_accepted and not self._cancel

    @Slot()
    def zero_focus(self) -> None:
        """Drive lens back to the connect-time reference (offset 0)."""
        if self._exclusive or not self.camera.is_connected():
            self._focus_offset = 0
            self._limit = "OK"
            self._stall_count = 0
            self.focus_state.emit(self._focus_offset, self._limit)
            return
        self._exclusive = True
        self.busy_changed.emit(True)
        try:
            with QMutexLocker(self._mutex):
                self._drive_to_offset(0)
                self._stall_count = 0
                if self._limit.endswith("LIMIT") and self._focus_offset == 0:
                    self._limit = "OK"
                self.focus_state.emit(self._focus_offset, self._limit)
                self.log.emit(f"Focus reset → offset {self._focus_offset:+d}")
        finally:
            self._exclusive = False
            self.busy_changed.emit(False)

    @Slot(bool)
    def set_recording_path(self, enabled: bool) -> None:
        self._recording_path = enabled
        if enabled:
            self._focus_path = [self._focus_offset]
            self.log.emit("Recording focus path — move focus with S/M/L or the slider.")
        else:
            self._focus_path = smooth_focus_path(self._focus_path)
            self.log.emit(f"Focus path saved ({len(self._focus_path)} points).")
        self.path_changed.emit(list(self._focus_path))

    @Slot()
    def clear_focus_path(self) -> None:
        self._recording_path = False
        self._focus_path = []
        self.path_changed.emit([])
        self.log.emit("Focus path cleared.")

    @Slot()
    def tick_preview(self) -> None:
        if not self._live or self._exclusive or not self.camera.is_connected():
            return
        if self._preview_pending:
            return
        self._preview_pending = True
        try:
            with QMutexLocker(self._mutex):
                if self._hold_direction is not None:
                    self._drive_focus(self._hold_direction, self._hold_size, from_hold=True)
                result = self.camera.capture_preview()
                if not result.success or result.preview_data is None:
                    return
                data = result.preview_data
                self._last_preview = data
                metrics = None
                self._metric_tick += 1
                need_metrics = self._histograms or self._roi is not None or self._metric_tick % 5 == 0
                if need_metrics:
                    try:
                        metrics = analyze_preview(
                            data,
                            roi=self._roi,
                            want_histogram=self._histograms,
                            want_roi_sharpness=True,
                        )
                    except Exception:
                        metrics = None
                self.preview_ready.emit(data, metrics)
        finally:
            self._preview_pending = False

    def _record_offset(self) -> None:
        if self._recording_path and (not self._focus_path or self._focus_path[-1] != self._focus_offset):
            self._focus_path.append(self._focus_offset)
            self.path_changed.emit(list(self._focus_path))

    def _drive_focus(self, direction: str, size: int, *, from_hold: bool) -> None:
        before = self._last_preview
        before_score = -1.0
        if before is not None and self._roi is not None:
            try:
                before_score = analyze_preview(before, roi=self._roi, want_roi_sharpness=True).roi_sharpness or -1.0
            except Exception:
                before_score = -1.0

        result = self.camera.focus_step(direction, size)
        if not result.success:
            if not from_hold:
                self.log.emit(result.stderr or "Focus drive failed.")
            return

        self._focus_offset += size if direction == "far" else -size
        self._record_offset()

        preview = self.camera.capture_preview()
        if preview.success and preview.preview_data is not None:
            change = preview_frame_delta(before, preview.preview_data)
            self._last_preview = preview.preview_data
            after_score = -1.0
            try:
                after_score = (
                    analyze_preview(preview.preview_data, roi=self._roi, want_roi_sharpness=True).roi_sharpness
                    or -1.0
                )
            except Exception:
                after_score = -1.0
            score_delta = abs(after_score - before_score) if after_score >= 0 and before_score >= 0 else 999.0
            # Same rule as Adaptive: only declare limit when frame+ROI flat and sharpness low
            low = after_score < 0 or after_score <= 8.0
            if change < 0.8 and score_delta < 3.0 and low:
                self._stall_count += 1
            else:
                self._stall_count = 0
                self._limit = "OK"
            if self._stall_count >= 5:
                self._limit = "FAR LIMIT" if direction == "far" else "NEAR LIMIT"
        else:
            self._stall_count = 0

        self.focus_state.emit(self._focus_offset, self._limit)
        if not from_hold:
            self.log.emit(f"Focus {direction} ×{size}  offset {self._focus_offset:+d}  {self._limit}")

    def _drive_to_offset(self, target: int) -> int:
        guard = 0
        consecutive_failures = 0
        max_consecutive_failures = 3
        batch = getattr(self.camera, "batch_config", None)
        with batch() if callable(batch) else nullcontext():
            while self._focus_offset != target and guard < 400:
                if self._cancel:
                    break
                guard += 1
                direction = "far" if target > self._focus_offset else "near"
                size = min(3, abs(target - self._focus_offset))
                before = self._focus_offset
                self._drive_focus(direction, size, from_hold=True)
                if self._focus_offset == before:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        break
                    continue
                consecutive_failures = 0
                if self._limit.endswith("LIMIT") and (
                    (direction == "far" and target > self._focus_offset)
                    or (direction == "near" and target < self._focus_offset)
                ):
                    break
        return self._focus_offset

    @Slot(str, int)
    def focus_once(self, direction: str, size: int) -> None:
        if self._exclusive or not self.camera.is_connected():
            return
        with QMutexLocker(self._mutex):
            self._drive_focus(direction, size, from_hold=False)

    @Slot(int)
    def seek_focus(self, target: int) -> None:
        if self._exclusive or not self.camera.is_connected():
            return
        with QMutexLocker(self._mutex):
            self._drive_to_offset(target)
            self.focus_state.emit(self._focus_offset, self._limit)

    @Slot(str, int)
    def start_hold(self, direction: str, size: int) -> None:
        self._hold_direction = direction
        self._hold_size = size

    @Slot()
    def stop_hold(self) -> None:
        self._hold_direction = None

    @Slot(str, str)
    def set_setting(self, key: str, value: str) -> None:
        if not self.camera.is_connected():
            return
        with QMutexLocker(self._mutex):
            result = self.camera.set_setting_value(key, value)
            if result.success:
                self.log.emit(f"{key} → {self.camera.get_setting_value(key) or value}")
                self.settings_ready.emit(self.camera.list_available_settings())
            else:
                self.log.emit(result.stderr or f"Failed to set {key}")

    @Slot(str)
    def shoot(self, output_dir: str) -> None:
        if not self.camera.is_connected():
            self.capture_done.emit(False, "No camera connected", 0.0, 0.0)
            return
        self._cancel = False
        self._exclusive = True
        self.busy_changed.emit(True)
        self._live = False
        try:
            with QMutexLocker(self._mutex):
                ok, path, error, sharpness, edge = self.controller.capture_single(
                    Path(output_dir),
                    options=self._stack_options(),
                )
                if ok and path is not None:
                    self.capture_done.emit(True, str(path), sharpness, edge)
                    self.log.emit(f"Captured {path.name}")
                else:
                    self.capture_done.emit(False, error or "Capture failed", 0.0, 0.0)
                    self.log.emit(error or "Capture failed.")
        except Exception as error:
            self.capture_done.emit(False, str(error), 0.0, 0.0)
            self.log.emit(str(error))
        finally:
            self._exclusive = False
            self.busy_changed.emit(False)
            self._live = self.camera.is_connected()

    @Slot(object)
    def run_af(self, roi: object) -> None:
        if not self.camera.is_connected():
            self.autofocus_done.emit(False, "No camera connected")
            return
        if self._exclusive:
            self.log.emit("AF skipped — camera busy (stack/shoot). Try again.")
            self.autofocus_done.emit(False, "Camera busy")
            return
        has_native_af = self.camera.has_widget(("autofocusdrive", "touchaf", "touchafposition", "eosremoterelease"))
        if not self.camera.has_widget(FOCUS_DRIVE_NAMES) and not has_native_af:
            self.autofocus_done.emit(False, "Camera has no AF drive — cannot box-AF")
            self.log.emit("Box AF needs manualfocusdrive or native autofocus drive.")
            return
        self._cancel = False
        self._exclusive = True
        self.busy_changed.emit(True)
        self._live = False
        box = roi if isinstance(roi, tuple) else self._roi
        self._roi = box
        self.log.emit("Box AF…")
        try:
            with QMutexLocker(self._mutex):
                if self._cancel:
                    self.autofocus_done.emit(False, "Cancelled")
                    return
                result = run_autofocus(self.camera, roi=box, prefer_camera=True)
                if result.method == "software" and result.net_offset:
                    self._focus_offset += result.net_offset
                    self.focus_state.emit(self._focus_offset, self._limit)
                self.autofocus_done.emit(result.success, f"{result.method}: {result.message}")
                self.log.emit(f"AF {result.method}: {result.message}")
        except Exception as error:
            self.autofocus_done.emit(False, str(error))
            self.log.emit(str(error))
        finally:
            self._exclusive = False
            self.busy_changed.emit(False)
            self._live = self.camera.is_connected()

    def _progress(self, index: int, total: int, sharpness: float, message: str) -> None:
        self.stack_progress.emit(index, total, sharpness, message)

    def _on_scan_sample(self, curve: dict[int, float], offset: int, score: float) -> None:
        self.stack_scan_sample.emit(curve, offset, score)

    @Slot(str, int, int, str, str, int)
    def run_stack(
        self,
        output_dir: str,
        start: int,
        end: int,
        preset: str,
        mode: str,
        max_frames: int,
    ) -> None:
        if not self.camera.is_connected():
            self.stack_done.emit("No camera connected", [])
            return
        if start == end and mode == "Basic":
            self.stack_done.emit("Set Start and End at different focus positions first", [])
            self.log.emit("Stack aborted: Start and End are the same. Move focus, Set Start, move, Set End.")
            return
        self._cancel = False
        self._exclusive = True
        self.busy_changed.emit(True)
        self._live = False
        try:
            with QMutexLocker(self._mutex):
                out = Path(output_dir)
                opts = self._stack_options()

                def drive_to(target: int) -> int:
                    return self._drive_to_offset(target)

                def bump(direction: str, size: int) -> None:
                    self._focus_offset += size if direction == "far" else -size
                    self.focus_state.emit(self._focus_offset, self._limit)

                if opts.exposure_lock:
                    self.exposure_lock_changed.emit({"locked": True})
                    self.log.emit("Exposure lock on for stack")

                if mode == "Adaptive":
                    if opts.roi is None:
                        self.stack_done.emit("Adaptive needs an AF box on the subject", [])
                        self.log.emit("Put the AF box on the jewelry/flower, then Start Stack.")
                        return
                    session = self.controller.capture_adaptive_session(
                        out,
                        max_frames=max_frames,
                        current_offset=self._focus_offset,
                        on_progress=self._progress,
                        bump_offset=bump,
                        drive_to_offset=drive_to,
                        options=opts,
                        on_scan_sample=self._on_scan_sample,
                        should_cancel=lambda: self._cancel,
                        await_plan=self._await_plan,
                    )
                else:
                    session = self.controller.capture_basic_stack(
                        out,
                        start_position=start,
                        end_position=end,
                        preset=FocusStepPreset(preset),
                        current_offset=self._focus_offset,
                        drive_to_offset=drive_to,
                        on_progress=self._progress,
                        options=opts,
                    )

                paths: list[str] = []
                for step in session.steps:
                    if step.success and step.image_path:
                        paths.append(str(step.image_path))
                        for extra in step.extra_paths:
                            paths.append(str(extra))

                cancelled = any(s.error in {"Cancelled", "Plan rejected"} for s in session.steps)
                if cancelled and not any(s.success for s in session.steps):
                    summary = "Stack cancelled"
                else:
                    ok_count = sum(1 for s in session.steps if s.success)
                    summary = f"Stack complete: {ok_count}/{len(session.steps)} frames"
                    if session.capture_offsets:
                        summary += f"  offsets {session.capture_offsets[0]:+d}→{session.capture_offsets[-1]:+d}"
                self.log.emit(summary)
                if not paths and not cancelled:
                    self.log.emit("No frames saved — check capture target / card / format.")
                self.stack_done.emit(summary, paths)
                self.focus_state.emit(self._focus_offset, self._limit)
        except Exception as error:
            self.stack_done.emit(str(error), [])
            self.log.emit(str(error))
        finally:
            self._awaiting_plan = False
            self.exposure_lock_changed.emit(None)
            self._exclusive = False
            self.busy_changed.emit(False)
            self._live = self.camera.is_connected()

    @Slot(str)
    def replay_focus_path(self, output_dir: str) -> None:
        if len(self._focus_path) < 2:
            self.stack_done.emit("Record a path with at least 2 focus points first", [])
            self.log.emit("Path capture needs 2+ points.")
            return
        if not self.camera.is_connected():
            self.stack_done.emit("No camera connected", [])
            return
        self._cancel = False
        self._exclusive = True
        self.busy_changed.emit(True)
        self._live = False
        try:
            with QMutexLocker(self._mutex):
                session = self.controller.capture_focus_path(
                    Path(output_dir),
                    list(self._focus_path),
                    drive_to_offset=self._drive_to_offset,
                    on_progress=self._progress,
                    options=self._stack_options(),
                )
                paths = [str(s.image_path) for s in session.steps if s.success and s.image_path]
                summary = f"Path capture complete: {len(paths)}/{len(session.steps)}"
                self.log.emit(summary)
                self.stack_done.emit(summary, paths)
                self.focus_state.emit(self._focus_offset, self._limit)
        except Exception as error:
            self.stack_done.emit(str(error), [])
            self.log.emit(str(error))
        finally:
            self._exclusive = False
            self.busy_changed.emit(False)
            self._live = self.camera.is_connected()
