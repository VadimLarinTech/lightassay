"""Shared helpers for CLI-backed built-in adapters.

Used by :mod:`lightassay.builtin_adapters.claude_cli` and
:mod:`lightassay.builtin_adapters.codex_cli`.

Zero runtime Python dependencies: pure stdlib.

The JSON response handling follows the approved contract:

1. Force JSON output on the agent CLI invocation (backend-specific
   flag, passed by each adapter module).
2. For CLIs that emit event streams (for example Codex JSONL),
   capture the final assistant message through a dedicated side channel.
3. Parse the returned payload with a single boundary-based utility —
   find the first ``{`` and the last ``}`` and ``json.loads`` the
   substring. No fenced-markdown probing, no trailing/object-regex
   salvage, no "try several strategies". If the CLI returned something
   that isn't a JSON object bracketed by ``{ ... }``, we hard-fail.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from typing import Any


def read_request() -> dict:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        fail(f"Adapter stdin is not valid JSON: {exc}")
        return {}  # unreachable


def emit_response(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)


def fail(reason: str, exit_code: int = 1) -> None:
    print(reason, file=sys.stderr)
    sys.exit(exit_code)


def _cleanup_temp_output(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def resolve_command(default: list[str]) -> list[str]:
    override = os.environ.get("LIGHTASSAY_AGENT_CMD")
    if override:
        return shlex.split(override)
    return list(default)


def run_agent(
    command: list[str],
    prompt: str,
    *,
    json_flags: list[str],
    capture_last_message: bool = False,
) -> str:
    """Run the agent CLI with forced-JSON-output flags.

    Each CLI-backed adapter passes its own forced-JSON flag set
    (e.g. ``--output-format=json``). Callers can add extra flags via
    ``LIGHTASSAY_AGENT_JSON_FLAG`` for backends whose CLI grows new
    JSON-mode switches.

    When ``capture_last_message`` is true, the subprocess still emits
    its normal stdout stream, but the returned payload is loaded from
    the CLI's dedicated "last message" output file instead.
    """
    extra_flag = os.environ.get("LIGHTASSAY_AGENT_JSON_FLAG", "").strip()
    args = list(command)
    args.extend(json_flags)
    output_last_message_path: str | None = None
    temp_fd: int | None = None
    if capture_last_message:
        temp_fd, output_last_message_path = tempfile.mkstemp(
            prefix="lightassay-agent-last-message-",
            suffix=".txt",
        )
        os.close(temp_fd)
        temp_fd = None
        args.extend(["--output-last-message", output_last_message_path])
    if extra_flag:
        args.extend(shlex.split(extra_flag))
    if capture_last_message:
        return _run_agent_with_last_message(args, prompt, output_last_message_path)
    try:
        result = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        _cleanup_temp_output(output_last_message_path)
        fail(f"Agent CLI not found on PATH: {args[0]!r}")
    except PermissionError:
        _cleanup_temp_output(output_last_message_path)
        fail(f"Agent CLI not executable: {args[0]!r}")
    if result.returncode != 0:
        _cleanup_temp_output(output_last_message_path)
        fail(
            f"Agent CLI {args[0]!r} exited with code {result.returncode}.\n"
            f"stderr:\n{(result.stderr or '').strip()}"
        )
    return result.stdout


def _run_agent_with_last_message(args: list[str], prompt: str, path: str | None) -> str:
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        _cleanup_temp_output(path)
        fail(f"Agent CLI not found on PATH: {args[0]!r}")
        return ""  # unreachable
    except PermissionError:
        _cleanup_temp_output(path)
        fail(f"Agent CLI not executable: {args[0]!r}")
        return ""  # unreachable

    stderr_chunks: list[str] = []

    def _pump_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_chunks.append(line)

    stderr_thread = threading.Thread(target=_pump_stderr, daemon=True)
    stderr_thread.start()

    stdout_chunks: list[str] = []
    last_progress: str | None = None

    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(prompt)
    process.stdin.close()

    for line in process.stdout:
        stdout_chunks.append(line)
        last_progress = _emit_codex_progress_from_event_line(line, last_progress)

    returncode = process.wait()
    stderr_thread.join()
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    process.stdout.close()
    process.stderr.close()

    if returncode != 0:
        _cleanup_temp_output(path)
        fail(f"Agent CLI {args[0]!r} exited with code {returncode}.\nstderr:\n{stderr.strip()}")
        return ""  # unreachable

    return _read_last_message_output(path, stdout=stdout, stderr=stderr)


def _read_last_message_output(path: str | None, *, stdout: str, stderr: str) -> str:
    if not path:
        fail("Agent CLI last-message capture path was not configured.")
        return ""  # unreachable
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except FileNotFoundError:
        fail(
            "Agent CLI did not produce the requested last-message file. "
            "stdout snippet:\n" + stdout.strip()[:500]
        )
        return ""  # unreachable
    finally:
        _cleanup_temp_output(path)
    if not content.strip():
        fail(
            "Agent CLI last-message output file was empty. "
            "stdout snippet:\n"
            + stdout.strip()[:500]
            + "\nstderr snippet:\n"
            + stderr.strip()[:500]
        )
        return ""  # unreachable
    return content


def _emit_codex_progress_from_event_line(line: str, last_progress: str | None) -> str | None:
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return last_progress
    if not isinstance(payload, dict):
        return last_progress
    progress = _progress_message_from_codex_event(payload)
    if not progress or progress == last_progress:
        return last_progress
    print(f"  {progress}", file=sys.stderr, flush=True)
    return progress


def _progress_message_from_codex_event(payload: dict) -> str | None:
    if payload.get("type") != "item.completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") != "agent_message":
        return None
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return None
    compact = " ".join(first_line.split())
    if len(compact) > 160:
        compact = compact[:157].rstrip() + "..."
    return compact


def extract_json_object(raw: str) -> dict:
    """Boundary-based JSON extraction.

    Adapted from ``word-trainer/backend/utils.py::extract_json_from_response``
    — the approved reference implementation. Finds the first ``{`` and
    the last ``}``, strips surrounding backticks and whitespace, then
    ``json.loads`` the substring. If that fails we fail hard; there is
    no second attempt, no fenced-markdown probing, no salvage.
    """
    start_index = raw.find("{")
    end_index = raw.rfind("}")
    if start_index == -1 or end_index == -1 or end_index < start_index:
        fail(
            "Agent response does not contain a JSON object. "
            "Raw output snippet:\n" + raw.strip()[:500]
        )

    json_str = raw[start_index : end_index + 1].strip("`").strip()
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        fail(f"Agent response JSON did not parse: {exc}. Raw output snippet:\n" + raw.strip()[:500])
        return {}  # unreachable
    if not isinstance(parsed, dict):
        fail(
            f"Agent response JSON is not an object: got {type(parsed).__name__}. "
            "Raw output snippet:\n" + raw.strip()[:500]
        )
        return {}  # unreachable
    return parsed


def dispatch(
    operation: str,
    request: dict,
    *,
    agent_command: list[str],
    json_flags: list[str],
    backend_label: str,
    capture_last_message: bool = False,
) -> dict:
    if operation == "bootstrap":
        prompt = _build_bootstrap_prompt(request, backend_label)
    elif operation == "generate_directions":
        prompt = _build_directions_prompt(request, backend_label)
    elif operation == "generate_cases":
        prompt = _build_cases_prompt(request, backend_label)
    elif operation == "reconcile_readiness":
        prompt = _build_readiness_prompt(request, backend_label)
    elif operation == "analyze":
        prompt = _build_analyze_prompt(request, backend_label)
    elif operation == "compare":
        prompt = _build_compare_prompt(request, backend_label)
    else:
        fail(f"Unknown operation for CLI-backed adapter: {operation!r}")
        return {}

    raw = run_agent(
        agent_command,
        prompt,
        json_flags=json_flags,
        capture_last_message=capture_last_message,
    )
    return extract_json_object(raw)


def run_main(
    default_command: list[str],
    backend_label: str,
    *,
    json_flags: list[str],
    capture_last_message: bool = False,
) -> None:
    """Entry point shared by claude_cli and codex_cli built-ins."""
    request = read_request()
    if not isinstance(request, dict):
        fail("Adapter request must be a JSON object.")
    operation = request.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        fail("Adapter request missing required 'operation' field.")
    command = resolve_command(default_command)
    response = dispatch(
        operation,
        request,
        agent_command=command,
        json_flags=json_flags,
        backend_label=backend_label,
        capture_last_message=capture_last_message,
    )
    emit_response(response)


_JSON_ONLY_GUARD = (
    "Respond with ONE valid JSON object and nothing else. No prose, "
    "no markdown fences, no commentary before or after the object. "
    "Your entire response must be parseable with json.loads."
)


def _dump(name: str, payload: Any) -> str:
    return f"{name}:\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"


def _build_bootstrap_prompt(request: dict, backend: str) -> str:
    return (
        f"You are the quickstart bootstrap layer for lightassay running via {backend}.\n"
        "Given the user's plain-language message and target hint, "
        "inspect the repo/workspace rooted at workspace_root and produce a "
        "structured JSON object with:\n"
        "  target: {kind, name, locator, boundary, sources[], notes, assumptions[]}\n"
        "  execution_shape: python-callable | http | command\n"
        "  assumptions: [string]\n"
        "  quickstart_constraints: {max_directions, max_cases, focus_notes[]}\n"
        "  resolution_notes: string (optional concise bootstrap notes suitable "
        "for workbook additional context)\n"
        "  clarification_request: null | string (set only if you cannot safely "
        "produce target+shape)\n"
        "Treat target_hint as an imprecise human signal — never a resolved target. "
        "If you cannot confidently resolve the target, set clarification_request "
        "to one precise question and leave target / execution_shape null.\n"
        + _JSON_ONLY_GUARD
        + "\n\n"
        + _dump("request", request)
    )


def _build_directions_prompt(request: dict, backend: str) -> str:
    return (
        f"You are the preparation adapter (directions) for lightassay via {backend}.\n"
        "Generate a small, high-signal set of testing directions per the planning_mode.\n"
        "Return JSON: {directions: [{direction_id, body, behavior_facet, testing_lens, "
        "covered_user_priority_sections[], source_rationale}], priority_conflicts: []}.\n"
        "If planning_mode is 'quickstart_minimal_high_signal' and planning_context.full_intent "
        "is false, merge the human request with the internal quickstart framing. When those "
        "signals conflict, the human request wins only for the conflicting part and the "
        "non-conflicting internal framing stays active. Stay within 1-2 directions unless "
        "that conflict rule requires otherwise. If full_intent is true, follow the human "
        "request as stated instead. If 'continue_refine', preserve existing direction intent while "
        "reflecting the feedback; reuse previous direction_ids when the intent is preserved.\n"
        + _JSON_ONLY_GUARD
        + "\n\n"
        + _dump("request", request)
    )


def _build_cases_prompt(request: dict, backend: str) -> str:
    return (
        f"You are the preparation adapter (cases) for lightassay via {backend}.\n"
        "Generate cases that exercise the given directions. Return JSON:\n"
        "  {cases: [{case_id, input, target_directions[], expected_behavior, "
        "behavior_facet, testing_lens, covered_user_priority_sections[], source_rationale, "
        "context|null, notes|null}], priority_conflicts: []}.\n"
        "For planning_mode 'quickstart_minimal_high_signal' and full_intent=false, keep the "
        "default suite narrow but still honor any conflicting human instruction for the "
        "conflicting part only. Keep non-conflicting internal quickstart framing active. "
        "Stay within 2-4 cases unless that conflict rule requires otherwise.\n"
        + _JSON_ONLY_GUARD
        + "\n\n"
        + _dump("request", request)
    )


def _build_readiness_prompt(request: dict, backend: str) -> str:
    return (
        f"You are the preparation adapter (readiness) for lightassay via {backend}.\n"
        "Reconcile directions, cases, and feedback; emit JSON:\n"
        "  {directions:[...], cases:[...], run_ready:bool, readiness_note:string, "
        "priority_conflicts:[]}.\n"
        "Set run_ready=false with a reason only when planning is genuinely blocked.\n"
        + _JSON_ONLY_GUARD
        + "\n\n"
        + _dump("request", request)
    )


def _build_analyze_prompt(request: dict, backend: str) -> str:
    return (
        f"You are the semantic analyzer for lightassay via {backend}.\n"
        "Emit JSON: {analysis_markdown: string, recommendations: [{title, to_ensure, "
        "section, source|null, detail|null}]}.\n"
        "Every recommendation must answer 'to ensure what?'. Sections MUST be one of "
        "'broader_coverage' | 'weak_spots' | 'why_they_matter' — no others are accepted.\n"
        "Source is optional. Only use 'observed_behavior' when the recommendation is "
        "grounded in evidence actually observed in the run artifact; otherwise use "
        "'user_intent' or 'prompt_design' / 'workflow_design'. Never use "
        "'observed_behavior' for filler recommendations or hand-wavy guesses.\n"
        "Do not pad recommendations to hit a count — return only valuable ones.\n"
        + _JSON_ONLY_GUARD
        + "\n\n"
        + _dump("request", request)
    )


def _build_compare_prompt(request: dict, backend: str) -> str:
    return (
        f"You are the semantic comparer for lightassay via {backend}.\n"
        "Emit JSON: {compare_markdown: string, goal_alignment_summary?: string}.\n"
        "Include 'goal_alignment_summary' when the request contains 'compare_goal'.\n"
        + _JSON_ONLY_GUARD
        + "\n\n"
        + _dump("request", request)
    )
