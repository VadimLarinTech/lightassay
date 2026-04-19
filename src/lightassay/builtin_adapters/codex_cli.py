"""Built-in Codex CLI-backed adapter.

Invoked as ``python -m lightassay.builtin_adapters.codex_cli`` by the
built-in ``codex-cli`` backend. Dispatches every adapter operation
through the locally authenticated ``codex`` CLI.

The CLI is always invoked with forced JSON output. The returned payload
is parsed with a single boundary-based utility — see
:mod:`lightassay.builtin_adapters._agent_cli_common`.

See :mod:`lightassay.builtin_adapters` for environment knobs.
"""

from __future__ import annotations

from ._agent_cli_common import run_main

_DEFAULT_COMMAND = ["codex", "exec"]
_JSON_FLAGS = ["--json"]
_BACKEND_LABEL = "codex-cli"


def main() -> None:
    run_main(
        _DEFAULT_COMMAND,
        _BACKEND_LABEL,
        json_flags=_JSON_FLAGS,
        capture_last_message=True,
    )


if __name__ == "__main__":
    main()
