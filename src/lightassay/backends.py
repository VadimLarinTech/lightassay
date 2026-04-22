"""Built-in backend registry.

A *backend* is the internal implementation term for a named bundle of a
preparation adapter and a semantic adapter. When the user selects an
agent such as ``claude-cli`` or ``codex-cli`` on the CLI (or passes
``agent=<name>`` through the library), lightassay resolves this into
fully-constructed :class:`PreparationConfig` and :class:`SemanticConfig`
objects that invoke the matching module from
:mod:`lightassay.builtin_adapters`.

Backends do not carry LLM-under-test metadata: the target workflow's
provider / model belong in the workflow config, not here.

Users can still pass their own ``--preparation-config`` /
``--semantic-config`` files to override any or all slots (for custom
adapters or one-off experimentation).

The demo stub adapter is deliberately not registered here. It is a
test-only helper and must not appear as a first-class backend for
everyday users — shipping it would let quickstart "look alive" by
simulating success on an invented target.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from importlib import util as importlib_util

from .errors import EvalError
from .preparation_config import PreparationConfig
from .semantic_config import SemanticConfig


@dataclass(frozen=True)
class BackendDefinition:
    """Metadata for a named built-in backend."""

    name: str
    description: str
    preparation_module: str
    semantic_module: str
    requires_cli: str | None  # local CLI binary this backend expects, or None for in-process


BUILTIN_BACKENDS: dict[str, BackendDefinition] = {
    "claude-cli": BackendDefinition(
        name="claude-cli",
        description=(
            "Dispatches every adapter operation through the locally authenticated "
            "`claude` CLI. Requires the Claude Code / Claude CLI to be installed and "
            "signed in."
        ),
        preparation_module="lightassay.builtin_adapters.claude_cli",
        semantic_module="lightassay.builtin_adapters.claude_cli",
        requires_cli="claude",
    ),
    "codex-cli": BackendDefinition(
        name="codex-cli",
        description=(
            "Dispatches every adapter operation through the locally authenticated "
            "`codex` CLI. Requires the Codex CLI to be installed and signed in."
        ),
        preparation_module="lightassay.builtin_adapters.codex_cli",
        semantic_module="lightassay.builtin_adapters.codex_cli",
        requires_cli="codex",
    ),
}


def list_backends() -> list[str]:
    """Return the set of known built-in backend names, sorted."""
    return sorted(BUILTIN_BACKENDS.keys())


def describe_backends() -> list[tuple[str, str]]:
    """Return ``(name, description)`` tuples for every built-in backend."""
    return [(b.name, b.description) for b in BUILTIN_BACKENDS.values()]


def resolve_backend(name: str) -> tuple[PreparationConfig, SemanticConfig]:
    """Resolve a backend name to constructed preparation + semantic configs.

    Raises ``EvalError`` for an unknown backend name or when the built-in
    module cannot be located (e.g. a corrupt install).
    """
    if name not in BUILTIN_BACKENDS:
        raise EvalError(f"Unknown backend: {name!r}. Known backends: {', '.join(list_backends())}.")
    backend = BUILTIN_BACKENDS[name]

    prep_command, prep_env = _module_command(backend.preparation_module)
    sem_command, sem_env = _module_command(backend.semantic_module)

    prep_config = PreparationConfig(
        adapter=backend.preparation_module,
        provider=name,
        model=name,
        command=prep_command,
        env=prep_env,
    )
    sem_config = SemanticConfig(
        adapter=backend.semantic_module,
        provider=name,
        model=name,
        command=sem_command,
        env=sem_env,
    )
    return prep_config, sem_config


def _module_command(module: str) -> tuple[list[str], dict[str, str] | None]:
    """Return a ``python -m <module>`` command line for the subprocess.

    Built-in adapters ship as package modules inside the ``lightassay``
    distribution. We launch them as ``python -m <module>`` and, when
    necessary, add the discovered package root to ``PYTHONPATH`` so the
    same command works both from an installed distribution and from a
    source tree without relying on accidental ambient import state.
    """
    spec = importlib_util.find_spec(module)
    if spec is None or spec.origin is None:
        raise EvalError(
            f"Built-in adapter module not found: {module!r}. The lightassay install may be corrupt."
        )
    import_root = os.path.abspath(spec.origin)
    for _ in module.split("."):
        import_root = os.path.dirname(import_root)
    path_entries = [import_root]
    for entry in sys.path:
        if not entry:
            continue
        abs_entry = os.path.abspath(entry)
        if abs_entry not in path_entries:
            path_entries.append(abs_entry)
    existing = os.environ.get("PYTHONPATH")
    if existing:
        for entry in existing.split(os.pathsep):
            if not entry:
                continue
            abs_entry = os.path.abspath(entry)
            if abs_entry not in path_entries:
                path_entries.append(abs_entry)
    pythonpath = os.pathsep.join(path_entries)
    return [sys.executable, "-m", module], {"PYTHONPATH": pythonpath}
