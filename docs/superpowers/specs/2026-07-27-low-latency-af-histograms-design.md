# Low-Latency Remote + AF Box Design

**Date:** 2026-07-27  
**Status:** Approved  
**Goal:** Unfreeze the UI, cut preview latency, add hold-to-focus with end-stop hints, AF rectangle (camera AF when available + software hunt), optional RGB/luma histograms, and a more polished remote UI.

## Decisions

| Topic | Choice |
|-------|--------|
| Scope | Everything in one pass |
| AF rectangle | Camera AF when available, else software contrast hunt |
| Architecture | Single camera worker thread owns all gphoto2 I/O |
| Focus limits | Relative step counter + stall detection (absolute only if exposed) |
| Histograms | Optional RGB + luminance, toggleable |
| UI | Stronger viewfinder remote look (not form dump) |

## Architecture

- `CameraWorker` (QObject on `QThread`) owns `GPhoto2Camera` and serializes all camera ops.
- UI sends commands via queued signals; receives preview frames, status, focus state, stack progress.
- Preview loop runs on the worker; drops stale work by keeping only the latest frame for display.
- Capture, settings, focus, and stacking never run on the UI thread.

## Features

### Hold-to-focus + end-stop
- Click = one step; press-and-hold = repeat at interval until release.
- Track relative focus offset from session zero.
- Detect likely Near/Far limit when consecutive steps produce negligible preview change.
- UI shows offset + Near limit / Far limit / OK; Zero button resets offset.

### AF rectangle
- Drag a rectangle on the live preview (normalized to image coords).
- **Enable AF:** try camera touch/AF-drive widgets if present; otherwise software peak-hunt on ROI sharpness (Laplacian variance).
- Clear/reset box control.

### Histograms
- Toggle for RGB + brightness (luma) histograms computed from preview (full frame or ROI if set).
- Off by default for latency; when on, computed on worker and painted in UI.

### Latency
- Faster preview cadence when live.
- Skip analysis/histograms unless needed.
- Pause live preview only during exclusive ops (full capture / stack / AF hunt) with progress feedback.

### Stacking
- Runs on worker with progress log lines; window stays responsive.

## UI direction

- Dark graphite body, amber LCD readouts, viewfinder-forward preview.
- Focus drive as a clear Near/Far hold pad with size S/M/L.
- Compact focus position strip; optional histogram strip under/beside preview.
- Collapsible focus stack remains secondary.
- Typography: Red Hat Display + Source Code Pro / Noto Sans.

## Out of scope

- True absolute focus encoder unless camera exposes it
- Continuous lens motor streams beyond stepped gphoto2 drive
- Multiprocess camera daemon

## Success criteria

- UI does not freeze during preview, hold-focus, shoot, or stack.
- Hold-to-focus works; end-stop hint appears when travel stalls.
- AF box + Enable AF can refine focus (software path always available).
- Histograms optional and do not wreck responsiveness when off.
- UI reads as a camera remote, not a settings form.
