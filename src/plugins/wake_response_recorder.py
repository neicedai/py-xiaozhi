"""Plugin for persisting a wake confirmation phrase.

When the assistant hears the specific confirmation phrase ("我在") after
being woken up, it writes the phrase into a small configuration file. This
allows downstream automation or external tooling to read the assistant's
verbal confirmation state directly from disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.plugins.base import Plugin
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class WakeResponseRecorderPlugin(Plugin):
    """Record the assistant's wake confirmation phrase to a config file."""

    name = "wake_response_recorder"

    _TARGET_PHRASE = "我在"

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self._config_manager: ConfigManager | None = None
        self._output_path: Path | None = None
        self._last_saved_phrase: str | None = None

    async def setup(self, app: Any) -> None:  # noqa: D401 - inherited docstring
        self.app = app
        self._config_manager = ConfigManager.get_instance()
        try:
            config_dir = self._config_manager.config_dir
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("无法获取配置目录: %s", exc)
            return

        self._output_path = Path(config_dir) / "wake_response.json"

    async def on_incoming_json(self, message: Any) -> None:
        if not isinstance(message, dict):
            return

        msg_type = str(message.get("type") or "").lower()
        if msg_type not in {"stt", "tts"}:
            return

        text = self._normalize_phrase(message.get("text"))
        state = str(message.get("state") or "").lower()

        # TTS 消息通常在 state == "stop" 时不携带文本，此时无需记录
        if msg_type == "tts" and state == "stop" and not text:
            return

        if not text:
            return

        if text != self._TARGET_PHRASE:
            return

        if msg_type == "stt" and not self._is_final_update(message):
            return

        await self._persist_phrase(text)

    def _normalize_phrase(self, text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""

        # 去掉常见的标点符号，避免 “我在。” 无法匹配
        punctuation = "，,。.!！?？\n\r"
        table = str.maketrans("", "", punctuation)
        return raw.translate(table).strip()

    def _is_final_update(self, message: dict) -> bool:
        state = str(message.get("state") or "").lower()
        if state:
            if any(token in state for token in ("partial", "interim", "detect")):
                return False
            if any(token in state for token in ("final", "complete", "done", "stop")):
                return True

        if message.get("is_final") is True:
            return True
        if message.get("is_final") is False:
            return False

        # 当没有状态信息时，避免重复写入
        return True

    async def _persist_phrase(self, phrase: str) -> None:
        if not self._output_path:
            logger.debug("未配置 wake_response.json 输出路径")
            return

        if phrase == self._last_saved_phrase:
            return

        payload = {"wake_confirmation": phrase}

        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            self._output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._last_saved_phrase = phrase
            logger.info("已将唤醒确认词写入 %s", self._output_path)
            self._notify_user()
        except Exception as exc:  # pragma: no cover - filesystem errors
            logger.error("写入唤醒确认配置失败: %s", exc, exc_info=True)

    def _notify_user(self) -> None:
        if not self.app or not hasattr(self.app, "set_chat_message"):
            return

        try:
            self.app.set_chat_message(
                "assistant", "已记录唤醒确认词，配置文件已生成。"
            )
        except Exception:  # pragma: no cover - UI failures are non-critical
            logger.debug("通知 UI 生成唤醒确认配置失败", exc_info=True)
