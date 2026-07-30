from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep, time

from camera_engine.analysis import analyze_image_bytes, analyze_saved_file, preview_frame_delta
from camera_engine.camera import GPhoto2Camera
from camera_engine.dof import drive_unit_for_step_mm, recommended_step_mm, smooth_focus_path
from camera_engine.stacking import (
    FocusStackPlan,
    FocusStepPreset,
    densify_where_steep,
    fine_fill_offsets,
    roi_sharpness_bounds,
    sort_capture_offsets,
)


ProgressCallback = Callable[[int, int, float, str], None]
CoverageCallback = Callable[[dict[int, float], list[int]], None]
ScanSampleCallback = Callable[[dict[int, float], int, float], None]
CancelCheck = Callable[[], bool]
PlanDecisionCallback = Callable[[list[int], dict[int, float]], bool]


@dataclass(slots=True)
class CaptureOptions:
    settle_ms: int = 800
    stillness: bool = True
    stillness_threshold: float = 2.5
    stillness_timeout_ms: int = 2500
    prefer_raw_jpeg: bool = True
    roi: tuple[float, float, float, float] | None = None
    near_to_far: bool = True
    magnification: float = 0.8
    aperture: float = 5.6
    max_scan_steps: int = 180
    coarse_step: int = 2
    peak_threshold: float = 0.20
    bound_margin: int = 6
    # Coarse-scan end-stop: macro LV often barely moves → need patience + min travel
    scan_stall_patience: int = 5
    scan_min_steps: int = 12
    scan_stall_delta: float = 0.8
    # Absolute sharpness floor used alongside the relative (peak * 0.12)
    # one when deciding a frame reads "low". Lower this for low-texture/
    # low-contrast subjects where real readings sit under the default.
    stall_floor: float = 3.0
    exposure_lock: bool = True
    flash_recycle_ms: int = 0
    fetch_companions: bool = True


@dataclass(slots=True)
class CaptureStep:
    index: int
    focus_position: int
    image_path: Path | None = None
    extra_paths: list[Path] = field(default_factory=list)
    success: bool = False
    error: str = ""
    sharpness: float = 0.0
    edge_density: float = 0.0


@dataclass(slots=True)
class FocusStackSession:
    plan: FocusStackPlan
    output_dir: Path
    steps: list[CaptureStep] = field(default_factory=list)
    scan_curve: dict[int, float] = field(default_factory=dict)
    capture_offsets: list[int] = field(default_factory=list)


def _unit_step(preset: FocusStepPreset) -> int:
    return {"Tiny": 1, "Small": 1, "Medium": 2, "Large": 3}[preset.value]


def merger_command(tool: str, paths: list[str]) -> str:
    quoted = " ".join(f'"{p}"' for p in paths)
    if tool.lower().startswith("zerene"):
        return f"java -jar ZereneStacker.jar {quoted}"
    return f"HeliconFocus -silent {quoted}"


class FocusStackController:
    def __init__(self, camera: GPhoto2Camera | None = None) -> None:
        self.camera = camera or GPhoto2Camera()

    def build_session(self, plan: FocusStackPlan, output_dir: Path) -> FocusStackSession:
        steps = [
            CaptureStep(index=idx, focus_position=position)
            for idx, position in enumerate(plan.focus_positions, start=1)
        ]
        return FocusStackSession(plan=plan, output_dir=output_dir, steps=steps)

    def build_preset_plan(
        self, start_position: int, end_position: int, preset: FocusStepPreset
    ) -> FocusStackPlan:
        unit = _unit_step(preset)
        distance = abs(end_position - start_position)
        step_count = max(1, distance // unit + 1)
        return FocusStackPlan(
            start_position=start_position,
            end_position=end_position,
            step_count=step_count,
        )

    def _prepare_capture(self, options: CaptureOptions | None = None) -> dict[str, str]:
        locked: dict[str, str] = {}
        if options is not None and options.prefer_raw_jpeg:
            prefer = getattr(self.camera, "prefer_raw_jpeg", None)
            if callable(prefer):
                prefer()
        if options is not None and options.exposure_lock:
            snap = getattr(self.camera, "snapshot_exposure", None)
            if callable(snap):
                locked = snap()
        for target in ("Memory card", "Card", "Internal RAM", "SDRAM"):
            result = self.camera.set_capture_target(target)
            if result.success:
                break
        return locked

    def _settle(self, options: CaptureOptions) -> None:
        if not options.stillness:
            if options.settle_ms > 0:
                sleep(options.settle_ms / 1000.0)
            return
        # Stillness polling exists precisely to answer "how long do we
        # actually need to wait" -- don't also pay the full settle_ms as
        # an unconditional tax in front of it. settle_ms instead sets a
        # floor on the poll deadline alongside stillness_timeout_ms, so a
        # slow/cautious settle_ms still bounds the wait without being
        # paid twice.
        deadline = time() + max(options.settle_ms, options.stillness_timeout_ms) / 1000.0
        previous: bytes | None = None
        while time() < deadline:
            preview = self.camera.capture_preview()
            if not preview.success or preview.preview_data is None:
                return
            delta = preview_frame_delta(previous, preview.preview_data)
            previous = preview.preview_data
            if previous is not None and delta <= options.stillness_threshold:
                return
            sleep(0.08)

    def _before_shutter(self, options: CaptureOptions, locked: dict[str, str]) -> None:
        if options.exposure_lock and locked:
            restore = getattr(self.camera, "restore_exposure", None)
            if callable(restore):
                restore(locked)
        self._settle(options)
        if options.flash_recycle_ms > 0:
            sleep(options.flash_recycle_ms / 1000.0)

    def _save_capture(
        self,
        destination: Path,
        *,
        options: CaptureOptions | None = None,
        roi: tuple[float, float, float, float] | None = None,
        locked: dict[str, str] | None = None,
    ) -> tuple[bool, Path | None, str, float, float, list[Path]]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        opts = options or CaptureOptions(settle_ms=0, stillness=False)
        self._before_shutter(opts, locked or {})
        fetch = opts.fetch_companions
        capture = self.camera.capture_image(destination, fetch_companions=fetch)
        sharpness = 0.0
        edge = 0.0
        extras = list(getattr(capture, "extra_paths", []) or [])
        if capture.success and capture.saved_path is not None:
            # Prefer JPEG among primary+extras for sharpness readout (fastest path)
            candidates = [capture.saved_path, *extras]
            for path in candidates:
                if path.suffix.lower() in {".jpg", ".jpeg"}:
                    try:
                        analysis = analyze_image_bytes(path.read_bytes(), roi=roi, max_side=1280)
                        sharpness = analysis.sharpness
                        edge = analysis.edge_density
                        break
                    except Exception:
                        continue
            if sharpness == 0.0:
                # No JPEG companion (e.g. RAW-only capture): analyze_saved_file
                # extracts the embedded preview via rawpy for raw extensions
                # instead of handing raw bytes to cv2.imdecode, which cannot
                # decode them and previously logged spurious TIFF errors while
                # silently leaving sharpness/edge_density at 0.0 for every frame.
                try:
                    analysis = analyze_saved_file(capture.saved_path, roi=roi, max_side=1280)
                    if analysis is not None:
                        sharpness = analysis.sharpness
                        edge = analysis.edge_density
                except Exception:
                    pass
        return capture.success, capture.saved_path, capture.stderr, sharpness, edge, extras

    def _batch_config(self):
        """self.camera.batch_config() when available, otherwise a no-op --
        keeps hot loops fast on the real camera while staying compatible
        with lightweight test doubles that don't implement batching."""
        batch = getattr(self.camera, "batch_config", None)
        return batch() if callable(batch) else nullcontext()

    def _measure_roi(
        self,
        roi: tuple[float, float, float, float] | None,
    ) -> float:
        preview = self.camera.capture_preview()
        if not preview.success or preview.preview_data is None:
            return -1.0
        return analyze_image_bytes(preview.preview_data, roi=roi, max_side=640).sharpness

    def capture_single(
        self,
        output_dir: Path,
        *,
        options: CaptureOptions | None = None,
    ) -> tuple[bool, Path | None, str, float, float]:
        opts = options or CaptureOptions(settle_ms=0, stillness=False, exposure_lock=False, flash_recycle_ms=0)
        locked = self._prepare_capture(opts)
        stamp = int(time())
        ok, path, err, sharp, edge, _extras = self._save_capture(
            output_dir / f"capture_{stamp}",
            options=opts,
            locked=locked,
        )
        return ok, path, err, sharp, edge

    def capture_basic_stack(
        self,
        output_dir: Path,
        start_position: int,
        end_position: int,
        preset: FocusStepPreset,
        *,
        current_offset: int,
        drive_to_offset: Callable[[int], int],
        on_progress: ProgressCallback | None = None,
        on_coverage: CoverageCallback | None = None,
        options: CaptureOptions | None = None,
    ) -> FocusStackSession:
        opts = options or CaptureOptions()
        plan = self.build_preset_plan(start_position, end_position, preset)
        session = self.build_session(plan, output_dir)
        unit = _unit_step(preset)
        direction = 1 if end_position >= start_position else -1
        # These are the positions actually driven below (start + i*direction*unit),
        # not plan.focus_positions' evenly-spaced/rounded values -- the two only
        # match when (end - start) is an exact multiple of the preset's unit size.
        session.capture_offsets = [
            start_position + index * direction * unit for index in range(plan.step_count)
        ]
        output_dir.mkdir(parents=True, exist_ok=True)
        locked = self._prepare_capture(opts)
        if on_coverage:
            on_coverage({}, list(session.capture_offsets))

        total = len(session.steps)
        stamp = int(time())

        current = drive_to_offset(start_position)
        if on_progress:
            on_progress(0, total, 0.0, f"Moved to start ({current:+d})")

        for step in session.steps:
            if step.index > 1:
                target = current + direction * unit
                current = drive_to_offset(target)
            step.focus_position = current
            dest = output_dir / f"stack_{stamp}_{step.index:04d}"
            ok, path, error, sharpness, edge, extras = self._save_capture(
                dest, options=opts, roi=opts.roi, locked=locked
            )
            step.success = ok
            step.image_path = path
            step.extra_paths = extras
            step.error = error
            step.sharpness = sharpness
            step.edge_density = edge
            if on_progress:
                on_progress(step.index, total, sharpness, f"Image {step.index}/{total}")
        return session

    def _coarse_scan_direction(
        self,
        *,
        direction: str,
        start_offset: int,
        bump_offset: Callable[[str, int], None] | None,
        options: CaptureOptions,
        curve: dict[int, float],
        on_progress: ProgressCallback | None,
        on_scan_sample: ScanSampleCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> int:
        """Walk until hard focus limit / confirmed stall, or max steps."""
        offset = start_offset
        stalls = 0
        step = max(1, options.coarse_step)
        patience = max(2, options.scan_stall_patience)
        min_steps = max(0, options.scan_min_steps)
        stall_delta = max(0.1, options.scan_stall_delta)
        prev_score = curve.get(offset, -1.0)

        with self._batch_config():
            for i in range(options.max_scan_steps):
                if should_cancel and should_cancel():
                    break
                score = self._measure_roi(options.roi)
                if score >= 0:
                    curve[offset] = max(curve.get(offset, 0.0), score)
                    if on_scan_sample:
                        on_scan_sample(dict(curve), offset, score)
                if on_progress and i % 3 == 0:
                    scan_total_hint = min(
                        options.max_scan_steps, max(min_steps, i + patience + 1)
                    )
                    on_progress(i, scan_total_hint, score, f"Scan {direction} @ {offset:+d}")

                before = self.camera.capture_preview()
                before_bytes = before.preview_data if before.success else None
                drive = self.camera.focus_step(direction, step)
                if not drive.success:
                    break
                if bump_offset is not None:
                    bump_offset(direction, step)
                offset += step if direction == "far" else -step
                sleep(max(0.12, options.settle_ms / 2500.0) if options.settle_ms else 0.05)
                after = self.camera.capture_preview()
                frame_delta = 255.0
                if after.success and after.preview_data is not None:
                    frame_delta = preview_frame_delta(before_bytes, after.preview_data)

                after_score = self._measure_roi(options.roi)
                if after_score >= 0:
                    curve[offset] = max(curve.get(offset, 0.0), after_score)
                    if on_scan_sample:
                        on_scan_sample(dict(curve), offset, after_score)
                score_delta = abs(after_score - prev_score) if after_score >= 0 and prev_score >= 0 else 999.0
                if after_score >= 0:
                    prev_score = after_score

                peak = max(curve.values()) if curve else 0.0
                low = after_score >= 0 and (peak <= 0 or after_score <= max(options.stall_floor, peak * 0.12))
                if frame_delta < stall_delta and score_delta < 3.0 and low:
                    stalls += 1
                else:
                    stalls = 0

                if i + 1 >= min_steps and stalls >= patience:
                    break
        return offset

    def plan_adaptive_offsets(
        self,
        *,
        current_offset: int,
        bump_offset: Callable[[str, int], None] | None = None,
        drive_to_offset: Callable[[int], int] | None = None,
        options: CaptureOptions | None = None,
        on_progress: ProgressCallback | None = None,
        on_scan_sample: ScanSampleCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> tuple[list[int], dict[int, float]]:
        """Approach A: coarse full travel → ROI bounds → fine fill → densify."""
        opts = options or CaptureOptions()
        curve: dict[int, float] = {}
        score0 = self._measure_roi(opts.roi)
        if score0 >= 0:
            curve[current_offset] = score0
            if on_scan_sample:
                on_scan_sample(dict(curve), current_offset, score0)

        near_end = self._coarse_scan_direction(
            direction="near",
            start_offset=current_offset,
            bump_offset=bump_offset,
            options=opts,
            curve=curve,
            on_progress=on_progress,
            on_scan_sample=on_scan_sample,
            should_cancel=should_cancel,
        )
        if should_cancel and should_cancel():
            return [], curve
        far_end = self._coarse_scan_direction(
            direction="far",
            start_offset=near_end,
            bump_offset=bump_offset,
            options=opts,
            curve=curve,
            on_progress=on_progress,
            on_scan_sample=on_scan_sample,
            should_cancel=should_cancel,
        )
        _ = far_end
        if should_cancel and should_cancel():
            return [], curve

        near_b, far_b = roi_sharpness_bounds(
            curve,
            threshold=opts.peak_threshold,
            margin=opts.bound_margin,
            step=opts.coarse_step,
        )
        if curve:
            scanned_lo, scanned_hi = min(curve), max(curve)
            span = far_b - near_b
            scanned_span = scanned_hi - scanned_lo
            if scanned_span >= 20 and span < max(12, scanned_span // 4):
                pad = max(opts.bound_margin, scanned_span // 8)
                near_b = max(scanned_lo, near_b - pad)
                far_b = min(scanned_hi, far_b + pad)

        unit = drive_unit_for_step_mm(recommended_step_mm(opts.magnification, opts.aperture))
        filled = fine_fill_offsets(near_b, far_b, unit=unit)
        filled = densify_where_steep(curve, filled)
        ordered = sort_capture_offsets(filled, near_to_far=opts.near_to_far)

        if ordered and drive_to_offset is not None and not (should_cancel and should_cancel()):
            drive_to_offset(ordered[0])
        return ordered, curve

    def capture_adaptive_session(
        self,
        output_dir: Path,
        max_frames: int,
        *,
        current_offset: int = 0,
        on_progress: ProgressCallback | None = None,
        bump_offset: Callable[[str, int], None] | None = None,
        drive_to_offset: Callable[[int], int] | None = None,
        options: CaptureOptions | None = None,
        on_scan_sample: ScanSampleCallback | None = None,
        should_cancel: CancelCheck | None = None,
        await_plan: PlanDecisionCallback | None = None,
    ) -> FocusStackSession:
        """
        Adaptive Approach A: scan → live plan → optional confirm → capture.
        """
        opts = options or CaptureOptions()
        output_dir.mkdir(parents=True, exist_ok=True)
        locked = self._prepare_capture(opts)

        if on_progress:
            on_progress(0, max_frames, 0.0, "Building focus plan (scanning ROI)…")

        offsets, curve = self.plan_adaptive_offsets(
            current_offset=current_offset,
            bump_offset=bump_offset,
            drive_to_offset=drive_to_offset,
            options=opts,
            on_progress=on_progress,
            on_scan_sample=on_scan_sample,
            should_cancel=should_cancel,
        )
        if should_cancel and should_cancel():
            plan = FocusStackPlan(0, 0, 1)
            session = FocusStackSession(plan=plan, output_dir=output_dir, scan_curve=curve)
            session.steps.append(
                CaptureStep(index=1, focus_position=current_offset, success=False, error="Cancelled")
            )
            return session

        if max_frames > 0 and len(offsets) > max_frames:
            if max_frames == 1:
                offsets = [offsets[len(offsets) // 2]]
            else:
                step = (len(offsets) - 1) / (max_frames - 1)
                offsets = [offsets[round(i * step)] for i in range(max_frames)]
                offsets = sort_capture_offsets(offsets, near_to_far=opts.near_to_far)

        if not offsets:
            plan = FocusStackPlan(0, 0, 1)
            session = FocusStackSession(plan=plan, output_dir=output_dir, scan_curve=curve)
            session.steps.append(
                CaptureStep(index=1, focus_position=current_offset, success=False, error="No adaptive offsets")
            )
            return session

        if await_plan is not None:
            if on_progress:
                on_progress(0, len(offsets), 0.0, f"Plan ready: {len(offsets)} frames — confirm to shoot")
            if not await_plan(list(offsets), dict(curve)):
                plan = FocusStackPlan(offsets[0], offsets[-1], len(offsets))
                session = FocusStackSession(
                    plan=plan, output_dir=output_dir, scan_curve=curve, capture_offsets=list(offsets)
                )
                session.steps.append(
                    CaptureStep(index=1, focus_position=current_offset, success=False, error="Plan rejected")
                )
                return session

        plan = FocusStackPlan(
            start_position=offsets[0],
            end_position=offsets[-1],
            step_count=len(offsets),
        )
        session = FocusStackSession(
            plan=plan,
            output_dir=output_dir,
            scan_curve=curve,
            capture_offsets=list(offsets),
        )
        stamp = int(time())
        total = len(offsets)
        mover = drive_to_offset or (lambda target: target)

        for index, target in enumerate(offsets, start=1):
            if should_cancel and should_cancel():
                session.steps.append(
                    CaptureStep(index=index, focus_position=target, success=False, error="Cancelled")
                )
                break
            current = mover(target)
            dest = output_dir / f"adaptive_{stamp}_{index:04d}"
            ok, path, error, sharpness, edge, extras = self._save_capture(
                dest, options=opts, roi=opts.roi, locked=locked
            )
            session.steps.append(
                CaptureStep(
                    index=index,
                    focus_position=current,
                    image_path=path,
                    extra_paths=extras,
                    success=ok,
                    error=error,
                    sharpness=sharpness,
                    edge_density=edge,
                )
            )
            if on_progress:
                on_progress(index, total, sharpness, f"Capture {index}/{total} @ {current:+d}")
        return session

    def capture_focus_path(
        self,
        output_dir: Path,
        offsets: list[int],
        *,
        drive_to_offset: Callable[[int], int],
        on_progress: ProgressCallback | None = None,
        options: CaptureOptions | None = None,
    ) -> FocusStackSession:
        opts = options or CaptureOptions()
        path = smooth_focus_path(offsets)
        if not path:
            plan = FocusStackPlan(0, 0, 1)
            return FocusStackSession(plan=plan, output_dir=output_dir)

        plan = FocusStackPlan(
            start_position=path[0],
            end_position=path[-1],
            step_count=len(path),
        )
        session = FocusStackSession(plan=plan, output_dir=output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        locked = self._prepare_capture(opts)
        stamp = int(time())
        total = len(path)

        for index, target in enumerate(path, start=1):
            current = drive_to_offset(target)
            ok, image_path, error, sharpness, edge, extras = self._save_capture(
                output_dir / f"path_{stamp}_{index:04d}",
                options=opts,
                roi=opts.roi,
                locked=locked,
            )
            session.steps.append(
                CaptureStep(
                    index=index,
                    focus_position=current,
                    image_path=image_path,
                    extra_paths=extras,
                    success=ok,
                    error=error,
                    sharpness=sharpness,
                    edge_density=edge,
                )
            )
            if on_progress:
                on_progress(index, total, sharpness, f"Path {index}/{total} @ {current:+d}")
        return session
