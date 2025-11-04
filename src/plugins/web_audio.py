"""面向浏览器的远程音频插件."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import opuslib

from src.constants.constants import AudioConfig, DeviceState, ListeningMode
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger
from src.webapp.audio import web_audio_bridge

LOGGER = get_logger(__name__)


class WebAudioPlugin(Plugin):
    """将浏览器音频与协议通道桥接."""

    name = "web-audio"

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self._encoder: Optional[opuslib.Encoder] = None
        self._decoder: Optional[opuslib.Decoder] = None
        self._buffer = bytearray()
        self._send_lock = asyncio.Lock()

    async def setup(self, app: Any) -> None:
        self.app = app
        try:
            self._encoder = opuslib.Encoder(
                AudioConfig.INPUT_SAMPLE_RATE,
                AudioConfig.CHANNELS,
                opuslib.APPLICATION_AUDIO,
            )
            self._decoder = opuslib.Decoder(
                AudioConfig.OUTPUT_SAMPLE_RATE,
                AudioConfig.CHANNELS,
            )
        except Exception as exc:  # pragma: no cover - 防御
            LOGGER.error("初始化 Opus 编解码器失败: %s", exc, exc_info=True)
            self._encoder = None
            self._decoder = None
        web_audio_bridge.set_microphone_handler(self._on_microphone_bytes)

    async def start(self) -> None:
        # 向浏览器广播当前设备状态
        try:
            device_state = getattr(self.app, "device_state", DeviceState.IDLE)
            await web_audio_bridge.notify_device_state(device_state)
        except Exception:
            pass

    async def shutdown(self) -> None:
        web_audio_bridge.clear_handlers()
        self._buffer.clear()
        self._encoder = None
        self._decoder = None

    async def on_incoming_audio(self, data: bytes) -> None:
        if not data or self._decoder is None:
            return
        try:
            frame_size = AudioConfig.OUTPUT_FRAME_SIZE
            pcm = self._decoder.decode(data, frame_size)
            if pcm:
                await web_audio_bridge.broadcast_speaker_audio(pcm)
        except Exception as exc:  # pragma: no cover - 防御
            LOGGER.debug("解码扬声器音频失败: %s", exc)

    async def on_device_state_changed(self, state: Any) -> None:
        try:
            await web_audio_bridge.notify_device_state(state)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 麦克风处理
    # ------------------------------------------------------------------
    async def _on_microphone_bytes(self, data: bytes) -> None:
        if not data:
            return
        self._buffer.extend(data)
        frame_bytes = AudioConfig.INPUT_FRAME_SIZE * 2
        while len(self._buffer) >= frame_bytes:
            chunk = bytes(self._buffer[:frame_bytes])
            del self._buffer[:frame_bytes]
            await self._forward_audio(chunk)

    async def _forward_audio(self, pcm16: bytes) -> None:
        if not self.app or not self._encoder:
            return
        protocol = getattr(self.app, "protocol", None)
        if protocol is None:
            return
        if not await self._ensure_connection(protocol):
            return
        if not self._should_send_microphone_audio():
            return
        try:
            encoded = self._encoder.encode(pcm16, AudioConfig.INPUT_FRAME_SIZE)
        except Exception as exc:  # pragma: no cover - 防御
            LOGGER.debug("编码浏览器音频失败: %s", exc)
            return
        if not encoded:
            return
        async with self._send_lock:
            try:
                await protocol.send_audio(encoded)
            except Exception as exc:  # pragma: no cover - 防御
                LOGGER.debug("发送浏览器音频失败: %s", exc)

    async def _ensure_connection(self, protocol) -> bool:
        try:
            if protocol.is_audio_channel_opened():
                return True
        except Exception:
            pass
        try:
            return await self.app.connect_protocol()
        except Exception:
            return False

    def _should_send_microphone_audio(self) -> bool:
        try:
            if not self.app:
                return False
            if self.app.device_state == DeviceState.LISTENING and not getattr(
                self.app, "aborted", False
            ):
                return True
            return (
                self.app.device_state == DeviceState.SPEAKING
                and bool(getattr(self.app, "aec_enabled", False))
                and bool(getattr(self.app, "keep_listening", False))
                and getattr(self.app, "listening_mode", None) == ListeningMode.REALTIME
            )
        except Exception:
            return False
