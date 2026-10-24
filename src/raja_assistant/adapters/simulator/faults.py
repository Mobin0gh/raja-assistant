"""Explicit, deterministic failure switches for the simulator.

Nothing here is random and nothing sleeps. A test asks for exactly one failure
mode per operation and gets that mode every time, so a failing scenario can be
reproduced from the test definition alone.

At most one submit fault and one lookup fault may be active at once. Refusing
contradictory combinations is deliberate: silently letting one flag win makes a
test that "should" fail pass for the wrong reason.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from raja_assistant.application.ports.errors import ProviderContractError

_SUBMIT_FAULTS = (
    "submit_unavailable",
    "submit_rejected",
    "submit_outcome_unknown",
    "submit_pending",
    "submit_holds_seats",
)

_LOOKUP_FAULTS = (
    "lookup_unavailable",
    "lookup_reports_not_found",
)


@dataclass(frozen=True, slots=True)
class SimulatorFaults:
    """Which failure mode, if any, each operation should report."""

    #: Search cannot reach the provider at all.
    search_unavailable: bool = False

    #: The submission never reached the provider; nothing was booked.
    submit_unavailable: bool = False

    #: The provider definitively refused; nothing was booked.
    submit_rejected: bool = False

    #: The provider accepted the request internally but the answer was lost.
    #: The simulator records a confirmed reservation, then reports ambiguity.
    submit_outcome_unknown: bool = False

    #: The provider accepted the request and has not decided yet.
    submit_pending: bool = False

    #: The provider reserved seats for a limited time instead of confirming.
    submit_holds_seats: bool = False

    #: Lookup cannot reach the provider, so nothing is resolved.
    lookup_unavailable: bool = False

    #: Lookup answers "not found" even though a record exists, simulating data
    #: the provider no longer has. Used to rehearse a wrong "not booked" answer.
    lookup_reports_not_found: bool = False

    def __post_init__(self) -> None:
        for group, names in (
            ("submit", _SUBMIT_FAULTS),
            ("lookup", _LOOKUP_FAULTS),
        ):
            active = tuple(name for name in names if getattr(self, name))
            if len(active) > 1:
                raise ProviderContractError(
                    "faults",
                    f"at most one {group} fault may be active, got {', '.join(active)}",
                )

    @property
    def active_faults(self) -> tuple[str, ...]:
        """Return the names of every enabled switch, in declaration order."""

        return tuple(field.name for field in fields(self) if getattr(self, field.name))


__all__ = ["SimulatorFaults"]
