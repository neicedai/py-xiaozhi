"""Client helper for New Concept lessons via DeepSeek or Xiaozhi service."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, Optional

import requests

try:  # pragma: no cover - optional dependency
    import aiohttp  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - handled gracefully at runtime
    aiohttp = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiohttp as aiohttp_type

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_shared_client: Optional["DeepSeekClient"] = None


class DeepSeekClient:
    """Helper for calling DeepSeek directly or via Xiaozhi provided service."""

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

        # Xiaozhi-provided service configuration (set via MCP capabilities)
        self.service_url: Optional[str] = None
        self.service_token: Optional[str] = None
        self.service_headers: Dict[str, str] = {}

        # Cache device/client identifiers for service calls
        self._device_id: str = str(
            config.get_config("SYSTEM_OPTIONS.DEVICE_ID", "") or ""
        )
        self._client_id: str = str(
            config.get_config("SYSTEM_OPTIONS.CLIENT_ID", "") or ""
        )

    # ------------------------------------------------------------------
    def configure_service(
        self,
        url: Optional[str],
        token: Optional[str] = None,
        *,
        model: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        timeout: Optional[int] = None,
    ) -> None:
        """Configure Xiaozhi-hosted service endpoint for lesson generation."""

        if isinstance(url, str):
            self.service_url = url.rstrip("/")
        elif url:
            logger.warning(
                "[NewConcept] Ignoring non-string service URL from capabilities: %s",
                url,
            )
            self.service_url = None
        else:
            self.service_url = None
        self.service_token = token or None

        if model:
            self.model = model
        if temperature is not None:
            try:
                self.default_temperature = float(temperature)
            except (TypeError, ValueError):
                logger.warning(
                    "[NewConcept] Invalid temperature from capabilities: %s",
                    temperature,
                )
        if timeout is not None:
            try:
                self.timeout = int(timeout)
            except (TypeError, ValueError):
                logger.warning(
                    "[NewConcept] Invalid timeout from capabilities: %s", timeout
                )

        if headers and isinstance(headers, dict):
            self.service_headers = {str(k): str(v) for k, v in headers.items()}
        else:
            self.service_headers = {}

        if self.service_url:
            logger.info(
                "[NewConcept] Configured Xiaozhi lesson service: %s", self.service_url
            )
        else:
            logger.info("[NewConcept] Cleared Xiaozhi lesson service configuration")

    # ------------------------------------------------------------------
    def has_service(self) -> bool:
        return bool(self.service_url)

    def has_direct_credentials(self) -> bool:
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
        if self.has_service():
            return await self._chat_via_service(payload)

        if not self.has_direct_credentials():
            raise RuntimeError(
                "DeepSeek API key is not configured and no Xiaozhi service is available"
            )

        if aiohttp is None:
            raise RuntimeError(
                "aiohttp is not installed. Please install it to enable DeepSeek API calls."
            )

        return await self._chat_direct(payload)

    async def _chat_via_service(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.service_url:
            raise RuntimeError("Xiaozhi lesson service URL is not configured")

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._device_id:
            headers["Device-Id"] = self._device_id
        if self._client_id:
            headers["Client-Id"] = self._client_id
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
        headers.update(self.service_headers)

        def _post_request() -> Dict[str, Any]:
            response = requests.post(
                self.service_url or "",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {"raw": response.text}

        try:
            data = await asyncio.to_thread(_post_request)
        except requests.RequestException as exc:  # pragma: no cover - network errors
            logger.error("[NewConcept] Xiaozhi lesson service request failed: %s", exc)
            raise RuntimeError(f"Xiaozhi 课程服务调用失败: {exc}") from exc

        logger.info("[NewConcept] Xiaozhi lesson service request succeeded")
        return data

    async def _chat_direct(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self._build_url("chat/completions")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if isinstance(self.extra_headers, dict):
            headers.update({str(k): str(v) for k, v in self.extra_headers.items()})

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:  # type: ignore[arg-type]
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


def get_deepseek_client() -> DeepSeekClient:
    global _shared_client
    if _shared_client is None:
        _shared_client = DeepSeekClient()
    return _shared_client


def configure_deepseek_service(
    url: Optional[str],
    token: Optional[str] = None,
    *,
    model: Optional[str] = None,
    headers: Optional[Dict[str, Any]] = None,
    temperature: Optional[float] = None,
    timeout: Optional[int] = None,
) -> None:
    client = get_deepseek_client()
    client.configure_service(
        url,
        token,
        model=model,
        headers=headers,
        temperature=temperature,
        timeout=timeout,
    )


def _extract_error_message(data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if "error" in data and isinstance(data["error"], dict):
        return data["error"].get("message")
    return data.get("message")
