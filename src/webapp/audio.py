"""浏览器端音频桥接管理."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Optional

from fastapi import WebSocket

from src.constants.constants import AudioConfig, DeviceState
from src.utils.logging_config import get_logger

LOGGER = get_logger(__name__)

MicrophoneHandler = Callable[[bytes], Awaitable[None]]


@dataclass
class _ClientState:
    websocket: WebSocket
    lock: asyncio.Lock
    streaming: bool = False

    def as_dict(self) -> Dict[str, bool]:
        return {"streaming": self.streaming}


class WebAudioBridge:
    """管理浏览器与后端之间的音频通道."""

    def __init__(self) -> None:
        self._clients: Dict[WebSocket, _ClientState] = {}
        self._clients_lock = asyncio.Lock()
        self._microphone_handler: Optional[MicrophoneHandler] = None
        self._last_microphone_at: Optional[datetime] = None
        self._last_speaker_at: Optional[datetime] = None
        self._last_device_state: str = DeviceState.IDLE

    # ------------------------------------------------------------------
    # 客户端管理
    # ------------------------------------------------------------------
    async def handle_client(self, websocket: WebSocket) -> None:
        """接入新的浏览器音频会话."""

        await websocket.accept()
        state = _ClientState(websocket=websocket, lock=asyncio.Lock())
        async with self._clients_lock:
            self._clients[websocket] = state
        LOGGER.info("浏览器音频客户端已连接：%s", websocket.client)

        try:
            await self._send_config(state)
            await self._send_device_state(state, self._last_device_state)

            while True:
                message = await websocket.receive()
                message_type = message.get("type")
                if message_type == "websocket.disconnect":
                    break

                if message.get("bytes") is not None:
                    await self._handle_microphone_frame(state, message["bytes"])
                    continue

                text = message.get("text")
                if text:
                    await self._handle_text_message(state, text)
        except Exception as exc:  # pragma: no cover - 防御
            LOGGER.debug("浏览器音频会话异常：%s", exc, exc_info=True)
        finally:
            await self._remove_client(websocket)
            LOGGER.info("浏览器音频客户端已断开：%s", websocket.client)

    async def _remove_client(self, websocket: WebSocket) -> None:
        async with self._clients_lock:
            if websocket in self._clients:
                del self._clients[websocket]

    async def _send_config(self, state: _ClientState) -> None:
        payload = {
            "type": "config",
            "inputSampleRate": AudioConfig.INPUT_SAMPLE_RATE,
            "outputSampleRate": AudioConfig.OUTPUT_SAMPLE_RATE,
            "frameSamples": AudioConfig.INPUT_FRAME_SIZE,
            "outputFrameSamples": AudioConfig.OUTPUT_FRAME_SIZE,
            "deviceState": self._last_device_state,
        }
        async with state.lock:
            await state.websocket.send_text(json.dumps(payload))

    async def _send_device_state(self, state: _ClientState, device_state: str) -> None:
        payload = {"type": "device_state", "state": device_state}
        async with state.lock:
            await state.websocket.send_text(json.dumps(payload))

    async def broadcast_device_state(self, device_state: str) -> None:
        self._last_device_state = device_state
        payload = json.dumps({"type": "device_state", "state": device_state})
        async with self._clients_lock:
            for state in list(self._clients.values()):
                try:
                    async with state.lock:
                        await state.websocket.send_text(payload)
                except Exception:  # pragma: no cover - 防御
                    LOGGER.debug("发送设备状态失败", exc_info=True)

    async def broadcast_speaker_audio(self, pcm_data: bytes) -> None:
        if not pcm_data:
            return
        self._last_speaker_at = datetime.now(timezone.utc)
        async with self._clients_lock:
            for state in list(self._clients.values()):
                try:
                    async with state.lock:
                        await state.websocket.send_bytes(pcm_data)
                except Exception:  # pragma: no cover - 防御
                    LOGGER.debug("发送扬声器音频失败", exc_info=True)

    async def _handle_text_message(self, state: _ClientState, text: str) -> None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            LOGGER.debug("忽略无法解析的音频消息: %s", text[:100])
            return

        msg_type = payload.get("type")
        if msg_type == "mic":
            streaming = bool(payload.get("active"))
            state.streaming = streaming
        elif msg_type == "ping":
            async with state.lock:
                await state.websocket.send_text(json.dumps({"type": "pong"}))
        elif msg_type == "notice":
            # 仅用于调试回显
            LOGGER.info("浏览器音频消息: %s", payload.get("message"))

    async def _handle_microphone_frame(self, state: _ClientState, data: bytes) -> None:
        if not data:
            return
        if self._microphone_handler is None:
            return
        if not state.streaming:
            return

        self._last_microphone_at = datetime.now(timezone.utc)
        try:
            await self._microphone_handler(data)
        except Exception:  # pragma: no cover - 防御
            LOGGER.exception("处理浏览器麦克风数据失败")

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_microphone_handler(self, handler: Optional[MicrophoneHandler]) -> None:
        self._microphone_handler = handler

    def clear_handlers(self) -> None:
        self._microphone_handler = None

    async def notify_device_state(self, device_state: str) -> None:
        await self.broadcast_device_state(device_state)

    async def get_status(self) -> Dict[str, object]:
        now = datetime.now(timezone.utc)
        mic_active = False
        if self._last_microphone_at:
            mic_active = (now - self._last_microphone_at).total_seconds() < 2.0
        speaker_active = False
        if self._last_speaker_at:
            speaker_active = (now - self._last_speaker_at).total_seconds() < 2.0
        async with self._clients_lock:
            clients_snapshot = list(self._clients.values())
        connected = bool(clients_snapshot)
        streaming = any(state.streaming for state in clients_snapshot)
        status_text = "未连接"
        if connected and streaming and mic_active:
            status_text = "已连接·麦克风传输中"
        elif connected:
            status_text = "已连接"

        return {
            "connected": connected,
            "microphoneStreaming": streaming,
            "microphoneActive": mic_active,
            "speakerActive": speaker_active,
            "lastMicrophoneAt": self._last_microphone_at.isoformat()
            if self._last_microphone_at
            else None,
            "lastSpeakerAt": self._last_speaker_at.isoformat()
            if self._last_speaker_at
            else None,
            "statusText": status_text,
            "inputSampleRate": AudioConfig.INPUT_SAMPLE_RATE,
            "outputSampleRate": AudioConfig.OUTPUT_SAMPLE_RATE,
            "frameSamples": AudioConfig.INPUT_FRAME_SIZE,
        }


web_audio_bridge = WebAudioBridge()

__all__ = ["web_audio_bridge", "WebAudioBridge"]
