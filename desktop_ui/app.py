from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as error:
        raise SystemExit(
            "PySide6 is not installed in the active Python interpreter. "
            "Install project dependencies before launching the desktop UI."
        ) from error

    from desktop_ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
