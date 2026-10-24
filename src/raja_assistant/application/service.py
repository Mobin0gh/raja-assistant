"""The reservation orchestration service.

One class drives the domain state machine from provider results. It owns no
clock, opens no socket, and never retries: every command receives ``now`` from
its caller, performs at most one provider call, and returns what the machine
now knows.

Shape of every mutating command
-------------------------------

1. ``_begin`` loads the record and runs the single conflict guard (quarantine →
   ledger → audit mirror). It is the first thing every command does.
2. The command transitions the domain aggregate through the real table, so every
   guard the domain defines still applies; nothing here re-implements one.
3. ``_commit`` is the only place a record is written.

``approve_and_submit`` is the only command with a side effect outside the
process, and its order is load-bearing:

* Phase A (guards, request construction) raises and writes nothing if it fails.
* ``SUBMITTING`` is committed **before** ``provider.submit`` is called. A failed
  save there aborts without submitting, so no external request can happen
  without a persisted in-flight record.
* Phase B is the only window where an exception becomes an unknown outcome. A
  raised call, an unmappable result, or an acknowledgement the machine refuses
  all park the reservation in ``SUBMISSION_UNKNOWN`` — never a second attempt.
* A failed acknowledgement save leaves the record in ``SUBMITTING`` and raises
  ``StorageError``: no rollback, no retry, no re-submission.

``reconcile`` is lookup-only. It commits ``SUBMISSION_UNKNOWN`` before the
lookup, wraps (never interprets) a raised call in ``LookupFailed``, and has no
code path that could submit.

Limits that this module cannot fix and does not pretend to:

* the store, the conflict ledger, and the quarantine are process-local; nothing
  survives termination, so ``SUBMITTING`` recovery is in-process only;
* there is no concurrency control, so per-call atomic saves are not a
  transaction over read-modify-write;
* the no-side-effect meaning of ``ProviderUnavailable`` is an obligation on each
  adapter, not something this service can verify.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, NoReturn, cast

from raja_assistant.application.conflicts import (
    ConflictLedger,
    InMemoryConflictLedger,
    Quarantine,
)
from raja_assistant.application.errors import (
    ConflictLatchFailed,
    DuplicateSubmissionBlocked,
    LookupFailed,
    RecordInconsistent,
    ReservationQuarantined,
    StorageError,
    UnknownReservation,
)
from raja_assistant.application.mapping import reconciliation_event, submission_event
from raja_assistant.application.models import ReservationRecord, ReservationView
from raja_assistant.application.offers import eligible_offers
from raja_assistant.application.ports.errors import (
    DuplicateSubmissionAttempt,
    ProviderContractError,
)
from raja_assistant.application.ports.models import (
    LookupRequest,
    ProviderOutcome,
    ProviderUnavailable,
    ReservationHeld,
    SearchResult,
    SearchSucceeded,
    ServiceOffer,
    SubmissionOutcomeUnknown,
    SubmissionRequest,
)
from raja_assistant.application.ports.provider import (
    ReservationProvider,
    require_booking_capability,
)
from raja_assistant.application.store import (
    IdFactory,
    InMemoryReservationStore,
    ReservationStore,
    SequentialIdFactory,
)
from raja_assistant.domain.errors import IllegalTransition
from raja_assistant.domain.models.passenger import PassengerRef
from raja_assistant.domain.models.preferences import QuotaClass, TripPreferences
from raja_assistant.domain.models.reservation import Reservation, ReservationIntent
from raja_assistant.domain.reservation.events import ReservationEvent
from raja_assistant.domain.reservation.guards import Approval, ReservationContext
from raja_assistant.domain.reservation.states import ReservationState
from raja_assistant.domain.reservation.transitions import transition
from raja_assistant.domain.validation import require

#: Recorded when an answer could not be mapped onto the machine at all.
UNCLASSIFIED_ANSWER = "unclassified provider answer"

#: Recorded when an adapter reports a live reservation under another key.
CONFLICT_DETAIL = "adapter reports a live reservation under a different key"

def mutation[F: Callable[..., Any]](func: F) -> F:
    """Mark a service command and guard it before its body runs.

    The wrapper consults the centralized guard for any command whose first
    parameter is ``reservation_id``. Commands without one — only ``open_quote``,
    which creates a reservation that cannot be latched yet — pass through; their
    bodies still call ``_begin`` so the structural rule "every mutating command
    begins with ``_begin``" holds for all of them.

    Parameter names are read from the function's own code object rather than
    from ``inspect``: the dependency boundary forbids this layer from growing a
    ``signature`` reflection dependency for one check. Keyword-only parameters
    are included, so a ``reservation_id`` declared after ``*`` is guarded too.
    """

    code = func.__code__
    positional_names = code.co_varnames[: code.co_argcount]
    keyword_only_names = code.co_varnames[
        code.co_argcount : code.co_argcount + code.co_kwonlyargcount
    ]
    # ``self`` occupies the first positional slot, so a positional
    # ``reservation_id`` is at ``positional_index - 1`` in ``*args``.
    positional_index = (
        positional_names.index("reservation_id")
        if "reservation_id" in positional_names
        else None
    )
    keyword_only = "reservation_id" in keyword_only_names

    def wrapper(self: ReservationService, *args: Any, **kwargs: Any) -> Any:
        reservation_id: str | None = None
        if positional_index is not None:
            argument_index = positional_index - 1
            if len(args) > argument_index:
                reservation_id = args[argument_index]
            else:
                reservation_id = kwargs.get("reservation_id")
        elif keyword_only:
            reservation_id = kwargs.get("reservation_id")
        if reservation_id is not None:
            self._require_clear(reservation_id, func.__name__)
        return func(self, *args, **kwargs)

    wrapper.is_mutation = True  # type: ignore[attr-defined]
    wrapper.__name__ = func.__name__
    wrapper.__qualname__ = func.__qualname__
    wrapper.__doc__ = func.__doc__
    return cast(F, wrapper)


def _unclassified_detail(exc: BaseException) -> str:
    """Label a failure to apply an answer, without persisting provider text."""

    if isinstance(exc, ProviderContractError):
        return UNCLASSIFIED_ANSWER
    return type(exc).__name__


class ReservationService:
    """Use cases for quoting, submitting, declining, and reconciling."""

    def __init__(
        self,
        *,
        provider: ReservationProvider,
        store: ReservationStore | None = None,
        ledger: ConflictLedger | None = None,
        quarantine: Quarantine | None = None,
        ids: IdFactory | None = None,
    ) -> None:
        self._provider = provider
        self._store = store if store is not None else InMemoryReservationStore()
        self._ledger = ledger if ledger is not None else InMemoryConflictLedger()
        self._quarantine = quarantine if quarantine is not None else Quarantine()
        self._ids = ids if ids is not None else SequentialIdFactory()

    # ------------------------------------------------------------------ #
    # Read-only commands
    # ------------------------------------------------------------------ #

    def find_offers(self, preferences: TripPreferences, *, now: datetime) -> SearchResult:
        """Search through the adapter and keep only offers the traveller accepts."""

        result = self._provider.search(preferences, now=now)
        if isinstance(result, ProviderUnavailable):
            return result
        return SearchSucceeded(
            offers=eligible_offers(preferences, result.offers),
            observed_at=result.observed_at,
        )

    def get(self, reservation_id: str) -> ReservationView:
        """Return one reservation, including its process-local overlays."""

        record = self._store.get(reservation_id)
        if record is None:
            raise UnknownReservation(reservation_id)
        return self._view(record)

    def open_reservations(self) -> tuple[ReservationView, ...]:
        """Return every reservation that is not terminal, ordered by id."""

        return tuple(self._view(record) for record in self._store.open_reservations())

    # ------------------------------------------------------------------ #
    # Mutating commands
    # ------------------------------------------------------------------ #

    @mutation
    def open_quote(
        self,
        offer: ServiceOffer,
        *,
        quota_class: QuotaClass,
        preferences: TripPreferences,
        passenger_refs: Sequence[PassengerRef],
        quote_expires_at: datetime,
        now: datetime,
        fare_irr: int | None = None,
    ) -> ReservationView:
        """Open a quote and drive it to ``AWAITING_APPROVAL``.

        ``fare_irr`` is the **per-passenger** fare shown to the traveller. When
        given it must equal the quoted service's fare exactly, so a stale or
        invented price cannot be approved; the total for the party is derived on
        the record and never stored.
        """

        self._begin(None, "open_quote")
        party_size = preferences.party_size
        require(
            len(passenger_refs) == party_size,
            "passenger_refs",
            "must match the party size",
        )
        require(
            bool(eligible_offers(preferences, (offer,))),
            "offer",
            "is not eligible for these preferences",
        )

        availability = offer.availability_for(quota_class)
        require(availability is not None, "quota_class", "must be offered by the quoted service")
        require(availability.is_bookable, "quota_class", "must be observed as available")
        require(
            availability.remaining_seats is not None and availability.remaining_seats >= party_size,
            "quota_class",
            "must have enough seats for the whole party",
        )

        service_fare = offer.service.fare_irr
        quoted_fare = service_fare if fare_irr is None else fare_irr
        require(quoted_fare == service_fare, "fare_irr", "must equal the quoted service fare")

        intent = ReservationIntent(
            intent_id=self._ids.next_intent_id(),
            preferences=preferences,
            service_id=offer.service.service_id,
            quota_class=quota_class,
            passenger_refs=tuple(passenger_refs),
            fare_irr=quoted_fare,
            quote_expires_at=quote_expires_at,
            created_at=now,
        )
        reservation = Reservation.start(
            reservation_id=self._ids.next_reservation_id(),
            intent_id=intent.intent_id,
            now=now,
        )
        record = ReservationRecord(reservation=reservation, intent=intent)
        context = self._context(record, now=now)
        reservation = transition(reservation, ReservationEvent.VALIDATE, context)
        reservation = transition(reservation, ReservationEvent.REQUEST_APPROVAL, context)

        stored = replace(record, reservation=reservation)
        self._commit(stored)
        return self._view(stored)

    @mutation
    def approve_and_submit(
        self,
        reservation_id: str,
        *,
        approval: Approval,
        now: datetime,
    ) -> ReservationView:
        """Submit the approved intent exactly once, or record why it is ambiguous."""

        record = cast(ReservationRecord, self._begin(reservation_id, "approve_and_submit"))
        require_booking_capability(self._provider)

        context = self._context(record, now=now, approval=approval)
        submitted = transition(record.reservation, ReservationEvent.APPROVE_AND_SUBMIT, context)
        request = SubmissionRequest.for_intent(
            record.intent,
            submitted_at=now,
            attempt_epoch=submitted.attempt_epoch,
        )

        in_flight = replace(record, reservation=submitted, observed_hold_expires_at=None)
        self._commit(in_flight)

        try:
            result = self._provider.submit(request)
        except DuplicateSubmissionAttempt as conflict:
            self._latch_conflict(in_flight, conflict)
        except Exception as exc:
            # Classified as an unknown outcome; never retried, never re-sent.
            return self._acknowledge_unknown(in_flight, now=now, detail=type(exc).__name__)

        try:
            event, payload = submission_event(result)
            acknowledged = transition(submitted, event, context, payload)
        except Exception as exc:
            # An answer the machine cannot apply is still an answer: park it, but
            # keep the received outcome so "no answer arrived" stays
            # distinguishable from "the answer could not be applied".
            candidate = getattr(result, "outcome", None)
            return self._acknowledge_unknown(
                in_flight,
                now=now,
                detail=_unclassified_detail(exc),
                outcome=candidate if isinstance(candidate, ProviderOutcome) else None,
            )

        stored = replace(
            in_flight,
            reservation=acknowledged,
            last_outcome=result.outcome,
            last_detail=result.detail if isinstance(result, SubmissionOutcomeUnknown) else None,
        )
        self._commit(stored)
        return self._view(stored)

    @mutation
    def reconcile(self, reservation_id: str, *, now: datetime) -> ReservationView:
        """Resolve (or re-diagnose) an ambiguous submission using a lookup only."""

        record = cast(ReservationRecord, self._begin(reservation_id, "reconcile"))
        state = record.reservation.state
        context = self._context(record, now=now)

        if state is ReservationState.SUBMITTING:
            unknown = transition(record.reservation, ReservationEvent.ACK_UNKNOWN, context)
            record = replace(record, reservation=unknown, observed_hold_expires_at=None)
            # Committed before the lookup, so a failure below can only ever leave
            # a reconcilable state behind.
            self._commit(record)
        elif state is not ReservationState.SUBMISSION_UNKNOWN:
            raise IllegalTransition(state, ReservationEvent.RECONCILE_INCONCLUSIVE)

        request = LookupRequest.for_submission(
            SubmissionRequest.for_intent(
                record.intent,
                submitted_at=now,
                attempt_epoch=record.reservation.attempt_epoch,
            ),
            requested_at=now,
        )

        try:
            result = self._provider.lookup(request)
        except Exception as exc:
            # An exception is not an outcome: it is never read as NOT_FOUND.
            raise LookupFailed(reservation_id, record.reservation.state) from exc

        event, payload = reconciliation_event(result)
        resolved = transition(record.reservation, event, context, payload)
        stored = replace(
            record,
            reservation=resolved,
            last_outcome=result.outcome,
            last_detail=None,
            observed_hold_expires_at=(
                result.hold_expires_at if isinstance(result, ReservationHeld) else None
            ),
        )
        self._commit(stored)
        return self._view(stored)

    @mutation
    def decline(self, reservation_id: str, *, now: datetime) -> ReservationView:
        """Cancel a quote the traveller no longer wants."""

        record = cast(ReservationRecord, self._begin(reservation_id, "decline"))
        context = self._context(record, now=now)
        declined = transition(record.reservation, ReservationEvent.DECLINE, context)
        stored = replace(record, reservation=declined)
        self._commit(stored)
        return self._view(stored)

    @mutation
    def expire_quote(self, reservation_id: str, *, now: datetime) -> ReservationView:
        """Expire a quote explicitly; approving one never does this implicitly."""

        record = cast(ReservationRecord, self._begin(reservation_id, "expire_quote"))
        context = self._context(record, now=now)
        expired = transition(record.reservation, ReservationEvent.QUOTE_EXPIRED, context)
        stored = replace(record, reservation=expired)
        self._commit(stored)
        return self._view(stored)

    # ------------------------------------------------------------------ #
    # Guard, load, and commit
    # ------------------------------------------------------------------ #

    def _begin(self, reservation_id: str | None, command: str) -> ReservationRecord | None:
        """Load a record and refuse to continue when the reservation is blocked.

        ``reservation_id`` is None only for the command that creates a new
        reservation: a reservation that does not exist yet cannot be latched, so
        there is nothing to guard.
        """

        if reservation_id is None:
            return None
        record = self._store.get(reservation_id)
        if record is None:
            raise UnknownReservation(reservation_id)
        self._require_clear(reservation_id, command, record)
        if record.intent.intent_id != record.reservation.intent_id:
            raise RecordInconsistent(
                "intent_id",
                "the stored intent does not match its reservation",
            )
        return record

    def _require_clear(
        self,
        reservation_id: str,
        command: str,
        record: ReservationRecord | None = None,
    ) -> None:
        """The single conflict guard: quarantine, then ledger, then mirror."""

        reason = self._quarantine.reason_for(reservation_id)
        if reason is not None:
            raise ReservationQuarantined(reservation_id, reason, command)

        reference = self._ledger.reference_for(reservation_id)
        if reference is not None:
            raise DuplicateSubmissionBlocked(reservation_id, reference, command)

        if record is None:
            record = self._store.get(reservation_id)
        if record is not None and record.conflict_reference is not None:
            raise DuplicateSubmissionBlocked(reservation_id, record.conflict_reference, command)

    def _commit(self, record: ReservationRecord) -> None:
        """Persist one record; the only write path in the service."""

        reservation_id = record.reservation.reservation_id
        try:
            self._store.save(record)
        except (RecordInconsistent, StorageError):
            raise
        except Exception as exc:
            # A store failure must stay typed and must leave the prior record.
            raise StorageError(reservation_id, "store") from exc

    def _context(
        self,
        record: ReservationRecord,
        *,
        now: datetime,
        approval: Approval | None = None,
    ) -> ReservationContext:
        """Build the context every guard reads, without duplicating any guard rule."""

        others = [
            other
            for other in self._store.open_for_intent(record.intent.intent_id)
            if other.reservation.reservation_id != record.reservation.reservation_id
        ]
        return ReservationContext(
            now=now,
            intent=record.intent,
            approval=approval,
            capabilities=self._provider.capabilities,
            open_reservations_for_intent=len(others),
        )

    def _view(self, record: ReservationRecord) -> ReservationView:
        """Attach the authoritative overlays so readers never see two truths."""

        reservation_id = record.reservation.reservation_id
        return ReservationView(
            record=record,
            conflict_reference=(
                self._ledger.reference_for(reservation_id) or record.conflict_reference
            ),
            quarantine_reason=self._quarantine.reason_for(reservation_id),
        )

    def _acknowledge_unknown(
        self,
        record: ReservationRecord,
        *,
        now: datetime,
        detail: str,
        outcome: ProviderOutcome | None = None,
    ) -> ReservationView:
        """Park an unusable answer in ``SUBMISSION_UNKNOWN`` and persist it.

        ``outcome`` carries the provider's own result when one was received and
        the machine refused to apply it; it stays ``None`` when the call raised
        before returning, so "the answer was lost" and "the answer was unusable"
        remain distinguishable to an operator and to a later reconciliation.

        ``detail`` is always a fixed phrase or an exception *class* name — never a
        message, credential, or payload.

        If this itself fails, the exception propagates and the stored record stays
        ``SUBMITTING`` — the safest resting place, because ``reconcile`` accepts
        it.
        """

        context = self._context(record, now=now)
        unknown = transition(record.reservation, ReservationEvent.ACK_UNKNOWN, context)
        stored = replace(
            record,
            reservation=unknown,
            last_outcome=outcome,
            last_detail=detail,
            observed_hold_expires_at=None,
        )
        self._commit(stored)
        return self._view(stored)

    def _latch_conflict(
        self,
        record: ReservationRecord,
        conflict: DuplicateSubmissionAttempt,
    ) -> NoReturn:
        """Record a duplicate conflict, then refuse to go on.

        The ledger is authoritative and written first. If it fails, the
        reservation is quarantined *before* the error is raised, so later commands
        are still refused — while the error itself states plainly that no latch
        exists. If only the audit mirror fails, the latch still stands.
        """

        reservation_id = record.reservation.reservation_id
        try:
            self._ledger.latch(reservation_id, conflict.existing_reference)
        except Exception as exc:
            # Fail closed: the quarantine is marked before the error escapes.
            self._quarantine.mark(reservation_id)
            raise ConflictLatchFailed(reservation_id, latched=False) from exc

        mirrored = replace(
            record,
            conflict_reference=conflict.existing_reference,
            last_outcome=None,
            last_detail=CONFLICT_DETAIL,
        )
        try:
            self._commit(mirrored)
        except Exception as exc:
            # The authoritative latch outranks a failed audit mirror.
            raise ConflictLatchFailed(
                reservation_id,
                latched=True,
                conflict_reference=conflict.existing_reference,
            ) from exc

        raise DuplicateSubmissionBlocked(reservation_id, conflict.existing_reference)


#: Every public command that may write or call the provider, derived from the
#: ``@mutation`` markers rather than maintained by hand.
MUTATING_COMMANDS: frozenset[str] = frozenset(
    name
    for name, member in vars(ReservationService).items()
    if getattr(member, "is_mutation", False)
)

#: Public commands that only read. Anything public must appear here or above.
READ_ONLY_COMMANDS: frozenset[str] = frozenset({"find_offers", "get", "open_reservations"})


__all__ = [
    "CONFLICT_DETAIL",
    "MUTATING_COMMANDS",
    "READ_ONLY_COMMANDS",
    "UNCLASSIFIED_ANSWER",
    "ReservationService",
    "mutation",
]
