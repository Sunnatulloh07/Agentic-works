from pydantic import ValidationError
import pytest

from app.domain import (
    Channel,
    DeliveryReceipt,
    DeliveryStatus,
    InboundMessage,
    SinkReceipt,
    TaskStatus,
)


def test_inbound_message_is_tenant_scoped_and_typed():
    message = InboundMessage(
        tenant_id="demo-retail",
        channel="telegram",
        external_key="update-1",
        sender_id="customer-1",
        conversation_id="chat-1",
        text="KB001 narxi?",
        correlation_id="corr-1",
    )

    assert message.channel is Channel.TELEGRAM
    assert message.tenant_id == "demo-retail"


def test_domain_contracts_reject_missing_identity():
    with pytest.raises(ValidationError):
        InboundMessage(
            tenant_id="",
            channel=Channel.TELEGRAM,
            external_key="update-1",
            sender_id="customer-1",
            conversation_id="chat-1",
            correlation_id="corr-1",
        )


def test_receipts_preserve_external_and_idempotency_identity():
    delivery = DeliveryReceipt(
        provider="telegram",
        external_id="message-1",
        status=DeliveryStatus.SENT,
        correlation_id="corr-1",
    )
    sink = SinkReceipt(
        provider="google-sheets",
        external_id="row-1",
        idempotency_key="demo-retail:order-1",
    )

    assert delivery.status is DeliveryStatus.SENT
    assert sink.idempotency_key == "demo-retail:order-1"
    assert TaskStatus.WAITING_APPROVAL.value == "waiting_approval"