"""Typed domain errors shared by models, guards, and the state machine."""

from __future__ import annotations

from enum import StrEnum


class DomainError(Exception):
    """Base class for every error raised by the domain layer."""


class ValidationError(DomainError):
    """A value object or entity violated a domain invariant at construction time."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class IllegalTransition(DomainError):
    """An event was applied to a state that does not accept it."""

    def __init__(self, state: object, event: object) -> None:
        self.state = state
        self.event = event
        state_value = getattr(state, "value", state)
        event_value = getattr(event, "value", event)
        super().__init__(f"event {event_value!r} is not allowed in state {state_value!r}")


class GuardReason(StrEnum):
    """Stable, machine-readable reasons a transition was refused."""

    INTENT_MISSING = "intent_missing"
    PROVIDER_NOT_AUTHORIZED = "provider_not_authorized"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_INTENT_MISMATCH = "approval_intent_mismatch"
    APPROVAL_STALE = "approval_stale"
    QUOTE_EXPIRED = "quote_expired"
    QUOTE_NOT_EXPIRED = "quote_not_expired"
    DUPLICATE_SUBMISSION_PENDING = "duplicate_submission_pending"
    PROVIDER_REFERENCE_MISSING = "provider_reference_missing"
    REJECTION_REASON_MISSING = "rejection_reason_missing"
    HOLD_EXPIRY_INVALID = "hold_expiry_invalid"
    HOLD_NOT_EXPIRED = "hold_not_expired"
    EVIDENCE_MISSING = "evidence_missing"
    CLOCK_BACKWARDS = "clock_backwards"


class GuardViolation(DomainError):
    """A syntactically allowed transition was refused by its guard."""

    def __init__(self, reason: GuardReason, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        message = reason.value if not detail else f"{reason.value}: {detail}"
        super().__init__(message)
