import asyncio
from typing import Any, Optional

from src.mcp.tools.camera import initialize_camera, is_camera_active, shutdown_camera
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class CameraControlPlugin(Plugin):
    """Provide simple voice-triggered camera controls."""

    name = "camera_control"

    _NEGATION_WORDS = ("不要", "别", "不想", "不准", "别再")

    def __init__(self) -> None:
        super().__init__()
        self.app: Optional[Any] = None

    async def setup(self, app: Any) -> None:
        self.app = app

    async def on_incoming_json(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        if str(message.get("type")).lower() != "stt":
            return

        text = str(message.get("text") or "").strip()
        if not text:
            return

        if await self._try_handle_command(text):
            logger.info("Processed voice camera command: %s", text)

    async def _try_handle_command(self, text: str) -> bool:
        if self._is_open_command(text):
            await self._toggle_camera(True)
            return True
        if self._is_close_command(text):
            await self._toggle_camera(False)
            return True
        return False

    async def _toggle_camera(self, enable: bool) -> None:
        loop = asyncio.get_running_loop()
        if enable:
            if is_camera_active():
                self._notify_user("摄像头已开启")
                return
            await loop.run_in_executor(
                None, lambda: initialize_camera(force_reopen=True)
            )
            if is_camera_active():
                self._notify_user("摄像头已开启")
            else:
                logger.warning("Voice command failed to open camera")
                self._notify_user("摄像头打开失败，请检查设备")
        else:
            if not is_camera_active():
                self._notify_user("摄像头已关闭")
                return
            await loop.run_in_executor(None, shutdown_camera)
            if is_camera_active():
                logger.warning("Voice command failed to close camera")
                self._notify_user("摄像头关闭失败，请稍后重试")
            else:
                self._notify_user("摄像头已关闭")

    def _notify_user(self, message: str) -> None:
        if not self.app or not hasattr(self.app, "set_chat_message"):
            return
        try:
            self.app.set_chat_message("assistant", message)
        except Exception:
            logger.debug(
                "Failed to send camera status message to UI", exc_info=True
            )

    def _normalize(self, text: str) -> str:
        normalized = text.lower()
        for ch in [" ", "，", "。", "！", "!", "?", "？", "、"]:
            normalized = normalized.replace(ch, "")
        return normalized

    def _has_negation_prefix(self, normalized: str, phrase: str) -> bool:
        idx = normalized.find(phrase)
        if idx == -1:
            return False
        prefix = normalized[:idx]
        return any(word in prefix[-4:] for word in self._NEGATION_WORDS)

    def _is_open_command(self, text: str) -> bool:
        normalized = self._normalize(text)
        phrases = ("打开摄像头", "开启摄像头", "启动摄像头")
        for phrase in phrases:
            if phrase in normalized and not self._has_negation_prefix(normalized, phrase):
                return True

        text_lower = text.lower()
        english_phrases = ("turn on the camera", "turn on camera", "open the camera", "open camera")
        for phrase in english_phrases:
            if phrase in text_lower and not self._has_english_negation(text_lower, phrase):
                return True
        return False

    def _is_close_command(self, text: str) -> bool:
        normalized = self._normalize(text)
        phrases = ("关闭摄像头", "关掉摄像头", "停用摄像头")
        for phrase in phrases:
            if phrase in normalized and not self._has_negation_prefix(normalized, phrase):
                return True

        text_lower = text.lower()
        english_phrases = ("turn off the camera", "turn off camera", "close the camera", "close camera")
        for phrase in english_phrases:
            if phrase in text_lower and not self._has_english_negation(text_lower, phrase):
                return True
        return False

    def _has_english_negation(self, text_lower: str, phrase: str) -> bool:
        idx = text_lower.find(phrase)
        if idx == -1:
            return False
        prefix = text_lower[:idx]
        return any(token in prefix[-10:] for token in ("don't", "do not", "not", "no"))
