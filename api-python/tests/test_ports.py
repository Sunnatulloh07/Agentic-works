from app.domain import Channel, DeliveryStatus, InboundMessage
from app.orders import Order
from app.ports import NullMessageSender, NullOrderSink


def _message() -> InboundMessage:
    return InboundMessage(
        tenant_id="demo-retail",
        channel=Channel.TELEGRAM,
        external_key="update-1",
        sender_id="customer-1",
        conversation_id="chat-1",
        correlation_id="corr-1",
    )


def test_null_sender_preserves_correlation_and_external_key():
    receipt = NullMessageSender().send_reply(_message(), "Salom")

    assert receipt.status is DeliveryStatus.SENT
    assert receipt.external_id == "dry:update-1"
    assert receipt.correlation_id == "corr-1"


def test_null_sink_preserves_idempotency_key():
    order = Order(
        id="demo-retail-order-1",
        tenant="demo-retail",
        update_id=1,
        phone="+998901234567",
        product_id="KB001",
        qty=1,
        branch_id="chilonzor",
    )

    receipt = NullOrderSink().append_order(order, "demo-retail:order-1")

    assert receipt.provider == "null"
    assert receipt.idempotency_key == "demo-retail:order-1"