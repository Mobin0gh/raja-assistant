"""The port is only useful if adapters can be held to it and disabled by it."""

from __future__ import annotations

import pytest

from provider_support import make_lookup, make_submission
from raja_assistant.adapters.simulator import SimulatedReservationProvider
from raja_assistant.application.ports.errors import (
    CapabilityUnsupported,
    ProviderNotAuthorized,
)
from raja_assistant.application.ports.provider import (
    ReservationProvider,
    require_booking_capability,
)
from raja_assistant.domain.reservation.guards import ProviderCapabilities


def provider_with(**capabilities: bool) -> SimulatedReservationProvider:
    return SimulatedReservationProvider(capabilities=ProviderCapabilities(**capabilities))


def test_the_simulator_satisfies_the_port() -> None:
    provider = SimulatedReservationProvider()

    assert isinstance(provider, ReservationProvider)
    assert provider.name == "simulator"


def test_arbitrary_objects_do_not_satisfy_the_port() -> None:
    assert not isinstance(object(), ReservationProvider)


def test_the_simulator_declares_what_it_can_do() -> None:
    capabilities = SimulatedReservationProvider().capabilities

    assert capabilities.authorized
    assert capabilities.book
    assert capabilities.hold
    assert capabilities.lookup_by_reference
    # No payment path exists anywhere in this project, so the simulator must not
    # advertise one.
    assert not capabilities.pay


def test_the_booking_requirement_passes_for_an_authorized_adapter() -> None:
    require_booking_capability(SimulatedReservationProvider())


def test_the_booking_requirement_refuses_an_unauthorized_adapter() -> None:
    provider = provider_with(book=True)

    with pytest.raises(ProviderNotAuthorized) as error:
        require_booking_capability(provider)

    assert error.value.provider == "simulator"


def test_the_booking_requirement_refuses_a_read_only_adapter() -> None:
    provider = provider_with(authorized=True, lookup_by_reference=True)

    with pytest.raises(CapabilityUnsupported) as error:
        require_booking_capability(provider)

    assert error.value.capability == "book"


def test_an_unauthorized_adapter_cannot_submit_anything() -> None:
    provider = provider_with(book=True)

    with pytest.raises(ProviderNotAuthorized):
        provider.submit(make_submission())

    assert provider.records == ()


def test_an_unauthorized_adapter_can_still_answer_lookup() -> None:
    """Reconciliation is read-only, so disabling booking must not disable it."""

    provider = provider_with(book=True)
    request = make_submission()

    result = provider.lookup(make_lookup(request))

    assert result.outcome.value == "not_found"
