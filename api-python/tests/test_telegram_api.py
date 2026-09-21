import httpx
import pytest

from app.domain import Channel, InboundMessage
from app.integrations.telegram_api import TelegramApiError, TelegramApiSender


def _message() -> InboundMessage:
    return InboundMessage(
        tenant_id="demo-retail",
        channel=Channel.TELEGRAM,
        external_key="update-1",
        sender_id="customer-1",
        conversation_id="chat-1",
        correlation_id="corr-1",
    )


def test_sender_validates_telegram_success_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/botsecret/sendMessage")
        assert request.read() == b'{"chat_id":"chat-1","text":"Salom"}'
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    sender = TelegramApiSender(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    receipt = sender.send_reply(_message(), "Salom")

    assert receipt.external_id == "42"
    assert receipt.provider == "telegram"


def test_sender_retries_transient_provider_failure():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    sender = TelegramApiSender(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    receipt = sender.send_reply(_message(), "Salom")

    assert receipt.external_id == "7"
    assert attempts == 2


def test_sender_does_not_expose_token_on_permanent_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "unauthorized"})

    sender = TelegramApiSender(
        "super-secret-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(TelegramApiError) as error:
        sender.send_reply(_message(), "Salom")

    assert error.value.retryable is False
    assert "super-secret-token" not in str(error.value)


def test_sender_classifies_telegram_ok_false_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error_code": 429})

    sender = TelegramApiSender(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )

    with pytest.raises(TelegramApiError) as error:
        sender.send_reply(_message(), "Salom")

    assert error.value.retryable is True