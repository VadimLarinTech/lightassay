"""Run artifact JSON serialization and deserialization.

Strict contract: no guessed values, no defaults, no normalization.
Raises explicit errors on any missing or malformed field.
"""

from __future__ import annotations

import json
from typing import Any

from .errors import RunError
from .run_models import Aggregate, CaseRecord, CaseUsage, RunArtifact

_VALID_RUN_STATUSES = {"completed", "failed"}
_VALID_CASE_STATUSES = {"completed", "failed_execution"}

_REQUIRED_TOP_LEVEL = [
    "run_id",
    "workflow_id",
    "workbook_path",
    "workbook_sha256",
    "workflow_config_sha256",
    "target_kind",
    "target_name",
    "target_locator",
    "target_boundary",
    "target_sources",
    "started_at",
    "finished_at",
    "status",
    "cases",
    "aggregate",
]

_REQUIRED_CASE_FIELDS = [
    "case_id",
    "input",
    "context",
    "expected_behavior",
    "raw_response",
    "parsed_response",
    "duration_ms",
    "usage",
    "status",
    "execution_error",
]

_REQUIRED_AGGREGATE_FIELDS = [
    "total_cases",
    "completed_cases",
    "failed_cases",
    "total_duration_ms",
    "total_input_tokens",
    "total_output_tokens",
]


def run_artifact_to_dict(artifact: RunArtifact) -> dict[str, Any]:
    """Serialize a RunArtifact to a plain dict suitable for JSON encoding."""
    cases = []
    for c in artifact.cases:
        case_dict: dict[str, Any] = {
            "case_id": c.case_id,
            "input": c.input,
            "context": c.context,
            "expected_behavior": c.expected_behavior,
            "raw_response": c.raw_response,
            "parsed_response": c.parsed_response,
            "duration_ms": c.duration_ms,
            "usage": None,
            "status": c.status,
            "execution_error": c.execution_error,
        }
        if c.usage is not None:
            case_dict["usage"] = {
                "input_tokens": c.usage.input_tokens,
                "output_tokens": c.usage.output_tokens,
            }
        cases.append(case_dict)

    result: dict[str, Any] = {
        "run_id": artifact.run_id,
        "workflow_id": artifact.workflow_id,
        "workbook_path": artifact.workbook_path,
        "workbook_sha256": artifact.workbook_sha256,
        "workflow_config_sha256": artifact.workflow_config_sha256,
        "target_kind": artifact.target_kind,
        "target_name": artifact.target_name,
        "target_locator": artifact.target_locator,
        "target_boundary": artifact.target_boundary,
        "target_sources": list(artifact.target_sources),
        "started_at": artifact.started_at,
        "finished_at": artifact.finished_at,
        "status": artifact.status,
        "cases": cases,
        "aggregate": {
            "total_cases": artifact.aggregate.total_cases,
            "completed_cases": artifact.aggregate.completed_cases,
            "failed_cases": artifact.aggregate.failed_cases,
            "total_duration_ms": artifact.aggregate.total_duration_ms,
            "total_input_tokens": artifact.aggregate.total_input_tokens,
            "total_output_tokens": artifact.aggregate.total_output_tokens,
        },
    }

    if artifact.provider is not None:
        result["provider"] = artifact.provider
    if artifact.model is not None:
        result["model"] = artifact.model

    return result


def save_run_artifact(artifact: RunArtifact, path: str) -> None:
    """Serialize a RunArtifact to a JSON file."""
    data = run_artifact_to_dict(artifact)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_run_artifact(path: str) -> RunArtifact:
    """Load a RunArtifact from a JSON file.

    Raises RunError if the file is missing, not valid JSON,
    or missing any required field. No defaults are filled in.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise RunError(f"Run artifact file not found: {path!r}") from None
    except json.JSONDecodeError as exc:
        raise RunError(f"Run artifact file is not valid JSON: {path!r}: {exc}") from None

    if not isinstance(data, dict):
        raise RunError(f"Run artifact must be a JSON object, got {type(data).__name__}")

    for field in _REQUIRED_TOP_LEVEL:
        if field not in data:
            raise RunError(f"Run artifact missing required field: {field!r}")

    # Type-check required top-level string fields.
    _TOP_LEVEL_STRINGS = [
        "run_id",
        "workflow_id",
        "workbook_path",
        "workbook_sha256",
        "workflow_config_sha256",
        "target_kind",
        "target_name",
        "target_locator",
        "target_boundary",
        "started_at",
        "finished_at",
        "status",
    ]
    for field in _TOP_LEVEL_STRINGS:
        if not isinstance(data[field], str):
            raise RunError(
                f"Run artifact field {field!r} must be a string, got {type(data[field]).__name__}"
            )

    for field in ("provider", "model"):
        if field in data and data[field] is not None and not isinstance(data[field], str):
            raise RunError(
                f"Run artifact field {field!r} must be a string or null, got "
                f"{type(data[field]).__name__}"
            )

    status = data["status"]
    if status not in _VALID_RUN_STATUSES:
        raise RunError(
            f"Run artifact has invalid status: {status!r}. "
            f"Expected one of: {', '.join(sorted(_VALID_RUN_STATUSES))}"
        )

    if not isinstance(data["target_sources"], list):
        raise RunError("Run artifact field 'target_sources' must be a JSON array")
    for i, item in enumerate(data["target_sources"]):
        if not isinstance(item, str) or not item:
            raise RunError(f"Run artifact field 'target_sources[{i}]' must be a non-empty string")

    if not isinstance(data["cases"], list):
        raise RunError("Run artifact 'cases' must be a JSON array")

    cases = _parse_cases(data["cases"])
    aggregate = _parse_aggregate(data["aggregate"])

    # Validate run-level status invariants.
    _validate_run_status_invariants(status, cases)

    # Validate aggregate consistency with case records.
    _validate_aggregate_consistency(aggregate, cases)

    return RunArtifact(
        run_id=data["run_id"],
        workflow_id=data["workflow_id"],
        workbook_path=data["workbook_path"],
        workbook_sha256=data["workbook_sha256"],
        workflow_config_sha256=data["workflow_config_sha256"],
        provider=data.get("provider"),
        model=data.get("model"),
        target_kind=data["target_kind"],
        target_name=data["target_name"],
        target_locator=data["target_locator"],
        target_boundary=data["target_boundary"],
        target_sources=list(data["target_sources"]),
        started_at=data["started_at"],
        finished_at=data["finished_at"],
        status=status,
        cases=cases,
        aggregate=aggregate,
    )


def _parse_cases(cases_data: list[Any]) -> list[CaseRecord]:
    records: list[CaseRecord] = []
    for i, item in enumerate(cases_data):
        if not isinstance(item, dict):
            raise RunError(f"Case record [{i}] must be a JSON object")
        for field in _REQUIRED_CASE_FIELDS:
            if field not in item:
                raise RunError(f"Case record [{i}] missing required field: {field!r}")

        # Type-check mandatory string fields.
        for field in ("case_id", "input", "expected_behavior"):
            if not isinstance(item[field], str):
                raise RunError(
                    f"Case record [{i}] field {field!r} must be a string, "
                    f"got {type(item[field]).__name__}"
                )

        # Type-check nullable string fields.
        for field in ("context", "raw_response", "execution_error"):
            val = item[field]
            if val is not None and not isinstance(val, str):
                raise RunError(
                    f"Case record [{i}] field {field!r} must be a string or null, "
                    f"got {type(val).__name__}"
                )

        # Type-check duration_ms.
        if not isinstance(item["duration_ms"], int) or isinstance(item["duration_ms"], bool):
            raise RunError(
                f"Case record [{i}] field 'duration_ms' must be an integer, "
                f"got {type(item['duration_ms']).__name__}"
            )
        if item["duration_ms"] < 0:
            raise RunError(
                f"Case record [{i}] field 'duration_ms' must be >= 0, got {item['duration_ms']}"
            )

        case_status = item["status"]
        if not isinstance(case_status, str):
            raise RunError(
                f"Case record [{i}] field 'status' must be a string, "
                f"got {type(case_status).__name__}"
            )
        if case_status not in _VALID_CASE_STATUSES:
            raise RunError(
                f"Case record [{i}] has invalid status: {case_status!r}. "
                f"Expected one of: {', '.join(sorted(_VALID_CASE_STATUSES))}"
            )

        # Parse and type-check usage.
        usage = None
        if item["usage"] is not None:
            if not isinstance(item["usage"], dict):
                raise RunError(f"Case record [{i}] 'usage' must be an object or null")
            for ufield in ("input_tokens", "output_tokens"):
                if ufield not in item["usage"]:
                    raise RunError(f"Case record [{i}] usage missing field: {ufield!r}")
                if not isinstance(item["usage"][ufield], int) or isinstance(
                    item["usage"][ufield], bool
                ):
                    raise RunError(
                        f"Case record [{i}] usage field {ufield!r} must be an integer, "
                        f"got {type(item['usage'][ufield]).__name__}"
                    )
                if item["usage"][ufield] < 0:
                    raise RunError(
                        f"Case record [{i}] usage field {ufield!r} must be >= 0, "
                        f"got {item['usage'][ufield]}"
                    )
            usage = CaseUsage(
                input_tokens=item["usage"]["input_tokens"],
                output_tokens=item["usage"]["output_tokens"],
            )

        # Status-dependent invariants.
        if case_status == "completed":
            if item["raw_response"] is None:
                raise RunError(
                    f"Case record [{i}] has status 'completed' but 'raw_response' is null"
                )
            if item["usage"] is None:
                raise RunError(f"Case record [{i}] has status 'completed' but 'usage' is null")
            if item["execution_error"] is not None:
                raise RunError(
                    f"Case record [{i}] has status 'completed' but 'execution_error' is not null"
                )
        elif case_status == "failed_execution":
            if item["execution_error"] is None:
                raise RunError(
                    f"Case record [{i}] has status 'failed_execution' but 'execution_error' is null"
                )

        records.append(
            CaseRecord(
                case_id=item["case_id"],
                input=item["input"],
                context=item["context"],
                expected_behavior=item["expected_behavior"],
                raw_response=item["raw_response"],
                parsed_response=item["parsed_response"],
                duration_ms=item["duration_ms"],
                usage=usage,
                status=case_status,
                execution_error=item["execution_error"],
            )
        )
    return records


def _parse_aggregate(agg_data: Any) -> Aggregate:
    if not isinstance(agg_data, dict):
        raise RunError("Run artifact 'aggregate' must be a JSON object")
    for field in _REQUIRED_AGGREGATE_FIELDS:
        if field not in agg_data:
            raise RunError(f"Run artifact aggregate missing field: {field!r}")
        if not isinstance(agg_data[field], int) or isinstance(agg_data[field], bool):
            raise RunError(
                f"Run artifact aggregate field {field!r} must be an integer, "
                f"got {type(agg_data[field]).__name__}"
            )
        if agg_data[field] < 0:
            raise RunError(
                f"Run artifact aggregate field {field!r} must be >= 0, got {agg_data[field]}"
            )
    return Aggregate(
        total_cases=agg_data["total_cases"],
        completed_cases=agg_data["completed_cases"],
        failed_cases=agg_data["failed_cases"],
        total_duration_ms=agg_data["total_duration_ms"],
        total_input_tokens=agg_data["total_input_tokens"],
        total_output_tokens=agg_data["total_output_tokens"],
    )


def _validate_run_status_invariants(status: str, cases: list[CaseRecord]) -> None:
    """Validate that run status is consistent with case statuses."""
    if status == "completed":
        for i, c in enumerate(cases):
            if c.status != "completed":
                raise RunError(
                    f"Run has status 'completed' but case [{i}] "
                    f"(case_id={c.case_id!r}) has status {c.status!r}"
                )
    elif status == "failed":
        has_failed = any(c.status == "failed_execution" for c in cases)
        if not has_failed:
            raise RunError("Run has status 'failed' but no case has status 'failed_execution'")


def _validate_aggregate_consistency(aggregate: Aggregate, cases: list[CaseRecord]) -> None:
    """Validate that aggregate counts and totals match the case records."""
    expected_total = len(cases)
    if aggregate.total_cases != expected_total:
        raise RunError(
            f"Aggregate total_cases ({aggregate.total_cases}) does not match "
            f"actual number of cases ({expected_total})"
        )

    expected_completed = sum(1 for c in cases if c.status == "completed")
    if aggregate.completed_cases != expected_completed:
        raise RunError(
            f"Aggregate completed_cases ({aggregate.completed_cases}) does not "
            f"match actual count ({expected_completed})"
        )

    expected_failed = sum(1 for c in cases if c.status == "failed_execution")
    if aggregate.failed_cases != expected_failed:
        raise RunError(
            f"Aggregate failed_cases ({aggregate.failed_cases}) does not "
            f"match actual count ({expected_failed})"
        )

    expected_duration = sum(c.duration_ms for c in cases)
    if aggregate.total_duration_ms != expected_duration:
        raise RunError(
            f"Aggregate total_duration_ms ({aggregate.total_duration_ms}) does "
            f"not match sum of case durations ({expected_duration})"
        )

    expected_input_tokens = sum(c.usage.input_tokens for c in cases if c.usage is not None)
    if aggregate.total_input_tokens != expected_input_tokens:
        raise RunError(
            f"Aggregate total_input_tokens ({aggregate.total_input_tokens}) "
            f"does not match sum from cases ({expected_input_tokens})"
        )

    expected_output_tokens = sum(c.usage.output_tokens for c in cases if c.usage is not None)
    if aggregate.total_output_tokens != expected_output_tokens:
        raise RunError(
            f"Aggregate total_output_tokens ({aggregate.total_output_tokens}) "
            f"does not match sum from cases ({expected_output_tokens})"
        )
