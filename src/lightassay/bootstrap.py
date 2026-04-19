"""Quickstart bootstrap layer.

The bootstrap layer turns a plain-language user message (plus an optional
target hint) into the minimal structured target, execution shape,
assumptions, and planning constraints needed to drive quickstart.

Resolution is agent-led. The human may supply a rough hint, but the
bootstrap adapter is responsible for inspecting the workspace and
producing the authoritative target and execution shape. Local code
passes the hint to the adapter as input signal only — it never decides
that a URL is POST, guesses that something is a command, or otherwise
auto-binds a hint to an execution shape on its own.

When the adapter cannot resolve the target safely, it returns a single
``clarification_request`` string; quickstart stops and surfaces it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from ._subprocess_capture import run_text_subprocess
from .errors import PreparationError
from .preparation_config import PreparationConfig

# ── Exec shape types ─────────────────────────────────────────────────────────

EXEC_SHAPE_PYTHON = "python-callable"
EXEC_SHAPE_HTTP = "http"
EXEC_SHAPE_COMMAND = "command"

_KNOWN_EXEC_SHAPES = frozenset({EXEC_SHAPE_PYTHON, EXEC_SHAPE_HTTP, EXEC_SHAPE_COMMAND})


# ── Data shapes ──────────────────────────────────────────────────────────────


@dataclass
class TargetResolution:
    """Structured target entity derived by the bootstrap adapter."""

    kind: str
    name: str
    locator: str
    boundary: str
    sources: list[str]
    notes: str
    assumptions: list[str] = field(default_factory=list)


@dataclass
class ExecutionShape:
    """Minimal internal execution shape used by quickstart to build a
    workflow config without forcing the user to author JSON by hand."""

    type: str
    module: str | None = None
    function: str | None = None
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    timeout_seconds: int | None = None
    command: list[str] | None = None


@dataclass
class QuickstartConstraints:
    """Planning bounds recorded so the preparation layer can honor the
    "small, high-signal" contract."""

    max_directions: int = 2
    max_cases: int = 4
    focus_notes: list[str] = field(default_factory=list)


@dataclass
class BootstrapResult:
    """Full bootstrap output produced by the adapter.

    ``clarification_request`` is non-None when the adapter declines to
    produce a safe target — quickstart then stops with that single
    precise question.
    """

    target: TargetResolution | None
    execution_shape: ExecutionShape | None
    assumptions: list[str]
    constraints: QuickstartConstraints
    resolution_notes: str
    clarification_request: str | None = None
    full_intent: bool = False


def _default_quickstart_constraints(full_intent: bool) -> QuickstartConstraints:
    if full_intent:
        # --full-intent disables the default minimal narrowing so the
        # adapter follows the human request without being squeezed into
        # the baseline "small first pass" shape.
        return QuickstartConstraints(
            max_directions=0,
            max_cases=0,
            focus_notes=[
                "Full-intent mode: do not artificially narrow suite breadth.",
                "Follow the human request as written instead of imposing a "
                "default minimal first pass.",
            ],
        )
    return QuickstartConstraints(
        max_directions=2,
        max_cases=4,
        focus_notes=[
            "Prefer the most important user-facing risks.",
            "Prefer the most vulnerable or failure-prone areas.",
            "Stay narrow. Avoid broad generic coverage.",
            "Produce a short suite that demonstrates signal quickly.",
        ],
    )


# ── Adapter call ─────────────────────────────────────────────────────────────


def _call_bootstrap_adapter(
    config: PreparationConfig,
    request: dict,
) -> dict:
    command = config.invocation()

    # File-backed bootstrap adapter: pre-check file is executable.
    if not config.command:
        adapter = config.adapter
        if not os.path.exists(adapter):
            raise PreparationError(f"Bootstrap adapter not found: {adapter!r}")
        if not os.access(adapter, os.X_OK):
            raise PreparationError(f"Bootstrap adapter not executable: {adapter!r}")

    request_json = json.dumps(request, ensure_ascii=False)

    try:
        result = run_text_subprocess(
            command,
            input_text=request_json,
            env=config.subprocess_env(),
            live_stderr=bool(config.command),
        )
    except FileNotFoundError:
        raise PreparationError(f"Bootstrap adapter not found: {command[0]!r}") from None
    except PermissionError:
        raise PreparationError(f"Bootstrap adapter not executable: {command[0]!r}") from None

    if result.returncode != 0:
        raise PreparationError(
            f"Bootstrap adapter exited with code {result.returncode}: "
            f"{(result.stderr or '').strip()[:400]}"
        )

    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        raise PreparationError("Bootstrap adapter stdout is not valid JSON") from None

    if not isinstance(response, dict):
        raise PreparationError(
            f"Bootstrap adapter response must be a JSON object, got {type(response).__name__}"
        )
    return response


def _validate_target_payload(payload: Any) -> TargetResolution:
    if not isinstance(payload, dict):
        raise PreparationError("Bootstrap adapter response: 'target' must be a JSON object")
    required = ("kind", "name", "locator", "boundary")
    for field_name in required:
        if field_name not in payload:
            raise PreparationError(
                f"Bootstrap adapter response: target missing required field: {field_name!r}"
            )
        if not isinstance(payload[field_name], str) or not payload[field_name].strip():
            raise PreparationError(
                f"Bootstrap adapter response: target.{field_name} must be a non-empty string"
            )
    sources = payload.get("sources", [])
    if not isinstance(sources, list) or any(
        not isinstance(item, str) or not item.strip() for item in sources
    ):
        raise PreparationError(
            "Bootstrap adapter response: target.sources must be a list of non-empty strings"
        )
    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise PreparationError("Bootstrap adapter response: target.notes must be a string")
    assumptions = payload.get("assumptions", [])
    if not isinstance(assumptions, list) or any(
        not isinstance(item, str) or not item.strip() for item in assumptions
    ):
        raise PreparationError(
            "Bootstrap adapter response: target.assumptions must be a list of non-empty strings"
        )
    return TargetResolution(
        kind=payload["kind"].strip(),
        name=payload["name"].strip(),
        locator=payload["locator"].strip(),
        boundary=payload["boundary"].strip(),
        sources=[s.strip() for s in sources],
        notes=notes.strip(),
        assumptions=[a.strip() for a in assumptions],
    )


def _validate_execution_shape_payload(payload: Any) -> ExecutionShape:
    if not isinstance(payload, dict):
        raise PreparationError(
            "Bootstrap adapter response: 'execution_shape' must be a JSON object"
        )
    exec_type = payload.get("type")
    if exec_type not in _KNOWN_EXEC_SHAPES:
        raise PreparationError(
            f"Bootstrap adapter response: execution_shape.type must be one of "
            f"{sorted(_KNOWN_EXEC_SHAPES)}, got {exec_type!r}"
        )
    shape = ExecutionShape(type=exec_type)
    if exec_type == EXEC_SHAPE_PYTHON:
        for field_name in ("module", "function"):
            val = payload.get(field_name)
            if not isinstance(val, str) or not val.strip():
                raise PreparationError(
                    f"Bootstrap adapter response: execution_shape.{field_name} "
                    "must be a non-empty string for python-callable shape"
                )
        shape.module = payload["module"].strip()
        shape.function = payload["function"].strip()
    elif exec_type == EXEC_SHAPE_HTTP:
        for field_name in ("url", "method"):
            val = payload.get(field_name)
            if not isinstance(val, str) or not val.strip():
                raise PreparationError(
                    f"Bootstrap adapter response: execution_shape.{field_name} "
                    "must be a non-empty string for http shape"
                )
        shape.url = payload["url"].strip()
        shape.method = payload["method"].strip().upper()
        headers = payload.get("headers")
        if headers is not None:
            if not isinstance(headers, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()
            ):
                raise PreparationError(
                    "Bootstrap adapter response: execution_shape.headers must be a "
                    "dict of string→string"
                )
            shape.headers = dict(headers)
        timeout = payload.get("timeout_seconds")
        if timeout is not None:
            if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
                raise PreparationError(
                    "Bootstrap adapter response: execution_shape.timeout_seconds "
                    "must be a positive integer"
                )
            shape.timeout_seconds = timeout
    elif exec_type == EXEC_SHAPE_COMMAND:
        cmd = payload.get("command")
        if (
            not isinstance(cmd, list)
            or not cmd
            or any(not isinstance(p, str) or not p.strip() for p in cmd)
        ):
            raise PreparationError(
                "Bootstrap adapter response: execution_shape.command must be a "
                "non-empty list of non-empty strings"
            )
        shape.command = [p.strip() for p in cmd]
    return shape


def _validate_constraints_payload(payload: Any, full_intent: bool) -> QuickstartConstraints:
    defaults = _default_quickstart_constraints(full_intent)
    if payload is None:
        return defaults
    if not isinstance(payload, dict):
        raise PreparationError(
            "Bootstrap adapter response: 'quickstart_constraints' must be a JSON object"
        )

    max_directions = payload.get("max_directions", defaults.max_directions)
    max_cases = payload.get("max_cases", defaults.max_cases)
    # max_* == 0 means "no cap" in full-intent mode; negative values still fail.
    for name, value in (("max_directions", max_directions), ("max_cases", max_cases)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PreparationError(
                f"Bootstrap adapter response: quickstart_constraints.{name} "
                "must be a non-negative integer"
            )

    focus_notes = payload.get("focus_notes", defaults.focus_notes)
    if not isinstance(focus_notes, list) or any(
        not isinstance(n, str) or not n.strip() for n in focus_notes
    ):
        raise PreparationError(
            "Bootstrap adapter response: quickstart_constraints.focus_notes must be a "
            "list of non-empty strings"
        )

    return QuickstartConstraints(
        max_directions=max_directions,
        max_cases=max_cases,
        focus_notes=[n.strip() for n in focus_notes],
    )


def _validate_bootstrap_response(
    response: dict,
    full_intent: bool,
) -> tuple[
    TargetResolution | None,
    ExecutionShape | None,
    QuickstartConstraints,
    list[str],
    str,
    str | None,
]:
    assumptions = response.get("assumptions", [])
    if not isinstance(assumptions, list) or any(
        not isinstance(item, str) or not item.strip() for item in assumptions
    ):
        raise PreparationError(
            "Bootstrap adapter response: 'assumptions' must be a list of non-empty strings"
        )
    assumptions = [a.strip() for a in assumptions]

    resolution_notes = response.get("resolution_notes", "")
    if not isinstance(resolution_notes, str):
        raise PreparationError("Bootstrap adapter response: 'resolution_notes' must be a string")

    clarification = response.get("clarification_request")
    if clarification is not None:
        if not isinstance(clarification, str) or not clarification.strip():
            raise PreparationError(
                "Bootstrap adapter response: 'clarification_request' must be a "
                "non-empty string or null"
            )
        # Clarification path: target + shape may be null.
        target = (
            _validate_target_payload(response["target"])
            if response.get("target") is not None
            else None
        )
        shape = (
            _validate_execution_shape_payload(response["execution_shape"])
            if response.get("execution_shape") is not None
            else None
        )
        return (
            target,
            shape,
            _validate_constraints_payload(response.get("quickstart_constraints"), full_intent),
            assumptions,
            resolution_notes,
            clarification.strip(),
        )

    if "target" not in response:
        raise PreparationError(
            "Bootstrap adapter response missing required field: 'target' "
            "(required when 'clarification_request' is not set)"
        )
    if "execution_shape" not in response:
        raise PreparationError(
            "Bootstrap adapter response missing required field: 'execution_shape' "
            "(required when 'clarification_request' is not set)"
        )
    target = _validate_target_payload(response["target"])
    shape = _validate_execution_shape_payload(response["execution_shape"])
    return (
        target,
        shape,
        _validate_constraints_payload(response.get("quickstart_constraints"), full_intent),
        assumptions,
        resolution_notes,
        None,
    )


def bootstrap_quickstart(
    user_message: str,
    *,
    target_hint: str | None,
    preparation_config: PreparationConfig | None,
    workspace_root: str | None = None,
    full_intent: bool = False,
) -> BootstrapResult:
    """Resolve the user's message and target hint via the bootstrap adapter.

    ``preparation_config`` must be configured — there is no local
    deterministic auto-binding fallback. The hint is passed to the
    adapter as raw input signal; the adapter inspects the workspace and
    produces the authoritative target, execution shape, and planning
    constraints. If it cannot do so safely it must return a
    ``clarification_request``.

    Raises ``PreparationError`` when the adapter fails or is missing.
    """
    if not isinstance(user_message, str) or not user_message.strip():
        raise PreparationError("Bootstrap requires a non-empty user_message.")

    if preparation_config is None:
        raise PreparationError(
            "Bootstrap requires a preparation_config with a bootstrap-capable "
            "adapter. The user's target hint is input signal only — local code "
            "does not auto-resolve targets."
        )

    workspace_root = os.path.abspath(workspace_root or os.getcwd())
    target_hint = (target_hint or "").strip() or None

    request = {
        "operation": "bootstrap",
        "user_message": user_message.strip(),
        "target_hint": target_hint,
        "workspace_root": workspace_root,
        "full_intent": full_intent,
        "bootstrap_directive": (
            "Inspect the workspace rooted at workspace_root to determine the "
            "intended target and execution shape. Treat target_hint as an "
            "imprecise human signal, never as an already-resolved target. "
            "If you cannot confidently resolve the target, set "
            "clarification_request to one precise question and leave target / "
            "execution_shape null."
        ),
    }
    if full_intent:
        request["full_intent_directive"] = (
            "Full-intent mode is active. Do not impose the default minimal "
            "first-pass narrowing on suite breadth or selection. Follow the "
            "human request as stated."
        )

    response = _call_bootstrap_adapter(preparation_config, request)
    (
        target,
        shape,
        constraints,
        assumptions,
        resolution_notes,
        clarification,
    ) = _validate_bootstrap_response(response, full_intent)

    if clarification is not None:
        return BootstrapResult(
            target=target,
            execution_shape=shape,
            assumptions=assumptions,
            constraints=constraints,
            resolution_notes=resolution_notes,
            clarification_request=clarification,
            full_intent=full_intent,
        )

    return BootstrapResult(
        target=target,
        execution_shape=shape,
        assumptions=assumptions,
        constraints=constraints,
        resolution_notes=resolution_notes,
        full_intent=full_intent,
    )
