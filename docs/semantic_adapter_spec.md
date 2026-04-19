# Semantic Adapter Specification

Version: 0.2.0

## Purpose

A semantic adapter is an external executable that performs LLM-driven semantic
operations (analysis, compare) on run artifacts. The tool calls the adapter via
subprocess, passing data on stdin and reading results from stdout.

The tool never calls LLM provider APIs directly. All LLM interaction is
delegated to semantic adapters. This keeps the tool provider-agnostic and
API-key-free.

## Semantic Config

The semantic adapter is configured via a JSON file with the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `adapter` | string | yes | Path to the adapter executable. Relative paths are resolved from the config file directory. |
| `provider` | string | yes | Provider name (informational, stored in artifact metadata). |
| `model` | string | yes | Model name (informational, stored in artifact metadata). |

No other fields are allowed. Unknown fields cause a validation error.

### Example

```json
{
  "adapter": "./my_analyzer.py",
  "provider": "anthropic",
  "model": "claude-sonnet-4-20250514"
}
```

## Subprocess Protocol

### Invocation

The tool calls the adapter as a subprocess:

```
<adapter_path> < request.json > response.json
```

- Request JSON is written to the adapter's **stdin**.
- Response JSON is read from the adapter's **stdout**.
- The adapter's **stderr** is captured but not parsed by the tool.

### Analyze Request Format

```json
{
  "operation": "analyze",
  "run_artifact": {
    "run_id": "...",
    "workflow_id": "...",
    "workbook_path": "...",
    "workbook_sha256": "...",
    "workflow_config_sha256": "...",
    "provider": "...",
    "model": "...",
    "started_at": "...",
    "finished_at": "...",
    "status": "completed",
    "cases": [...],
    "aggregate": {...}
  }
}
```

The `run_artifact` field contains the complete run artifact as a JSON object
(same structure as the run artifact JSON file).

The `operation` field is always `"analyze"` for the analyze command. This field
allows a single adapter to handle multiple semantic operations (analyze,
compare) by dispatching on the operation type.

### Analyze Response Format

```json
{
  "analysis_markdown": "## Summary\n\n...",
  "recommendations": [
    {
      "title": "Add a direction around named-entity preservation",
      "to_ensure": "wording improvements do not silently damage user meaning.",
      "section": "broader_coverage",
      "source": "prompt_design",
      "detail": "Optional freeform detail line."
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `analysis_markdown` | string | yes | The analysis content as markdown. Must be non-empty (after trimming whitespace). |
| `recommendations` | array or null | no | Structured next-step recommendations; see below. |

#### Structured next-step recommendations

When present, the `recommendations` array drives the final "Next-step
recommendations" section of the analysis artifact.  Every recommendation must
answer `to ensure what?` — this is the "to ensure what?" principle from the
quickstart contract, made explicit in the contract:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Short imperative title ("Add a direction around X"). Non-empty. |
| `to_ensure` | string | yes | The product reason, answering `to ensure what?`. Non-empty. |
| `section` | string | yes | One of `broader_coverage`, `weak_spots`, `why_they_matter`. |
| `source` | string or null | no | When set, one of `user_intent`, `prompt_design`, `workflow_design`, `observed_behavior`. |
| `detail` | string or null | no | Optional freeform line rendered under the bullet. Must be non-empty when set. |

There is **no hard cap** on the number of recommendations.  Adapters must not pad the
list to hit a count — each entry must be evidence-based and valuable.

The request carries `recommendation_schema` and optional `analysis_profile` /
`analysis_context` fields that surface the caller's intent (e.g. quickstart vs.
continue).  Adapters may ignore these fields, but are encouraged to honour them.

### Strict Contract

1. The adapter **must** exit with code 0 on success.
2. The adapter's stdout **must** be valid JSON.
3. The JSON **must** be an object (not array, string, etc.).
4. The object **must** contain the `analysis_markdown` field.
5. The `analysis_markdown` field **must** be a non-empty string.

### Failure Conditions

Any of the following causes an `AnalysisError` — no best-effort recovery:

| Condition | Error message |
|-----------|---------------|
| Adapter executable not found | `Semantic adapter not found: {path}` |
| Adapter not executable | `Semantic adapter not executable: {path}` |
| Non-zero exit code | `Semantic adapter exited with code {N}` |
| stdout is not valid JSON | `Semantic adapter stdout is not valid JSON` |
| Response is not a JSON object | `Semantic adapter response must be a JSON object, got {type}` |
| Missing `analysis_markdown` | `Semantic adapter response missing required field: 'analysis_markdown'` |
| `analysis_markdown` is not a string | `Semantic adapter response field 'analysis_markdown' must be a string, got {type}` |
| `analysis_markdown` is empty/whitespace | `Semantic adapter response field 'analysis_markdown' must be non-empty` |

### No Timeout (v1)

Same as the workflow subprocess protocol: no timeout enforcement in v1. The
adapter is expected to complete in reasonable time. The user can terminate the
process externally if needed.

## Compare Operation

The `compare` command uses the same semantic config format and adapter
subprocess protocol. The adapter distinguishes operations via the `operation`
field. A single adapter can handle both `analyze` and `compare`, or separate
adapters can be used for each.

### Compare Request Format

```json
{
  "operation": "compare",
  "run_artifacts": [
    {
      "run_id": "...",
      "workflow_id": "...",
      "...": "..."
    },
    {
      "run_id": "...",
      "workflow_id": "...",
      "...": "..."
    }
  ]
}
```

The `run_artifacts` field contains an array of 2 or more complete run artifacts
(same structure as the run artifact JSON file). All run artifacts are guaranteed
to have status `"completed"` — the tool validates this before calling the
adapter.

### Compare Response Format

```json
{
  "compare_markdown": "## Comparison Summary\n\n..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `compare_markdown` | string | yes | The comparison content as markdown. Must be non-empty (after trimming whitespace). |

### Compare Failure Conditions

Any of the following causes a `CompareError` — no best-effort recovery:

| Condition | Error message |
|-----------|---------------|
| Adapter executable not found | `Semantic adapter not found: {path}` |
| Adapter not executable | `Semantic adapter not executable: {path}` |
| Non-zero exit code | `Semantic adapter exited with code {N}` |
| stdout is not valid JSON | `Semantic adapter stdout is not valid JSON` |
| Response is not a JSON object | `Semantic adapter response must be a JSON object, got {type}` |
| Missing `compare_markdown` | `Semantic adapter response missing required field: 'compare_markdown'` |
| `compare_markdown` is not a string | `Semantic adapter response field 'compare_markdown' must be a string, got {type}` |
| `compare_markdown` is empty/whitespace | `Semantic adapter response field 'compare_markdown' must be non-empty` |

### Compare Preconditions (validated before adapter call)

| Condition | Error |
|-----------|-------|
| Fewer than 2 run artifacts | `Compare requires at least 2 run artifacts, got {N}` |
| Any run has status != `"completed"` | `Run artifact [{i}] (run_id={id}) has status {status}. Compare only accepts completed runs.` |
