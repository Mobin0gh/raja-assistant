"""The single translation from provider results to reservation events.

P2 wrote this translation inside a test to prove the contract's vocabulary was
sufficient; P3 needs it in production, so it lives here and the test now imports
it. One table, one place to audit.

Two mappings carry the safety story:

* ``ReservationPending`` and ``SubmissionOutcomeUnknown`` both become
  ``ACK_UNKNOWN``. A pending answer is *not* a booking, and the state machine has
  no pending node; the raw outcome is preserved on the record instead
  (``last_outcome``), so the ambiguity stays diagnosable without inventing a
  state.
* ``ProviderUnavailable`` becomes ``SEND_FAILED_NO_SIDE_EFFECT`` **only** because
  the adapter contract obliges the adapter to guarantee the request was not
  processed. A post-send timeout must be reported as
  ``SubmissionOutcomeUnknown``; the orchestrator never decides that question
  itself.

Reconciliation is deliberately narrower: a lookup can only ever resolve, park,
or re-diagnose a reservation. It cannot produce ``ACK_*`` events, and nothing in
this module can produce a submission.
"""

from __future__ import annotations

from datetime import datetime

from raja_assistant.application.ports.errors import ProviderContractError
from raja_assistant.application.ports.models import (
    LookupResult,
    ProviderUnavailable,
    ReservationConfirmed,
    ReservationHeld,
    ReservationNotFound,
    ReservationPending,
    ReservationRejected,
    SubmissionOutcomeUnknown,
    SubmissionResult,
)
from raja_assistant.domain.reservation.events import ReservationEvent, TransitionPayload

#: Fixed evidence strings. They are written into the reservation's evidence note,
#: so they are stable, short, and free of identifiers.
BOOKED_EVIDENCE = "lookup reports the reservation as booked"
NOT_BOOKED_EVIDENCE = "lookup found no reservation for this client reference"
UNAVAILABLE_EVIDENCE = "lookup could not reach the provider"
PENDING_EVIDENCE = "the provider still reports the submission as pending"


def _unclassified(result: object) -> ProviderContractError:
    return ProviderContractError(
        "result",
        f"{type(result).__name__} is not a result this operation can return",
    )


def submission_event(result: SubmissionResult) -> tuple[ReservationEvent, TransitionPayload]:
    """Translate a submission result into the acknowledgement event it licenses.

    ``ReservationPending`` maps to ``ACK_UNKNOWN``: the provider accepted the
    request but has not decided, which is exactly the ambiguous state. The caller
    keeps ``result.outcome`` so the two cases stay distinguishable.
    """

    if isinstance(result, ReservationConfirmed):
        return (
            ReservationEvent.ACK_CONFIRMED,
            TransitionPayload(provider_reference=result.provider_reference),
        )
    if isinstance(result, ReservationHeld):
        return (
            ReservationEvent.ACK_HELD,
            TransitionPayload(hold_expires_at=result.hold_expires_at),
        )
    if isinstance(result, ReservationRejected):
        return (
            ReservationEvent.ACK_REJECTED,
            TransitionPayload(rejection_reason=result.reason),
        )
    if isinstance(result, (ReservationPending, SubmissionOutcomeUnknown)):
        return ReservationEvent.ACK_UNKNOWN, TransitionPayload()
    if isinstance(result, ProviderUnavailable):
        # Safe only under the adapter's no-side-effect guarantee (see the port).
        return ReservationEvent.SEND_FAILED_NO_SIDE_EFFECT, TransitionPayload()
    raise _unclassified(result)


def reconciliation_event(result: LookupResult) -> tuple[ReservationEvent, TransitionPayload]:
    """Translate a lookup result into the event that resolves ambiguity.

    A hold discovered by lookup is *inconclusive*, not a booking: its expiry is
    preserved as evidence so the operator can see what the provider reported,
    while the reservation stays ambiguous.
    """

    if isinstance(result, ReservationConfirmed):
        return (
            ReservationEvent.RECONCILE_BOOKED,
            TransitionPayload(
                provider_reference=result.provider_reference,
                evidence_note=BOOKED_EVIDENCE,
            ),
        )
    if isinstance(result, ReservationNotFound):
        return (
            ReservationEvent.RECONCILE_NOT_BOOKED,
            TransitionPayload(evidence_note=NOT_BOOKED_EVIDENCE),
        )
    if isinstance(result, ProviderUnavailable):
        return (
            ReservationEvent.RECONCILE_INCONCLUSIVE,
            TransitionPayload(evidence_note=UNAVAILABLE_EVIDENCE),
        )
    if isinstance(result, ReservationPending):
        return (
            ReservationEvent.RECONCILE_INCONCLUSIVE,
            TransitionPayload(evidence_note=PENDING_EVIDENCE),
        )
    if isinstance(result, ReservationHeld):
        return (
            ReservationEvent.RECONCILE_INCONCLUSIVE,
            TransitionPayload(evidence_note=hold_evidence(result.hold_expires_at)),
        )
    raise _unclassified(result)


def hold_evidence(hold_expires_at: datetime) -> str:
    """Return the note recorded when a lookup reports a hold."""

    return (
        f"lookup reports a seat hold until {hold_expires_at.isoformat()}; "
        "a hold is not a confirmed reservation"
    )


__all__ = [
    "BOOKED_EVIDENCE",
    "NOT_BOOKED_EVIDENCE",
    "PENDING_EVIDENCE",
    "UNAVAILABLE_EVIDENCE",
    "hold_evidence",
    "reconciliation_event",
    "submission_event",
]
