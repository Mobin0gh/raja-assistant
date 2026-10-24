# Raja Assistant — Train Ticket Reservation Orchestrator (Phases P1–P3)

This repository is complete through **Phase P3**: **Domain Layer** (P1), **Provider Contract and Deterministic Simulator** (P2), and **Orchestration Service** (P3).

> **Important: This is a simulation-only project. No real Raja connection exists. No network requests are sent, no persistent storage is used, no scheduling or payment processing is implemented. The only available provider is a local simulator, and all reference numbers it produces are purely fake (`SIM-…`). A reservation is only considered successful when the simulator or a test confirms it with a "provider reference".**

## What Has Been Built So Far

- Python package structure under `src/raja_assistant/` with a clear boundary between domain and other layers.
- Domain models: travel preferences, stations, capacity class, train service, capacity observation, passenger alias reference, reservation intent, and the reservation itself.
- Reserve state machine with 11 states, explicit transition table, and separate guards.
- Domain-typed errors (`ValidationError`, `IllegalTransition`, `GuardViolation` with stable reasons).
- Deterministic idempotency key generation based on SHA-256 and logical inputs.
- Unit tests for all allowed transitions, all disallowed transitions, final states, guards, and anti-duplicate-reservation constants.
- **Provider contract** (`application/ports/`) with request/response models and typed errors.
- **Deterministic simulator** (`adapters/simulator/`) with explicit error injection and no network calls.
- Simulator compatibility tests with the state machine, and machine-layer boundary tests.
- **Orchestration layer** (`application/`): reservation service, result-to-event mapping, policies, in-memory store, conflict ledger, and quarantine.
- Service tests (full workflow, no resubmission, conflict latch, reconciliation) and structural checks with negative controls.
- **Reserve state machine** with 11 states and allowed transitions documented.

## What Has Been Deliberately Not Built

- Real Raja adapter, automatic browser, live booking.
- Balance tracking, timeouts, queues, persistent database and durable crash recovery.
- Any code or hypotheses about endpoints, credentials, or approved Raja rates.
- No bank-card details are accepted or stored; the simulator has no such path.

## Project Structure

```text
src/raja_assistant/
├─ domain/                  # no outer dependencies
│  ├─ errors.py            # typed errors and guard reasons
│  ├─ validation.py        # validation helper functions
│  ├─ models/
│  │  ├─ preferences.py    # station, capacity class, travel preferences
│  │  ├─ service.py        # train service and capacity observation
│  │  ├─ passenger.py      # passenger alias reference (no personal data)
│  │  └─ reservation.py    # reservation intent and aggregate reservation
│  └─ reservation/
│     ├─ states.py         # 11 states and final/in-progress split
│     ├─ events.py         # events and evidence payload
│     ├─ guards.py         # guards and execution wrapper
│     ├─ transitions.py    # transition table and transition function
│     └─ idempotency.py    # deterministic idempotency key
├─ application/             # depends only on domain
│  ├─ service.py           # orchestration service and command registry
│  ├─ mapping.py           # sole mapping from provider result to state-event
│  ├─ offers.py            # pricing per passenger, movement window, capacity
│  ├─ models.py            # stored record and readable view
│  ├─ store.py             # in-memory store with atomic per-record persistence
│  ├─ conflicts.py         # in-memory conflict ledger and in-process quarantine
│  ├─ errors.py            # typed errors for the orchestration layer
│  └─ ports/
│     ├─ errors.py         # provider contract errors
│     ├─ models.py         # requests, outcomes, vocabulary
│     └─ provider.py       # ReservationProvider protocol and adapter obligations
└─ adapters/
   └─ simulator/
      ├─ fixtures.py       # completely fake static data
      ├─ faults.py         explicit error switches
      ├─ records.py        # internal simulator record
      └─ provider.py       # in-memory implementation
tests/
├─ conftest.py             # network guard for all tests (blocks socket creation)
├─ support.py              # deterministic domain builders
├─ provider_support.py     # deterministic provider builders
└─ unit/
   ├─ test_domain_purity.py         # domain-dependency rule
   ├─ test_layer_boundaries.py     # layer prohibitions: no network/clock/coincidence
   ├─ domain/                     # models, state machine, guards, idempotency
   ├─ application/                 # contract and protocol tests
   └─ adapters/simulator/          # search, submit, reconciliation, certainty, compat
```

## Phase P2: Provider Contract and Simulator

### Contract (`application/ports/`)

Three operations, and no more:

| Operation | Meaning |
|---|---|
| `search(preferences, now=…)` | Services available for validated preferences |
| `submit(request)` | Record one attempt, with idempotency key |
| `lookup(request)` | Report status of a prior attempt, with the same key |

Every result has a stable `outcome`:

| Outcome | Meaning |
|---|---|
| `SearchSucceeded` | Search performed (may yield zero services) |
| `ReservationConfirmed` | Reservation exists with "provider reference" |
| `ReservationHeld` | Seat temporarily held; it has a deadline |
| `ReservationPending` | Accepted but not yet decided |
| `ReservationRejected` | Definitive refusal; nothing was booked |
| `ReservationNotFound` | Definitive lookup: no reservation exists |
| `SubmissionOutcomeUnknown` | May have been booked; only resolvable via `lookup` |
| `ProviderUnavailable` | Temporary condition; nothing is known about the reservation |

Errors are only contract violations: `ProviderNotAuthorized`, `CapabilityUnsupported`, `ProviderContractError`, `DuplicateSubmissionAttempt`. None of these mean "rejected".

**Key rule**: The idempotency key comes from the domain and `SubmissionRequest` refuses to build a key that does not match its intent. Therefore a re-send of the same request returns the first outcome and never creates a second reservation; and a fresh attempt for the same intent, with a new epoch, produces a new key and may be rejected as `DuplicateSubmissionAttempt`.

### Simulator (`adapters/simulator/`)

- Fixed fake data: five services with `sim-…` IDs, including one fully available, one business-class sold out, one economy sold out, a second date, and a return path.
- No network, no file, no system clock, no race: whenever the caller takes it, the reservation reference is a deterministic function of the idempotency key (`SIM-` + 10-digit hash).
- Successful submission reduces capacity; subsequent calls follow the same sequence.

### Error Injection

Every scenario has an explicit switch; no random errors and no real delays. At most one submit switch and one lookup switch may be active, and a contradictory combination with `ProviderContractError` is rejected.

| Switch | Behavior |
|---|---|
| `search_unavailable` | `ProviderUnavailable` for search |
| `submit_unavailable` | `ProviderUnavailable`; no record is created |
| `submit_rejected` | `ReservationRejected(reason="simulated_rejection")` |
| `submit_outcome_unknown` | Reservation **is recorded** in the simulator but `SubmissionOutcomeUnknown` is returned |
| `submit_pending` | `ReservationPending` and a `pending` record |
| `submit_holds_seats` | `ReservationHeld` with fixed 30-minute deadline |
| `lookup_unavailable` | `ProviderUnavailable` for lookup (nothing is resolved) |
| `lookup_reports_not_found` | `ReservationNotFound` even when a record exists |

**Important scenario**: With `submit_outcome_unknown`, the simulator actually records the reservation and then returns "unknown" as the answer. `lookup` with the same key returns the same reference. Re-sending without lookup repeats the unknown answer and never creates a second reservation.

## Phase P3: Orchestration Layer

### Service (`application/service.py`)

The orchestration layer connects domain models and the provider contract. No clock, no network, no database, no event loop are in it: every command takes `now` from the caller and makes at most one provider call.

| Command | Role |
|---|---|
| `find_offers(preferences, *, now)` | Provider search and keep eligible offers |
| `open_quote(offer, *, quota_class, preferences, passenger_refs, quote_expires_at, now, fare_irr=None)` | Build intent and reservation up to `AWAITING_APPROVAL` |
| `approve_and_submit(reservation_id, *, approval, now)` | sole submit path; once, with domain guards |
| `reconcile(reservation_id, *, now)` | resolve ambiguity via `lookup` only |
| `decline(reservation_id, *, now)` | cancel before submit |
| `expire_quote(reservation_id, *, now)` | explicit price expiry |
| `get(reservation_id)`, `open_reservations()` | read, with conflict/quarantine layers |

`fare_irr` in intent and `ReservationIntent` is the **per-passenger rate** and must match the service rate; the total administrative fee is computed from `ReservationRecord.total_fare_irr` and is never stored.

### Safety Rules (enforced by tests)

1. `SUBMITTING` record is **saved before** the sole `provider.submit` call; if this save fails, nothing is sent.
2. No automatic resubmission. Unknown outcome, an exception during submit, or an unmappable response leaves the reservation in `SUBMISSION_UNKNOWN` and no second submission occurs.
3. Pre-submit validation failures or persistence failures never become `SUBMISSION_UNKNOWN` and never call the provider.
4. `reconcile` only does `lookup`. A `lookup` exception never reads `NOT_FOUND`; it raises `LookupFailed` with the real cause.
5. On `SUBMITTING` entry, `SUBMISSION_UNKNOWN` is **saved before** `lookup`; so a later failure always leaves a recoverable state.
6. If the final-record save fails, the prior record remains unchanged and no resubmission occurs.
7. `ReservationPending` maps to `SUBMISSION_UNKNOWN` and the raw outcome stays in `last_outcome`.
8. `ReservationHeld` in reconciliation is "inconclusive": its deadline is in `observed_hold_expires_at` and in the evidence, but the reservation is not confirmed.
9. `save` per record is atomic: either the full record replaces the prior one, or an error is raised and the prior record is left unchanged.

### Duplicate Submission Attempt

When `DuplicateSubmissionAttempt` occurs three states are possible:

| Status | Error | `conflict_reference` | Blocking factor |
|---|---|---|---|
| Conflict latch recorded | `DuplicateSubmissionBlocked` | Provider reference | Conflict ledger |
| Latch recorded, mirror not saved | `ConflictLatchFailed(latched=True)` | Provider reference | Conflict ledger |
| Latch not recorded | `ConflictLatchFailed(latched=False)` | `None` | In-process quarantine |

After any of these, **all** mutating commands — including `reconcile` — are rejected. In the third state `ReservationQuarantined` is raised and quarantine is never presented as a "recorded latch". Reading (`get`, `open_reservations`) is allowed and shows the lock/quarantine status.

### Adapter Obligations (Provider Contract)

These distinctions are the adapter's responsibility, not the service's:

- `ProviderUnavailable` only when the adapter can guarantee the request was **not** processed and no reservation was made.
- Post-send timeout, connection reset, or lost acknowledgement must be `SubmissionOutcomeUnknown`, never `ProviderUnavailable` or `ReservationRejected` unless the adapter can definitively confirm no booking was made.
- `ReservationRejected` only when the adapter can prove no reservation was made.

### Sample Usage

```python
from raja_assistant.application.conflicts import InMemoryConflictLedger, Quarantine
from raja_assistant.application.service import ReservationService
from raja_assistant.application.store import InMemoryReservationStore
from raja_assistant.domain.reservation.guards import Approval

service = ReservationService(
    provider=provider,        # in P3 only the simulator
    store=InMemoryReservationStore(),
    ledger=InMemoryConflictLedger(),
    quarantine=Quarantine(),
)

view = service.open_quote(
    offer,
    quota_class=quota_class,
    preferences=preferences,
    passenger_refs=passenger_refs,
    quote_expires_at=now + timedelta(minutes=15),
    now=now,
)

confirmed = service.approve_and_submit(
    view.reservation.reservation_id,
    approval=Approval(
        intent_id=view.intent.intent_id,
        approved_by="operator",
        approved_at=now,
    ),
    now=now,
)
```

### Tests

- `tests/unit/application/test_service.py`: full workflow to `CONFIRMED`, save-before-submit order, no resubmission, pre-submit rejections, conflict latch and quarantine, atomic save, and all reconciliation branches (exception, unmappable result, held, pending, unavailable provider).
- `tests/unit/application/test_structure.py`: structural check and negative controls — unregistered mutator, direct store-write path, additional `submit` call site, and clock/sleep prohibition are all detected and fail.
- `tests/unit/application/test_offer_selection.py`: offer eligibility, passenger references, quote expiry, invalid/incompatible inputs safely handled.
- `tests/unit/application/test_mapping.py`: mapping from provider outcomes to state events.
- `tests/unit/domain/`: state machine, guards, idempotency invariants.
- `tests/unit/test_layer_boundaries.py`: prohibitions on network, clock, coincidence, directly exercised.
- `tests/conftest.py`: network guard monkeypatches `socket.*` to raise `AssertionError("tests must not open network connections")` for every test.

### Known Limitations (explicit)

- No Raja adapter exists; a real one must be provided with a documented, written contract before any network code is written.
- `HELD` and `PAYMENT_PENDING` states are modelled but no payment is implemented; the simulator declares `pay=False`.
- Orchestration in P3 is implemented, but balance tracking and timeout are absent; provider-result-to-event mapping lives in `application/mapping.py`.
- Service (conflict ledger, quarantine, store) is **process-local** and vanishes with the process; durable crash recovery is not implemented.
- Provider protocol is **sync**. A real adapter that does I/O must run it outside the event loop; converting the protocol to `async` is a deliberate decision for a later phase.
- Domain dependency rule enforced by `tests/unit/test_domain_purity.py`; P2 boundary checks by `tests/unit/test_layer_boundaries.py`.

## Passenger Data

In this phase no personal data is stored. The domain only sees an alias token (`PassengerRef`); mapping a token to a real passenger and encrypting it is an identity adapter task for later phases.

## Date and Calendar

Models use `datetime.date` and timezone-aware timestamps. Persian (Jalali) display and conversion are done at the system edge; the official Raja date format is still unknown.

## Current Limitations (explicit)

- No Raja adapter exists; a real one must be provided with a documented, written contract before any network code is written.
- `HELD` and `PAYMENT_PENDING` are modelled but no payment is implemented; the simulator reports `pay=False`.
- Orchestration in P3 is implemented, but balance tracking and timeout are absent; provider-result mapping lives in `application/mapping.py`.
- Service (conflict ledger, quarantine, store) is **process-local** and vanishes when the process ends; durable crash recovery is not implemented.
- Provider protocol is **sync**. A real adapter that does I/O must run it outside the event loop; converting to `async` is a deliberate decision for a later phase.
- Domain dependency rule enforced by `tests/unit/test_domain_purity.py`; P2 boundary checks by `tests/unit/test_layer_boundaries.py`.

## Installation

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
 pip install --upgrade pip
 pip install -e ".[dev]"
```

### macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

If `py -3.12` or `python3.12` is not available, use the respective `python3.12+` or newer version installed on the system.

## Running the Simulated CLI

```bash
python -m raja_assistant
```

The CLI presents a Persian-text menu for entering trip details and starting the reservation workflow. The workflow uses the existing `SimulatedReservationProvider` and displays results clearly as **SIMULATED** — never as a real Raja booking.

## Running Tests

```bash
python -m pytest -v       # 408 passed in ~1.5s
python -m pytest -q       # succinct pass/fail
python -m ruff check .    # all checks pass
```

No test requires internet access. `tests/conftest.py` blocks all socket creation; any accidental network attempt fails loudly with `AssertionError`.

## Code Quality

```bash
python -m ruff check .
python -m ruff check . --fix   # auto-fixable only
```

## Reservation State Machine (11 States)

| Status | Meaning | Allowed Transitions |
|---|---|---|
| `DRAFT` | Intent being completed | `VALIDATE` → `VALIDATED`, `VALIDATION_FAILED` → `REJECTED` |
| `VALIDATED` | Local validation and fresh price | `REQUEST_APPROVAL` → `AWAITING_APPROVAL`, `QUOTE_EXPIRED` → `EXPIRED` |
| `AWAITING_APPROVAL` | Waiting for explicit human approval | `APPROVE_AND_SUBMIT` → `SUBMITTING`, `DECLINE` → `CANCELLED`, `QUOTE_EXPIRED` → `EXPIRED` |
| `SUBMITTING` | A submit in progress | `ACK_CONFIRMED`/`ACK_HELD`/`ACK_REJECTED`, `ACK_UNKNOWN` → `SUBMISSION_UNKNOWN`, ineffective errors → `AWAITING_APPROVAL` |
| `SUBMISSION_UNKNOWN` | Unknown result; **locked** | only `RECONCILE_*` |
| `HELD` | Seat held (model-only) | `PAY` → `PAYMENT_PENDING`, `HOLD_EXPIRED` → `EXPIRED` |
| `PAYMENT_PENDING` | Payment awaited (model-only) | `PAYMENT_CONFIRMED` → `CONFIRMED`, `PAYMENT_FAILED` → `REJECTED`, `PAYMENT_AMBIGUOUS` → `SUBMISSION_UNKNOWN` |
| `CONFIRMED` | Final; only with provider reference | — |
| `REJECTED` | Final; only with reason | — |
| `EXPIRED` | Final | — |
| `CANCELLED` | Final | — |

## How to Run the Persian CLI

```bash
python -m raja_assistant
```

The CLI shows a simple Persian menu:

1. **Start reservation workflow** — Enter origin, destination, travel date (validated YYYY-MM-DD), class, and launch the search → quote → approve → submit flow. All results are labeled **SIMULATED**; the final line always includes `--- Sistem: only simulation ---`.
2. **Exit** — Quit the application.

Invalid or malformed dates are caught with a clear Persian error and the user is prompted again. The date is validated before the reservation workflow starts.

## Contact / Handoff

This is a bounded Phase P3 artefact. Real Raja integration requires an authorized, documented API contract and is not implemented in this codebase. For questions about the project structure, the provider protocol, or the state machine, refer to `PROVIDER_INTEGRATION.md` in this repository or the inline source comments.

---

**No LICENSE file is currently included in this repository. No usage permissions are claimed or implied.**