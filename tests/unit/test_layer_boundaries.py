"""The application and adapter layers may not reach outward.

This is the executable version of the dependency rule for the layers P2 added: the
application knows only the domain, adapters know only the application and the
domain, and neither may import a network, database, or UI package. The simulator
is held to an extra rule: it must not read the clock or any randomness, because
that is what makes it reproducible.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "raja_assistant"
APPLICATION_ROOT = PACKAGE_ROOT / "application"
ADAPTER_ROOT = PACKAGE_ROOT / "adapters"
SIMULATOR_ROOT = ADAPTER_ROOT / "simulator"

ALLOWED_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "types",
    "typing",
    "raja_assistant",
}

ALLOWED_FIRST_PARTY = (
    "raja_assistant.application",
    "raja_assistant.domain",
    "raja_assistant.adapters",
)

FORBIDDEN_IN_SIMULATOR = (
    "datetime.now",
    "datetime.utcnow",
    "datetime.today",
    "time.time",
    "time.monotonic",
    "random.",
    "uuid4",
    "secrets.",
)


def _modules(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _ids(path: Path) -> str:
    return str(path.relative_to(PACKAGE_ROOT))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            modules.append(node.module or "")
    return modules


@pytest.mark.parametrize("path", _modules(APPLICATION_ROOT), ids=_ids)
def test_application_modules_import_only_allowed_roots(path: Path) -> None:
    for module in _imports(path):
        root = module.split(".")[0]
        assert root in ALLOWED_ROOTS, f"{path.name} imports forbidden module {module!r}"


@pytest.mark.parametrize("path", _modules(ADAPTER_ROOT), ids=_ids)
def test_adapter_modules_import_only_allowed_roots(path: Path) -> None:
    for module in _imports(path):
        root = module.split(".")[0]
        assert root in ALLOWED_ROOTS, f"{path.name} imports forbidden module {module!r}"


@pytest.mark.parametrize("path", _modules(APPLICATION_ROOT), ids=_ids)
def test_application_never_imports_an_adapter(path: Path) -> None:
    for module in _imports(path):
        assert not module.startswith("raja_assistant.adapters"), (
            f"{path.name} imports the adapter {module!r}"
        )


@pytest.mark.parametrize("path", _modules(ADAPTER_ROOT), ids=_ids)
def test_adapters_depend_only_on_application_and_domain(path: Path) -> None:
    for module in _imports(path):
        if module == "raja_assistant" or module.startswith("raja_assistant."):
            assert module.startswith(ALLOWED_FIRST_PARTY) or module == "raja_assistant", (
                f"{path.name} imports {module!r}"
            )


@pytest.mark.parametrize("path", _modules(SIMULATOR_ROOT), ids=_ids)
def test_simulator_never_reads_the_clock_or_randomness(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    for marker in FORBIDDEN_IN_SIMULATOR:
        assert marker not in source, f"{path.name} references {marker!r}"


def test_both_layers_exist() -> None:
    assert _modules(APPLICATION_ROOT), "the application layer is missing"
    assert _modules(SIMULATOR_ROOT), "the simulator adapter is missing"


def test_the_suite_blocks_sockets() -> None:
    """The network guard is a test fixture; prove it is actually installed."""

    with pytest.raises(AssertionError):
        socket.socket()
    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 80))
    with pytest.raises(AssertionError):
        socket.getaddrinfo("example.com", 80)
