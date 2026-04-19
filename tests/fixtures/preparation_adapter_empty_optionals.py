#!/usr/bin/env python3
"""Test preparation adapter that returns empty-string optional fields.

Used to verify that empty-string context/notes are preserved as-is,
not silently normalized to None.
"""

import json
import sys

request = json.load(sys.stdin)
operation = request["operation"]

if operation == "generate_cases":
    directions = request["directions"]

    cases = []
    for i, d in enumerate(directions):
        cases.append(
            {
                "case_id": f"case-{i + 1}",
                "input": f"Test input for {d['direction_id']}",
                "target_directions": [d["direction_id"]],
                "expected_behavior": f"Should satisfy {d['direction_id']} direction.",
                "behavior_facet": d["behavior_facet"],
                "testing_lens": d["testing_lens"],
                "covered_user_priority_sections": d["covered_user_priority_sections"],
                "source_rationale": d["source_rationale"],
                "context": "",
                "notes": "",
            }
        )

    response = {"cases": cases, "priority_conflicts": []}

elif operation == "reconcile_readiness":
    directions = request["directions"]
    cases = request["cases"]

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
            "context": "",
            "notes": "",
        }
        for c in cases
    ]

    response = {
        "directions": reconciled_directions,
        "cases": reconciled_cases,
        "run_ready": True,
        "readiness_note": "Ready with empty optionals.",
        "priority_conflicts": [],
    }

else:
    print(f"Unsupported operation: {operation}", file=sys.stderr)
    sys.exit(1)

json.dump(response, sys.stdout)
