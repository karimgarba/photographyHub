from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMetaObject, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from camera_engine.analysis import PreviewMetrics
from camera_engine.camera import CameraInfo, CameraSetting, SETTING_LABELS
from camera_engine.dof import estimate_dof, estimate_duration_seconds, format_duration, parse_aperture, plan_shot_count
from camera_engine.stacking import FocusStepPreset, preset_for_step_mm
from camera_engine.workflow import FocusStackController, merger_command
from desktop_ui.camera_worker import CameraWorker
from desktop_ui.compare_preview import ComparePreview
from desktop_ui.preview_canvas import PreviewCanvas
from desktop_ui.sharpness_graph import SharpnessGraph
from desktop_ui.styles import APP_STYLESHEET


class MainWindow(QMainWindow):
    cmd_refresh_cameras = Signal()
    cmd_connect = Signal(int)
    cmd_set_live = Signal(bool)
    cmd_set_histograms = Signal(bool)
    cmd_set_roi = Signal(object)
    cmd_zero_focus = Signal()
    cmd_focus_once = Signal(str, int)
    cmd_start_hold = Signal(str, int)
    cmd_stop_hold = Signal()
    cmd_seek_focus = Signal(int)
    cmd_set_setting = Signal(str, str)
    cmd_shoot = Signal(str)
    cmd_run_af = Signal(object)
    cmd_run_stack = Signal(str, int, int, str, str, int)
    cmd_set_recording = Signal(bool)
    cmd_clear_path = Signal()
    cmd_replay_path = Signal(str)
    cmd_set_capture_options = Signal(object)
    cmd_cancel = Signal()
    cmd_resolve_plan = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Photography Hub")
        self.resize(1480, 920)
        self.setStyleSheet(APP_STYLESHEET)

        self.output_dir = Path("sample_data") / "output"
        self._setting_widgets: dict[str, QComboBox] = {}
        self._updating_settings = False
        self._busy = False
        self._roi: tuple[float, float, float, float] | None = None
        self._focus_offset = 0
        self._stack_start = 0
        self._stack_end = 40
        self._updating_slider = False
        self._path_points: list[int] = []
        self._last_stack_paths: list[str] = []

        self._thread = QThread(self)
        self.worker = CameraWorker()
        self.worker.moveToThread(self._thread)

        self.cmd_refresh_cameras.connect(self.worker.refresh_cameras)
        self.cmd_connect.connect(self.worker.connect_camera)
        self.cmd_set_live.connect(self.worker.set_live_preview)
        self.cmd_set_histograms.connect(self.worker.set_histograms_enabled)
        self.cmd_set_roi.connect(self.worker.set_roi)
        self.cmd_zero_focus.connect(self.worker.zero_focus)
        self.cmd_focus_once.connect(self.worker.focus_once)
        self.cmd_start_hold.connect(self.worker.start_hold)
        self.cmd_stop_hold.connect(self.worker.stop_hold)
        self.cmd_seek_focus.connect(self.worker.seek_focus)
        self.cmd_set_setting.connect(self.worker.set_setting)
        self.cmd_shoot.connect(self.worker.shoot)
        self.cmd_run_af.connect(self.worker.run_af)
        self.cmd_run_stack.connect(self.worker.run_stack)
        self.cmd_set_recording.connect(self.worker.set_recording_path)
        self.cmd_clear_path.connect(self.worker.clear_focus_path)
        self.cmd_replay_path.connect(self.worker.replay_focus_path)
        self.cmd_set_capture_options.connect(self.worker.set_capture_options)
        self.cmd_cancel.connect(self.worker.request_cancel)
        self.cmd_resolve_plan.connect(self.worker.resolve_stack_plan)

        self.worker.log.connect(self._log)
        self.worker.connected.connect(self._on_connected)
        self.worker.camera_info.connect(self._on_camera_info)
        self.worker.settings_ready.connect(self._on_settings)
        self.worker.preview_ready.connect(self._on_preview)
        self.worker.focus_state.connect(self._on_focus_state)
        self.worker.capture_done.connect(self._on_capture_done)
        self.worker.stack_progress.connect(self._on_stack_progress)
        self.worker.stack_scan_sample.connect(self._on_scan_sample)
        self.worker.stack_plan_ready.connect(self._on_plan_ready)
        self.worker.stack_done.connect(self._on_stack_done)
        self.worker.autofocus_done.connect(self._on_af_done)
        self.worker.busy_changed.connect(self._on_busy)
        self.worker.cameras_found.connect(self._on_cameras_found)
        self.worker.path_changed.connect(self._on_path_changed)
        self.worker.exposure_lock_changed.connect(self._on_exposure_lock)

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self.worker.tick_preview)

        self._plan_autostart_timer = QTimer(self)
        self._plan_autostart_timer.setSingleShot(True)
        self._plan_autostart_timer.timeout.connect(lambda: self._resolve_plan(True))

        self._thread.start()
        self._build_ui()
        self.cmd_set_roi.emit(self.preview.roi())
        self.cmd_refresh_cameras.emit()
        self.cmd_connect.emit(0)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.preview_timer.stop()
        self.cmd_stop_hold.emit()
        self.cmd_set_live.emit(False)
        if self._thread.isRunning():
            QMetaObject.invokeMethod(
                self.worker,
                "disconnect_camera",
                Qt.ConnectionType.BlockingQueuedConnection,
            )
            self._thread.quit()
            self._thread.wait(2500)
        super().closeEvent(event)

    def _section(self, title: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName("railSection")
        return label

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(20, 14, 20, 10)
        brand_col = QVBoxLayout()
        brand = QLabel("Photography Hub")
        brand.setObjectName("brandTitle")
        sub = QLabel("TETHERED VIEWFINDER")
        sub.setObjectName("brandSub")
        brand_col.addWidget(brand)
        brand_col.addWidget(sub)
        header.addLayout(brand_col)
        header.addStretch(1)
        self.status_chip = QLabel("DISCONNECTED")
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setProperty("connected", "false")
        header.addWidget(self.status_chip)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)

        # —— Viewfinder (always visible, max space) ——
        left = QVBoxLayout()
        left.setContentsMargins(16, 0, 8, 10)
        left.setSpacing(8)

        well = QFrame()
        well.setObjectName("viewfinderWell")
        well_l = QVBoxLayout(well)
        well_l.setContentsMargins(8, 8, 8, 8)
        self.preview = PreviewCanvas()
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.roi_changed.connect(self._on_roi_changed)
        self.preview.click_af.connect(self._on_click_af)
        self._roi = self.preview.roi()
        well_l.addWidget(self.preview)
        left.addWidget(well, stretch=1)

        meter = QHBoxLayout()
        self.metrics_label = QLabel("Sharpness —   ·   Edge —")
        self.metrics_label.setObjectName("lcdMuted")
        meter.addWidget(self.metrics_label)
        meter.addStretch(1)
        hint = QLabel("AF box · click anywhere to AF · drag box to move")
        hint.setObjectName("lcdMuted")
        meter.addWidget(hint)
        self.hist_toggle = QCheckBox("Histograms")
        self.hist_toggle.toggled.connect(self._on_hist_toggled)
        meter.addWidget(self.hist_toggle)
        left.addLayout(meter)

        focus_strip = QFrame()
        focus_strip.setObjectName("focusStrip")
        fs = QVBoxLayout(focus_strip)
        fs.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        top.addWidget(QLabel("FOCUS"))
        self.focus_readout = QLabel("offset +0   ·   OK")
        self.focus_readout.setObjectName("lcdReadout")
        top.addWidget(self.focus_readout, stretch=1)
        reset = QPushButton("Reset Focus")
        reset.setObjectName("ghostButton")
        reset.setToolTip("Drive lens back to offset 0 (position when you connected)")
        reset.clicked.connect(self.cmd_zero_focus.emit)
        top.addWidget(reset)
        fs.addLayout(top)
        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel("Near"))
        self.focus_slider = QSlider(Qt.Orientation.Horizontal)
        self.focus_slider.setRange(-120, 120)
        self.focus_slider.setValue(0)
        self.focus_slider.sliderReleased.connect(self._slider_seek)
        self.focus_slider.valueChanged.connect(self._slider_moved)
        slider_row.addWidget(self.focus_slider, stretch=1)
        slider_row.addWidget(QLabel("Far"))
        fs.addLayout(slider_row)
        left.addWidget(focus_strip)

        body.addLayout(left, stretch=1)

        # —— Right rail: Shoot | Stack tabs ——
        rail = QFrame()
        rail.setObjectName("railPanel")
        rail.setFixedWidth(360)
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(10, 8, 10, 10)
        rail_l.setSpacing(6)

        self.rail_tabs = QTabWidget()
        self.rail_tabs.setObjectName("railTabs")
        self.rail_tabs.addTab(self._build_shoot_tab(), "Shoot")
        self.rail_tabs.addTab(self._build_stack_tab(), "Stack")
        rail_l.addWidget(self.rail_tabs, stretch=1)

        body.addWidget(rail)
        root.addLayout(body, stretch=1)

        self.output = QPlainTextEdit()
        self.output.setObjectName("logView")
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(88)
        root.addWidget(self.output)

        self._log("Ready. Shoot = live view + AF. Stack = focus brackets.")
        self._update_estimates()
        self._update_dof()

    def _wrap_scroll(self, inner: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        return scroll

    def _build_shoot_tab(self) -> QScrollArea:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        layout.addWidget(self._section("CAMERA"))
        self.camera_selector = QComboBox()
        layout.addWidget(self.camera_selector)
        cam_btns = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.setObjectName("ghostButton")
        refresh.clicked.connect(self.cmd_refresh_cameras.emit)
        connect_btn = QPushButton("Connect")
        connect_btn.setObjectName("primaryButton")
        connect_btn.clicked.connect(lambda: self.cmd_connect.emit(self.camera_selector.currentIndex()))
        cam_btns.addWidget(refresh)
        cam_btns.addWidget(connect_btn)
        layout.addLayout(cam_btns)

        self.info_card = QFrame()
        self.info_card.setObjectName("infoCard")
        info_l = QVBoxLayout(self.info_card)
        info_l.setContentsMargins(10, 8, 10, 8)
        self.info_title = QLabel("No camera")
        self.info_title.setObjectName("infoTitle")
        self.info_body = QLabel("Connect a camera to see battery, lens, and capabilities.")
        self.info_body.setWordWrap(True)
        self.info_body.setObjectName("lcdMuted")
        info_l.addWidget(self.info_title)
        info_l.addWidget(self.info_body)
        layout.addWidget(self.info_card)

        layout.addWidget(self._section("EXPOSURE"))
        self.settings_host = QVBoxLayout()
        self.settings_host.setSpacing(6)
        self._build_placeholder_settings()
        layout.addLayout(self.settings_host)

        layout.addWidget(self._section("FOCUS DRIVE  ·  HOLD TO MOVE"))
        af_grid = QGridLayout()
        af_grid.setSpacing(6)
        for col, label in enumerate(("S", "M", "L")):
            tip = QLabel(label)
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tip.setObjectName("lcdMuted")
            af_grid.addWidget(tip, 0, col + 1)
        for row, (direction, name) in enumerate((("near", "NEAR"), ("far", "FAR")), start=1):
            row_label = QLabel(name)
            row_label.setObjectName("lcdMuted")
            af_grid.addWidget(row_label, row, 0)
            for col, size in enumerate((1, 2, 3)):
                glyphs = ("‹", "‹‹", "‹‹‹") if direction == "near" else ("›", "››", "›››")
                button = QPushButton(glyphs[col])
                button.setObjectName("afButton")
                button.pressed.connect(lambda d=direction, s=size: self._af_pressed(d, s))
                button.released.connect(lambda: self.cmd_stop_hold.emit())
                af_grid.addWidget(button, row, col + 1)
        layout.addLayout(af_grid)

        layout.addWidget(self._section("BOX AF"))
        af_row = QHBoxLayout()
        self.af_button = QPushButton("AF on Box")
        self.af_button.setObjectName("accentAf")
        self.af_button.clicked.connect(lambda: self.cmd_run_af.emit(self.preview.roi()))
        center_roi = QPushButton("Center")
        center_roi.setObjectName("ghostButton")
        center_roi.clicked.connect(self.preview.center_roi)
        af_row.addWidget(self.af_button)
        af_row.addWidget(center_roi)
        layout.addLayout(af_row)
        af_help = QLabel("Click the viewfinder to AF on the box (fast hunt). Lens must be in MF.")
        af_help.setWordWrap(True)
        af_help.setObjectName("lcdMuted")
        layout.addWidget(af_help)

        layout.addWidget(self._section("CAPTURE"))
        self.shoot_button = QPushButton("SHOOT")
        self.shoot_button.setObjectName("shootButton")
        self.shoot_button.clicked.connect(lambda: self.cmd_shoot.emit(str(self.output_dir)))
        layout.addWidget(self.shoot_button)

        folder_row = QHBoxLayout()
        self.output_dir_label = QLabel(str(self.output_dir))
        self.output_dir_label.setWordWrap(True)
        self.output_dir_label.setObjectName("lcdMuted")
        folder_row.addWidget(self.output_dir_label, stretch=1)
        pick = QPushButton("Folder")
        pick.setObjectName("ghostButton")
        pick.clicked.connect(self._pick_output_folder)
        open_btn = QPushButton("Open")
        open_btn.setObjectName("ghostButton")
        open_btn.clicked.connect(self._open_output_folder)
        folder_row.addWidget(pick)
        folder_row.addWidget(open_btn)
        layout.addLayout(folder_row)

        layout.addStretch(1)
        return self._wrap_scroll(tab)

    def _build_stack_tab(self) -> QScrollArea:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        layout.addWidget(self._section("MODE"))
        self.capture_mode = QComboBox()
        self.capture_mode.addItems(["Basic", "Adaptive"])
        self.capture_mode.currentTextChanged.connect(self._on_mode_changed)
        layout.addWidget(self.capture_mode)
        self.mode_help = QLabel("")
        self.mode_help.setWordWrap(True)
        self.mode_help.setObjectName("lcdMuted")
        layout.addWidget(self.mode_help)

        self.marks_section = QWidget()
        marks_l = QVBoxLayout(self.marks_section)
        marks_l.setContentsMargins(0, 0, 0, 0)
        marks_l.setSpacing(8)
        marks_l.addWidget(self._section("MARKS (Basic only)"))
        how = QLabel("Focus Near → Set Start → Focus Far → Set End → Start Stack")
        how.setWordWrap(True)
        how.setObjectName("lcdMuted")
        marks_l.addWidget(how)
        marks = QHBoxLayout()
        self.start_label = QLabel("Start: +0")
        self.end_label = QLabel("End: +40")
        set_start = QPushButton("Set Start")
        set_start.setObjectName("ghostButton")
        set_start.clicked.connect(self._set_start)
        set_end = QPushButton("Set End")
        set_end.setObjectName("ghostButton")
        set_end.clicked.connect(self._set_end)
        marks.addWidget(self.start_label)
        marks.addWidget(set_start)
        marks.addWidget(self.end_label)
        marks.addWidget(set_end)
        marks_l.addLayout(marks)
        layout.addWidget(self.marks_section)

        layout.addWidget(self._section("PLAN"))
        opts = QGridLayout()
        opts.setSpacing(6)
        self.step_preset = QComboBox()
        self.step_preset.addItems([p.value for p in FocusStepPreset])
        self.step_preset.setCurrentText("Small")
        self.step_preset.currentTextChanged.connect(self._update_estimates)
        self.image_count = QSpinBox()
        self.image_count.setRange(0, 500)
        self.image_count.setSpecialValueText("Auto")
        self.image_count.setValue(0)
        self.image_count.valueChanged.connect(self._update_estimates)
        self.step_row_label = QLabel("Step")
        self.max_row_label = QLabel("Max frames")
        opts.addWidget(self.step_row_label, 0, 0)
        opts.addWidget(self.step_preset, 0, 1)
        opts.addWidget(self.max_row_label, 1, 0)
        opts.addWidget(self.image_count, 1, 1)
        layout.addLayout(opts)

        self.estimate_label = QLabel("Shots —   ·   Duration —")
        self.estimate_label.setObjectName("lcdReadout")
        layout.addWidget(self.estimate_label)

        dof_row = QHBoxLayout()
        self.mag_spin = QDoubleSpinBox()
        self.mag_spin.setRange(0.05, 5.0)
        self.mag_spin.setSingleStep(0.05)
        self.mag_spin.setValue(0.8)
        self.mag_spin.valueChanged.connect(self._update_dof)
        self.aperture_edit = QComboBox()
        self.aperture_edit.setEditable(True)
        self.aperture_edit.addItems(["f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16"])
        self.aperture_edit.setCurrentText("f/5.6")
        self.aperture_edit.currentTextChanged.connect(self._update_dof)
        self.dof_label = QLabel("")
        self.dof_label.setObjectName("lcdMuted")
        self.dof_label.setWordWrap(True)
        dof_row.addWidget(QLabel("Mag"))
        dof_row.addWidget(self.mag_spin)
        dof_row.addWidget(QLabel("f/"))
        dof_row.addWidget(self.aperture_edit)
        layout.addLayout(dof_row)
        layout.addWidget(self.dof_label)
        apply_dof = QPushButton("Apply DOF plan")
        apply_dof.setObjectName("ghostButton")
        apply_dof.clicked.connect(self._apply_dof_plan)
        layout.addWidget(apply_dof)

        layout.addWidget(self._section("QUALITY"))
        self.direction_combo = QComboBox()
        self.direction_combo.addItems(["Closest → Furthest", "Furthest → Closest"])
        self.direction_combo.currentTextChanged.connect(self._push_capture_options)
        layout.addWidget(self.direction_combo)
        settle_row = QHBoxLayout()
        settle_row.addWidget(QLabel("Settle ms"))
        self.settle_spin = QSpinBox()
        self.settle_spin.setRange(0, 5000)
        self.settle_spin.setSingleStep(100)
        self.settle_spin.setValue(800)
        self.settle_spin.valueChanged.connect(self._push_capture_options)
        settle_row.addWidget(self.settle_spin)
        layout.addLayout(settle_row)
        self.stillness_check = QCheckBox("Wait for stillness")
        self.stillness_check.setChecked(True)
        self.stillness_check.toggled.connect(self._push_capture_options)
        layout.addWidget(self.stillness_check)
        self.raw_jpeg_check = QCheckBox("Prefer RAW+JPEG")
        self.raw_jpeg_check.setChecked(True)
        self.raw_jpeg_check.setToolTip("RAW+JPEG when available (preview + merge), else RAW")
        self.raw_jpeg_check.toggled.connect(self._push_capture_options)
        layout.addWidget(self.raw_jpeg_check)
        flash_row = QHBoxLayout()
        flash_row.addWidget(QLabel("Flash recycle ms"))
        self.flash_spin = QSpinBox()
        self.flash_spin.setRange(0, 10000)
        self.flash_spin.setSingleStep(100)
        self.flash_spin.setValue(0)
        self.flash_spin.setToolTip("Extra wait before each shutter (strobe recycle)")
        self.flash_spin.valueChanged.connect(self._push_capture_options)
        flash_row.addWidget(self.flash_spin)
        layout.addLayout(flash_row)
        self.exposure_lock_label = QLabel("")
        self.exposure_lock_label.setObjectName("lcdMuted")
        layout.addWidget(self.exposure_lock_label)

        start_row = QHBoxLayout()
        start_stack = QPushButton("Start Stack")
        start_stack.setObjectName("primaryButton")
        start_stack.clicked.connect(self._start_stack)
        self.cancel_stack_btn = QPushButton("Cancel")
        self.cancel_stack_btn.setObjectName("ghostButton")
        self.cancel_stack_btn.setEnabled(False)
        self.cancel_stack_btn.clicked.connect(self._cancel_ops)
        start_row.addWidget(start_stack)
        start_row.addWidget(self.cancel_stack_btn)
        layout.addLayout(start_row)

        self.progress_label = QLabel("Ready")
        self.progress_label.setObjectName("lcdMuted")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

        layout.addWidget(self._section("FOCUS PLAN"))
        self.plan_label = QLabel("Start Adaptive to build a live focus plan")
        self.plan_label.setWordWrap(True)
        self.plan_label.setObjectName("lcdReadout")
        layout.addWidget(self.plan_label)
        self.sharpness_graph = SharpnessGraph()
        layout.addWidget(self.sharpness_graph)
        plan_btns = QHBoxLayout()
        self.confirm_plan_btn = QPushButton("Confirm Capture")
        self.confirm_plan_btn.setObjectName("accentAf")
        self.confirm_plan_btn.setEnabled(False)
        self.confirm_plan_btn.clicked.connect(lambda: self._resolve_plan(True))
        self.reject_plan_btn = QPushButton("Reject Plan")
        self.reject_plan_btn.setObjectName("ghostButton")
        self.reject_plan_btn.setEnabled(False)
        self.reject_plan_btn.clicked.connect(lambda: self._resolve_plan(False))
        plan_btns.addWidget(self.confirm_plan_btn)
        plan_btns.addWidget(self.reject_plan_btn)
        layout.addLayout(plan_btns)

        layout.addWidget(self._section("PREVIEW / MERGE"))
        self.compare = ComparePreview()
        self.compare.setMaximumHeight(200)
        layout.addWidget(self.compare)
        merge_row = QHBoxLayout()
        open_folder = QPushButton("Open folder")
        open_folder.setObjectName("ghostButton")
        open_folder.clicked.connect(self._open_output_folder)
        copy_helicon = QPushButton("Copy Helicon")
        copy_helicon.setObjectName("ghostButton")
        copy_helicon.clicked.connect(lambda: self._copy_merger("helicon"))
        copy_zerene = QPushButton("Copy Zerene")
        copy_zerene.setObjectName("ghostButton")
        copy_zerene.clicked.connect(lambda: self._copy_merger("zerene"))
        merge_row.addWidget(open_folder)
        merge_row.addWidget(copy_helicon)
        merge_row.addWidget(copy_zerene)
        layout.addLayout(merge_row)

        self.path_section = QWidget()
        self.path_section.setVisible(False)
        path_l = QVBoxLayout(self.path_section)
        path_l.setContentsMargins(0, 0, 0, 0)
        path_l.addWidget(self._section("FOCUS PATH"))
        self.record_btn = QPushButton("Record")
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._toggle_record_path)
        self.path_label = QLabel("Path: 0 points")
        path_l.addWidget(self.record_btn)
        path_l.addWidget(self.path_label)
        layout.addWidget(self.path_section)

        layout.addStretch(1)
        self._on_mode_changed(self.capture_mode.currentText())
        self._push_capture_options()
        return self._wrap_scroll(tab)

    def _build_placeholder_settings(self) -> None:
        while self.settings_host.count():
            item = self.settings_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._setting_widgets.clear()
        for key, label in SETTING_LABELS.items():
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(90)
            combo = QComboBox()
            combo.addItem("—")
            combo.setEnabled(False)
            row.addWidget(name)
            row.addWidget(combo, stretch=1)
            wrap = QWidget()
            wrap.setLayout(row)
            self.settings_host.addWidget(wrap)
            self._setting_widgets[key] = combo

    def _log(self, message: str) -> None:
        self.output.appendPlainText(message)

    def _set_chip(self, connected: bool, label: str) -> None:
        self.status_chip.setText(label.upper() if connected else "DISCONNECTED")
        self.status_chip.setProperty("connected", "true" if connected else "false")
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)

    def _on_connected(self, connected: bool, label: str) -> None:
        self._set_chip(connected, label)
        if connected:
            self.preview.set_placeholder("Waiting for live view…")
            self.cmd_set_live.emit(True)
            if not self.preview_timer.isActive():
                self.preview_timer.start()
        else:
            self.preview_timer.stop()
            self.preview.set_frame(QPixmap())
            self.preview.set_placeholder("No camera connected")
            self.info_title.setText("No camera")
            self.info_body.setText("Connect a camera to see battery, lens, and capabilities.")
            self._build_placeholder_settings()

    def _on_camera_info(self, info: object) -> None:
        if not isinstance(info, CameraInfo):
            return
        self.info_title.setText(info.model)
        box_af = "ready (software hunt)" if info.manual_focus else "needs MF focus drive"
        flags = (
            f"LiveView: {'yes' if info.live_view else 'no'}\n"
            f"Focus drive: {'yes' if info.manual_focus else 'no'}\n"
            f"Box AF: {box_af}"
        )
        self.info_body.setText(
            f"Battery: {info.battery}\nLens: {info.lens}\nStorage: {info.storage}\n\n{flags}"
        )

    def _on_cameras_found(self, items: object) -> None:
        self.camera_selector.clear()
        for item in items if isinstance(items, list) else []:
            self.camera_selector.addItem(str(item))

    def _on_settings(self, settings: object) -> None:
        available = {s.key: s for s in settings if isinstance(s, CameraSetting)} if isinstance(settings, list) else {}
        self._updating_settings = True
        for key, combo in self._setting_widgets.items():
            combo.blockSignals(True)
            combo.clear()
            setting = available.get(key)
            if setting is None:
                combo.addItem("—")
                combo.setEnabled(False)
            else:
                combo.addItems(setting.choices)
                index = combo.findText(setting.current)
                if index >= 0:
                    combo.setCurrentIndex(index)
                combo.setEnabled(True)
                try:
                    combo.currentTextChanged.disconnect()
                except TypeError:
                    pass
                combo.currentTextChanged.connect(
                    lambda value, k=key: self._setting_changed(k, value)
                )
            combo.blockSignals(False)
        self._updating_settings = False
        if "aperture" in available:
            self.aperture_edit.setCurrentText(available["aperture"].current)

    def _setting_changed(self, key: str, value: str) -> None:
        if self._updating_settings or self._busy or value == "—":
            return
        self.cmd_set_setting.emit(key, value)

    def _on_preview(self, data: bytes, metrics: object) -> None:
        pixmap = QPixmap()
        payload = data if isinstance(data, bytes) else bytes(data)
        if not pixmap.loadFromData(payload):
            return
        self.preview.set_frame(pixmap)
        if isinstance(metrics, PreviewMetrics):
            analysis = metrics.analysis
            text = f"Sharpness {analysis.sharpness:.1f}   ·   Edge {analysis.edge_density:.3f}"
            if metrics.roi_sharpness is not None:
                text += f"   ·   ROI {metrics.roi_sharpness:.1f}"
            self.metrics_label.setText(text)
            if self.hist_toggle.isChecked():
                self.preview.set_histogram(metrics.histogram)

    def _on_focus_state(self, offset: int, limit: str) -> None:
        self._focus_offset = offset
        self.focus_readout.setText(f"offset {offset:+d}   ·   {limit}")
        self._updating_slider = True
        clamped = max(self.focus_slider.minimum(), min(self.focus_slider.maximum(), offset))
        self.focus_slider.setValue(clamped)
        self._updating_slider = False

    def _slider_moved(self, value: int) -> None:
        if self._updating_slider:
            return
        self.focus_readout.setText(f"offset {value:+d}   ·   seek…")

    def _slider_seek(self) -> None:
        self.cmd_seek_focus.emit(self.focus_slider.value())

    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        self.shoot_button.setEnabled(not busy)
        self.af_button.setEnabled(not busy)
        self.cancel_stack_btn.setEnabled(busy)
        if not busy:
            self.confirm_plan_btn.setEnabled(False)
            self.reject_plan_btn.setEnabled(False)

    def _on_capture_done(self, ok: bool, message: str, sharpness: float, edge: float) -> None:
        if ok:
            self.metrics_label.setText(f"Sharpness {sharpness:.1f}   ·   Edge {edge:.3f}")

    def _on_stack_progress(self, index: int, total: int, sharpness: float, message: str) -> None:
        self.rail_tabs.setCurrentIndex(1)
        if total > 0:
            self.progress_bar.setValue(int(100 * index / total))
            remaining = estimate_duration_seconds(max(0, total - index))
            self.progress_label.setText(
                f"{message}   ·   {format_duration(remaining)} left"
            )
        else:
            self.progress_label.setText(message)
        if sharpness > 0 and not self.sharpness_graph.has_curve():
            self.sharpness_graph.add_value(sharpness)

    def _on_scan_sample(self, curve: object, offset: int, score: float) -> None:
        if not isinstance(curve, dict):
            return
        self.rail_tabs.setCurrentIndex(1)
        self.plan_label.setText(f"Scanning… offset {offset:+d}  ROI {score:.0f}  ({len(curve)} samples)")
        self.sharpness_graph.set_scan_curve(curve, title="Building focus plan…")
        self.progress_label.setText(f"Scan @ {offset:+d}")

    def _on_plan_ready(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        curve = data.get("curve") or {}
        offsets = data.get("offsets") or []
        message = str(data.get("message") or "")
        self.rail_tabs.setCurrentIndex(1)
        self.plan_label.setText(f"FOCUS PLAN — {message}. Auto-starting unless rejected.")
        self.sharpness_graph.set_plan(curve, offsets, title=f"Plan · {message}")
        self.confirm_plan_btn.setEnabled(True)
        self.reject_plan_btn.setEnabled(True)
        self.progress_label.setText(f"Plan ready — {message}")
        self.progress_bar.setValue(0)
        self._plan_autostart_timer.start(300)

    def _resolve_plan(self, accept: bool) -> None:
        if self._plan_autostart_timer.isActive():
            self._plan_autostart_timer.stop()
        self.confirm_plan_btn.setEnabled(False)
        self.reject_plan_btn.setEnabled(False)
        if accept:
            self.plan_label.setText("Plan confirmed — capturing…")
            self.progress_label.setText("Capturing stack…")
        else:
            self.plan_label.setText("Plan rejected")
        self.cmd_resolve_plan.emit(accept)

    def _cancel_ops(self) -> None:
        if self._plan_autostart_timer.isActive():
            self._plan_autostart_timer.stop()
        self.cmd_cancel.emit()
        self.confirm_plan_btn.setEnabled(False)
        self.reject_plan_btn.setEnabled(False)
        self.plan_label.setText("Cancelling…")

    def _on_exposure_lock(self, locked: object) -> None:
        if isinstance(locked, dict) and locked.get("locked"):
            self.exposure_lock_label.setText("Exposure locked for stack")
        else:
            self.exposure_lock_label.setText("")

    def _on_stack_done(self, message: str, paths: object) -> None:
        self._log(message)
        self.progress_bar.setValue(100)
        self.progress_label.setText(message)
        self.confirm_plan_btn.setEnabled(False)
        self.reject_plan_btn.setEnabled(False)
        path_list = paths if isinstance(paths, list) else []
        self._last_stack_paths = [str(p) for p in path_list]
        previewable: list[str] = []
        for p in self._last_stack_paths:
            path = Path(p)
            if path.suffix.lower() in {".jpg", ".jpeg"}:
                previewable.append(str(path))
                continue
            for alt in (path.with_suffix(".JPG"), path.with_suffix(".jpg"), path.with_suffix(".JPEG")):
                if alt.exists():
                    previewable.append(str(alt))
                    break
        before = previewable[0] if previewable else (self._last_stack_paths[0] if self._last_stack_paths else None)
        after = previewable[-1] if previewable else (self._last_stack_paths[-1] if self._last_stack_paths else None)
        self.compare.set_images(before, after)
        self.rail_tabs.setCurrentIndex(1)

    def _on_af_done(self, ok: bool, message: str) -> None:
        self._log(("AF ok — " if ok else "AF failed — ") + message)

    def _on_roi_changed(self, roi: object) -> None:
        self._roi = roi if isinstance(roi, tuple) else self.preview.roi()
        self.cmd_set_roi.emit(self._roi)

    def _on_click_af(self, roi: object) -> None:
        if self._busy:
            self._log("AF skipped — busy. Wait for shoot/stack/AF to finish.")
            return
        self._roi = roi if isinstance(roi, tuple) else self.preview.roi()
        self.cmd_set_roi.emit(self._roi)
        self.cmd_run_af.emit(self._roi)

    def _on_hist_toggled(self, enabled: bool) -> None:
        self.preview.set_show_histogram(enabled)
        self.cmd_set_histograms.emit(enabled)

    def _af_pressed(self, direction: str, size: int) -> None:
        if self._busy:
            return
        self.cmd_focus_once.emit(direction, size)
        self.cmd_start_hold.emit(direction, size)

    def _set_start(self) -> None:
        self._stack_start = self._focus_offset
        self.start_label.setText(f"Start: {self._stack_start:+d}")
        self._update_estimates()
        self._log(f"Stack start marked at {self._stack_start:+d}")

    def _set_end(self) -> None:
        self._stack_end = self._focus_offset
        self.end_label.setText(f"End: {self._stack_end:+d}")
        self._update_estimates()
        self._log(f"Stack end marked at {self._stack_end:+d}")

    def _on_mode_changed(self, mode: str) -> None:
        adaptive = mode == "Adaptive"
        self.marks_section.setVisible(not adaptive)
        self.step_preset.setEnabled(not adaptive)
        self.step_row_label.setEnabled(not adaptive)
        if adaptive:
            self.mode_help.setText(
                "AF box on subject → Start. Watch the focus plan build live, then Confirm Capture. "
                "Cancel anytime."
            )
            self.max_row_label.setText("Max frames")
        else:
            self.mode_help.setText(
                "Manual range: Set Start/End. Fixed step. Settle + stillness still apply."
            )
            self.max_row_label.setText("Images (0=Auto)")
        self._update_estimates()

    def _push_capture_options(self, *_args) -> None:
        aperture = parse_aperture(self.aperture_edit.currentText()) or 5.6
        self.cmd_set_capture_options.emit(
            {
                "settle_ms": self.settle_spin.value(),
                "stillness": self.stillness_check.isChecked(),
                "prefer_raw_jpeg": self.raw_jpeg_check.isChecked(),
                "near_to_far": self.direction_combo.currentIndex() == 0,
                "magnification": self.mag_spin.value(),
                "aperture": aperture,
                "flash_recycle_ms": self.flash_spin.value(),
            }
        )

    def _apply_dof_plan(self) -> None:
        aperture = parse_aperture(self.aperture_edit.currentText()) or 5.6
        estimate = estimate_dof(self.mag_spin.value(), aperture)
        preset = preset_for_step_mm(estimate.step_mm)
        self.step_preset.setCurrentText(preset.value)
        shots = plan_shot_count(
            self._stack_start,
            self._stack_end,
            magnification=self.mag_spin.value(),
            aperture=aperture,
        )
        self.image_count.setValue(shots)
        self._log(
            f"DOF plan → step {preset.value} (~{estimate.step_mm:.2f} mm), {shots} frames for current marks"
        )
        self._update_estimates()

    def _copy_merger(self, tool: str) -> None:
        from PySide6.QtWidgets import QApplication

        if not self._last_stack_paths:
            self._log("No stack paths to copy yet.")
            return
        cmd = merger_command(tool, self._last_stack_paths)
        QApplication.clipboard().setText(cmd)
        self._log(f"Copied {tool} command ({len(self._last_stack_paths)} files).")

    def _update_estimates(self) -> None:
        if self.capture_mode.currentText() == "Adaptive":
            shots = self.image_count.value() or 80
            duration = format_duration(estimate_duration_seconds(shots) + shots * 0.8)
            self.estimate_label.setText(f"Up to {shots}   ·   ~{duration} (scan + capture)")
            return
        preset = FocusStepPreset(self.step_preset.currentText())
        controller = FocusStackController()
        plan = controller.build_preset_plan(self._stack_start, self._stack_end, preset)
        shots = self.image_count.value() or plan.step_count
        duration = format_duration(estimate_duration_seconds(shots) + shots * 0.8)
        self.estimate_label.setText(f"Shots {shots}   ·   ~{duration}")

    def _update_dof(self) -> None:
        aperture = parse_aperture(self.aperture_edit.currentText()) or 5.6
        estimate = estimate_dof(self.mag_spin.value(), aperture)
        self.dof_label.setText(
            f"Diffraction {estimate.diffraction} · step ~{estimate.step_mm:.2f} mm"
        )
        self._push_capture_options()

    def _toggle_record_path(self, enabled: bool) -> None:
        self.record_btn.setText("Stop" if enabled else "Record")
        self.cmd_set_recording.emit(enabled)

    def _on_path_changed(self, points: object) -> None:
        self._path_points = list(points) if isinstance(points, list) else []
        self.path_label.setText(f"Path: {len(self._path_points)} points")

    def _pick_output_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Output Folder", str(self.output_dir))
        if chosen:
            self.output_dir = Path(chosen)
            self.output_dir_label.setText(str(self.output_dir))
            self._log(f"Output folder → {self.output_dir}")

    def _open_output_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def _start_stack(self) -> None:
        self._push_capture_options()
        self.sharpness_graph.clear()
        self.plan_label.setText(
            "Building focus plan…"
            if self.capture_mode.currentText() == "Adaptive"
            else "Stack running…"
        )
        self.confirm_plan_btn.setEnabled(False)
        self.reject_plan_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        adaptive = self.capture_mode.currentText() == "Adaptive"
        max_frames = self.image_count.value() or (80 if adaptive else 40)
        self.cmd_run_stack.emit(
            str(self.output_dir),
            self._stack_start,
            self._stack_end,
            self.step_preset.currentText(),
            self.capture_mode.currentText(),
            max_frames,
        )
