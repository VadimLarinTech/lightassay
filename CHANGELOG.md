# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] — Initial public release

### Added
- First public release of `lightassay`: a file-based, library-first eval
  harness for applied LLM workflows.
- Interactive CLI onboarding via `lightassay init` for choosing the
  default agent.
- `lightassay agents` for listing, inspecting, and changing the saved
  default agent.
- `lightassay workbook` for creating the next free empty workbook as
  `workbookN.workbook.md`.
- Main self-serve CLI flow via `lightassay quickstart` and follow-up
  iteration via `lightassay continue`.
- L1 public surface in `lightassay`: `open_session`, `init_workbook`,
  `quick_try`, `quick_try_workbook`, `refine_workbook`, `explore_workbook`,
  `compare_runs`, `quickstart`, `continue_workbook`, agent helpers, plus
  the supporting types (`EvalTarget`, `EvalState`, `PrepareResult`,
  `RunResult`, `AnalyzeResult`, `CompareResult`) and the error boundary
  `EvalError`.
- L2 diagnostics layer (`session.open_diagnostics()`) and L3 expert layer
  (`diag.open_expert()`) for bounded inspection and recovery.
- CLI entrypoint `lightassay` with `init`, `quick-try`, `refine-suite`,
  `explore-workbook`, `run`, `analyze`, `compare`, `prepare-directions`,
  `prepare-cases`, `prepare-readiness`, `agents`, `workbook`, `quickstart`,
  and `continue`.
- Built-in workflow drivers: `python-callable`, `http`, `command`.
- Built-in user-facing agents: `claude-cli` and `codex-cli`.
- Runnable end-to-end example in `examples/quickstart/` using deterministic
  stub adapters (zero external dependencies).
- Public documentation set under `docs/` covering workbook grammar,
  preparation protocol, semantic adapter protocol, flow, and architecture.
