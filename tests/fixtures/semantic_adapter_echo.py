#!/usr/bin/env python3
"""Test semantic adapter: dispatches on operation field.

Handles both 'analyze' and 'compare' operations.
A single adapter can handle multiple semantic operations by dispatching
on the 'operation' field in the request — this is the intended design.
"""

import json
import sys

request = json.load(sys.stdin)
operation = request["operation"]

if operation == "analyze":
    run = request["run_artifact"]

    lines = [
        "## Summary",
        "",
        f"Analyzed run **{run['run_id']}** for workflow **{run['workflow_id']}**.",
        "",
        f"- Total cases: {run['aggregate']['total_cases']}",
        f"- Completed: {run['aggregate']['completed_cases']}",
        f"- Failed: {run['aggregate']['failed_cases']}",
        "",
        "## Case Details",
        "",
    ]

    for case in run["cases"]:
        lines.append(f"### {case['case_id']}")
        lines.append("")
        lines.append(f"- Input: {case['input']}")
        lines.append(f"- Expected: {case['expected_behavior']}")
        lines.append(f"- Response: {case['raw_response']}")
        lines.append(f"- Status: {case['status']}")
        lines.append("")

    lines.append("## Conclusion")
    lines.append("")
    lines.append("All cases executed successfully.")

    response = {
        "analysis_markdown": "\n".join(lines),
    }

elif operation == "compare":
    runs = request["run_artifacts"]
    compare_goal = request.get("compare_goal")

    lines = [
        "## Comparison Summary",
        "",
        f"Compared {len(runs)} runs.",
        "",
    ]

    if compare_goal is not None:
        lines.append(f"- Goal: {compare_goal}")
        lines.append("")

    for i, run in enumerate(runs):
        provider = run.get("provider")
        model = run.get("model")
        lines.append(f"### Run {i + 1}: {run['run_id']}")
        lines.append("")
        lines.append(f"- Workflow: {run['workflow_id']}")
        if provider is not None:
            lines.append(f"- Provider: {provider}")
        if model is not None:
            lines.append(f"- Model: {model}")
        lines.append(f"- Total cases: {run['aggregate']['total_cases']}")
        lines.append(f"- Duration: {run['aggregate']['total_duration_ms']}ms")
        lines.append("")

    lines.append("## Differences")
    lines.append("")
    lines.append("No significant differences detected (echo adapter).")
    lines.append("")
    lines.append("## Recommendations")
    lines.append("")
    lines.append("Both runs produced equivalent results.")

    response = {
        "compare_markdown": "\n".join(lines),
    }
    if compare_goal is not None:
        response["goal_alignment_summary"] = f"Compared runs against goal: {compare_goal}"

else:
    print(f"Unknown operation: {operation}", file=sys.stderr)
    sys.exit(1)

json.dump(response, sys.stdout)
