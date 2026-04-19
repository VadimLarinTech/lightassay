# Workbook Specification

## What a workbook is

A workbook is a single markdown file that is the source of truth for one evaluation session.

Properties:
- Overwritten as the current working state — not versioned or snapshotted.
- Contains the entire session in one place: target, brief, directions, cases, human feedback, readiness status, and artifact references.
- Used by both the human and the LLM.
- Does not split into multiple files at different stages.

If a history of human feedback is ever needed, the only permitted form is a compact journal of human feedback entries — no workbook copies, no full snapshots.

## Workbook skeleton (v1)

This is the minimal structural skeleton every workbook must follow. It is not a content template — it is the shape that code, LLM, and human all rely on.

```markdown
# Eval Workbook

## Target
[target-first contract — see below]

## Brief
[guided brief — see below]

## Directions
[LLM-generated directions]

### HUMAN:global_instruction
[empty or user instruction for all directions]

### Direction: <direction_id>
[LLM-generated direction content]
**Behavior facet:** <behavior_facet>
**Testing lens:** <testing_lens>
**Covered user priorities:** <section_id[, ...]>
**Source rationale:** <source-grounded rationale>
HUMAN:instruction
[empty or user instruction for this direction]

## Cases
[LLM-generated cases]

### HUMAN:global_instruction
[empty or user instruction for all cases]

### Case: <case_id>
[LLM-generated case content]
**Behavior facet:** <behavior_facet>
**Testing lens:** <testing_lens>
**Covered user priorities:** <section_id[, ...]>
**Source rationale:** <source-grounded rationale>
HUMAN:instruction
[empty or user instruction for this case]

## Run readiness
RUN_READY: no
READINESS_NOTE:

## Artifact references
- run:
- analysis:
- compare:
```

## Target-first contract

Every workbook starts with a `## Target` section. The target defines:
- what is being evaluated;
- where the real execution boundary lives;
- which source files/modules planning must inspect.

The target is not optional scaffolding. Preparation is not considered
`planning-ready` until the target is filled.

Only the canonical target fields participate in the contract.
Anything written outside those fields is not guaranteed to be read and may be
ignored. Use `TARGET_NOTES` for any extra target-specific comments.

### Target template

```markdown
## Target

### TARGET_KIND
<!-- What kind of target is this?
     Examples:
     - workflow
     - http-api
     - python-callable
     - prompt
     - hidden-flow -->

### TARGET_NAME
<!-- Short human-readable target name. -->

### TARGET_LOCATOR
<!-- Where is the target defined or entered?
     Examples:
     - myapp.pipeline.run
     - POST /api/predict
     - myapp/prompts/summarize.py::build_prompt -->

### TARGET_BOUNDARY
<!-- What is the real execution boundary for evaluation? -->

### TARGET_SOURCES
<!-- One bullet per source file/module that planning must inspect. -->

### TARGET_NOTES
<!-- Optional scope notes, hidden flow details, or constraints. -->
```

### Readiness implications

- `planning-ready` requires:
  - a complete target (`TARGET_KIND`, `TARGET_NAME`, `TARGET_LOCATOR`, `TARGET_BOUNDARY`);
  - at least one `TARGET_SOURCES` entry;
  - a brief with human-authored content.
- `run-ready` is stricter:
  - the workbook must already be `planning-ready`;
  - cases must exist;
  - `RUN_READY: yes` must be set by readiness reconciliation;
  - a valid execution binding (`workflow_config`) must be present.

## Guided brief template

The brief is a guided free-form section. The human fills it to express their testing intention. It is neither a blank text area nor a rigid form — it is a guided template that helps extract high-level meaning without constraining the human into a narrow format.

The brief is not the only planning input. In v1, directions and cases are built
from:
- the target;
- source-grounded context read from `TARGET_SOURCES` and bounded discovery from the target;
- the human brief.

The human still writes the brief in natural language. The system structures the
input internally; it must not force the human to speak in schema terms.

Only the canonical brief fields participate in this contract.
Text written outside those fields or under custom `### ...` headings is not
guaranteed to be read and may be ignored. Use `Additional context (optional)`
for anything that does not fit the other brief fields.

### Brief readiness gate

The `prepare-directions` CLI command enforces a structural gate: the brief must contain at least one line of human-authored content beyond template scaffolding. Template scaffolding consists of `### ` headings, `<!-- -->` HTML comments, and blank lines. A fresh untouched workbook with only the init template is rejected. This is a deterministic structural check — no semantic interpretation or heuristic scoring is applied.

### Brief template content

```markdown
## Brief

### What is being tested
<!-- Describe the workflow under test. What does it do? What is the scope of this evaluation?
     (whole workflow / specific mode / specific function / specific behavior) -->

### What matters in the output
<!-- What aspects of the output are important to verify?
     (all behavior / selected important parts / critical required properties) -->

### Aspects that are especially significant
<!-- Which aspects of behavior are most important to you?
     Mark as: primary / secondary / risky / already suspicious -->

### Failure modes and problem classes that matter
<!-- Which kinds of problems are important to catch?
     Examples: missed problems, false positives, poor decisions, poor explanations,
     poor transformations, instability, boundary cases, weak spots, critical failures,
     other significant groups. -->

### What must not break
<!-- List any invariants or behaviors that are non-negotiable. -->

### Additional context (optional)
<!-- Any of the following that are relevant:
     - real examples from production or testing
     - known production observations
     - known weak spots
     - constraints on the evaluation
     - preferences on evaluation scale (depth vs breadth) -->
```

## Human feedback model

The human does not edit LLM-generated source content in the workbook.

The human provides instructions through two types of designated fields:

### `HUMAN:global_instruction`
Controls the entire section it appears in (all directions or all cases). Placed once per section, immediately after the section header.

### `HUMAN:instruction`
Controls one specific direction or case. Placed inside the direction or case block.

### Semantic contract

- An empty field means: no objections, approved.
- Any text in a field is a strict instruction for the LLM's next workbook update.
- The LLM reads all human feedback fields when updating the workbook.
- The LLM must not silently skip or reinterpret human feedback.
- The human brief remains the highest-priority planning input. Code/prompt grounding
  enriches it but must not silently override it.

## Direction and case traceability

Directions and cases are not just free-form prose. In v1 they must remain
traceable to:
- the target behavior facet they are probing;
- the testing lens under which they were generated;
- the user-priority sections they explicitly cover;
- the source-grounded rationale that explains why this direction or case exists.

This traceability is part of the workbook contract, not optional metadata.
It exists so that:
- the human can inspect why a direction or case was created;
- user priorities cannot be silently dissolved inside generic planning;
- source-grounded planning remains explainable rather than magical.

## Run readiness section

The `Run readiness` section is written by the LLM after reconciling the workbook with all human feedback.

### Fields

```
RUN_READY: no|yes
READINESS_NOTE: [explanation]
```

The workbook grammar allows an empty `READINESS_NOTE` value — this is the valid state for fresh/init workbooks before any reconciliation has occurred. However, the `reconcile_readiness` response contract is stricter: see Contract below.

### Contract

- `RUN_READY: yes` means: all cases have an input and an expected behavior, directions and cases are consistent, human feedback has been incorporated, and the workbook is ready for a run. `READINESS_NOTE` may be empty or contain an optional explanation.
- `RUN_READY: no` means: the LLM identified a reason the workbook is not ready. The reason must be stated in `READINESS_NOTE` — blank or whitespace-only is not accepted when the reconcile_readiness adapter actively decides not-ready.
- This is an orchestration signal created by the LLM and read by the human. It is not a quality judgment by code.
- Code must not proceed to run if `RUN_READY` is not `yes`.

## Artifact references section

The `Artifact references` section contains file paths to produced artifacts:

```
## Artifact references
- run: path/to/run_<run_id>.json
- analysis: path/to/analysis_<analysis_id>.md
- compare: path/to/compare_<compare_id>.md
```

Where `analysis_id` and `compare_id` are 12-character lowercase hex strings (UUID4 prefix), independent of `run_id`.

**Auto-update behavior:**
- `run` reference: auto-filled by the `run` command after producing a run artifact.
- `analysis` reference: auto-filled by the `analyze` command after producing an analysis artifact.
- `compare` reference: **not** auto-filled in v1. Compare operates across runs that may come from different workbooks, so automatic workbook update would require ambiguous multi-workbook choice resolution. The user can manually set this reference if desired.

## Case structure

Each case in the workbook must contain:

| Field | Required | Description |
|-------|----------|-------------|
| Case ID | Yes | Stable identifier, unique within the workbook |
| Input | Yes | The input to pass to the workflow under test |
| Context | No | Additional context the workflow needs, if any |
| Notes | No | Observations, reasoning, or special considerations |
| Target directions | Yes | Which directions this case covers |
| Expected behavior | Yes | Structured semantic description of intended behavior |

## Expected behavior

Expected behavior is a structured semantic description of intent, not a code contract.

It must describe:
- what the workflow should do in this case
- what is especially important in this case
- what would be unacceptable
- where the boundary lies, if this is a boundary case
- which behavior obligations the analysis should focus on

Expected behavior must be:
- structured enough that both human and LLM read it the same way
- universal enough to apply to both structured and free-text workflow outputs
- not tied to specific field names of a particular workflow's output format
