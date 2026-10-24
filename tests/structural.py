"""Structural checks over the service's shape.

These checks are the review gate, not a language guarantee: Python does not stop
a new public method from reaching the store or the provider directly. What the
suite does establish is that the real class owns exactly one write path, exactly
one submission path, and one guard they both pass — and that the negative
controls in ``tests/unit/application/test_structure.py`` are rejected when they
break any of those rules. ``test_structure.py`` is also what makes this module run
under pytest; nothing else imports it.

What the checks do **not** prove, and must not be described as proving:

* a public command misclassified into ``READ_ONLY_COMMANDS`` is still trusted,
  so it may call ``_commit`` without being decorated;
* the ``getattr`` rule catches a computed attribute name, not every possible
  indirection;
* ``clock_references`` is syntactic: it recognises ``datetime.now``,
  ``datetime.datetime.now``, ``date.today``, ``time.sleep`` and friends, not an
  alias assigned somewhere else.

Everything below reads source with :mod:`ast`; it never imports or executes the
code it inspects.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterable

from raja_assistant.application.service import MUTATING_COMMANDS, READ_ONLY_COMMANDS

GUARD_CALL = "self._begin"
STORE_SAVE = "self._store.save"
PROVIDER_SUBMIT = "self._provider.submit"

COMMIT_OWNER = "_commit"
SUBMIT_OWNER = "approve_and_submit"

#: Attribute reads that would mean the code read a clock.
CLOCK_ATTRIBUTES = frozenset({"now", "utcnow", "today"})
#: Owners those reads may belong to (``datetime.now``, ``date.today``, ...).
CLOCK_OWNERS = frozenset({"datetime", "date", "time"})
#: Function calls that would mean the code slept or measured wall time.
SLEEP_CALLS = frozenset({"sleep", "monotonic", "perf_counter"})


def _chain(node: ast.AST) -> str:
    """Return a dotted name such as ``self._store.save`` for an attribute node."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _chain(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _calls(node: ast.AST, chain: str) -> list[ast.Call]:
    """Return every call in ``node`` whose callee is exactly ``chain``."""

    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and _chain(child.func) == chain
    ]


def _is_mutation_decorator(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "mutation"
    if isinstance(node, ast.Attribute):
        return node.attr == "mutation"
    if isinstance(node, ast.Call):
        return _is_mutation_decorator(node.func)
    return False


def _class_node(cls: type) -> ast.ClassDef:
    source = inspect.getsource(cls)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls.__name__:
            return node
    raise AssertionError(f"could not locate the source of class {cls.__name__!r}")


def _functions(class_node: ast.ClassDef) -> Iterable[ast.FunctionDef]:
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef):
            yield node


def _sites(class_node: ast.ClassDef, chain: str) -> list[str]:
    """Return the names of the methods that call ``chain``."""

    return [fn.name for fn in _functions(class_node) if _calls(fn, chain)]


def _clock_owner(node: ast.AST) -> bool:
    """Return True when an attribute belongs to a clock type or module.

    Both ``datetime.now()`` (imported name) and ``datetime.datetime.now()``
    (module-qualified) are recognised, because checking only ``ast.Name``
    bases missed the second form.
    """

    return _chain(node).split(".")[-1] in CLOCK_OWNERS


def assert_mutations_are_guarded(cls: type) -> None:
    """Refuse any public command that is unclassified, unguarded, or writes directly."""

    class_node = _class_node(cls)
    known = MUTATING_COMMANDS | READ_ONLY_COMMANDS
    marked: dict[str, ast.FunctionDef] = {}

    for fn in _functions(class_node):
        if fn.name.startswith("_"):
            continue
        if any(_is_mutation_decorator(item) for item in fn.decorator_list):
            marked[fn.name] = fn
        assert fn.name in known, (
            f"public method {cls.__name__}.{fn.name} is in neither MUTATING_COMMANDS nor "
            "READ_ONLY_COMMANDS; classify it or decorate it with @mutation"
        )

    for name, fn in sorted(marked.items()):
        assert _calls(fn, GUARD_CALL), (
            f"mutating command {cls.__name__}.{name} does not call {GUARD_CALL}; "
            "every mutating command must begin there"
        )

    outside_commit = [name for name in _sites(class_node, STORE_SAVE) if name != COMMIT_OWNER]
    assert not outside_commit, (
        f"{cls.__name__} writes the store outside _commit: {', '.join(outside_commit)}"
    )

    outside_submit = [name for name in _sites(class_node, PROVIDER_SUBMIT) if name != SUBMIT_OWNER]
    assert not outside_submit, (
        f"{cls.__name__} calls provider.submit outside {SUBMIT_OWNER}: "
        f"{', '.join(outside_submit)}"
    )

    for fn in _functions(class_node):
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and _chain(node.func) == "getattr"
                and len(node.args) > 1
                and not isinstance(node.args[1], ast.Constant)
            ):
                raise AssertionError(
                    f"{cls.__name__}.{fn.name} uses getattr with a computed name; "
                    "attribute access must be visible to this check"
                )


def save_sites(cls: type) -> list[str]:
    return _sites(_class_node(cls), STORE_SAVE)


def submit_sites(cls: type) -> list[str]:
    return _sites(_class_node(cls), PROVIDER_SUBMIT)


def clock_references(root) -> list[str]:
    """Return forbidden clock/sleep references found under ``root`` (a directory).

    Both an imported name (``datetime.now()``) and a module-qualified call
    (``datetime.datetime.now()``, ``time.sleep()``) are reported; the check is
    syntactic and sees no alias introduced at runtime.
    """

    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in CLOCK_ATTRIBUTES
                and _clock_owner(node.value)
            ):
                found.append(f"{path.name}: {_chain(node)}")
            if (
                isinstance(node, ast.Call)
                and _chain(node.func).split(".")[-1] in SLEEP_CALLS
            ):
                found.append(f"{path.name}: {_chain(node.func)}()")
    return found


__all__ = [
    "GUARD_CALL",
    "PROVIDER_SUBMIT",
    "STORE_SAVE",
    "assert_mutations_are_guarded",
    "clock_references",
    "save_sites",
    "submit_sites",
]
