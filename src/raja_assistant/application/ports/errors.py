"""Typed errors for the provider contract.

Outcomes are *values*, not exceptions. A definitive rejection, a hold, a pending
answer, an unavailable provider, and an ambiguous submission are ordinary
results the caller must handle explicitly (see :mod:`models`).

The errors below mean something else entirely: the caller asked for an operation
the adapter cannot legally perform, two identities collided, or a contract
invariant was broken. Raising one of these is always a bug or a safety stop, not
a booking outcome.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for every provider-contract failure."""


class ProviderContractError(ProviderError):
    """A request or result violated the shape the contract promises.

    ``field`` names the offending attribute so callers and tests can assert on a
    stable value instead of parsing a message.
    """

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class ProviderNotAuthorized(ProviderError):
    """The adapter is not permitted to act as a booking provider at all."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"provider {provider!r} is not authorized to perform booking operations")


class CapabilityUnsupported(ProviderError):
    """The adapter is authorized but does not implement the requested operation."""

    def __init__(self, provider: str, capability: str) -> None:
        self.provider = provider
        self.capability = capability
        super().__init__(f"provider {provider!r} does not support the {capability!r} capability")


class DuplicateSubmissionAttempt(ProviderError):
    """A second, differently keyed submission for an intent that is already live.

    This is deliberately *not* a rejection result: nothing was refused by the
    provider, the caller simply tried to submit twice. Callers must reconcile the
    existing submission instead of opening a parallel one.
    """

    def __init__(self, intent_id: str, existing_reference: str) -> None:
        self.intent_id = intent_id
        self.existing_reference = existing_reference
        super().__init__(
            f"intent {intent_id!r} already has live reservation {existing_reference!r}; "
            "reconcile it instead of submitting again"
        )


__all__ = [
    "CapabilityUnsupported",
    "DuplicateSubmissionAttempt",
    "ProviderContractError",
    "ProviderError",
    "ProviderNotAuthorized",
]
