#!/usr/bin/env python3
"""Agent CLI fixture for built-in backend tests.

This script stands in for the external ``claude`` / ``codex`` CLI. It
receives the full prompt on stdin, extracts the embedded JSON request,
and returns deterministic JSON for every adapter operation.
"""

from __future__ import annotations

import json
import os
import re
import sys

_DOTTED_CALLABLE_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_\.]*)\.(?P<function>[A-Za-z_][A-Za-z0-9_]*)$"
)
_REQUEST_MARKER = "request:\n```json\n"
_CALLABLE_SOURCE = os.path.abspath(os.path.join(os.path.dirname(__file__), "callable_echo.py"))


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def _extract_request(prompt: str) -> dict:
    start = prompt.rfind(_REQUEST_MARKER)
    if start == -1:
        _fail("agent fixture could not find request JSON block in prompt")
    start += len(_REQUEST_MARKER)
    end = prompt.find("\n```", start)
    if end == -1:
        _fail("agent fixture could not find end of request JSON block in prompt")
    try:
        payload = json.loads(prompt[start:end])
    except json.JSONDecodeError as exc:
        _fail(f"agent fixture could not parse request JSON block: {exc}")
    if not isinstance(payload, dict):
        _fail("agent fixture request payload is not a JSON object")
    return payload


def _bootstrap_response(request: dict) -> dict:
    target_hint = (request.get("target_hint") or "").strip()
    workspace_root = os.path.abspath(request.get("workspace_root") or os.getcwd())
    match = _DOTTED_CALLABLE_RE.match(target_hint)
    if not match:
        return {
            "target": None,
            "execution_shape": None,
            "assumptions": [],
            "quickstart_constraints": {
                "max_directions": 2,
                "max_cases": 4,
                "focus_notes": ["Agent fixture bootstrap needs an explicit callable hint."],
            },
            "resolution_notes": "",
            "clarification_request": (
                "Agent fixture requires a dotted callable target hint for tests."
            ),
        }

    module = match.group("module")
    function = match.group("function")
    relative_source = "relative_target.py"
    callable_sources = (
        [relative_source]
        if os.path.isfile(os.path.join(workspace_root, relative_source))
        else [_CALLABLE_SOURCE]
    )
    return {
        "target": {
            "kind": "python-callable",
            "name": function,
            "locator": target_hint,
            "boundary": f"python callable {target_hint}",
            "sources": callable_sources,
            "notes": "Agent fixture resolved the dotted callable target hint.",
            "assumptions": [],
        },
        "execution_shape": {
            "type": "python-callable",
            "module": module,
            "function": function,
        },
        "assumptions": ["Agent fixture resolved the target from target_hint."],
        "quickstart_constraints": {
            "max_directions": 2,
            "max_cases": 4,
            "focus_notes": ["Stay narrow and hit the most important risks first."],
        },
        "resolution_notes": f"Resolved dotted callable hint {target_hint!r} for backend tests.",
    }


def _priority_ids(request: dict) -> list[str]:
    sections = (request.get("user_priorities") or {}).get("sections") or []
    ids = [
        section.get("section_id")
        for section in sections
        if isinstance(section, dict)
        and section.get("priority_label") != "context"
        and (section.get("text") or "").strip()
    ]
    if not ids:
        ids = [
            section.get("section_id")
            for section in sections
            if isinstance(section, dict) and (section.get("text") or "").strip()
        ]
    return [i for i in ids if i] or ["freeform_brief"]


def _directions_response(request: dict) -> dict:
    target = request.get("target") or {}
    target_name = target.get("name", "target")
    priority_ids = _priority_ids(request)
    directions = [
        {
            "direction_id": "core_correctness",
            "body": f"Verify {target_name} handles a normal input correctly.",
            "behavior_facet": "core_output_behavior",
            "testing_lens": "positive_and_regression",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": "Grounded in the resolved target hint.",
        },
        {
            "direction_id": "edge_behavior",
            "body": f"Probe edge and failure-prone behavior for {target_name}.",
            "behavior_facet": "failure_mode",
            "testing_lens": "negative_and_robustness",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": "Grounded in the resolved target hint.",
        },
    ]
    return {"directions": directions, "priority_conflicts": []}


def _cases_response(request: dict) -> dict:
    directions = request.get("directions") or []
    cases = []
    for index, direction in enumerate(directions, start=1):
        cases.append(
            {
                "case_id": f"case_{index}",
                "input": f"input-for-{direction['direction_id']}",
                "target_directions": [direction["direction_id"]],
                "expected_behavior": f"Satisfies {direction['direction_id']}.",
                "behavior_facet": direction["behavior_facet"],
                "testing_lens": direction["testing_lens"],
                "covered_user_priority_sections": direction["covered_user_priority_sections"],
                "source_rationale": direction["source_rationale"],
                "context": None,
                "notes": None,
            }
        )
    return {"cases": cases, "priority_conflicts": []}


def _readiness_response(request: dict) -> dict:
    directions = [
        {k: v for k, v in item.items() if k != "human_instruction"}
        for item in (request.get("directions") or [])
    ]
    cases = [
        {k: v for k, v in item.items() if k != "human_instruction"}
        for item in (request.get("cases") or [])
    ]
    return {
        "directions": directions,
        "cases": cases,
        "run_ready": True,
        "readiness_note": "Agent fixture marked the workbook ready.",
        "priority_conflicts": [],
    }


def _analyze_response(request: dict) -> dict:
    run_artifact = request["run_artifact"]
    agg = run_artifact["aggregate"]
    return {
        "analysis_markdown": (
            "## Agent fixture analysis\n\n"
            f"Run {run_artifact['run_id']}: "
            f"{agg['completed_cases']}/{agg['total_cases']} completed."
        ),
        "recommendations": [
            {
                "title": "Add one follow-up direction for weak inputs",
                "to_ensure": "the next pass covers weak spots instead of only the happy path.",
                "section": "weak_spots",
                "source": "workflow_design",
                "detail": "Deterministic backend-test recommendation.",
            }
        ],
    }


def _compare_response(request: dict) -> dict:
    runs = request.get("run_artifacts") or []
    goal = request.get("compare_goal")
    response = {"compare_markdown": (f"## Agent fixture compare\n\nCompared {len(runs)} runs.")}
    if goal is not None:
        response["goal_alignment_summary"] = f"Compared {len(runs)} runs against goal: {goal}"
    return response


def main() -> None:
    argv = sys.argv[1:]
    output_last_message_path = None
    if "--output-last-message" in argv:
        index = argv.index("--output-last-message")
        try:
            output_last_message_path = argv[index + 1]
        except IndexError:
            _fail("agent fixture expected a path after --output-last-message")

    prompt = sys.stdin.read()
    request = _extract_request(prompt)
    operation = request.get("operation")

    if operation == "bootstrap":
        response = _bootstrap_response(request)
    elif operation == "generate_directions":
        response = _directions_response(request)
    elif operation == "generate_cases":
        response = _cases_response(request)
    elif operation == "reconcile_readiness":
        response = _readiness_response(request)
    elif operation == "analyze":
        response = _analyze_response(request)
    elif operation == "compare":
        response = _compare_response(request)
    else:
        _fail(f"agent fixture does not support operation {operation!r}")

    if output_last_message_path:
        print(json.dumps({"type": "thread.started", "thread_id": "fixture-thread"}))
        print(json.dumps({"type": "turn.started"}))
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_0",
                        "type": "agent_message",
                        "text": f"Processing {operation} for the current workspace.",
                    },
                }
            )
        )
        with open(output_last_message_path, "w", encoding="utf-8") as fh:
            json.dump(response, fh, ensure_ascii=False)
        return

    json.dump(response, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
