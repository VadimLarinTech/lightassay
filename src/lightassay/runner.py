"""Subprocess-based workflow runner implementing the JSON protocol.

See docs/subprocess_protocol.md for the full specification.
No fallback, no best-effort recovery.

Supports two execution modes:

- **Legacy adapter**: single executable path called via subprocess.
- **First-party driver**: dispatched through the adapter_pack module.

Both modes produce the same run artifact structure and enforce the
same strict response validation.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone

from .adapter_pack import DriverConfig, DriverError, execute_driver
from .errors import RunError
from .run_models import Aggregate, CaseRecord, CaseUsage, RunArtifact
from .workbook_models import Case, Workbook
from .workflow_config import WorkflowConfig


def compute_sha256(path: str) -> str:
    """Compute hex-encoded SHA-256 of a file's raw bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def execute_run(
    workbook: Workbook,
    workbook_path: str,
    config: WorkflowConfig,
    config_path: str,
) -> RunArtifact:
    """Execute a full run: call the adapter for each case, build the run artifact.

    Pre-conditions (must be checked by caller):
    - workbook.run_readiness.run_ready is True
    - For legacy adapter: config.adapter points to an existing executable
    - For driver: config.driver is a valid DriverConfig

    Raises RunError if the legacy adapter executable is not found or not
    executable.  Individual case failures are recorded in the case records,
    not raised.
    """
    _validate_execution_binding_before_run(config)

    run_id = uuid.uuid4().hex[:12]
    workbook_sha = compute_sha256(workbook_path)
    config_sha = compute_sha256(config_path)
    started_at = datetime.now(timezone.utc).isoformat()

    case_records: list[CaseRecord] = []

    for case in workbook.cases:
        record = _execute_case(config, case)
        case_records.append(record)

    finished_at = datetime.now(timezone.utc).isoformat()

    aggregate = _build_aggregate(case_records)

    has_failure = any(r.status == "failed_execution" for r in case_records)
    run_status = "failed" if has_failure else "completed"

    return RunArtifact(
        run_id=run_id,
        workflow_id=config.workflow_id,
        workbook_path=workbook_path,
        workbook_sha256=workbook_sha,
        workflow_config_sha256=config_sha,
        provider=config.provider,
        model=config.model,
        target_kind=workbook.target.kind,
        target_name=workbook.target.name,
        target_locator=workbook.target.locator,
        target_boundary=workbook.target.boundary,
        target_sources=list(workbook.target.sources),
        started_at=started_at,
        finished_at=finished_at,
        status=run_status,
        cases=case_records,
        aggregate=aggregate,
    )


def _validate_execution_binding_before_run(config: WorkflowConfig) -> None:
    """Hard-stop on broken execution bindings before any case loop starts."""
    if config.adapter is not None:
        adapter = config.adapter
        if not os.path.exists(adapter):
            raise RunError(f"Execution binding broken: adapter not found: {adapter!r}")
        if not os.access(adapter, os.X_OK):
            raise RunError(f"Execution binding broken: adapter not executable: {adapter!r}")
        return

    driver = config.driver
    if driver is None:
        return

    from .adapter_pack import CommandDriverConfig, HttpDriverConfig, PythonCallableDriverConfig

    if isinstance(driver, PythonCallableDriverConfig):
        try:
            module = importlib.import_module(driver.module)
        except ImportError as exc:
            raise RunError(
                "Execution binding broken: python-callable driver module "
                f"{driver.module!r} cannot be imported: {exc}"
            ) from exc
        if not hasattr(module, driver.function):
            raise RunError(
                "Execution binding broken: python-callable driver module "
                f"{driver.module!r} has no attribute {driver.function!r}"
            )
        func = getattr(module, driver.function)
        if not callable(func):
            raise RunError(
                "Execution binding broken: python-callable driver "
                f"{driver.module}.{driver.function} is not callable"
            )
        return

    if isinstance(driver, HttpDriverConfig):
        from urllib.parse import urlparse

        parsed = urlparse(driver.url)
        if not parsed.scheme:
            raise RunError(
                f"Execution binding broken: http driver URL {driver.url!r} has no scheme"
            )
        if not parsed.netloc:
            raise RunError(f"Execution binding broken: http driver URL {driver.url!r} has no host")
        return

    if isinstance(driver, CommandDriverConfig):
        cmd = driver.command[0]
        if shutil.which(cmd) is not None:
            return
        command_root = driver.working_dir or driver.config_dir
        resolved = os.path.normpath(os.path.join(command_root, cmd)) if command_root else cmd
        if not os.path.exists(resolved):
            raise RunError(
                f"Execution binding broken: command driver executable not found: {cmd!r}"
            )
        if not os.access(resolved, os.X_OK):
            raise RunError(
                f"Execution binding broken: command driver executable not executable: {resolved!r}"
            )
        return


def _build_request(config: WorkflowConfig, case: Case) -> dict:
    """Build the standard adapter request dict."""
    request = {
        "case_id": case.case_id,
        "input": case.input,
        "context": case.context,
        "workflow_id": config.workflow_id,
    }
    if config.provider is not None:
        request["provider"] = config.provider
    if config.model is not None:
        request["model"] = config.model
    return request


def _execute_case(config: WorkflowConfig, case: Case) -> CaseRecord:
    """Execute a single case via the appropriate adapter path."""
    request_data = _build_request(config, case)
    start_time = time.monotonic()

    if config.driver is not None:
        # First-party driver path.
        raw_result = _call_driver(config.driver, request_data)
    else:
        # Legacy subprocess adapter path.
        raw_result = _call_subprocess(config.adapter, request_data)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Error from execution layer.
    if raw_result[1] is not None:
        return _failed_case(case, duration_ms, raw_result[1])

    # Validate the response dict (shared for both paths).
    response = raw_result[0]
    return _validate_and_build_record(case, duration_ms, response)


def _call_driver(driver_config: DriverConfig, request_data: dict) -> tuple[dict | None, str | None]:
    """Call a first-party driver.

    Returns ``(response_dict, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    try:
        response = execute_driver(driver_config, request_data)
        return (response, None)
    except DriverError as exc:
        return (None, str(exc))


def _call_subprocess(adapter: str, request_data: dict) -> tuple[dict | None, str | None]:
    """Call a legacy subprocess adapter.

    Returns ``(response_dict, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    request_json = json.dumps(request_data, ensure_ascii=False)

    try:
        result = subprocess.run(
            [adapter],
            input=request_json,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return (None, f"Adapter not found: {adapter!r}")
    except PermissionError:
        return (None, f"Adapter not executable: {adapter!r}")

    if result.returncode != 0:
        return (None, f"Adapter exited with code {result.returncode}")

    stdout = result.stdout
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return (None, "Adapter stdout is not valid JSON")

    if not isinstance(response, dict):
        return (
            None,
            f"Adapter response must be a JSON object, got {type(response).__name__}",
        )

    return (response, None)


def _validate_and_build_record(case: Case, duration_ms: int, response: dict) -> CaseRecord:
    """Validate the adapter response dict and build a CaseRecord.

    This is the shared validation path for both subprocess adapters and
    first-party drivers.  Any response contract violation results in a
    ``failed_execution`` record.
    """
    # Validate required fields.
    for field in ("raw_response", "parsed_response", "usage"):
        if field not in response:
            return _failed_case(
                case,
                duration_ms,
                f"Adapter response missing required field: {field!r}",
            )

    # Validate raw_response type.
    if not isinstance(response["raw_response"], str):
        return _failed_case(
            case,
            duration_ms,
            "Adapter response field 'raw_response' has invalid type",
        )

    # Validate usage structure.
    usage_data = response["usage"]
    if not isinstance(usage_data, dict):
        return _failed_case(
            case,
            duration_ms,
            "Adapter response field 'usage' has invalid type",
        )
    for ufield in ("input_tokens", "output_tokens"):
        if ufield not in usage_data:
            return _failed_case(
                case,
                duration_ms,
                f"Adapter response missing required field: 'usage.{ufield}'",
            )
        if not isinstance(usage_data[ufield], int) or isinstance(usage_data[ufield], bool):
            return _failed_case(
                case,
                duration_ms,
                f"Adapter response field 'usage.{ufield}' has invalid type",
            )
        if usage_data[ufield] < 0:
            return _failed_case(
                case,
                duration_ms,
                f"Adapter response field 'usage.{ufield}' is negative",
            )

    return CaseRecord(
        case_id=case.case_id,
        input=case.input,
        context=case.context,
        expected_behavior=case.expected_behavior,
        raw_response=response["raw_response"],
        parsed_response=response["parsed_response"],
        duration_ms=duration_ms,
        usage=CaseUsage(
            input_tokens=usage_data["input_tokens"],
            output_tokens=usage_data["output_tokens"],
        ),
        status="completed",
        execution_error=None,
    )


def _failed_case(case: Case, duration_ms: int, error: str) -> CaseRecord:
    """Build a CaseRecord for a failed execution."""
    return CaseRecord(
        case_id=case.case_id,
        input=case.input,
        context=case.context,
        expected_behavior=case.expected_behavior,
        raw_response=None,
        parsed_response=None,
        duration_ms=duration_ms,
        usage=None,
        status="failed_execution",
        execution_error=error,
    )


def _build_aggregate(records: list[CaseRecord]) -> Aggregate:
    """Compute aggregate raw facts from case records."""
    total = len(records)
    completed = sum(1 for r in records if r.status == "completed")
    failed = sum(1 for r in records if r.status == "failed_execution")
    total_duration = sum(r.duration_ms for r in records)
    total_input = sum(r.usage.input_tokens for r in records if r.usage is not None)
    total_output = sum(r.usage.output_tokens for r in records if r.usage is not None)

    return Aggregate(
        total_cases=total,
        completed_cases=completed,
        failed_cases=failed,
        total_duration_ms=total_duration,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
    )
