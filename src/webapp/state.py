"""维护 Web UI 显示实例的注册表."""

from __future__ import annotations

import asyncio
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型提示
    from src.display.web_display import WebDisplay


class _WebUIRegistry:
    def __init__(self) -> None:
        self._display: Optional["WebDisplay"] = None
        self._event = asyncio.Event()

    def register_display(self, display: "WebDisplay") -> None:
        self._display = display
        if not self._event.is_set():
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(self._event.set)
            except RuntimeError:
                # 当没有运行的事件循环时直接设置
                self._event.set()

    def unregister_display(self, display: "WebDisplay") -> None:
        if self._display is display:
            self._display = None
            self._event = asyncio.Event()

    async def wait_for_display(self, timeout: float | None = None) -> "WebDisplay":
        if self._display is not None:
            return self._display
        await asyncio.wait_for(self._event.wait(), timeout=timeout)
        if self._display is None:
            raise RuntimeError("Web display not registered")
        return self._display

    def get_display(self) -> Optional["WebDisplay"]:
        return self._display


web_ui_registry = _WebUIRegistry()

__all__ = ["web_ui_registry"]
