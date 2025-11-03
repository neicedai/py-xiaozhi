"""
Camera tool for MCP.
"""

from typing import Optional

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

from .normal_camera import NormalCamera
from .vl_camera import VLCamera

logger = get_logger(__name__)


_camera_initialized = False
_camera_selection_logged: Optional[str] = None


def get_camera_instance():
    """
    根据配置返回对应的摄像头实现.
    """
    config = ConfigManager.get_instance()

    # 检查是否配置了智普AI
    vl_key = config.get_config("CAMERA.VLapi_key")
    vl_url = config.get_config("CAMERA.Local_VL_url")

    global _camera_selection_logged

    if vl_key and vl_url:
        if _camera_selection_logged != "vl":
            logger.info(f"Initializing VL Camera with URL: {vl_url}")
            _camera_selection_logged = "vl"
        return VLCamera.get_instance()

    if _camera_selection_logged != "normal":
        logger.info("VL configuration not found, using normal Camera implementation")
        _camera_selection_logged = "normal"
    return NormalCamera.get_instance()


def initialize_camera(force_reopen: bool = False):
    """Initialize and keep the camera connection alive."""

    global _camera_initialized
    camera = get_camera_instance()
    if force_reopen or not _camera_initialized:
        camera.initialize_capture(force_open=True)
        _camera_initialized = True
    else:
        camera.initialize_capture()
    return camera


def get_camera_status() -> str:
    """Return a human readable camera status."""

    return get_camera_instance().get_status()


def read_camera_preview_frame():
    """Read a frame for UI preview usage without reopening closed cameras."""

    global _camera_initialized

    if not _camera_initialized:
        return None

    camera = get_camera_instance()

    if not camera.is_active():
        if camera.initialize_capture(force_open=True):
            return camera.read_preview_frame()
        return None

    return camera.read_preview_frame()


def shutdown_camera():
    """Release the camera device and reset initialization flag."""

    global _camera_initialized
    camera = get_camera_instance()
    camera.release()
    _camera_initialized = False


def is_camera_active() -> bool:
    """Return True when the camera capture device is active."""

    try:
        return get_camera_instance().is_active()
    except Exception:
        return False


def take_photo(arguments: dict) -> str:
    """
    拍照并分析的工具函数.
    """
    camera = initialize_camera()
    logger.info(f"Using camera implementation: {camera.__class__.__name__}")

    question = arguments.get("question", "")
    logger.info(f"Taking photo with question: {question}")

    # 拍照
    success = camera.capture()
    if not success:
        logger.error("Failed to capture photo")
        return '{"success": false, "message": "Failed to capture photo"}'

    # 分析图片
    logger.info("Photo captured, starting analysis...")
    return camera.analyze(question)
