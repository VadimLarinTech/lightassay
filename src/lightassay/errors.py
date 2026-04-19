"""Error types for lightassay."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .diagnostics import DiagnosticReport


class EvalError(Exception):
    """L1 public error boundary.

    All errors surfaced through the ordinary L1 control layer
    (``EvalSession``) are instances of ``EvalError``.  The original
    engine-level exception, if any, is preserved as ``__cause__`` so
    that the diagnostics/expert layers (L2/L3) can inspect it.

    When a structured L2 diagnostic report is available for the failure,
    it is attached as ``diagnostics``.  Callers who only use L1 can
    ignore this attribute; callers who want structured recovery context
    can inspect it.

    Callers that use only L1 need to catch only ``EvalError``.
    """

    diagnostics: DiagnosticReport | None

    def __init__(self, message: str, *, diagnostics: DiagnosticReport | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class WorkbookParseError(Exception):
    """Raised when a workbook file violates the grammar contract.

    The message identifies the specific violation.
    No fallback behavior. The caller must handle or propagate this error.
    """


class WorkflowConfigError(Exception):
    """Raised when a workflow config file is missing, malformed, or violates the spec.

    The message identifies the specific violation.
    No fallback behavior.
    """


class RunError(Exception):
    """Raised when a run cannot start due to a pre-condition violation.

    Examples: workbook not parseable, RUN_READY != yes, adapter not found.
    """


class AnalysisError(Exception):
    """Raised when analysis cannot proceed due to a contract violation.

    Examples: semantic config invalid, adapter returned malformed response,
    adapter exited non-zero.
    """


class CompareError(Exception):
    """Raised when compare cannot proceed due to a contract violation.

    Examples: fewer than 2 run artifacts, non-completed run status,
    adapter returned malformed response, adapter exited non-zero.
    """


class SemanticConfigError(Exception):
    """Raised when a semantic config file is missing, malformed, or violates the spec.

    The message identifies the specific violation.
    No fallback behavior.
    """


class PreparationConfigError(Exception):
    """Raised when a preparation config file is missing, malformed, or violates the spec.

    The message identifies the specific violation.
    No fallback behavior.
    """


class PreparationError(Exception):
    """Raised when a preparation operation cannot proceed due to a contract violation.

    Examples: adapter not found, adapter returned malformed response,
    adapter exited non-zero, response missing required fields.
    """
