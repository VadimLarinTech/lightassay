"""command driver: run an explicit command list as a subprocess.

Similar to the legacy raw executable adapter path, but the command is
specified as an explicit list of strings rather than a single executable
path.  This allows arguments, interpreters, and flags to be specified
directly in the workflow config.

The subprocess receives the adapter request JSON on stdin and must
write the adapter response JSON to stdout.

**Working-directory semantics:** when ``CommandDriverConfig.working_dir``
is set, the subprocess runs with ``cwd=working_dir``. Otherwise, if only
``config_dir`` is set, the subprocess runs with ``cwd=config_dir`` so
existing config-origin semantics are preserved. Generated quickstart
configs may set ``working_dir`` to the workspace root while still living
under ``--output-dir``.

**Non-zero exit diagnostics:** when the subprocess exits with a non-zero
code, a bounded excerpt of its stdout is included in the ``DriverError``
message so that adapter-side diagnostic output is not silently lost.
"""

from __future__ import annotations

import json
import subprocess

from . import CommandDriverConfig, DriverError

# Maximum number of characters to include from stdout when surfacing
# a non-zero exit error.  Large enough to be diagnostic, bounded to
# avoid unbounded error messages.
_STDOUT_EXCERPT_LIMIT = 2000


def execute(config: CommandDriverConfig, request_data: dict) -> dict:
    """Execute the command driver.

    When ``config.working_dir`` is set, the subprocess runs with that
    directory as its working directory. Otherwise ``config.config_dir``
    is used when present.

    Raises ``DriverError`` on subprocess failures (non-zero exit,
    invalid JSON output, non-dict response, not found, not executable).
    On non-zero exit, the error includes a bounded stdout excerpt.
    """
    request_json = json.dumps(request_data, ensure_ascii=False)

    run_kwargs: dict = {}
    if config.working_dir is not None:
        run_kwargs["cwd"] = config.working_dir
    elif config.config_dir is not None:
        run_kwargs["cwd"] = config.config_dir

    try:
        result = subprocess.run(
            config.command,
            input=request_json,
            capture_output=True,
            text=True,
            **run_kwargs,
        )
    except FileNotFoundError:
        raise DriverError(f"command driver: command not found: {config.command[0]!r}") from None
    except PermissionError:
        raise DriverError(
            f"command driver: command not executable: {config.command[0]!r}"
        ) from None

    if result.returncode != 0:
        msg = f"command driver: command exited with code {result.returncode}"
        stdout_excerpt = (result.stdout or "")[:_STDOUT_EXCERPT_LIMIT]
        if stdout_excerpt:
            msg += f"; stdout: {stdout_excerpt}"
        raise DriverError(msg)

    stdout = result.stdout
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        raise DriverError("command driver: command stdout is not valid JSON") from None

    if not isinstance(response, dict):
        raise DriverError(
            f"command driver: response must be a JSON object, got {type(response).__name__}"
        )

    return response
