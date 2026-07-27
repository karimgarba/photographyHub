"""Regression test for the Confirm/Cancel-does-nothing deadlock.

Bug: while an adaptive stack is running, CameraWorker.run_stack() executes
synchronously on the worker QThread and blocks inside _await_plan()'s
threading.Event busy-wait. cmd_resolve_plan / cmd_cancel were connected with
Qt's default (queued, cross-thread) connection, so clicking Confirm/Cancel
could never be dispatched to the worker thread's event loop until run_stack
returned -- which it never does, because it's waiting on that exact click.

Fix: connect cmd_cancel/cmd_resolve_plan with Qt.ConnectionType.DirectConnection,
since the target slots only touch a threading.Event + plain bools (thread-safe).

This test reproduces the real hang using the actual worker's _await_plan
method (not a re-implementation), run on a real QThread, and asserts it
resolves promptly rather than timing out.
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QThread, QTimer, Qt, Signal, QObject

from desktop_ui.camera_worker import CameraWorker


class _FakeCamera:
    def is_connected(self) -> bool:
        return True


def _make_worker() -> CameraWorker:
    worker = CameraWorker()
    worker.camera = _FakeCamera()
    return worker


def _drive_confirm_through(connection_type, timeout_s: float = 3.0):
    """Start _await_plan on a real worker thread, then fire a 'Confirm Capture'
    click via a signal connected with `connection_type`. Returns (resolved, accepted, elapsed).
    """
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    thread = QThread()
    worker = _make_worker()
    worker.moveToThread(thread)

    class Bridge(QObject):
        start_await = Signal(list, dict)
        confirm = Signal(bool)

    bridge = Bridge()
    bridge.start_await.connect(worker._await_plan)  # queued: runs on worker thread, like run_stack does
    bridge.confirm.connect(worker.resolve_stack_plan, connection_type)

    result = {"resolved": False, "accepted": None}

    def capture_result(accepted_or_none):
        # _await_plan returns a bool but Slot-less plain methods invoked via
        # queued connection don't propagate return values across threads,
        # so we poll worker state instead (mirrors how the real app observes
        # completion via the stack_done/stack_progress signals).
        pass

    thread.start()
    bridge.start_await.emit([-2, 0, 2], {})

    # Give _await_plan a moment to actually enter its wait loop, then "click Confirm".
    QTimer.singleShot(150, lambda: bridge.confirm.emit(True))

    start = time.time()

    def poll():
        elapsed = time.time() - start
        if not worker._awaiting_plan:
            result["resolved"] = True
            result["accepted"] = worker._plan_accepted
            result["elapsed"] = elapsed
            thread.quit()
        elif elapsed > timeout_s:
            result["resolved"] = False
            result["elapsed"] = elapsed
            thread.quit()

    poll_timer = QTimer()
    poll_timer.timeout.connect(poll)
    poll_timer.start(20)

    thread.finished.connect(app.quit)
    QTimer.singleShot(int((timeout_s + 1) * 1000), app.quit)
    app.exec()
    poll_timer.stop()

    if not result.get("resolved"):
        # The worker thread is still stuck inside _await_plan()'s busy-wait
        # (this is the bug being demonstrated). Force it loose directly --
        # request_cancel() only flips a bool + sets a threading.Event, so
        # it's safe to call straight from this thread -- purely so the test
        # doesn't leak a running QThread. This cleanup step is not part of
        # what's being measured above.
        worker.request_cancel()
        thread.quit()
    thread.wait(2000)
    return result


def test_queued_connection_deadlocks_like_the_original_bug():
    """Documents the bug: with the original (queued) connection, Confirm
    click is never delivered while the worker thread is busy-waiting."""
    result = _drive_confirm_through(Qt.ConnectionType.QueuedConnection, timeout_s=1.5)
    assert result["resolved"] is False, (
        "Expected the queued-connection version to hang (reproducing the bug); "
        "if this now resolves, Qt's behavior around blocked event loops may have changed."
    )


def test_direct_connection_resolves_the_plan_promptly():
    """Verifies the fix: DirectConnection lets Confirm reach the worker
    immediately even while it's inside the busy-wait."""
    result = _drive_confirm_through(Qt.ConnectionType.DirectConnection, timeout_s=3.0)
    assert result["resolved"] is True, "Confirm click did not resolve the plan (deadlock not fixed)"
    assert result["accepted"] is True
    assert result["elapsed"] < 1.0, f"Resolved too slowly ({result['elapsed']:.2f}s) -- check the wiring"
