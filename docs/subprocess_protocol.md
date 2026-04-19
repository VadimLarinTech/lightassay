# Subprocess JSON Protocol (v1)

## Overview

The `run` command calls the workflow under test via a subprocess (the "adapter").
Communication uses a strict JSON protocol over stdin/stdout.
No fallback, no best-effort recovery.

## Invocation

The adapter executable is called once per case:

```
<adapter_path> < request.json > response.json
```

The adapter receives a JSON request on **stdin** and must write a JSON response to **stdout**.
Anything written to **stderr** is ignored by the tool (the adapter may use it for logging).

## Request format

The tool writes exactly this JSON object to the adapter's stdin:

```json
{
  "case_id": "<string>",
  "input": "<string>",
  "context": "<string or null>",
  "workflow_id": "<string>"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `case_id` | string | Stable case ID from the workbook |
| `input` | string | The input text for this case |
| `context` | string or null | Optional context from the workbook case, null if absent |
| `workflow_id` | string | Workflow identity from the workflow config |

Optional fields when the workflow config includes LLM metadata:

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Model provider label from workflow LLM metadata |
| `model` | string | Model identifier from workflow LLM metadata |

`workflow_id` always identifies the workflow under test. `provider` /
`model` appear only when that workflow carries explicit LLM metadata.
Adapters may use them for provider-specific routing or tracing; adapters
that do not need them may ignore them.

## Response format

The adapter must write exactly this JSON object to stdout:

```json
{
  "raw_response": "<string>",
  "parsed_response": "<any JSON value or null>",
  "usage": {
    "input_tokens": <integer>,
    "output_tokens": <integer>
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `raw_response` | string | The raw text output from the workflow |
| `parsed_response` | any JSON value or null | Structured parse of the output, if applicable; null if not |
| `usage.input_tokens` | integer >= 0 | Number of input tokens consumed |
| `usage.output_tokens` | integer >= 0 | Number of output tokens consumed |

## Strict contract

- The adapter **must** exit with code 0 on success.
- The adapter **must** write valid JSON to stdout on success.
- The response JSON **must** contain all four fields (`raw_response`, `parsed_response`, `usage.input_tokens`, `usage.output_tokens`).
- Any violation results in a `failed_execution` status for that case.

## Failure conditions

The tool records `failed_execution` for a case when any of these occur:

| Condition | Recorded error |
|-----------|---------------|
| Adapter exits with non-zero code | `"Adapter exited with code <N>"` |
| Adapter stdout is not valid JSON | `"Adapter stdout is not valid JSON"` |
| Response JSON is missing required fields | `"Adapter response missing required field: <field>"` |
| Response field has wrong type | `"Adapter response field '<field>' has invalid type"` |
| Usage field is negative | `"Adapter response field 'usage.<field>' is negative"` |
| Adapter executable not found | `"Adapter not found: <path>"` |
| Permission denied on adapter | `"Adapter not executable: <path>"` |

In all failure cases, the case is recorded with `status: "failed_execution"` and the error message in `execution_error`. The run continues to the next case.

## Duration measurement

The tool measures wall-clock time from subprocess start to subprocess exit.
This is recorded as `duration_ms` in the case record.

## No timeout in v1

The v1 protocol does not impose any timeout on the adapter subprocess.
The adapter runs until it exits on its own.
If timeout behaviour is needed, it is the adapter's responsibility to implement it internally.

## No environment variable injection

The tool does not inject environment variables into the adapter process.
The adapter inherits the caller's environment.
If the adapter needs API keys or configuration, it must obtain them from its own environment or config files.
