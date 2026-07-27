APP_STYLESHEET = """
QMainWindow, QWidget#centralRoot {
    background-color: #101218;
    color: #e8e2d6;
    font-family: "Noto Sans";
    font-size: 13px;
}

QLabel#brandTitle {
    font-family: "Red Hat Display";
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 0.5px;
    color: #f7f1e6;
}

QLabel#brandSub {
    font-family: "Source Code Pro", "Adwaita Mono", monospace;
    font-size: 10px;
    color: #8f887c;
    letter-spacing: 2px;
}

QLabel#statusChip {
    font-family: "Source Code Pro", monospace;
    font-size: 11px;
    font-weight: 700;
    color: #1a1408;
    background-color: #f0b429;
    padding: 8px 14px;
    border-radius: 1px;
}

QLabel#statusChip[connected="false"] {
    background-color: #4a453c;
    color: #d2cbbd;
}

QLabel#lcdReadout {
    font-family: "Source Code Pro", monospace;
    font-size: 13px;
    color: #f0b429;
    background-color: #0a0c10;
    border: 1px solid #2a3038;
    padding: 8px 10px;
}

QLabel#lcdMuted {
    font-family: "Source Code Pro", monospace;
    font-size: 11px;
    color: #8f887c;
}

QLabel#railSection {
    font-family: "Red Hat Display";
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 2px;
    color: #7d776c;
    padding-top: 6px;
}

QFrame#railPanel {
    background-color: #161a22;
    border-left: 1px solid #2a303a;
}

QFrame#viewfinderWell {
    background-color: #0a0c10;
    border: 1px solid #242a34;
}

QWidget#previewCanvas {
    background-color: #07080a;
}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #0a0c10;
    border: 1px solid #343b48;
    border-radius: 1px;
    padding: 7px 8px;
    color: #f3eee4;
    font-family: "Source Code Pro", monospace;
    min-height: 18px;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox QAbstractItemView {
    background-color: #12151c;
    color: #f3eee4;
    selection-background-color: #f0b429;
    selection-color: #1a1408;
}

QPushButton {
    background-color: #232833;
    color: #f3eee4;
    border: 1px solid #3a4250;
    border-radius: 1px;
    padding: 9px 12px;
    font-family: "Red Hat Display";
    font-weight: 700;
}

QPushButton:hover {
    background-color: #2c3340;
    border-color: #4a5364;
}

QPushButton:pressed {
    background-color: #1a1e28;
}

QPushButton:disabled {
    color: #625d55;
    background-color: #151820;
    border-color: #2a303a;
}

QPushButton#shootButton {
    background-color: #d4531c;
    border: 1px solid #f06a2e;
    color: #fff7f0;
    font-size: 16px;
    letter-spacing: 1px;
    padding: 16px 12px;
}

QPushButton#shootButton:hover {
    background-color: #e45e24;
}

QPushButton#afButton {
    min-width: 54px;
    min-height: 40px;
    font-family: "Source Code Pro", monospace;
    font-size: 14px;
    background-color: #1a1f2a;
}

QPushButton#afButton:pressed {
    background-color: #3ecfcf;
    color: #061214;
    border-color: #5ee0e0;
}

QPushButton#primaryButton {
    background-color: #f0b429;
    color: #1a1408;
    border-color: #ffcc4d;
}

QPushButton#primaryButton:hover {
    background-color: #ffc33a;
}

QPushButton#ghostButton {
    background: transparent;
    border: 1px solid #3a4250;
    color: #cfc7b8;
}

QPushButton#accentAf {
    background-color: #123338;
    border: 1px solid #3ecfcf;
    color: #9ff0f0;
}

QPushButton#accentAf:hover {
    background-color: #184248;
}

QToolButton#stackToggle {
    background: transparent;
    border: none;
    color: #f0b429;
    font-family: "Red Hat Display";
    font-weight: 800;
    font-size: 11px;
    letter-spacing: 2px;
    text-align: left;
    padding: 10px 0;
}

QCheckBox {
    color: #cfc7b8;
    font-family: "Noto Sans";
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #3a4250;
    background: #0a0c10;
}

QCheckBox::indicator:checked {
    background: #f0b429;
    border-color: #ffcc4d;
}

QPlainTextEdit#logView {
    background-color: #090b0f;
    border-top: 1px solid #242a34;
    color: #8f887c;
    font-family: "Source Code Pro", monospace;
    font-size: 11px;
}

QTabWidget#railTabs::pane {
    border: none;
    background: transparent;
}

QTabWidget#railTabs QTabBar::tab {
    background: #12151c;
    color: #8f887c;
    border: 1px solid #2a3038;
    padding: 8px 16px;
    font-family: "Red Hat Display";
    font-weight: 800;
    font-size: 11px;
    letter-spacing: 1px;
    margin-right: 4px;
}

QTabWidget#railTabs QTabBar::tab:selected {
    background: #1a1f2a;
    color: #f0b429;
    border-bottom-color: #1a1f2a;
}

QFrame#infoCard {
    background-color: #12151c;
    border: 1px solid #2a3038;
    border-radius: 2px;
}

QLabel#infoTitle {
    font-family: "Red Hat Display";
    font-size: 14px;
    font-weight: 800;
    color: #f7f1e6;
}

QProgressBar {
    background-color: #0a0c10;
    border: 1px solid #2a3038;
    border-radius: 1px;
    text-align: center;
    color: #f0b429;
    font-family: "Source Code Pro", monospace;
    min-height: 18px;
}

QProgressBar::chunk {
    background-color: #3ecfcf;
}

QSlider::groove:horizontal {
    height: 6px;
    background: #0a0c10;
    border: 1px solid #2a3038;
}

QSlider::handle:horizontal {
    background: #f0b429;
    width: 14px;
    margin: -6px 0;
    border-radius: 1px;
}

QSlider::sub-page:horizontal {
    background: #3ecfcf;
}
"""
