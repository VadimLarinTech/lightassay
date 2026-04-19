# Preparation Adapter Specification

Version: 0.2.0

## Purpose

A preparation adapter is an external executable that performs LLM-driven
preparation operations (generate directions, generate cases, reconcile
readiness) on workbooks. The tool calls the adapter via subprocess, passing
data on stdin and reading results from stdout.

The tool never calls LLM provider APIs directly. All LLM interaction is
delegated to preparation adapters. This keeps the tool provider-agnostic and
API-key-free.

Preparation is now target-first and source-grounded. The adapter no longer
receives only the free-form brief. It also receives:
- `target` — the structured evaluation target;
- `user_priorities` — the human brief re-expressed as structured natural-language sections;
- `source_context` — explicit target sources plus bounded target-anchored discovery;
- `planning_mode` — `full`, `quick_try`, or `exploratory`;
- optional `planning_context` — bounded mode-specific context such as exploration limits.

Two constraints matter:
- user priorities remain primary input and must not be silently dissolved into generic planning heuristics;
- source grounding is bounded by the target and its anchored discovery path; adapters must not drift into unrelated repository areas.

`user_priorities` are derived only from the canonical `Brief` fields.
Text outside those fields or under unsupported brief headings is outside the
planning contract and may be ignored by the tool before the adapter call.

## Preparation Config

The preparation adapter is configured via a JSON file with the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `adapter` | string | yes | Path to the adapter executable. Relative paths are resolved from the config file directory. |
| `provider` | string | yes | Provider name (informational metadata, validated but not passed to the adapter). |
| `model` | string | yes | Model name (informational metadata, validated but not passed to the adapter). |

No other fields are allowed. Unknown fields cause a validation error.

### Example

```json
{
  "adapter": "./my_preparer.py",
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

The adapter is expected to reason over the structured request it receives. It
must not invent a wider planning scope than the provided target, source
context, and user priorities justify.

### Key Design Constraint

The adapter returns **structured JSON data**, NOT raw markdown. The tool
converts the structured response into Workbook model mutations, then renders
canonical markdown via `workbook_renderer.render()`. This guarantees the
workbook always round-trips through the parser without errors.

---

## Operation: bootstrap

The `bootstrap` operation is invoked by `lightassay quickstart` to turn
the user's plain-language message plus a target hint into a
structured target, execution shape, assumptions, planning constraints,
and optional bootstrap resolution notes for workbook context.

### Request Format

```json
{
  "operation": "bootstrap",
  "user_message": "<one freeform sentence from the user>",
  "target_hint": "<string or null>",
  "workspace_root": "<absolute path>",
  "full_intent": false,
  "bootstrap_directive": "<tool-provided instruction describing the resolution contract>",
  "full_intent_directive": "<optional tool-provided instruction when full-intent mode is active>"
}
```

There is no authoritative local pre-resolution step. `target_hint` is
only a human signal; the bootstrap adapter is responsible for
inspecting the workspace and resolving the actual target when possible.
If it cannot do so confidently, it must return one precise
`clarification_request` and leave `target` / `execution_shape` null.

### Response Format

```json
{
  "target": {
    "kind": "...",
    "name": "...",
    "locator": "...",
    "boundary": "...",
    "sources": ["..."],
    "notes": "...",
    "assumptions": ["..."]
  },
  "execution_shape": {
    "type": "python-callable" | "http" | "command",
    "module": "...", "function": "...",
    "url": "...", "method": "...", "headers": {...}, "timeout_seconds": 30,
    "command": ["..."]
  },
  "assumptions": ["..."],
  "quickstart_constraints": {
    "max_directions": 2,
    "max_cases": 4,
    "focus_notes": ["..."]
  },
  "resolution_notes": "...",
  "clarification_request": null | "<question>"
}
```

Fields per shape:

- `python-callable`: `module` + `function` required.
- `http`: `url` + `method` required; `headers` and `timeout_seconds` optional.
- `command`: non-empty `command` list required.

`clarification_request` is non-null only when the adapter cannot safely produce
both `target` and `execution_shape`; quickstart then stops with that question.
`resolution_notes` is optional freeform bootstrap context that lightassay may
place into the workbook's additional-context section. It must not contain
system-authored planning boilerplate disguised as human priorities.

## Operation: generate_directions

### Request Format

```json
{
  "operation": "generate_directions",
  "brief": "The full brief text from the workbook.",
  "planning_mode": "full",
  "target": {
    "kind": "workflow",
    "name": "check_sentence",
    "locator": "myapp.pipeline.run",
    "boundary": "high-level sentence-check workflow boundary",
    "sources": ["myapp/pipeline.py", "myapp/prompts/summarize.py"],
    "notes": ""
  },
  "user_priorities": {
    "input_mode": "natural_language",
    "raw_brief": "...",
    "sections": [
      {
        "section_id": "what_is_being_tested",
        "heading": "What is being tested",
        "text": "...",
        "priority_label": "scope",
        "ordinal": 0
      }
    ]
  },
  "source_context": {
    "project_root": "/repo/root",
    "discovery_mode": "bounded_target_anchored",
    "explicit_sources": [
      {
        "path": "myapp/pipeline.py",
        "reason": "explicit target source",
        "content": "..."
      }
    ],
    "discovered_sources": []
  }
}
```

### Response Format

```json
{
  "directions": [
    {
      "direction_id": "correctness",
      "body": "Verify that the workflow produces correct output...",
      "behavior_facet": "core_output_behavior",
      "testing_lens": "positive_and_regression",
      "covered_user_priority_sections": ["what_is_being_tested", "must_not_break"],
      "source_rationale": "Grounded in the explicit target source."
    },
    {
      "direction_id": "edge-cases",
      "body": "Test boundary and edge-case inputs...",
      "behavior_facet": "edge_case_behavior",
      "testing_lens": "boundary_and_negative",
      "covered_user_priority_sections": ["failure_modes"],
      "source_rationale": "Grounded in neighboring discovered source behavior."
    }
  ],
  "priority_conflicts": []
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `directions` | array | yes | Non-empty list of direction objects. |
| `directions[].direction_id` | string | yes | Unique direction ID. Must be non-empty. |
| `directions[].body` | string | yes | Direction description. Must be non-empty. |
| `directions[].behavior_facet` | string | yes | Which behavior facet of the target this direction probes. Must be non-empty. |
| `directions[].testing_lens` | string | yes | Which testing lens this direction uses. Must be non-empty. |
| `directions[].covered_user_priority_sections` | array of strings | yes | Non-empty list of user-priority section IDs explicitly covered by the direction. |
| `directions[].source_rationale` | string | yes | Non-empty explanation of why this direction is grounded in the provided source context. |
| `priority_conflicts` | array | yes | Explicitly declared user-priority conflicts or uncovered areas. Use an empty list when none exist. |

### Preconditions (validated before adapter call)

| Condition | Error |
|-----------|-------|
| Workbook brief has no user content (empty or contains only template scaffolding: ``###`` headings, HTML comments, blank lines) | CLI rejects with error before calling adapter |
| Workbook already has directions | `PreparationError`: same-stage re-entry rejected to prevent silent loss of existing directions and human feedback |
| Workbook already has cases | `PreparationError`: downstream derived state would become stale |
| Workbook has `RUN_READY: yes` | `PreparationError`: downstream readiness state would become stale |
| Workbook has non-empty artifact references (run, analysis, or compare) | `PreparationError`: downstream artifacts would reference stale workbook state |

---

## Operation: generate_cases

### Request Format

```json
{
  "operation": "generate_cases",
  "brief": "The full brief text.",
  "planning_mode": "full",
  "directions_global_instruction": "Human global feedback on directions (empty = approved).",
  "directions": [
    {
      "direction_id": "correctness",
      "body": "Verify output correctness...",
      "behavior_facet": "core_output_behavior",
      "testing_lens": "positive_and_regression",
      "covered_user_priority_sections": ["what_is_being_tested"],
      "source_rationale": "Grounded in explicit target source behavior.",
      "human_instruction": "Feedback on this specific direction (empty = approved)."
    }
  ],
  "target": { "...": "same as generate_directions" },
  "user_priorities": { "...": "same as generate_directions" },
  "source_context": { "...": "same as generate_directions" }
}
```

For exploratory mode, the request may also include:

```json
{
  "planning_context": {
    "exploration_goal": "Investigate weak spots around failed cases.",
    "seed_run_id": "run_abc123",
    "max_cases": 3,
    "max_iterations": 1,
    "failed_cases": [
      {
        "case_id": "case-fail",
        "status": "failed_execution",
        "execution_error": "Adapter exited with code 1"
      }
    ]
  }
}
```

For quick try, the request uses:

```json
{
  "planning_mode": "quick_try"
}
```

The expectation is one representative direction and one representative case
that still use the normal workbook model.

### Response Format

```json
{
  "cases": [
    {
      "case_id": "case-1",
      "input": "Hello world",
      "target_directions": ["correctness"],
      "expected_behavior": "Should echo the input text back.",
      "behavior_facet": "core_output_behavior",
      "testing_lens": "positive_and_regression",
      "covered_user_priority_sections": ["what_is_being_tested"],
      "source_rationale": "Grounded in explicit target source behavior.",
      "context": "Optional context string or null.",
      "notes": "Optional notes string or null."
    }
  ],
  "priority_conflicts": []
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cases` | array | yes | Non-empty list of case objects. |
| `cases[].case_id` | string | yes | Unique case ID. Must be non-empty. |
| `cases[].input` | string | yes | Case input. Must be non-empty. |
| `cases[].target_directions` | array of strings | yes | Non-empty list of direction IDs this case covers. |
| `cases[].expected_behavior` | string | yes | Expected behavior description. Must be non-empty. |
| `cases[].behavior_facet` | string | yes | Behavior facet inherited or synthesized for this case. Must be non-empty. |
| `cases[].testing_lens` | string | yes | Testing lens used for this case. Must be non-empty. |
| `cases[].covered_user_priority_sections` | array of strings | yes | Non-empty list of user-priority sections this case explicitly covers. |
| `cases[].source_rationale` | string | yes | Non-empty source-grounded rationale for why this case exists. |
| `cases[].context` | string or null | no | Optional context. |
| `cases[].notes` | string or null | no | Optional notes. |
| `priority_conflicts` | array | yes | Explicitly declared user-priority conflicts or uncovered areas. Use an empty list when none exist. |

### Post-validation (after adapter response)

| Condition | Error |
|-----------|-------|
| `target_directions` references a direction ID not in workbook | `PreparationError` with details |

### Preconditions (validated before adapter call)

| Condition | Error |
|-----------|-------|
| Workbook has no directions | CLI rejects with error before calling adapter |
| Workbook already has cases | `PreparationError`: same-stage re-entry rejected to prevent silent loss of existing cases and human feedback |
| Workbook has `RUN_READY: yes` | `PreparationError`: downstream readiness state would become stale |
| Workbook has non-empty artifact references (run, analysis, or compare) | `PreparationError`: downstream artifacts would reference stale workbook state |

---

## Operation: reconcile_readiness

### Request Format

```json
{
  "operation": "reconcile_readiness",
  "brief": "The full brief text.",
  "planning_mode": "full",
  "directions_global_instruction": "Human global feedback on directions.",
  "directions": [
    {
      "direction_id": "correctness",
      "body": "...",
      "behavior_facet": "core_output_behavior",
      "testing_lens": "positive_and_regression",
      "covered_user_priority_sections": ["what_is_being_tested"],
      "source_rationale": "Grounded in explicit target source behavior.",
      "human_instruction": "..."
    }
  ],
  "cases_global_instruction": "Human global feedback on cases.",
  "cases": [
    {
      "case_id": "case-1",
      "input": "...",
      "target_directions": ["correctness"],
      "expected_behavior": "...",
      "behavior_facet": "core_output_behavior",
      "testing_lens": "positive_and_regression",
      "covered_user_priority_sections": ["what_is_being_tested"],
      "source_rationale": "Grounded in explicit target source behavior.",
      "context": null,
      "notes": null,
      "human_instruction": "..."
    }
  ],
  "target": { "...": "same as generate_directions" },
  "user_priorities": { "...": "same as generate_directions" },
  "source_context": { "...": "same as generate_directions" }
}
```

### Response Format

```json
{
  "directions": [
    {
      "direction_id": "correctness",
      "body": "...",
      "behavior_facet": "core_output_behavior",
      "testing_lens": "positive_and_regression",
      "covered_user_priority_sections": ["what_is_being_tested"],
      "source_rationale": "Grounded in explicit target source behavior."
    }
  ],
  "cases": [
    {
      "case_id": "case-1",
      "input": "...",
      "target_directions": ["correctness"],
      "expected_behavior": "...",
      "behavior_facet": "core_output_behavior",
      "testing_lens": "positive_and_regression",
      "covered_user_priority_sections": ["what_is_being_tested"],
      "source_rationale": "Grounded in explicit target source behavior.",
      "context": null,
      "notes": null
    }
  ],
  "run_ready": true,
  "readiness_note": "All cases reconciled and ready.",
  "priority_conflicts": []
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `directions` | array | yes | Reconciled directions (same shape as generate_directions response). |
| `cases` | array | yes | Reconciled cases (same shape as generate_cases response). |
| `run_ready` | boolean | yes | Whether the workbook is ready for a run. |
| `readiness_note` | string | yes | Explanation of readiness decision. Must be non-empty when `run_ready` is `false` (the adapter must explain why the workbook is not ready). May be empty when `run_ready` is `true`. |
| `priority_conflicts` | array | yes | Explicitly declared user-priority conflicts or uncovered areas. Use an empty list when none exist. |

### Post-validation (after adapter response)

| Condition | Error |
|-----------|-------|
| Case `target_directions` references a direction ID not in reconciled directions | `PreparationError` with details |

### Preconditions (validated before adapter call)

| Condition | Error |
|-----------|-------|
| Workbook has no directions | CLI rejects with error before calling adapter |
| Workbook has no cases | CLI rejects with error before calling adapter |
| Workbook has non-empty artifact references (run, analysis, or compare) | `PreparationError`: downstream artifacts would reference stale workbook state |

---

## Stage Entry Contract

All preparation commands enforce a single-pass preparation model:

1. **Same-stage re-entry is rejected.** `generate_directions` rejects if directions already exist. `generate_cases` rejects if cases already exist. This prevents silent loss of human feedback on existing items.

2. **Downstream-state protection.** `generate_directions` rejects if cases or `RUN_READY: yes` exist. `generate_cases` rejects if `RUN_READY: yes` exists. Overwriting upstream state would leave downstream state semantically stale.

3. **Artifact-reference protection.** All three commands (`generate_directions`, `generate_cases`, `reconcile_readiness`) reject if any artifact reference (run, analysis, compare) is non-empty. A workbook that already points at downstream artifacts must not be mutated by preparation, because those artifacts were produced from the prior workbook state.

4. **`reconcile_readiness` is re-entrant** within the preparation phase (no same-stage re-entry guard). It may be called multiple times as the human provides feedback. However, it is still blocked by artifact-reference protection.

To start a fresh preparation pass, create a new workbook.

## Planning Semantics

Preparation is no longer only `brief -> directions -> cases`.

The effective planning inputs are:
- `target`;
- `user_priorities`;
- `source_context`;
- current workbook state for the stage being executed;
- bounded `planning_context` for quick-try or exploratory modes.

This means:
- direction generation is target- and source-grounded, not brief-only;
- case generation is derived from directions plus the same target-grounded planning context;
- exploratory planning is allowed to use prior run evidence, but only within explicit iteration and case limits;
- adapters must not silently down-rank the user's priorities in favor of generic code-derived ideas;
- if required user-priority sections are not covered, the adapter must either cover them explicitly via `covered_user_priority_sections` or declare explicit `priority_conflicts`;
- directions and cases must remain traceable via `behavior_facet`, `testing_lens`, `covered_user_priority_sections`, and `source_rationale`.

---

## Common Failure Conditions

Any of the following causes a `PreparationError` — no best-effort recovery:

| Condition | Error message |
|-----------|---------------|
| Adapter executable not found | `Preparation adapter not found: {path}` |
| Adapter not executable | `Preparation adapter not executable: {path}` |
| Non-zero exit code | `Preparation adapter exited with code {N}` |
| stdout is not valid JSON | `Preparation adapter stdout is not valid JSON` |
| Response is not a JSON object | `Preparation adapter response must be a JSON object, got {type}` |
| Missing required field | `Preparation adapter response missing required field: {field}` |
| Field has wrong type | `Preparation adapter response: {field} must be {expected_type}, got {actual_type}` |
| Field is empty when non-empty required | `Preparation adapter response: {field} must be non-empty` |
| Duplicate IDs | `Preparation adapter response: duplicate {id_type} {id}` |

## No Timeout (v1)

Same as other subprocess protocols: no timeout enforcement in v1. The adapter
is expected to complete in reasonable time. The user can terminate the process
externally if needed.
