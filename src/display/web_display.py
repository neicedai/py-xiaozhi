"""基于浏览器的显示实现."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.display.base_display import BaseDisplay
from src.utils.logging_config import get_logger
from src.webapp.state import web_ui_registry

CallbackType = Optional[Callable[..., Any]]


class WebDisplay(BaseDisplay):
    """用于 Web 控制台的显示实现."""

    def __init__(self) -> None:
        super().__init__()
        self.logger = get_logger(__name__)
        self._press_callback: CallbackType = None
        self._release_callback: CallbackType = None
        self._mode_callback: CallbackType = None
        self._auto_callback: CallbackType = None
        self._abort_callback: CallbackType = None
        self._send_text_callback: CallbackType = None

        self._state_lock = asyncio.Lock()
        self._running = False
        self._state: Dict[str, Any] = {
            "statusText": "初始化中",
            "connected": False,
            "currentText": "",
            "emotion": "neutral",
            "buttonText": "",
            "updatedAt": None,
        }

    # ------------------------------------------------------------------
    # BaseDisplay 接口实现
    # ------------------------------------------------------------------
    async def set_callbacks(
        self,
        press_callback: CallbackType = None,
        release_callback: CallbackType = None,
        mode_callback: CallbackType = None,
        auto_callback: CallbackType = None,
        abort_callback: CallbackType = None,
        send_text_callback: CallbackType = None,
    ) -> None:
        self._press_callback = press_callback
        self._release_callback = release_callback
        self._mode_callback = mode_callback
        self._auto_callback = auto_callback
        self._abort_callback = abort_callback
        self._send_text_callback = send_text_callback

        # 注册自身以便运行时获取显示实例
        web_ui_registry.register_display(self)

    async def update_button_status(self, text: str) -> None:
        async with self._state_lock:
            self._state["buttonText"] = text or ""
            self._state["updatedAt"] = self._now()

    async def update_status(self, status: str, connected: bool) -> None:
        async with self._state_lock:
            self._state["statusText"] = status or ""
            self._state["connected"] = bool(connected)
            self._state["updatedAt"] = self._now()

    async def update_text(self, text: str) -> None:
        if text and text.strip():
            async with self._state_lock:
                self._state["currentText"] = text.strip()
                self._state["updatedAt"] = self._now()

    async def update_emotion(self, emotion_name: str) -> None:
        async with self._state_lock:
            if emotion_name:
                self._state["emotion"] = emotion_name
                self._state["updatedAt"] = self._now()

    async def start(self) -> None:
        self._running = True
        try:
            while self._running:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        self._running = False
        web_ui_registry.unregister_display(self)

    # ------------------------------------------------------------------
    # 对外辅助方法
    # ------------------------------------------------------------------
    def get_state_snapshot(self) -> Dict[str, Any]:
        """获取当前显示状态的副本."""

        snapshot = {
            "statusText": self._state["statusText"],
            "connected": self._state["connected"],
            "currentText": self._state["currentText"],
            "emotion": self._state["emotion"],
            "buttonText": self._state["buttonText"],
            "updatedAt": self._state["updatedAt"],
        }
        return snapshot

    async def trigger_press(self) -> None:
        await self._invoke(self._press_callback)

    async def trigger_release(self) -> None:
        await self._invoke(self._release_callback)

    async def trigger_auto(self) -> None:
        await self._invoke(self._auto_callback)

    async def trigger_abort(self) -> None:
        await self._invoke(self._abort_callback)

    async def trigger_send_text(self, text: str) -> None:
        if not text or not text.strip():
            return
        await self._invoke(self._send_text_callback, text.strip())

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    async def _invoke(self, callback: CallbackType, *args: Any) -> None:
        if callback is None:
            return
        try:
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            self.logger.exception("WebDisplay 回调执行失败")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["WebDisplay"]
