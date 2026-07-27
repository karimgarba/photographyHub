from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from camera_engine.analysis import HistogramData


class PreviewCanvas(QWidget):
    """Live viewfinder with an EOS-style persistent AF rectangle."""

    roi_changed = Signal(object)  # tuple[float,float,float,float]
    click_af = Signal(object)  # ROI tuple for click AF

    # Normalized box size (EOS-like fixed AF frame)
    BOX_SIZE = 0.10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("previewCanvas")
        self.setMinimumSize(640, 420)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pixmap = QPixmap()
        self._draw_rect = QRect()
        # Always-on AF box, centered by default
        self._roi: tuple[float, float, float, float] = self._centered_roi()
        self._dragging_box = False
        self._drag_grab_offset: QPoint | None = None
        self._press_point: QPoint | None = None
        self._moved_during_press = False
        self._histogram: HistogramData | None = None
        self._show_histogram = False
        self._placeholder = "No camera connected"
        self._af_flash = 0

    @classmethod
    def _centered_roi(cls) -> tuple[float, float, float, float]:
        size = cls.BOX_SIZE
        return ((1.0 - size) / 2.0, (1.0 - size) / 2.0, size, size)

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if self._pixmap.isNull():
            self.update()

    def set_frame(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        if self._af_flash > 0:
            self._af_flash -= 1
        self.update()

    def set_histogram(self, histogram: HistogramData | None) -> None:
        self._histogram = histogram
        self.update()

    def set_show_histogram(self, show: bool) -> None:
        self._show_histogram = show
        self.update()

    def clear_roi(self) -> None:
        """Reset AF box to center (stays visible)."""
        self._roi = self._centered_roi()
        self.roi_changed.emit(self._roi)
        self.update()

    def center_roi(self) -> None:
        self.clear_roi()

    def set_roi(self, roi: tuple[float, float, float, float] | None) -> None:
        if roi is None:
            self._roi = self._centered_roi()
        else:
            self._roi = self._clamp_roi(roi[0] + roi[2] / 2.0, roi[1] + roi[3] / 2.0)
        self.roi_changed.emit(self._roi)
        self.update()

    def roi(self) -> tuple[float, float, float, float]:
        return self._roi

    def _clamp_roi(self, cx: float, cy: float) -> tuple[float, float, float, float]:
        size = self.BOX_SIZE
        half = size / 2.0
        x = max(0.0, min(1.0 - size, cx - half))
        y = max(0.0, min(1.0 - size, cy - half))
        return (x, y, size, size)

    def _image_rect(self) -> QRect:
        if self._pixmap.isNull():
            return QRect()
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        self._draw_rect = QRect(x, y, scaled.width(), scaled.height())
        return self._draw_rect

    def _widget_from_norm(self, roi: tuple[float, float, float, float]) -> QRect:
        image = self._image_rect()
        if image.isEmpty():
            return QRect()
        x, y, w, h = roi
        return QRect(
            int(image.x() + x * image.width()),
            int(image.y() + y * image.height()),
            max(1, int(w * image.width())),
            max(1, int(h * image.height())),
        )

    def _norm_center_from_point(self, point: QPoint) -> tuple[float, float] | None:
        image = self._image_rect()
        if image.isEmpty() or not image.contains(point):
            return None
        cx = (point.x() - image.x()) / image.width()
        cy = (point.y() - image.y()) / image.height()
        return (cx, cy)

    def _move_box_to_point(self, point: QPoint) -> bool:
        center = self._norm_center_from_point(point)
        if center is None:
            return False
        self._roi = self._clamp_roi(center[0], center[1])
        self.roi_changed.emit(self._roi)
        return True

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#07080a"))
        painter.setPen(QPen(QColor("#2c323c"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QPen(QColor("#151820"), 6))
        painter.drawRect(self.rect().adjusted(3, 3, -4, -4))

        if self._pixmap.isNull():
            painter.setPen(QColor("#6a655c"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return

        image = self._image_rect()
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(image.topLeft(), scaled)

        # Viewfinder corner ticks
        painter.setPen(QPen(QColor("#f0b429"), 1))
        tick = 18
        for corner in (
            (image.left(), image.top(), 1, 1),
            (image.right(), image.top(), -1, 1),
            (image.left(), image.bottom(), 1, -1),
            (image.right(), image.bottom(), -1, -1),
        ):
            x, y, dx, dy = corner
            painter.drawLine(x, y, x + dx * tick, y)
            painter.drawLine(x, y, x, y + dy * tick)

        # Persistent EOS-style AF rectangle
        self._paint_af_box(painter, self._widget_from_norm(self._roi))

        if self._show_histogram and self._histogram is not None:
            self._paint_histogram(painter, image)

    def _paint_af_box(self, painter: QPainter, rect: QRect) -> None:
        if rect.isEmpty():
            return
        # Brief confirm flash after click-AF (EOS-ish)
        active = self._af_flash > 0
        color = QColor("#5ee8e8") if active else QColor("#f2f2f0")
        pen = QPen(color, 2 if active else 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Outer hollow rectangle
        painter.drawRect(rect)

        # Corner brackets (classic EOS AF frame)
        arm = max(6, min(rect.width(), rect.height()) // 4)
        corners = (
            (rect.left(), rect.top(), 1, 1),
            (rect.right(), rect.top(), -1, 1),
            (rect.left(), rect.bottom(), 1, -1),
            (rect.right(), rect.bottom(), -1, -1),
        )
        thick = QPen(color, 2)
        painter.setPen(thick)
        for x, y, dx, dy in corners:
            painter.drawLine(x, y, x + dx * arm, y)
            painter.drawLine(x, y, x, y + dy * arm)

        # Center crosshair
        painter.setPen(QPen(color, 1))
        cx, cy = rect.center().x(), rect.center().y()
        painter.drawLine(cx - 4, cy, cx + 4, cy)
        painter.drawLine(cx, cy - 4, cx, cy + 4)

    def _paint_histogram(self, painter: QPainter, image: QRect) -> None:
        box = QRect(image.left() + 12, image.bottom() - 92, min(220, image.width() - 24), 80)
        painter.fillRect(box, QColor(8, 10, 12, 180))
        painter.setPen(QPen(QColor("#3a4048"), 1))
        painter.drawRect(box)

        def draw_channel(values: list[int], color: QColor, alpha: int = 160) -> None:
            if not values:
                return
            peak = max(values) or 1
            path_w = box.width() - 4
            path_h = box.height() - 4
            c = QColor(color)
            c.setAlpha(alpha)
            for i, value in enumerate(values):
                x = box.left() + 2 + int(i * path_w / len(values))
                h = int((value / peak) * path_h)
                painter.fillRect(x, box.bottom() - 2 - h, max(1, path_w // len(values)), h, c)

        draw_channel(self._histogram.luma, QColor("#d8d2c4"), 90)
        draw_channel(self._histogram.red, QColor("#e85d5d"), 140)
        draw_channel(self._histogram.green, QColor("#5dce7a"), 140)
        draw_channel(self._histogram.blue, QColor("#5d9dde"), 140)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap.isNull():
            return
        point = event.position().toPoint()
        self._press_point = point
        self._moved_during_press = False
        box = self._widget_from_norm(self._roi)
        if box.contains(point):
            # Possible drag of AF box; short click will AF on release
            self._dragging_box = True
            self._drag_grab_offset = point - box.center()
        else:
            # Click elsewhere: move persistent box immediately; AF on release
            if self._move_box_to_point(point):
                self._af_flash = 8
                self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_point is not None:
            delta = event.position().toPoint() - self._press_point
            if abs(delta.x()) + abs(delta.y()) > 6:
                self._moved_during_press = True
        if self._dragging_box:
            point = event.position().toPoint()
            if self._drag_grab_offset is not None:
                point = point - self._drag_grab_offset
            if self._move_box_to_point(point):
                self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was_dragging = self._dragging_box
        moved = self._moved_during_press
        self._dragging_box = False
        self._drag_grab_offset = None
        self._press_point = None
        self._moved_during_press = False
        # Every click (tap) runs AF; pure drag relocates without hunting
        if not moved or not was_dragging:
            self._af_flash = 10
            self.click_af.emit(self._roi)
        self.update()
