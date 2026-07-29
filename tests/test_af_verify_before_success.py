"""Regression tests for the AF "loops forever / keeps racking" bug.

Old behavior: try_camera_autofocus() reported success=True the instant
gPhoto2 *accepted* the Press Half command, never checking whether the lens
actually achieved focus, and never sending Release Half. On a body like the
4000D, Live View contrast AF runs asynchronously and takes real time to
settle -- so the app would say "AF ok" before focus was actually achieved,
and a follow-up Box AF click would interrupt the in-flight hunt and restart
it, producing an endless refocus loop that never converges.

New behavior: after the drive command is accepted, wait for it to settle,
then verify sharpness actually held/improved before declaring success (and
always send Release Half to close the press cycle). If sharpness dropped,
report None so the caller falls back to software_contrast_hunt.
"""
from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np

from camera_engine.autofocus import try_camera_autofocus


def _jpeg_of_sharpness(level: str) -> bytes:
    """'sharp' -> lots of high-frequency edge content; 'blurry' -> a flat blur."""
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    if level == "sharp":
        # checkerboard -> high local variance / edge density
        for y in range(0, 64, 4):
            for x in range(0, 64, 4):
                if (x // 4 + y // 4) % 2 == 0:
                    img[y:y + 4, x:x + 4] = 255
    else:
        img[:] = 120  # flat/blurry -- effectively zero edge content
    ok, encoded = cv2.imencode(".jpg", img)
    assert ok
    return encoded.tobytes()


class FakeCamera:
    def __init__(self, preview_sequence):
        # preview_sequence: list of "sharp"/"blurry" strings returned on
        # successive capture_preview() calls (before-measure, after-measure, ...)
        self._sequence = list(preview_sequence)
        self.calls: list[tuple[str, object]] = []

    def set_widget_choice(self, names, value):
        self.calls.append((names[0], value))
        if names[0] == "autofocusdrive":
            return SimpleNamespace(success=True)
        if names[0] == "eosremoterelease" and value in ("Press Half", "Release Half"):
            return SimpleNamespace(success=True)
        return SimpleNamespace(success=False)

    def capture_preview(self):
        level = self._sequence.pop(0) if self._sequence else "sharp"
        return SimpleNamespace(success=True, preview_data=_jpeg_of_sharpness(level))


def test_af_reports_success_when_sharpness_holds_after_settling():
    cam = FakeCamera(preview_sequence=["sharp", "sharp"])  # before, after
    result = try_camera_autofocus(cam, None, settle_seconds=0)
    assert result is not None
    assert result.success


def test_af_falls_back_when_sharpness_drops_after_settling():
    """This is the core fix: if focus visibly got WORSE after the settle
    period, don't report a false success -- return None so the caller uses
    software_contrast_hunt instead."""
    cam = FakeCamera(preview_sequence=["sharp", "blurry"])  # before sharp, after blurry
    result = try_camera_autofocus(cam, None, settle_seconds=0)
    assert result is None


def test_af_sends_release_half_to_close_the_press_cycle():
    """Regardless of outcome, a successful Press Half must be paired with a
    Release Half -- leaving the camera in a held half-press state was
    contributing to the AF confusion/loop."""
    cam = FakeCamera(preview_sequence=["sharp", "sharp"])
    # Force the code down the eosremoterelease branch by making
    # autofocusdrive values fail.
    original_set = cam.set_widget_choice

    def gated(names, value):
        if names[0] == "autofocusdrive":
            return SimpleNamespace(success=False)
        return original_set(names, value)

    cam.set_widget_choice = gated
    try_camera_autofocus(cam, None, settle_seconds=0)
    assert ("eosremoterelease", "Press Half") in cam.calls
    assert ("eosremoterelease", "Release Half") in cam.calls
