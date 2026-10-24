"""Minimal Persian-language CLI for raja-assistant.

Launches via: python -m raja_assistant
Never imports from tests/. Uses only production APIs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from raja_assistant.adapters.simulator import SimulatedReservationProvider
from raja_assistant.application.offers import eligible_offers
from raja_assistant.application.ports.models import ProviderUnavailable
from raja_assistant.application.service import ReservationService
from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass, StationCode, TripPreferences
from raja_assistant.domain.reservation.guards import Approval

_IRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")

NOW = datetime(2026, 10, 15, 14, 0, 0, tzinfo=_IRAN)


def _cli_now() -> datetime:
    return NOW


def _run_workflow(service: ReservationService) -> None:
    """Core reservation workflow: search -> quote -> approve -> submit."""
    print("\n=== Enter trip details ===")

    origin_str = input(" Origin (THR/MHD): ").strip().upper()
    dest_str = input(" Destination (THR/MHD): ").strip().upper()
    origin = StationCode(origin_str)
    destination = StationCode(dest_str)

    while True:
        date_str = input(" Travel date (YYYY-MM-DD): ").strip()
        try:
            travel_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            break
        except ValueError:
            print("\nDATE_INVALID: لطفاً یک تاریخ معتبر وارد کنید (فرمت YYYY-MM-DD).\n")

    class_str = input(" Class (economy/business): ").strip().lower()
    quota_class = QuotaClass(class_str)

    # Build preferences and search
    preferences = TripPreferences(
        origin=origin,
        destination=destination,
        travel_date=travel_date,
        party_size=1,
        quota_classes=frozenset({quota_class}),
    )

    search_result = service._provider.search(preferences, now=_cli_now())

    if isinstance(search_result, ProviderUnavailable):
        print("\nProvider unavailable (" + search_result.detail + ")")
        return

    offers = eligible_offers(preferences, search_result.offers)
    if not offers:
        print("\nNo service found for class " + quota_class.value + ".")
        return

    # Show first eligible offer simply
    offer = offers[0]
    print("\nService found: " + offer.service.train_name)
    print("   " + offer.service.origin.value + " -> " + offer.service.destination.value)
    print("   Date: " + str(offer.service.travel_date))
    print("   Fare: " + str(offer.service.fare_irr) + " IRR")

    # Open quote
    view = service.open_quote(
        offer=offer,
        quota_class=quota_class,
        preferences=preferences,
        passenger_refs=tuple(PassengerRef(token="cli-pax-" + str(i+1).zfill(2)) for i in range(1)),
        quote_expires_at=_cli_now() + timedelta(minutes=15),
        now=_cli_now(),
    )

    print("\nQuote opened")
    print("   Reservation code: " + view.reservation.reservation_id)
    print("   Status: " + str(view.reservation.state))

    # Approve and submit
    approval = Approval(
        intent_id=view.reservation.intent_id,
        approved_by="user",
        approved_at=_cli_now(),
    )

    view2 = service.approve_and_submit(
        reservation_id=view.reservation.reservation_id,
        approval=approval,
        now=_cli_now(),
    )

    print("\n=== Final result ===")
    print("   Reservation code: " + view2.reservation.reservation_id)
    print("   Status: " + str(view2.reservation.state))
    if view2.reservation.state.value == "confirmed":
        print("   Provider reference: " + view2.reservation.provider_reference)
    print("   --- Sistem: only simulation ---")


def main() -> None:
    print("Raja Assistant - Reservation Service")
    print("Version 0.3.0 - Simulated (Network: Cut)")

    service = ReservationService(provider=SimulatedReservationProvider())

    while True:
        print("\nMain Menu:")
        print("  1. Start reservation workflow")
        print("  2. Exit")

        choice = input("Enter your choice: ").strip()

        if choice == "1":
            _run_workflow(service)

        elif choice == "2" or choice == "":
            print("Goodbye!")
            break

        else:
            print("Invalid choice, tried again.")


if __name__ == "__main__":
    main()
