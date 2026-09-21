"""Platforma qatlamlari o'rtasidagi umumiy domen kontraktlari."""
from enum import StrEnum

from pydantic import BaseModel, Field


class Channel(StrEnum):
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    WEB = "web"
    VOICE = "voice"
    RUNNER = "runner"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


from .domain_enums import DeliveryStatus

class InboundMessage(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    channel: Channel
    external_key: str = Field(min_length=1, max_length=256)
    sender_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=256)
    text: str = Field(default="", max_length=4000)
    correlation_id: str = Field(min_length=1, max_length=128)


class DeliveryReceipt(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=256)
    status: DeliveryStatus
    correlation_id: str = Field(min_length=1, max_length=128)


class SinkReceipt(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
