"""Idempotency keys must be reproducible, and sensitive to the right inputs."""

from __future__ import annotations

import re
from datetime import date

import pytest

from raja_assistant.domain.errors import ValidationError
from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass
from raja_assistant.domain.models.service import ServiceId
from raja_assistant.domain.reservation.idempotency import (
    KEY_PREFIX,
    derive_idempotency_key,
)

KEY_PATTERN = re.compile(rf"{re.escape(KEY_PREFIX)}:[0-9a-f]{{64}}")

BASE: dict[str, object] = {
    "intent_id": "intent-0001",
    "service_id": ServiceId("svc-1001"),
    "travel_date": date(2026, 11, 1),
    "passenger_refs": (PassengerRef("pax-aaaa"), PassengerRef("pax-bbbb")),
    "quota_class": QuotaClass("economy"),
    "attempt_epoch": 1,
}


def derive(**overrides: object) -> str:
    values = dict(BASE)
    values.update(overrides)
    return derive_idempotency_key(**values)  # type: ignore[arg-type]


def test_key_has_a_versioned_sha256_shape() -> None:
    assert KEY_PATTERN.fullmatch(derive())


def test_same_inputs_produce_the_same_key() -> None:
    assert derive() == derive()


def test_passenger_order_does_not_matter() -> None:
    reversed_refs = (PassengerRef("pax-bbbb"), PassengerRef("pax-aaaa"))
    assert derive(passenger_refs=reversed_refs) == derive()


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent_id": "intent-0002"},
        {"service_id": ServiceId("svc-2002")},
        {"travel_date": date(2026, 11, 2)},
        {"quota_class": QuotaClass("business")},
        {"attempt_epoch": 2},
        {"passenger_refs": (PassengerRef("pax-aaaa"), PassengerRef("pax-cccc"))},
    ],
    ids=["intent", "service", "date", "quota", "epoch", "passengers"],
)
def test_every_logical_input_changes_the_key(overrides: dict[str, object]) -> None:
    assert derive(**overrides) != derive()


def test_plain_strings_are_normalized_like_value_objects() -> None:
    assert derive(service_id="svc-1001", quota_class="economy") == derive()


def test_a_new_attempt_epoch_changes_the_key() -> None:
    assert derive(attempt_epoch=2) != derive(attempt_epoch=1)
    assert derive(attempt_epoch=2) != derive(attempt_epoch=3)


@pytest.mark.parametrize("epoch", [0, -1])
def test_attempt_epoch_must_be_positive(epoch: int) -> None:
    with pytest.raises(ValidationError) as error:
        derive(attempt_epoch=epoch)
    assert error.value.field == "attempt_epoch"


def test_empty_passenger_list_is_refused() -> None:
    with pytest.raises(ValidationError) as error:
        derive(passenger_refs=())
    assert error.value.field == "passenger_refs"


def test_blank_service_is_refused() -> None:
    with pytest.raises(ValidationError) as error:
        derive(service_id="   ")
    assert error.value.field == "service_id"


def test_invalid_intent_id_is_refused() -> None:
    with pytest.raises(ValidationError) as error:
        derive(intent_id="bad intent")
    assert error.value.field == "intent_id"
