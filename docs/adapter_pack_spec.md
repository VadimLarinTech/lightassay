# Adapter Pack Spec

## Purpose

The adapter pack ships first-party generic drivers for common workflow integration shapes. Instead of writing a custom subprocess adapter for every workflow, users can use a shipped driver directly in the workflow config.

The pack does not replace custom adapters — it reduces the need for them in common cases.

## Shipped drivers

| Driver | Config type | Use case |
|--------|------------|----------|
| `python-callable` | object | Call a Python function directly (no subprocess) |
| `http` | object | Call an HTTP endpoint with JSON request/response |
| `command` | object | Run an explicit command list as subprocess |

The legacy `adapter` field (raw executable path) remains supported alongside the new `driver` field.

## Config shape

A workflow config must contain exactly one of `adapter` or `driver`. Both present or both absent is a validation error.

### Legacy adapter (unchanged)

```json
{
  "workflow_id": "my-workflow",
  "adapter": "./my_adapter.py"
}
```

### First-party driver

```json
{
  "workflow_id": "my-workflow",
  "driver": {
    "type": "<driver-type>",
    ...driver-specific fields...
  }
}
```

Optional LLM metadata may also be provided either via legacy top-level
`provider` / `model` keys or via a dedicated `llm_metadata` object when
the workflow under test is itself an LLM call. Plain callable / HTTP /
command workflows should not invent placeholder values.

The `driver` object must contain a `type` field that selects the driver, plus driver-specific required and optional fields. Unknown fields are rejected. No defaults, no guessing.

## Driver: `python-callable`

Calls a Python function directly in the same process. No subprocess overhead.

### Config

```json
{
  "type": "python-callable",
  "module": "my_package.adapter",
  "function": "handle_request"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"python-callable"` | yes | Driver selector |
| `module` | string, non-empty | yes | Dotted Python module path |
| `function` | string, non-empty | yes | Function name within the module |

No optional fields.

### Function contract

The function must:
- Accept a single `dict` argument (the adapter request)
- Return a `dict` (the adapter response) conforming to the standard response contract

```python
def handle_request(request: dict) -> dict:
    # request always has: case_id, input, context, workflow_id
    # request may also include: provider, model
    return {
        "raw_response": "...",
        "parsed_response": {...} or None,
        "usage": {
            "input_tokens": 42,
            "output_tokens": 17,
        },
    }
```

### Module resolution

The `module` is imported via `importlib.import_module`. The module must be importable from the Python path at execution time. The runner does not manipulate `sys.path`.

### Error conditions

| Condition | Error |
|-----------|-------|
| Module not importable | `"failed to import module"` |
| Function not found in module | `"has no attribute"` |
| Function not callable | `"is not callable"` |
| Function raises exception | `"raised <ExceptionType>: <message>"` |
| Function returns non-dict | `"must return a dict"` |

## Driver: `http`

Calls an HTTP endpoint with a JSON request body and expects a JSON response body.

### Config

```json
{
  "type": "http",
  "url": "http://localhost:8080/api/eval",
  "method": "POST"
}
```

With optional fields:

```json
{
  "type": "http",
  "url": "http://localhost:8080/api/eval",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer token123"
  },
  "timeout_seconds": 30
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"http"` | yes | Driver selector |
| `url` | string, non-empty | yes | Full HTTP endpoint URL |
| `method` | string, non-empty | yes | HTTP method (e.g. `"POST"`) |
| `headers` | object (string → string) | no | Extra HTTP headers |
| `timeout_seconds` | positive integer | no | Request timeout in seconds |

### Request/response contract

- Request: adapter request dict serialized as JSON body with `Content-Type: application/json`
- Response: must be a JSON object conforming to the standard response contract
- The `Content-Type: application/json` header is always sent
- Custom `headers` are added after the Content-Type header

### Timeout behavior

If `timeout_seconds` is absent, no timeout is enforced (consistent with the v1 subprocess protocol which also has no timeout).

### Error conditions

| Condition | Error |
|-----------|-------|
| Non-2xx HTTP response | `"HTTP <code> from <redacted-url>"` |
| Connection failure | `"connection failed to <redacted-url>"` |
| Response body not valid JSON | `"response body is not valid JSON"` |
| Response not a JSON object | `"response must be a JSON object"` |

## Driver: `command`

Runs an explicit command list as a subprocess. Similar to the legacy `adapter` path but with an explicit command array instead of a single executable path. This allows specifying interpreters, arguments, and flags directly in config.

### Config

```json
{
  "type": "command",
  "command": ["python3", "my_adapter.py", "--verbose"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"command"` | yes | Driver selector |
| `command` | array of non-empty strings | yes | Command to execute |
| `working_dir` | non-empty string | no | Explicit subprocess working directory. Relative values resolve against the workflow config directory. |

### Config-origin semantics (working directory)

When the workflow config is loaded via `load_workflow_config`, the directory containing the config file is recorded as the **config-origin directory**.

- If `working_dir` is absent, the command driver subprocess runs with `cwd` set to the config-origin directory.
- If `working_dir` is present, the subprocess runs with `cwd` set to that directory instead. Relative `working_dir` values resolve against the config-origin directory at load time.

This means relative paths in the command array resolve against the config file location, not the caller's current working directory. For example, given a config file at `/project/configs/workflow.json`:

```json
{
  "type": "command",
  "command": ["python3", "adapters/my_adapter.py"]
}
```

The subprocess runs with `cwd=/project/configs/`, so `adapters/my_adapter.py` resolves to `/project/configs/adapters/my_adapter.py` regardless of where the caller invokes the tool from.

This matches the legacy `adapter` path, which resolves relative adapter paths against the config file directory at load time.

Generated quickstart configs may set `working_dir` explicitly to the original workspace root while still writing the generated workflow config file under a separate `--output-dir`.

The structural viability check (`can_run`/`why_not`) uses the same resolution rules, so viability results stay aligned with runtime behavior.

### Subprocess contract

Identical to the raw subprocess adapter protocol:
- Request JSON written to stdin
- Response JSON read from stdout
- stderr is captured but ignored
- Exit code 0 = success; non-zero = failure

### Error conditions

| Condition | Error |
|-----------|-------|
| Command not found | `"command not found"` |
| Command not executable | `"command not executable"` |
| Non-zero exit code | `"command exited with code <N>; stdout: <bounded excerpt>"` |
| stdout not valid JSON | `"command stdout is not valid JSON"` |
| Response not a JSON object | `"response must be a JSON object"` |

On non-zero exit, a bounded excerpt of the subprocess stdout (up to 2000 characters) is included in the error message. This preserves adapter-side diagnostic output without silently discarding it. If the subprocess produced no stdout, only the exit code is reported.

## Shared response contract

All three drivers must produce a response dict with exactly the same fields as the raw subprocess protocol:

```json
{
  "raw_response": "<string>",
  "parsed_response": "<any JSON value or null>",
  "usage": {
    "input_tokens": "<integer >= 0>",
    "output_tokens": "<integer >= 0>"
  }
}
```

The runner applies the same strict validation to driver responses as to subprocess adapter responses. Missing fields, wrong types, or negative tokens result in `failed_execution` for that case.

## Execution model

Driver dispatch happens inside the runner's per-case execution loop. The flow is:

1. Runner builds the standard adapter request dict (same for all paths)
2. If config has `driver`: dispatch to the appropriate driver module
3. If config has `adapter`: call legacy subprocess path
4. Runner validates the response (same validation for all paths)
5. Runner builds the CaseRecord (same structure for all paths)

There is one shared validation path. Drivers do not bypass or reinterpret the response contract.

## Validation strictness

- Driver type must be one of the known types (`python-callable`, `http`, `command`)
- Each driver has specific required fields with strict type checks
- Unknown fields in any driver config are rejected
- Empty strings and zero/negative values are rejected where applicable
- Boolean values are not accepted as integers (`timeout_seconds: true` is rejected)
- The `adapter`/`driver` mutual exclusion is enforced at config load time
