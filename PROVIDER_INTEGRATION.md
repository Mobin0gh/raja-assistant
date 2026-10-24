# Provider Integration Protocol

This document describes the `ReservationProvider` protocol that any adapter must implement
to interop with the raja-assistant orchestration service. It is based on the actual
`application/ports/provider.py` and `application/ports/models.py` files in the repository.

## Three Operations

The protocol defines exactly three operations. An adapter must implement all three;
additional methods are not part of the contract.

### `search(preferences, now)`

- **Purpose**: Return the services the provider offers for validated preferences.
- **Parameters**:
  - `preferences` (TripPreferences): Validated travel preferences (origin, destination,
    travel date, party size, quota class).
  - `now` (datetime, passed in): Reproducibility — the caller controls the timestamp.
- **Returns**: `SearchResult` — either `SearchSucceeded(offers, observed_at)` or
  `ProviderUnavailable(observed_at, detail)`.
- **Key obligation**: `ProviderUnavailable` may be returned **only** when the adapter
  can guarantee the request was **not** processed (no side effect). A post-send
  timeout or lost acknowledgement must be `SubmissionOutcomeUnknown`, never
  `ProviderUnavailable`.

### `submit(request)`

- **Purpose**: Submit one attempt, identified by its idempotency key.
- **Parameters**:
  - `request` (SubmissionRequest): Carries the idempotency key, intent, and attempt epoch.
- **Returns**: `SubmissionResult` — one of:
  - `ReservationConfirmed(provider_reference, observed_at)`
  - `ReservationHeld(provider_reference, hold_expires_at, observed_at)`
  - `ReservationPending(client_reference, observed_at)`
  - `ReservationRejected(reason, observed_at, detail=None)`
  - `SubmissionOutcomeUnknown(client_reference, observed_at, detail=None)`
  - `ProviderUnavailable(observed_at, detail)`
- **Key obligations**:
  - Must raise `DuplicateSubmissionAttempt` rather than open a parallel reservation
    when the intent already has a live one.
  - Must return `SubmissionOutcomeUnknown` for any post-send ambiguity (timeout,
    connection reset, lost acknowledgement). Never reclassify as `ProviderUnavailable`
    or `ReservationRejected` unless the adapter can definitively confirm no booking was
    made.
  - May return `ProviderUnavailable` only when no side effect occurred.

### `lookup(request)`

- **Purpose**: Report what the provider knows about one previously submitted attempt.
- **Parameters**:
  - `request` (LookupRequest): Keyed by the client's idempotency key and intent ID.
- **Returns**: `LookupResult` — one of:
  - `ReservationConfirmed(provider_reference, observed_at)`
  - `ReservationHeld(provider_reference, hold_expires_at, observed_at)`
  - `ReservationPending(client_reference, observed_at)`
  - `ReservationNotFound(client_reference, observed_at)`
  - `ProviderUnavailable(observed_at, detail)`
- **Key obligation**: Lookup must be keyed by the client's identity, never by a provider
  reference. After an ambiguous submission the caller may not have a reference yet, and
  guessing one is how applications book twice.

## Request Types

### `SubmissionRequest`

- **idempotency_key** (str): Must be produced by `derive_idempotency_key()` from the
  domain: `intent_id`, `service_id`, `travel_date`, `passenger_refs`, `quota_class`,
  and `attempt_epoch`. The protocol refuses to build a request with a key that does
  not match its intent and attempt epoch.
- **intent** (ReservationIntent): The domain intent being submitted.
- **attempt_epoch** (int): Attempt sequence number (default 1). A replayed submission
  with the same key returns the first outcome; a new epoch produces a new key and may
  be rejected as `DuplicateSubmissionAttempt`.
- **submitted_at** (datetime): Must be timezone-aware (`require_aware`).

### `LookupRequest`

- **idempotency_key** (str): Same derivation rules as `SubmissionRequest`.
- **intent_id** (str): The intent identifier, max 64 characters.
- **requested_at** (datetime): Must be timezone-aware.

## Outcome Semantics

Every result class exposes one of these as a class-level `outcome`:

| Outcome Value | Meaning |
|---|---|
| `found` | Provider located a service (search only) |
| `confirmed` | Provider states a reservation exists with `provider_reference` |
| `held` | Seats held but not yet a reservation; hold expires on its own |
| `pending` | Provider accepted but has not decided yet |
| `rejected` | Definitive refusal; adapter confirms nothing was booked |
| `not_found` | Lookup answered definitively: no reservation exists |
| `outcome_unknown` | Provider may or may not have acted; only resolvable by lookup |
| `unavailable` | Temporary condition; adapter guarantees no side effect occurred |

## Key Distinctions the Adapter Must Honour

| Result | When Allowed | Meaning |
|---|---|---|
| `ProviderUnavailable` | Adapter guarantees no side effect | Request was not processed; nothing booked |
| `SubmissionOutcomeUnknown` | Post-send timeout, reset, lost ack | Request may have been processed; ambiguous |
| `ReservationRejected` | Adapter confirms no reservation was made | Definitive refusal; nothing was booked |

**Never reclassify**: No timeout or transport error may be reclassified to make the
orchestrator's job easier. The mapping from transport events to these three results
is part of what an adapter is approved to do.

## How a Future Adapter Should Be Wired

1. **Implement `ReservationProvider`** as a runtime-checked protocol (use
   `@runtime_checkable` from `typing`).
2. **Declare `capabilities`** (`ProviderCapabilities`) with:
   - `authorized: bool` — set to `False` for a disabled real-provider adapter;
     every submission path stops at `require_booking_capability()`.
   - `book: bool` — set to `True` if the adapter can book.
3. **Implement `search()`**: Return `SearchSucceeded(offers, observed_at)` or
   `ProviderUnavailable(observed_at, detail)`.
4. **Implement `submit(request)`**: Build a `SubmissionRequest` from the intent,
   call the real API, and map the response to one of the `SubmissionResult` types.
   Return `SubmissionOutcomeUnknown` for any ambiguity; return `ProviderUnavailable`
   only when no side effect occurred.
5. **Implement `lookup(request)`**: Query the real API with the idempotency key and
   map the response to a `LookupResult` type.
6. **Never** reclassify transport errors. The three outcome categories above are
   strict and the orchestrator depends on them being honoured.
7. **Register** the provider in the service: `ReservationService(provider=provider)`.
8. **Test** with the simulator fixture first; then replace with the real adapter
   without changing application code (the orchestrator only sees the protocol).

## Error Types (Port-Level)

| Error | When Raised |
|---|---|
| `ProviderNotAuthorized` | `require_booking_capability()` called and `capabilities.authorized == False` |
| `CapabilityUnsupported` | `require_booking_capability()` called and `capabilities.book == False` |
| `ProviderContractError` | Request shape violation (blank timestamp, bad reference key, etc.) |
| `DuplicateSubmissionAttempt` | Replayed submission with the same idempotency key |

None of these errors represent a "rejection" of a booking. They are contract or capability
violations.

## Real Raja Integration Status

**Real Raja integration is not implemented** in this codebase. The protocol and simulator
were designed as a bounded, deterministic Phase P3 artefact. A future real adapter must:

- Honour the exact outcome categories above (`ProviderUnavailable`, `SubmissionOutcomeUnknown`,
  `ReservationRejected`).
- Never invent endpoints, payloads, or capabilities not documented here.
- Provide a documented API contract (OpenAPI/Swagger or equivalent) before any network code
  is written.
- Be shipped with `capabilities.authorized == False` and `capabilities.book == False` until
  a full review and test suite confirms compliance.
- Run all existing tests unchanged; the `tests/conftest.py` network guard must remain in place.

Until an authorized, documented interface is provided, the only operational 
