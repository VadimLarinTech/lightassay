"""Built-in Claude CLI-backed adapter.

Invoked as ``python -m lightassay.builtin_adapters.claude_cli`` by the
built-in ``claude-cli`` backend. Dispatches every adapter operation
(bootstrap, generate_directions, generate_cases, reconcile_readiness,
analyze, compare) through the locally authenticated ``claude`` CLI.

The CLI is always invoked with forced JSON output. The returned payload
is parsed with a single boundary-based utility — see
:mod:`lightassay.builtin_adapters._agent_cli_common`.

See :mod:`lightassay.builtin_adapters` for environment knobs.
"""

from __future__ import annotations

from ._agent_cli_common import run_main

_DEFAULT_COMMAND = ["claude", "-p"]
_JSON_FLAGS = ["--output-format", "json"]
_BACKEND_LABEL = "claude-cli"


def main() -> None:
    run_main(_DEFAULT_COMMAND, _BACKEND_LABEL, json_flags=_JSON_FLAGS)


if __name__ == "__main__":
    main()
