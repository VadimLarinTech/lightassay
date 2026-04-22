#!/usr/bin/env python3
"""Test preparation adapter with bootstrap support.

Extends preparation_adapter_echo with a deterministic 'bootstrap'
operation response so quickstart end-to-end tests can exercise the
orchestrator without a real LLM. Also supports 'analyze' so the same
config can double as the semantic adapter during quickstart / continue
smoke tests.
"""

import json
import os
import re
import sys

request = json.load(sys.stdin)
operation = request["operation"]
planning_mode = request.get("planning_mode", "full")
planning_context = request.get("planning_context", {})
target = request.get("target", {})
user_priorities = request.get("user_priorities", {})
source_context = request.get("source_context", {})

priority_sections = user_priorities.get("sections", [])
required_sections = [
    section
    for section in priority_sections
    if section.get("priority_label") != "context" and section.get("text", "").strip()
]
if not required_sections:
    required_sections = [
        section for section in priority_sections if section.get("text", "").strip()
    ]
required_section_ids = [section["section_id"] for section in required_sections] or [
    "freeform_brief"
]
explicit_sources = source_context.get("explicit_sources", [])
first_explicit_source = explicit_sources[0]["path"] if explicit_sources else "no-explicit-source"
_DOTTED_CALLABLE_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_\.]*)\.(?P<function>[A-Za-z_][A-Za-z0-9_]*)$"
)
_METHOD_URL_RE = re.compile(
    r"^(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(?P<url>https?://\S+)$",
    re.IGNORECASE,
)
_CALLABLE_SOURCE = os.path.abspath(os.path.join(os.path.dirname(__file__), "callable_echo.py"))


if operation == "bootstrap":
    user_message = (request.get("user_message") or "").strip() or "test"
    target_hint = (request.get("target_hint") or "").strip()
    workspace_root = os.path.abspath(request.get("workspace_root") or os.getcwd())
    relative_source = "relative_target.py"
    callable_sources = (
        [relative_source]
        if os.path.isfile(os.path.join(workspace_root, relative_source))
        else [_CALLABLE_SOURCE]
    )
    constraints = {
        "max_directions": 2,
        "max_cases": 4,
        "focus_notes": ["Test bootstrap focus note."],
    }
    dotted = _DOTTED_CALLABLE_RE.match(target_hint)
    method_url = _METHOD_URL_RE.match(target_hint)
    if dotted:
        module = dotted.group("module")
        function = dotted.group("function")
        response = {
            "target": {
                "kind": "python-callable",
                "name": function,
                "locator": target_hint,
                "boundary": f"python callable {target_hint}",
                "sources": callable_sources,
                "notes": "Bootstrap fixture resolved the dotted callable hint.",
                "assumptions": [],
            },
            "execution_shape": {
                "type": "python-callable",
                "module": module,
                "function": function,
            },
            "assumptions": ["Bootstrap fixture resolved the target from target_hint."],
            "quickstart_constraints": constraints,
            "resolution_notes": (
                f"Resolved dotted callable hint {target_hint!r}. User message: {user_message}"
            ),
        }
    elif method_url:
        method = method_url.group("method").upper()
        url = method_url.group("url")
        response = {
            "target": {
                "kind": "http-api",
                "name": f"{method} {url}",
                "locator": url,
                "boundary": f"{method} {url}",
                "sources": [url],
                "notes": "Bootstrap fixture resolved the explicit METHOD URL hint.",
                "assumptions": [],
            },
            "execution_shape": {
                "type": "http",
                "url": url,
                "method": method,
            },
            "assumptions": ["Bootstrap fixture resolved the target from target_hint."],
            "quickstart_constraints": constraints,
            "resolution_notes": (
                f"Resolved HTTP hint {target_hint!r}. User message: {user_message}"
            ),
        }
    else:
        response = {
            "target": None,
            "execution_shape": None,
            "assumptions": [],
            "quickstart_constraints": constraints,
            "resolution_notes": "",
            "clarification_request": (
                "Bootstrap fixture requires a resolvable dotted callable or METHOD URL hint."
            ),
        }

elif operation == "generate_directions":
    directions = [
        {
            "direction_id": "core_correctness",
            "body": (
                f"Verify {target.get('name', 'target')} handles a normal input correctly. "
                f"Brief: {request.get('brief', '')[:40]}"
            ),
            "behavior_facet": "core_output_behavior",
            "testing_lens": "positive_and_regression",
            "covered_user_priority_sections": required_section_ids,
            "source_rationale": f"Anchored to {first_explicit_source}.",
        },
        {
            "direction_id": "edge_behavior",
            "body": (f"Probe edge-case behavior for {target.get('name', 'target')}."),
            "behavior_facet": "edge_case_behavior",
            "testing_lens": "boundary_and_negative",
            "covered_user_priority_sections": required_section_ids,
            "source_rationale": f"Anchored to {first_explicit_source}.",
        },
    ]
    if planning_mode == "quickstart_minimal_high_signal":
        directions = directions[:1]
    response = {"directions": directions, "priority_conflicts": []}

elif operation == "generate_cases":
    directions = request["directions"]
    cases = []
    for i, d in enumerate(directions):
        cases.append(
            {
                "case_id": f"case_{i + 1}",
                "input": f"input-{d['direction_id']}",
                "target_directions": [d["direction_id"]],
                "expected_behavior": f"Satisfies direction {d['direction_id']}.",
                "behavior_facet": d["behavior_facet"],
                "testing_lens": d["testing_lens"],
                "covered_user_priority_sections": d["covered_user_priority_sections"],
                "source_rationale": d["source_rationale"],
                "context": None,
                "notes": None,
            }
        )
    if planning_mode == "quickstart_minimal_high_signal":
        cases = cases[:2]
    response = {"cases": cases, "priority_conflicts": []}

elif operation == "reconcile_readiness":
    directions = request["directions"]
    cases = request["cases"]
    response = {
        "directions": [
            {
                "direction_id": d["direction_id"],
                "body": d["body"],
                "behavior_facet": d["behavior_facet"],
                "testing_lens": d["testing_lens"],
                "covered_user_priority_sections": d["covered_user_priority_sections"],
                "source_rationale": d["source_rationale"],
            }
            for d in directions
        ],
        "cases": [
            {
                "case_id": c["case_id"],
                "input": c["input"],
                "target_directions": c["target_directions"],
                "expected_behavior": c["expected_behavior"],
                "behavior_facet": c["behavior_facet"],
                "testing_lens": c["testing_lens"],
                "covered_user_priority_sections": c["covered_user_priority_sections"],
                "source_rationale": c["source_rationale"],
                "context": c.get("context"),
                "notes": c.get("notes"),
            }
            for c in cases
        ],
        "run_ready": True,
        "readiness_note": "Bootstrap fixture reconciled.",
        "priority_conflicts": [],
    }

elif operation == "analyze":
    run_artifact = request["run_artifact"]
    agg = run_artifact["aggregate"]
    response = {
        "analysis_markdown": (
            f"## Bootstrap fixture analysis\n\n"
            f"Run {run_artifact['run_id']}: {agg['completed_cases']}/{agg['total_cases']} completed."
        ),
        "recommendations": [
            {
                "title": "Add coverage for the less-exercised branch",
                "to_ensure": "the workflow still handles boundary input safely.",
                "section": "broader_coverage",
                "source": "workflow_design",
                "detail": "Deterministic fixture recommendation based on suite shape, not run evidence.",
            }
        ],
    }

elif operation == "compare":
    goal = request.get("compare_goal")
    runs = request.get("run_artifacts", [])
    response = {
        "compare_markdown": (
            f"## Bootstrap fixture compare\n\n"
            f"Goal: {goal or 'no explicit goal'}. Compared {len(runs)} runs."
        )
    }
    if goal is not None:
        response["goal_alignment_summary"] = f"Compared {len(runs)} runs against goal: {goal}."

else:
    print(f"Unknown operation: {operation}", file=sys.stderr)
    sys.exit(1)

json.dump(response, sys.stdout)
