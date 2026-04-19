"""http driver: call an HTTP endpoint with JSON request/response.

The adapter request dict is serialized as JSON and sent to the
configured URL using the configured HTTP method.  The response body
is parsed as JSON and returned as the adapter response dict.

Uses ``urllib.request`` from the standard library (no third-party
dependency).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import DriverError, HttpDriverConfig


def execute(config: HttpDriverConfig, request_data: dict) -> dict:
    """Execute the http driver.

    Raises ``DriverError`` on network errors, non-2xx responses,
    invalid JSON in the response body, or if the response is not a dict.
    """
    request_json = json.dumps(request_data, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        config.url,
        data=request_json,
        method=config.method,
    )
    req.add_header("Content-Type", "application/json")

    if config.headers is not None:
        for key, value in config.headers.items():
            req.add_header(key, value)

    timeout_kwargs: dict = {}
    if config.timeout_seconds is not None:
        timeout_kwargs["timeout"] = config.timeout_seconds

    try:
        with urllib.request.urlopen(req, **timeout_kwargs) as response:
            try:
                response_body = response.read().decode("utf-8")
            except Exception as exc:
                raise DriverError(
                    f"http driver: failed to read response body: {type(exc).__name__}: {exc}"
                ) from exc
    except urllib.error.HTTPError as exc:
        exc.close()
        raise DriverError(f"http driver: HTTP {exc.code} from {config.url!r}") from exc
    except urllib.error.URLError as exc:
        raise DriverError(
            f"http driver: connection failed to {config.url!r}: {exc.reason}"
        ) from exc
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(
            f"http driver: request failed to {config.url!r}: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        result = json.loads(response_body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DriverError("http driver: response body is not valid JSON") from exc

    if not isinstance(result, dict):
        raise DriverError(
            f"http driver: response must be a JSON object, got {type(result).__name__}"
        )

    return result
