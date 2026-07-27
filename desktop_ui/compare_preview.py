from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ComparePreview(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._before = QPixmap()
        self._after = QPixmap()
        self._mode = "Before"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image = QLabel("After a stack, first & last frames show here")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(160)
        self.image.setStyleSheet("background:#0a0c10; border:1px solid #2a3038; color:#8f887c;")
        layout.addWidget(self.image)
        row = QHBoxLayout()
        for mode in ("Before", "After", "Overlay"):
            btn = QPushButton(mode)
            btn.setObjectName("ghostButton")
            btn.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            row.addWidget(btn)
        layout.addLayout(row)

    @staticmethod
    def _load(path: str | None) -> QPixmap:
        if not path:
            return QPixmap()
        p = Path(path)
        if not p.exists():
            return QPixmap()
        pix = QPixmap(str(p))
        if not pix.isNull():
            return pix
        # Fallback decode via QImage for odd JPEG flavors
        image = QImage(str(p))
        return QPixmap.fromImage(image) if not image.isNull() else QPixmap()

    def set_images(self, before_path: str | None, after_path: str | None) -> None:
        self._before = self._load(before_path)
        self._after = self._load(after_path)
        self.set_mode("Before" if not self._before.isNull() else "After")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        target = self.image.size()
        if target.width() < 40 or target.height() < 40:
            target = self.image.size().expandedTo(self.image.minimumSize())

        def fit(pix: QPixmap) -> None:
            self.image.setPixmap(
                pix.scaled(
                    max(320, target.width()),
                    max(160, target.height()),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

        if mode == "Before" and not self._before.isNull():
            fit(self._before)
        elif mode == "After" and not self._after.isNull():
            fit(self._after)
        elif mode == "Overlay" and not self._before.isNull() and not self._after.isNull():
            a = self._before.toImage().convertToFormat(QImage.Format.Format_RGB32)
            b = self._after.toImage().convertToFormat(QImage.Format.Format_RGB32)
            if a.size() != b.size():
                b = b.scaled(a.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            out = QImage(a.size(), QImage.Format.Format_RGB32)
            painter = QPainter(out)
            painter.drawImage(0, 0, a)
            painter.setOpacity(0.5)
            painter.drawImage(0, 0, b)
            painter.end()
            fit(QPixmap.fromImage(out))
        else:
            self.image.setText("No JPEG stack frames yet (RAW-only captures won't preview here)")
