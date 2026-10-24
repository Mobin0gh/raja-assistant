"""Application layer: use-case contracts that sit between domain and adapters.

This layer may import the domain. It must never import an adapter, a network
library, a database driver, or a UI framework; the boundary is enforced by
``tests/unit/test_layer_boundaries.py``.

It contains two things:

* the provider **port** (``ports``): the contract an adapter satisfies;
* the **orchestration service** (``service``), together with its typed errors,
  stored records, result mapping, offer policy, in-memory store, and the
  conflict ledger/quarantine that keep a duplicate submission from being acted
  on twice.

Nothing here persists to disk, reads a clock, sleeps, retries, or contacts Raja.
"""

from raja_assistant.application.conflicts import (
    ConflictLedger,
    InMemoryConflictLedger,
    Quarantine,
)
from raja_assistant.application.errors import (
    ConflictLatchFailed,
    DuplicateSubmissionBlocked,
    LookupFailed,
    OrchestrationError,
    RecordInconsistent,
    ReservationQuarantined,
    StorageError,
    UnknownReservation,
)
from raja_assistant.application.mapping import reconciliation_event, submission_event
from raja_assistant.application.models import ReservationRecord, ReservationView
from raja_assistant.application.offers import bookable_class, eligible_offers, is_eligible
from raja_assistant.application.service import (
    MUTATING_COMMANDS,
    READ_ONLY_COMMANDS,
    ReservationService,
    mutation,
)
from raja_assistant.application.store import (
    IdFactory,
    InMemoryReservationStore,
    ReservationStore,
    SequentialIdFactory,
)

__all__ = [
    "MUTATING_COMMANDS",
    "READ_ONLY_COMMANDS",
    "ConflictLatchFailed",
    "ConflictLedger",
    "DuplicateSubmissionBlocked",
    "IdFactory",
    "InMemoryConflictLedger",
    "InMemoryReservationStore",
    "LookupFailed",
    "OrchestrationError",
    "Quarantine",
    "RecordInconsistent",
    "ReservationQuarantined",
    "ReservationRecord",
    "ReservationService",
    "ReservationStore",
    "ReservationView",
    "SequentialIdFactory",
    "StorageError",
    "UnknownReservation",
    "bookable_class",
    "eligible_offers",
    "is_eligible",
    "mutation",
    "reconciliation_event",
    "submission_event",
]
