"""Dependency-free storage enums, shared with API models."""
from enum import StrEnum


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"

