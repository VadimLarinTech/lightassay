"""Public L1 types for the lightassay library surface.

These types form the caller-visible result and state boundary for the
ordinary L1 goal/task interface.  They are designed to be narrow, typed,
and file-truth-based.

Deeper engine types (Workbook, RunArtifact, etc.) are internal.

``DiagnosticsHandle`` is the L2 diagnostics door: it exposes structured
diagnostic reports, evidence, and bounded recovery actions.  L2 detail
types (``DiagnosticReport``, ``RecoveryOption``, etc.) live in the
``diagnostics`` module and are NOT part of the ordinary top-level
export set.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .diagnostics import DiagnosticReport, RecoveryOption, RecoveryResult
    from .expert import ExpertHandle


class PreparationStage(Enum):
    """The current preparation stage derived from workbook file truth.

    Each value indicates what the next lawful ``prepare()`` call would do.
    ``PREPARED`` means workbook planning is complete; actual ``run_ready``
    still depends on execution binding.
    """

    NEEDS_DIRECTIONS = "needs_directions"
    NEEDS_CASES = "needs_cases"
    NEEDS_READINESS = "needs_readiness"
    PREPARED = "prepared"


@dataclass(frozen=True)
class EvalTarget:
    """Public L1 target definition for target-first planning flows."""

    kind: str
    name: str
    locator: str
    boundary: str
    sources: list[str]
    notes: str = ""


@dataclass(frozen=True)
class EvalState:
    """Typed snapshot of workbook/artifact state derived from file truth.

    Produced by ``EvalSession.state()``.  Every field is read from the
    workbook file at call time --- no hidden cached state.
    """

    workbook_path: str
    preparation_stage: PreparationStage
    has_target_content: bool
    source_reference_count: int
    has_brief_content: bool
    planning_ready: bool
    execution_binding_ready: bool
    direction_count: int
    case_count: int
    workbook_run_ready: bool
    run_ready: bool
    run_artifact: str | None
    analysis_artifact: str | None
    compare_artifact: str | None


@dataclass(frozen=True)
class PrepareResult:
    """Result of a single ``prepare()`` call.

    ``stage_completed`` names the preparation step that was executed.
    ``state`` is the post-operation snapshot.
    """

    stage_completed: PreparationStage
    state: EvalState


@dataclass(frozen=True)
class QuickTryResult:
    """Result of a target-first quick-try planning flow."""

    workbook_path: str
    assumptions: list[str]
    state: EvalState


@dataclass(frozen=True)
class RefineResult:
    """Result of creating a refinement workbook from an existing suite."""

    workbook_path: str
    inherited_direction_count: int
    inherited_case_count: int
    state: EvalState


@dataclass(frozen=True)
class ExploreResult:
    """Result of a bounded exploratory investigation over real run evidence."""

    workbook_path: str
    seeded_from_run_id: str
    failed_case_count: int
    iteration_count: int
    iteration_run_artifact_paths: list[str]
    state: EvalState


@dataclass(frozen=True)
class RunResult:
    """Result of a ``run()`` call."""

    artifact_path: str
    run_id: str
    status: str
    total_cases: int
    completed_cases: int
    failed_cases: int


@dataclass(frozen=True)
class AnalyzeResult:
    """Result of an ``analyze()`` call."""

    artifact_path: str
    analysis_id: str


@dataclass(frozen=True)
class CompareResult:
    """Result of a ``compare()`` call."""

    artifact_path: str
    compare_id: str
    goal: str | None


@dataclass(frozen=True)
class QuickstartResult:
    """Result of an end-to-end ``quickstart`` orchestration.

    Quickstart always runs bootstrap → preparation → run → analyze.
    Compare is intentionally not part of quickstart.
    """

    workbook_path: str
    run_artifact_path: str
    analysis_artifact_path: str
    workflow_config_path: str
    conclusion: str
    assumptions: list[str]
    direction_count: int
    case_count: int
    run_status: str
    total_cases: int
    completed_cases: int
    failed_cases: int
    state: EvalState
    execution_log_path: str
    active_workbook_pointer_path: str


@dataclass(frozen=True)
class ContinueResult:
    """Result of an end-to-end ``continue`` orchestration."""

    workbook_path: str
    run_artifact_path: str
    analysis_artifact_path: str
    compare_artifact_path: str | None
    workflow_config_path: str
    continuation_version: int
    direction_count: int
    case_count: int
    run_status: str
    total_cases: int
    completed_cases: int
    failed_cases: int
    conclusion: str
    state: EvalState
    execution_log_path: str
    active_workbook_pointer_path: str


class DiagnosticsHandle:
    """L2 diagnostics/recovery handle opened by ``open_diagnostics()``
    or attached to ``EvalError`` on L1 failure paths.

    Provides:
    - ``state`` — typed snapshot of workbook/artifact state
    - ``issues`` — summary issue strings (quick scan)
    - ``reports`` — structured ``DiagnosticReport`` objects with
      evidence, confidence, suggested actions, and recovery options
    - ``apply_recovery_action(action_id)`` — execute a bounded
      deterministic recovery action
    - ``open_expert()`` — deliberate escalation into the L3 expert
      surface for deep inspection and bounded low-level control

    L2 detail types (``DiagnosticReport``, ``RecoveryOption``, etc.)
    live in ``lightassay.diagnostics`` and are NOT part of the
    ordinary top-level export set.  L3 expert types live in
    ``lightassay.expert``.  Callers who only use L1 can treat
    this handle as an opaque object; callers who want structured
    recovery can inspect ``reports`` and call ``apply_recovery_action``;
    callers who need deep inspection can call ``open_expert()``.
    """

    __slots__ = ("_state", "_issues", "_reports", "_recovery_executor", "_expert_opener")

    def __init__(
        self,
        state: EvalState,
        issues: list[str],
        reports: list[DiagnosticReport],
        recovery_executor: Callable[[str], RecoveryResult] | None = None,
        expert_opener: Callable[[], ExpertHandle] | None = None,
    ) -> None:
        self._state = state
        self._issues = list(issues)
        self._reports = list(reports)
        self._recovery_executor = recovery_executor
        self._expert_opener = expert_opener

    @property
    def state(self) -> EvalState:
        """Typed snapshot of workbook/artifact state at diagnostics time."""
        return self._state

    @property
    def issues(self) -> list[str]:
        """Summary issue strings (quick human-readable scan)."""
        return list(self._issues)

    @property
    def reports(self) -> list[DiagnosticReport]:
        """Structured L2 diagnostic reports with evidence and recovery."""
        return list(self._reports)

    def apply_recovery_action(self, action_id: str) -> RecoveryResult:
        """Execute a bounded recovery action by ``action_id``.

        Only actions listed in a report's ``recovery_options`` with
        ``available=True`` can be executed.  Raises ``EvalError`` if
        the action is unknown, unavailable, or if no recovery executor
        is bound to this handle.

        Returns a ``RecoveryResult`` with the outcome and post-action
        state.
        """
        from .errors import EvalError

        # Collect all known recovery options across reports.
        known: dict[str, RecoveryOption] = {}
        for report in self._reports:
            for opt in report.recovery_options:
                known[opt.action_id] = opt

        if action_id not in known:
            raise EvalError(
                f"Unknown recovery action: {action_id!r}. Known actions: {sorted(known.keys())}."
            )

        opt = known[action_id]
        if not opt.available:
            raise EvalError(
                f"Recovery action {action_id!r} is not available: {opt.unavailable_reason}"
            )

        if self._recovery_executor is None:
            raise EvalError(
                f"Recovery action {action_id!r} cannot be executed: "
                "no recovery executor is bound to this diagnostics handle."
            )

        return self._recovery_executor(action_id)

    def open_expert(self) -> ExpertHandle:
        """Escalate from L2 diagnostics into the L3 expert surface.

        This is the deliberate L2→L3 escalation path.  The expert
        handle provides deep inspection of workbook source, config
        bindings, and run artifacts, plus one bounded low-level control
        (``rebind_config``).

        Expert types live in ``lightassay.expert`` and are NOT
        part of the ordinary top-level export set.

        Raises ``EvalError`` if no expert opener is bound to this
        diagnostics handle (e.g., when diagnostics came from a reactive
        error path without a live session).
        """
        from .errors import EvalError

        if self._expert_opener is None:
            raise EvalError(
                "Cannot open expert surface: no expert opener is bound "
                "to this diagnostics handle. The expert surface requires "
                "a live session via open_diagnostics()."
            )

        return self._expert_opener()

    def __repr__(self) -> str:
        return (
            f"DiagnosticsHandle(state=..., issues={self._issues!r}, "
            f"reports=[{len(self._reports)} report(s)])"
        )
