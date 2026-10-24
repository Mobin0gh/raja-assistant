"""Reservation lifecycle: states, events, guards, transitions, idempotency.

Submodules are intentionally not re-exported here. Keeping this package empty
prevents an import cycle with ``raja_assistant.domain.models.reservation``,
which needs the state enum.
"""
