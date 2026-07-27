from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class SharpnessGraph(QWidget):
    """Live sharpness curve with optional capture-plan markers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.setMaximumHeight(140)
        self._values: list[float] = []
        self._curve: dict[int, float] = {}
        self._marks: list[int] = []
        self._title = "Focus plan"

    def clear(self) -> None:
        self._values.clear()
        self._curve.clear()
        self._marks.clear()
        self._title = "Focus plan"
        self.update()

    def add_value(self, value: float) -> None:
        self._values.append(float(value))
        if len(self._values) > 240:
            self._values = self._values[-240:]
        self.update()

    def set_values(self, values: list[float]) -> None:
        self._values = [float(v) for v in values]
        self.update()

    def set_scan_curve(self, curve: dict[int, float], *, title: str | None = None) -> None:
        self._curve = {int(k): float(v) for k, v in curve.items()}
        if title:
            self._title = title
        self.update()

    def set_capture_marks(self, offsets: list[int]) -> None:
        self._marks = [int(o) for o in offsets]
        self.update()

    def set_plan(self, curve: dict[int, float], offsets: list[int], *, title: str = "Focus plan") -> None:
        self._curve = {int(k): float(v) for k, v in curve.items()}
        self._marks = [int(o) for o in offsets]
        self._title = title
        self._values.clear()
        self.update()

    def has_curve(self) -> bool:
        return bool(self._curve)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0a0c10"))
        painter.setPen(QPen(QColor("#2a3038"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QColor("#8f887c"))
        painter.drawText(8, 16, self._title)

        left, top, right, bottom = 10, 22, self.width() - 10, self.height() - 18
        width = max(1, right - left)
        height = max(1, bottom - top)

        if self._curve:
            keys = sorted(self._curve)
            vals = [self._curve[k] for k in keys]
            peak = max(vals) or 1.0
            lo, hi = keys[0], keys[-1]
            span = max(1, hi - lo)

            # Capture band fill
            if len(self._marks) >= 2:
                mlo, mhi = min(self._marks), max(self._marks)
                x0 = left + int((mlo - lo) / span * width)
                x1 = left + int((mhi - lo) / span * width)
                painter.fillRect(x0, top, max(2, x1 - x0), height, QColor(240, 180, 41, 35))

            painter.setPen(QPen(QColor("#3ecfcf"), 2))
            points = []
            for key, value in zip(keys, vals):
                x = left + int((key - lo) / span * width)
                y = bottom - int((value / peak) * height)
                points.append((x, y))
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                painter.drawLine(x0, y0, x1, y1)

            # Capture ticks
            painter.setPen(QPen(QColor("#f0b429"), 1))
            for mark in self._marks:
                x = left + int((mark - lo) / span * width)
                painter.drawLine(x, bottom, x, bottom - 6)

            painter.setPen(QColor("#f0b429"))
            painter.drawText(left, bottom + 14, f"{lo:+d}")
            painter.drawText(right - 36, bottom + 14, f"{hi:+d}")
            if self._marks:
                painter.drawText(
                    left + width // 2 - 40,
                    bottom + 14,
                    f"{len(self._marks)} shots",
                )
            return

        if len(self._values) < 2:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Scanning…")
            return

        peak = max(self._values) or 1.0
        painter.setPen(QPen(QColor("#3ecfcf"), 2))
        points = []
        for index, value in enumerate(self._values):
            x = left + int(index * (width / max(1, len(self._values) - 1)))
            y = bottom - int((value / peak) * height)
            points.append((x, y))
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            painter.drawLine(x0, y0, x1, y1)
        painter.setPen(QColor("#f0b429"))
        painter.drawText(left, bottom + 14, "1")
        painter.drawText(right - 24, bottom + 14, str(len(self._values)))
