# Quickstart

This guide is the **explicit, stage-by-stage** target-first path: create a
workbook, fill the brief, run preparation stages one by one, then run and
analyze. Use it when you want full control over every stage or when you
are writing your own preparation / workflow / semantic adapters from
scratch.

If you just want the tool to do the whole first pass from one plain-language
message, use the `lightassay quickstart` command described in `README.md`.
It wraps the steps below into a single self-serve entrypoint with the
saved default agent (or `--agent claude-cli` / `--agent codex-cli`) and
leaves the same canonical workbook behind for `lightassay continue` to
pick up.

The project does **not** expect the human or the agent to start by guessing
internal code paths from scratch. The normal flow is:

1. choose a target;
2. record the target in a workbook;
3. provide a natural-language brief;
4. let preparation build directions and cases from the target, the brief, and
   bounded source-grounded context;
5. bind a real execution adapter only when you are ready to run.

This guide assumes the package is installed (`pip install -e .` from the
repo, or `pip install lightassay`). All commands use the installed
`lightassay` CLI entrypoint.

---

## 1. Create the canonical start artifact

Create a workbook:

```bash
lightassay workbook
```

This workbook file is the canonical planning artifact. It is the same file
shape used by:
- normal full planning;
- `quick-try`;
- refinement from an existing suite;
- exploratory follow-up from a run artifact.

`lightassay workbook` always creates the next free numbered file in the
chosen directory (`workbook1.workbook.md`, `workbook2.workbook.md`, ...).
The examples below assume the created file is `workbook1.workbook.md`.

---

## 2. Fill the `## Target` section first

The `Target` block answers:
- what is being evaluated;
- where the real execution boundary lives;
- which sources planning should inspect.

Example:

```markdown
## Target

### TARGET_KIND
workflow

### TARGET_NAME
check_sentence

### TARGET_LOCATOR
myapp.pipeline.run

### TARGET_BOUNDARY
high-level sentence-check workflow boundary

### TARGET_SOURCES
- myapp/pipeline.py
- myapp/prompts/summarize.py

### TARGET_NOTES
Repeat-mode behavior matters. Do not drift into unrelated billing or auth code.
```

The workbook template already includes comments explaining each field and where
to get the value.
Write only inside the canonical target fields. If you need extra notes, use
`TARGET_NOTES`. Text outside target fields may be ignored.

---

## 3. Fill the `## Brief` in natural language

Do not translate your intention into an internal schema yourself.
Write what matters in ordinary language:
- what you want to test;
- what must not break;
- what looks risky;
- what kinds of failures matter.

The system structures that input internally as `user_priorities`, but the
human-facing input remains natural language.
Write only inside the canonical brief fields already present in the template.
If something does not fit, use `Additional context (optional)`. Text outside
those fields or under custom brief headings may be ignored.

---

## 4. Understand the two readiness states

`planning-ready` means:
- target is filled;
- at least one target source exists;
- the brief has human-authored content.

`run-ready` is stricter:
- the workbook must already be planning-ready;
- directions and cases must exist;
- readiness reconciliation must set workbook `RUN_READY: yes`;
- a valid execution binding (`workflow_config`) must exist.

This means:
- you **can** prepare a workbook before you have an adapter;
- you **cannot** run cases before the adapter or driver is bound.

---

## 5. Choose your start path

### Full planning

Use the normal preparation steps:

```bash
lightassay prepare-directions workbook1.workbook.md --preparation-config prep.json
lightassay prepare-cases workbook1.workbook.md --preparation-config prep.json
lightassay prepare-readiness workbook1.workbook.md --preparation-config prep.json
```

### Quick try

Use `quick-try` if you want the system to create one direction and one case and
show the full workbook shape immediately.

There are two valid start paths:

1. the fastest guided path for a human or agent:
   - create the canonical start workbook with `workbook`;
   - fill only the `## Target` block;
   - run `quick-try --workbook ...`;
2. the inline path:
   - provide the target fields directly on the CLI.

The first path is the recommended one because it exposes the real workbook
shape from the beginning and keeps the start artifact visible.

Recommended path:

```bash
lightassay workbook
# fill the ## Target block in workbook1.workbook.md
lightassay quick-try \
  --workbook workbook1.workbook.md \
  --user-request "Check that the workflow catches obvious errors without over-correcting." \
  --preparation-config prep.json
```

Inline path:

```bash
lightassay quick-try my-quick-try \
  --target-kind workflow \
  --target-name check_sentence \
  --target-locator myapp.pipeline.run \
  --target-boundary "high-level sentence-check workflow boundary" \
  --target-source myapp/pipeline.py \
  --target-source myapp/prompts/summarize.py \
  --user-request "Check that the workflow catches obvious errors without over-correcting." \
  --preparation-config prep.json
```

Quick try is a bridge into the full model, not a separate simplified format.

### Refine an existing suite

```bash
lightassay refine-suite baseline.workbook.md baseline-refined \
  --refinement-request "Keep the target but strengthen cases around repeat-mode."
```

### Explore from a prior run

```bash
lightassay explore-workbook baseline.workbook.md run_abc123.json baseline-explore \
  --exploration-goal "Find weak spots around failing cases." \
  --preparation-config prep.json \
  --workflow-config workflow.json \
  --max-cases 3 \
  --max-iterations 3
```

Exploratory mode is bounded, iterative, and evidence-driven:
- each iteration plans from the prior run evidence;
- each iteration executes against the bound workflow config;
- the resulting workbook records the iteration trace and the per-iteration run artifacts.

---

## 6. Bind execution only when you are ready to run

Planning and execution are deliberately separated.

After the workbook is prepared, bind a real workflow config and run:

```bash
lightassay run workbook1.workbook.md --workflow-config workflow.json
```

---

## 7. Important guardrails

- The system must not silently repair missing or conflicting input.
- User priorities remain primary even when planning reads code and prompts.
- Source-grounded planning is bounded by the target; it must not drift into
  unrelated parts of the repository.
- Execution remains black-box at the configured boundary even when planning is
  informed by internal code and prompts.
