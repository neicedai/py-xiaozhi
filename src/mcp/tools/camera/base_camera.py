"""Base camera implementation with persistent capture support."""

import threading
from abc import ABC, abstractmethod
from typing import Dict, Optional

import cv2

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class BaseCamera(ABC):
    """基础摄像头类，提供持久化的摄像头连接能力."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.jpeg_data = {"buf": b"", "len": 0}

        # 摄像头配置
        self._refresh_camera_settings()

        # 持久化视频流
        self._cap: Optional[cv2.VideoCapture] = None
        self._cap_lock = threading.Lock()
        self._active_camera_index: Optional[int] = None
        self._active_frame_width: Optional[int] = None
        self._active_frame_height: Optional[int] = None
        self._active_fps: Optional[int] = None
        self._status: str = "摄像头: 未初始化"

    def _refresh_camera_settings(self):
        config = ConfigManager.get_instance()
        self.camera_index = config.get_config("CAMERA.camera_index", 0)
        self.frame_width = config.get_config("CAMERA.frame_width", 640)
        self.frame_height = config.get_config("CAMERA.frame_height", 480)
        self.fps = config.get_config("CAMERA.fps", 30)

    def refresh_settings(self):
        self._refresh_camera_settings()

    # ------------------------------------------------------------------
    # 摄像头管理
    # ------------------------------------------------------------------
    def initialize_capture(self, force_open: bool = False) -> bool:
        """Ensure the capture device is opened and ready for use."""

        return self._ensure_capture_ready(force_open=force_open)

    def _ensure_capture_ready(self, force_open: bool = False) -> bool:
        with self._cap_lock:
            if force_open or self._capture_needs_reopen():
                return self._open_capture_locked()
            return self._cap is not None and self._cap.isOpened()

    def _capture_needs_reopen(self) -> bool:
        if self._cap is None:
            return True
        if not self._cap.isOpened():
            return True
        return (
            self._active_camera_index != self.camera_index
            or self._active_frame_width != self.frame_width
            or self._active_frame_height != self.frame_height
            or self._active_fps != self.fps
        )

    def _open_capture_locked(self) -> bool:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        logger.info(
            "Opening camera: index=%s, width=%s, height=%s, fps=%s",
            self.camera_index,
            self.frame_width,
            self.frame_height,
            self.fps,
        )

        cap = cv2.VideoCapture(self.camera_index)
        if not cap or not cap.isOpened():
            self._status = f"摄像头: 打开失败(索引 {self.camera_index})"
            logger.error("Cannot open camera at index %s", self.camera_index)
            return False

        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            if self.fps:
                cap.set(cv2.CAP_PROP_FPS, self.fps)
        except Exception as exc:
            logger.warning("Failed to apply camera settings: %s", exc)

        self._cap = cap
        self._active_camera_index = self.camera_index
        self._active_frame_width = self.frame_width
        self._active_frame_height = self.frame_height
        self._active_fps = self.fps
        self._status = "摄像头: 已开启"
        logger.info("Camera opened successfully")
        return True

    def read_frame(self):
        """Read a single frame from the persistent capture device."""

        if not self._ensure_capture_ready():
            return None

        with self._cap_lock:
            if not self._cap or not self._cap.isOpened():
                self._status = "摄像头: 未连接"
                return None

            ret, frame = self._cap.read()

        if not ret or frame is None:
            self._status = "摄像头: 无法读取画面"
            logger.error("Failed to read frame from camera")
            return None

        self._status = "摄像头: 运行中"
        return frame

    def read_preview_frame(self):
        """Read a frame for preview purposes."""

        return self.read_frame()

    def release(self):
        with self._cap_lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
        self._active_camera_index = None
        self._active_frame_width = None
        self._active_frame_height = None
        self._active_fps = None
        self._status = "摄像头: 已关闭"

    def get_status(self) -> str:
        return self._status

    def is_active(self) -> bool:
        with self._cap_lock:
            return bool(self._cap and self._cap.isOpened())

    # ------------------------------------------------------------------
    # 子类接口
    # ------------------------------------------------------------------
    @abstractmethod
    def capture(self) -> bool:
        """捕获图像."""

    @abstractmethod
    def analyze(self, question: str) -> str:
        """分析图像."""

    # ------------------------------------------------------------------
    # JPEG 数据管理
    # ------------------------------------------------------------------
    def get_jpeg_data(self) -> Dict[str, any]:
        return self.jpeg_data

    def set_jpeg_data(self, data_bytes: bytes):
        self.jpeg_data["buf"] = data_bytes
        self.jpeg_data["len"] = len(data_bytes)
