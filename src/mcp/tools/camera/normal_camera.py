"""Normal camera implementation using remote API."""

import cv2
import requests

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

from .base_camera import BaseCamera
from .image_enhancement import enhance_frame_brightness

logger = get_logger(__name__)


class NormalCamera(BaseCamera):
    """
    普通摄像头实现，使用远程API进行分析.
    """

    _instance = None

    def __init__(self):
        """
        初始化普通摄像头.
        """
        super().__init__()
        self.explain_url = ""
        self.explain_token = ""
        # 在初始化阶段打开摄像头，保持持续连接
        self.initialize_capture(force_open=True)

    @classmethod
    def get_instance(cls):
        """
        获取单例实例.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_explain_url(self, url: str):
        """
        设置解释服务的URL.
        """
        self.explain_url = url
        logger.info(f"Vision service URL set to: {url}")

    def set_explain_token(self, token: str):
        """
        设置解释服务的token.
        """
        self.explain_token = token
        if token:
            logger.info("Vision service token has been set")

    def capture(self) -> bool:
        """
        捕获图像.
        """
        try:
            logger.info("Capturing frame from persistent camera stream...")

            # 重新加载配置，并在需要时重建连接
            self.refresh_settings()
            if not self.initialize_capture():
                return False

            frame = self.read_frame()
            if frame is None:
                return False

            # 提升画面亮度，增强低光场景的清晰度
            frame = enhance_frame_brightness(frame)

            # 获取原始图像尺寸
            height, width = frame.shape[:2]

            # 计算缩放比例，使最长边为320
            max_dim = max(height, width)
            scale = 320 / max_dim if max_dim > 320 else 1.0

            # 等比例缩放图像
            if scale < 1.0:
                new_width = int(width * scale)
                new_height = int(height * scale)
                frame = cv2.resize(
                    frame, (new_width, new_height), interpolation=cv2.INTER_AREA
                )

            # 直接将图像编码为JPEG字节流
            success, jpeg_data = cv2.imencode(".jpg", frame)

            if not success:
                logger.error("Failed to encode image to JPEG")
                return False

            # 保存字节数据
            self.set_jpeg_data(jpeg_data.tobytes())
            logger.info(
                f"Image captured successfully (size: {self.jpeg_data['len']} bytes)"
            )
            return True

        except Exception as e:
            logger.error(f"Exception during capture: {e}")
            return False

    def read_preview_frame(self):
        """读取用于预览的原始帧."""

        if not self.initialize_capture():
            return None
        return self.read_frame()

    def analyze(self, question: str) -> str:
        """
        分析图像.
        """
        if not self.explain_url:
            return '{"success": false, "message": "Image explain URL is not set"}'

        if not self.jpeg_data["buf"]:
            return '{"success": false, "message": "Camera buffer is empty"}'

        # 准备请求头
        headers = {
            "Device-Id": ConfigManager.get_instance().get_config(
                "SYSTEM_OPTIONS.DEVICE_ID"
            ),
            "Client-Id": ConfigManager.get_instance().get_config(
                "SYSTEM_OPTIONS.CLIENT_ID"
            ),
        }

        if self.explain_token:
            headers["Authorization"] = f"Bearer {self.explain_token}"

        # 准备文件数据
        files = {
            "question": (None, question),
            "file": ("camera.jpg", self.jpeg_data["buf"], "image/jpeg"),
        }

        try:
            # 发送请求
            response = requests.post(
                self.explain_url, headers=headers, files=files, timeout=10
            )

            # 检查响应状态
            if response.status_code != 200:
                error_msg = (
                    f"Failed to upload photo, status code: {response.status_code}"
                )
                logger.error(error_msg)
                return f'{{"success": false, "message": "{error_msg}"}}'

            # 记录响应
            logger.info(
                f"Explain image size={self.jpeg_data['len']}, "
                f"question={question}\n{response.text}"
            )
            return response.text

        except requests.RequestException as e:
            error_msg = f"Failed to connect to explain URL: {str(e)}"
            logger.error(error_msg)
            return f'{{"success": false, "message": "{error_msg}"}}'
