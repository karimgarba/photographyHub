# EOS Utility–style Remote Control MVP

**Date:** 2026-07-27  
**Status:** Approved for planning  
**Goal:** Make Photography Hub feel like a bare-minimum EOS Utility remote: live preview, AF stepping, exposure settings, shoot — with focus stacking kept secondary.

## Decisions

| Topic | Choice |
|-------|--------|
| Primary job | EOS Utility–style remote control |
| Stacking | Keep, secondary (same window, de-emphasized) |
| Exposure controls | Show only what the camera exposes via gphoto2; omit missing ones (do not grey out) |
| AF stepping | Near / Far × Small / Medium / Large |
| Layout | Remote-first: big preview + right rail; stacking collapsible at bottom |
| Approach | Rebuild UI shell; extend existing `GPhoto2Camera` (no engine rewrite) |

## Problem

The current UI is a stacked form dump. Preview crashes on connect because `QPixmap.loadFromData` receives a non-`bytes` buffer from gphoto2. There is no AF step UI and no ISO/shutter/aperture/WB controls.

## Layout & UX

```
┌──────────────────────────────────────────────────────────────┐
│ Photography Hub                          Camera: <status>    │
├────────────────────────────────────┬─────────────────────────┤
│                                    │ Connect / camera pick   │
│         LIVE PREVIEW               │ Exposure (ISO, shutter, │
│         (dominant)                 │  aperture, WB — if any) │
│                                    │ AF Near/Far S/M/L       │
│                                    │ Shoot Once              │
│                                    │ Output folder           │
├────────────────────────────────────┴─────────────────────────┤
│ ▾ Focus Stack (collapsible) — existing basic/adaptive flow   │
├──────────────────────────────────────────────────────────────┤
│ Status / log strip                                           │
└──────────────────────────────────────────────────────────────┘
```

- Live preview dominates left/center.
- Right rail: connection, dynamic exposure combos, AF controls, shoot, output folder.
- Bottom: collapsible Focus Stack panel (existing Basic/Adaptive behavior, restyled only as needed).
- Thin status/log strip — not the main UI.

## Camera engine

Extend `camera_engine/camera.py` (keep `GPhoto2Camera`):

1. **Preview bytes** — Always coerce preview payload to `bytes` before handing to Qt (`QPixmap.loadFromData`).
2. **Config introspection** — Helpers to list choices and get/set common widgets by known name aliases:
   - ISO: `iso`
   - Shutter: `shutterspeed`, `shutterSpeed`
   - Aperture: `aperture`, `f-number`, `fnumber`
   - White balance: `whitebalance`, `whiteBalance`
   - If a widget is missing, do not create a UI control for it.
3. **AF drive** — Reuse `focus_manual_drive`; UI sends Near1–3 / Far1–3 (or mapped equivalents from camera choices).
4. **Connect flow** — On successful connect: start preview timer, query available settings, populate only present controls.

Errors are non-fatal: failed setting/AF/preview appends a log line and leaves the rest of the UI usable.

## UI structure

Split the current monolithic `desktop_ui/main_window.py` into focused pieces as needed:

- Window shell / layout
- Preview pane (timer refresh when connected)
- Settings + AF rail (dynamic combos)
- Stacking panel (existing logic, secondary placement)

### Behavior rules

- Preview refreshes on a timer while connected.
- Pause preview while capturing or stacking so gphoto2 is not contended.
- Changing an exposure combo writes immediately to the camera, then re-reads the actual value into the combo.
- Stacking remains today’s Basic/Adaptive flow under a collapsible “Focus Stack” section.

## Out of scope

- Continuous hold-to-move AF
- Greying out unsupported settings (omit instead)
- Full engine rewrite / plugin architecture
- New stacking algorithms

## Manual test plan

1. Launch app with camera connected — no crash; live preview appears.
2. Step AF Near/Far at Small/Medium/Large — preview updates.
3. Change each available exposure setting — camera accepts; UI shows actual value.
4. Shoot once — file lands in output folder.
5. Run a basic stack from the secondary panel — still works.
6. Disconnect / no camera — UI stays usable; clear disconnected status.

## Success criteria

- App starts and shows live preview without `loadFromData` crash.
- User can step focus and change basic exposure from the UI.
- Shoot once works.
- Stacking still available but clearly secondary.
- UI reads as a camera remote, not a form dump.
