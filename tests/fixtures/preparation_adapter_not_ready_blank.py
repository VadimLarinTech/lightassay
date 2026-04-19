#!/usr/bin/env python3
"""Test preparation adapter: returns run_ready=false with blank readiness_note.

This adapter violates the reconcile_readiness response contract: when the
adapter decides the workbook is not ready, it must explain why via a non-empty
readiness_note. Used to test that preparer rejects this response.
"""

import json
import sys

request = json.load(sys.stdin)
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
    "run_ready": False,
    "readiness_note": "",
    "priority_conflicts": [],
}

json.dump(response, sys.stdout)
