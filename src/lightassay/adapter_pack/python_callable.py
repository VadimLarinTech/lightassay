"""python-callable driver: call a Python function directly.

The configured ``module`` is imported via ``importlib.import_module``
and the configured ``function`` is looked up as an attribute.

The function must accept a single ``dict`` (adapter request) and return
a ``dict`` (adapter response) conforming to the standard response
contract.

No subprocess overhead.  The function runs in the same process.
"""

from __future__ import annotations

import importlib

from . import DriverError, PythonCallableDriverConfig


def execute(config: PythonCallableDriverConfig, request_data: dict) -> dict:
    """Execute the python-callable driver.

    Raises ``DriverError`` on import failure, missing function, or
    if the function raises an exception or returns a non-dict value.
    """
    # Import the module.
    try:
        module = importlib.import_module(config.module)
    except ImportError as exc:
        raise DriverError(
            f"python-callable driver: failed to import module {config.module!r}: {exc}"
        ) from exc

    # Look up the function.
    if not hasattr(module, config.function):
        raise DriverError(
            f"python-callable driver: module {config.module!r} has no attribute {config.function!r}"
        )

    func = getattr(module, config.function)
    if not callable(func):
        raise DriverError(
            f"python-callable driver: {config.module}.{config.function} is not callable"
        )

    # Call the function.
    try:
        response = func(request_data)
    except Exception as exc:
        raise DriverError(
            f"python-callable driver: function "
            f"{config.module}.{config.function} raised {type(exc).__name__}: "
            f"{exc}"
        ) from exc

    if not isinstance(response, dict):
        raise DriverError(
            f"python-callable driver: function "
            f"{config.module}.{config.function} must return a dict, "
            f"got {type(response).__name__}"
        )

    return response
