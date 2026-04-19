"""L2 diagnostics/recovery types for lightassay.

This module defines the structured types for the L2 diagnostics/recovery
layer.  These types are NOT part of the ordinary L1 top-level exports.
They are accessed through the diagnostics door (``open_diagnostics()``)
or attached to ``EvalError`` on L1 failure paths.

Callers who only use L1 do not need to import from this module.
Callers who inspect diagnostics can import types here for annotations::

    from lightassay.diagnostics import DiagnosticReport, RecoveryOption
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DiagnosticConfidence(Enum):
    """Confidence level for a diagnostic classification.

    ``HIGH`` means the evidence is definitive (e.g., a required file is
    missing).  ``MEDIUM`` means the evidence is strong but the root cause
    may differ.  ``LOW`` means the evidence is circumstantial.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class DiagnosticEvidence:
    """A single piece of evidence supporting a diagnosis.

    ``field`` names what was checked (e.g. ``"preparation_stage"``).
    ``observed`` describes the actual value found.
    ``expected`` describes the value that would satisfy the check,
    or ``None`` if there is no single expected value.
    """

    field: str
    observed: str
    expected: str | None = None


@dataclass(frozen=True)
class RecoveryOption:
    """A bounded, deterministic recovery action.

    ``action_id`` is a stable identifier used with
    ``DiagnosticsHandle.apply_recovery_action()``.

    ``available`` indicates whether preconditions for execution are
    currently satisfied.  When ``False``, ``unavailable_reason`` explains
    why.  Recovery options are always listed (so the caller knows the
    action *exists*), but only executable when ``available is True``.
    """

    action_id: str
    description: str
    available: bool
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class DiagnosticReport:
    """Structured L2 diagnostic report.

    Each report classifies one observed problem with evidence, confidence,
    suggested next actions, and bounded recovery options.

    Diagnostics classify known patterns but do not pretend the
    classification is the full truth.  The final decision remains with
    the human, orchestrator, or calling agent.
    """

    diagnosis: str
    confidence: DiagnosticConfidence
    evidence: list[DiagnosticEvidence]
    suggested_actions: list[str]
    recovery_options: list[RecoveryOption]


@dataclass(frozen=True)
class RecoveryResult:
    """Result of executing a recovery action via ``apply_recovery_action()``.

    ``success`` indicates whether the action completed without error.
    ``detail`` provides a human-readable summary of what happened.
    ``post_state`` is the state snapshot after the action executed
    (present only when ``success is True``).
    """

    action_id: str
    success: bool
    detail: str
    post_state: EvalState | None = None  # noqa: F821 — forward ref


# ── Recovery action IDs (constants) ────────────────────────────────────────

RECOVERY_ADVANCE_PREPARATION = "advance_preparation"
