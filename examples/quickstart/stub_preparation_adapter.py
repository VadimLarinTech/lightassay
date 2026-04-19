#!/usr/bin/env python3
"""Stub preparation adapter — stands in for an LLM during planning.

`lightassay` delegates direction/case generation and readiness reconciliation
to an external adapter. This stub returns deterministic structured JSON so the
quickstart flow runs end-to-end with zero external dependencies. Replace it
with a real LLM adapter for real evaluations.
"""

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


def first_or(items, default):
    return items[0] if items else default


def required_section_ids(user_priorities):
    sections = user_priorities.get("sections", [])
    non_context = [
        section
        for section in sections
        if section.get("priority_label") != "context" and section.get("text", "").strip()
    ]
    if not non_context:
        non_context = [section for section in sections if section.get("text", "").strip()]
    return [section["section_id"] for section in non_context] or ["freeform_brief"]


def build_directions(target_name, first_source, priority_ids):
    return [
        {
            "direction_id": "happy_path",
            "body": f"Verify {target_name} returns a well-formed response on a normal input.",
            "behavior_facet": "core_output_behavior",
            "testing_lens": "positive_and_regression",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": f"Anchored to source {first_source}.",
        },
        {
            "direction_id": "failure_mode",
            "body": f"Verify {target_name} handles a degenerate input safely.",
            "behavior_facet": "failure_mode",
            "testing_lens": "negative_and_robustness",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": f"Anchored to source {first_source}.",
        },
    ]


def build_cases(target_name, first_source, priority_ids):
    return [
        {
            "case_id": "c1_happy",
            "input": "The quick brown fox jumps over the lazy dog.",
            "context": None,
            "notes": None,
            "target_directions": ["happy_path"],
            "expected_behavior": f"{target_name} returns a well-formed response marking the input as ok.",  # noqa: E501
            "behavior_facet": "core_output_behavior",
            "testing_lens": "positive_and_regression",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": f"Anchored to source {first_source}.",
        },
        {
            "case_id": "c2_edge",
            "input": "?",
            "context": None,
            "notes": None,
            "target_directions": ["failure_mode"],
            "expected_behavior": f"{target_name} handles a minimal/edge input without crashing.",
            "behavior_facet": "failure_mode",
            "testing_lens": "negative_and_robustness",
            "covered_user_priority_sections": priority_ids,
            "source_rationale": f"Anchored to source {first_source}.",
        },
    ]


def strip_field(items, field):
    return [{k: v for k, v in item.items() if k != field} for item in items]


def build_bootstrap_response(request):
    """Return a deterministic bootstrap payload for the stub adapter.

    This example adapter resolves only safe, explicit hint forms
    (dotted Python callable or METHOD URL). Otherwise it asks for
    clarification instead of inventing a fake target.
    """
    user_message = (request.get("user_message") or "").strip() or "Quickstart smoke test."
    target_hint = (request.get("target_hint") or "").strip()
    constraints = {
        "max_directions": 2,
        "max_cases": 4,
        "focus_notes": [
            "Prefer the most important user-facing risks.",
            "Stay narrow; avoid generic coverage.",
        ],
    }
    dotted = _DOTTED_CALLABLE_RE.match(target_hint)
    method_url = _METHOD_URL_RE.match(target_hint)
    if dotted:
        module = dotted.group("module")
        function = dotted.group("function")
        target = {
            "kind": "python-callable",
            "name": function,
            "locator": target_hint,
            "boundary": f"python callable {target_hint}",
            "sources": [],
            "notes": "Resolved from the dotted callable hint.",
            "assumptions": [],
        }
        shape = {
            "type": "python-callable",
            "module": module,
            "function": function,
        }
        return {
            "target": target,
            "execution_shape": shape,
            "assumptions": ["Resolved target from explicit dotted callable hint."],
            "quickstart_constraints": constraints,
            "resolution_notes": (
                f"Resolved dotted callable hint {target_hint!r}. User message: {user_message}"
            ),
        }
    if method_url:
        method = method_url.group("method").upper()
        url = method_url.group("url")
        return {
            "target": {
                "kind": "http-api",
                "name": f"{method} {url}",
                "locator": url,
                "boundary": f"{method} {url}",
                "sources": [],
                "notes": "Resolved from the explicit METHOD URL hint.",
                "assumptions": [],
            },
            "execution_shape": {
                "type": "http",
                "url": url,
                "method": method,
            },
            "assumptions": ["Resolved target from explicit METHOD URL hint."],
            "quickstart_constraints": constraints,
            "resolution_notes": (
                f"Resolved HTTP hint {target_hint!r}. User message: {user_message}"
            ),
        }

    return {
        "target": None,
        "execution_shape": None,
        "assumptions": [],
        "quickstart_constraints": constraints,
        "resolution_notes": "",
        "clarification_request": (
            "Stub preparation adapter requires a dotted callable or METHOD URL hint."
        ),
    }


def main() -> None:
    request = json.load(sys.stdin)
    operation = request["operation"]
    target = request.get("target", {})
    user_priorities = request.get("user_priorities", {})
    source_context = request.get("source_context", {})

    priority_ids = required_section_ids(user_priorities)
    first_source = first_or(source_context.get("explicit_sources", []), {"path": "n/a"})["path"]
    target_name = target.get("name", "target")

    if operation == "bootstrap":
        response = build_bootstrap_response(request)

    elif operation == "generate_directions":
        response = {"directions": build_directions(target_name, first_source, priority_ids)}

    elif operation == "generate_cases":
        response = {"cases": build_cases(target_name, first_source, priority_ids)}

    elif operation == "reconcile_readiness":
        existing_directions = request.get("directions") or []
        existing_cases = request.get("cases") or []
        if existing_directions:
            directions = strip_field(existing_directions, "human_instruction")
        else:
            directions = build_directions(target_name, first_source, priority_ids)
        if existing_cases:
            cases = strip_field(existing_cases, "human_instruction")
        else:
            cases = build_cases(target_name, first_source, priority_ids)
        response = {
            "directions": directions,
            "cases": cases,
            "run_ready": True,
            "readiness_note": "Stub adapter: deterministic planning, ready to run.",
        }

    else:
        print(f"Unknown operation: {operation}", file=sys.stderr)
        sys.exit(1)

    json.dump(response, sys.stdout)


if __name__ == "__main__":
    main()
