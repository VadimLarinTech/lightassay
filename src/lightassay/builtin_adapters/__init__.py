"""First-party built-in adapter modules.

Each module in this package is invokable as ``python -m
lightassay.builtin_adapters.<name>`` and implements the full
preparation + semantic + bootstrap adapter contract over stdin/stdout.

The modules here are what the user-facing built-in agents
(``lightassay quickstart --agent <name>`` / ``continue --agent <name>``)
use under the hood. Users do not need to author preparation/semantic
config JSON to use them.

Available built-in agents:

- ``claude_cli`` — dispatches every operation through the locally
  authenticated ``claude`` CLI (the user's Anthropic subscription).
- ``codex_cli`` — dispatches every operation through the locally
  authenticated ``codex`` CLI (the user's OpenAI subscription).

Internal test helper modules also live here, including ``stub``. They
are not part of the ordinary agent surface and must not be presented
to users as real evaluation agents.

Environment knobs for CLI-backed adapters (``claude_cli``, ``codex_cli``):

- ``LIGHTASSAY_AGENT_CMD``        — override the base CLI invocation.
- ``LIGHTASSAY_AGENT_JSON_FLAG``  — extra flags appended to the CLI
  invocation (e.g. ``--output-format json``).
"""
