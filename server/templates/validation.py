"""Structured validation errors raised by deployment templates."""
from __future__ import annotations

from collections.abc import Iterable, Mapping


class TemplateValidationError(ValueError):
    """Validation failure that callers can associate with template fields."""

    def __init__(self, field_errors: Mapping[str, str | Iterable[str]]):
        normalized: dict[str, list[str]] = {}
        for field, messages in field_errors.items():
            items = [messages] if isinstance(messages, str) else list(messages)
            clean = [str(message) for message in items if str(message).strip()]
            if clean:
                normalized[str(field)] = clean
        self.field_errors = normalized
        super().__init__("validation failed")
