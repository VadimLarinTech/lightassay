"""First-party adapter pack for common workflow integration shapes.

This module ships generic drivers for three common integration patterns:

- ``python-callable``: call a Python function directly (no subprocess)
- ``http``: call an HTTP endpoint with JSON request/response
- ``command``: run an explicit command list as a subprocess

Drivers are selected via the ``driver`` field in workflow config (see
``docs/adapter_pack_spec.md``).  Each driver produces the same response
contract as the raw subprocess adapter protocol.

The legacy ``adapter`` field (raw executable path) remains supported.
Exactly one of ``adapter`` or ``driver`` must be present in a workflow
config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

# ── Driver error ────────────────────────────────────────────────────────────


class DriverError(Exception):
    """Raised when a first-party driver fails to execute.

    The message is recorded as the ``execution_error`` in the case record,
    identical to how subprocess adapter failures are recorded.
    """


# ── Driver config types ────────────────────────────────────────────────────

DRIVER_TYPE_PYTHON_CALLABLE = "python-callable"
DRIVER_TYPE_HTTP = "http"
DRIVER_TYPE_COMMAND = "command"

KNOWN_DRIVER_TYPES = frozenset(
    {
        DRIVER_TYPE_PYTHON_CALLABLE,
        DRIVER_TYPE_HTTP,
        DRIVER_TYPE_COMMAND,
    }
)


@dataclass(frozen=True)
class PythonCallableDriverConfig:
    """Config for the ``python-callable`` driver.

    ``module`` is a dotted Python module path (e.g. ``my_package.adapter``).
    ``function`` is the function name within that module.

    The function must accept a single ``dict`` argument (the adapter request)
    and return a ``dict`` (the adapter response) conforming to the standard
    response contract.
    """

    module: str
    function: str


@dataclass(frozen=True)
class HttpDriverConfig:
    """Config for the ``http`` driver.

    ``url`` is the full HTTP endpoint URL.
    ``method`` is the HTTP method (e.g. ``"POST"``).
    ``headers`` is an optional dict of extra HTTP headers.
    ``timeout_seconds`` is an optional request timeout in seconds.
    If ``timeout_seconds`` is absent, no timeout is enforced (consistent
    with the v1 subprocess protocol).
    """

    url: str
    method: str
    headers: dict[str, str] | None
    timeout_seconds: int | None


@dataclass(frozen=True)
class CommandDriverConfig:
    """Config for the ``command`` driver.

    ``command`` is a non-empty list of strings forming the subprocess
    command (e.g. ``["python3", "my_adapter.py"]``).

    ``config_dir`` is the absolute path to the directory containing the
    workflow config file.  When set, the subprocess runs with this as its
    working directory, so relative paths in the command array resolve
    against the config file location rather than the caller's cwd.
    This field is injected by ``load_workflow_config``, not by the user's
    JSON config.

    The subprocess receives the adapter request JSON on stdin and must
    write the adapter response JSON to stdout, identical to the raw
    subprocess protocol.
    """

    command: list[str]
    config_dir: str | None = None


DriverConfig = Union[
    PythonCallableDriverConfig,
    HttpDriverConfig,
    CommandDriverConfig,
]


# ── Driver config validation ───────────────────────────────────────────────

_PYTHON_CALLABLE_REQUIRED = {"module", "function"}
_HTTP_REQUIRED = {"url", "method"}
_HTTP_OPTIONAL = {"headers", "timeout_seconds"}
_COMMAND_REQUIRED = {"command"}


def validate_driver_config(data: dict) -> DriverConfig:
    """Validate a raw driver config dict and return a typed DriverConfig.

    Raises ``ValueError`` with a descriptive message on any violation.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Driver config must be a JSON object, got {type(data).__name__}")

    if "type" not in data:
        raise ValueError("Driver config missing required field: 'type'")

    driver_type = data["type"]
    if not isinstance(driver_type, str):
        raise ValueError(
            f"Driver config field 'type' must be a string, got {type(driver_type).__name__}"
        )

    if driver_type not in KNOWN_DRIVER_TYPES:
        raise ValueError(
            f"Unknown driver type: {driver_type!r}. "
            f"Known types: {', '.join(sorted(KNOWN_DRIVER_TYPES))}"
        )

    # Remaining fields (excluding 'type') for per-type validation.
    fields = {k: v for k, v in data.items() if k != "type"}

    if driver_type == DRIVER_TYPE_PYTHON_CALLABLE:
        return _validate_python_callable(fields)
    elif driver_type == DRIVER_TYPE_HTTP:
        return _validate_http(fields)
    elif driver_type == DRIVER_TYPE_COMMAND:
        return _validate_command(fields)
    else:
        # Unreachable due to KNOWN_DRIVER_TYPES check above.
        raise ValueError(f"Unknown driver type: {driver_type!r}")


def _validate_python_callable(fields: dict) -> PythonCallableDriverConfig:
    unknown = set(fields.keys()) - _PYTHON_CALLABLE_REQUIRED
    if unknown:
        raise ValueError(
            f"python-callable driver has unknown fields: "
            f"{', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(_PYTHON_CALLABLE_REQUIRED))}"
        )

    for name in sorted(_PYTHON_CALLABLE_REQUIRED):
        if name not in fields:
            raise ValueError(f"python-callable driver missing required field: {name!r}")
        val = fields[name]
        if not isinstance(val, str):
            raise ValueError(
                f"python-callable driver field {name!r} must be a string, got {type(val).__name__}"
            )
        if not val.strip():
            raise ValueError(f"python-callable driver field {name!r} must be non-empty")

    return PythonCallableDriverConfig(
        module=fields["module"],
        function=fields["function"],
    )


def _validate_http(fields: dict) -> HttpDriverConfig:
    allowed = _HTTP_REQUIRED | _HTTP_OPTIONAL
    unknown = set(fields.keys()) - allowed
    if unknown:
        raise ValueError(
            f"http driver has unknown fields: "
            f"{', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )

    for name in sorted(_HTTP_REQUIRED):
        if name not in fields:
            raise ValueError(f"http driver missing required field: {name!r}")
        val = fields[name]
        if not isinstance(val, str):
            raise ValueError(
                f"http driver field {name!r} must be a string, got {type(val).__name__}"
            )
        if not val.strip():
            raise ValueError(f"http driver field {name!r} must be non-empty")

    headers = None
    if "headers" in fields:
        h = fields["headers"]
        if not isinstance(h, dict):
            raise ValueError(
                f"http driver field 'headers' must be a JSON object, got {type(h).__name__}"
            )
        for k, v in h.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError(
                    "http driver field 'headers' must be a dict of string keys and string values"
                )
        headers = h

    timeout = None
    if "timeout_seconds" in fields:
        t = fields["timeout_seconds"]
        if not isinstance(t, int) or isinstance(t, bool):
            raise ValueError(
                f"http driver field 'timeout_seconds' must be an integer, got {type(t).__name__}"
            )
        if t <= 0:
            raise ValueError("http driver field 'timeout_seconds' must be positive")
        timeout = t

    return HttpDriverConfig(
        url=fields["url"],
        method=fields["method"],
        headers=headers,
        timeout_seconds=timeout,
    )


def _validate_command(fields: dict) -> CommandDriverConfig:
    unknown = set(fields.keys()) - _COMMAND_REQUIRED
    if unknown:
        raise ValueError(
            f"command driver has unknown fields: "
            f"{', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(_COMMAND_REQUIRED))}"
        )

    if "command" not in fields:
        raise ValueError("command driver missing required field: 'command'")

    cmd = fields["command"]
    if not isinstance(cmd, list):
        raise ValueError(
            f"command driver field 'command' must be a JSON array, got {type(cmd).__name__}"
        )
    if not cmd:
        raise ValueError("command driver field 'command' must be a non-empty array")
    for i, item in enumerate(cmd):
        if not isinstance(item, str):
            raise ValueError(
                f"command driver field 'command[{i}]' must be a string, got {type(item).__name__}"
            )
        if not item.strip():
            raise ValueError(f"command driver field 'command[{i}]' must be non-empty")

    return CommandDriverConfig(command=cmd)


# ── Driver dispatch ─────────────────────────────────────────────────────────


def execute_driver(config: DriverConfig, request_data: dict) -> dict:
    """Execute a first-party driver with the given request data.

    Returns the adapter response dict on success.
    Raises ``DriverError`` on any execution failure.

    The response dict must conform to the standard adapter response
    contract (``raw_response``, ``parsed_response``, ``usage``).
    Response validation is the caller's responsibility (the runner
    applies the same strict validation as for subprocess adapters).
    """
    if isinstance(config, PythonCallableDriverConfig):
        from .python_callable import execute as _execute_callable

        return _execute_callable(config, request_data)
    elif isinstance(config, HttpDriverConfig):
        from .http_driver import execute as _execute_http

        return _execute_http(config, request_data)
    elif isinstance(config, CommandDriverConfig):
        from .command import execute as _execute_command

        return _execute_command(config, request_data)
    else:
        raise DriverError(f"Unknown driver config type: {type(config).__name__}")
