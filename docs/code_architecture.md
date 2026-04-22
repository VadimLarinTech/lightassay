# Code Architecture

## What this document is

This document describes the architecture of `lightassay` **from the actual
code**:

- `src/lightassay/`
- `tests/`
- `examples/`
- `pyproject.toml`

It is not a retelling of a product plan and not a repackaging of spec files.
Where code and written intent diverge, this document follows the code.

---

## 1. System View

`lightassay` is a local file-based eval engine for applied LLM workflows.

In the current code the project is organized as a system of three concentric
surfaces:

1. `L1` — ordinary library surface for agents and applications.
2. `L2` — diagnostics/recovery surface.
3. `L3` — expert inspection/control surface.

The real operational model is neither server-based nor database-backed. The
system lives around files:

- workbook in markdown
- config files in JSON
- run artifact in JSON
- analysis/compare artifacts in markdown

Internal code does not keep hidden runtime state between calls. A session
in `surface.py` only holds file paths and a `released` flag; actual state
is derived from the files each time it is needed.

### Main architectural property

`lightassay` is a **controller/orchestrator over external boundaries**, not
an engine that itself runs the LLM scenario.

It:

- manages the preparation and evaluation flow;
- validates contracts;
- calls the external workflow under test;
- calls the external semantic/preparation adapter;
- saves artifacts;
- exposes typed state and diagnostics.

It does not:

- own the business logic of the workflow under test;
- keep history in a database;
- hide errors through fallback behavior;
- “auto-fix” data.

---

## 2. Layered architecture

Below is the actual split into layers as it manifests in the code.

```text
┌──────────────────────────────────────────────────────────────┐
│ L1 Public Surface                                           │
│ __init__.py, surface.py, types.py, errors.py                │
└───────────────┬──────────────────────────────────────────────┘
                │
                ├───────────────┬──────────────────────────────┬───────────────┐
                │               │                              │               │
                v               v                              v               v
      Workbook/State     Preparation Pipeline         Execution Pipeline   Semantic Pipeline
      parser/renderer     preparation_config.py       workflow_config.py   semantic_config.py
      workbook_models.py  preparer.py                 runner.py            analyzer.py
                                                     adapter_pack/*       comparer.py
                                                     run_models.py
                                                     run_artifact_io.py
                │
                v
        Diagnostics / Expert
        diagnostics.py
        expert.py

Secondary surface:
    cli.py  -> thin wrapper over L1 primitives
```

### 2.1 L1 Public Surface

Files:

- `__init__.py`
- `surface.py`
- `types.py`
- `errors.py`

This is the main entry into the library.

The exports actually re-exported at the top level are:

- `open_session`
- `init_workbook`
- `quick_try`
- `quick_try_workbook`
- `refine_workbook`
- `explore_workbook`
- `compare_runs`
- `quickstart`
- `continue_workbook`
- `list_agents`
- `current_agent`
- `set_agent`
- `EvalSession`
- `EvalTarget`
- `EvalState`
- `PreparationStage`
- `PrepareResult`
- `QuickstartResult`
- `QuickTryResult`
- `ContinueResult`
- `RefineResult`
- `ExploreResult`
- `RunResult`
- `AnalyzeResult`
- `CompareResult`
- `EvalError`

This surface solves three concerns:

1. It provides a narrow typed API for the happy path.
2. It wraps internal exceptions into `EvalError`.
3. It consolidates orchestration of different engine modules into a single
   semantic control layer.

`surface.py` is the library’s actual application service layer:

- open a session;
- derive state;
- advance preparation;
- readiness checks;
- run;
- analyze;
- compare;
- open diagnostics.

### 2.2 Workbook / State Layer

Files:

- `workbook_models.py`
- `workbook_parser.py`
- `workbook_renderer.py`

This layer forms the **primary source of truth** for an eval session.

`Workbook` is the central domain model:

- `target`
- `brief`
- global instructions for directions/cases
- `directions`
- `cases`
- `run_readiness`
- `artifact_references`

An important point: the workbook is not just “human-readable markdown”. In
the code it plays the role of strict serialized state.

#### File roles inside the layer

`workbook_models.py`

- dataclass domain models;
- the minimal representation level of workbook state.

`workbook_parser.py`

- strict markdown → `Workbook` parser;
- enforces grammar;
- validates cross-references;
- does not perform tolerant parsing and does not guess intended meaning.

`workbook_renderer.py`

- canonical `Workbook` → markdown serializer;
- responsible for round-trip compatibility with the parser;
- contains the workbook init template, target-first template, and the
  `brief_has_user_content()` gate.

#### Architectural invariant

All further preparation logic is built not on an internal mutable graph but
on the sequence:

- `read file`
- `parse`
- `derive state`
- `mutate model`
- `render canonical markdown`
- `write file`

That is, the workbook layer is simultaneously:

- a state format;
- an exchange format between human and LLM;
- a persistent state machine snapshot.

### 2.3 Preparation Pipeline

Files:

- `preparation_config.py`
- `preparer.py`

This layer implements LLM-driven preparation of the workbook:

- generate directions
- generate cases
- reconcile readiness

In the current code the preparation layer no longer works as a raw
“brief-driven” mechanism. It hands the adapter:

- `target`;
- structured `user_priorities` derived from the brief;
- `source_context` with explicit sources and bounded target-anchored
  discovery;
- `planning_mode`;
- an optional `planning_context` for quick-try/exploratory scenarios.

`preparer.py` intentionally does not operate on markdown as the source of
adapter output. The architecture embedded in the code is important:

- the adapter returns **structured JSON**
- code validates it
- code mutates the `Workbook`
- the renderer produces canonical markdown

This keeps markdown as the format of truth, while not making the external
LLM responsible for the exact rendering of the whole file structure.

#### Internal structure of the preparation layer

`_call_adapter()`

- subprocess boundary for the preparation adapter;
- strict JSON stdin/stdout contract;
- no fallback.

`execute_generate_directions()`

- takes a `Workbook` + `PreparationConfig`;
- serializes `target`, `user_priorities`, and `source_context`;
- forbids re-entry when downstream state already exists;
- updates directions only.

`execute_generate_cases()`

- serializes target + brief + directions + human feedback + source context;
- validates the `target_directions` cross-reference;
- updates cases only.

`execute_reconcile_readiness()`

- takes the full workbook state;
- accepts reconciled directions + cases + readiness;
- re-writes directions/cases/readiness.

#### Important architectural property

The preparation layer deliberately protects derived state from partial
re-generation.

Planning is also semantically separated from the execution binding:

- `planning-ready` is defined by `Target` + sources + human brief;
- `run-ready` additionally requires an honest execution binding.

According to the code it is forbidden to:

- regenerate directions over existing cases;
- regenerate cases over existing readiness;
- mutate a workbook that already references downstream artifacts.

This makes preparation single-pass oriented rather than a freely editable
graph.

### 2.4 Execution Pipeline

Files:

- `workflow_config.py`
- `adapter_pack/__init__.py`
- `adapter_pack/python_callable.py`
- `adapter_pack/http_driver.py`
- `adapter_pack/command.py`
- `runner.py`
- `run_models.py`
- `run_artifact_io.py`

This layer is responsible for black-box execution of the workflow under
test.

#### Conceptual shape

The execution layer takes:

- workbook cases
- workflow config

and produces:

- per-case execution records
- aggregate raw facts
- run artifact

#### Layer structure

`workflow_config.py`

- strict config loader;
- supports two execution modes:
  - legacy `adapter`
  - typed `driver`
- injects `config_dir` into the command driver and defaults `working_dir` to
  config-origin semantics unless generated quickstart config sets an explicit
  workspace root.

`adapter_pack`

- generic first-party drivers:
  - `python-callable`
  - `http`
  - `command`
- provides typed configs and unified dispatch/validation logic.

`runner.py`

- orchestrates case execution;
- builds the request for the workflow boundary;
- dispatches either the driver or the legacy subprocess adapter;
- validates the adapter response;
- turns the result into a `CaseRecord` and a `RunArtifact`.

`run_models.py`

- dataclass representation of the run artifact in memory.

`run_artifact_io.py`

- serialization and strict deserialization of run artifact JSON;
- checks of status invariants and aggregate consistency.

#### Architectural meaning of the execution layer

The system does not know the internals of the workflow under test.

It only knows:

- how to build a case request;
- how to call the configured boundary;
- how to check the returned contract;
- how to aggregate raw execution facts.

This is, in fact, a black-box execution orchestration layer.

### 2.5 Semantic Pipeline

Files:

- `semantic_config.py`
- `analyzer.py`
- `comparer.py`

This layer is not responsible for running the workflow; it performs
**semantic interpretation** of run artifacts that have already been
produced.

`analyzer.py`

- takes a single `RunArtifact`;
- calls the semantic adapter with `operation = analyze`;
- requires `analysis_markdown`;
- adds a metadata header;
- returns the markdown artifact and an analysis id.

`comparer.py`

- takes at least two completed run artifacts;
- calls the semantic adapter with `operation = compare`;
- requires `compare_markdown`;
- adds a metadata header;
- returns the markdown artifact and a compare id.

#### Important architectural boundary

The semantic layer does not participate:

- in workbook preparation;
- in execution of the workflow under test;
- in raw metrics calculation.

Its job is post-execution:

- read an artifact;
- hand it to an external semantic engine;
- wrap the result in a standard artifact format.

### 2.6 Diagnostics / Recovery / Expert

Files:

- `diagnostics.py`
- `expert.py`
- parts of `surface.py`

This layer sits on top of L1, but is architecturally important because it
defines the second and third depths of interaction with the system.

`diagnostics.py`

- pure data types:
  - `DiagnosticConfidence`
  - `DiagnosticEvidence`
  - `RecoveryOption`
  - `DiagnosticReport`
  - `RecoveryResult`

`types.py`

- contains `DiagnosticsHandle` as the L2 runtime handle.

`expert.py`

- contains `ExpertHandle` and deep inspection views.

#### L2 as implemented in code

`open_diagnostics()` in `surface.py`:

- reads the workbook;
- builds `EvalState`;
- collects issues;
- builds structured reports;
- returns a `DiagnosticsHandle`.

Diagnostics exposes:

- `state`
- `issues`
- `reports`
- `apply_recovery_action()`
- `open_expert()`

For now recovery is intentionally small:

- the main bounded action today is `advance_preparation`.

#### L3 as implemented in code

`ExpertHandle` provides deep inspection:

- workbook source
- config bindings
- run artifact content

and one bounded low-level control:

- `rebind_config(...)`

So the expert layer in code is not a “second CLI” or an unrestricted admin
shell. It is an inspection-heavy surface with a very limited control scope.

### 2.7 CLI Surface

File:

- `cli.py`

In the current code the CLI is a secondary surface.

This is important not as a declaration but as a fact of implementation:

- handlers in `cli.py` do not reach directly into `runner.py`, `preparer.py`,
  `analyzer.py`;
- they route through `open_session()`, `EvalSession` methods, and
  `compare_runs()`.

CLI commands:

- `quickstart`
- `continue`
- `init`
- `agents`
- `workbook`
- `quick-try`
- `refine-suite`
- `explore-workbook`
- `run`
- `analyze`
- `compare`
- `prepare-directions`
- `prepare-cases`
- `prepare-readiness`
- `current-workbook`
- `workbooks`

Architecturally, the CLI is a thin wrapper over L1, not a separate
orchestration stack.

---

## 3. Main end-to-end flows

Below is how the system actually works end-to-end.

### 3.1 Workbook bootstrap

```text
init_workbook()
  -> render_init_workbook()
  -> write <name>.workbook.md
```

Properties:

- a valid skeleton workbook is created;
- the initial target and brief contain guided templates;
- initial readiness = `RUN_READY: no`.

### 3.2 Session open / state derivation

```text
open_session(workbook_path, ...)
  -> EvalSession(paths only)

session.state()
  -> _read_workbook()
  -> parse(markdown)
  -> _determine_preparation_stage()
  -> EvalState
```

This is an important flow: system state is not cached — it is re-derived on
every call.

`EvalState` in the current code already distinguishes:

- `planning_ready`
- `execution_binding_ready`
- `workbook_run_ready`
- and the final `run_ready`

### 3.3 Preparation flow

```text
session.prepare()
  -> load_preparation_config()
  -> read workbook
  -> determine current stage
  -> execute_generate_directions()
     or execute_generate_cases()
     or execute_reconcile_readiness()
  -> save canonical workbook
  -> rebuild EvalState
  -> PrepareResult
```

Properties:

- one `prepare()` call advances exactly one lawful stage;
- `PREPARED` does not allow further `prepare()`;
- the brief gate is checked before generating directions;
- the target/source gate also participates in planning readiness;
- on failure, preparation is not masked — it is raised as `EvalError`.

Additional user-facing planning entrypoints:

```text
quick_try()
  -> init_workbook()
  -> fill target
  -> write bounded bridge brief
  -> execute bounded directions/cases/readiness planning

refine_workbook()
  -> read existing workbook
  -> init new workbook
  -> preserve target
  -> preserve old directions/cases as first-class structure
  -> add explicit refinement context for the next planning step

explore_workbook()
  -> read existing workbook
  -> read seed run artifact
  -> init new workbook
  -> preserve target
  -> write bounded exploration brief
  -> execute bounded iterative planning and execution
  -> feed each new run artifact into the next iteration
```

### 3.4 Run flow

```text
session.run()
  -> load_workflow_config()
  -> read workbook
  -> readiness checks
  -> execute_run()
       -> for each case:
            build request
            call driver/subprocess adapter
            validate response
            build CaseRecord
       -> build Aggregate
       -> RunArtifact
  -> save run artifact JSON
  -> update workbook artifact refs
  -> RunResult
```

Properties:

- each case execution is isolated;
- case-level execution failures do not crash the whole orchestrator — they
  land inside the artifact;
- the overall run gets status `completed` or `failed` depending on case
  outcomes.

### 3.5 Analyze flow

```text
session.analyze(run_artifact_path)
  -> load run artifact
  -> verify workbook match
  -> load semantic config
  -> execute_analysis()
       -> call semantic adapter
       -> validate analysis_markdown
       -> render markdown artifact
  -> save analysis artifact
  -> update workbook refs
  -> AnalyzeResult
```

Properties:

- analyze requires artifact ↔ current workbook correspondence;
- failed runs are allowed as analyze inputs;
- the semantic body comes from outside; the metadata shell is completed by
  code.

### 3.6 Compare flow

```text
compare_runs(...)
  -> validate inputs
  -> load semantic config
  -> load run artifacts
  -> require all status=completed
  -> execute_compare()
       -> call semantic adapter
       -> validate compare_markdown
       -> render markdown artifact
  -> save compare artifact
  -> CompareResult
```

Properties:

- compare is deliberately a pre-session primitive;
- compare is not tied to a single workbook;
- compare does not update workbook refs.

### 3.7 Diagnostics / recovery / expert flow

```text
session.open_diagnostics()
  -> build EvalState
  -> collect issues
  -> build DiagnosticReport[]
  -> DiagnosticsHandle

diag.apply_recovery_action("advance_preparation")
  -> session.prepare()
  -> RecoveryResult

diag.open_expert()
  -> ExpertHandle
  -> inspect_workbook_source()
  -> inspect_config_bindings()
  -> inspect_run_artifact()
  -> rebind_config()
```

This is not a separate engine. It is an overlay on top of the same
state/orchestration core.

---

## 4. Sources of truth and state model

The project has several kinds of state, and they are kept strictly
separate.

### 4.1 Workbook state

Source of truth:

- markdown workbook file

Contains:

- human intent
- generated directions
- generated cases
- human feedback
- run readiness
- downstream artifact references

This is the primary state carrier for eval preparation.

### 4.2 Config state

Sources of truth:

- preparation config JSON
- workflow config JSON
- semantic config JSON

This is not a global config registry — it is a set of external bindings
that the session only references and validates.

### 4.3 Run state

Source of truth:

- run artifact JSON

Contains:

- run metadata
- case records
- aggregate execution facts

Run state is separated from the workbook, but the workbook stores a
reference to it.

### 4.4 Semantic output state

Sources of truth:

- analysis markdown
- compare markdown

These are no longer executable state — they are interpretive artifacts.

### 4.5 Session state

In memory, `EvalSession` holds only:

- workbook path
- config paths
- `released` flag

So a session is a handle, not a repository of truth.

---

## 5. Module map

Below is a practical map for entering the code.

### Public API and orchestration

- `__init__.py`
  - defines the ordinary top-level export set
- `surface.py`
  - the library’s main coordination module
- `orchestrator.py`
  - end-to-end orchestration for `quickstart` and `continue`
- `bootstrap.py`
  - quickstart bootstrap layer: drives the adapter-led target / execution
    shape resolution from a user message
- `workflow_config_builder.py`
  - turns a bootstrap-resolved execution shape into a validated workflow
    config (no hand-authored JSON for built-in shapes)
- `runtime_state.py`
  - active-workbook pointer, workbook-id registry, cwd-scoped JSONL execution
    log, plus the global default-agent config under the user's config home
- `backends.py`
  - internal built-in backend registry for the user-facing agents
    (`claude-cli`, `codex-cli`); resolves a name into `PreparationConfig`
    + `SemanticConfig`
- `types.py`
  - L1 result/state types plus `DiagnosticsHandle`
- `errors.py`
  - public error boundary and engine-specific errors

### Workbook / state representation

- `workbook_models.py`
  - workbook domain model
- `workbook_parser.py`
  - strict markdown → model
- `workbook_renderer.py`
  - model → canonical markdown

### Preparation

- `preparation_config.py`
  - loading of the preparation adapter config
- `preparer.py`
  - generation of directions/cases/readiness via the subprocess adapter

### Execution

- `workflow_config.py`
  - workflow config loader
- `adapter_pack/__init__.py`
  - driver config types + validation + dispatch layer
- `adapter_pack/python_callable.py`
  - direct Python callable execution
- `adapter_pack/http_driver.py`
  - HTTP JSON boundary execution
- `adapter_pack/command.py`
  - subprocess command execution
- `runner.py`
  - per-case execution orchestration
- `run_models.py`
  - in-memory run artifact model
- `run_artifact_io.py`
  - strict JSON serialization/deserialization

### Semantic operations

- `semantic_config.py`
  - semantic adapter config loader
- `analyzer.py`
  - single-run semantic analysis, plus strict validation of the structured
    next-step recommendations contract
- `comparer.py`
  - multi-run semantic comparison

### Built-in adapters

- `builtin_adapters/claude_cli.py` / `builtin_adapters/codex_cli.py`
  - thin entrypoints that dispatch every adapter operation through the
    locally authenticated CLI with forced JSON output
- `builtin_adapters/_agent_cli_common.py`
  - shared subprocess boundary, prompt templates, and the single
    boundary-based JSON parser (first `{` / last `}` → `json.loads`)
- `builtin_adapters/stub.py`
  - internal test helper, not a shipped backend

### Diagnostics and expert

- `diagnostics.py`
  - structured diagnosis/recovery types
- `expert.py`
  - deep inspection and bounded low-level control

### Secondary surface

- `cli.py`
  - CLI entrypoint over L1; the main user-facing commands are `init`,
    `agents`, `workbook`, `quickstart`, and `continue`

---

## 6. Dependency and boundary model

### 6.1 External boundaries

The project has three main kinds of external boundary:

1. `Preparation adapter`
   - subprocess JSON boundary
   - directions/cases/readiness generation

2. `Workflow under test`
   - legacy adapter subprocess
   - or first-party driver:
     - python callable
     - HTTP
     - command

3. `Semantic adapter`
   - subprocess JSON boundary
   - analysis/compare reasoning

Key architectural idea: the core does not hardcode a specific LLM provider
or workflow runtime. It owns only contracts and orchestration.

### 6.2 What belongs to the core

The core owns:

- workbook lifecycle
- preparation stage transitions
- config loading/validation
- case execution orchestration
- artifact generation
- diagnostics
- expert inspection

### 6.3 What does not belong to the core

The core does not own:

- business logic of the workflow under test
- actual semantic quality assessment
- bootstrap/install of external systems
- persistent storage outside of artifact files

---

## 7. Invariants actually enforced by code

Below is not a set of slogans — these are invariants that are actually
enforced by the code.

### 7.1 File truth over in-memory truth

State is derived from files on every call:

- workbook parse
- artifact load
- config validation

### 7.2 No fallback / no guessed values

The system systematically raises explicit errors on:

- parse violations
- missing config fields
- malformed adapter responses
- inconsistent artifact states

### 7.3 Canonical re-render after mutation

Workbook mutations are never written as raw strings. The code first updates
the dataclass model, then canonically renders markdown.

### 7.4 Separation of raw facts and semantic judgment

Code does:

- execute
- validate
- measure
- persist

The external semantic layer does:

- interpret
- compare
- narrate

### 7.5 Narrow public surface, wider internal engine

The ordinary entrance is narrow:

- `open_session`
- `EvalSession`
- `compare_runs`

Deep inspection lives in L2/L3 and does not stick out in the ordinary
top-level exports.

### 7.6 Bounded recovery

Recovery in diagnostics is not hidden auto-heal logic. It is a bounded set
of explicit actions executed through the handle.

---

## 8. Test topology as a reflection of architecture

Tests in `tests/` are organized along the same architectural zones as the
code:

- `test_workbook.py`
  - parser/renderer/workbook contracts
- `test_preparation.py`
  - preparation pipeline
- `test_run.py`
  - execution layer
- `test_analyze.py`
  - analysis layer
- `test_compare.py`
  - compare layer
- `test_surface.py`
  - L1 orchestration surface
- `test_diagnostics.py`
  - L2 diagnostics
- `test_expert.py`
  - L3 expert
- `test_adapter_pack.py`
  - first-party drivers
- `test_cli_library_parity.py`
  - parity between CLI and library paths
- `test_quickstart_continue.py`
  - end-to-end `quickstart` / `continue` orchestration, workbook
    continuation round-trip, active workbook pointer, workbook registry,
    default-agent persistence, JSON parser contract, brief ownership
- `test_smoke.py`
  - package/CLI smoke

This is a meaningful sign of architectural maturity: test topology mirrors
system topology rather than forming a chaotic pile of e2e files.

---

## 9. Architectural strengths

The following decisions look particularly strong in the code.

### 9.1 Clean separation of orchestration and semantics

The code does not mix:

- raw execution facts
- semantic quality judgments

This keeps the system extensible and honest about responsibilities.

### 9.2 Workbook as a strict state carrier

The workbook is simultaneously:

- human-editable
- LLM-consumable
- machine-validated
- file-based persistent state

This is a good architectural compromise for an agent-oriented workflow.

### 9.3 Adapter pack as a shape-of-boundary layer, not a business-logic layer

`python-callable`, `http`, `command` describe the shape of the boundary
rather than hardcoding knowledge about specific products.

### 9.4 CLI is genuinely secondary to the library API

In the code, the CLI does not live a separate life. This reduces the risk
of divergence between the CLI and library paths.

### 9.5 Diagnostics and expert are not wired into the happy path

L2/L3 are truly layered, not just “another bag of functions”.

---

## 10. Architectural weaknesses and limitations

Below is not a generic quality audit but the structural limitations
visible from the code.

### 10.1 Strong role of files as the operational substrate

This is a plus, but also a limitation:

- no concurrency model;
- no shared coordination layer;
- no transaction boundary across multiple files;
- the workflow is naturally oriented toward local usage and single-user
  editing semantics.

### 10.2 Preparation adapters and semantic adapters are still subprocess-first

There is no first-party driver pack for preparation/semantic layers
analogous to the one in the execution layer.

In other words, the execution boundary architecture is more developed than
the preparation/semantic boundary architectures.

### 10.3 Compare remains external to the workbook lifecycle

This is a deliberate decision in code, but it means:

- compare artifacts live separately;
- the unified lifecycle “workbook → all downstream outputs” is incomplete;
- compare does not re-enter the workbook state machine.

---

## 11. Bottom Line

From the actual code, `lightassay` today is not just a set of helper
utilities and not only a CLI over markdown.

It is already a structured multi-layer system with:

- a narrow public control surface,
- a strict workbook/state layer,
- separate preparation/execution/semantic pipelines,
- structured diagnostics,
- deliberate expert escalation,
- adapter-based external boundaries,
- and a secondary CLI built on the same core.

If the architectural essence is compressed into one sentence:

> `lightassay` is a file-truth, contract-driven orchestration layer for
> evaluating LLM workflows, where code manages state and execution while
> semantic generation and semantic assessment are delegated to external
> adapters.
