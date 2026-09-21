"""Telegram Bot API outbound adapteri."""
import time
from collections.abc import Callable

import httpx

from ..domain import DeliveryReceipt, DeliveryStatus, InboundMessage


class TelegramApiError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class TelegramApiSender:
    def __init__(self, token: str, *, client: httpx.Client | None = None,
                 max_retries: int = 2, sleep: Callable[[float], None] = time.sleep,
                 base_url: str = "https://api.telegram.org") -> None:
        if not token:
            raise ValueError("Telegram token bo'sh bo'lmasin")
        self._token = token
        self._client = client or httpx.Client(timeout=httpx.Timeout(5.0))
        self._max_retries = max(0, max_retries)
        self._sleep = sleep
        self._base_url = base_url.rstrip("/")

    def send_reply(self, message: InboundMessage, text: str) -> DeliveryReceipt:
        url = f"{self._base_url}/bot{self._token}/sendMessage"
        payload = {"chat_id": message.conversation_id, "text": text[:4096]}
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < self._max_retries:
                    self._sleep(2 ** attempt)
                    continue
                raise TelegramApiError("Telegram tarmoq xatosi", retryable=True) from exc
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self._max_retries:
                    self._sleep(2 ** attempt)
                    continue
                raise TelegramApiError("Telegram vaqtinchalik xatosi", retryable=True)
            if response.status_code >= 400:
                raise TelegramApiError("Telegram so'rov rad etildi", retryable=False)
            try:
                body = response.json()
                if body.get("ok") is not True:
                    retryable = body.get("error_code") == 429 or body.get("error_code", 0) >= 500
                    if retryable and attempt < self._max_retries:
                        self._sleep(2 ** attempt)
                        continue
                    raise TelegramApiError("Telegram provider xatosi", retryable=retryable)
                message_id = body["result"]["message_id"]
            except (ValueError, KeyError, TypeError) as exc:
                raise TelegramApiError("Telegram javobi noto'g'ri", retryable=False) from exc
            return DeliveryReceipt(
                provider="telegram",
                external_id=str(message_id),
                status=DeliveryStatus.SENT,
                correlation_id=message.correlation_id,
            )
        raise AssertionError("retry loop tugashi kerak")
