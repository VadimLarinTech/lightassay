#!/usr/bin/env python3
"""Test preparation adapter: dispatches on operation field.

Handles 'generate_directions', 'generate_cases', and 'reconcile_readiness'.
Returns deterministic structured JSON for testing — no LLM involved.
"""

import json
import sys

request = json.load(sys.stdin)
operation = request["operation"]
planning_mode = request.get("planning_mode", "full")
planning_context = request.get("planning_context", {})
target = request.get("target", {})
user_priorities = request.get("user_priorities", {})
source_context = request.get("source_context", {})

priority_sections = user_priorities.get("sections", [])
first_priority = priority_sections[0]["heading"] if priority_sections else "no-priority"
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
discovered_sources = source_context.get("discovered_sources", [])
first_explicit_source = explicit_sources[0]["path"] if explicit_sources else "no-explicit-source"
first_discovered_source = (
    discovered_sources[0]["path"] if discovered_sources else "no-discovered-source"
)
exploration_goal = planning_context.get("exploration_goal", "no-exploration-goal")
failed_cases = planning_context.get("failed_cases", [])
max_cases = planning_context.get("max_cases")
iteration_index = planning_context.get("iteration_index", 1)
iteration_trace = planning_context.get("iteration_trace", [])

if operation == "generate_directions":
    brief = request["brief"]
    directions = [
        {
            "direction_id": "correctness",
            "body": (
                f"Verify {target.get('name', 'unknown-target')} correctness "
                f"based on priority {first_priority} and source {first_explicit_source}. "
                f"Brief: {brief[:50]}"
            ),
            "behavior_facet": "core_output_behavior",
            "testing_lens": "positive_and_regression",
            "covered_user_priority_sections": required_section_ids,
            "source_rationale": (
                f"Anchored to explicit source {first_explicit_source} for target "
                f"{target.get('locator', 'unknown-locator')}."
            ),
        },
        {
            "direction_id": "edge-cases",
            "body": (
                f"Test boundary and edge-case inputs for locator "
                f"{target.get('locator', 'unknown-locator')} with discovered "
                f"context {first_discovered_source}."
            ),
            "behavior_facet": "edge_case_behavior",
            "testing_lens": "boundary_and_negative",
            "covered_user_priority_sections": required_section_ids,
            "source_rationale": (
                f"Anchored to discovered source {first_discovered_source} to inspect "
                f"neighboring behavior around {target.get('locator', 'unknown-locator')}."
            ),
        },
    ]
    if planning_mode == "quick_try":
        directions = directions[:1]
    elif planning_mode == "exploratory":
        directions[0]["direction_id"] = f"explore-{iteration_index}-correctness"
        directions[0]["body"] += (
            f" Exploration goal: {exploration_goal}. "
            f"Iteration: {iteration_index}. Prior trace length: {len(iteration_trace)}."
        )
        if len(directions) > 1:
            directions[1]["direction_id"] = f"explore-{iteration_index}-weak-spots"
            directions[1]["body"] = (
                f"Investigate weak spots suggested by {len(failed_cases)} failed "
                f"case(s) under goal {exploration_goal} during iteration {iteration_index}."
            )

    response = {"directions": directions, "priority_conflicts": []}

elif operation == "generate_cases":
    brief = request["brief"]
    directions = request["directions"]

    # Generate one case per direction for deterministic testing.
    cases = []
    for i, d in enumerate(directions):
        cases.append(
            {
                "case_id": f"case-{i + 1}",
                "input": f"Test input for {d['direction_id']}",
                "target_directions": [d["direction_id"]],
                "expected_behavior": (
                    f"Should satisfy {d['direction_id']} direction for "
                    f"{target.get('boundary', 'unknown-boundary')} while respecting "
                    f"user priority {first_priority}."
                ),
                "behavior_facet": d["behavior_facet"],
                "testing_lens": d["testing_lens"],
                "covered_user_priority_sections": d["covered_user_priority_sections"],
                "source_rationale": d["source_rationale"],
                "context": f"Context for case {i + 1}" if i == 0 else None,
                "notes": (
                    f"Grounded in {first_explicit_source}; discovered {first_discovered_source}."
                    if i == 0
                    else None
                ),
            }
        )

    if planning_mode == "quick_try":
        cases = cases[:1]
    elif planning_mode == "exploratory" and isinstance(max_cases, int):
        cases = cases[:max_cases]
        if cases:
            cases[0]["case_id"] = f"iter-{iteration_index}-case-1"
            cases[0]["notes"] = (
                f"Exploratory case seeded from {len(failed_cases)} failed case(s); "
                f"goal: {exploration_goal}; iteration: {iteration_index}; "
                f"prior_trace_length: {len(iteration_trace)}."
            )

    response = {"cases": cases, "priority_conflicts": []}

elif operation == "reconcile_readiness":
    directions = request["directions"]
    cases = request["cases"]

    # Pass through directions and cases, set RUN_READY: yes.
    reconciled_directions = [
        {
            "direction_id": d["direction_id"],
            "body": d["body"],
            "behavior_facet": d["behavior_facet"],
            "testing_lens": d["testing_lens"],
            "covered_user_priority_sections": d["covered_user_priority_sections"],
            "source_rationale": d["source_rationale"],
        }
        for d in directions
    ]
    reconciled_cases = [
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
    ]

    response = {
        "directions": reconciled_directions,
        "cases": reconciled_cases,
        "run_ready": True,
        "readiness_note": (
            "Quick try workbook reconciled and ready."
            if planning_mode == "quick_try"
            else (
                f"Exploratory workbook reconciled under goal: {exploration_goal}."
                if planning_mode == "exploratory"
                else "All cases reconciled and ready."
            )
        ),
        "priority_conflicts": [],
    }

else:
    print(f"Unknown operation: {operation}", file=sys.stderr)
    sys.exit(1)

json.dump(response, sys.stdout)
