"""Domain models for run artifacts (v1).

These dataclasses represent the complete run artifact structure.
They are produced by the run command and saved as JSON.
No fallback, no guessed values. Every field must be explicitly provided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CaseUsage:
    """Token usage for a single case execution."""

    input_tokens: int
    output_tokens: int


@dataclass
class CaseRecord:
    """Record of a single case execution within a run."""

    case_id: str
    input: str
    context: str | None
    expected_behavior: str
    raw_response: str | None
    parsed_response: Any
    duration_ms: int
    usage: CaseUsage | None
    status: str  # "completed" | "failed_execution"
    execution_error: str | None


@dataclass
class Aggregate:
    """Aggregate raw facts across all cases in a run."""

    total_cases: int
    completed_cases: int
    failed_cases: int
    total_duration_ms: int
    total_input_tokens: int
    total_output_tokens: int


@dataclass
class RunArtifact:
    """Complete run artifact — the machine-readable record of one run."""

    run_id: str
    workflow_id: str
    workbook_path: str
    workbook_sha256: str
    workflow_config_sha256: str
    provider: str | None
    model: str | None
    target_kind: str
    target_name: str
    target_locator: str
    target_boundary: str
    target_sources: list[str]
    started_at: str  # ISO 8601
    finished_at: str  # ISO 8601
    status: str  # "completed" | "failed"
    cases: list[CaseRecord]
    aggregate: Aggregate
