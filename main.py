# -*- coding: utf-8 -*-
"""
LocalScreenCam MVP

A local Windows desktop application that captures screen/window/video/image frames
and sends them to an existing virtual camera backend through pyvirtualcam.

Important:
- This MVP does NOT install a camera driver by itself.
- Install OBS Studio Virtual Camera or Unity Capture first.
- Browsers will usually show the backend device name such as "OBS Virtual Camera"
  or "Unity Video Capture", not necessarily "LocalScreenCam".
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import mss
import numpy as np

try:
    import pyvirtualcam
    PYVIRTUALCAM_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - handled at runtime on user machines
    pyvirtualcam = None  # type: ignore
    PYVIRTUALCAM_IMPORT_ERROR = repr(exc)

try:
    import win32con
    import win32gui
except Exception as exc:  # pragma: no cover - handled at runtime on user machines
    win32con = None  # type: ignore
    win32gui = None  # type: ignore
    WIN32_IMPORT_ERROR = repr(exc)
else:
    WIN32_IMPORT_ERROR = ""

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "LocalScreenCam"
DEFAULT_CONFIG: Dict[str, Any] = {
    "source_type": "全屏",
    "monitor_index": 1,
    "window_title": "",
    "video_path": "",
    "image_path": "",
    "region": {"left": 0, "top": 0, "width": 1280, "height": 720},
    "resolution": "1280x720",
    "fps": 30,
    "mirror": False,
    "vertical_flip": False,
    "fill_mode": "适应",
    "always_on_top": False,
}
SCREEN_SOURCES = {"全屏", "指定显示器", "指定窗口", "指定区域"}


def app_dir() -> Path:
    """Return the application directory for source and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = app_dir()
CONFIG_PATH = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / f"{APP_NAME}_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
LOGGER = logging.getLogger(APP_NAME)


def deep_update(defaults: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a loaded config over defaults without losing nested default keys."""
    result = dict(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            nested = dict(result[key])
            nested.update(value)
            result[key] = nested
        else:
            result[key] = value
    return result


def load_config() -> Dict[str, Any]:
    """Load config.json. Invalid or missing config falls back to defaults."""
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("config.json root must be an object")
        return deep_update(DEFAULT_CONFIG, loaded)
    except Exception as exc:
        LOGGER.warning("Failed to load config.json, using defaults: %s", exc)
        return dict(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> None:
    """Persist config.json in UTF-8."""
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_image_bgr(path: str) -> Optional[np.ndarray]:
    """Read an image from a Windows path, including paths with non-ASCII chars."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 4:
            # Alpha composite BGRA over black background.
            bgr = img[:, :, :3].astype(np.float32)
            alpha = (img[:, :, 3:4].astype(np.float32) / 255.0)
            blended = bgr * alpha
            return np.clip(blended, 0, 255).astype(np.uint8)
        if img.shape[2] == 3:
            return img
        return None
    except Exception:
        LOGGER.exception("Failed to load image: %s", path)
        return None


def placeholder_bgr(width: int, height: int, title: str, detail: str = "") -> np.ndarray:
    """Generate a black diagnostic frame in BGR format."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    title = title[:80]
    detail = detail[:120]
    cv2.putText(frame, title, (40, max(60, height // 2 - 20)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (230, 230, 230), 2)
    if detail:
        cv2.putText(frame, detail, (40, max(105, height // 2 + 25)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 180, 180), 1)
    return frame


def resize_with_mode(frame_bgr: np.ndarray, target_w: int, target_h: int, fill_mode: str) -> np.ndarray:
    """Resize/crop/letterbox a BGR frame to the requested output size."""
    if frame_bgr is None or frame_bgr.size == 0:
        return placeholder_bgr(target_w, target_h, "No frame")

    src_h, src_w = frame_bgr.shape[:2]
    if src_w <= 0 or src_h <= 0:
        return placeholder_bgr(target_w, target_h, "Invalid frame size")

    if fill_mode == "裁剪填充":
        scale = max(target_w / src_w, target_h / src_h)
        new_w = max(1, int(round(src_w * scale)))
        new_h = max(1, int(round(src_h * scale)))
        interp = cv2.INTER_LINEAR if scale >= 1.0 else cv2.INTER_AREA
        resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=interp)
        x0 = max(0, (new_w - target_w) // 2)
        y0 = max(0, (new_h - target_h) // 2)
        cropped = resized[y0 : y0 + target_h, x0 : x0 + target_w]
        if cropped.shape[1] != target_w or cropped.shape[0] != target_h:
            return cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        return cropped

    # 默认“适应”：等比缩放 + 黑边适配。
    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    interp = cv2.INTER_LINEAR if scale >= 1.0 else cv2.INTER_AREA
    resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=interp)
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    x0 = (target_w - new_w) // 2
    y0 = (target_h - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def parse_resolution(value: str) -> Tuple[int, int]:
    """Parse a config resolution value like 1280x720."""
    try:
        w_str, h_str = value.lower().split("x", 1)
        return int(w_str), int(h_str)
    except Exception:
        return 1280, 720


def prepare_output_rgb(
    frame_bgr: np.ndarray,
    target_w: int,
    target_h: int,
    fill_mode: str,
    mirror: bool,
    vertical_flip: bool,
) -> np.ndarray:
    """Convert a captured BGR frame into a contiguous RGB frame for pyvirtualcam."""
    out = resize_with_mode(frame_bgr, target_w, target_h, fill_mode)
    if mirror:
        out = cv2.flip(out, 1)
    if vertical_flip:
        out = cv2.flip(out, 0)
    rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb, dtype=np.uint8)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    rect: Tuple[int, int, int, int]


def enum_windows() -> List[WindowInfo]:
    """Enumerate visible top-level windows that have titles and non-zero size."""
    if win32gui is None:
        return []

    windows: List[WindowInfo] = []

    def callback(hwnd: int, _extra: Any) -> bool:
        try:
            if not win32gui.IsWindow(hwnd):
                return True
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect
            if (right - left) < 50 or (bottom - top) < 50:
                return True
            windows.append(WindowInfo(hwnd=hwnd, title=title, rect=rect))
        except Exception:
            return True
        return True

    win32gui.EnumWindows(callback, None)
    # Sort by title for easier selection.
    windows.sort(key=lambda item: item.title.lower())
    return windows


def find_window_by_title(title: str) -> Optional[int]:
    """Find a window by exact title first, then by substring."""
    if not title or win32gui is None:
        return None
    candidates = enum_windows()
    for item in candidates:
        if item.title == title:
            return item.hwnd
    title_lower = title.lower()
    for item in candidates:
        if title_lower in item.title.lower():
            return item.hwnd
    return None


class CaptureWorker(QThread):
    """Background capture + virtual camera output worker.

    It intentionally runs outside the GUI thread so screen capture, OpenCV work,
    video decoding, and pyvirtualcam sleeps do not freeze the UI.
    """

    frame_ready = Signal(object)  # RGB ndarray
    status = Signal(str)
    log = Signal(str)
    error = Signal(str)

    def __init__(self, config: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = dict(config)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _open_virtual_camera(self, width: int, height: int, fps: int):
        """Open pyvirtualcam with a robust backend fallback sequence."""
        if pyvirtualcam is None:
            raise RuntimeError(
                "pyvirtualcam 导入失败。请确认 requirements.txt 已安装。"
                f" 原始错误: {PYVIRTUALCAM_IMPORT_ERROR}"
            )

        errors: List[str] = []
        backend_candidates = [None, "obs", "unitycapture"]

        for backend in backend_candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "fmt": pyvirtualcam.PixelFormat.RGB,
                    "print_fps": False,
                }
                if backend is not None:
                    kwargs["backend"] = backend
                cam = pyvirtualcam.Camera(**kwargs)
                backend_name = backend or "auto"
                self.log.emit(f"虚拟摄像头后端已打开: backend={backend_name}, device={getattr(cam, 'device', 'unknown')}")
                return cam
            except Exception as exc:
                backend_name = backend or "auto"
                errors.append(f"{backend_name}: {exc}")

        detail = " | ".join(errors)
        raise RuntimeError(
            "没有可用的虚拟摄像头后端。请安装 OBS Studio（带 OBS Virtual Camera）"
            "或 Unity Capture，安装后重启本程序和浏览器。"
            f" 详细错误: {detail}"
        )

    def _monitor_region(self, sct: mss.mss) -> Dict[str, int]:
        source = self.config.get("source_type", "全屏")
        monitors = sct.monitors
        if source == "全屏":
            return dict(monitors[0])
        if source == "指定显示器":
            idx = int(self.config.get("monitor_index", 1) or 1)
            if idx < 1 or idx >= len(monitors):
                raise RuntimeError(f"显示器索引无效: {idx}。当前可用显示器数量: {max(0, len(monitors) - 1)}")
            return dict(monitors[idx])
        if source == "指定区域":
            region = self.config.get("region", {}) or {}
            left = int(region.get("left", 0))
            top = int(region.get("top", 0))
            width = max(1, int(region.get("width", 1280)))
            height = max(1, int(region.get("height", 720)))
            return {"left": left, "top": top, "width": width, "height": height}
        if source == "指定窗口":
            return self._window_region()
        return dict(monitors[0])

    def _window_region(self) -> Dict[str, int]:
        if win32gui is None:
            raise RuntimeError(f"pywin32/win32gui 不可用，无法采集窗口。导入错误: {WIN32_IMPORT_ERROR}")

        hwnd = int(self.config.get("window_hwnd") or 0)
        title = str(self.config.get("window_title") or "")
        if not hwnd or not win32gui.IsWindow(hwnd):
            found = find_window_by_title(title)
            hwnd = int(found or 0)

        if not hwnd or not win32gui.IsWindow(hwnd):
            raise RuntimeError("未找到指定窗口，请刷新窗口列表后重新选择。")
        if not win32gui.IsWindowVisible(hwnd):
            raise RuntimeError("指定窗口不可见。")
        if win32gui.IsIconic(hwnd):
            raise RuntimeError("指定窗口已最小化；请恢复窗口后再采集。")

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = int(right - left)
        height = int(bottom - top)
        if width <= 0 or height <= 0:
            raise RuntimeError("指定窗口尺寸无效。")
        return {"left": int(left), "top": int(top), "width": width, "height": height}

    def _capture_screen_frame(self, sct: mss.mss) -> np.ndarray:
        region = self._monitor_region(sct)
        raw = np.array(sct.grab(region), dtype=np.uint8)
        return cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

    def run(self) -> None:  # noqa: C901 - explicit control flow improves readability here
        width, height = parse_resolution(str(self.config.get("resolution", "1280x720")))
        fps = int(self.config.get("fps", 30) or 30)
        source = str(self.config.get("source_type", "全屏"))
        fill_mode = str(self.config.get("fill_mode", "适应"))
        mirror = bool(self.config.get("mirror", False))
        vertical_flip = bool(self.config.get("vertical_flip", False))

        camera = None
        video_cap: Optional[cv2.VideoCapture] = None
        static_image: Optional[np.ndarray] = None
        sct: Optional[mss.mss] = None
        last_capture_error = ""
        last_error_time = 0.0
        frame_index = 0
        preview_every = max(1, fps // 15)  # avoid overloading the GUI at 30 FPS

        try:
            cv2.setNumThreads(1)
            self.status.emit("正在初始化来源...")
            self.log.emit(f"启动输出: source={source}, resolution={width}x{height}, fps={fps}, fill={fill_mode}")

            if source == "本地视频":
                video_path = str(self.config.get("video_path") or "").strip()
                if not video_path:
                    raise RuntimeError("未选择本地视频文件。")
                if not Path(video_path).exists():
                    raise RuntimeError(f"视频文件不存在: {video_path}")
                video_cap = cv2.VideoCapture(video_path)
                if not video_cap.isOpened():
                    raise RuntimeError(
                        "视频文件打开失败。请确认文件格式受 OpenCV/系统解码器支持；"
                        "如果路径包含特殊字符，可先复制到英文路径测试。"
                    )
                self.log.emit(f"视频文件已打开: {video_path}")

            elif source == "本地图片":
                image_path = str(self.config.get("image_path") or "").strip()
                if not image_path:
                    raise RuntimeError("未选择本地图片文件。")
                if not Path(image_path).exists():
                    raise RuntimeError(f"图片文件不存在: {image_path}")
                static_image = load_image_bgr(image_path)
                if static_image is None:
                    raise RuntimeError("图片读取失败，请确认是 JPG/PNG/BMP/WebP 等 OpenCV 支持格式。")
                self.log.emit(f"图片文件已打开: {image_path}")

            elif source in SCREEN_SOURCES:
                sct = mss.mss()
                # Validate the requested region once; errors in later frames are handled gracefully.
                _ = self._monitor_region(sct)
                self.log.emit("屏幕/窗口采集已初始化。")
            else:
                raise RuntimeError(f"未知来源类型: {source}")

            self.status.emit("正在打开虚拟摄像头后端...")
            camera = self._open_virtual_camera(width, height, fps)
            device_name = getattr(camera, "device", "OBS/Unity virtual camera")
            self.status.emit(f"正在输出到虚拟摄像头: {device_name}")

            while not self._stop_event.is_set():
                try:
                    if source == "本地视频":
                        assert video_cap is not None
                        ok, frame_bgr = video_cap.read()
                        if not ok or frame_bgr is None:
                            # Loop the video for continuous camera output.
                            video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            ok, frame_bgr = video_cap.read()
                        if not ok or frame_bgr is None:
                            raise RuntimeError("视频读取到末尾后仍无法重新读取第一帧。")
                    elif source == "本地图片":
                        assert static_image is not None
                        frame_bgr = static_image.copy()
                    else:
                        assert sct is not None
                        frame_bgr = self._capture_screen_frame(sct)

                    last_capture_error = ""
                except Exception as exc:
                    now = time.time()
                    msg = str(exc)
                    if msg != last_capture_error or (now - last_error_time) > 2.0:
                        last_capture_error = msg
                        last_error_time = now
                        self.error.emit(f"采集异常: {msg}")
                        LOGGER.warning("Capture error: %s", msg)
                    frame_bgr = placeholder_bgr(width, height, "Capture Error", msg)

                output_rgb = prepare_output_rgb(frame_bgr, width, height, fill_mode, mirror, vertical_flip)
                camera.send(output_rgb)
                if frame_index % preview_every == 0:
                    self.frame_ready.emit(output_rgb.copy())
                frame_index += 1
                camera.sleep_until_next_frame()

        except Exception as exc:
            details = traceback.format_exc()
            LOGGER.error("Worker failed: %s\n%s", exc, details)
            self.error.emit(f"输出启动失败: {exc}")
            self.status.emit("输出失败")
        finally:
            self.status.emit("正在释放资源...")
            try:
                if video_cap is not None:
                    video_cap.release()
                    self.log.emit("视频文件已释放。")
            except Exception:
                LOGGER.exception("Failed to release video capture")
            try:
                if camera is not None:
                    if hasattr(camera, "close"):
                        camera.close()
                    self.log.emit("虚拟摄像头已关闭。")
            except Exception:
                LOGGER.exception("Failed to close virtual camera")
            try:
                if sct is not None:
                    sct.close()
            except Exception:
                LOGGER.exception("Failed to close mss")
            self.status.emit("已停止")


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} MVP")
        self.resize(1180, 760)
        self.config = load_config()
        self.worker: Optional[CaptureWorker] = None

        self._build_ui()
        self.refresh_monitors()
        self.refresh_windows()
        self.apply_config_to_ui(self.config)
        self.update_source_controls()
        self.append_log(f"程序启动。配置文件: {CONFIG_PATH}")
        self.append_log(f"日志文件: {LOG_PATH}")
        if PYVIRTUALCAM_IMPORT_ERROR:
            self.append_log(f"pyvirtualcam 导入失败: {PYVIRTUALCAM_IMPORT_ERROR}")
        if WIN32_IMPORT_ERROR:
            self.append_log(f"pywin32/win32gui 导入失败: {WIN32_IMPORT_ERROR}")

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        controls = QWidget(central)
        controls.setMaximumWidth(430)
        controls_layout = QVBoxLayout(controls)
        root.addWidget(controls)

        base_group = QGroupBox("基础设置", controls)
        base_form = QFormLayout(base_group)
        controls_layout.addWidget(base_group)

        self.source_combo = QComboBox(base_group)
        self.source_combo.addItems(["全屏", "指定显示器", "指定窗口", "指定区域", "本地视频", "本地图片"])
        self.source_combo.currentTextChanged.connect(self.update_source_controls)
        base_form.addRow("来源类型", self.source_combo)

        self.resolution_combo = QComboBox(base_group)
        self.resolution_combo.addItem("720p (1280x720)", "1280x720")
        self.resolution_combo.addItem("1080p (1920x1080)", "1920x1080")
        base_form.addRow("分辨率", self.resolution_combo)

        self.fps_combo = QComboBox(base_group)
        self.fps_combo.addItem("15 FPS", 15)
        self.fps_combo.addItem("30 FPS", 30)
        base_form.addRow("帧率", self.fps_combo)

        self.fill_combo = QComboBox(base_group)
        self.fill_combo.addItems(["适应", "裁剪填充"])
        base_form.addRow("填充模式", self.fill_combo)

        self.mirror_checkbox = QCheckBox("水平镜像", base_group)
        base_form.addRow("镜像", self.mirror_checkbox)

        self.vertical_flip_checkbox = QCheckBox("垂直翻转", base_group)
        base_form.addRow("翻转", self.vertical_flip_checkbox)

        self.always_on_top_checkbox = QCheckBox("窗口置顶", base_group)
        self.always_on_top_checkbox.toggled.connect(self.set_always_on_top)
        base_form.addRow("窗口", self.always_on_top_checkbox)

        self.monitor_group = QGroupBox("显示器来源", controls)
        monitor_form = QFormLayout(self.monitor_group)
        self.monitor_combo = QComboBox(self.monitor_group)
        monitor_form.addRow("指定显示器", self.monitor_combo)
        controls_layout.addWidget(self.monitor_group)

        self.window_group = QGroupBox("窗口来源", controls)
        window_layout = QVBoxLayout(self.window_group)
        self.window_combo = QComboBox(self.window_group)
        self.window_combo.setMinimumWidth(340)
        window_layout.addWidget(self.window_combo)
        controls_layout.addWidget(self.window_group)

        self.region_group = QGroupBox("区域来源", controls)
        region_grid = QGridLayout(self.region_group)
        self.region_left_spin = QSpinBox(self.region_group)
        self.region_top_spin = QSpinBox(self.region_group)
        self.region_width_spin = QSpinBox(self.region_group)
        self.region_height_spin = QSpinBox(self.region_group)
        for spin in [self.region_left_spin, self.region_top_spin]:
            spin.setRange(-100000, 100000)
        for spin in [self.region_width_spin, self.region_height_spin]:
            spin.setRange(1, 100000)
        self.region_width_spin.setValue(1280)
        self.region_height_spin.setValue(720)
        region_grid.addWidget(QLabel("Left"), 0, 0)
        region_grid.addWidget(self.region_left_spin, 0, 1)
        region_grid.addWidget(QLabel("Top"), 0, 2)
        region_grid.addWidget(self.region_top_spin, 0, 3)
        region_grid.addWidget(QLabel("Width"), 1, 0)
        region_grid.addWidget(self.region_width_spin, 1, 1)
        region_grid.addWidget(QLabel("Height"), 1, 2)
        region_grid.addWidget(self.region_height_spin, 1, 3)
        controls_layout.addWidget(self.region_group)

        self.video_group = QGroupBox("本地视频", controls)
        video_layout = QHBoxLayout(self.video_group)
        self.video_path_edit = QLineEdit(self.video_group)
        self.video_browse_btn = QPushButton("选择...", self.video_group)
        self.video_browse_btn.clicked.connect(self.browse_video)
        video_layout.addWidget(self.video_path_edit)
        video_layout.addWidget(self.video_browse_btn)
        controls_layout.addWidget(self.video_group)

        self.image_group = QGroupBox("本地图片", controls)
        image_layout = QHBoxLayout(self.image_group)
        self.image_path_edit = QLineEdit(self.image_group)
        self.image_browse_btn = QPushButton("选择...", self.image_group)
        self.image_browse_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(self.image_path_edit)
        image_layout.addWidget(self.image_browse_btn)
        controls_layout.addWidget(self.image_group)

        button_group = QGroupBox("操作", controls)
        button_layout = QGridLayout(button_group)
        self.refresh_windows_btn = QPushButton("刷新窗口列表", button_group)
        self.refresh_windows_btn.clicked.connect(self.refresh_windows)
        self.start_btn = QPushButton("开始虚拟摄像头", button_group)
        self.start_btn.clicked.connect(self.start_output)
        self.stop_btn = QPushButton("停止虚拟摄像头", button_group)
        self.stop_btn.clicked.connect(self.stop_output)
        self.stop_btn.setEnabled(False)
        self.save_btn = QPushButton("保存配置", button_group)
        self.save_btn.clicked.connect(lambda: self.save_current_config(show_message=True))
        button_layout.addWidget(self.refresh_windows_btn, 0, 0)
        button_layout.addWidget(self.save_btn, 0, 1)
        button_layout.addWidget(self.start_btn, 1, 0)
        button_layout.addWidget(self.stop_btn, 1, 1)
        controls_layout.addWidget(button_group)

        self.status_label = QLabel("状态：未启动", controls)
        self.status_label.setWordWrap(True)
        controls_layout.addWidget(self.status_label)
        controls_layout.addStretch(1)

        right = QWidget(central)
        right_layout = QVBoxLayout(right)
        root.addWidget(right, stretch=1)

        preview_group = QGroupBox("实时预览", right)
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel("预览未启动", preview_group)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(QSize(640, 360))
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview_label.setStyleSheet("QLabel { background: #111; color: #ddd; border: 1px solid #333; }")
        preview_layout.addWidget(self.preview_label)
        right_layout.addWidget(preview_group, stretch=4)

        log_group = QGroupBox("日志", right)
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit(log_group)
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        log_layout.addWidget(self.log_text)
        right_layout.addWidget(log_group, stretch=2)

    def refresh_monitors(self) -> None:
        current = self.monitor_combo.currentData()
        self.monitor_combo.clear()
        try:
            with mss.mss() as sct:
                for idx, mon in enumerate(sct.monitors[1:], start=1):
                    text = f"显示器 {idx}: {mon['width']}x{mon['height']} @ ({mon['left']}, {mon['top']})"
                    self.monitor_combo.addItem(text, idx)
        except Exception as exc:
            self.append_log(f"刷新显示器失败: {exc}")
        if self.monitor_combo.count() == 0:
            self.monitor_combo.addItem("未检测到显示器", 1)
        if current is not None:
            self.set_combo_by_data(self.monitor_combo, current)

    def refresh_windows(self) -> None:
        current_title = self.current_selected_window_title()
        self.window_combo.clear()
        windows = enum_windows()
        if not windows:
            self.window_combo.addItem("未找到可见窗口 / pywin32 不可用", 0)
            if WIN32_IMPORT_ERROR:
                self.append_log(f"刷新窗口失败: {WIN32_IMPORT_ERROR}")
            return
        for item in windows:
            left, top, right, bottom = item.rect
            size_text = f"{right - left}x{bottom - top}"
            display = f"{item.title}  [{size_text}]"
            self.window_combo.addItem(display, item.hwnd)
        if current_title:
            self.select_window_by_title(current_title)
        self.append_log(f"窗口列表已刷新，共 {len(windows)} 个窗口。")

    def current_selected_window_title(self) -> str:
        hwnd = int(self.window_combo.currentData() or 0)
        if hwnd and win32gui is not None and win32gui.IsWindow(hwnd):
            return win32gui.GetWindowText(hwnd).strip()
        text = self.window_combo.currentText()
        # Strip the appended size marker when possible.
        return text.split("  [", 1)[0].strip()

    def select_window_by_title(self, title: str) -> None:
        if not title:
            return
        for idx in range(self.window_combo.count()):
            text = self.window_combo.itemText(idx).split("  [", 1)[0].strip()
            if text == title or title in text:
                self.window_combo.setCurrentIndex(idx)
                return

    @staticmethod
    def set_combo_by_data(combo: QComboBox, data: Any) -> None:
        for idx in range(combo.count()):
            if combo.itemData(idx) == data:
                combo.setCurrentIndex(idx)
                return

    @staticmethod
    def set_combo_by_text(combo: QComboBox, text: str) -> None:
        for idx in range(combo.count()):
            if combo.itemText(idx) == text:
                combo.setCurrentIndex(idx)
                return

    def apply_config_to_ui(self, config: Dict[str, Any]) -> None:
        self.set_combo_by_text(self.source_combo, str(config.get("source_type", "全屏")))
        self.set_combo_by_data(self.monitor_combo, int(config.get("monitor_index", 1) or 1))
        self.select_window_by_title(str(config.get("window_title", "")))
        self.set_combo_by_data(self.resolution_combo, str(config.get("resolution", "1280x720")))
        self.set_combo_by_data(self.fps_combo, int(config.get("fps", 30) or 30))
        self.set_combo_by_text(self.fill_combo, str(config.get("fill_mode", "适应")))
        self.mirror_checkbox.setChecked(bool(config.get("mirror", False)))
        self.vertical_flip_checkbox.setChecked(bool(config.get("vertical_flip", False)))
        self.always_on_top_checkbox.setChecked(bool(config.get("always_on_top", False)))
        self.video_path_edit.setText(str(config.get("video_path", "")))
        self.image_path_edit.setText(str(config.get("image_path", "")))
        region = config.get("region", {}) or {}
        self.region_left_spin.setValue(int(region.get("left", 0)))
        self.region_top_spin.setValue(int(region.get("top", 0)))
        self.region_width_spin.setValue(max(1, int(region.get("width", 1280))))
        self.region_height_spin.setValue(max(1, int(region.get("height", 720))))

    def collect_config_from_ui(self) -> Dict[str, Any]:
        hwnd = int(self.window_combo.currentData() or 0)
        window_title = self.current_selected_window_title()
        return {
            "source_type": self.source_combo.currentText(),
            "monitor_index": int(self.monitor_combo.currentData() or 1),
            "window_hwnd": hwnd,
            "window_title": window_title,
            "video_path": self.video_path_edit.text().strip(),
            "image_path": self.image_path_edit.text().strip(),
            "region": {
                "left": int(self.region_left_spin.value()),
                "top": int(self.region_top_spin.value()),
                "width": int(self.region_width_spin.value()),
                "height": int(self.region_height_spin.value()),
            },
            "resolution": str(self.resolution_combo.currentData() or "1280x720"),
            "fps": int(self.fps_combo.currentData() or 30),
            "mirror": bool(self.mirror_checkbox.isChecked()),
            "vertical_flip": bool(self.vertical_flip_checkbox.isChecked()),
            "fill_mode": self.fill_combo.currentText(),
            "always_on_top": bool(self.always_on_top_checkbox.isChecked()),
        }

    def update_source_controls(self) -> None:
        source = self.source_combo.currentText()
        self.monitor_group.setVisible(source == "指定显示器")
        self.window_group.setVisible(source == "指定窗口")
        self.region_group.setVisible(source == "指定区域")
        self.video_group.setVisible(source == "本地视频")
        self.image_group.setVisible(source == "本地图片")

    def set_always_on_top(self, checked: bool) -> None:
        flags = self.windowFlags()
        if checked:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()

    def browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            "Video Files (*.mp4 *.mov *.avi *.mkv *.wmv *.m4v);;All Files (*.*)",
        )
        if path:
            self.video_path_edit.setText(path)

    def browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片文件",
            "",
            "Image Files (*.jpg *.jpeg *.png *.bmp *.webp);;All Files (*.*)",
        )
        if path:
            self.image_path_edit.setText(path)

    def save_current_config(self, show_message: bool = False) -> None:
        config = self.collect_config_from_ui()
        # hwnd is runtime-only and may be invalid next launch; keep title for persistence.
        config_to_save = dict(config)
        config_to_save.pop("window_hwnd", None)
        try:
            save_config(config_to_save)
            self.config = config_to_save
            self.append_log("配置已保存。")
            if show_message:
                QMessageBox.information(self, APP_NAME, f"配置已保存到:\n{CONFIG_PATH}")
        except Exception as exc:
            self.append_log(f"保存配置失败: {exc}")
            if show_message:
                QMessageBox.critical(self, APP_NAME, f"保存配置失败:\n{exc}")

    def validate_before_start(self, config: Dict[str, Any]) -> bool:
        source = config.get("source_type")
        if source == "本地视频" and not config.get("video_path"):
            QMessageBox.warning(self, APP_NAME, "请先选择本地视频文件。")
            return False
        if source == "本地图片" and not config.get("image_path"):
            QMessageBox.warning(self, APP_NAME, "请先选择本地图片文件。")
            return False
        if source == "指定窗口" and not config.get("window_hwnd") and not config.get("window_title"):
            QMessageBox.warning(self, APP_NAME, "请先刷新并选择一个窗口。")
            return False
        return True

    def start_output(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, APP_NAME, "虚拟摄像头已经在运行。")
            return

        config = self.collect_config_from_ui()
        if not self.validate_before_start(config):
            return

        self.save_current_config(show_message=False)
        self.preview_label.setText("正在启动...")
        self.status_label.setText("状态：正在启动")
        self.append_log("开始启动虚拟摄像头输出。")

        self.worker = CaptureWorker(config, self)
        self.worker.frame_ready.connect(self.update_preview)
        self.worker.status.connect(self.update_status)
        self.worker.log.connect(self.append_log)
        self.worker.error.connect(self.handle_worker_error)
        self.worker.finished.connect(self.worker_finished)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker.start()

    def stop_output(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            self.status_label.setText("状态：未启动")
            self.stop_btn.setEnabled(False)
            self.start_btn.setEnabled(True)
            return
        self.append_log("正在停止虚拟摄像头输出...")
        self.status_label.setText("状态：正在停止")
        self.worker.stop()
        if not self.worker.wait(3500):
            self.append_log("停止等待超时：线程可能正在等待底层设备释放。请稍后重试或关闭程序。")

    def worker_finished(self) -> None:
        self.append_log("后台线程已退出。")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("状态：已停止")

    def update_status(self, text: str) -> None:
        self.status_label.setText(f"状态：{text}")
        if text:
            self.append_log(text)

    def handle_worker_error(self, text: str) -> None:
        self.append_log(text)
        # Startup failures are important enough to show; repeated capture errors stay in logs/status.
        if text.startswith("输出启动失败"):
            QMessageBox.critical(self, APP_NAME, text)

    def update_preview(self, rgb_frame: np.ndarray) -> None:
        try:
            if rgb_frame is None or rgb_frame.size == 0:
                return
            rgb_frame = np.ascontiguousarray(rgb_frame)
            h, w, ch = rgb_frame.shape
            bytes_per_line = ch * w
            qimg = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
        except Exception as exc:
            self.append_log(f"更新预览失败: {exc}")

    def append_log(self, message: str) -> None:
        message = str(message)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        LOGGER.info(message)

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self.save_current_config(show_message=False)
            if self.worker is not None and self.worker.isRunning():
                self.worker.stop()
                self.worker.wait(3000)
        finally:
            event.accept()


def main() -> int:
    # High DPI friendliness on Windows. PySide6 generally handles this, but the
    # environment variable helps older Qt builds.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
