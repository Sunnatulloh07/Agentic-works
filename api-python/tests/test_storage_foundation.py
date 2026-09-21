from app.domain import Channel, DeliveryStatus, InboundMessage
from app import storage


def _message(external_key: str = "update-1") -> InboundMessage:
    return InboundMessage(
        tenant_id="demo-retail",
        channel=Channel.TELEGRAM,
        external_key=external_key,
        sender_id="customer-1",
        conversation_id="chat-1",
        text="KB001 narxi?",
        correlation_id="corr-1",
    )


def test_record_inbound_is_idempotent_per_tenant_channel_and_external_key():
    assert storage.record_inbound(_message()) is True
    assert storage.record_inbound(_message()) is False
    assert storage.record_inbound(_message("update-2")) is True


def test_delivery_attempt_is_durable_and_tenant_scoped():
    delivery_id = storage.create_delivery(
        tenant_id="demo-retail",
        entity_type="message",
        entity_id="message-1",
        provider="telegram",
        idempotency_key="demo-retail:telegram:update-1",
    )
    storage.update_delivery(
        "demo-retail",
        delivery_id,
        status=DeliveryStatus.SENT,
        external_id="telegram-message-1",
    )

    row = storage.get_delivery("demo-retail", delivery_id)

    assert row["status"] == "sent"
    assert row["external_id"] == "telegram-message-1"
    assert row["idempotency_key"] == "demo-retail:telegram:update-1"
    assert storage.get_delivery("other-tenant", delivery_id) is None


def test_delivery_update_cannot_cross_tenant():
    delivery_id = storage.create_delivery(
        tenant_id="demo-retail",
        entity_type="message",
        entity_id="message-2",
        provider="telegram",
        idempotency_key="demo-retail:telegram:update-2",
    )

    assert storage.update_delivery(
        "other-tenant", delivery_id, status=DeliveryStatus.SENT,
        external_id="forged",
    ) is False
    assert storage.get_delivery("demo-retail", delivery_id)["status"] == "pending"


def test_delivery_idempotency_key_returns_existing_attempt():
    first = storage.create_delivery(
        tenant_id="demo-retail",
        entity_type="order",
        entity_id="order-1",
        provider="google-sheets",
        idempotency_key="demo-retail:order-1",
    )
    second = storage.create_delivery(
        tenant_id="demo-retail",
        entity_type="order",
        entity_id="order-1",
        provider="google-sheets",
        idempotency_key="demo-retail:order-1",
    )

    assert second == first


def test_delivery_claim_is_atomic_and_tenant_scoped():
    delivery_id = storage.create_delivery(
        tenant_id="demo-retail",
        entity_type="order",
        entity_id="order-2",
        provider="google-sheets",
        idempotency_key="demo-retail:order-2",
    )

    assert storage.claim_delivery("demo-retail", delivery_id, "worker-a", now=100) is True
    assert storage.claim_delivery("demo-retail", delivery_id, "worker-b", now=101) is False
    assert storage.claim_delivery("other-tenant", delivery_id, "worker-b", now=200) is False


def test_expired_delivery_lease_can_be_reclaimed():
    delivery_id = storage.create_delivery(
        tenant_id="demo-retail",
        entity_type="order",
        entity_id="order-3",
        provider="google-sheets",
        idempotency_key="demo-retail:order-3",
    )

    assert storage.claim_delivery("demo-retail", delivery_id, "worker-a", now=100, lease_seconds=10)
    assert storage.claim_delivery("demo-retail", delivery_id, "worker-b", now=111) is True