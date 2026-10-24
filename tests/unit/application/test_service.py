"""End-to-end tests for the orchestration service.

Every timestamp is fixed and every provider is the deterministic simulator, so a
failure here is reproducible from the test body alone. The suite never touches a
network: ``tests/conftest.py`` refuses socket creation for the whole session.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from provider_support import ECONOMY, QUOTE_TTL, make_intent
from raja_assistant.adapters.simulator import SimulatorFaults
from raja_assistant.application.conflicts import Quarantine
from raja_assistant.application.errors import (
    ConflictLatchFailed,
    DuplicateSubmissionBlocked,
    LookupFailed,
    RecordInconsistent,
    ReservationQuarantined,
    StorageError,
)
from raja_assistant.application.mapping import PENDING_EVIDENCE, UNAVAILABLE_EVIDENCE
from raja_assistant.application.models import ReservationRecord
from raja_assistant.application.ports.errors import (
    DuplicateSubmissionAttempt,
    ProviderContractError,
    ProviderNotAuthorized,
)
from raja_assistant.application.ports.models import (
    ProviderOutcome,
    ProviderUnavailable,
    ReservationHeld,
    ReservationPending,
    ReservationRejected,
)
from raja_assistant.application.service import UNCLASSIFIED_ANSWER, ReservationService
from raja_assistant.application.store import InMemoryReservationStore, SequentialIdFactory
from raja_assistant.domain.errors import (
    GuardReason,
    GuardViolation,
    IllegalTransition,
    ValidationError,
)
from raja_assistant.domain.models.reservation import Reservation
from raja_assistant.domain.reservation.guards import (
    APPROVAL_MAX_AGE,
    Approval,
    ProviderCapabilities,
)
from raja_assistant.domain.reservation.states import ReservationState
from service_support import (
    AFTER_QUOTE,
    NOW,
    FailingLedger,
    RecordingStore,
    SpyProvider,
    approval_for,
    build_service,
    manual_quote,
    offer_for,
    passenger_refs,
)
from support import make_preferences

HELD_EXPIRY = NOW + timedelta(minutes=30)


def _open(service: ReservationService, *, now=NOW, preferences=None):
    """Open a quote on a fixture offer without going through a reservation id."""

    preferences = preferences if preferences is not None else make_preferences()
    return service.open_quote(
        offer_for(preferences),
        quota_class=ECONOMY,
        preferences=preferences,
        passenger_refs=passenger_refs(preferences.party_size),
        quote_expires_at=now + QUOTE_TTL,
        now=now,
    )


def _service_with(ledger):
    """Build a service around a chosen conflict ledger and observable doubles."""

    provider = SpyProvider()
    store = RecordingStore()
    quarantine = Quarantine()
    service = ReservationService(
        provider=provider,
        store=store,
        ledger=ledger,
        quarantine=quarantine,
        ids=SequentialIdFactory(),
    )
    return service, provider, store, quarantine


def _parked(faults: SimulatorFaults):
    """Quote, approve, and return a harness whose reservation is ambiguous."""

    harness = build_service(faults=faults)
    view = harness.quote()
    parked = harness.service.approve_and_submit(
        view.reservation.reservation_id,
        approval=approval_for(view),
        now=NOW,
    )
    assert parked.state is ReservationState.SUBMISSION_UNKNOWN
    return harness, view


def _all_mutations(harness, reservation_id, view):
    """Return every mutating command aimed at one reservation, as callables."""

    return {
        "approve_and_submit": lambda: harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        ),
        "reconcile": lambda: harness.service.reconcile(reservation_id, now=NOW),
        "decline": lambda: harness.service.decline(reservation_id, now=NOW),
        "expire_quote": lambda: harness.service.expire_quote(
            reservation_id, now=AFTER_QUOTE
        ),
    }


# --------------------------------------------------------------------------- #
# 1. The happy path
# --------------------------------------------------------------------------- #


def test_a_successful_submission_confirms_with_a_fake_reference() -> None:
    harness = build_service()
    view = harness.quote()

    confirmed = harness.service.approve_and_submit(
        view.reservation.reservation_id,
        approval=approval_for(view),
        now=NOW,
    )

    assert confirmed.state is ReservationState.CONFIRMED
    assert confirmed.reservation.provider_reference is not None
    assert confirmed.reservation.provider_reference.startswith("SIM-")
    assert confirmed.record.last_outcome is ProviderOutcome.CONFIRMED
    assert confirmed.total_fare_irr == view.intent.fare_irr * view.intent.preferences.party_size
    assert harness.provider.submit_calls == 1
    assert harness.service.open_reservations() == ()


# --------------------------------------------------------------------------- #
# 2. SUBMITTING is written before the provider is called
# --------------------------------------------------------------------------- #


def test_submitting_is_saved_before_the_provider_is_called() -> None:
    harness = build_service()
    view = harness.quote()
    harness.events.clear()

    harness.service.approve_and_submit(
        view.reservation.reservation_id,
        approval=approval_for(view),
        now=NOW,
    )

    assert harness.events[0] == "save:submitting"
    assert harness.events.index("save:submitting") < harness.events.index("submit")


# --------------------------------------------------------------------------- #
# 3. Ambiguity never triggers a second submission
# --------------------------------------------------------------------------- #


def test_an_ambiguous_answer_is_never_resubmitted() -> None:
    harness, view = _parked(SimulatorFaults(submit_outcome_unknown=True))
    reservation_id = view.reservation.reservation_id

    parked = harness.service.get(reservation_id)
    assert parked.record.last_outcome is ProviderOutcome.OUTCOME_UNKNOWN
    assert parked.record.last_detail == "simulated acknowledgement loss"
    assert harness.provider.submit_calls == 1

    with pytest.raises(IllegalTransition):
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    assert harness.provider.submit_calls == 1
    assert len(harness.provider.inner.records) == 1


def test_a_raised_submit_call_parks_the_reservation_without_retrying() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    harness.provider.raise_on_submit = RuntimeError("connection reset")

    parked = harness.service.approve_and_submit(
        reservation_id, approval=approval_for(view), now=NOW
    )

    assert parked.state is ReservationState.SUBMISSION_UNKNOWN
    assert parked.record.last_outcome is None
    assert parked.record.last_detail == "RuntimeError"
    assert harness.provider.submit_calls == 1


# --------------------------------------------------------------------------- #
# 4. Pre-submit refusals and failures never reach the provider
# --------------------------------------------------------------------------- #


def test_an_approval_for_another_intent_never_submits() -> None:
    harness = build_service()
    view = harness.quote()
    wrong = Approval(intent_id="int-someone-else", approved_by="operator", approved_at=NOW)

    with pytest.raises(GuardViolation) as error:
        harness.service.approve_and_submit(
            view.reservation.reservation_id, approval=wrong, now=NOW
        )

    assert error.value.reason is GuardReason.APPROVAL_INTENT_MISMATCH
    assert harness.provider.submit_calls == 0
    assert harness.service.get(view.reservation.reservation_id).state is (
        ReservationState.AWAITING_APPROVAL
    )


def test_a_stale_approval_never_submits() -> None:
    harness = build_service()
    view = harness.quote()
    late = NOW + APPROVAL_MAX_AGE + timedelta(minutes=1)

    with pytest.raises(GuardViolation) as error:
        harness.service.approve_and_submit(
            view.reservation.reservation_id,
            approval=approval_for(view, now=NOW),
            now=late,
        )

    assert error.value.reason is GuardReason.APPROVAL_STALE
    assert harness.provider.submit_calls == 0


def test_an_expired_quote_never_submits() -> None:
    harness = build_service()
    view = harness.quote()

    with pytest.raises(GuardViolation) as error:
        harness.service.approve_and_submit(
            view.reservation.reservation_id,
            approval=approval_for(view, now=AFTER_QUOTE),
            now=AFTER_QUOTE,
        )

    assert error.value.reason is GuardReason.QUOTE_EXPIRED
    assert harness.provider.submit_calls == 0


def test_an_unauthorized_provider_never_submits() -> None:
    harness = build_service(
        capabilities=ProviderCapabilities(authorized=False, book=True)
    )
    view = harness.quote()

    with pytest.raises(ProviderNotAuthorized):
        harness.service.approve_and_submit(
            view.reservation.reservation_id, approval=approval_for(view), now=NOW
        )

    assert harness.provider.submit_calls == 0
    assert harness.service.get(view.reservation.reservation_id).state is (
        ReservationState.AWAITING_APPROVAL
    )


def test_a_failed_in_flight_save_never_submits() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    before = harness.store.get(reservation_id)
    harness.store.fail_on_save = harness.store.save_calls + 1  # the in-flight save

    with pytest.raises(StorageError):
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    assert harness.provider.submit_calls == 0
    assert harness.store.get(reservation_id) is before
    assert before is not None and before.reservation.state is ReservationState.AWAITING_APPROVAL


def test_two_open_reservations_for_one_intent_block_submission() -> None:
    harness = build_service()
    view = harness.quote()
    manual_quote(harness, view.intent, reservation_id="rsv-manual-0001")

    with pytest.raises(GuardViolation) as error:
        harness.service.approve_and_submit(
            view.reservation.reservation_id, approval=approval_for(view), now=NOW
        )

    assert error.value.reason is GuardReason.DUPLICATE_SUBMISSION_PENDING
    assert harness.provider.submit_calls == 0


# --------------------------------------------------------------------------- #
# 5-8. Conflict latching, quarantine, and store atomicity
# --------------------------------------------------------------------------- #


def test_a_duplicate_conflict_is_latched_and_blocks_every_command() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    harness.provider.raise_on_submit = DuplicateSubmissionAttempt(
        view.intent.intent_id, "SIM-EXISTING"
    )

    with pytest.raises(DuplicateSubmissionBlocked) as error:
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    assert error.value.conflict_reference == "SIM-EXISTING"
    assert harness.ledger.reference_for(reservation_id) == "SIM-EXISTING"

    exposed = harness.service.get(reservation_id)
    assert exposed.is_latched
    assert exposed.conflict_reference == "SIM-EXISTING"
    assert exposed.record.last_detail is not None

    listed = {item.reservation.reservation_id: item for item in harness.service.open_reservations()}
    assert listed[reservation_id].conflict_reference == "SIM-EXISTING"

    commands = _all_mutations(harness, reservation_id, view)
    for name, command in commands.items():
        with pytest.raises(DuplicateSubmissionBlocked):
            try:
                command()
            except DuplicateSubmissionBlocked:
                raise
            except Exception as unexpected:  # pragma: no cover - failure path
                raise AssertionError(
                    f"{name} raised {unexpected!r} instead of DuplicateSubmissionBlocked"
                ) from unexpected

    assert harness.provider.submit_calls == 1


def test_a_failed_latch_write_fails_closed_without_claiming_a_latch() -> None:
    service, provider, store, quarantine = _service_with(FailingLedger())
    view = _open(service)
    reservation_id = view.reservation.reservation_id
    provider.raise_on_submit = DuplicateSubmissionAttempt(
        view.intent.intent_id, "SIM-EXISTING"
    )

    with pytest.raises(ConflictLatchFailed) as error:
        service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    assert error.value.latched is False
    assert error.value.conflict_reference is None
    assert len(quarantine) == 1

    exposed = service.get(reservation_id)
    assert exposed.is_quarantined
    assert exposed.quarantine_reason is not None
    # A quarantine must never be presented as evidence of a latch.
    assert exposed.conflict_reference is None

    for command in (
        lambda: service.reconcile(reservation_id, now=NOW),
        lambda: service.decline(reservation_id, now=NOW),
        lambda: service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        ),
    ):
        with pytest.raises(ReservationQuarantined):
            command()

    assert provider.submit_calls == 1
    assert store.get(reservation_id) is not None


def test_a_failed_mirror_keeps_the_ledger_latch_authoritative() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    harness.provider.raise_on_submit = DuplicateSubmissionAttempt(
        view.intent.intent_id, "SIM-EXISTING"
    )
    # +1 is the SUBMITTING save, +2 is the audit mirror.
    harness.store.fail_on_save = harness.store.save_calls + 2

    with pytest.raises(ConflictLatchFailed) as error:
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    assert error.value.latched is True
    assert error.value.conflict_reference == "SIM-EXISTING"
    assert harness.ledger.reference_for(reservation_id) == "SIM-EXISTING"

    stored = harness.store.get(reservation_id)
    assert stored is not None
    assert stored.reservation.state is ReservationState.SUBMITTING
    assert stored.conflict_reference is None  # the mirror did not land

    # The override still reports the latch, and the command is refused.
    assert harness.service.get(reservation_id).conflict_reference == "SIM-EXISTING"
    with pytest.raises(DuplicateSubmissionBlocked):
        harness.service.reconcile(reservation_id, now=NOW)


def test_a_failed_acknowledgement_save_preserves_the_submitting_record() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    # +1 is the SUBMITTING save, +2 is the recorded provider answer.
    harness.store.fail_on_save = harness.store.save_calls + 2

    with pytest.raises(StorageError):
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    stored = harness.store.get(reservation_id)
    assert stored is not None
    assert stored.reservation.state is ReservationState.SUBMITTING
    assert harness.provider.submit_calls == 1


def test_a_refused_store_write_leaves_the_prior_record_untouched() -> None:
    store = InMemoryReservationStore()
    first_intent = make_intent(intent_id="intent-store-a")
    first = ReservationRecord(
        reservation=Reservation.start(
            reservation_id="rsv-store-1", intent_id=first_intent.intent_id, now=NOW
        ),
        intent=first_intent,
    )
    store.save(first)
    before = store.get("rsv-store-1")
    assert before is first

    other_intent = make_intent(intent_id="intent-store-b")
    with pytest.raises(RecordInconsistent):
        store.save(
            ReservationRecord(
                reservation=Reservation.start(
                    reservation_id="rsv-store-1",
                    intent_id=other_intent.intent_id,
                    now=NOW,
                ),
                intent=other_intent,
            )
        )

    assert store.get("rsv-store-1") is before


# --------------------------------------------------------------------------- #
# 9-12. Reconciliation
# --------------------------------------------------------------------------- #


def test_reconcile_from_submitting_commits_the_ambiguous_state_first() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    # +1 is the SUBMITTING save, +2 is the acknowledgement that must fail.
    harness.store.fail_on_save = harness.store.save_calls + 2
    with pytest.raises(StorageError):
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    stored = harness.store.get(reservation_id)
    assert stored is not None
    assert stored.reservation.state is ReservationState.SUBMITTING

    harness.store.fail_on_save = None
    harness.events.clear()
    resolved = harness.service.reconcile(reservation_id, now=NOW)

    assert harness.events[0] == "save:submission_unknown"
    assert harness.events.index("save:submission_unknown") < harness.events.index("lookup")
    assert resolved.state is ReservationState.CONFIRMED
    assert harness.provider.submit_calls == 1


def test_a_raised_lookup_is_wrapped_and_never_read_as_not_found() -> None:
    harness, view = _parked(SimulatorFaults(submit_outcome_unknown=True))
    reservation_id = view.reservation.reservation_id
    harness.provider.raise_on_lookup = OSError("socket reset")

    with pytest.raises(LookupFailed) as error:
        harness.service.reconcile(reservation_id, now=NOW)

    assert isinstance(error.value.__cause__, OSError)
    stored = harness.store.get(reservation_id)
    assert stored is not None
    assert stored.reservation.state is ReservationState.SUBMISSION_UNKNOWN
    assert harness.provider.submit_calls == 1
    assert harness.provider.lookup_calls == 1

    harness.provider.raise_on_lookup = None
    resolved = harness.service.reconcile(reservation_id, now=NOW)

    assert resolved.state is ReservationState.CONFIRMED
    assert harness.provider.submit_calls == 1


@pytest.mark.parametrize(
    "unmappable",
    [object(), ReservationRejected(reason="not_a_lookup_result", observed_at=NOW)],
    ids=["unknown_object", "submission_result"],
)
def test_an_unmappable_lookup_result_is_a_contract_error(unmappable: object) -> None:
    harness, view = _parked(SimulatorFaults(submit_pending=True))
    reservation_id = view.reservation.reservation_id
    harness.provider.lookup_result = unmappable

    with pytest.raises(ProviderContractError):
        harness.service.reconcile(reservation_id, now=NOW)

    stored = harness.store.get(reservation_id)
    assert stored is not None
    assert stored.reservation.state is ReservationState.SUBMISSION_UNKNOWN
    assert stored.reservation.rejection_reason is None
    assert stored.last_outcome is ProviderOutcome.PENDING


@pytest.mark.parametrize(
    ("lookup_result", "outcome", "evidence", "keeps_hold"),
    [
        (
            ReservationHeld(
                provider_reference="SIM-HELD-1",
                hold_expires_at=HELD_EXPIRY,
                observed_at=NOW,
            ),
            ProviderOutcome.HELD,
            "a hold is not a confirmed reservation",
            True,
        ),
        (
            ReservationPending(client_reference="key-1", observed_at=NOW),
            ProviderOutcome.PENDING,
            PENDING_EVIDENCE,
            False,
        ),
        (
            ProviderUnavailable(observed_at=NOW),
            ProviderOutcome.UNAVAILABLE,
            UNAVAILABLE_EVIDENCE,
            False,
        ),
    ],
    ids=["held", "pending", "unavailable"],
)
def test_an_inconclusive_lookup_keeps_the_reservation_ambiguous(
    lookup_result: object,
    outcome: ProviderOutcome,
    evidence: str,
    keeps_hold: bool,
) -> None:
    harness, view = _parked(SimulatorFaults(submit_pending=True))
    reservation_id = view.reservation.reservation_id
    harness.provider.lookup_result = lookup_result

    resolved = harness.service.reconcile(reservation_id, now=NOW)

    assert resolved.state is ReservationState.SUBMISSION_UNKNOWN
    assert resolved.record.last_outcome is outcome
    assert resolved.reservation.evidence_note is not None
    assert evidence in resolved.reservation.evidence_note
    assert (resolved.record.observed_hold_expires_at == HELD_EXPIRY) is keeps_hold
    assert harness.provider.submit_calls == 1


def test_reconcile_only_accepts_ambiguous_entry_states() -> None:
    harness = build_service()
    view = harness.quote()

    with pytest.raises(IllegalTransition):
        harness.service.reconcile(view.reservation.reservation_id, now=NOW)

    assert harness.provider.lookup_calls == 0
    assert harness.provider.submit_calls == 0


# --------------------------------------------------------------------------- #
# 13-14. Overlays and quote construction
# --------------------------------------------------------------------------- #


def test_open_quote_needs_no_reservation_id() -> None:
    harness = build_service()

    view = harness.quote()

    assert view.state is ReservationState.AWAITING_APPROVAL
    assert view.reservation.reservation_id.startswith("rsv-")
    assert harness.store.get(view.reservation.reservation_id) is not None


def test_open_quote_rejects_a_fare_that_is_not_the_service_fare() -> None:
    harness = build_service()

    with pytest.raises(ValidationError):
        harness.quote(fare_irr=1)

    assert len(harness.store) == 0
    assert harness.provider.submit_calls == 0


# --------------------------------------------------------------------------- #
# 15. A refused but received outcome keeps its diagnosis (M1)
# --------------------------------------------------------------------------- #


def test_a_refused_but_received_outcome_is_preserved() -> None:
    harness = build_service(
        capabilities=ProviderCapabilities(authorized=True, book=True, hold=False)
    )
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    harness.provider.submit_result = ReservationHeld(
        provider_reference="SIM-HELD-1",
        hold_expires_at=HELD_EXPIRY,
        observed_at=NOW,
    )

    parked = harness.service.approve_and_submit(
        reservation_id, approval=approval_for(view), now=NOW
    )

    assert parked.state is ReservationState.SUBMISSION_UNKNOWN
    # The provider answered with a hold; that fact must survive the refusal.
    assert parked.record.last_outcome is ProviderOutcome.HELD
    assert parked.record.last_detail == "GuardViolation"
    assert "provider has no hold support" not in (parked.record.last_detail or "")
    # The unusable answer is not promoted into a confirmation artefact.
    assert parked.reservation.provider_reference is None
    assert harness.provider.submit_calls == 1

    with pytest.raises(IllegalTransition):
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    assert harness.provider.submit_calls == 1


# --------------------------------------------------------------------------- #
# 16. Submission-level unavailability is not an ambiguous outcome
# --------------------------------------------------------------------------- #


def test_a_submission_unavailable_result_returns_to_approval() -> None:
    harness = build_service(faults=SimulatorFaults(submit_unavailable=True))
    view = harness.quote()

    returned = harness.service.approve_and_submit(
        view.reservation.reservation_id, approval=approval_for(view), now=NOW
    )

    assert returned.state is ReservationState.AWAITING_APPROVAL
    assert returned.record.last_outcome is ProviderOutcome.UNAVAILABLE
    assert returned.record.last_detail is None
    assert harness.provider.submit_calls == 1
    assert harness.provider.inner.records == ()


# --------------------------------------------------------------------------- #
# 17. Declining and expiring a quote
# --------------------------------------------------------------------------- #


def test_decline_cancels_the_quote_and_closes_it() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id

    cancelled = harness.service.decline(reservation_id, now=NOW)

    assert cancelled.state is ReservationState.CANCELLED
    assert cancelled.reservation.is_terminal
    stored = harness.store.get(reservation_id)
    assert stored is not None
    assert stored.reservation.state is ReservationState.CANCELLED
    assert harness.service.open_reservations() == ()


def test_expire_quote_requires_the_deadline_and_then_closes_it() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id

    with pytest.raises(GuardViolation) as error:
        harness.service.expire_quote(reservation_id, now=NOW)

    assert error.value.reason is GuardReason.QUOTE_NOT_EXPIRED
    before = harness.store.get(reservation_id)
    assert before is not None
    assert before.reservation.state is ReservationState.AWAITING_APPROVAL

    expired = harness.service.expire_quote(reservation_id, now=AFTER_QUOTE)

    assert expired.state is ReservationState.EXPIRED
    after = harness.store.get(reservation_id)
    assert after is not None
    assert after.reservation.state is ReservationState.EXPIRED
    assert harness.service.open_reservations() == ()


# --------------------------------------------------------------------------- #
# 18. A received but unmappable submission result is mapped as unknown
# --------------------------------------------------------------------------- #


def test_a_received_but_unmappable_submission_result_is_mapped_as_unknown() -> None:
    harness = build_service()
    view = harness.quote()
    reservation_id = view.reservation.reservation_id
    harness.provider.submit_result = object()

    parked = harness.service.approve_and_submit(
        reservation_id, approval=approval_for(view), now=NOW
    )

    # A result that no submission mapping can use must still park the
    # reservation as ambiguous, not as booked, not as rejected, and not as
    # silently swallowed.
    assert parked.state is ReservationState.SUBMISSION_UNKNOWN
    assert parked.record.last_detail == UNCLASSIFIED_ANSWER
    assert parked.record.last_outcome is None
    assert parked.reservation.provider_reference is None
    assert parked.record.observed_hold_expires_at is None
    assert parked.reservation.evidence_note is None
    assert harness.provider.submit_calls == 1

    # A second attempt is still rejected and makes no extra submit call.
    with pytest.raises(IllegalTransition):
        harness.service.approve_and_submit(
            reservation_id, approval=approval_for(view), now=NOW
        )

    assert harness.provider.submit_calls == 1


# --------------------------------------------------------------------------- #
# 19. Originally written # 18. A failed final reconciliation save is retryable
# --------------------------------------------------------------------------- #


def test_a_failed_reconciliation_save_keeps_the_ambiguous_record_and_can_retry() -> None:
    harness, view = _parked(SimulatorFaults(submit_outcome_unknown=True))
    reservation_id = view.reservation.reservation_id
    prior = harness.store.get(reservation_id)
    assert prior is not None
    assert prior.reservation.state is ReservationState.SUBMISSION_UNKNOWN

    harness.store.fail_on_save = harness.store.save_calls + 1  # the reconciled record
    with pytest.raises(StorageError):
        harness.service.reconcile(reservation_id, now=NOW)

    assert harness.store.get(reservation_id) is prior
    assert harness.provider.submit_calls == 1

    harness.store.fail_on_save = None
    resolved = harness.service.reconcile(reservation_id, now=NOW)

    assert resolved.state is ReservationState.CONFIRMED
    assert harness.provider.submit_calls == 1
