"""Test-only deterministic stub adapter.

This module is a test helper. It is intentionally NOT registered in
:mod:`lightassay.backends` and must not appear as a user-facing
backend. Shipping it as a normal backend would let quickstart "look
alive" by inventing a target and presenting fake recommendations as
if they were grounded in run evidence.

Test code can invoke it through a preparation / semantic config whose
``adapter`` points at this module. The adapter refuses to fabricate a
target: bootstrap requests without a safely-resolvable ``target_hint``
fail loudly rather than quietly inventing one.

Recommendations emitted here are labelled with ``source=user_intent``
(the user's quickstart message), never ``observed_behavior`` —
the run is deterministic and did not actually infer weak spots from
evidence, so pretending it did would lie.
"""

from __future__ import annotations

import json
import re
import sys

_DOTTED_CALLABLE_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_\.]*)\.(?P<function>[A-Za-z_][A-Za-z0-9_]*)$"
)
_METHOD_URL_RE = re.compile(
    r"^(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(?P<url>https?://\S+)$",
    re.IGNORECASE,
)


def _required_section_ids(user_priorities: dict) -> list[str]:
    sections = user_priorities.get("sections", [])
    non_context = [
        section
        for section in sections
        if section.get("priority_label") != "context" and section.get("text", "").strip()
    ]
    if not non_context:
        non_context = [s for s in sections if s.get("text", "").strip()]
    return [s["section_id"] for s in non_context] or ["freeform_brief"]


def _first_source_path(source_context: dict) -> str:
    explicit = source_context.get("explicit_sources") or []
    if explicit:
        return explicit[0]["path"]
    discovered = source_context.get("discovered_sources") or []
    if discovered:
        return discovered[0]["path"]
    return "n/a"


def _build_directions(
    target_name: str,
    first_source: str,
    priority_ids: list[str],
    planning_mode: str,
    previous_directions: list[dict] | None,
) -> list[dict]:
    reused_ids = [d["direction_id"] for d in (previous_directions or [])]
    base = [
        {
            "direction_id": reused_ids[0] if reused_ids else "core_correctness",
            "body": (
                f"Verify {target_name} returns a well-formed response on a normal input. "
                f"Grounded in {first_source}."
            ),
            "behavior_facet": "core_output_behavior",
            "testing_lens": "positive_and_regression",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": f"Anchored to source {first_source}.",
        },
        {
            "direction_id": reused_ids[1] if len(reused_ids) > 1 else "failure_mode",
            "body": (
                f"Probe failure modes and edge inputs for {target_name}. "
                f"Grounded in {first_source}."
            ),
            "behavior_facet": "failure_mode",
            "testing_lens": "negative_and_robustness",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": f"Anchored to source {first_source}.",
        },
    ]
    if planning_mode == "quickstart_minimal_high_signal":
        return base[:2]
    return base


def _build_cases(
    target_name: str,
    first_source: str,
    priority_ids: list[str],
    directions: list[dict],
    planning_mode: str,
    previous_cases: list[dict] | None,
) -> list[dict]:
    reused_ids = [c["case_id"] for c in (previous_cases or [])]
    cases: list[dict] = []
    for i, d in enumerate(directions):
        case_id = reused_ids[i] if i < len(reused_ids) else f"case_{i + 1}"
        cases.append(
            {
                "case_id": case_id,
                "input": f"stub-input-{d['direction_id']}",
                "context": None,
                "notes": None,
                "target_directions": [d["direction_id"]],
                "expected_behavior": (
                    f"{target_name} satisfies direction {d['direction_id']} "
                    f"({d['behavior_facet']})."
                ),
                "behavior_facet": d["behavior_facet"],
                "testing_lens": d["testing_lens"],
                "covered_user_priority_sections": d["covered_user_priority_sections"],
                "source_rationale": f"Anchored to source {first_source}.",
            }
        )
    if planning_mode == "quickstart_minimal_high_signal":
        cases = cases[:4]
    return cases


def _build_bootstrap(request: dict) -> dict:
    user_message = (request.get("user_message") or "").strip() or "stub quickstart"
    target_hint = (request.get("target_hint") or "").strip()
    constraints = {
        "max_directions": 2,
        "max_cases": 4,
        "focus_notes": [
            "Stub focus: user-facing risks first.",
            "Stay narrow, avoid generic coverage.",
        ],
    }
    if not target_hint:
        return {
            "target": None,
            "execution_shape": None,
            "assumptions": [],
            "quickstart_constraints": constraints,
            "resolution_notes": "",
            "clarification_request": (
                "Stub adapter requires an explicit target hint it can resolve safely. "
                "It does not invent targets."
            ),
        }
    match = _DOTTED_CALLABLE_RE.match(target_hint)
    if match:
        module = match.group("module")
        function = match.group("function")
        target = {
            "kind": "python-callable",
            "name": function,
            "locator": target_hint,
            "boundary": f"python callable {target_hint}",
            "sources": [],
            "notes": "Stub bootstrap resolved the target from the dotted callable hint.",
            "assumptions": [],
        }
        shape = {
            "type": "python-callable",
            "module": module,
            "function": function,
        }
        resolution_notes = f"Resolved target hint {target_hint!r} as a Python callable."
    else:
        method_url = _METHOD_URL_RE.match(target_hint)
        if method_url:
            method = method_url.group("method").upper()
            url = method_url.group("url")
            target = {
                "kind": "http-api",
                "name": f"{method} {url}",
                "locator": url,
                "boundary": f"{method} {url}",
                "sources": [],
                "notes": "Stub bootstrap resolved the target from an explicit METHOD URL hint.",
                "assumptions": [],
            }
            shape = {
                "type": "http",
                "url": url,
                "method": method,
            }
            resolution_notes = f"Resolved target hint {target_hint!r} as an HTTP endpoint."
        else:
            return {
                "target": None,
                "execution_shape": None,
                "assumptions": [],
                "quickstart_constraints": constraints,
                "resolution_notes": "",
                "clarification_request": (
                    "Stub adapter could not resolve the target hint safely. "
                    "Use an explicit dotted callable or METHOD URL hint."
                ),
            }
    return {
        "target": target,
        "execution_shape": shape,
        "assumptions": [
            "Stub bootstrap resolved the target from the provided hint.",
        ],
        "quickstart_constraints": constraints,
        "resolution_notes": (
            f"{resolution_notes} User message: {user_message}."
            if user_message
            else resolution_notes
        ),
    }


def echo(request: dict) -> dict:
    """Default stub workflow callable — used by tests whose workflow
    config targets this module directly. Not intended for production."""
    text = request.get("input", "")
    return {
        "raw_response": f"STUB(ok): {text}",
        "parsed_response": {"verdict": "ok", "echo": text},
        "usage": {
            "input_tokens": max(1, len(str(text).split())),
            "output_tokens": max(1, len(str(text).split()) + 2),
        },
    }


def _build_analysis(request: dict) -> dict:
    run_artifact = request.get("run_artifact", {})
    agg = run_artifact.get("aggregate", {})
    analysis_context = request.get("analysis_context", {})
    user_message = (analysis_context or {}).get("user_message") or "stub quickstart"
    return {
        "analysis_markdown": (
            "## Stub analysis\n\n"
            f"Run {run_artifact.get('run_id', 'unknown')}: "
            f"{agg.get('completed_cases', 0)}/{agg.get('total_cases', 0)} completed, "
            f"{agg.get('failed_cases', 0)} failed."
        ),
        "recommendations": [
            {
                "title": "Add one direction derived from the user's stated intent",
                "to_ensure": (
                    "the workflow covers the intent the user described, not "
                    "just a default first pass."
                ),
                "section": "broader_coverage",
                # source=user_intent is honest here: the stub did not
                # infer weak spots from the run, it restated the user's
                # stated intent.
                "source": "user_intent",
                "detail": f"User intent captured: {user_message}.",
            },
        ],
    }


def _build_compare(request: dict) -> dict:
    goal = request.get("compare_goal")
    runs = request.get("run_artifacts", [])
    response = {
        "compare_markdown": (
            f"## Stub compare\n\nGoal: {goal or 'no explicit goal'}. Compared {len(runs)} runs."
        ),
    }
    if goal is not None:
        response["goal_alignment_summary"] = (
            f"Stub compare — compared {len(runs)} runs against goal: {goal}."
        )
    return response


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Stub adapter stdin is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(request, dict):
        print("Stub adapter request must be a JSON object.", file=sys.stderr)
        sys.exit(1)

    operation = request.get("operation")
    target = request.get("target", {})
    user_priorities = request.get("user_priorities", {})
    source_context = request.get("source_context", {})
    planning_mode = request.get("planning_mode", "full")
    planning_context = request.get("planning_context", {}) or {}

    priority_ids = _required_section_ids(user_priorities)
    first_source = _first_source_path(source_context)
    target_name = target.get("name", "stub_target")
    previous_directions = planning_context.get("previous_directions_full")
    previous_cases = planning_context.get("previous_cases_full")

    if operation == "bootstrap":
        response = _build_bootstrap(request)

    elif operation == "generate_directions":
        directions = _build_directions(
            target_name, first_source, priority_ids, planning_mode, previous_directions
        )
        response = {"directions": directions, "priority_conflicts": []}

    elif operation == "generate_cases":
        directions = request.get("directions") or []
        cases = _build_cases(
            target_name,
            first_source,
            priority_ids,
            directions,
            planning_mode,
            previous_cases,
        )
        response = {"cases": cases, "priority_conflicts": []}

    elif operation == "reconcile_readiness":
        directions = request.get("directions") or []
        cases = request.get("cases") or []
        stripped_directions = [
            {k: v for k, v in d.items() if k != "human_instruction"} for d in directions
        ] or _build_directions(
            target_name, first_source, priority_ids, planning_mode, previous_directions
        )
        stripped_cases = [
            {k: v for k, v in c.items() if k != "human_instruction"} for c in cases
        ] or _build_cases(
            target_name,
            first_source,
            priority_ids,
            stripped_directions,
            planning_mode,
            previous_cases,
        )
        response = {
            "directions": stripped_directions,
            "cases": stripped_cases,
            "run_ready": True,
            "readiness_note": "Stub adapter: deterministic planning, ready to run.",
            "priority_conflicts": [],
        }

    elif operation == "analyze":
        response = _build_analysis(request)

    elif operation == "compare":
        response = _build_compare(request)

    else:
        print(f"Unknown operation: {operation}", file=sys.stderr)
        sys.exit(1)

    json.dump(response, sys.stdout)


if __name__ == "__main__":
    main()
