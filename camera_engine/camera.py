from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from time import time

import gphoto2 as gp


SETTING_ALIASES: dict[str, tuple[str, ...]] = {
    "iso": ("iso",),
    "shutterspeed": ("shutterspeed", "shutterspeed2", "shutterspeed3"),
    "aperture": ("aperture", "f-number", "fnumber", "aperture2"),
    "whitebalance": ("whitebalance", "whitebalance2", "whitebalancea", "colortemperature"),
    "exposurecompensation": ("exposurecompensation", "exposurecompensation2", "exposecomp"),
    "imageformat": ("imageformat", "imagequality", "imageformatcf", "imageformatsd"),
    "shuttermode": ("shuttermode", "capturemode", "drivemode"),
}

SETTING_LABELS: dict[str, str] = {
    "iso": "ISO",
    "shutterspeed": "Shutter",
    "aperture": "Aperture",
    "whitebalance": "WB",
    "exposurecompensation": "Exp Comp",
    "imageformat": "Format",
    "shuttermode": "Drive",
}


@dataclass(slots=True)
class CameraInfo:
    model: str
    battery: str = "—"
    lens: str = "—"
    storage: str = "—"
    live_view: bool = False
    autofocus: bool = False
    manual_focus: bool = False

FOCUS_DRIVE_NAMES = ("manualfocusdrive", "focusdrive", "focusstep", "focusmove")
FOCUS_POINT_NAMES = ("focuspoint", "autofocuspoint", "afpoint", "autofocus", "focusarea")


@dataclass(slots=True)
class CameraCommandResult:
    success: bool
    command: list[str]
    stderr: str = ""


@dataclass(slots=True)
class DetectedCamera:
    name: str
    port: str


@dataclass(slots=True)
class CapturedImage:
    success: bool
    saved_path: Path | None
    camera_path: str = ""
    stderr: str = ""
    preview_data: bytes | None = None
    extra_paths: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class CameraSetting:
    key: str
    label: str
    current: str
    choices: list[str]


def normalize_choice(value: str) -> str:
    return value.strip().lower().replace(" ", "")


def match_choice(choices: Iterable[str], wanted: str) -> str | None:
    normalized_wanted = normalize_choice(wanted)
    exact: str | None = None
    partial: str | None = None
    for choice in choices:
        normalized = normalize_choice(str(choice))
        if normalized_wanted == normalized:
            exact = str(choice)
            break
        if normalized_wanted in normalized or normalized in normalized_wanted:
            partial = partial or str(choice)
    return exact or partial


def focus_drive_label(direction: str, size: int) -> str:
    side = "Far" if normalize_choice(direction).startswith("far") else "Near"
    step = max(1, min(3, int(size)))
    return f"{side}{step}"


def destination_with_camera_suffix(destination: str | Path, camera_name: str) -> Path:
    """Replace destination suffix with the camera file's real extension."""
    dest = Path(destination)
    suffix = Path(camera_name).suffix
    if not suffix:
        return dest
    return dest.with_suffix(suffix)


def prefer_raw_jpeg_choice(choices: Iterable[str]) -> str | None:
    """Pick RAW+JPEG if present, else pure RAW, else JPEG, else first choice."""
    items = [str(c) for c in choices]
    if not items:
        return None

    def score(text: str) -> tuple[int, str]:
        n = normalize_choice(text)
        has_raw = "raw" in n
        has_jpeg = "jpeg" in n or "jpg" in n
        if has_raw and has_jpeg:
            return (0, text)
        if has_raw and not has_jpeg:
            return (1, text)
        if has_jpeg:
            return (2, text)
        return (3, text)

    return min(items, key=score)


class GPhoto2Camera:
    def __init__(self, executable: str = "gphoto2") -> None:
        self.executable = executable
        self.context = gp.Context()
        self.camera: gp.Camera | None = None
        self._model_name = ""
        self._config_cache: gp.CameraWidget | None = None

    @staticmethod
    def detect_cameras() -> list[DetectedCamera]:
        cameras: list[DetectedCamera] = []
        for item in gp.Camera.autodetect():
            if isinstance(item, tuple):
                name = str(item[0]) if len(item) > 0 else "Unknown"
                port = str(item[1]) if len(item) > 1 else ""
                cameras.append(DetectedCamera(name=name, port=port))
            else:
                cameras.append(DetectedCamera(name=str(item), port=""))
        return cameras

    def connect_camera(self, camera_index: int = 0) -> CameraCommandResult:
        return self.connect_selected_camera(camera_index)

    def is_connected(self) -> bool:
        return self.camera is not None

    @property
    def model_name(self) -> str:
        return self._model_name

    def connect_first_camera(self) -> CameraCommandResult:
        cameras = self.detect_cameras()
        if not cameras:
            self.camera = None
            self._model_name = ""
            return CameraCommandResult(False, ["autodetect"], "No camera detected")
        return self.connect_selected_camera(0)

    def _connect_detected_camera(
        self,
        detected: DetectedCamera,
        port_info_list: gp.PortInfoList,
        abilities_list: gp.CameraAbilitiesList,
    ) -> CameraCommandResult:
        try:
            if self.camera is not None:
                self.close()
            camera = gp.Camera()
            port_index = port_info_list.lookup_path(detected.port)
            camera.set_port_info(port_info_list[port_index])
            ability_index = abilities_list.lookup_model(detected.name)
            camera.set_abilities(abilities_list[ability_index])
            camera.init()
            self.camera = camera
            self._model_name = detected.name
            return CameraCommandResult(True, [detected.name, detected.port])
        except Exception as error:
            self.camera = None
            self._model_name = ""
            return CameraCommandResult(False, [detected.name, detected.port], str(error))

    def connect_selected_camera(self, camera_index: int) -> CameraCommandResult:
        cameras = self.detect_cameras()
        if not cameras:
            self.camera = None
            self._model_name = ""
            return CameraCommandResult(False, [str(camera_index)], "No camera detected")

        if camera_index < 0 or camera_index >= len(cameras):
            return CameraCommandResult(False, [str(camera_index)], "Camera selection out of range")

        port_info_list = gp.PortInfoList()
        port_info_list.load()
        abilities_list = gp.CameraAbilitiesList()
        abilities_list.load()
        return self._connect_detected_camera(cameras[camera_index], port_info_list, abilities_list)

    def close(self) -> None:
        if self.camera is not None:
            try:
                self.camera.exit()
            except Exception:
                pass
            self.camera = None
            self._model_name = ""

    def _require_camera(self) -> gp.Camera:
        if self.camera is None:
            result = self.connect_first_camera()
            if not result.success or self.camera is None:
                raise RuntimeError(result.stderr or "No camera connected")
        return self.camera

    def _get_config(self) -> gp.CameraWidget:
        if self._config_cache is not None:
            return self._config_cache
        return self._require_camera().get_config()

    def _set_config(self, config: gp.CameraWidget) -> None:
        self._require_camera().set_config(config)
    
    @contextmanager
    def batch_config(self):
        """Reuse one fetched config tree across a block instead of
        re-fetching before each call. Writes still happen every time --
        focus-drive widgets are one-shot actuators; batching writes would
        collapse several physical moves into one."""
        owns_batch = self._config_cache is None
        if owns_batch:
            self._config_cache = self._require_camera().get_config()
        try:
            yield
        finally:
            if owns_batch:
                self._config_cache = None

    @staticmethod
    def _find_child_by_names(root: gp.CameraWidget, names: Iterable[str]) -> gp.CameraWidget | None:
        wanted = {normalize_choice(name) for name in names}
        stack = [root]
        while stack:
            widget = stack.pop()
            try:
                name = normalize_choice(str(widget.get_name()))
            except Exception:
                name = ""
            if name in wanted:
                return widget
            try:
                child_count = widget.count_children()
            except Exception:
                child_count = 0
            for index in range(child_count):
                try:
                    stack.append(widget.get_child(index))
                except Exception:
                    continue
        return None

    def _widget_choices(self, widget: gp.CameraWidget) -> list[str]:
        try:
            return [str(choice) for choice in widget.get_choices()]
        except Exception:
            return []

    def list_config(self) -> CameraCommandResult:
        camera = self._require_camera()
        config = camera.get_config()
        return CameraCommandResult(True, ["list-config"], str(config))

    def capture_preview(self) -> CapturedImage:
        try:
            camera = self._require_camera()
            camera_file = camera.capture_preview()
            raw = camera_file.get_data_and_size()
            data = bytes(raw) if not isinstance(raw, bytes) else raw
            return CapturedImage(True, None, preview_data=data)
        except Exception as error:
            return CapturedImage(False, None, stderr=str(error))

    def set_capture_target(self, target: str) -> CameraCommandResult:
        try:
            config = self._get_config()
            widget = self._find_child_by_names(config, ("capturetarget",))
            if widget is None:
                return CameraCommandResult(False, [target], "Camera does not expose capturetarget")

            selected = match_choice(self._widget_choices(widget), target)
            if selected is None:
                return CameraCommandResult(False, [target], f"Unsupported capture target: {target}")

            widget.set_value(selected)
            self._set_config(config)
            return CameraCommandResult(True, [target])
        except Exception as error:
            return CameraCommandResult(False, [target], str(error))

    def set_widget_choice(self, widget_names: Iterable[str], choice_text: str) -> CameraCommandResult:
        try:
            config = self._get_config()
            widget = self._find_child_by_names(config, widget_names)
            if widget is None:
                return CameraCommandResult(
                    False, [choice_text], f"Camera does not expose {', '.join(widget_names)}"
                )

            choices = self._widget_choices(widget)
            selected = match_choice(choices, choice_text) if choices else None
            if selected is None and choices:
                return CameraCommandResult(False, [choice_text], f"Unsupported value: {choice_text}")

            widget.set_value(selected if selected is not None else choice_text)
            self._set_config(config)
            return CameraCommandResult(True, [choice_text])
        except Exception as error:
            return CameraCommandResult(False, [choice_text], str(error))

    def has_widget(self, widget_names: Iterable[str]) -> bool:
        if not self.is_connected():
            return False
        try:
            config = self._get_config()
            return self._find_child_by_names(config, widget_names) is not None
        except Exception:
            return False

    def set_focus_point(self, focus_point: str) -> CameraCommandResult:
        return self.set_widget_choice(FOCUS_POINT_NAMES, focus_point)

    def list_available_settings(self) -> list[CameraSetting]:
        if not self.is_connected():
            return []
        try:
            config = self._get_config()
        except Exception:
            return []

        settings: list[CameraSetting] = []
        for key, aliases in SETTING_ALIASES.items():
            widget = self._find_child_by_names(config, aliases)
            if widget is None:
                continue
            choices = self._widget_choices(widget)
            if not choices:
                continue
            try:
                current = str(widget.get_value())
            except Exception:
                current = choices[0]
            settings.append(
                CameraSetting(
                    key=key,
                    label=SETTING_LABELS.get(key, key),
                    current=current,
                    choices=choices,
                )
            )
        return settings

    def get_setting_value(self, key: str) -> str | None:
        aliases = SETTING_ALIASES.get(key)
        if not aliases:
            return None
        try:
            config = self._get_config()
            widget = self._find_child_by_names(config, aliases)
            if widget is None:
                return None
            return str(widget.get_value())
        except Exception:
            return None

    def set_setting_value(self, key: str, value: str) -> CameraCommandResult:
        aliases = SETTING_ALIASES.get(key)
        if not aliases:
            return CameraCommandResult(False, [key, value], f"Unknown setting: {key}")
        return self.set_widget_choice(aliases, value)

    def capture_image(self, destination: Path, *, fetch_companions: bool = True) -> CapturedImage:
        camera = self._require_camera()
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_path = camera.capture(gp.GP_CAPTURE_IMAGE)
            camera_file = camera.file_get(file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL)
            final_path = destination_with_camera_suffix(destination, file_path.name)
            camera_file.save(str(final_path))
            extras: list[Path] = []
            if fetch_companions:
                extras = self._download_companions(
                    file_path.folder,
                    file_path.name,
                    final_path,
                )
            return CapturedImage(
                True,
                final_path,
                f"{file_path.folder}/{file_path.name}",
                extra_paths=extras,
            )
        except Exception as error:
            return CapturedImage(False, None, stderr=str(error))

    def _download_companions(self, folder: str, name: str, primary: Path) -> list[Path]:
        """Download sibling RAW/JPEG with the same stem (dual capture)."""
        camera = self._require_camera()
        stem = Path(name).stem.lower()
        primary_name = Path(name).name.lower()
        found: set[str] = {primary_name}

        # Drain FILE_ADDED events briefly — second half of RAW+JPEG often arrives here
        deadline = time() + 2.0
        while time() < deadline:
            try:
                event_type, event_data = camera.wait_for_event(200)
            except Exception:
                break
            if event_type == gp.GP_EVENT_TIMEOUT:
                break
            if event_type == gp.GP_EVENT_FILE_ADDED and event_data is not None:
                try:
                    found.add(str(event_data.name).lower())
                    folder = str(event_data.folder) or folder
                except Exception:
                    continue

        try:
            for fname, _value in camera.folder_list_files(folder):
                if Path(str(fname)).stem.lower() == stem:
                    found.add(str(fname).lower())
        except Exception:
            pass

        saved: list[Path] = []
        for fname in sorted(found):
            if fname == primary_name:
                continue
            try:
                # Restore original casing from listing when possible
                real_name = fname
                try:
                    for listed, _ in camera.folder_list_files(folder):
                        if str(listed).lower() == fname:
                            real_name = str(listed)
                            break
                except Exception:
                    pass
                dest = primary.with_suffix(Path(real_name).suffix)
                if dest.exists() and dest.resolve() == primary.resolve():
                    continue
                camera_file = camera.file_get(folder, real_name, gp.GP_FILE_TYPE_NORMAL)
                camera_file.save(str(dest))
                saved.append(dest)
            except Exception:
                continue
        return saved

    def snapshot_exposure(self) -> dict[str, str]:
        locked: dict[str, str] = {}
        for key in ("iso", "shutterspeed", "aperture", "whitebalance", "exposurecompensation"):
            value = self.get_setting_value(key)
            if value:
                locked[key] = value
        return locked

    def restore_exposure(self, locked: dict[str, str]) -> None:
        for key, value in locked.items():
            current = self.get_setting_value(key)
            if current is None:
                continue
            if normalize_choice(current) != normalize_choice(value):
                self.set_setting_value(key, value)

    def prefer_raw_jpeg(self) -> CameraCommandResult:
        setting = None
        for item in self.list_available_settings():
            if item.key == "imageformat":
                setting = item
                break
        if setting is None:
            return CameraCommandResult(False, ["imageformat"], "No image format control")
        picked = prefer_raw_jpeg_choice(setting.choices)
        if picked is None:
            return CameraCommandResult(False, ["imageformat"], "No format choices")
        if normalize_choice(picked) == normalize_choice(setting.current):
            return CameraCommandResult(True, ["imageformat", picked])
        return self.set_setting_value("imageformat", picked)

    def focus_manual_drive(self, direction: str) -> CameraCommandResult:
        try:
            config = self._get_config()
            widget = self._find_child_by_names(config, FOCUS_DRIVE_NAMES)
            if widget is None:
                return CameraCommandResult(False, [direction], "Camera does not expose a manual focus drive control")

            choices = self._widget_choices(widget)
            selected_choice = match_choice(choices, direction)
            if selected_choice is None:
                normalized_direction = normalize_choice(direction)
                for choice in choices:
                    normalized_choice = normalize_choice(str(choice))
                    if normalized_direction.startswith("far") and "far" in normalized_choice:
                        selected_choice = str(choice)
                        break
                    if normalized_direction.startswith("near") and "near" in normalized_choice:
                        selected_choice = str(choice)
                        break

            if selected_choice is None:
                return CameraCommandResult(False, [direction], f"Unsupported focus drive value: {direction}")

            widget.set_value(selected_choice)
            self._set_config(config)
            return CameraCommandResult(True, [direction])
        except Exception as error:
            return CameraCommandResult(False, [direction], str(error))

    def focus_step(self, direction: str, size: int = 1) -> CameraCommandResult:
        return self.focus_manual_drive(focus_drive_label(direction, size))

    def _read_widget_text(self, names: Iterable[str]) -> str | None:
        try:
            config = self._get_config()
            widget = self._find_child_by_names(config, names)
            if widget is None:
                return None
            return str(widget.get_value())
        except Exception:
            return None

    def get_camera_info(self) -> CameraInfo:
        model = self._model_name or self._read_widget_text(("cameramodel", "model", "artist")) or "Camera"
        battery = self._read_widget_text(("batterylevel", "battery", "batterycheck")) or "—"
        lens = self._read_widget_text(("lensname", "lens", "lensid", "lensinfo")) or "—"
        storage = (
            self._read_widget_text(("availablespacestr", "freespace", "availablespace", "cardspace"))
            or "—"
        )
        return CameraInfo(
            model=model,
            battery=battery,
            lens=lens,
            storage=storage,
            live_view=True,
            autofocus=self.has_widget(("autofocusdrive", "autofocus", "touchaf", "eosremoterelease")),
            manual_focus=self.has_widget(FOCUS_DRIVE_NAMES),
        )

    def camera_summary(self) -> str:
        camera = self._require_camera()
        summary = camera.get_summary()
        return str(summary)
