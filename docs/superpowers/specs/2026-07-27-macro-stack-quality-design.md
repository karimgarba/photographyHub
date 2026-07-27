# Macro Stack Quality Pass — Design

**Date:** 2026-07-27  
**Status:** Implemented  
**Goal:** Best-output focus stacks for macro jewelry / flowers: real RAW files, stable captures, Adaptive that auto-discovers subject depth via the AF box, and easy handoff to a merger.

## Decisions (best-output)

| Topic | Choice | Why |
|-------|--------|-----|
| Adaptive scan | **Approach A** — coarse full travel, then fine fill in ROI peak band | Most complete depth coverage; slower but fewer soft gaps |
| Settle delay | **800 ms** default (UI-adjustable) | Macro rigs need time after focus drive before shutter |
| Stillness gate | On by default | Skip shutter while live view is still settling/vibrating |
| Capture format | Prefer **RAW+JPEG** when camera offers it; else camera’s RAW | RAW for merge quality; JPEG for in-app preview |
| File naming | Keep camera extension (`.CR3`, `.NEF`, `.ARW`, `.JPG`, …) | No more fake `.jpg` on RAW |
| Adaptive direction | User chooses Closest→Furthest or Furthest→Closest | Same positions, shoot order only |
| Merge | Folder open + Helicon/Zerene CLI copy | No in-app merger (scope) |

## Adaptive Approach A (detail)

1. Require AF box on the subject (ROI). Full-frame sharpness is ignored for bounds.
2. **Coarse scan** from current offset: walk Near until focus limit / stall, record ROI sharpness vs offset; walk Far the same way (or continue from near-limit across travel). Build a sharpness-vs-offset curve.
3. **Bounds:** near_bound / far_bound = outermost offsets where ROI sharpness ≥ `peak * threshold` (default 0.35). If bounds collapse, fall back to ±N steps around peak and log a warning.
4. **Fine fill:** inside [near_bound, far_bound], place capture offsets using DOF-derived unit step (from mag + aperture), clamped to Tiny–Large focus-drive sizes. Densify where the coarse curve was steep if needed.
5. **Sort** offsets Near→Far or Far→Near per UI.
6. **Capture:** for each offset → drive → settle (800 ms) → optional stillness wait → shutter → download with real extension.

Basic mode unchanged except it also uses settle / stillness / RAW path.

## Capture pipeline changes

- Add settings aliases: `imageformat` / `imagequality`; optional `shuttermode` / electronic shutter / MLU widgets (omit UI if missing).
- On stack start: if RAW+JPEG choice exists and current isn’t already it, offer set (or auto-set when “Prefer RAW+JPEG” checked).
- `capture_image` saves as `stem + real_suffix` from camera filename.
- Stack preview uses first/last **JPEG** sibling when present.

## Tripod-safe options (Stack tab)

- Settle ms (default 800)
- Stillness checkbox (default on): max preview delta threshold before shoot
- Electronic shutter / MLU combos if exposed

## DOF → Basic plan

- Mag + aperture already estimated.
- **Apply DOF plan** sets step preset from recommended mm and updates estimated shot count for current Start/End. Does not invent marks — user still Sets Start/End (or Adaptive finds them).

## Merge handoff

- After successful stack: buttons **Open folder**, **Copy Helicon cmd**, **Copy Zerene cmd** (paths joined for typical CLI). No process spawn required.

## Success criteria

- Stack files keep correct RAW extensions; JPEG preview works when RAW+JPEG.
- Adaptive with box on a ring/flower produces a sorted offset list spanning subject depth, then captures it.
- Settle + stillness reduce soft frames vs immediate shutter.
- User can open folder / paste merger command without hunting paths.

## Out of scope

- Built-in focus merge / Helicon API
- Motorized focus rail
- Camera firmware focus-bracketing menus

## Spec self-review

- No placeholders left.
- Approach A vs older Adaptive “walk Far until plateau” — replaced; UI copy must match.
- Adaptive needs focus drive + ROI; fail clearly if missing.
- Stillness depends on live preview; if preview fails, proceed after settle only.
