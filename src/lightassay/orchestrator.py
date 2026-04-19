"""End-to-end orchestration for the quickstart and continue commands.

Quickstart is the main self-serve entrypoint. It turns one plain-language
message plus a target hint into a canonical workbook, a run
artifact, and an analysis artifact — without forcing the user to hand
author target / preparation / workflow / semantic config JSON.

Continue is the iterative counterpart. It consumes the workbook's
continuation block (and optional ``--message``), rotates the current
continuation into versioned history, and performs the next full
preparation → run → analyze iteration with an optional compare step.

Both commands update the active workbook pointer and append a
structured JSONL execution log so the terminal surface can stay quiet
while full traceability remains available on disk.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Callable

from .backends import list_backends, resolve_backend
from .bootstrap import (
    BootstrapResult,
    TargetResolution,
    bootstrap_quickstart,
)
from .errors import (
    EvalError,
    PreparationConfigError,
    PreparationError,
    RunError,
    SemanticConfigError,
)
from .preparation_config import PreparationConfig, load_preparation_config
from .preparer import (
    execute_generate_cases,
    execute_generate_directions,
    execute_reconcile_readiness,
)
from .run_artifact_io import save_run_artifact
from .runner import execute_run
from .runtime_state import (
    append_execution_log,
    execution_log_path,
    get_active_workbook,
    set_active_workbook,
)
from .semantic_config import SemanticConfig, load_semantic_config
from .types import ContinueResult, QuickstartResult
from .workbook_models import (
    ArtifactReferences,
    ContinuationBlock,
    ContinuationFields,
    HistoricalContinuation,
    HumanFeedback,
    RunReadiness,
    Target,
)
from .workflow_config import LLMMetadata
from .workflow_config_builder import write_workflow_config

QUICKSTART_PLANNING_MODE = "quickstart_minimal_high_signal"
CONTINUE_PLANNING_MODE = "continue_refine"

_STAGE_BOOTSTRAP = "Resolving intent"
_STAGE_TARGET = "Building target"
_STAGE_PREP_DIRECTIONS = "Preparing directions"
_STAGE_PREP_CASES = "Preparing cases"
_STAGE_PREP_READINESS = "Reconciling readiness"
_STAGE_RUN = "Running workflow"
_STAGE_ANALYZE = "Writing analysis"
_STAGE_COMPARE = "Comparing with previous run"

StageReporter = Callable[[str, str, str], None]
"""Callback signature ``(stage_name, status, detail)`` — status is one
of ``in_progress``, ``done``, ``failed``.  ``detail`` is a short human
string (may be empty)."""

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _noop_reporter(stage: str, status: str, detail: str) -> None:  # pragma: no cover
    return


def _workflow_llm_metadata(
    workflow_provider: str | None,
    workflow_model: str | None,
) -> LLMMetadata | None:
    """Turn caller-supplied provider/model strings into LLMMetadata.

    Returns ``None`` when both are unset so the generated config does
    not ship placeholder LLM metadata for a non-LLM target.
    """
    provider = workflow_provider.strip() if workflow_provider else None
    model = workflow_model.strip() if workflow_model else None
    if not provider and not model:
        return None
    return LLMMetadata(provider=provider or None, model=model or None)


def _normalize_name(name: str) -> str:
    """Return a filesystem-safe workbook name derived from *name*."""
    cleaned = _NAME_SAFE_RE.sub("-", name.strip()).strip("-")
    return cleaned or "lightassay"


def _stage(
    reporter: StageReporter,
    log_root: str,
    command: str,
    workbook_path: str | None,
    backend: str,
    stage_name: str,
    status: str,
    detail: str,
    duration_ms: int | None = None,
) -> None:
    reporter(stage_name, status, detail)
    entry = {
        "command": command,
        "stage": stage_name,
        "status": status,
        "detail": detail,
        "workbook_path": workbook_path,
        "backend": backend,
    }
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    append_execution_log(entry, state_root=log_root)


def _wrap_stage(
    reporter: StageReporter,
    log_root: str,
    command: str,
    workbook_path: str | None,
    backend: str,
    stage_name: str,
    func: Callable[[], object],
):
    _stage(reporter, log_root, command, workbook_path, backend, stage_name, "in_progress", "")
    start = time.monotonic()
    try:
        value = func()
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        _stage(
            reporter,
            log_root,
            command,
            workbook_path,
            backend,
            stage_name,
            "failed",
            str(exc),
            duration_ms=duration_ms,
        )
        raise
    duration_ms = int((time.monotonic() - start) * 1000)
    _stage(
        reporter,
        log_root,
        command,
        workbook_path,
        backend,
        stage_name,
        "done",
        "",
        duration_ms=duration_ms,
    )
    return value


def _apply_target_to_workbook(workbook, target: TargetResolution) -> None:
    workbook.target = Target(
        kind=target.kind,
        name=target.name,
        locator=target.locator,
        boundary=target.boundary,
        sources=list(target.sources),
        notes=target.notes,
    )


def _render_quickstart_brief(
    target: TargetResolution,
    user_message: str,
    assumptions: list[str],
    resolution_notes: str,
) -> str:
    """Build the guided-brief content quickstart writes into the workbook.

    The canonical brief contains only target facts, the human-authored
    intent, and clearly marked assumptions / resolution notes. System
    authored planning guidance (focus notes, failure-mode platitudes,
    "must not break" boilerplate) is deliberately omitted — it belongs
    in the bootstrap/planning prompts, not in the user-priority sections
    of the workbook that preparation later reads as human priority input.
    """
    assumption_lines = "\n".join(f"- {a}" for a in assumptions) or "- (none recorded)"
    resolution_notes_block = resolution_notes.rstrip()
    resolution_notes_section = (
        f"\nBootstrap resolution notes:\n{resolution_notes_block}\n"
        if resolution_notes_block
        else ""
    )
    return (
        "### What is being tested\n"
        f"{target.name} ({target.kind}) at {target.locator}.\n"
        f"Boundary: {target.boundary}.\n\n"
        "### What matters in the output\n"
        f"{user_message.strip()}\n\n"
        "### Additional context (optional)\n"
        "Bootstrap assumptions:\n"
        f"{assumption_lines}\n"
        f"{resolution_notes_section}"
    )


def _quickstart_planning_context(bootstrap: BootstrapResult, user_message: str) -> dict:
    return {
        "user_message": user_message.strip(),
        "target": {
            "kind": bootstrap.target.kind,
            "name": bootstrap.target.name,
            "locator": bootstrap.target.locator,
            "boundary": bootstrap.target.boundary,
            "sources": list(bootstrap.target.sources),
        },
        "assumptions": list(bootstrap.assumptions),
        "quickstart_constraints": {
            "max_directions": bootstrap.constraints.max_directions,
            "max_cases": bootstrap.constraints.max_cases,
            "focus_notes": list(bootstrap.constraints.focus_notes),
        },
        "full_intent": bootstrap.full_intent,
        "internal_quickstart_framing": (
            # This framing is system-authored and intentionally lives in
            # planning context only — it must never be written into the
            # user-priority sections of the workbook brief.
            "Baseline quickstart: keep the first pass small and high-signal, "
            "prefer the most important user-facing risks, and stay narrow. "
            "Human priority wins on any conflicting portion of the request."
        )
        if not bootstrap.full_intent
        else (
            "Full-intent mode: follow the human request as stated without "
            "the default minimal-first-pass narrowing."
        ),
    }


def _continue_planning_context(
    continuation_text: str,
    continuation_source: list[str],
    previous_directions_full: list[dict],
    previous_cases_full: list[dict],
) -> dict:
    """Build planning_context for continue.

    Captures the previous directions / cases BEFORE the workbook is
    reset so the adapter receives the full prior state and can truly
    extend or refine it (per plan §2 "Continue is a full iteration").
    """
    return {
        "continuation_text": continuation_text.strip(),
        "continuation_sources": list(continuation_source),
        "previous_directions": [d["direction_id"] for d in previous_directions_full],
        "previous_cases": [c["case_id"] for c in previous_cases_full],
        "previous_directions_full": previous_directions_full,
        "previous_cases_full": previous_cases_full,
    }


def _snapshot_previous_directions(workbook) -> list[dict]:
    return [
        {
            "direction_id": d.direction_id,
            "body": d.body,
            "behavior_facet": d.behavior_facet,
            "testing_lens": d.testing_lens,
            "covered_user_priority_sections": list(d.covered_user_priority_sections),
            "source_rationale": d.source_rationale,
            "human_instruction": d.human_instruction.text,
        }
        for d in workbook.directions
    ]


def _snapshot_previous_cases(workbook) -> list[dict]:
    return [
        {
            "case_id": c.case_id,
            "input": c.input,
            "target_directions": list(c.target_directions),
            "expected_behavior": c.expected_behavior,
            "behavior_facet": c.behavior_facet,
            "testing_lens": c.testing_lens,
            "covered_user_priority_sections": list(c.covered_user_priority_sections),
            "source_rationale": c.source_rationale,
            "context": c.context,
            "notes": c.notes,
            "human_instruction": c.human_instruction.text,
        }
        for c in workbook.cases
    ]


def _resolve_backend_label(
    backend: str | None,
    prep_config: PreparationConfig | None,
) -> str:
    if backend is not None:
        return backend
    if prep_config is None:
        return "unconfigured"
    return f"{prep_config.provider}/{prep_config.model}"


def _resolve_adapter_configs(
    *,
    backend: str | None,
    preparation_config: str | PreparationConfig | None,
    semantic_config: str | SemanticConfig | None,
) -> tuple[PreparationConfig | None, SemanticConfig | None, str | None]:
    """Resolve adapter configs from either a built-in backend name or
    explicit config paths.

    Precedence: explicit paths override backend-provided defaults.
    Returns ``(prep_config, sem_config, active_backend_name_or_None)``.
    """
    active_backend: str | None = None
    prep_from_backend: PreparationConfig | None = None
    sem_from_backend: SemanticConfig | None = None

    if backend is not None:
        prep_from_backend, sem_from_backend = resolve_backend(backend)
        active_backend = backend

    if isinstance(preparation_config, PreparationConfig):
        prep_config: PreparationConfig | None = preparation_config
    elif isinstance(preparation_config, str):
        try:
            prep_config = load_preparation_config(preparation_config)
        except PreparationConfigError as exc:
            raise EvalError(f"Preparation config invalid: {exc}") from exc
    elif prep_from_backend is not None:
        prep_config = prep_from_backend
    else:
        raise EvalError(
            "Cannot resolve preparation adapter: supply either agent "
            f"(one of {list_backends()}) or preparation_config."
        )

    if isinstance(semantic_config, SemanticConfig):
        sem_config: SemanticConfig | None = semantic_config
    elif isinstance(semantic_config, str):
        try:
            sem_config = load_semantic_config(semantic_config)
        except SemanticConfigError as exc:
            raise EvalError(f"Semantic config invalid: {exc}") from exc
    elif sem_from_backend is not None:
        sem_config = sem_from_backend
    else:
        raise EvalError(
            "Cannot resolve semantic adapter: supply either agent "
            f"(one of {list_backends()}) or semantic_config."
        )

    return prep_config, sem_config, active_backend


# ── Quickstart ───────────────────────────────────────────────────────────────


def run_quickstart(
    name: str,
    *,
    message: str,
    target_hint: str,
    preparation_config: str | PreparationConfig | None = None,
    semantic_config: str | SemanticConfig | None = None,
    output_dir: str = ".",
    backend: str | None = None,
    reporter: StageReporter | None = None,
    workflow_provider: str | None = None,
    workflow_model: str | None = None,
    full_intent: bool = False,
) -> QuickstartResult:
    """Run the full quickstart orchestration end-to-end.

    Adapter configuration comes from either ``backend`` (built-in
    registry, e.g. ``"claude-cli"``) or explicit ``preparation_config``
    / ``semantic_config`` paths.  Explicit paths override backend
    defaults per slot.

    Steps: bootstrap → build target/workflow/brief → preparation (3
    stages) → run → analyze → update artifact references and the
    active workbook pointer → append structured log events.

    Raises ``EvalError`` on any orchestration failure.  Individual
    preparation / run / analyze errors are normalised into ``EvalError``.
    """
    # Lazy import to avoid surface import cycles.
    from .surface import (
        _build_eval_state,
        _read_workbook,
        _save_workbook,
        init_workbook,
    )

    if not isinstance(message, str) or not message.strip():
        raise EvalError("Quickstart requires a non-empty --message.")
    if not isinstance(target_hint, str) or not target_hint.strip():
        raise EvalError("Quickstart requires a non-empty --target.")
    if not os.path.isdir(output_dir):
        raise EvalError(f"Output directory does not exist: {output_dir!r}")

    reporter = reporter or _noop_reporter
    state_root = os.getcwd()
    log_root = state_root

    # Bootstrap is the authoritative target resolution path. It needs
    # either a backend or an explicit preparation config; there is no
    # local deterministic auto-binding fallback any more.
    prep_config_obj, sem_config_obj, active_backend = _resolve_adapter_configs(
        backend=backend,
        preparation_config=preparation_config,
        semantic_config=semantic_config,
    )
    backend_label = _resolve_backend_label(active_backend, prep_config_obj)

    try:
        bootstrap_result: BootstrapResult = _wrap_stage(
            reporter,
            log_root,
            "quickstart",
            None,
            backend_label,
            _STAGE_BOOTSTRAP,
            lambda: bootstrap_quickstart(
                message,
                target_hint=target_hint,
                preparation_config=prep_config_obj,
                workspace_root=os.getcwd(),
                full_intent=full_intent,
            ),
        )
    except PreparationError as exc:
        raise EvalError(f"Bootstrap failed: {exc}") from exc

    if bootstrap_result.clarification_request is not None:
        raise EvalError(
            "Quickstart stopped: bootstrap needs a clarification before it can "
            f"proceed: {bootstrap_result.clarification_request}"
        )
    if bootstrap_result.target is None or bootstrap_result.execution_shape is None:
        raise EvalError(
            "Quickstart stopped: bootstrap did not produce both a target and an "
            "execution shape, and did not return a clarification request."
        )

    # Stage 2: build workbook + workflow config.
    safe_name = _normalize_name(name)
    workbook_path = _wrap_stage(
        reporter,
        log_root,
        "quickstart",
        None,
        backend_label,
        _STAGE_TARGET,
        lambda: init_workbook(safe_name, output_dir=output_dir),
    )

    workbook = _read_workbook(workbook_path)
    _apply_target_to_workbook(workbook, bootstrap_result.target)
    workbook.brief = _render_quickstart_brief(
        bootstrap_result.target,
        message,
        bootstrap_result.assumptions,
        bootstrap_result.resolution_notes,
    )
    workbook.continuation = ContinuationBlock()
    _save_workbook(workbook, workbook_path)

    workflow_config_path = os.path.join(output_dir, f"{safe_name}.workflow.generated.json")
    write_workflow_config(
        bootstrap_result.execution_shape,
        workflow_id=f"quickstart-{safe_name}",
        llm_metadata=_workflow_llm_metadata(workflow_provider, workflow_model),
        path=workflow_config_path,
    )

    planning_context = _quickstart_planning_context(bootstrap_result, message)

    # Stage 3 — preparation (three stages, recorded individually).
    def _do_directions():
        wb = _read_workbook(workbook_path)
        wb = execute_generate_directions(
            wb,
            prep_config_obj,
            source_root=os.path.dirname(workbook_path),
            planning_mode=QUICKSTART_PLANNING_MODE,
            planning_context=planning_context,
        )
        _save_workbook(wb, workbook_path)
        return wb

    def _do_cases():
        wb = _read_workbook(workbook_path)
        wb = execute_generate_cases(
            wb,
            prep_config_obj,
            source_root=os.path.dirname(workbook_path),
            planning_mode=QUICKSTART_PLANNING_MODE,
            planning_context=planning_context,
        )
        _save_workbook(wb, workbook_path)
        return wb

    def _do_readiness():
        wb = _read_workbook(workbook_path)
        wb = execute_reconcile_readiness(
            wb,
            prep_config_obj,
            source_root=os.path.dirname(workbook_path),
            planning_mode=QUICKSTART_PLANNING_MODE,
            planning_context=planning_context,
        )
        _save_workbook(wb, workbook_path)
        return wb

    try:
        _wrap_stage(
            reporter,
            log_root,
            "quickstart",
            workbook_path,
            backend_label,
            _STAGE_PREP_DIRECTIONS,
            _do_directions,
        )
        _wrap_stage(
            reporter,
            log_root,
            "quickstart",
            workbook_path,
            backend_label,
            _STAGE_PREP_CASES,
            _do_cases,
        )
        workbook = _wrap_stage(
            reporter,
            log_root,
            "quickstart",
            workbook_path,
            backend_label,
            _STAGE_PREP_READINESS,
            _do_readiness,
        )
    except PreparationError as exc:
        raise EvalError(f"Quickstart preparation failed: {exc}") from exc

    if not workbook.run_readiness.run_ready:
        raise EvalError(
            "Quickstart preparation finished but RUN_READY is 'no': "
            f"{workbook.run_readiness.readiness_note!r}"
        )

    # Stage 4 — run.
    run_result = _wrap_stage(
        reporter,
        log_root,
        "quickstart",
        workbook_path,
        backend_label,
        _STAGE_RUN,
        lambda: _execute_run_and_save(workbook, workbook_path, workflow_config_path, output_dir),
    )
    run_artifact, run_artifact_path = run_result

    # Update workbook with run artifact ref (read fresh from disk to respect any
    # prep-time mutations, then re-save).
    workbook = _read_workbook(workbook_path)
    workbook.artifact_references.run = run_artifact_path
    _save_workbook(workbook, workbook_path)

    # Stage 5 — analyze.
    analysis_artifact_path = _wrap_stage(
        reporter,
        log_root,
        "quickstart",
        workbook_path,
        backend_label,
        _STAGE_ANALYZE,
        lambda: _execute_analyze_and_save(
            run_artifact,
            run_artifact_path,
            sem_config_obj,
            output_dir,
            analysis_profile="quickstart_first_pass",
            analysis_context={"user_message": message.strip()},
        ),
    )

    workbook = _read_workbook(workbook_path)
    workbook.artifact_references.analysis = analysis_artifact_path
    _save_workbook(workbook, workbook_path)

    pointer_path = set_active_workbook(workbook_path, state_root=state_root)

    state = _build_eval_state(workbook, workbook_path, workflow_config_path)
    conclusion = _quickstart_conclusion(run_artifact, workbook)

    append_execution_log(
        {
            "command": "quickstart",
            "event": "completed",
            "workbook_path": workbook_path,
            "backend": backend_label,
            "run_artifact": run_artifact_path,
            "analysis_artifact": analysis_artifact_path,
            "workflow_config": workflow_config_path,
        },
        state_root=log_root,
    )

    return QuickstartResult(
        workbook_path=workbook_path,
        run_artifact_path=run_artifact_path,
        analysis_artifact_path=analysis_artifact_path,
        workflow_config_path=workflow_config_path,
        conclusion=conclusion,
        assumptions=list(bootstrap_result.assumptions) + list(bootstrap_result.target.assumptions),
        direction_count=len(workbook.directions),
        case_count=len(workbook.cases),
        run_status=run_artifact.status,
        total_cases=run_artifact.aggregate.total_cases,
        completed_cases=run_artifact.aggregate.completed_cases,
        failed_cases=run_artifact.aggregate.failed_cases,
        state=state,
        execution_log_path=execution_log_path(log_root),
        active_workbook_pointer_path=pointer_path,
    )


def _execute_run_and_save(
    workbook,
    workbook_path: str,
    workflow_config_path: str,
    output_dir: str,
):
    from .workflow_config import load_workflow_config

    workflow_binding = load_workflow_config(workflow_config_path)
    try:
        artifact = execute_run(workbook, workbook_path, workflow_binding, workflow_config_path)
    except RunError as exc:
        raise EvalError(f"Run failed: {exc}") from exc

    artifact_filename = f"run_{artifact.run_id}.json"
    artifact_path = os.path.join(output_dir, artifact_filename)
    save_run_artifact(artifact, artifact_path)
    return artifact, artifact_path


def _execute_compare_and_save(
    run_artifact_paths: list[str],
    sem_config: SemanticConfig,
    *,
    goal: str | None,
    output_dir: str,
):
    """Compare runs with an already-resolved semantic config.

    Uses the same validation + rendering as
    :func:`lightassay.surface.compare_runs` but skips the path-loading
    step so built-in backends don't need a config JSON file.
    """
    from .comparer import execute_compare
    from .errors import CompareError, RunError
    from .run_artifact_io import load_run_artifact
    from .types import CompareResult

    if len(run_artifact_paths) < 2:
        raise EvalError(
            f"Compare requires at least 2 run artifacts, got {len(run_artifact_paths)}."
        )

    artifacts = []
    for path in run_artifact_paths:
        if not os.path.isfile(path):
            raise EvalError(f"Run artifact file not found: {path!r}")
        try:
            artifacts.append(load_run_artifact(path))
        except RunError as exc:
            raise EvalError(f"Run artifact invalid ({path!r}): {exc}") from exc

    for i, artifact in enumerate(artifacts):
        if artifact.status != "completed":
            raise EvalError(
                f"Run artifact {run_artifact_paths[i]!r} has status "
                f"{artifact.status!r}. Compare only accepts completed runs."
            )

    try:
        artifact_text, compare_id = execute_compare(
            artifacts, run_artifact_paths, sem_config, compare_goal=goal
        )
    except CompareError as exc:
        raise EvalError(f"Compare failed: {exc}") from exc

    artifact_filename = f"compare_{compare_id}.md"
    artifact_path = os.path.join(output_dir, artifact_filename)
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write(artifact_text)
    return CompareResult(artifact_path=artifact_path, compare_id=compare_id, goal=goal)


def _execute_analyze_and_save(
    run_artifact,
    run_artifact_path: str,
    sem_config: SemanticConfig,
    output_dir: str,
    *,
    analysis_profile: str,
    analysis_context: dict | None = None,
) -> str:
    from .analyzer import execute_analysis
    from .errors import AnalysisError

    try:
        artifact_text, analysis_id = execute_analysis(
            run_artifact,
            run_artifact_path,
            sem_config,
            analysis_profile=analysis_profile,
            analysis_context=analysis_context,
        )
    except AnalysisError as exc:
        raise EvalError(f"Analysis failed: {exc}") from exc

    artifact_filename = f"analysis_{analysis_id}.md"
    artifact_path = os.path.join(output_dir, artifact_filename)
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write(artifact_text)
    return artifact_path


def _quickstart_conclusion(run_artifact, workbook) -> str:
    agg = run_artifact.aggregate
    prefix = (
        "Run completed" if run_artifact.status == "completed" else "Run recorded execution failures"
    )
    readiness = workbook.run_readiness.readiness_note or "ready"
    return (
        f"{prefix}. {agg.completed_cases}/{agg.total_cases} cases completed, "
        f"{agg.failed_cases} failed. Readiness note: {readiness}."
    )


# ── Continue ─────────────────────────────────────────────────────────────────


def _collect_continuation_input(
    workbook,
    message: str | None,
) -> tuple[str, list[str]]:
    """Merge CLI --message with workbook continuation fields.

    Per the plan: if both the CLI --message and workbook continuation
    fields are present, both are consumed as signal. Neither source
    nullifies the other.
    """
    current = workbook.continuation.current
    pieces: list[str] = []
    sources: list[str] = []
    if message is not None and message.strip():
        pieces.append(f"CLI --message: {message.strip()}")
        sources.append("cli_message")
    if current.general_instruction.strip():
        pieces.append(f"Workbook general: {current.general_instruction.strip()}")
        sources.append("workbook_general")
    if current.direction_instruction.strip():
        pieces.append(f"Workbook directions: {current.direction_instruction.strip()}")
        sources.append("workbook_directions")
    if current.case_instruction.strip():
        pieces.append(f"Workbook cases: {current.case_instruction.strip()}")
        sources.append("workbook_cases")
    return ("\n\n".join(pieces), sources)


def _next_history_version(workbook) -> int:
    if not workbook.continuation.history:
        return 1
    return max(entry.version for entry in workbook.continuation.history) + 1


def _rotate_continuation(workbook, cli_message: str | None) -> int:
    """Move the current continuation into versioned history.

    Every successful continue writes a full visible history entry
    regardless of whether individual slots were filled. Empty slots
    remain present (as empty strings) so the historical record shows
    exactly which slots the human used for each iteration — per the
    plan's "truthful, complete visible history" requirement.

    ``cli_message`` is the literal ``--message`` argument (if any) and
    is stored as part of the history entry even though it was not
    written into the workbook's editable fields.
    """
    current = workbook.continuation.current
    version = _next_history_version(workbook)
    stored_fields = ContinuationFields(
        general_instruction=current.general_instruction,
        direction_instruction=current.direction_instruction,
        case_instruction=current.case_instruction,
    )
    normalized_cli = (cli_message or "").strip()
    workbook.continuation.history.append(
        HistoricalContinuation(
            version=version,
            fields=stored_fields,
            cli_message=normalized_cli,
        )
    )
    workbook.continuation.current = ContinuationFields()
    return version


def _resolve_active_workbook_path(
    state_root: str,
    explicit_workbook: str | None,
    workbook_id: str | None = None,
) -> str:
    # Mutually exclusive: --workbook and --workbook-id cannot both be set.
    if explicit_workbook is not None and workbook_id is not None:
        raise EvalError("continue: --workbook and --workbook-id are mutually exclusive.")

    if explicit_workbook is not None:
        if not os.path.isfile(explicit_workbook):
            raise EvalError(f"Workbook file not found: {explicit_workbook!r}")
        return os.path.abspath(explicit_workbook)

    if workbook_id is not None:
        from .runtime_state import resolve_workbook_id

        resolved = resolve_workbook_id(workbook_id, state_root=state_root)
        if resolved is None:
            raise EvalError(
                f"Unknown workbook id {workbook_id!r}. "
                "Use `lightassay workbooks` to list known workbook ids."
            )
        if not os.path.isfile(resolved):
            raise EvalError(f"Workbook id {workbook_id!r} references a missing file: {resolved!r}.")
        return resolved

    active = get_active_workbook(state_root)
    if active is None:
        raise EvalError(
            "No active workbook pointer and --workbook / --workbook-id were "
            "not supplied. Run `lightassay quickstart ...` first (or pass "
            f"--workbook explicitly). Expected pointer under "
            f"{state_root!r}/.lightassay/."
        )
    if not os.path.isfile(active):
        raise EvalError(
            f"Active workbook pointer references a missing file: {active!r}. "
            "Re-run quickstart or pass --workbook explicitly."
        )
    return active


def run_continue(
    *,
    preparation_config: str | PreparationConfig | None = None,
    semantic_config: str | SemanticConfig | None = None,
    message: str | None = None,
    workbook_path: str | None = None,
    workbook_id: str | None = None,
    workflow_config_path: str | None = None,
    output_dir: str = ".",
    compare_previous: bool = False,
    backend: str | None = None,
    reporter: StageReporter | None = None,
) -> ContinueResult:
    """Run one full continue iteration on the active (or explicit) workbook.

    Adapter configuration comes from either ``backend`` or explicit
    ``preparation_config`` / ``semantic_config`` paths (or both — paths
    override the backend defaults).

    Continue extends or refines directions / cases, runs again, analyzes
    again, and optionally compares against the previous run.
    """
    from .surface import (
        _build_eval_state,
        _read_workbook,
        _save_workbook,
    )

    if not os.path.isdir(output_dir):
        raise EvalError(f"Output directory does not exist: {output_dir!r}")

    reporter = reporter or _noop_reporter
    state_root = os.getcwd()
    log_root = state_root

    prep_config_obj, sem_config_obj, active_backend = _resolve_adapter_configs(
        backend=backend,
        preparation_config=preparation_config,
        semantic_config=semantic_config,
    )
    backend_label = _resolve_backend_label(active_backend, prep_config_obj)

    resolved_path = _resolve_active_workbook_path(
        state_root, workbook_path, workbook_id=workbook_id
    )
    workbook = _read_workbook(resolved_path)
    previous_run_artifact = workbook.artifact_references.run
    with open(resolved_path, encoding="utf-8") as fh:
        original_workbook_text = fh.read()

    continuation_text, continuation_sources = _collect_continuation_input(workbook, message)
    if not continuation_text:
        raise EvalError(
            "No continuation request provided. Add continuation instructions to the "
            "workbook or pass --message."
        )

    # Snapshot the previous iteration BEFORE any reset so the adapter
    # sees full prior directions/cases and can truly extend or refine
    # them rather than regenerate from scratch.
    previous_directions_full = _snapshot_previous_directions(workbook)
    previous_cases_full = _snapshot_previous_cases(workbook)

    # Persist the snapshot in the execution log before the destructive
    # save so the previous iteration is always recoverable from on-disk
    # state even if the process is killed between reset and preparation.
    append_execution_log(
        {
            "command": "continue",
            "event": "pre_reset_snapshot",
            "workbook_path": resolved_path,
            "previous_directions": previous_directions_full,
            "previous_cases": previous_cases_full,
            "previous_run_artifact": previous_run_artifact,
        },
        state_root=log_root,
    )

    if compare_previous and not previous_run_artifact:
        raise EvalError(
            "Cannot compare with previous run: the workbook does not reference a prior run."
        )

    # Continue must be mode-independent: it may reuse a quickstart-generated
    # workflow config next to the workbook, or accept an explicit config path.
    resolved_workflow_config_path = _resolve_continue_workflow_config(
        resolved_path,
        explicit_workflow_config=workflow_config_path,
    )

    # Reset directions/cases/readiness + artifact refs so preparation can
    # regenerate, but leave the current continuation fields intact.  They
    # rotate into history only after the full iteration succeeds so the
    # user does not lose their input if preparation fails midway.
    pending_version = _next_history_version(workbook)
    planning_context = _continue_planning_context(
        continuation_text,
        continuation_sources,
        previous_directions_full,
        previous_cases_full,
    )

    try:
        workbook.directions_global_instruction = HumanFeedback(continuation_text)
        workbook.cases_global_instruction = HumanFeedback(continuation_text)
        workbook.directions = []
        workbook.cases = []
        workbook.run_readiness = RunReadiness(run_ready=False, readiness_note="")
        workbook.artifact_references = ArtifactReferences(run=None, analysis=None, compare=None)
        _save_workbook(workbook, resolved_path)

        def _do(stage_name, fn):
            return _wrap_stage(
                reporter, log_root, "continue", resolved_path, backend_label, stage_name, fn
            )

        source_root = os.path.dirname(resolved_path)

        def _prep_directions():
            wb = _read_workbook(resolved_path)
            wb = execute_generate_directions(
                wb,
                prep_config_obj,
                source_root=source_root,
                planning_mode=CONTINUE_PLANNING_MODE,
                planning_context=planning_context,
            )
            _save_workbook(wb, resolved_path)
            return wb

        def _prep_cases():
            wb = _read_workbook(resolved_path)
            wb = execute_generate_cases(
                wb,
                prep_config_obj,
                source_root=source_root,
                planning_mode=CONTINUE_PLANNING_MODE,
                planning_context=planning_context,
            )
            _save_workbook(wb, resolved_path)
            return wb

        def _prep_readiness():
            wb = _read_workbook(resolved_path)
            wb = execute_reconcile_readiness(
                wb,
                prep_config_obj,
                source_root=source_root,
                planning_mode=CONTINUE_PLANNING_MODE,
                planning_context=planning_context,
            )
            _save_workbook(wb, resolved_path)
            return wb

        try:
            _do(_STAGE_PREP_DIRECTIONS, _prep_directions)
            _do(_STAGE_PREP_CASES, _prep_cases)
            workbook = _do(_STAGE_PREP_READINESS, _prep_readiness)
        except PreparationError as exc:
            raise EvalError(f"Continue preparation failed: {exc}") from exc

        if not workbook.run_readiness.run_ready:
            raise EvalError(
                "Continue preparation finished but RUN_READY is 'no': "
                f"{workbook.run_readiness.readiness_note!r}"
            )

        run_artifact, run_artifact_path = _do(
            _STAGE_RUN,
            lambda: _execute_run_and_save(
                workbook, resolved_path, resolved_workflow_config_path, output_dir
            ),
        )
        workbook = _read_workbook(resolved_path)
        workbook.artifact_references.run = run_artifact_path
        _save_workbook(workbook, resolved_path)

        analysis_artifact_path = _do(
            _STAGE_ANALYZE,
            lambda: _execute_analyze_and_save(
                run_artifact,
                run_artifact_path,
                sem_config_obj,
                output_dir,
                analysis_profile="continue_iteration",
                analysis_context={
                    "continuation_text": continuation_text,
                    "continuation_sources": continuation_sources,
                    "continuation_version": pending_version,
                },
            ),
        )
        workbook = _read_workbook(resolved_path)
        workbook.artifact_references.analysis = analysis_artifact_path
        _save_workbook(workbook, resolved_path)

        compare_artifact_path: str | None = None
        if compare_previous:
            compare_result = _do(
                _STAGE_COMPARE,
                lambda: _execute_compare_and_save(
                    [previous_run_artifact, run_artifact_path],
                    sem_config_obj,
                    goal="Continue iteration comparison",
                    output_dir=output_dir,
                ),
            )
            compare_artifact_path = compare_result.artifact_path
            workbook = _read_workbook(resolved_path)
            workbook.artifact_references.compare = compare_artifact_path
            _save_workbook(workbook, resolved_path)

        # Iteration fully succeeded — rotate current continuation into
        # history now so a failed mid-iteration does not strand the user's
        # input in a half-rotated state.
        consumed_version = _rotate_continuation(workbook, message)
        _save_workbook(workbook, resolved_path)
    except Exception:
        with open(resolved_path, "w", encoding="utf-8") as fh:
            fh.write(original_workbook_text)
        raise

    pointer_path = set_active_workbook(resolved_path, state_root=state_root)

    state = _build_eval_state(workbook, resolved_path, resolved_workflow_config_path)

    append_execution_log(
        {
            "command": "continue",
            "event": "completed",
            "workbook_path": resolved_path,
            "backend": backend_label,
            "run_artifact": run_artifact_path,
            "analysis_artifact": analysis_artifact_path,
            "compare_artifact": compare_artifact_path,
            "continuation_version": consumed_version,
            "continuation_sources": continuation_sources,
            "workflow_config": resolved_workflow_config_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        state_root=log_root,
    )

    conclusion = _continue_conclusion(
        run_artifact, workbook, compare_artifact_path, consumed_version
    )

    return ContinueResult(
        workbook_path=resolved_path,
        run_artifact_path=run_artifact_path,
        analysis_artifact_path=analysis_artifact_path,
        compare_artifact_path=compare_artifact_path,
        workflow_config_path=resolved_workflow_config_path,
        continuation_version=consumed_version,
        direction_count=len(workbook.directions),
        case_count=len(workbook.cases),
        run_status=run_artifact.status,
        total_cases=run_artifact.aggregate.total_cases,
        completed_cases=run_artifact.aggregate.completed_cases,
        failed_cases=run_artifact.aggregate.failed_cases,
        conclusion=conclusion,
        state=state,
        execution_log_path=execution_log_path(log_root),
        active_workbook_pointer_path=pointer_path,
    )


def _continue_conclusion(
    run_artifact,
    workbook,
    compare_artifact_path: str | None,
    consumed_version: int,
) -> str:
    agg = run_artifact.aggregate
    prefix = (
        "Run completed" if run_artifact.status == "completed" else "Run recorded execution failures"
    )
    readiness = workbook.run_readiness.readiness_note or "ready"
    suffix = " (compared against previous run)" if compare_artifact_path is not None else ""
    return (
        f"{prefix}. v{consumed_version} rotated to history; "
        f"{agg.completed_cases}/{agg.total_cases} cases completed, "
        f"{agg.failed_cases} failed. Readiness note: {readiness}.{suffix}"
    )


def _resolve_continue_workflow_config(
    workbook_path: str,
    *,
    explicit_workflow_config: str | None = None,
) -> str:
    """Resolve the execution binding used by continue.

    Continue is mode-independent: it may reuse a generated workflow
    config next to the workbook, or the caller may pass an explicit
    workflow config path for workbooks that originated elsewhere.
    """
    if explicit_workflow_config is not None:
        candidate = os.path.abspath(explicit_workflow_config)
        if not os.path.isfile(candidate):
            raise EvalError(f"Workflow config file not found: {candidate!r}.")
        return candidate

    stem = os.path.splitext(os.path.basename(workbook_path))[0]
    if stem.endswith(".workbook"):
        stem = stem[: -len(".workbook")]
    candidate = os.path.join(
        os.path.dirname(os.path.abspath(workbook_path)),
        f"{stem}.workflow.generated.json",
    )
    if os.path.isfile(candidate):
        return candidate
    raise EvalError(
        f"Cannot continue: no generated workflow config found at {candidate!r}. "
        "Run `lightassay quickstart` first so continue inherits an execution shape, "
        "or pass --workflow-config explicitly."
    )


# ── Stage reporters for CLI ──────────────────────────────────────────────────


class TerminalReporter:
    """Simple in-place terminal stage reporter.

    Writes one line per stage state change.  Silenced automatically when
    stdout is not a TTY by the CLI caller (no auto-detection here — the
    caller decides whether to pass this reporter).
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def __call__(self, stage: str, status: str, detail: str) -> None:
        marker = {
            "in_progress": "…",
            "done": "✓",
            "failed": "✗",
        }.get(status, "?")
        suffix = f" — {detail}" if detail else ""
        self._stream.write(f"[{marker}] {stage}{suffix}\n")
        self._stream.flush()
