"""Global test guards.

The suite must never reach the network. Any accidental socket use — a stray HTTP
client in an adapter, a fixture that resolves a hostname — fails loudly here
instead of silently depending on connectivity or on a remote service being up.
"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every attempt to create or connect a socket."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("tests must not open network connections")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "socketpair", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
