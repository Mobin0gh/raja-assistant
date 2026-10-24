"""The domain layer must stay free of network, database, and framework imports.

This test is the executable version of the architecture's dependency rule. It
fails the build rather than trusting review discipline.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DOMAIN_ROOT = Path(__file__).resolve().parents[2] / "src" / "raja_assistant" / "domain"

ALLOWED_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "raja_assistant",
    "re",
    "typing",
}

FORBIDDEN_ROOTS = {
    "aiohttp",
    "asyncio",
    "http",
    "httpx",
    "keyring",
    "pydantic",
    "pydantic_settings",
    "requests",
    "selenium",
    "socket",
    "sqlite3",
    "ssl",
    "urllib",
    "playwright",
    "PySide6",
    "tkinter",
}


def _domain_files() -> list[Path]:
    return sorted(DOMAIN_ROOT.rglob("*.py"))


def test_domain_package_exists() -> None:
    assert _domain_files(), "no domain modules were found"


@pytest.mark.parametrize("path", _domain_files(), ids=lambda path: path.name)
def test_domain_modules_import_only_standard_library_and_domain(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            roots = [(node.module or "").split(".")[0]]
        else:
            continue
        for root in roots:
            assert root in ALLOWED_ROOTS, f"{path.name} imports forbidden module {root!r}"


@pytest.mark.parametrize("path", _domain_files(), ids=lambda path: path.name)
def test_domain_modules_never_reference_forbidden_packages(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    for module in FORBIDDEN_ROOTS:
        assert f"import {module}" not in source, f"{path.name} references {module!r}"
