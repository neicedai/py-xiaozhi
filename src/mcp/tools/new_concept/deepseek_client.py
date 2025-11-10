"""Async client helper for DeepSeek chat completions."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import aiohttp

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class DeepSeekClient:
    """Lightweight helper for calling the DeepSeek chat API."""

    def __init__(self) -> None:
        config = ConfigManager.get_instance()
        self.api_key: str = config.get_config("DEEPSEEK.api_key", "") or ""
        self.base_url: str = config.get_config(
            "DEEPSEEK.base_url", "https://api.deepseek.com/v1"
        )
        self.model: str = config.get_config("DEEPSEEK.model", "deepseek-chat")
        self.timeout: int = int(config.get_config("DEEPSEEK.timeout", 60) or 60)
        self.default_temperature: float = float(
            config.get_config("DEEPSEEK.temperature", 0.7) or 0.7
        )
        self.extra_headers = config.get_config("DEEPSEEK.extra_headers", {}) or {}

    # ------------------------------------------------------------------
    def has_credentials(self) -> bool:
        return bool(self.api_key)

    def build_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature if temperature is not None else self.default_temperature,
        }
        return payload

    async def chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.has_credentials():
            raise RuntimeError("DeepSeek API key is not configured in config.json")

        url = self._build_url("chat/completions")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if isinstance(self.extra_headers, dict):
            headers.update({str(k): str(v) for k, v in self.extra_headers.items()})

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                response_text = await response.text()
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError:
                    data = {"raw": response_text}

                if response.status >= 400:
                    message = _extract_error_message(data) or response_text
                    logger.error(
                        "[NewConcept] DeepSeek API error %s: %s", response.status, message
                    )
                    raise RuntimeError(
                        f"DeepSeek API error {response.status}: {message}"
                    )

                logger.info("[NewConcept] DeepSeek API request succeeded")
                return data

    def _build_url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        if not path:
            return base
        return f"{base}/{path.lstrip('/')}"


def _extract_error_message(data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if "error" in data and isinstance(data["error"], dict):
        return data["error"].get("message")
    return data.get("message")
