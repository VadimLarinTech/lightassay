# Roles and Formats (v1)

## Role boundaries

### Human

The human is the source of truth on:
- what to test and why
- what quality means for this workflow
- which constraints and invariants apply
- which failure modes matter
- final interpretation of results

The human participates by:
- filling the guided brief in the workbook
- leaving instructions in `HUMAN:global_instruction` and `HUMAN:instruction` fields
- deciding whether to proceed to run based on `RUN_READY` status
- reading analysis and compare artifacts to draw conclusions

The human does NOT:
- edit LLM-generated source content in the workbook
- write test cases manually in code
- compute quality scores
- judge quality inline during the run

### LLM

The LLM is the semantic reasoning layer. It:
- reads the brief and generates directions (behavior obligations × test lenses)
- reads directions and human feedback to generate cases
- reads human feedback on cases and reconciles the workbook
- sets `RUN_READY` status with explicit reasoning
- performs analysis of a run artifact (semantic, not metric-based)
- performs compare across multiple run artifacts

The LLM is constrained by:
- the brief and human feedback fields — these are strict instructions
- the prohibition on quality judgment in run artifacts
- the strict separation between analysis and run, and between compare and run
- the no-fallback rule: if the LLM cannot resolve an ambiguity from the workbook, it must make the ambiguity explicit in the workbook, not resolve it silently

### Code

Code is the orchestration and measurement layer. It:
- guides the human through the flow (CLI prompts, status checks)
- reads the workbook to extract cases and `RUN_READY` status
- calls the workflow under test for each case
- measures raw execution facts: duration, token usage, errors
- writes run artifacts (JSON)
- passes run artifacts to LLM for analysis
- passes multiple run artifacts to LLM for compare
- saves analysis and compare artifacts

Code does NOT:
- judge quality of workflow outputs
- compare workflow outputs semantically
- make recommendations
- apply fallback behavior when data is ambiguous or missing
- auto-fix, normalize, or guess missing values
- skip cases silently on execution failure — it records the failure

---

## Artifact formats (v1)

### Workbook — Markdown

- Single file per evaluation session
- Overwritten as current working state
- No versioning, no history copies
- Human and LLM both use the same file

### Run artifact — JSON

Self-contained machine-readable record of one run. Minimum required fields:

```json
{
  "run_id": "<uuid>",
  "workflow_id": "<string identifying the workflow under test>",
  "workbook_path": "<path to workbook file>",
  "workbook_sha256": "<sha256 digest of workbook at run start>",
  "workflow_config_sha256": "<sha256 digest of workflow config, if applicable>",
  "started_at": "<ISO 8601 timestamp>",
  "finished_at": "<ISO 8601 timestamp>",
  "status": "completed | failed",
  "cases": [
    {
      "case_id": "<stable case id from workbook>",
      "input": "<input passed to workflow>",
      "context": "<context passed to workflow, if any>",
      "expected_behavior": "<copied from workbook>",
      "raw_response": "<raw output from workflow>",
      "parsed_response": "<structured parse of output, if applicable>",
      "duration_ms": 0,
      "usage": {
        "input_tokens": 0,
        "output_tokens": 0
      },
      "status": "completed | failed_execution",
      "execution_error": "<error message if status is failed_execution, else null>"
    }
  ],
  "aggregate": {
    "total_cases": 0,
    "completed_cases": 0,
    "failed_cases": 0,
    "total_duration_ms": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0
  }
}
```

Optional when the workflow under test carries explicit LLM metadata:

```json
{
  "provider": "<model provider>",
  "model": "<model identifier>"
}
```

**Run status contract:**
- `completed`: all cases have status `completed` (no execution failures)
- `failed`: at least one case has status `failed_execution`
- Compare only accepts `completed` runs

**Case status contract:**
- `completed`: the workflow was called and returned a response (regardless of quality)
- `failed_execution`: the workflow call itself failed (exception, timeout, API error)
- Quality is not part of case status

### Analysis artifact — Markdown

Produced by LLM from a single run artifact. Must address:
- which cases met expected behavior and why
- which cases did not and how
- borderline/ambiguous cases
- repeating patterns across cases
- identified weak spots
- observations on tokens, latency, and other raw facts
- recommendations where they follow from the data

Filename convention: `analysis_<analysis_id>.md` (where `analysis_id` is a 12-character lowercase hex string, UUID4 prefix — independent of `run_id`)

### Compare artifact — Markdown

Produced by LLM from two or more completed run artifacts. Must show:
- which runs were compared and their configuration differences
- where one variant showed stronger behavior quality
- different failure patterns across runs
- raw fact differences (tokens, latency, etc.)
- where the conclusion is clear vs. where uncertainty remains
- recommendations where they legitimately follow

Compare is never initiated from within a run. It is always a separate explicit operation.

Filename convention: `compare_<compare_id>.md` (where `compare_id` is a 12-character lowercase hex string, UUID4 prefix — independent of individual `run_id` values)

In v1, the `compare` command does **not** automatically update any workbook's artifact references. Compare operates across runs that may come from different workbooks; automatic workbook update would require ambiguous multi-workbook choice resolution. The user can manually set the `- compare:` reference if desired.

---

## Error handling contract

Code must not silently recover from ambiguous or missing data. The required behavior:

| Situation | Required code behavior |
|-----------|----------------------|
| Workbook has `RUN_READY: no` | Refuse to start run, show `READINESS_NOTE` |
| Workbook has `RUN_READY` missing | Raise explicit error — do not assume readiness |
| Case is missing `input` | Raise explicit error — do not skip or substitute |
| Case is missing `expected_behavior` | Raise explicit error — do not skip or substitute |
| Workflow call throws exception | Record `failed_execution` with error, set run status to `failed` |
| Run artifact is missing required fields | Raise explicit error on load — do not fill in defaults |
| Compare receives a `failed` run | Raise explicit error — do not silently skip it |
