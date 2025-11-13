"""在 FastAPI 内部直接运行应用程序的运行时管理."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from fastapi import HTTPException, status

from main import start_app
from src.application import Application
from src.constants.constants import AbortReason, DeviceState
from src.display.web_display import WebDisplay
from src.mcp.tools.camera import (
    get_camera_status,
    initialize_camera,
    is_camera_active,
    read_camera_preview_frame,
    shutdown_camera,
    take_photo,
)
from src.utils.logging_config import setup_logging
from src.webapp.audio import web_audio_bridge
from src.webapp.state import web_ui_registry

LOGGER = logging.getLogger(__name__)


class _WebLogHandler(logging.Handler):
    """将日志条目保存到运行时的内存缓冲."""

    def __init__(self, runtime: "WebRuntime") -> None:
        super().__init__(level=logging.INFO)
        self.runtime = runtime
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(name)s] - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - 日志保障
        try:
            message = record.getMessage()
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "name": record.name,
                "message": message,
            }
            loop = self.runtime.loop
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(self.runtime.append_log, entry)
        except Exception:
            # 日志处理不可影响主流程
            pass


class WebRuntime:
    """管理应用主循环并向 FastAPI 暴露操作接口."""

    def __init__(
        self,
        *,
        mode: str = "web",
        protocol: str = "websocket",
        skip_activation: bool = False,
    ) -> None:
        self.mode = mode
        self.protocol = protocol
        self.skip_activation = skip_activation

        self._app_task: Optional[asyncio.Task] = None
        self._app: Optional[Application] = None
        self._display: Optional[WebDisplay] = None
        self._start_lock = asyncio.Lock()
        self._running = False
        self._exit_code: Optional[int] = None
        self._status_message = "服务未启动"

        self._log_entries: Deque[Dict[str, Any]] = deque(maxlen=2000)
        self._log_counter = 0
        self._log_handler: Optional[_WebLogHandler] = None
        self._log_lock = asyncio.Lock()

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._camera_lock = asyncio.Lock()
        self._audio_bridge = web_audio_bridge

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------
    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self._app_task and not self._app_task.done():
                return

            self.loop = asyncio.get_running_loop()
            setup_logging()
            self._install_log_handler()

            self._status_message = "应用启动中"
            self._running = True
            self._app_task = asyncio.create_task(self._run_app(), name="xiaozhi-app")

            try:
                self._display = await web_ui_registry.wait_for_display(timeout=120)
                self._status_message = "应用运行中"
            except Exception as exc:  # pragma: no cover - 启动异常
                self._status_message = f"等待 UI 初始化失败: {exc}"
                LOGGER.exception("Failed to wait for web display", exc_info=exc)
                raise

    async def _run_app(self) -> None:
        exit_code = 1
        try:
            self._app = Application.get_instance()
            exit_code = await start_app(
                mode=self.mode, protocol=self.protocol, skip_activation=self.skip_activation
            )
        except Exception as exc:  # pragma: no cover - 启动失败
            LOGGER.exception("XiaoZhi 应用运行异常", exc_info=exc)
            raise
        finally:
            self._exit_code = exit_code
            self._running = False
            if exit_code == 0:
                self._status_message = "应用已停止"
            else:
                self._status_message = f"应用异常退出 (code={exit_code})"

    async def shutdown(self) -> None:
        if self._app and getattr(self._app, "running", False):
            try:
                await self._app.shutdown()
            except Exception:  # pragma: no cover - 防御
                LOGGER.exception("Shutdown application failed")
        if self._app_task:
            self._app_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._app_task
        self._remove_log_handler()

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._running and self._app_task is not None and not self._app_task.done()

    async def get_status(self) -> Dict[str, Any]:
        app_state = {}
        ui_state = {}
        if self._app:
            snapshot = self._app.get_state_snapshot()
            app_state = {
                "deviceState": snapshot.get("device_state"),
                "listeningMode": snapshot.get("listening_mode"),
                "keepListening": snapshot.get("keep_listening"),
                "audioOpened": snapshot.get("audio_opened"),
            }
        if self._display:
            ui_state = self._display.get_state_snapshot()

        try:
            camera_status = await asyncio.to_thread(get_camera_status)
            camera_active = await asyncio.to_thread(is_camera_active)
        except Exception:  # pragma: no cover - 防御
            camera_status = "摄像头状态未知"
            camera_active = False

        audio_status = await self._audio_bridge.get_status()

        return {
            "runtime": {
                "running": self.is_running(),
                "exitCode": self._exit_code,
                "message": self._status_message,
                "mode": self.mode,
                "protocol": self.protocol,
                "skipActivation": self.skip_activation,
            },
            "application": app_state,
            "ui": ui_state,
            "camera": {
                "status": camera_status,
                "active": camera_active,
            },
            "webAudio": audio_status,
        }

    # ------------------------------------------------------------------
    # 会话与语音控制
    # ------------------------------------------------------------------
    async def start_manual_listening(self) -> None:
        app = self._require_app()
        await app.start_listening_manual()

    async def stop_manual_listening(self) -> None:
        app = self._require_app()
        await app.stop_listening_manual()

    async def start_auto_conversation(self) -> None:
        app = self._require_app()
        await app.start_auto_conversation()

    async def stop_conversation(self) -> None:
        app = self._require_app()
        app.keep_listening = False
        if app.protocol:
            try:
                await app.protocol.send_stop_listening()
            except Exception:  # pragma: no cover - 防御
                LOGGER.debug("Failed to send stop listening", exc_info=True)
        await app.set_device_state(DeviceState.IDLE)

    async def abort_speaking(self) -> None:
        app = self._require_app()
        if app.is_speaking():
            await app.abort_speaking(AbortReason.USER_INTERRUPTION)

    async def send_text(self, text: str) -> None:
        if not text or not text.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "文本不能为空")
        if not self._display:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "UI 尚未就绪")
        await self._display.trigger_send_text(text.strip())

    async def send_wake_word(self, text: str) -> None:
        app = self._require_app()
        text = (text or "").strip() or "小智小智"
        if not await app.connect_protocol():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "协议连接失败")
        await app.protocol.send_wake_word_detected(text)

    # ------------------------------------------------------------------
    # 摄像头
    # ------------------------------------------------------------------
    async def open_camera(self, force: bool = False) -> None:
        async with self._camera_lock:
            success = await asyncio.to_thread(self._initialize_camera_sync, force)
            if not success:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "未检测到可用的摄像头设备"
                )

    async def close_camera(self) -> None:
        async with self._camera_lock:
            await asyncio.to_thread(shutdown_camera)

    async def capture_photo(self, question: str) -> str:
        async with self._camera_lock:
            return await asyncio.to_thread(take_photo, {"question": question})

    async def get_camera_preview(self) -> Optional[bytes]:
        frame = await asyncio.to_thread(read_camera_preview_frame)
        if frame is None:
            return None
        import cv2  # 延迟导入，避免未使用摄像头时加载

        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            return None
        return buffer.tobytes()

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    def append_log(self, entry: Dict[str, Any]) -> None:
        self._log_counter += 1
        entry = dict(entry)
        entry["id"] = self._log_counter
        self._log_entries.append(entry)

    async def get_logs(self, since: Optional[int] = None) -> List[Dict[str, Any]]:
        async with self._log_lock:
            if since is None:
                return list(self._log_entries)
            return [entry for entry in self._log_entries if entry["id"] > since]

    async def reset_logs(self) -> None:
        async with self._log_lock:
            self._log_entries.clear()
            self._log_counter = 0

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _install_log_handler(self) -> None:
        if self._log_handler is not None:
            return
        handler = _WebLogHandler(self)
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

    def _remove_log_handler(self) -> None:
        if self._log_handler is None:
            return
        logging.getLogger().removeHandler(self._log_handler)
        self._log_handler = None

    @staticmethod
    def _initialize_camera_sync(force: bool) -> bool:
        try:
            initialize_camera(force_reopen=force)
        except TypeError:
            # 兼容旧的函数签名（仅用于防御性处理）
            initialize_camera(force)
        except Exception:
            LOGGER.debug("Failed to initialize camera", exc_info=True)
            return False
        try:
            return is_camera_active()
        except Exception:
            LOGGER.debug("Camera active check failed", exc_info=True)
            return False

    def _require_app(self) -> Application:
        if not self._app:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "应用尚未启动")
        return self._app


__all__ = ["WebRuntime"]
