"""The structural review gate: it runs here, and it rejects rogue shapes.

The checks themselves live in ``tests/structural.py``. This module is what makes
them part of the normal pytest run, and it proves they are not vacuous by feeding
them deliberately broken classes. They are source checks over the real service,
not a language guarantee; the blind spots listed in ``structural.py`` remain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raja_assistant.application.service import (
    MUTATING_COMMANDS,
    READ_ONLY_COMMANDS,
    ReservationService,
    mutation,
)
from structural import (
    assert_mutations_are_guarded,
    clock_references,
    save_sites,
    submit_sites,
)

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src" / "raja_assistant"


# --------------------------------------------------------------------------- #
# Positive controls: the real service satisfies every rule
# --------------------------------------------------------------------------- #


def test_the_real_service_passes_the_structural_gate() -> None:
    assert_mutations_are_guarded(ReservationService)


def test_every_public_method_is_classified() -> None:
    public = {
        name
        for name in vars(ReservationService)
        if not name.startswith("_") and callable(getattr(ReservationService, name))
    }

    assert public == set(MUTATING_COMMANDS) | set(READ_ONLY_COMMANDS)


def test_the_mutating_registry_is_derived_from_the_decorator() -> None:
    assert frozenset(
        {
            "open_quote",
            "approve_and_submit",
            "reconcile",
            "decline",
            "expire_quote",
        }
    ) == MUTATING_COMMANDS
    for name in MUTATING_COMMANDS:
        assert getattr(getattr(ReservationService, name), "is_mutation", False), name
    for name in READ_ONLY_COMMANDS:
        assert not getattr(getattr(ReservationService, name), "is_mutation", False), name


def test_the_service_has_one_write_path_and_one_submit_site() -> None:
    assert save_sites(ReservationService) == ["_commit"]
    assert submit_sites(ReservationService) == ["approve_and_submit"]


def test_no_source_module_reads_a_clock_or_sleeps() -> None:
    assert SOURCE_ROOT.is_dir()
    assert clock_references(SOURCE_ROOT) == []


# --------------------------------------------------------------------------- #
# Negative controls: deliberately broken shapes are rejected
# --------------------------------------------------------------------------- #


class _UnclassifiedCommand:
    """A public command that nobody registered anywhere."""

    def mystery_command(self) -> None:
        return None


class _DirectStoreWrite:
    """A command with a read-only name that writes the store directly."""

    def get(self) -> None:
        self._store.save(object())


class _ExtraSubmitSite:
    """A second place that calls the provider's submit operation."""

    def get(self) -> None:
        self._provider.submit(object())


class _UnguardedMutator:
    """A decorated mutating command that never passes the shared guard."""

    @mutation
    def approve_and_submit(self, reservation_id: str) -> None:
        return None


class _MisclassifiedCommit:
    """A documented blind spot: a read-only name that calls ``_commit``."""

    def get(self) -> None:
        self._commit(object())


def test_an_unclassified_public_command_is_rejected() -> None:
    with pytest.raises(AssertionError, match="in neither MUTATING_COMMANDS nor"):
        assert_mutations_are_guarded(_UnclassifiedCommand)


def test_a_direct_store_write_outside_commit_is_rejected() -> None:
    with pytest.raises(AssertionError, match="writes the store outside _commit"):
        assert_mutations_are_guarded(_DirectStoreWrite)


def test_a_second_provider_submit_site_is_rejected() -> None:
    with pytest.raises(AssertionError, match=r"calls provider\.submit outside"):
        assert_mutations_are_guarded(_ExtraSubmitSite)


def test_a_mutating_command_without_the_guard_is_rejected() -> None:
    with pytest.raises(AssertionError, match=r"does not call self\._begin"):
        assert_mutations_are_guarded(_UnguardedMutator)


def test_the_gate_does_not_detect_a_misclassified_commit_caller() -> None:
    """Pins the documented limit: read-only names are trusted, not verified."""

    assert assert_mutations_are_guarded(_MisclassifiedCommit) is None


# --------------------------------------------------------------------------- #
# The clock/sleep checker actually detects violations
# --------------------------------------------------------------------------- #


def test_the_clock_checker_detects_an_imported_clock_read(tmp_path: Path) -> None:
    module = tmp_path / "bad_clock.py"
    module.write_text(
        "from datetime import datetime\n"
        "\n"
        "def stamp():\n"
        "    return datetime.now()\n",
        encoding="utf-8",
    )

    assert clock_references(tmp_path) == ["bad_clock.py: datetime.now"]


def test_the_clock_checker_detects_qualified_reads_and_sleep(tmp_path: Path) -> None:
    module = tmp_path / "bad_sleep.py"
    module.write_text(
        "import datetime\n"
        "import time\n"
        "\n"
        "def stamp():\n"
        "    moment = datetime.datetime.now()\n"
        "    time.sleep(1)\n"
        "    return moment\n",
        encoding="utf-8",
    )

    assert sorted(clock_references(tmp_path)) == [
        "bad_sleep.py: datetime.datetime.now",
        "bad_sleep.py: time.sleep()",
    ]


# --------------------------------------------------------------------------- #
# The decorator guards both positional and keyword-only reservation ids
# --------------------------------------------------------------------------- #


class _GuardRecorder:
    """Records the guard calls the ``mutation`` wrapper makes."""

    def __init__(self) -> None:
        self.checked: list[tuple[str, str]] = []

    def _require_clear(self, reservation_id: str, command: str) -> None:
        self.checked.append((reservation_id, command))

    @mutation
    def positional(self, reservation_id: str) -> str:
        return reservation_id

    @mutation
    def keyword_only(self, *, reservation_id: str) -> str:
        return reservation_id

    @mutation
    def no_reservation_id(self) -> str:
        return "created"


def test_the_wrapper_guards_a_positional_reservation_id() -> None:
    recorder = _GuardRecorder()

    assert recorder.positional("rsv-1") == "rsv-1"

    assert recorder.checked == [("rsv-1", "positional")]


def test_the_wrapper_guards_a_keyword_only_reservation_id() -> None:
    recorder = _GuardRecorder()

    assert recorder.keyword_only(reservation_id="rsv-2") == "rsv-2"

    assert recorder.checked == [("rsv-2", "keyword_only")]


def test_the_wrapper_leaves_creation_commands_to_their_own_body() -> None:
    recorder = _GuardRecorder()

    assert recorder.no_reservation_id() == "created"

    assert recorder.checked == []
