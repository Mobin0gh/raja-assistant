"""Deterministic idempotency keys.

The key is derived from the *logical* submission, never from a timestamp or a
random value, so a retry of the same attempt reproduces the same key while a
genuinely new attempt (a new epoch) does not. Retrying with a different key is
how duplicate bookings happen; this module makes that impossible to do by
accident.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date

from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass
from raja_assistant.domain.models.service import ServiceId
from raja_assistant.domain.validation import require, require_token

KEY_PREFIX = "raja-assistant/idempotency/v1"
_SEPARATOR = "\x1f"


def _canonical_field(name: str, value: str) -> str:
    """Length-prefix every field so concatenation cannot be ambiguous."""

    return f"{name}={len(value)}:{value}"


def derive_idempotency_key(
    *,
    intent_id: str,
    service_id: ServiceId | str,
    travel_date: date,
    passenger_refs: Iterable[PassengerRef | str],
    quota_class: QuotaClass | str,
    attempt_epoch: int,
) -> str:
    """Return a stable key for one submission attempt of one intent.

    Passenger order does not matter; every other input does.
    """

    require_token(intent_id, "intent_id")
    require(isinstance(attempt_epoch, int) and attempt_epoch >= 1,
            "attempt_epoch", "must be an integer of at least 1")
    require(isinstance(travel_date, date), "travel_date", "must be a date")

    service_value = service_id.value if isinstance(service_id, ServiceId) else str(service_id)
    quota_value = quota_class.value if isinstance(quota_class, QuotaClass) else str(quota_class)
    require(bool(service_value.strip()), "service_id", "must not be blank")
    require(bool(quota_value.strip()), "quota_class", "must not be blank")

    passengers = sorted(
        ref.token if isinstance(ref, PassengerRef) else str(ref) for ref in passenger_refs
    )
    require(bool(passengers), "passenger_refs", "must not be empty")

    fields = [
        _canonical_field("intent", intent_id),
        _canonical_field("service", service_value),
        _canonical_field("travel_date", travel_date.isoformat()),
        _canonical_field("quota", quota_value),
        _canonical_field("epoch", str(attempt_epoch)),
        _canonical_field("passengers", ",".join(passengers)),
    ]
    digest = hashlib.sha256(_SEPARATOR.join(fields).encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}:{digest}"
