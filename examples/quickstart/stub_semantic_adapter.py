#!/usr/bin/env python3
"""Stub semantic adapter — stands in for an LLM during analyze/compare.

Returns a deterministic markdown summary. Replace with a real LLM adapter
to get actual semantic analysis of the run.
"""

import json
import sys


def render_analyze(run):
    agg = run["aggregate"]
    lines = [
        "## Summary",
        "",
        f"Analyzed run **{run['run_id']}** for workflow **{run['workflow_id']}**.",
        "",
        f"- Total cases: {agg['total_cases']}",
        f"- Completed: {agg['completed_cases']}",
        f"- Failed: {agg['failed_cases']}",
        "",
        "## Case Details",
        "",
    ]
    for case in run["cases"]:
        lines += [
            f"### {case['case_id']}",
            "",
            f"- Input: `{case['input']!r}`",
            f"- Expected: {case['expected_behavior']}",
            f"- Response: `{case['raw_response']!r}`",
            f"- Status: **{case['status']}**",
            "",
        ]
    lines += [
        "## Conclusion",
        "",
        "Stub analysis: this is a deterministic summary produced without an LLM. "
        "Replace this adapter with a real semantic adapter to get genuine qualitative analysis.",
    ]
    return "\n".join(lines)


def render_compare(runs, goal):
    lines = [
        "## Comparison Summary",
        "",
        f"Compared {len(runs)} runs.",
        "",
    ]
    if goal:
        lines += [f"- Goal: {goal}", ""]
    for index, run in enumerate(runs, start=1):
        agg = run["aggregate"]
        lines += [
            f"### Run {index}: {run['run_id']}",
            "",
            f"- Workflow: {run['workflow_id']}",
            f"- Cases: {agg['total_cases']} total, {agg['completed_cases']} completed",
            "",
        ]
    lines += [
        "## Recommendations",
        "",
        "Stub compare: replace this adapter with a real semantic adapter to surface "
        "meaningful differences between runs.",
    ]
    return "\n".join(lines)


def _stub_recommendations(run_artifact, analysis_context):
    """Return the stub recommendations payload with explicit
    ``to_ensure`` reasoning per the semantic adapter contract.
    """
    user_message = (analysis_context or {}).get("user_message")
    return [
        {
            "title": "Add a direction that probes boundary inputs",
            "to_ensure": (
                "the workflow does not silently over-correct inputs that are mostly valid."
            ),
            "section": "broader_coverage",
            "source": "workflow_design",
            "detail": (
                "Stub analysis is deterministic and not evidence-driven; "
                f"user intent: {user_message!r}."
                if user_message
                else None
            ),
        },
        {
            "title": "Investigate the least-exercised direction",
            "to_ensure": ("weak spots are surfaced before they leak into production."),
            "section": "weak_spots",
            "source": "workflow_design",
            "detail": None,
        },
    ]


def main() -> None:
    request = json.load(sys.stdin)
    operation = request["operation"]

    if operation == "analyze":
        analysis_context = request.get("analysis_context")
        response = {
            "analysis_markdown": render_analyze(request["run_artifact"]),
            "recommendations": _stub_recommendations(request["run_artifact"], analysis_context),
        }
    elif operation == "compare":
        goal = request.get("compare_goal")
        response = {"compare_markdown": render_compare(request["run_artifacts"], goal)}
        if goal is not None:
            response["goal_alignment_summary"] = f"Compared runs against goal: {goal}"
    else:
        print(f"Unknown operation: {operation}", file=sys.stderr)
        sys.exit(1)

    json.dump(response, sys.stdout)


if __name__ == "__main__":
    main()
