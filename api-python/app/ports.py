"""Tashqi servislar uchun core'dan mustaqil portlar."""
from typing import Protocol

from .domain import DeliveryReceipt, InboundMessage, SinkReceipt
from .orders import Order


class MessageSender(Protocol):
    def send_reply(self, message: InboundMessage, text: str) -> DeliveryReceipt:
        """Mijozga javob yuboradi; provider xatosi yuqoriga chiqadi."""


class OrderSink(Protocol):
    def append_order(self, order: Order, idempotency_key: str) -> SinkReceipt:
        """Buyurtmani tashqi tizimga bir marta yozadi."""


class NullMessageSender:
    """Dry-run va local demo uchun tashqi tarmoqsiz sender."""

    def send_reply(self, message: InboundMessage, text: str) -> DeliveryReceipt:
        return DeliveryReceipt(
            provider="null",
            external_id=f"dry:{message.external_key}",
            status="sent",
            correlation_id=message.correlation_id,
        )


class NullOrderSink:
    """Dry-run sink; hech qachon real external write qilmaydi."""

    def append_order(self, order: Order, idempotency_key: str) -> SinkReceipt:
        return SinkReceipt(
            provider="null",
            external_id=f"dry:{order.id or order.update_id}",
            idempotency_key=idempotency_key,
        )