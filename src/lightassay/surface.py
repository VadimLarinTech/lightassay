"""L1 public control surface for lightassay.

This module implements the ordinary library-first entrypoint for agents
and applications.  The surface is narrow by design: ``init_workbook``,
``open_session``, ``compare_runs``, and the ``EvalSession`` methods
(``prepare``, ``state``, ``release``, ``can_run``, ``why_not``, ``run``,
``analyze``, ``compare``, ``open_diagnostics``).

``compare_runs`` is a pre-session convenience (like ``init_workbook``)
because compare semantically operates across runs from potentially
different workbooks and does not require a session or workbook context.

All state is derived from file truth (workbook markdown, JSON artifacts,
config files).  The session object holds path references only --- no
hidden in-memory cache.

Deeper engine internals are not exposed here.  One diagnostics door
(``open_diagnostics``) is the controlled entry into the L2 layer.
The L2 handle provides structured diagnostic reports with evidence,
confidence, suggested actions, and bounded recovery options.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from importlib.machinery import PathFinder

from .diagnostics import (
    RECOVERY_ADVANCE_PREPARATION,
    DiagnosticConfidence,
    DiagnosticEvidence,
    DiagnosticReport,
    RecoveryOption,
    RecoveryResult,
)
from .errors import EvalError
from .types import (
    AnalyzeResult,
    CompareResult,
    ContinueResult,
    DiagnosticsHandle,
    EvalState,
    EvalTarget,
    ExploreResult,
    PreparationStage,
    PrepareResult,
    QuickstartResult,
    QuickTryResult,
    RefineResult,
    RunResult,
)

# ── Helpers (private) ────────────────────────────────────────────────────────


def _read_workbook(workbook_path: str):
    """Read and parse a workbook from *workbook_path*.

    Returns the parsed ``Workbook`` model.
    Raises ``EvalError`` on any file or parse failure.
    """
    from .errors import WorkbookParseError
    from .workbook_parser import parse

    if not os.path.isfile(workbook_path):
        raise EvalError(
            f"Workbook file not found: {workbook_path!r}",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook file not found.",
                evidence=[
                    DiagnosticEvidence(
                        field="workbook_path",
                        observed=workbook_path,
                        expected="path to an existing workbook markdown file",
                    )
                ],
                suggested_actions=[
                    "Check the workbook path. Use init_workbook() to create "
                    "a new workbook if needed.",
                ],
            ),
        )

    try:
        with open(workbook_path, encoding="utf-8") as fh:
            text = fh.read()
        return parse(text)
    except WorkbookParseError as exc:
        raise EvalError(
            f"Workbook parse failed: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook file failed to parse.",
                evidence=[
                    DiagnosticEvidence(
                        field="workbook_path",
                        observed=workbook_path,
                    ),
                    DiagnosticEvidence(
                        field="parse_error",
                        observed=str(exc),
                        expected="valid workbook markdown grammar",
                    ),
                ],
                suggested_actions=[
                    "Check the workbook file for syntax errors. "
                    "The workbook must follow the expected markdown grammar.",
                ],
            ),
        ) from exc


def _save_workbook(workbook, workbook_path: str) -> None:
    """Render *workbook* to canonical markdown and write to *workbook_path*."""
    from .workbook_renderer import render

    text = render(workbook)
    with open(workbook_path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _brief_has_content(brief: str) -> bool:
    from .workbook_renderer import brief_has_user_content

    return brief_has_user_content(brief)


def _target_has_content(target) -> bool:
    return (
        bool(target.kind.strip())
        and bool(target.name.strip())
        and bool(target.locator.strip())
        and bool(target.boundary.strip())
    )


def _planning_readiness_issues(workbook) -> list[str]:
    issues: list[str] = []

    if not _target_has_content(workbook.target):
        issues.append(
            "Target is incomplete. Fill TARGET_KIND, TARGET_NAME, "
            "TARGET_LOCATOR, and TARGET_BOUNDARY."
        )

    if not workbook.target.sources:
        issues.append("Target has no source references. Add at least one entry to TARGET_SOURCES.")

    if not _brief_has_content(workbook.brief):
        issues.append("Workbook brief has no user content.")

    return issues


def _determine_preparation_stage(workbook) -> PreparationStage:
    """Derive the preparation stage from workbook file truth.

    The logic mirrors the strict single-pass preparation protocol:

    * Artifact references set → already past preparation (``PREPARED``).
    * ``RUN_READY: yes`` → workbook-side preparation complete (``PREPARED``).
    * Cases exist → next lawful step is reconcile readiness.
    * Directions exist → next lawful step is generate cases.
    * Otherwise → next lawful step is generate directions.
    """
    refs = workbook.artifact_references
    if refs.run is not None or refs.analysis is not None or refs.compare is not None:
        return PreparationStage.PREPARED

    if workbook.run_readiness.run_ready:
        return PreparationStage.PREPARED

    if workbook.cases:
        return PreparationStage.NEEDS_READINESS

    if workbook.directions:
        return PreparationStage.NEEDS_CASES

    return PreparationStage.NEEDS_DIRECTIONS


def _build_eval_state(
    workbook,
    workbook_path: str,
    workflow_config_path: str | None,
) -> EvalState:
    planning_ready = len(_planning_readiness_issues(workbook)) == 0
    execution_binding_ready = _validate_workflow_config(workflow_config_path) is None
    workbook_run_ready = workbook.run_readiness.run_ready
    run_ready = (
        planning_ready
        and workbook_run_ready
        and bool(workbook.cases)
        and execution_binding_ready
    )

    return EvalState(
        workbook_path=workbook_path,
        preparation_stage=_determine_preparation_stage(workbook),
        has_target_content=_target_has_content(workbook.target),
        source_reference_count=len(workbook.target.sources),
        has_brief_content=_brief_has_content(workbook.brief),
        planning_ready=planning_ready,
        execution_binding_ready=execution_binding_ready,
        direction_count=len(workbook.directions),
        case_count=len(workbook.cases),
        workbook_run_ready=workbook_run_ready,
        run_ready=run_ready,
        run_artifact=workbook.artifact_references.run,
        analysis_artifact=workbook.artifact_references.analysis,
        compare_artifact=workbook.artifact_references.compare,
    )


def _validate_workflow_config(path: str | None) -> str | None:
    """Validate workflow config path: file existence, parseability, and
    structural viability of the adapter/driver target.

    Returns ``None`` if the config is valid and the target is structurally
    viable, or a human-readable reason string if validation fails.
    Returns a reason when *path* is ``None`` as well.

    Structural viability checks are local and deterministic — no network
    probes or runtime reachability tests.
    """
    if path is None:
        return "No workflow_config provided. Pass workflow_config when opening the session."

    if not os.path.isfile(path):
        return f"Workflow config file not found: {path!r}."

    from .errors import WorkflowConfigError
    from .workflow_config import load_workflow_config

    try:
        config = load_workflow_config(path)
    except WorkflowConfigError as exc:
        return f"Workflow config invalid: {exc}"

    # Check structural viability of the adapter/driver target.
    viability_reason = _check_structural_viability(config)
    if viability_reason is not None:
        return viability_reason

    return None


def _check_structural_viability(config) -> str | None:
    """Check structural viability of the adapter/driver target.

    Returns ``None`` if the target is structurally viable for execution,
    or a human-readable reason string explaining why it is not.

    All checks are local and deterministic.  No network probes or
    runtime reachability tests.  For ``http`` drivers, only URL
    structure (scheme + host) is validated.
    """
    if config.adapter is not None:
        return _check_legacy_adapter_viability(config.adapter)
    if config.driver is not None:
        return _check_driver_viability(config.driver)
    return None


def _check_legacy_adapter_viability(adapter_path: str) -> str | None:
    """Check that a legacy adapter executable exists and is executable."""
    if not os.path.exists(adapter_path):
        return f"Adapter not found: {adapter_path!r}."
    if not os.access(adapter_path, os.X_OK):
        return f"Adapter not executable: {adapter_path!r}."
    return None


def _check_driver_viability(driver_config) -> str | None:
    """Dispatch structural viability check by driver type."""
    from .adapter_pack import (
        CommandDriverConfig,
        HttpDriverConfig,
        PythonCallableDriverConfig,
    )

    if isinstance(driver_config, PythonCallableDriverConfig):
        return _check_python_callable_viability(driver_config)
    if isinstance(driver_config, HttpDriverConfig):
        return _check_http_viability(driver_config)
    if isinstance(driver_config, CommandDriverConfig):
        return _check_command_viability(driver_config)

    return f"Unknown driver config type: {type(driver_config).__name__}."


def _check_python_callable_viability(config) -> str | None:
    """Check python-callable viability without importing target code.

    ``state()`` / ``can_run()`` / ``why_not()`` must remain dry, so this
    check validates the static identifier shape, verifies that the module
    can be found on the import path without importing it, and, when source
    code is available, performs a best-effort static check that the module
    binds the requested name somewhere at top level. Final importability
    and callability are still enforced later in the real run path.
    """
    identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    parts = config.module.split(".")
    if not parts or any(not part or not identifier.match(part) for part in parts):
        return f"python-callable driver: module {config.module!r} is not a valid dotted module path."
    if not identifier.match(config.function):
        return (
            "python-callable driver: function name "
            f"{config.function!r} is not a valid Python identifier."
        )
    module_spec = _find_module_spec_without_import(config.module)
    if module_spec is None:
        return f"python-callable driver: module {config.module!r} not found on the import path."

    static_name_check = _check_python_callable_name_from_source(
        module_name=config.module,
        function_name=config.function,
        module_spec=module_spec,
    )
    if static_name_check is not None:
        return static_name_check
    return None


def _find_module_spec_without_import(module_name: str):
    """Resolve *module_name* through ``PathFinder`` without importing it."""
    search_path = list(sys.path)
    spec = None
    parts = module_name.split(".")
    for index in range(len(parts)):
        fullname = ".".join(parts[: index + 1])
        spec = PathFinder.find_spec(fullname, search_path)
        if spec is None:
            return None
        if index < len(parts) - 1:
            search_locations = spec.submodule_search_locations
            if search_locations is None:
                return None
            search_path = list(search_locations)
    return spec


def _check_python_callable_name_from_source(
    *,
    module_name: str,
    function_name: str,
    module_spec,
) -> str | None:
    """Best-effort static validation that *function_name* is bound in source.

    When loader/source information is unavailable we stay permissive and
    defer the final callable check to the real run path.
    """
    loader = getattr(module_spec, "loader", None)
    if loader is None or not hasattr(loader, "get_source"):
        return None
    try:
        source = loader.get_source(module_name)
    except (ImportError, OSError):
        return None
    if not source:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    if _module_binds_name_statically(tree, function_name):
        return None
    return (
        "python-callable driver: module "
        f"{module_name!r} does not statically bind {function_name!r}."
    )


def _module_binds_name_statically(tree: ast.AST, name: str) -> bool:
    """Return whether module source binds *name* at top level.

    This is intentionally a best-effort dry check; dynamic module-level
    exports remain the responsibility of the actual run path.
    """
    if not isinstance(tree, ast.Module):
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return True
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".")[0]
                if bound_name == name:
                    return True
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound_name = alias.asname or alias.name
                if bound_name == name:
                    return True
            continue
        if isinstance(node, ast.Assign):
            if any(_target_binds_name(target, name) for target in node.targets):
                return True
            continue
        if isinstance(node, ast.AnnAssign):
            if _target_binds_name(node.target, name):
                return True
            continue
        if isinstance(node, ast.AugAssign):
            if _target_binds_name(node.target, name):
                return True
            continue
    return False


def _target_binds_name(target: ast.AST, name: str) -> bool:
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_binds_name(item, name) for item in target.elts)
    return False


def _check_http_viability(config) -> str | None:
    """Check that the http driver URL has a valid structure (scheme + host).

    No runtime reachability check — only structural URL validity.
    """
    safe_url = _redact_url_for_message(config.url)
    from urllib.parse import urlparse

    parsed = urlparse(config.url)
    if not parsed.scheme:
        return f"http driver: URL {safe_url!r} has no scheme."
    if not parsed.netloc:
        return f"http driver: URL {safe_url!r} has no host."
    return None


def _redact_url_for_message(url: str) -> str:
    """Return a URL form safe for human-facing diagnostics."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    scheme = parsed.scheme or "<missing-scheme>"
    host = parsed.hostname or "<missing-host>"
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port is not None else ""
    suffix = "/..." if (parsed.path or parsed.params or parsed.query or parsed.fragment) else ""
    return f"{scheme}://{host}{port}{suffix}"


def _check_command_viability(config) -> str | None:
    """Check that the command driver's command[0] is findable.

    Relative command paths are resolved against the effective command
    working directory. When ``working_dir`` is set, it wins; otherwise
    ``config_dir`` preserves the older config-origin behavior. This keeps
    structural viability aligned with runtime execution semantics.
    """
    import shutil

    cmd = config.command[0]

    # Always check PATH first — works regardless of cwd.
    if shutil.which(cmd) is not None:
        return None

    # Resolve against the effective command cwd when available, otherwise
    # against config_dir for older in-memory configs, otherwise check raw path.
    command_root = config.working_dir or config.config_dir
    if command_root is not None:
        resolved = os.path.normpath(os.path.join(command_root, cmd))
    else:
        resolved = cmd

    if os.path.exists(resolved) and os.access(resolved, os.X_OK):
        return None

    return f"command driver: command {cmd!r} not found."


def _collect_issues(workbook, workbook_path: str, workflow_config_path: str | None) -> list[str]:
    """Collect known issues from workbook/config state for diagnostics."""
    issues: list[str] = []

    issues.extend(_planning_readiness_issues(workbook))

    stage = _determine_preparation_stage(workbook)
    if stage != PreparationStage.PREPARED:
        issues.append(f"Preparation incomplete: stage is {stage.value}.")

    if workbook.run_readiness.run_ready and not workbook.cases:
        issues.append("Workbook marked RUN_READY but has no cases.")

    if workbook.run_readiness.readiness_note and not workbook.run_readiness.run_ready:
        issues.append(f"Readiness note (not ready): {workbook.run_readiness.readiness_note}")

    config_reason = _validate_workflow_config(workflow_config_path)
    if config_reason is not None:
        issues.append(config_reason)

    return issues


def _build_diagnostic_reports(
    workbook,
    workbook_path: str,
    workflow_config_path: str | None,
    preparation_config_path: str | None,
) -> list[DiagnosticReport]:
    """Build structured L2 diagnostic reports from current file truth.

    Each detected issue becomes a ``DiagnosticReport`` with evidence,
    confidence, suggested actions, and bounded recovery options.
    """
    reports: list[DiagnosticReport] = []

    # ── Brief empty ─────────────────────────────────────────────────────
    planning_issues = _planning_readiness_issues(workbook)
    if not _target_has_content(workbook.target):
        reports.append(
            DiagnosticReport(
                diagnosis="Workbook target is incomplete.",
                confidence=DiagnosticConfidence.HIGH,
                evidence=[
                    DiagnosticEvidence(
                        field="target",
                        observed="missing required target fields",
                        expected="TARGET_KIND, TARGET_NAME, TARGET_LOCATOR, and TARGET_BOUNDARY filled",  # noqa: E501
                    ),
                ],
                suggested_actions=[
                    "Fill the target block before preparing directions and cases.",
                ],
                recovery_options=[],
            )
        )

    if not workbook.target.sources:
        reports.append(
            DiagnosticReport(
                diagnosis="Workbook target has no source references.",
                confidence=DiagnosticConfidence.HIGH,
                evidence=[
                    DiagnosticEvidence(
                        field="target.sources",
                        observed="0 source references",
                        expected=">=1 source reference",
                    ),
                ],
                suggested_actions=[
                    "Add at least one source reference in TARGET_SOURCES before planning.",
                ],
                recovery_options=[],
            )
        )

    if not _brief_has_content(workbook.brief):
        reports.append(
            DiagnosticReport(
                diagnosis="Workbook brief has no user content.",
                confidence=DiagnosticConfidence.HIGH,
                evidence=[
                    DiagnosticEvidence(
                        field="brief",
                        observed="empty or template-only",
                        expected="user-authored testing intention",
                    ),
                ],
                suggested_actions=[
                    "Fill the guided brief template in the workbook with "
                    "a concrete testing intention before preparing.",
                ],
                recovery_options=[],
            )
        )

    # ── Preparation incomplete ──────────────────────────────────────────
    stage = _determine_preparation_stage(workbook)
    if stage != PreparationStage.PREPARED:
        # Determine recovery availability for advance_preparation.
        prep_available = False
        prep_unavailable_reason: str | None = None

        if planning_issues:
            prep_unavailable_reason = "Cannot advance preparation: " + " ".join(planning_issues)
        elif preparation_config_path is None:
            prep_unavailable_reason = (
                "No preparation_config provided. Pass preparation_config when opening the session."
            )
        else:
            # Validate preparation config is loadable.
            try:
                from .errors import PreparationConfigError
                from .preparation_config import load_preparation_config

                load_preparation_config(preparation_config_path)
                prep_available = True
            except PreparationConfigError as exc:
                prep_unavailable_reason = f"Preparation config invalid: {exc}"
            except Exception as exc:
                prep_unavailable_reason = f"Preparation config error: {exc}"

        reports.append(
            DiagnosticReport(
                diagnosis=f"Preparation incomplete: stage is {stage.value}.",
                confidence=DiagnosticConfidence.HIGH,
                evidence=[
                    DiagnosticEvidence(
                        field="preparation_stage",
                        observed=stage.value,
                        expected=PreparationStage.PREPARED.value,
                    ),
                ],
                suggested_actions=[
                    f"Call prepare() to advance from {stage.value} to the next stage.",
                ],
                recovery_options=[
                    RecoveryOption(
                        action_id=RECOVERY_ADVANCE_PREPARATION,
                        description=(
                            f"Advance preparation from {stage.value} to the next lawful stage."
                        ),
                        available=prep_available,
                        unavailable_reason=prep_unavailable_reason,
                    ),
                ],
            )
        )

    # ── Readiness inconsistency ─────────────────────────────────────────
    if workbook.run_readiness.run_ready and not workbook.cases:
        reports.append(
            DiagnosticReport(
                diagnosis="Workbook marked RUN_READY but has no cases.",
                confidence=DiagnosticConfidence.HIGH,
                evidence=[
                    DiagnosticEvidence(
                        field="run_readiness.run_ready",
                        observed="True",
                        expected="True only when cases exist",
                    ),
                    DiagnosticEvidence(
                        field="case_count",
                        observed="0",
                        expected=">0",
                    ),
                ],
                suggested_actions=[
                    "This is an inconsistent workbook state. "
                    "Re-run preparation or manually add cases before running.",
                ],
                recovery_options=[],
            )
        )

    # ── Readiness note (not ready) ──────────────────────────────────────
    if workbook.run_readiness.readiness_note and not workbook.run_readiness.run_ready:
        reports.append(
            DiagnosticReport(
                diagnosis="Workbook is not run-ready with a readiness note.",
                confidence=DiagnosticConfidence.MEDIUM,
                evidence=[
                    DiagnosticEvidence(
                        field="run_readiness.run_ready",
                        observed="False",
                        expected="True",
                    ),
                    DiagnosticEvidence(
                        field="run_readiness.readiness_note",
                        observed=workbook.run_readiness.readiness_note,
                    ),
                ],
                suggested_actions=[
                    "Review the readiness note and address the issue, "
                    "then re-run preparation readiness reconciliation.",
                ],
                recovery_options=[],
            )
        )

    # ── Workflow config problems ────────────────────────────────────────
    config_reason = _validate_workflow_config(workflow_config_path)
    if config_reason is not None:
        reports.append(
            DiagnosticReport(
                diagnosis="Workflow config issue prevents run execution.",
                confidence=DiagnosticConfidence.HIGH,
                evidence=[
                    DiagnosticEvidence(
                        field="workflow_config",
                        observed=config_reason,
                        expected="valid workflow config file",
                    ),
                ],
                suggested_actions=[
                    "Provide a valid workflow_config path when opening the session.",
                ],
                recovery_options=[],
            )
        )

    return reports


def _build_reactive_report(
    diagnosis: str,
    evidence: list[DiagnosticEvidence],
    suggested_actions: list[str],
    confidence: DiagnosticConfidence = DiagnosticConfidence.HIGH,
    recovery_options: list[RecoveryOption] | None = None,
) -> DiagnosticReport:
    """Convenience builder for a single reactive diagnostic report."""
    return DiagnosticReport(
        diagnosis=diagnosis,
        confidence=confidence,
        evidence=evidence,
        suggested_actions=suggested_actions,
        recovery_options=recovery_options or [],
    )


def _to_internal_target(target: EvalTarget):
    from .workbook_models import Target

    return Target(
        kind=target.kind,
        name=target.name,
        locator=target.locator,
        boundary=target.boundary,
        sources=list(target.sources),
        notes=target.notes,
    )


def _render_quick_try_brief(target: EvalTarget, user_request: str, assumptions: list[str]) -> str:
    assumption_lines = "\n".join(f"- {item}" for item in assumptions)
    return (
        "What is being tested:\n"
        f"Quick try for {target.name}.\n"
        f"User request: {user_request}\n\n"
        "What matters in the output:\n"
        "Generate one representative direction and one representative case.\n\n"
        "Aspects that are especially significant:\n"
        "Primary: preserve the user's request as the highest-priority planning input.\n\n"
        "Failure modes and problem classes that matter:\n"
        "Do not lose the user's request while grounding planning in code and prompts.\n\n"
        "What must not break:\n"
        "The quick try must use the same workbook model as the full flow.\n\n"
        "Additional context (optional):\n"
        f"{assumption_lines}\n"
    )


def _execute_quick_try_preparation(
    *,
    workbook,
    workbook_path: str,
    target: EvalTarget,
    user_request: str,
    preparation_config: str,
) -> QuickTryResult:
    assumptions = [
        "Quick try is intentionally limited to one direction and one case.",
        "This workbook uses the same Target, Brief, Directions, Cases, and Run readiness sections as the full flow.",  # noqa: E501
        "Execution binding is not created by quick try; it only prepares a planning artifact.",
    ]
    workbook.brief = _render_quick_try_brief(target, user_request.strip(), assumptions)

    from .errors import PreparationConfigError, PreparationError
    from .preparation_config import load_preparation_config
    from .preparer import (
        execute_generate_cases,
        execute_generate_directions,
        execute_reconcile_readiness,
    )

    try:
        config = load_preparation_config(preparation_config)
        workbook = execute_generate_directions(
            workbook,
            config,
            source_root=os.getcwd(),
            planning_mode="quick_try",
        )
        workbook = execute_generate_cases(
            workbook,
            config,
            source_root=os.getcwd(),
            planning_mode="quick_try",
        )
        workbook = execute_reconcile_readiness(
            workbook,
            config,
            source_root=os.getcwd(),
            planning_mode="quick_try",
        )
    except PreparationConfigError as exc:
        raise EvalError(
            f"Preparation config invalid: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Preparation config is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="preparation_config",
                        observed=str(exc),
                        expected="valid preparation config",
                    )
                ],
                suggested_actions=[
                    "Fix the preparation config file and retry quick try.",
                ],
            ),
        ) from exc
    except PreparationError as exc:
        raise EvalError(
            f"Quick try failed: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Quick try preparation failed.",
                evidence=[
                    DiagnosticEvidence(
                        field="error",
                        observed=str(exc),
                        expected="successful bounded quick try preparation",
                    )
                ],
                suggested_actions=[
                    "Fix the target, source references, or preparation adapter and retry quick try.",  # noqa: E501
                ],
            ),
        ) from exc

    _save_workbook(workbook, workbook_path)
    state = _build_eval_state(workbook, workbook_path, workflow_config_path=None)
    return QuickTryResult(
        workbook_path=workbook_path,
        assumptions=assumptions,
        state=state,
    )


def _validate_quick_try_workbook_seed(workbook) -> None:
    if not _target_has_content(workbook.target):
        raise EvalError(
            "Quick try from workbook requires a complete Target block.",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook target is incomplete for quick try.",
                evidence=[
                    DiagnosticEvidence(
                        field="target",
                        observed="incomplete or empty",
                        expected="filled TARGET_KIND, TARGET_NAME, TARGET_LOCATOR, and TARGET_BOUNDARY",  # noqa: E501
                    )
                ],
                suggested_actions=[
                    "Fill the Target block in the workbook before running quick try.",
                ],
            ),
        )

    if not workbook.target.sources:
        raise EvalError(
            "Quick try from workbook requires at least one TARGET_SOURCES entry.",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook target has no source references.",
                evidence=[
                    DiagnosticEvidence(
                        field="target.sources",
                        observed="[]",
                        expected="at least one source reference",
                    )
                ],
                suggested_actions=[
                    "Add at least one TARGET_SOURCES entry before running quick try.",
                ],
            ),
        )

    if _brief_has_content(workbook.brief):
        raise EvalError(
            "Quick try from workbook requires a fresh workbook with an untouched brief template.",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook brief already has user-authored content.",
                evidence=[
                    DiagnosticEvidence(
                        field="brief",
                        observed="already contains user content",
                        expected="empty/template brief for quick try bootstrap",
                    )
                ],
                suggested_actions=[
                    "Use a fresh workbook for quick try or continue with the normal planning flow.",
                ],
            ),
        )

    if workbook.directions or workbook.cases or workbook.run_readiness.run_ready:
        raise EvalError(
            "Quick try from workbook requires a fresh workbook with no directions, cases, or RUN_READY state.",  # noqa: E501
            diagnostics=_build_reactive_report(
                diagnosis="Workbook already moved past the quick-try starting point.",
                evidence=[
                    DiagnosticEvidence(
                        field="workbook_state",
                        observed=(
                            f"directions={len(workbook.directions)}, "
                            f"cases={len(workbook.cases)}, "
                            f"run_ready={'yes' if workbook.run_readiness.run_ready else 'no'}"
                        ),
                        expected="directions=0, cases=0, run_ready=no",
                    )
                ],
                suggested_actions=[
                    "Use a fresh workbook for quick try or continue with normal preparation.",
                ],
            ),
        )

    refs = workbook.artifact_references
    if refs.run is not None or refs.analysis is not None or refs.compare is not None:
        raise EvalError(
            "Quick try from workbook requires no existing artifact references.",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook already has downstream artifact references.",
                evidence=[
                    DiagnosticEvidence(
                        field="artifact_references",
                        observed=f"run={refs.run!r}, analysis={refs.analysis!r}, compare={refs.compare!r}",  # noqa: E501
                        expected="all references unset",
                    )
                ],
                suggested_actions=[
                    "Use a fresh workbook for quick try or continue with normal planning instead.",
                ],
            ),
        )


def _render_refinement_brief(
    source_workbook,
    refinement_request: str,
) -> str:
    return (
        f"{source_workbook.brief.rstrip()}\n\n"
        "Refinement context from existing suite:\n"
        f"Refinement request: {refinement_request}\n"
    )


def _render_exploration_brief(
    source_workbook,
    exploration_goal: str,
    max_cases: int,
    max_iterations: int,
    run_artifact,
) -> str:
    failed_cases = [case for case in run_artifact.cases if case.status != "completed"]
    completed_cases = [case for case in run_artifact.cases if case.status == "completed"]

    failed_lines = (
        "\n".join(
            f"- {case.case_id}: status={case.status}; error={case.execution_error or 'n/a'}"
            for case in failed_cases
        )
        or "- No failed cases observed."
    )
    completed_lines = (
        "\n".join(
            f"- {case.case_id}: expected={case.expected_behavior}; response={case.raw_response!r}"
            for case in completed_cases
        )
        or "- No completed cases observed."
    )

    return (
        f"{source_workbook.brief.rstrip()}\n\n"
        "Exploratory investigation context:\n"
        f"Exploration goal: {exploration_goal}\n"
        f"Bounded iteration budget: {max_iterations}\n"
        f"Bounded case budget: {max_cases}\n"
        f"Seed run_id: {run_artifact.run_id}\n\n"
        "Observed failed cases:\n"
        f"{failed_lines}\n\n"
        "Observed completed cases:\n"
        f"{completed_lines}\n"
    )


def _render_exploration_iteration_trace(iteration_trace: list[dict]) -> str:
    if not iteration_trace:
        return ""

    lines = [
        "",
        "Exploration iteration trace:",
        "",
    ]
    for item in iteration_trace:
        lines.append(
            f"- Iteration {item['iteration_index']}: "
            f"directions={', '.join(item['direction_ids'])}; "
            f"cases={', '.join(item['case_ids'])}; "
            f"run_id={item['run_id']}; "
            f"run_status={item['run_status']}; "
            f"failed_case_count={item['failed_case_count']}; "
            f"run_artifact={item['run_artifact_path']}; "
            f"run_ready={'yes' if item['run_ready'] else 'no'}; "
            f"readiness_note={item['readiness_note']}"
        )
    return "\n".join(lines)


# ── Public: init_workbook ────────────────────────────────────────────────────


def init_workbook(name: str, output_dir: str = ".") -> str:
    """Create a new workbook file and return its path.

    This is a pre-session convenience.  It creates a skeleton workbook
    with the guided brief template, suitable for passing to
    ``open_session``.

    *name* must match ``[A-Za-z0-9_-]+``.

    Returns the absolute path to the created workbook file.
    Raises ``EvalError`` if the name is invalid, the output directory
    does not exist, or the file already exists.
    """
    import re

    from .workbook_renderer import render_init_workbook

    if not re.match(r"^[A-Za-z0-9_-]+$", name):
        raise EvalError(
            f"Workbook name {name!r} is invalid. "
            "Name must contain only ASCII letters, digits, hyphens, "
            "and underscores.",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook name is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="name",
                        observed=repr(name),
                        expected="string matching [A-Za-z0-9_-]+",
                    )
                ],
                suggested_actions=[
                    "Use only ASCII letters, digits, hyphens, and underscores "
                    "in the workbook name.",
                ],
            ),
        )

    if not os.path.isdir(output_dir):
        raise EvalError(
            f"Output directory does not exist: {output_dir!r}",
            diagnostics=_build_reactive_report(
                diagnosis="Output directory does not exist.",
                evidence=[
                    DiagnosticEvidence(
                        field="output_dir",
                        observed=output_dir,
                        expected="path to an existing directory",
                    )
                ],
                suggested_actions=[
                    "Create the output directory or pass an existing one.",
                ],
            ),
        )

    filename = f"{name}.workbook.md"
    path = os.path.join(output_dir, filename)
    abs_path = os.path.abspath(path)

    if os.path.exists(abs_path):
        raise EvalError(
            f"Workbook file already exists: {abs_path!r}. "
            "Choose a different name or remove the existing file.",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook file already exists.",
                evidence=[
                    DiagnosticEvidence(
                        field="workbook_path",
                        observed=abs_path,
                        expected="path that does not already exist",
                    )
                ],
                suggested_actions=[
                    "Choose a different name or remove the existing file.",
                ],
            ),
        )

    content = render_init_workbook(name)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    return abs_path


def quick_try(
    name: str,
    *,
    target: EvalTarget,
    user_request: str,
    preparation_config: str,
    output_dir: str = ".",
) -> QuickTryResult:
    """Run a minimal target-first planning flow and return a prepared workbook.

    Quick try is a bridge into the full system: it creates a real workbook,
    fills the target block, writes a minimal brief with explicit assumptions,
    and executes one bounded preparation pass that yields exactly one
    representative direction and one representative case.
    """
    if not isinstance(user_request, str) or not user_request.strip():
        raise EvalError(
            "Quick try requires a non-empty user_request.",
            diagnostics=_build_reactive_report(
                diagnosis="Quick try user request is missing.",
                evidence=[
                    DiagnosticEvidence(
                        field="user_request",
                        observed=repr(user_request),
                        expected="non-empty natural-language request",
                    )
                ],
                suggested_actions=[
                    "Provide a concrete user request describing what should be evaluated.",
                ],
            ),
        )

    workbook_path = init_workbook(name, output_dir=output_dir)
    workbook = _read_workbook(workbook_path)
    workbook.target = _to_internal_target(target)
    return _execute_quick_try_preparation(
        workbook=workbook,
        workbook_path=workbook_path,
        target=target,
        user_request=user_request,
        preparation_config=preparation_config,
    )


def quick_try_workbook(
    workbook_path: str,
    *,
    user_request: str,
    preparation_config: str,
) -> QuickTryResult:
    """Run quick try in-place on an existing canonical start workbook."""
    workbook = _read_workbook(workbook_path)
    _validate_quick_try_workbook_seed(workbook)
    target = EvalTarget(
        kind=workbook.target.kind,
        name=workbook.target.name,
        locator=workbook.target.locator,
        boundary=workbook.target.boundary,
        sources=list(workbook.target.sources),
        notes=workbook.target.notes,
    )
    return _execute_quick_try_preparation(
        workbook=workbook,
        workbook_path=workbook_path,
        target=target,
        user_request=user_request,
        preparation_config=preparation_config,
    )


def refine_workbook(
    source_workbook_path: str,
    *,
    name: str,
    refinement_request: str,
    output_dir: str = ".",
) -> RefineResult:
    """Create a new planning workbook from an existing suite.

    This is the first-class continue/refine path. It preserves the target and
    the current suite structure, resets downstream artifact state, and routes
    the refinement request into explicit global instructions so the next
    preparation step can refine existing material instead of forcing a restart.
    """
    if not isinstance(refinement_request, str) or not refinement_request.strip():
        raise EvalError(
            "Refinement requires a non-empty refinement_request.",
            diagnostics=_build_reactive_report(
                diagnosis="Refinement request is missing.",
                evidence=[
                    DiagnosticEvidence(
                        field="refinement_request",
                        observed=repr(refinement_request),
                        expected="non-empty natural-language refinement request",
                    )
                ],
                suggested_actions=[
                    "Provide a concrete refinement request describing what should change or be explored next.",  # noqa: E501
                ],
            ),
        )

    source_workbook = _read_workbook(source_workbook_path)
    workbook_path = init_workbook(name, output_dir=output_dir)
    workbook = _read_workbook(workbook_path)

    from .workbook_models import ArtifactReferences, HumanFeedback, RunReadiness

    workbook.target = source_workbook.target
    workbook.brief = _render_refinement_brief(
        source_workbook,
        refinement_request=refinement_request.strip(),
    )
    workbook.directions_global_instruction = HumanFeedback(refinement_request.strip())
    workbook.directions = source_workbook.directions
    workbook.cases_global_instruction = HumanFeedback(refinement_request.strip())
    workbook.cases = source_workbook.cases
    workbook.run_readiness = RunReadiness(run_ready=False, readiness_note="")
    workbook.artifact_references = ArtifactReferences(run=None, analysis=None, compare=None)

    _save_workbook(workbook, workbook_path)
    state = _build_eval_state(workbook, workbook_path, workflow_config_path=None)
    return RefineResult(
        workbook_path=workbook_path,
        inherited_direction_count=len(source_workbook.directions),
        inherited_case_count=len(source_workbook.cases),
        state=state,
    )


def explore_workbook(
    source_workbook_path: str,
    *,
    run_artifact_path: str,
    workflow_config: str,
    name: str,
    exploration_goal: str,
    preparation_config: str,
    max_cases: int,
    max_iterations: int = 1,
    output_dir: str = ".",
) -> ExploreResult:
    """Run a bounded multi-iteration exploratory planning flow from an existing run."""
    if not isinstance(exploration_goal, str) or not exploration_goal.strip():
        raise EvalError(
            "Exploration requires a non-empty exploration_goal.",
            diagnostics=_build_reactive_report(
                diagnosis="Exploration goal is missing.",
                evidence=[
                    DiagnosticEvidence(
                        field="exploration_goal",
                        observed=repr(exploration_goal),
                        expected="non-empty natural-language exploration goal",
                    )
                ],
                suggested_actions=[
                    "Provide a concrete exploration goal before starting exploratory mode.",
                ],
            ),
        )
    if not isinstance(max_cases, int) or isinstance(max_cases, bool) or max_cases <= 0:
        raise EvalError(
            f"Invalid max_cases: {max_cases!r}. max_cases must be a positive integer.",
            diagnostics=_build_reactive_report(
                diagnosis="Exploration case budget is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="max_cases",
                        observed=repr(max_cases),
                        expected="positive integer",
                    )
                ],
                suggested_actions=[
                    "Provide a positive max_cases budget.",
                ],
            ),
        )
    if (
        not isinstance(max_iterations, int)
        or isinstance(max_iterations, bool)
        or max_iterations <= 0
    ):
        raise EvalError(
            f"Invalid max_iterations: {max_iterations!r}. max_iterations must be a positive integer.",  # noqa: E501
            diagnostics=_build_reactive_report(
                diagnosis="Exploration iteration budget is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="max_iterations",
                        observed=repr(max_iterations),
                        expected="positive integer",
                    )
                ],
                suggested_actions=[
                    "Provide a positive max_iterations budget.",
                ],
            ),
        )

    from .errors import PreparationConfigError, PreparationError, RunError, WorkflowConfigError
    from .preparation_config import load_preparation_config
    from .preparer import (
        execute_generate_cases,
        execute_generate_directions,
        execute_reconcile_readiness,
    )
    from .run_artifact_io import load_run_artifact, save_run_artifact
    from .runner import execute_run
    from .workbook_models import ArtifactReferences, HumanFeedback, RunReadiness
    from .workflow_config import load_workflow_config

    source_workbook = _read_workbook(source_workbook_path)
    try:
        run_artifact = load_run_artifact(run_artifact_path)
    except RunError as exc:
        raise EvalError(
            f"Run artifact invalid: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Exploration seed run artifact is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="run_artifact_path",
                        observed=run_artifact_path,
                        expected="valid run artifact JSON",
                    )
                ],
                suggested_actions=[
                    "Provide a valid run artifact path for exploratory mode.",
                ],
            ),
        ) from exc

    try:
        workflow_binding = load_workflow_config(workflow_config)
    except WorkflowConfigError as exc:
        raise EvalError(
            f"Workflow config invalid: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Exploratory workflow config is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="workflow_config",
                        observed=str(exc),
                        expected="valid workflow config for exploratory execution",
                    )
                ],
                suggested_actions=[
                    "Fix the workflow config before running exploratory mode.",
                ],
            ),
        ) from exc

    workbook_path = init_workbook(name, output_dir=output_dir)
    seed_brief = _render_exploration_brief(
        source_workbook,
        exploration_goal=exploration_goal.strip(),
        max_cases=max_cases,
        max_iterations=max_iterations,
        run_artifact=run_artifact,
    )
    iteration_trace = []
    iteration_run_artifact_paths: list[str] = []
    workbook = None
    current_run_artifact = run_artifact

    try:
        config = load_preparation_config(preparation_config)
        for iteration_index in range(1, max_iterations + 1):
            workbook = _read_workbook(workbook_path)
            workbook.target = source_workbook.target
            workbook.brief = seed_brief + _render_exploration_iteration_trace(iteration_trace)
            workbook.directions_global_instruction = HumanFeedback("")
            workbook.directions = []
            workbook.cases_global_instruction = HumanFeedback("")
            workbook.cases = []
            workbook.run_readiness = RunReadiness(run_ready=False, readiness_note="")
            workbook.artifact_references = ArtifactReferences(run=None, analysis=None, compare=None)

            planning_context = {
                "exploration_goal": exploration_goal.strip(),
                "seed_run_id": current_run_artifact.run_id,
                "max_cases": max_cases,
                "max_iterations": max_iterations,
                "iteration_index": iteration_index,
                "iteration_trace": list(iteration_trace),
                "failed_cases": [
                    {
                        "case_id": case.case_id,
                        "status": case.status,
                        "execution_error": case.execution_error,
                    }
                    for case in current_run_artifact.cases
                    if case.status != "completed"
                ],
            }

            workbook = execute_generate_directions(
                workbook,
                config,
                source_root=os.getcwd(),
                planning_mode="exploratory",
                planning_context=planning_context,
            )
            workbook = execute_generate_cases(
                workbook,
                config,
                source_root=os.getcwd(),
                planning_mode="exploratory",
                planning_context=planning_context,
            )
            workbook = execute_reconcile_readiness(
                workbook,
                config,
                source_root=os.getcwd(),
                planning_mode="exploratory",
                planning_context=planning_context,
            )

            if not workbook.run_readiness.run_ready:
                raise EvalError(
                    "Exploratory iteration did not produce a RUN_READY workbook, so no new run evidence can be collected.",  # noqa: E501
                    diagnostics=_build_reactive_report(
                        diagnosis="Exploratory iteration stopped before execution.",
                        evidence=[
                            DiagnosticEvidence(
                                field="workbook.run_readiness",
                                observed=f"run_ready=no; note={workbook.run_readiness.readiness_note!r}",  # noqa: E501
                                expected="RUN_READY: yes before collecting the next iteration's evidence",  # noqa: E501
                            )
                        ],
                        suggested_actions=[
                            "Tighten the exploration goal or fix planning inputs so exploratory mode can produce a runnable follow-up suite.",  # noqa: E501
                        ],
                    ),
                )

            _save_workbook(workbook, workbook_path)
            current_run_artifact = execute_run(
                workbook,
                workbook_path,
                workflow_binding,
                workflow_config,
            )
            current_artifact_path = os.path.join(
                output_dir,
                f"explore_iter_{iteration_index:02d}_run_{current_run_artifact.run_id}.json",
            )
            save_run_artifact(current_run_artifact, current_artifact_path)
            iteration_run_artifact_paths.append(current_artifact_path)

            iteration_trace.append(
                {
                    "iteration_index": iteration_index,
                    "direction_ids": [direction.direction_id for direction in workbook.directions],
                    "case_ids": [case.case_id for case in workbook.cases],
                    "run_ready": workbook.run_readiness.run_ready,
                    "readiness_note": workbook.run_readiness.readiness_note,
                    "run_id": current_run_artifact.run_id,
                    "run_status": current_run_artifact.status,
                    "run_artifact_path": current_artifact_path,
                    "failed_case_count": len(
                        [case for case in current_run_artifact.cases if case.status != "completed"]
                    ),
                }
            )
    except PreparationConfigError as exc:
        raise EvalError(
            f"Preparation config invalid: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Preparation config is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="preparation_config",
                        observed=str(exc),
                        expected="valid preparation config",
                    )
                ],
                suggested_actions=[
                    "Fix the preparation config file and retry exploratory mode.",
                ],
            ),
        ) from exc
    except PreparationError as exc:
        raise EvalError(
            f"Exploratory planning failed: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Exploratory planning failed.",
                evidence=[
                    DiagnosticEvidence(
                        field="error",
                        observed=str(exc),
                        expected="successful bounded exploratory planning",
                    )
                ],
                suggested_actions=[
                    "Fix the target, sources, or preparation adapter and retry exploratory mode.",
                ],
            ),
        ) from exc
    except RunError as exc:
        raise EvalError(
            f"Exploratory execution failed: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Exploratory execution binding failed.",
                evidence=[
                    DiagnosticEvidence(
                        field="workflow_config",
                        observed=str(exc),
                        expected="successful execution after each exploratory iteration",
                    )
                ],
                suggested_actions=[
                    "Fix the workflow binding or choose a runnable target before using exploratory mode.",  # noqa: E501
                ],
            ),
        ) from exc

    assert workbook is not None
    workbook.brief = seed_brief + _render_exploration_iteration_trace(iteration_trace)
    workbook.artifact_references = ArtifactReferences(
        run=iteration_run_artifact_paths[-1] if iteration_run_artifact_paths else None,
        analysis=None,
        compare=None,
    )
    _save_workbook(workbook, workbook_path)
    state = _build_eval_state(workbook, workbook_path, workflow_config_path=workflow_config)
    failed_case_count = len([case for case in run_artifact.cases if case.status != "completed"])
    return ExploreResult(
        workbook_path=workbook_path,
        seeded_from_run_id=run_artifact.run_id,
        failed_case_count=failed_case_count,
        iteration_count=len(iteration_trace),
        iteration_run_artifact_paths=iteration_run_artifact_paths,
        state=state,
    )


# ── Public: compare_runs ─────────────────────────────────────────────────────


def compare_runs(
    run_artifact_paths: list[str],
    *,
    semantic_config: str,
    goal: str | None = None,
    output_dir: str = ".",
) -> CompareResult:
    """Compare two or more completed run artifacts.

    This is the shared library primitive for compare.  It does **not**
    require a session or a workbook because compare semantically operates
    across runs that may come from different workbooks.

    *semantic_config* is the path to a semantic adapter config JSON file.
    *output_dir* is the directory where the compare artifact markdown is saved.

    Returns a ``CompareResult``.
    Raises ``EvalError`` on any validation or execution failure.
    """
    if len(run_artifact_paths) < 2:
        raise EvalError(
            f"Compare requires at least 2 run artifacts, got {len(run_artifact_paths)}.",
            diagnostics=_build_reactive_report(
                diagnosis="Too few run artifacts for compare.",
                evidence=[
                    DiagnosticEvidence(
                        field="run_artifact_count",
                        observed=str(len(run_artifact_paths)),
                        expected=">=2",
                    )
                ],
                suggested_actions=[
                    "Provide at least 2 completed run artifact paths.",
                ],
            ),
        )

    for path in run_artifact_paths:
        if not os.path.isfile(path):
            raise EvalError(
                f"Run artifact file not found: {path!r}",
                diagnostics=_build_reactive_report(
                    diagnosis="Run artifact file not found.",
                    evidence=[
                        DiagnosticEvidence(
                            field="run_artifact_path",
                            observed=path,
                            expected="path to an existing run artifact JSON file",
                        )
                    ],
                    suggested_actions=[
                        "Check the path or run the workflow first.",
                    ],
                ),
            )

    if not os.path.isdir(output_dir):
        raise EvalError(
            f"Output directory does not exist: {output_dir!r}",
            diagnostics=_build_reactive_report(
                diagnosis="Output directory does not exist.",
                evidence=[
                    DiagnosticEvidence(
                        field="output_dir",
                        observed=output_dir,
                        expected="path to an existing directory",
                    )
                ],
                suggested_actions=[
                    "Create the output directory or pass an existing one.",
                ],
            ),
        )

    from .comparer import execute_compare
    from .errors import CompareError, RunError, SemanticConfigError
    from .run_artifact_io import load_run_artifact
    from .semantic_config import load_semantic_config

    # Load and validate semantic config.
    try:
        sem_config = load_semantic_config(semantic_config)
    except SemanticConfigError as exc:
        raise EvalError(
            f"Semantic config invalid: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Semantic config is invalid.",
                evidence=[
                    DiagnosticEvidence(
                        field="semantic_config",
                        observed=str(exc),
                        expected="valid semantic config",
                    )
                ],
                suggested_actions=[
                    "Fix the semantic config file and retry.",
                ],
            ),
        ) from exc

    # Load run artifacts.
    run_artifacts = []
    for path in run_artifact_paths:
        try:
            artifact = load_run_artifact(path)
        except RunError as exc:
            raise EvalError(
                f"Run artifact invalid ({path!r}): {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis="Run artifact is invalid.",
                    evidence=[
                        DiagnosticEvidence(
                            field="run_artifact_path",
                            observed=path,
                        ),
                        DiagnosticEvidence(
                            field="error",
                            observed=str(exc),
                        ),
                    ],
                    suggested_actions=[
                        "Check the run artifact JSON file for format errors.",
                    ],
                ),
            ) from exc
        run_artifacts.append(artifact)

    # Validate all runs are completed.
    for i, artifact in enumerate(run_artifacts):
        if artifact.status != "completed":
            raise EvalError(
                f"Run artifact {run_artifact_paths[i]!r} has status "
                f"{artifact.status!r}. Compare only accepts completed runs.",
                diagnostics=_build_reactive_report(
                    diagnosis="Run artifact is not completed.",
                    evidence=[
                        DiagnosticEvidence(
                            field="run_artifact.status",
                            observed=artifact.status,
                            expected="completed",
                        ),
                        DiagnosticEvidence(
                            field="run_artifact_path",
                            observed=run_artifact_paths[i],
                        ),
                    ],
                    suggested_actions=[
                        "Only completed run artifacts can be compared. "
                        "Fix the failed run and re-run, or exclude it.",
                    ],
                ),
            )

    # Execute compare.
    try:
        artifact_text, compare_id = execute_compare(
            run_artifacts, run_artifact_paths, sem_config, compare_goal=goal
        )
    except CompareError as exc:
        raise EvalError(
            f"Compare failed: {exc}",
            diagnostics=_build_reactive_report(
                diagnosis="Semantic compare execution failed.",
                evidence=[
                    DiagnosticEvidence(
                        field="error",
                        observed=str(exc),
                        expected="successful comparison",
                    )
                ],
                suggested_actions=[
                    "Check the semantic adapter and config, then retry.",
                ],
            ),
        ) from exc

    # Save compare artifact.
    artifact_filename = f"compare_{compare_id}.md"
    artifact_path = os.path.join(output_dir, artifact_filename)
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write(artifact_text)

    return CompareResult(
        artifact_path=artifact_path,
        compare_id=compare_id,
        goal=goal,
    )


# ── Public: open_session ─────────────────────────────────────────────────────


def open_session(
    workbook_path: str,
    *,
    preparation_config: str | None = None,
    workflow_config: str | None = None,
    semantic_config: str | None = None,
) -> EvalSession:
    """Open an L1 eval session bound to *workbook_path*.

    Config paths are optional at open time; each operation validates that
    the config it needs was provided and raises ``EvalError`` if not.

    Returns an ``EvalSession`` instance.
    Raises ``EvalError`` if the workbook path does not point to an
    existing file.
    """
    abs_path = os.path.abspath(workbook_path)
    if not os.path.isfile(abs_path):
        raise EvalError(
            f"Workbook file not found: {abs_path!r}",
            diagnostics=_build_reactive_report(
                diagnosis="Workbook file not found at session open.",
                evidence=[
                    DiagnosticEvidence(
                        field="workbook_path",
                        observed=abs_path,
                        expected="path to an existing workbook markdown file",
                    )
                ],
                suggested_actions=[
                    "Check the workbook path. Use init_workbook() to create "
                    "a new workbook if the file does not exist yet.",
                ],
            ),
        )

    return EvalSession(
        workbook_path=abs_path,
        preparation_config=preparation_config,
        workflow_config=workflow_config,
        semantic_config=semantic_config,
    )


# ── EvalSession ──────────────────────────────────────────────────────────────


class EvalSession:
    """L1 library control handle for one eval workbook.

    All operations read file truth on each call.  The session holds
    only path references and a ``released`` flag --- no hidden cached
    state.

    After ``release()`` every operation raises ``EvalError``.
    ``release()`` does **not** mutate workbook or artifact files.
    """

    def __init__(
        self,
        workbook_path: str,
        *,
        preparation_config: str | None = None,
        workflow_config: str | None = None,
        semantic_config: str | None = None,
    ) -> None:
        self._workbook_path = os.path.abspath(workbook_path)
        self._preparation_config = preparation_config
        self._workflow_config = workflow_config
        self._semantic_config = semantic_config
        self._released = False

    # ── guards ───────────────────────────────────────────────────────────

    def _check_released(self) -> None:
        if self._released:
            raise EvalError(
                "Session has been released.",
                diagnostics=_build_reactive_report(
                    diagnosis="Operation attempted on a released session.",
                    evidence=[
                        DiagnosticEvidence(
                            field="session.released",
                            observed="True",
                            expected="False",
                        )
                    ],
                    suggested_actions=[
                        "Open a new session with open_session() instead of "
                        "reusing a released session handle.",
                    ],
                ),
            )

    # ── state ────────────────────────────────────────────────────────────

    def state(self) -> EvalState:
        """Return a typed snapshot of workbook/artifact state.

        Every field is derived from file truth at call time.
        """
        self._check_released()
        workbook = _read_workbook(self._workbook_path)
        return _build_eval_state(
            workbook,
            self._workbook_path,
            self._workflow_config,
        )

    # ── release ──────────────────────────────────────────────────────────

    def release(self) -> None:
        """Release this session handle.

        After release every operation raises ``EvalError``.
        ``release()`` does **not** mutate workbook or artifact files
        and does **not** delete outputs.  It marks the in-memory handle
        as unusable.

        Calling ``release()`` on an already-released session raises
        ``EvalError``.
        """
        self._check_released()
        self._released = True

    @property
    def released(self) -> bool:
        return self._released

    # ── prepare ──────────────────────────────────────────────────────────

    def prepare(self) -> PrepareResult:
        """Advance exactly one lawful preparation step.

        The step is determined by current workbook file truth:

        * ``NEEDS_DIRECTIONS`` → generate directions from brief.
        * ``NEEDS_CASES`` → generate cases from directions + feedback.
        * ``NEEDS_READINESS`` → reconcile readiness.
        * ``PREPARED`` → no lawful step; raises ``EvalError``.

        Requires that ``preparation_config`` was provided at session open.

        Returns a ``PrepareResult`` with the completed stage and the
        post-operation state snapshot.
        """
        self._check_released()

        if self._preparation_config is None:
            raise EvalError(
                "Cannot prepare: no preparation_config provided. "
                "Pass preparation_config when opening the session.",
                diagnostics=_build_reactive_report(
                    diagnosis="Preparation config not provided.",
                    evidence=[
                        DiagnosticEvidence(
                            field="preparation_config",
                            observed="None",
                            expected="path to a valid preparation config file",
                        )
                    ],
                    suggested_actions=[
                        "Pass preparation_config when opening the session.",
                    ],
                ),
            )

        from .errors import PreparationConfigError, PreparationError
        from .preparation_config import load_preparation_config
        from .preparer import (
            execute_generate_cases,
            execute_generate_directions,
            execute_reconcile_readiness,
        )

        # Load preparation config.
        try:
            config = load_preparation_config(self._preparation_config)
        except PreparationConfigError as exc:
            raise EvalError(
                f"Preparation config invalid: {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis="Preparation config is invalid.",
                    evidence=[
                        DiagnosticEvidence(
                            field="preparation_config",
                            observed=str(exc),
                            expected="valid preparation config",
                        )
                    ],
                    suggested_actions=[
                        "Fix the preparation config file and retry.",
                    ],
                ),
            ) from exc

        # Read current workbook truth.
        workbook = _read_workbook(self._workbook_path)
        planning_issues = _planning_readiness_issues(workbook)
        stage = _determine_preparation_stage(workbook)

        if stage == PreparationStage.PREPARED:
            raise EvalError(
                "Cannot prepare: workbook preparation is already complete "
                "(stage is PREPARED). No lawful next preparation step exists.",
                diagnostics=_build_reactive_report(
                    diagnosis="Preparation is already complete.",
                    evidence=[
                        DiagnosticEvidence(
                            field="preparation_stage",
                            observed=PreparationStage.PREPARED.value,
                            expected="a non-PREPARED stage",
                        )
                    ],
                    suggested_actions=[
                        "No preparation step is needed. Proceed with can_run() / run().",
                    ],
                ),
            )

        if planning_issues:
            diagnosis = "Planning is not ready."
            normalized_issues = [issue.lower() for issue in planning_issues]
            if any("brief" in issue for issue in normalized_issues):
                diagnosis = "Workbook brief has no user content."
            elif any("target is incomplete" in issue for issue in normalized_issues):
                diagnosis = "Workbook target is incomplete."
            elif any("source references" in issue for issue in normalized_issues):
                diagnosis = "Workbook target has no source references."

            raise EvalError(
                "Cannot prepare: " + " ".join(planning_issues),
                diagnostics=_build_reactive_report(
                    diagnosis=diagnosis,
                    evidence=[
                        DiagnosticEvidence(
                            field="planning_ready",
                            observed="False",
                            expected="True",
                        ),
                        DiagnosticEvidence(
                            field="planning_issues",
                            observed=" | ".join(planning_issues),
                        ),
                    ],
                    suggested_actions=planning_issues,
                ),
            )

        try:
            if stage == PreparationStage.NEEDS_DIRECTIONS:
                if not _brief_has_content(workbook.brief):
                    raise EvalError(
                        "Cannot prepare: workbook brief has no user content. "
                        "Fill the guided brief template before preparing.",
                        diagnostics=_build_reactive_report(
                            diagnosis="Workbook brief is empty; cannot generate directions.",
                            evidence=[
                                DiagnosticEvidence(
                                    field="brief",
                                    observed="empty or template-only",
                                    expected="user-authored testing intention",
                                )
                            ],
                            suggested_actions=[
                                "Fill the guided brief template in the workbook.",
                            ],
                        ),
                    )
                workbook = execute_generate_directions(
                    workbook,
                    config,
                    source_root=os.getcwd(),
                )

            elif stage == PreparationStage.NEEDS_CASES:
                workbook = execute_generate_cases(
                    workbook,
                    config,
                    source_root=os.getcwd(),
                )

            elif stage == PreparationStage.NEEDS_READINESS:
                workbook = execute_reconcile_readiness(
                    workbook,
                    config,
                    source_root=os.getcwd(),
                )

        except EvalError:
            raise  # Re-raise EvalError (e.g. empty brief) as-is.
        except PreparationError as exc:
            raise EvalError(
                f"Preparation failed: {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis=f"Preparation step {stage.value} failed.",
                    evidence=[
                        DiagnosticEvidence(
                            field="preparation_stage",
                            observed=stage.value,
                        ),
                        DiagnosticEvidence(
                            field="error",
                            observed=str(exc),
                        ),
                    ],
                    suggested_actions=[
                        "Check the preparation adapter and config, then retry.",
                    ],
                ),
            ) from exc

        # Save updated workbook.
        _save_workbook(workbook, self._workbook_path)

        post_state = _build_eval_state(
            workbook,
            self._workbook_path,
            self._workflow_config,
        )
        return PrepareResult(stage_completed=stage, state=post_state)

    # ── can_run / why_not ────────────────────────────────────────────────

    def can_run(self) -> bool:
        """Check whether the workbook is ready for a run.

        Returns ``True`` only when the workbook is planning-ready,
        ``RUN_READY: yes``, cases exist, and ``workflow_config`` points
        to an existing, parseable config file.
        """
        self._check_released()
        workbook = _read_workbook(self._workbook_path)
        state = _build_eval_state(
            workbook,
            self._workbook_path,
            self._workflow_config,
        )
        return state.run_ready

    def why_not(self) -> list[str]:
        """Explain why ``run()`` cannot proceed.

        Returns a list of human-readable reason strings.  An empty list
        means ``can_run()`` would return ``True``.
        """
        self._check_released()
        workbook = _read_workbook(self._workbook_path)
        reasons: list[str] = []
        reasons.extend(_planning_readiness_issues(workbook))

        if not workbook.run_readiness.run_ready:
            reasons.append("Workbook is not run-ready (RUN_READY: no).")
            if workbook.run_readiness.readiness_note:
                reasons.append(f"READINESS_NOTE: {workbook.run_readiness.readiness_note}")

        if not workbook.cases:
            reasons.append("Workbook has no cases.")

        config_reason = _validate_workflow_config(self._workflow_config)
        if config_reason is not None:
            reasons.append(config_reason)

        return reasons

    # ── run ──────────────────────────────────────────────────────────────

    def run(self, *, output_dir: str = ".") -> RunResult:
        """Execute the workflow against approved cases.

        Requires ``workflow_config`` and a run-ready workbook with cases.
        Saves the run artifact JSON to *output_dir* and updates the
        workbook's artifact references.

        Returns a ``RunResult``.
        """
        self._check_released()

        if self._workflow_config is None:
            raise EvalError(
                "Cannot run: no workflow_config provided. "
                "Pass workflow_config when opening the session.",
                diagnostics=_build_reactive_report(
                    diagnosis="Workflow config not provided.",
                    evidence=[
                        DiagnosticEvidence(
                            field="workflow_config",
                            observed="None",
                            expected="path to a valid workflow config file",
                        )
                    ],
                    suggested_actions=[
                        "Pass workflow_config when opening the session.",
                    ],
                ),
            )

        if not os.path.isdir(output_dir):
            raise EvalError(
                f"Output directory does not exist: {output_dir!r}",
                diagnostics=_build_reactive_report(
                    diagnosis="Output directory does not exist.",
                    evidence=[
                        DiagnosticEvidence(
                            field="output_dir",
                            observed=output_dir,
                            expected="path to an existing directory",
                        )
                    ],
                    suggested_actions=[
                        "Create the output directory or pass an existing one.",
                    ],
                ),
            )

        from .errors import RunError, WorkflowConfigError
        from .run_artifact_io import save_run_artifact
        from .runner import execute_run
        from .workflow_config import load_workflow_config

        # Load config.
        try:
            config = load_workflow_config(self._workflow_config)
        except WorkflowConfigError as exc:
            raise EvalError(
                f"Workflow config invalid: {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis="Workflow config is invalid.",
                    evidence=[
                        DiagnosticEvidence(
                            field="workflow_config",
                            observed=str(exc),
                            expected="valid workflow config",
                        )
                    ],
                    suggested_actions=[
                        "Fix the workflow config file and retry.",
                    ],
                ),
            ) from exc

        # Read workbook.
        workbook = _read_workbook(self._workbook_path)
        planning_issues = _planning_readiness_issues(workbook)

        if planning_issues:
            raise EvalError(
                "Cannot run: workbook planning foundation is incomplete.",
                diagnostics=_build_reactive_report(
                    diagnosis="Workbook planning foundation is incomplete.",
                    evidence=[
                        DiagnosticEvidence(
                            field="planning_ready",
                            observed="False",
                            expected="True",
                        ),
                        *[
                            DiagnosticEvidence(
                                field="planning_issue",
                                observed=issue,
                                expected="planning foundation complete",
                            )
                            for issue in planning_issues
                        ],
                    ],
                    suggested_actions=[
                        "Fill the target fields, source references, and brief before running.",
                    ],
                ),
            )

        if not workbook.run_readiness.run_ready:
            raise EvalError(
                "Cannot run: workbook is not run-ready (RUN_READY: no).",
                diagnostics=_build_reactive_report(
                    diagnosis="Workbook is not run-ready.",
                    evidence=[
                        DiagnosticEvidence(
                            field="run_readiness.run_ready",
                            observed="False",
                            expected="True",
                        )
                    ],
                    suggested_actions=[
                        "Complete preparation (all three stages) before running.",
                    ],
                ),
            )

        if not workbook.cases:
            raise EvalError(
                "Cannot run: workbook has no cases.",
                diagnostics=_build_reactive_report(
                    diagnosis="Workbook has no cases.",
                    evidence=[
                        DiagnosticEvidence(
                            field="case_count",
                            observed="0",
                            expected=">0",
                        )
                    ],
                    suggested_actions=[
                        "Run preparation to generate cases before running.",
                    ],
                ),
            )

        # Execute.
        try:
            artifact = execute_run(workbook, self._workbook_path, config, self._workflow_config)
        except RunError as exc:
            raise EvalError(
                f"Run failed: {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis="Run execution failed.",
                    evidence=[
                        DiagnosticEvidence(
                            field="error",
                            observed=str(exc),
                            expected="successful execution",
                        )
                    ],
                    suggested_actions=[
                        "Check that the workflow execution binding exists and is runnable.",
                        "Verify the workflow config references a working adapter or driver.",
                    ],
                ),
            ) from exc

        # Save artifact.
        artifact_filename = f"run_{artifact.run_id}.json"
        artifact_path = os.path.join(output_dir, artifact_filename)
        save_run_artifact(artifact, artifact_path)

        # Update workbook artifact references.
        workbook.artifact_references.run = artifact_path
        _save_workbook(workbook, self._workbook_path)

        return RunResult(
            artifact_path=artifact_path,
            run_id=artifact.run_id,
            status=artifact.status,
            total_cases=artifact.aggregate.total_cases,
            completed_cases=artifact.aggregate.completed_cases,
            failed_cases=artifact.aggregate.failed_cases,
        )

    # ── analyze ──────────────────────────────────────────────────────────

    def analyze(self, run_artifact_path: str, *, output_dir: str = ".") -> AnalyzeResult:
        """Produce a semantic analysis of a run artifact.

        Requires ``semantic_config``.  The run artifact must reference
        the same workbook as this session.

        Saves the analysis artifact markdown to *output_dir* and
        updates the workbook's artifact references.

        Returns an ``AnalyzeResult``.
        """
        self._check_released()

        if self._semantic_config is None:
            raise EvalError(
                "Cannot analyze: no semantic_config provided. "
                "Pass semantic_config when opening the session.",
                diagnostics=_build_reactive_report(
                    diagnosis="Semantic config not provided.",
                    evidence=[
                        DiagnosticEvidence(
                            field="semantic_config",
                            observed="None",
                            expected="path to a valid semantic config file",
                        )
                    ],
                    suggested_actions=[
                        "Pass semantic_config when opening the session.",
                    ],
                ),
            )

        if not os.path.isfile(run_artifact_path):
            raise EvalError(
                f"Run artifact file not found: {run_artifact_path!r}",
                diagnostics=_build_reactive_report(
                    diagnosis="Run artifact file not found.",
                    evidence=[
                        DiagnosticEvidence(
                            field="run_artifact_path",
                            observed=run_artifact_path,
                            expected="path to an existing run artifact JSON file",
                        )
                    ],
                    suggested_actions=[
                        "Run the workflow first to produce a run artifact, or check the path.",
                    ],
                ),
            )

        if not os.path.isdir(output_dir):
            raise EvalError(
                f"Output directory does not exist: {output_dir!r}",
                diagnostics=_build_reactive_report(
                    diagnosis="Output directory does not exist.",
                    evidence=[
                        DiagnosticEvidence(
                            field="output_dir",
                            observed=output_dir,
                            expected="path to an existing directory",
                        )
                    ],
                    suggested_actions=[
                        "Create the output directory or pass an existing one.",
                    ],
                ),
            )

        from .analyzer import execute_analysis
        from .errors import AnalysisError, RunError, SemanticConfigError
        from .run_artifact_io import load_run_artifact
        from .semantic_config import load_semantic_config

        # Load run artifact.
        try:
            run_artifact = load_run_artifact(run_artifact_path)
        except RunError as exc:
            raise EvalError(
                f"Run artifact invalid: {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis="Run artifact is invalid.",
                    evidence=[
                        DiagnosticEvidence(
                            field="run_artifact_path",
                            observed=run_artifact_path,
                        ),
                        DiagnosticEvidence(
                            field="error",
                            observed=str(exc),
                        ),
                    ],
                    suggested_actions=[
                        "Check the run artifact JSON file for format errors.",
                    ],
                ),
            ) from exc

        # Verify workbook match.
        artifact_wb = os.path.abspath(run_artifact.workbook_path)
        if artifact_wb != self._workbook_path:
            raise EvalError(
                f"Run artifact workbook path {artifact_wb!r} does not match "
                f"session workbook {self._workbook_path!r}.",
                diagnostics=_build_reactive_report(
                    diagnosis="Run artifact references a different workbook.",
                    evidence=[
                        DiagnosticEvidence(
                            field="run_artifact.workbook_path",
                            observed=artifact_wb,
                            expected=self._workbook_path,
                        )
                    ],
                    suggested_actions=[
                        "Open a session on the workbook that produced this "
                        "run artifact, or use the correct run artifact.",
                    ],
                ),
            )

        # Load semantic config.
        try:
            semantic_config = load_semantic_config(self._semantic_config)
        except SemanticConfigError as exc:
            raise EvalError(
                f"Semantic config invalid: {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis="Semantic config is invalid.",
                    evidence=[
                        DiagnosticEvidence(
                            field="semantic_config",
                            observed=str(exc),
                            expected="valid semantic config",
                        )
                    ],
                    suggested_actions=[
                        "Fix the semantic config file and retry.",
                    ],
                ),
            ) from exc

        # Read workbook (must be parseable for artifact ref update).
        workbook = _read_workbook(self._workbook_path)

        # Execute analysis.
        try:
            artifact_text, analysis_id = execute_analysis(
                run_artifact, run_artifact_path, semantic_config
            )
        except AnalysisError as exc:
            raise EvalError(
                f"Analysis failed: {exc}",
                diagnostics=_build_reactive_report(
                    diagnosis="Semantic analysis execution failed.",
                    evidence=[
                        DiagnosticEvidence(
                            field="error",
                            observed=str(exc),
                            expected="successful analysis",
                        )
                    ],
                    suggested_actions=[
                        "Check the semantic adapter and config, then retry.",
                    ],
                ),
            ) from exc

        # Save analysis artifact.
        artifact_filename = f"analysis_{analysis_id}.md"
        artifact_path = os.path.join(output_dir, artifact_filename)
        with open(artifact_path, "w", encoding="utf-8") as fh:
            fh.write(artifact_text)

        # Update workbook artifact references.
        workbook.artifact_references.analysis = artifact_path
        _save_workbook(workbook, self._workbook_path)

        return AnalyzeResult(
            artifact_path=artifact_path,
            analysis_id=analysis_id,
        )

    # ── compare ──────────────────────────────────────────────────────────

    def compare(
        self,
        run_artifact_paths: list[str],
        *,
        goal: str | None = None,
        output_dir: str = ".",
    ) -> CompareResult:
        """Compare two or more completed run artifacts.

        Requires ``semantic_config``.

        Delegates to the shared ``compare_runs()`` library primitive.
        Compare does **not** update the workbook (v1 design decision:
        compare operates across runs that may come from different
        workbooks).

        Returns a ``CompareResult``.
        """
        self._check_released()

        if self._semantic_config is None:
            raise EvalError(
                "Cannot compare: no semantic_config provided. "
                "Pass semantic_config when opening the session.",
                diagnostics=_build_reactive_report(
                    diagnosis="Semantic config not provided.",
                    evidence=[
                        DiagnosticEvidence(
                            field="semantic_config",
                            observed="None",
                            expected="path to a valid semantic config file",
                        )
                    ],
                    suggested_actions=[
                        "Pass semantic_config when opening the session.",
                    ],
                ),
            )

        return compare_runs(
            run_artifact_paths,
            semantic_config=self._semantic_config,
            goal=goal,
            output_dir=output_dir,
        )

    # ── open_diagnostics ─────────────────────────────────────────────────

    def open_diagnostics(self) -> DiagnosticsHandle:
        """Open the L2 diagnostics door.

        Returns a ``DiagnosticsHandle`` with:
        - ``state`` — current workbook/artifact state snapshot
        - ``issues`` — summary issue strings (quick scan)
        - ``reports`` — structured ``DiagnosticReport`` objects with
          evidence, confidence, suggested actions, and recovery options
        - ``apply_recovery_action(action_id)`` — execute a bounded
          deterministic recovery action

        Can be called proactively (without a prior failure) per the
        escalation model.
        """
        self._check_released()
        workbook = _read_workbook(self._workbook_path)
        eval_state = _build_eval_state(
            workbook,
            self._workbook_path,
            self._workflow_config,
        )
        issues = _collect_issues(workbook, self._workbook_path, self._workflow_config)
        reports = _build_diagnostic_reports(
            workbook,
            self._workbook_path,
            self._workflow_config,
            self._preparation_config,
        )
        return DiagnosticsHandle(
            state=eval_state,
            issues=issues,
            reports=reports,
            recovery_executor=self._execute_recovery,
            expert_opener=self._create_expert_handle,
        )

    # ── expert opener (L2→L3 escalation) ─────────────────────────────────

    def _create_expert_handle(self):
        """Create an L3 ExpertHandle bound to this session.

        This is the expert_opener callback passed to DiagnosticsHandle.
        It is not part of the L1 public surface.
        """
        from .expert import ExpertHandle

        self._check_released()

        return ExpertHandle(
            workbook_path=self._workbook_path,
            get_config_paths=self._get_config_paths,
            set_config_paths=self._set_config_paths,
        )

    def _get_config_paths(self) -> dict:
        """Return the current config path bindings."""
        return {
            "preparation_config": self._preparation_config,
            "workflow_config": self._workflow_config,
            "semantic_config": self._semantic_config,
        }

    def _set_config_paths(self, paths: dict) -> None:
        """Update config path bindings from *paths*.

        Only keys present in *paths* are updated.
        Raises ``EvalError`` if the session has been released.
        """
        self._check_released()

        if "preparation_config" in paths:
            self._preparation_config = paths["preparation_config"]
        if "workflow_config" in paths:
            self._workflow_config = paths["workflow_config"]
        if "semantic_config" in paths:
            self._semantic_config = paths["semantic_config"]

    # ── recovery executor (L2 internal) ─────────────────────────────────

    def _execute_recovery(self, action_id: str) -> RecoveryResult:
        """Execute a bounded recovery action.

        This is the recovery executor bound to ``DiagnosticsHandle``.
        It is not part of the L1 public surface.  Callers access it
        through ``handle.apply_recovery_action(action_id)``.
        """
        if action_id == RECOVERY_ADVANCE_PREPARATION:
            return self._recovery_advance_preparation()

        raise EvalError(f"Recovery executor does not know action: {action_id!r}.")

    def _recovery_advance_preparation(self) -> RecoveryResult:
        """Execute the advance_preparation recovery action.

        Calls ``prepare()`` and wraps the result in a ``RecoveryResult``.
        """
        try:
            result = self.prepare()
            post_state = result.state
            return RecoveryResult(
                action_id=RECOVERY_ADVANCE_PREPARATION,
                success=True,
                detail=(
                    f"Preparation advanced from {result.stage_completed.value}. "
                    f"New stage: {post_state.preparation_stage.value}."
                ),
                post_state=post_state,
            )
        except EvalError as exc:
            return RecoveryResult(
                action_id=RECOVERY_ADVANCE_PREPARATION,
                success=False,
                detail=f"Preparation failed: {exc}",
            )


# ── Public: quickstart / continue (end-to-end orchestration) ────────────────


def quickstart(
    name: str,
    *,
    message: str,
    preparation_config: str | None = None,
    semantic_config: str | None = None,
    target_hint: str | None = None,
    output_dir: str = ".",
    agent: str | None = None,
    full_intent: bool = False,
    reporter=None,
) -> QuickstartResult:
    """Run the full quickstart orchestration end-to-end.

    Adapter configuration is resolved from either ``agent`` (e.g.
    ``"claude-cli"``, ``"codex-cli"`` — see :func:`lightassay.list_agents`)
    or explicit ``preparation_config`` / ``semantic_config`` paths.
    Explicit paths override agent defaults. If neither is provided,
    ``EvalError`` is raised. ``target_hint`` is required for quickstart.

    Baseline quickstart keeps the first pass small and high-signal.
    Set ``full_intent=True`` to disable the default minimal narrowing on
    suite breadth when the human request genuinely asks for more.

    ``compare`` is intentionally not part of quickstart.
    """
    from .orchestrator import run_quickstart

    return run_quickstart(
        name,
        message=message,
        target_hint=target_hint,
        preparation_config=preparation_config,
        semantic_config=semantic_config,
        output_dir=output_dir,
        backend=agent,
        reporter=reporter,
        full_intent=full_intent,
    )


def list_agents():
    """Return ``(name, description)`` pairs for every built-in agent."""
    from .backends import describe_backends

    return describe_backends()


def agent_cli_requirement(name: str) -> str | None:
    """Return the CLI binary name required by a built-in agent, if any."""
    from .backends import BUILTIN_BACKENDS

    backend = BUILTIN_BACKENDS.get((name or "").strip())
    if backend is None:
        return None
    return backend.requires_cli


def current_agent(config_root: str | None = None):
    """Return the persisted default agent name, or ``None`` if unset."""
    from .runtime_state import get_default_agent

    return get_default_agent(config_root=config_root)


def set_agent(name: str, config_root: str | None = None) -> str:
    """Persist *name* as the default agent for future commands."""
    from .backends import BUILTIN_BACKENDS
    from .backends import list_backends as _ls
    from .runtime_state import set_default_agent

    stripped = (name or "").strip()
    if stripped not in BUILTIN_BACKENDS:
        raise EvalError(f"Unknown agent: {stripped!r}. Known agents: {', '.join(_ls())}.")
    return set_default_agent(stripped, config_root=config_root)


def list_backends():
    """Internal alias for the built-in agent registry."""
    return list_agents()


def current_backend(config_root: str | None = None):
    """Internal alias for the persisted default agent name."""
    return current_agent(config_root=config_root)


def set_backend(name: str, config_root: str | None = None) -> str:
    """Internal alias for persisting the default agent name."""
    return set_agent(name, config_root=config_root)


def current_workbook(state_root: str = "."):
    """Return the active workbook path for *state_root*, or ``None``.

    Ordinary CLI usage scopes this to the current working directory.
    """
    from .runtime_state import get_active_workbook

    return get_active_workbook(state_root=state_root)


def known_workbooks(state_root: str = "."):
    """Return ``[{id, workbook_path, updated_at}, ...]`` for *state_root*.

    Ordinary CLI usage scopes this to the current working directory.
    """
    from .runtime_state import list_known_workbooks

    return list_known_workbooks(state_root=state_root)


def make_terminal_reporter(stream):
    """Return a stage reporter that writes one line per stage state change.

    Used by the CLI when the caller has not opted into ``--quiet``.  The
    returned object is ``(stage, status, detail) -> None``.
    """
    from .orchestrator import TerminalReporter

    return TerminalReporter(stream)


def continue_workbook(
    *,
    preparation_config: str | None = None,
    semantic_config: str | None = None,
    message: str | None = None,
    workbook_path: str | None = None,
    workbook_id: str | None = None,
    workflow_config_path: str | None = None,
    output_dir: str = ".",
    compare_previous: bool = False,
    agent: str | None = None,
    reporter=None,
) -> ContinueResult:
    """Run one full continue iteration on the active (or explicit) workbook.

    Workbook selection is fully explicit:

    - default: use the active workbook pointer scoped to the current
      working directory;
    - ``workbook_path``: explicit path override;
    - ``workbook_id``: look up a known workbook by id (see
      :func:`known_workbooks`).

    Execution binding is also explicit when needed:

    - default: reuse ``<workbook-stem>.workflow.generated.json`` next to
      the workbook when present;
    - ``workflow_config_path``: explicit execution binding override for
      workbooks that did not originate from quickstart.

    If both ``--message`` and workbook continuation fields are present,
    both are consumed. Continue extends or refines directions / cases,
    runs again, analyzes again, and optionally compares with the
    previous run when ``compare_previous`` is ``True``.
    """
    from .orchestrator import run_continue

    return run_continue(
        preparation_config=preparation_config,
        semantic_config=semantic_config,
        message=message,
        workbook_path=workbook_path,
        workbook_id=workbook_id,
        workflow_config_path=workflow_config_path,
        output_dir=output_dir,
        compare_previous=compare_previous,
        backend=agent,
        reporter=reporter,
    )
