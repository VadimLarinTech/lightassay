# Reviewed Flow (v1)

This document describes the reviewed, agreed-upon flow for v1. Each step specifies who acts, what they produce, and what the next step requires.

The diagram below is the explicit, stage-by-stage flow. The main self-serve
entrypoint — `lightassay quickstart` — wraps steps 0 through 6 into one
command that takes a single plain-language message plus an optional target
hint and drives the full bootstrap → preparation → run → analysis pass.
`lightassay continue` is the iterative counterpart that runs one full next
iteration on the active workbook with optional compare against the prior
run. See `README.md` and `docs/quickstart.md` for the user-facing entry
points; this document is the reference for what each underlying step must
produce.

---

## Overview

```
[Human/Agent: define target] → [Human: fill brief] → [LLM: generate directions] → [Human: feedback on directions]
→ [LLM: generate cases] → [Human: feedback on cases]
→ [LLM: reconcile workbook, set RUN_READY]
→ [Code: run workflow, save run artifact]
→ [LLM: analyze run artifact, save analysis]
→ [LLM: compare run artifacts, save compare]  ← separate, explicit step
```

---

## Step 0 — Target definition (Human / Agent)

The evaluation starts by defining the target.

The target captures:
- what is being evaluated;
- where the real execution boundary lives;
- which source files/modules planning should inspect;
- any scope notes that bound discovery and prevent drift into unrelated code.

**Output:** workbook with completed `## Target` section.

**Required for next planning step:** target must contain:
- `TARGET_KIND`
- `TARGET_NAME`
- `TARGET_LOCATOR`
- `TARGET_BOUNDARY`
- at least one `TARGET_SOURCES` entry

This is what makes the workbook `planning-ready` together with the brief.

---

## Step 1 — User intention (Human)

The human fills the guided brief inside the workbook.

The brief captures:
- what is being tested
- what aspects of output behavior matter
- which sides of behavior are most significant (primary / risky / suspicious)
- which failure modes and problem classes are important
- what must not break
- any examples, production observations, known weak spots, constraints, or scale preferences

The brief is written in natural language. The system may structure it internally
as user priorities, but the human is not expected to write in schema terms.

**Output:** workbook with completed `## Brief` section.

**Required for next step:** the brief must contain at least one line of human-authored content beyond template scaffolding (`### ` headings, `<!-- -->` HTML comments, blank lines). The `prepare-directions` CLI gate enforces this structurally — a fresh untouched workbook with only template scaffolding is rejected.

At this point the workbook may be `planning-ready` without being `run-ready`.
An execution adapter is not required yet.

---

## Step 2 — Directions (LLM)

The LLM reads:
- the target;
- the target sources and bounded target-anchored discovery context;
- the human brief and user priorities.

It does not generate directions from the brief alone.

Directions are built along two universal coordinates:

**Coordinate 1: Behavior obligations** — what the workflow is responsible for doing (e.g., produce the main decision, explain the output, preserve invariants, transform input correctly).

**Coordinate 2: Test lenses** — universal testing perspectives (e.g., nominal correct behavior, explicit failures, boundary/ambiguous cases, input variability and noise, robustness, known weak spots, critical invariants, costly errors, must-have scenarios).

Default: full coverage across all relevant obligations and all relevant lenses. Narrowing coverage is an explicit configuration, not the default.

Directions must be expressed in universal terms — not tied to specific field names of the workflow's output format.

User priorities remain primary. Code- and prompt-grounded reasoning may enrich
or refine the directions, but it must not silently override what the human said
matters most.

**Output:** workbook `## Directions` section filled with LLM-generated directions.

**Then:** human reviews and writes feedback.

### Step 2a — Human feedback on directions

The human may:
- write a global instruction in `### HUMAN:global_instruction` under `## Directions`
- write a local instruction in `HUMAN:instruction` under any specific direction

An empty field means: no objections, approved.

**Output:** workbook with `HUMAN:` fields under directions filled (or confirmed empty).

---

## Step 3 — Cases (LLM)

The LLM reads:
- the target
- target-grounded source context
- the brief
- all generated directions
- all human feedback on directions

The LLM generates concrete test cases. Each case must have:
- stable case ID
- input
- context (if needed)
- notes
- target directions (which directions this case covers)
- expected behavior (structured semantic description of intent)

Expected behavior describes what the workflow should do, what is critical, what would be unacceptable, and which behavior obligations the analysis should focus on.

Cases must be traceable back to:
- the direction(s) they cover;
- the target behavior facet or prompt/code seam that motivated them;
- the user priorities they are meant to preserve.

**Output:** workbook `## Cases` section filled with LLM-generated cases.

**Then:** human reviews and writes feedback.

### Step 3a — Human feedback on cases

The human may:
- write a global instruction in `### HUMAN:global_instruction` under `## Cases`
- write a local instruction in `HUMAN:instruction` under any specific case

An empty field means: no objections, approved.

**Output:** workbook with `HUMAN:` fields under cases filled (or confirmed empty).

---

## Step 4 — Run readiness (LLM)

The LLM reads the full workbook including all human feedback.

The LLM must:
- incorporate all global and local human feedback
- resolve any semantic contradictions between directions and cases
- bring every case to a state where it has both `input` and `expected behavior`
- write `RUN_READY: yes` if the workbook is ready for a run
- write `RUN_READY: no` with a `READINESS_NOTE` if it is not

This is an explicit orchestration signal. The LLM creates it; the human reads it. Code enforces it as a precondition for run.

**Output:** workbook with reconciled cases and explicit `RUN_READY` status set.

**Required for next step:** `RUN_READY: yes` in the workbook.

This is still only workbook-level run readiness. A real execution binding is
also required before code can run the cases.

---

## Step 5 — Run (Code)

Code executes one independent run:

1. Reads workbook, verifies `RUN_READY: yes` and a valid execution binding. Stops with an error if not.
2. Computes and stores `workbook_sha256`.
3. For each case: calls the workflow under test, records raw response, parsed response (if applicable), duration, token usage, and execution status.
4. If a case throws an execution error: records `failed_execution` with the error message.
5. Sets overall run status: `completed` if all cases are `completed`, `failed` if any are `failed_execution`.
6. Writes the run artifact JSON.
7. Updates the workbook's `## Artifact references` section with the run artifact path.

Code does not judge whether the workflow's output was good. Code only records what happened.

**Output:** run artifact JSON file.

Execution remains black-box at the configured boundary even if planning was
white-box-informed through code and prompt inspection.

---

## Step 6 — Analysis (LLM)

The LLM receives the run artifact and performs semantic analysis.

The LLM must address:
- which cases met their expected behavior and how
- which cases did not meet expected behavior and how
- borderline or ambiguous cases
- repeating patterns across cases
- identified weak spots
- observations on tokens, latency, and other raw facts
- recommendations that follow from the data

**Output:** analysis artifact (markdown), path written to workbook `## Artifact references`.

---

## Step 7 — Compare (LLM, separate step)

Compare is always a separate, explicitly initiated operation. It is never part of a run.

The LLM receives two or more completed run artifacts (status: `completed` only).

The LLM must show:
- which runs were compared and what configuration differences are significant
- where one variant showed stronger behavior quality
- different failure patterns across runs
- raw fact differences (tokens, latency, etc.)
- where the conclusion is clear vs. where uncertainty remains
- recommendations where they legitimately follow

**Output:** compare artifact (markdown). In v1, the tool does **not** update any workbook's `## Artifact references` automatically. Compare operates across runs that may come from different workbooks; automatic workbook update would require ambiguous multi-workbook choice resolution. The user can manually set the `- compare:` reference if desired.

---

## What does not exist in v1

- Partial run / resume: if a run fails, fix the cause and start a new run from scratch
- Compare inside run: compare is always a separate step
- Automated quality judgment: code never assesses whether output was good
- Stage-level testing: v1 evaluates end-to-end workflow behavior only
- History snapshots: workbook is overwritten, no snapshot copies
- Compact human-feedback history: optional future addition, not required for v1 to work
- Silent repair or guessed values: invalid target, brief, binding, or workbook state must fail explicitly
