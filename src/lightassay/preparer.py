"""Subprocess-based preparation operations implementing the preparation adapter protocol.

See docs/preparation_protocol.md for the full specification.

Three operations:
- generate_directions: LLM reads brief, generates directions.
- generate_cases: LLM reads brief + directions + feedback, generates cases.
- reconcile_readiness: LLM reads full workbook, reconciles and sets RUN_READY.

Key design constraint: the adapter returns structured JSON data, NOT raw markdown.
Code converts the structured response into Workbook model mutations,
then the caller renders canonical markdown via workbook_renderer.render().

No fallback, no best-effort recovery.
"""

from __future__ import annotations

import json
import os
import re

from ._subprocess_capture import run_text_subprocess
from .errors import PreparationError
from .preparation_config import PreparationConfig
from .workbook_models import (
    Case,
    Direction,
    HumanFeedback,
    RunReadiness,
    Workbook,
)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BRIEF_HEADING_RE = re.compile(r"^###\s+(?P<heading>.+?)\s*$")
_IMPORT_RE = re.compile(r"^\s*import\s+(.+?)\s*(?:#.*)?$")
_FROM_IMPORT_RE = re.compile(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+.+$")
_MAX_DISCOVERED_SOURCES = 6

_BRIEF_SECTION_IDS = {
    "what is being tested": "what_is_being_tested",
    "what matters in the output": "what_matters_in_output",
    "aspects that are especially significant": "significant_aspects",
    "failure modes and problem classes that matter": "failure_modes",
    "what must not break": "must_not_break",
    "additional context (optional)": "additional_context",
}

_PRIORITY_LABELS = {
    "what is being tested": "scope",
    "what matters in the output": "evaluation_focus",
    "aspects that are especially significant": "priority_focus",
    "failure modes and problem classes that matter": "failure_modes",
    "what must not break": "must_not_break",
    "additional context (optional)": "context",
}


def _call_adapter(config: PreparationConfig, request_data: dict) -> dict:
    """Call the preparation adapter via subprocess and return the parsed JSON response.

    Raises PreparationError on any protocol violation.
    """
    command = config.invocation()

    # File-backed adapters: verify path is executable before the
    # subprocess call so the error message is specific.  Command-backed
    # adapters (e.g. built-in backends) skip this check — subprocess
    # will surface FileNotFoundError / PermissionError naturally.
    if not config.command:
        adapter = config.adapter
        if not os.path.exists(adapter):
            raise PreparationError(f"Preparation adapter not found: {adapter!r}")
        if not os.access(adapter, os.X_OK):
            raise PreparationError(f"Preparation adapter not executable: {adapter!r}")

    request_json = json.dumps(request_data, ensure_ascii=False)

    try:
        result = run_text_subprocess(
            command,
            input_text=request_json,
            env=config.subprocess_env(),
            live_stderr=bool(config.command),
        )
    except FileNotFoundError:
        raise PreparationError(f"Preparation adapter not found: {command[0]!r}") from None
    except PermissionError:
        raise PreparationError(f"Preparation adapter not executable: {command[0]!r}") from None

    if result.returncode != 0:
        raise PreparationError(
            f"Preparation adapter exited with code {result.returncode}: "
            f"{(result.stderr or '').strip()[:400]}"
        )

    stdout = result.stdout
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        raise PreparationError("Preparation adapter stdout is not valid JSON") from None

    if not isinstance(response, dict):
        raise PreparationError(
            f"Preparation adapter response must be a JSON object, got {type(response).__name__}"
        )

    return response


def _project_root(source_root: str | None) -> str:
    if source_root is None:
        return os.path.abspath(os.getcwd())
    return os.path.abspath(source_root)


def _to_relpath(path: str, project_root: str) -> str:
    try:
        rel = os.path.relpath(path, project_root)
    except ValueError:
        return path
    return rel if not rel.startswith("..") else path


def _strip_comments(text: str) -> str:
    return _HTML_COMMENT_RE.sub("", text)


def _slugify_heading(heading: str) -> str:
    lowered = heading.strip().lower()
    if lowered in _BRIEF_SECTION_IDS:
        return _BRIEF_SECTION_IDS[lowered]

    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    if not slug:
        raise PreparationError("Brief section heading cannot be empty")
    return slug


def _priority_label_for_heading(heading: str) -> str:
    return _PRIORITY_LABELS.get(heading.strip().lower(), "context")


def _extract_user_priority_sections(brief: str) -> list[dict]:
    cleaned = _strip_comments(brief)
    sections: list[dict] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    current_heading_supported = False
    saw_any_heading = False
    ordinal = 0

    def flush() -> None:
        nonlocal current_heading, current_lines, current_heading_supported, ordinal
        if current_heading is None:
            return
        if not current_heading_supported:
            current_heading = None
            current_lines = []
            current_heading_supported = False
            return
        sections.append(
            {
                "section_id": _slugify_heading(current_heading),
                "heading": current_heading,
                "text": "\n".join(line.rstrip() for line in current_lines).strip(),
                "priority_label": _priority_label_for_heading(current_heading),
                "ordinal": ordinal,
            }
        )
        ordinal += 1
        current_heading = None
        current_lines = []

    for raw_line in cleaned.splitlines():
        heading_match = _BRIEF_HEADING_RE.match(raw_line.strip())
        if heading_match:
            saw_any_heading = True
            flush()
            current_heading = heading_match.group("heading")
            current_heading_supported = current_heading.strip().lower() in _BRIEF_SECTION_IDS
            continue
        if current_heading is None:
            continue
        current_lines.append(raw_line)

    flush()

    if sections:
        return [section for section in sections if section["text"].strip()]

    if saw_any_heading:
        return []

    freeform = cleaned.strip()
    if not freeform:
        return []

    return [
        {
            "section_id": "freeform_brief",
            "heading": "Brief",
            "text": freeform,
            "priority_label": "freeform",
            "ordinal": 0,
        }
    ]


def _build_user_priorities(brief: str) -> dict:
    return {
        "input_mode": "natural_language",
        "raw_brief": brief,
        "sections": _extract_user_priority_sections(brief),
    }


def _resolve_explicit_source(source: str, project_root: str) -> str:
    resolved = source if os.path.isabs(source) else os.path.join(project_root, source)
    resolved = os.path.abspath(resolved)
    if not os.path.isfile(resolved):
        raise PreparationError(
            f"Target source reference does not resolve to a readable file: {source!r}"
        )
    return resolved


def _module_candidates(module_path: str, project_root: str) -> list[str]:
    module_fs = module_path.replace(".", os.sep)
    return [
        os.path.abspath(os.path.join(project_root, module_fs + ".py")),
        os.path.abspath(os.path.join(project_root, module_fs, "__init__.py")),
        os.path.abspath(os.path.join(project_root, "src", module_fs + ".py")),
        os.path.abspath(os.path.join(project_root, "src", module_fs, "__init__.py")),
    ]


def _resolve_locator_source(locator: str, project_root: str) -> str | None:
    locator = locator.strip()
    if not locator:
        return None

    file_candidate = locator.split("::", 1)[0].strip()
    if file_candidate.endswith(".py"):
        resolved = (
            file_candidate
            if os.path.isabs(file_candidate)
            else os.path.join(project_root, file_candidate)
        )
        resolved = os.path.abspath(resolved)
        return resolved if os.path.isfile(resolved) else None

    if "/" in locator or "\\" in locator:
        resolved = os.path.abspath(os.path.join(project_root, locator))
        return resolved if os.path.isfile(resolved) else None

    parts = locator.split(".")
    if len(parts) < 2:
        return None

    for length in range(len(parts) - 1, 0, -1):
        module_path = ".".join(parts[:length])
        for candidate in _module_candidates(module_path, project_root):
            if os.path.isfile(candidate):
                return candidate
    return None


def _candidate_import_paths(
    module_path: str, project_root: str, anchor_prefixes: list[str]
) -> list[str]:
    candidates = _module_candidates(module_path, project_root)
    module_fs = module_path.replace(".", os.sep)
    for prefix in anchor_prefixes:
        candidates.append(os.path.abspath(os.path.join(project_root, prefix, module_fs + ".py")))
        candidates.append(
            os.path.abspath(os.path.join(project_root, prefix, module_fs, "__init__.py"))
        )
    return candidates


def _extract_import_modules(source_text: str) -> list[str]:
    modules: list[str] = []
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        from_match = _FROM_IMPORT_RE.match(line)
        if from_match:
            modules.append(from_match.group(1))
            continue

        import_match = _IMPORT_RE.match(line)
        if import_match:
            for chunk in import_match.group(1).split(","):
                module_name = chunk.strip().split(" as ", 1)[0].strip()
                if module_name:
                    modules.append(module_name)
    return modules


def _discover_related_sources(
    anchor_paths: list[str],
    project_root: str,
    seen_paths: list[str],
) -> list[dict]:
    discovered: list[dict] = []
    seen = set(seen_paths)
    anchor_prefixes = sorted(
        {
            _to_relpath(path, project_root).split(os.sep, 1)[0]
            for path in anchor_paths
            if _to_relpath(path, project_root) != path
        }
    )

    for anchor_path in anchor_paths:
        with open(anchor_path, encoding="utf-8") as fh:
            source_text = fh.read()

        for module_name in _extract_import_modules(source_text):
            for candidate in _candidate_import_paths(module_name, project_root, anchor_prefixes):
                if len(discovered) >= _MAX_DISCOVERED_SOURCES:
                    return discovered
                if candidate in seen or not os.path.isfile(candidate):
                    continue
                if os.path.basename(candidate) == "__init__.py":
                    continue

                seen.add(candidate)
                with open(candidate, encoding="utf-8") as discovered_fh:
                    discovered.append(
                        {
                            "path": _to_relpath(candidate, project_root),
                            "reason": (
                                f"discovered from import {module_name!r} in "
                                f"{_to_relpath(anchor_path, project_root)}"
                            ),
                            "content": discovered_fh.read(),
                        }
                    )
                break

    return discovered


def _build_source_context(workbook: Workbook, source_root: str | None) -> dict:
    project_root = _project_root(source_root)
    explicit_paths: list[str] = []
    explicit_entries: list[dict] = []

    for source in workbook.target.sources:
        resolved = _resolve_explicit_source(source, project_root)
        explicit_paths.append(resolved)
        with open(resolved, encoding="utf-8") as fh:
            explicit_entries.append(
                {
                    "path": _to_relpath(resolved, project_root),
                    "reason": "explicit target source",
                    "content": fh.read(),
                }
            )

    anchor_paths = list(explicit_paths)
    discovered_entries: list[dict] = []
    locator_source = _resolve_locator_source(workbook.target.locator, project_root)
    if locator_source is not None and locator_source not in explicit_paths:
        anchor_paths.append(locator_source)
        with open(locator_source, encoding="utf-8") as fh:
            discovered_entries.append(
                {
                    "path": _to_relpath(locator_source, project_root),
                    "reason": "resolved from TARGET_LOCATOR",
                    "content": fh.read(),
                }
            )

    discovered_entries.extend(
        _discover_related_sources(
            anchor_paths,
            project_root,
            explicit_paths + ([locator_source] if locator_source is not None else []),
        )
    )

    return {
        "project_root": project_root,
        "discovery_mode": "bounded_target_anchored",
        "explicit_sources": explicit_entries,
        "discovered_sources": discovered_entries,
    }


def _serialize_target_for_adapter(workbook: Workbook) -> dict:
    target = workbook.target
    return {
        "kind": target.kind,
        "name": target.name,
        "locator": target.locator,
        "boundary": target.boundary,
        "sources": list(target.sources),
        "notes": target.notes,
    }


def _required_user_priority_sections(workbook: Workbook) -> list[dict]:
    sections = _build_user_priorities(workbook.brief)["sections"]
    prioritized = [
        section
        for section in sections
        if section["text"].strip() and section["priority_label"] != "context"
    ]
    if prioritized:
        return prioritized
    return [section for section in sections if section["text"].strip()]


def _validate_priority_conflicts(
    response: dict,
    *,
    required_section_ids: set[str],
) -> dict[str, dict]:
    conflicts = response.get("priority_conflicts", [])
    if not isinstance(conflicts, list):
        raise PreparationError(
            "Preparation adapter response field 'priority_conflicts' must be a list"
        )

    seen: set[str] = set()
    normalized: dict[str, dict] = {}
    for index, conflict in enumerate(conflicts):
        if not isinstance(conflict, dict):
            raise PreparationError(
                f"Preparation adapter response: priority_conflicts[{index}] must be an object"
            )
        for field in ("section_id", "reason", "source_rationale"):
            if field not in conflict:
                raise PreparationError(
                    f"Preparation adapter response: priority_conflicts[{index}] missing required field: {field!r}"  # noqa: E501
                )
            if not isinstance(conflict[field], str) or not conflict[field].strip():
                raise PreparationError(
                    f"Preparation adapter response: priority_conflicts[{index}].{field} must be a non-empty string"  # noqa: E501
                )
        section_id = conflict["section_id"].strip()
        if section_id in seen:
            raise PreparationError(
                f"Preparation adapter response: duplicate priority conflict for section_id {section_id!r}"  # noqa: E501
            )
        if section_id not in required_section_ids:
            raise PreparationError(
                f"Preparation adapter response: priority conflict references unknown or non-required section_id {section_id!r}"  # noqa: E501
            )
        seen.add(section_id)
        normalized[section_id] = {
            "reason": conflict["reason"].strip(),
            "source_rationale": conflict["source_rationale"].strip(),
        }

    return normalized


def _parse_covered_priority_sections(
    value,
    *,
    owner: str,
    required_section_ids: set[str],
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PreparationError(
            f"Preparation adapter response: {owner}.covered_user_priority_sections must be a non-empty list"  # noqa: E501
        )

    seen: set[str] = set()
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise PreparationError(
                f"Preparation adapter response: {owner}.covered_user_priority_sections[{index}] must be a non-empty string"  # noqa: E501
            )
        section_id = item.strip()
        if section_id in seen:
            raise PreparationError(
                f"Preparation adapter response: {owner}.covered_user_priority_sections contains duplicate section_id {section_id!r}"  # noqa: E501
            )
        if section_id not in required_section_ids:
            raise PreparationError(
                f"Preparation adapter response: {owner}.covered_user_priority_sections references unknown or non-required section_id {section_id!r}"  # noqa: E501
            )
        seen.add(section_id)
        result.append(section_id)
    return result


def _validate_coverage(
    *,
    owner: str,
    entities: list[dict],
    required_section_ids: set[str],
    conflicts: dict[str, dict],
) -> None:
    covered: set[str] = set()
    for entity in entities:
        covered.update(entity["covered_user_priority_sections"])

    uncovered = sorted(
        section_id
        for section_id in required_section_ids
        if section_id not in covered and section_id not in conflicts
    )
    if uncovered:
        raise PreparationError(
            f"{owner} did not cover required user priority sections and did not declare explicit conflicts: {', '.join(uncovered)}"  # noqa: E501
        )


# ── generate_directions ──────────────────────────────────────────────────────


def _validate_directions_response(response: dict) -> list[dict]:
    """Validate the adapter response for generate_directions.

    Required shape:
    {
        "directions": [
            {
                "direction_id": "...",
                "body": "...",
                "behavior_facet": "...",
                "testing_lens": "...",
                "covered_user_priority_sections": ["section_id"],
                "source_rationale": "..."
            },
            ...
        ],
        "priority_conflicts": [...]
    }

    Returns the validated list of direction dicts.
    Raises PreparationError on any violation.
    """
    if "directions" not in response:
        raise PreparationError("Preparation adapter response missing required field: 'directions'")

    directions = response["directions"]
    if not isinstance(directions, list):
        raise PreparationError(
            f"Preparation adapter response field 'directions' must be a list, "
            f"got {type(directions).__name__}"
        )

    if len(directions) == 0:
        raise PreparationError("Preparation adapter response field 'directions' must be non-empty")

    seen_ids: set = set()
    for i, d in enumerate(directions):
        if not isinstance(d, dict):
            raise PreparationError(
                f"Preparation adapter response: directions[{i}] must be an object, "
                f"got {type(d).__name__}"
            )
        for field in ("direction_id", "body", "behavior_facet", "testing_lens", "source_rationale"):
            if field not in d:
                raise PreparationError(
                    f"Preparation adapter response: directions[{i}] missing "
                    f"required field: {field!r}"
                )
            if not isinstance(d[field], str):
                raise PreparationError(
                    f"Preparation adapter response: directions[{i}].{field} "
                    f"must be a string, got {type(d[field]).__name__}"
                )
            if not d[field].strip():
                raise PreparationError(
                    f"Preparation adapter response: directions[{i}].{field} must be non-empty"
                )
        if "covered_user_priority_sections" not in d:
            raise PreparationError(
                f"Preparation adapter response: directions[{i}] missing required field: 'covered_user_priority_sections'"  # noqa: E501
            )
        did = d["direction_id"]
        if did in seen_ids:
            raise PreparationError(f"Preparation adapter response: duplicate direction_id {did!r}")
        seen_ids.add(did)

    return directions


def _check_artifact_references(workbook: Workbook, operation: str) -> None:
    """Reject preparation if workbook already points at downstream artifacts.

    Any non-None artifact reference (run, analysis, compare) means the workbook
    has progressed past preparation. Mutating it would leave those artifact
    references pointing at stale state.

    Raises PreparationError if any artifact reference is set.
    """
    refs = workbook.artifact_references
    populated = []
    if refs.run is not None:
        populated.append(f"run={refs.run!r}")
    if refs.analysis is not None:
        populated.append(f"analysis={refs.analysis!r}")
    if refs.compare is not None:
        populated.append(f"compare={refs.compare!r}")
    if populated:
        raise PreparationError(
            f"Cannot {operation}: workbook already has artifact references "
            f"({', '.join(populated)}). Mutating a workbook that points at "
            f"downstream artifacts would leave them referencing stale state. "
            f"Start a new workbook for a fresh preparation pass."
        )


def execute_generate_directions(
    workbook: Workbook,
    preparation_config: PreparationConfig,
    source_root: str | None = None,
    planning_mode: str = "full",
    planning_context: dict | None = None,
) -> Workbook:
    """Call the adapter to generate directions from the brief.

    Mutates workbook.directions with the generated directions.
    Each direction gets an empty HUMAN:instruction (awaiting human feedback).

    Rejects if:
    - Directions already exist (same-stage re-entry would silently discard
      human feedback on existing directions).
    - Downstream derived state (cases, run_ready) already exists,
      because overwriting directions would leave that state semantically stale.
    - Artifact references are set (run/analysis/compare artifacts would
      become stale).

    Returns the updated Workbook.
    Raises PreparationError on any contract violation.
    """
    _check_artifact_references(workbook, "generate directions")

    if workbook.directions:
        raise PreparationError(
            "Cannot generate directions: workbook already has directions. "
            "Re-generating would silently discard existing directions and "
            "any human feedback on them. Start a new workbook for a fresh "
            "preparation pass."
        )
    if workbook.cases:
        raise PreparationError(
            "Cannot generate directions: workbook already has cases. "
            "Overwriting directions would leave existing cases referencing "
            "stale direction IDs. Remove cases first or start a new workbook."
        )
    if workbook.run_readiness.run_ready:
        raise PreparationError(
            "Cannot generate directions: workbook has RUN_READY: yes. "
            "Overwriting directions would invalidate the readiness state. "
            "Start a new workbook for a fresh preparation pass."
        )

    request_data = {
        "operation": "generate_directions",
        "brief": workbook.brief,
        "planning_mode": planning_mode,
        **_build_preparation_request_context(workbook, source_root),
    }
    if planning_context is not None:
        request_data["planning_context"] = planning_context

    response = _call_adapter(preparation_config, request_data)
    direction_dicts = _validate_directions_response(response)
    required_sections = _required_user_priority_sections(workbook)
    required_section_ids = {section["section_id"] for section in required_sections}
    conflicts = _validate_priority_conflicts(
        response,
        required_section_ids=required_section_ids,
    )

    new_directions: list[Direction] = []
    normalized_direction_dicts: list[dict] = []
    for index, d in enumerate(direction_dicts):
        owner = f"directions[{index}]"
        normalized_covered = _parse_covered_priority_sections(
            d["covered_user_priority_sections"],
            owner=owner,
            required_section_ids=required_section_ids,
        )
        normalized_direction_dict = {
            "direction_id": d["direction_id"],
            "body": d["body"],
            "behavior_facet": d["behavior_facet"],
            "testing_lens": d["testing_lens"],
            "covered_user_priority_sections": normalized_covered,
            "source_rationale": d["source_rationale"],
        }
        normalized_direction_dicts.append(normalized_direction_dict)
        new_directions.append(
            Direction(
                direction_id=normalized_direction_dict["direction_id"],
                body=normalized_direction_dict["body"],
                behavior_facet=normalized_direction_dict["behavior_facet"],
                testing_lens=normalized_direction_dict["testing_lens"],
                covered_user_priority_sections=normalized_direction_dict[
                    "covered_user_priority_sections"
                ],
                source_rationale=normalized_direction_dict["source_rationale"],
                human_instruction=HumanFeedback(""),
            )
        )

    _validate_coverage(
        owner="Directions generation",
        entities=normalized_direction_dicts,
        required_section_ids=required_section_ids,
        conflicts=conflicts,
    )

    workbook.directions = new_directions
    return workbook


# ── generate_cases ───────────────────────────────────────────────────────────


def _serialize_directions_for_adapter(workbook: Workbook) -> list:
    """Serialize directions + human feedback for the adapter request."""
    result = []
    for d in workbook.directions:
        result.append(
            {
                "direction_id": d.direction_id,
                "body": d.body,
                "behavior_facet": d.behavior_facet,
                "testing_lens": d.testing_lens,
                "covered_user_priority_sections": d.covered_user_priority_sections,
                "source_rationale": d.source_rationale,
                "human_instruction": d.human_instruction.text,
            }
        )
    return result


def _build_preparation_request_context(workbook: Workbook, source_root: str | None) -> dict:
    return {
        "target": _serialize_target_for_adapter(workbook),
        "user_priorities": _build_user_priorities(workbook.brief),
        "source_context": _build_source_context(workbook, source_root),
    }


def _validate_cases_response(response: dict) -> list[dict]:
    """Validate the adapter response for generate_cases.

    Required shape:
    {
        "cases": [
            {
                "case_id": "...",
                "input": "...",
                "target_directions": ["dir1", "dir2"],
                "expected_behavior": "...",
                "behavior_facet": "...",
                "testing_lens": "...",
                "covered_user_priority_sections": ["section_id"],
                "source_rationale": "...",
                "context": "..." | null,
                "notes": "..." | null
            },
            ...
        ],
        "priority_conflicts": [...]
    }

    Returns the validated list of case dicts.
    Raises PreparationError on any violation.
    """
    if "cases" not in response:
        raise PreparationError("Preparation adapter response missing required field: 'cases'")

    cases = response["cases"]
    if not isinstance(cases, list):
        raise PreparationError(
            f"Preparation adapter response field 'cases' must be a list, got {type(cases).__name__}"
        )

    if len(cases) == 0:
        raise PreparationError("Preparation adapter response field 'cases' must be non-empty")

    seen_ids: set = set()
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            raise PreparationError(
                f"Preparation adapter response: cases[{i}] must be an object, "
                f"got {type(c).__name__}"
            )

        # Required string fields.
        for field in (
            "case_id",
            "input",
            "expected_behavior",
            "behavior_facet",
            "testing_lens",
            "source_rationale",
        ):
            if field not in c:
                raise PreparationError(
                    f"Preparation adapter response: cases[{i}] missing required field: {field!r}"
                )
            if not isinstance(c[field], str):
                raise PreparationError(
                    f"Preparation adapter response: cases[{i}].{field} "
                    f"must be a string, got {type(c[field]).__name__}"
                )
            if not c[field].strip():
                raise PreparationError(
                    f"Preparation adapter response: cases[{i}].{field} must be non-empty"
                )
        if "covered_user_priority_sections" not in c:
            raise PreparationError(
                f"Preparation adapter response: cases[{i}] missing required field: 'covered_user_priority_sections'"  # noqa: E501
            )

        # target_directions: required, non-empty list of strings.
        if "target_directions" not in c:
            raise PreparationError(
                f"Preparation adapter response: cases[{i}] missing "
                f"required field: 'target_directions'"
            )
        tds = c["target_directions"]
        if not isinstance(tds, list):
            raise PreparationError(
                f"Preparation adapter response: cases[{i}].target_directions "
                f"must be a list, got {type(tds).__name__}"
            )
        if len(tds) == 0:
            raise PreparationError(
                f"Preparation adapter response: cases[{i}].target_directions must be non-empty"
            )
        for j, td in enumerate(tds):
            if not isinstance(td, str) or not td.strip():
                raise PreparationError(
                    f"Preparation adapter response: cases[{i}].target_directions[{j}] "
                    f"must be a non-empty string"
                )

        # Optional nullable fields: context, notes.
        for field in ("context", "notes"):
            if field in c:
                val = c[field]
                if val is not None and not isinstance(val, str):
                    raise PreparationError(
                        f"Preparation adapter response: cases[{i}].{field} "
                        f"must be a string or null, got {type(val).__name__}"
                    )

        cid = c["case_id"]
        if cid in seen_ids:
            raise PreparationError(f"Preparation adapter response: duplicate case_id {cid!r}")
        seen_ids.add(cid)

    return cases


def execute_generate_cases(
    workbook: Workbook,
    preparation_config: PreparationConfig,
    source_root: str | None = None,
    planning_mode: str = "full",
    planning_context: dict | None = None,
) -> Workbook:
    """Call the adapter to generate cases from brief + directions + feedback.

    Mutates workbook.cases with the generated cases.
    Each case gets an empty HUMAN:instruction (awaiting human feedback).

    Cross-references target_directions against existing direction IDs.

    Rejects if:
    - Cases already exist (same-stage re-entry would silently discard
      human feedback on existing cases).
    - Downstream derived state (run_ready) already exists,
      because overwriting cases would leave that state semantically stale.
    - Artifact references are set (run/analysis/compare artifacts would
      become stale).

    Returns the updated Workbook.
    Raises PreparationError on any contract violation.
    """
    _check_artifact_references(workbook, "generate cases")

    if workbook.cases:
        raise PreparationError(
            "Cannot generate cases: workbook already has cases. "
            "Re-generating would silently discard existing cases and "
            "any human feedback on them. Start a new workbook for a fresh "
            "preparation pass."
        )
    if workbook.run_readiness.run_ready:
        raise PreparationError(
            "Cannot generate cases: workbook has RUN_READY: yes. "
            "Overwriting cases would invalidate the readiness state. "
            "Start a new workbook for a fresh preparation pass."
        )

    request_data = {
        "operation": "generate_cases",
        "brief": workbook.brief,
        "planning_mode": planning_mode,
        "directions_global_instruction": workbook.directions_global_instruction.text,
        "directions": _serialize_directions_for_adapter(workbook),
        **_build_preparation_request_context(workbook, source_root),
    }
    if planning_context is not None:
        request_data["planning_context"] = planning_context

    response = _call_adapter(preparation_config, request_data)
    case_dicts = _validate_cases_response(response)
    required_sections = _required_user_priority_sections(workbook)
    required_section_ids = {section["section_id"] for section in required_sections}
    conflicts = _validate_priority_conflicts(
        response,
        required_section_ids=required_section_ids,
    )

    # Cross-reference target_directions against existing direction IDs.
    direction_ids = {d.direction_id for d in workbook.directions}
    for c in case_dicts:
        for td in c["target_directions"]:
            if td not in direction_ids:
                raise PreparationError(
                    f"Preparation adapter response: case {c['case_id']!r} references "
                    f"target direction {td!r} which does not exist in the workbook"
                )

    new_cases: list[Case] = []
    normalized_case_dicts: list[dict] = []
    for index, c in enumerate(case_dicts):
        owner = f"cases[{index}]"
        normalized_covered = _parse_covered_priority_sections(
            c["covered_user_priority_sections"],
            owner=owner,
            required_section_ids=required_section_ids,
        )
        normalized_case_dict = {
            "case_id": c["case_id"],
            "input": c["input"],
            "target_directions": c["target_directions"],
            "expected_behavior": c["expected_behavior"],
            "behavior_facet": c["behavior_facet"],
            "testing_lens": c["testing_lens"],
            "covered_user_priority_sections": normalized_covered,
            "source_rationale": c["source_rationale"],
            "context": c.get("context"),
            "notes": c.get("notes"),
        }
        normalized_case_dicts.append(normalized_case_dict)
        new_cases.append(
            Case(
                case_id=normalized_case_dict["case_id"],
                input=normalized_case_dict["input"],
                target_directions=normalized_case_dict["target_directions"],
                expected_behavior=normalized_case_dict["expected_behavior"],
                behavior_facet=normalized_case_dict["behavior_facet"],
                testing_lens=normalized_case_dict["testing_lens"],
                covered_user_priority_sections=normalized_case_dict[
                    "covered_user_priority_sections"
                ],
                source_rationale=normalized_case_dict["source_rationale"],
                context=normalized_case_dict["context"],
                notes=normalized_case_dict["notes"],
                human_instruction=HumanFeedback(""),
            )
        )

    _validate_coverage(
        owner="Cases generation",
        entities=normalized_case_dicts,
        required_section_ids=required_section_ids,
        conflicts=conflicts,
    )

    workbook.cases = new_cases
    return workbook


# ── reconcile_readiness ──────────────────────────────────────────────────────


def _serialize_cases_for_adapter(workbook: Workbook) -> list:
    """Serialize cases + human feedback for the adapter request."""
    result = []
    for c in workbook.cases:
        result.append(
            {
                "case_id": c.case_id,
                "input": c.input,
                "target_directions": c.target_directions,
                "expected_behavior": c.expected_behavior,
                "behavior_facet": c.behavior_facet,
                "testing_lens": c.testing_lens,
                "covered_user_priority_sections": c.covered_user_priority_sections,
                "source_rationale": c.source_rationale,
                "context": c.context,
                "notes": c.notes,
                "human_instruction": c.human_instruction.text,
            }
        )
    return result


def _validate_readiness_response(response: dict) -> tuple[list[dict], list[dict], bool, str]:
    """Validate the adapter response for reconcile_readiness.

    Required shape:
    {
        "directions": [...],  # same shape as generate_directions
        "cases": [...],       # same shape as generate_cases
        "run_ready": true|false,
        "readiness_note": "..."
    }

    Returns (direction_dicts, case_dicts, run_ready, readiness_note).
    Raises PreparationError on any violation.
    """
    # Validate directions.
    direction_dicts = _validate_directions_response(response)

    # Validate cases.
    case_dicts = _validate_cases_response(response)

    # Validate run_ready.
    if "run_ready" not in response:
        raise PreparationError("Preparation adapter response missing required field: 'run_ready'")
    run_ready = response["run_ready"]
    if not isinstance(run_ready, bool):
        raise PreparationError(
            f"Preparation adapter response field 'run_ready' must be a boolean, "
            f"got {type(run_ready).__name__}"
        )

    # Validate readiness_note.
    if "readiness_note" not in response:
        raise PreparationError(
            "Preparation adapter response missing required field: 'readiness_note'"
        )
    readiness_note = response["readiness_note"]
    if not isinstance(readiness_note, str):
        raise PreparationError(
            f"Preparation adapter response field 'readiness_note' must be a string, "
            f"got {type(readiness_note).__name__}"
        )

    # When run_ready is false, readiness_note must explain why.
    # This is the reconcile_readiness response contract (see flow_v1.md Step 4,
    # workbook_spec.md Run readiness contract), not the workbook grammar rule.
    # The workbook grammar allows empty READINESS_NOTE for fresh/init state,
    # but the adapter must provide a reason when it actively decides not-ready.
    if not run_ready and not readiness_note.strip():
        raise PreparationError(
            "Preparation adapter response: readiness_note must be non-empty "
            "when run_ready is false — the adapter must explain why the "
            "workbook is not ready"
        )

    return direction_dicts, case_dicts, run_ready, readiness_note


def execute_reconcile_readiness(
    workbook: Workbook,
    preparation_config: PreparationConfig,
    source_root: str | None = None,
    planning_mode: str = "full",
    planning_context: dict | None = None,
) -> Workbook:
    """Call the adapter to reconcile the workbook and set RUN_READY.

    The adapter receives the full workbook state (brief, directions with feedback,
    cases with feedback) and returns reconciled directions, cases, and readiness.

    Mutates workbook.directions, workbook.cases, and workbook.run_readiness.

    Rejects if artifact references are set (run/analysis/compare artifacts would
    become stale after reconciliation mutates directions/cases/readiness).

    Returns the updated Workbook.
    Raises PreparationError on any contract violation.
    """
    _check_artifact_references(workbook, "reconcile readiness")

    request_data = {
        "operation": "reconcile_readiness",
        "brief": workbook.brief,
        "planning_mode": planning_mode,
        "directions_global_instruction": workbook.directions_global_instruction.text,
        "directions": _serialize_directions_for_adapter(workbook),
        "cases_global_instruction": workbook.cases_global_instruction.text,
        "cases": _serialize_cases_for_adapter(workbook),
        **_build_preparation_request_context(workbook, source_root),
    }
    if planning_context is not None:
        request_data["planning_context"] = planning_context

    response = _call_adapter(preparation_config, request_data)
    direction_dicts, case_dicts, run_ready, readiness_note = _validate_readiness_response(response)
    required_sections = _required_user_priority_sections(workbook)
    required_section_ids = {section["section_id"] for section in required_sections}
    conflicts = _validate_priority_conflicts(
        response,
        required_section_ids=required_section_ids,
    )

    # Cross-reference case target_directions against returned direction IDs.
    direction_ids = {d["direction_id"] for d in direction_dicts}
    for c in case_dicts:
        for td in c["target_directions"]:
            if td not in direction_ids:
                raise PreparationError(
                    f"Preparation adapter response: case {c['case_id']!r} references "
                    f"target direction {td!r} which does not exist in the "
                    f"reconciled directions"
                )

    # Build new directions (feedback reset to empty — human reviews again).
    new_directions: list[Direction] = []
    normalized_direction_dicts: list[dict] = []
    for index, d in enumerate(direction_dicts):
        owner = f"directions[{index}]"
        normalized_covered = _parse_covered_priority_sections(
            d["covered_user_priority_sections"],
            owner=owner,
            required_section_ids=required_section_ids,
        )
        normalized_direction = {
            "direction_id": d["direction_id"],
            "body": d["body"],
            "behavior_facet": d["behavior_facet"],
            "testing_lens": d["testing_lens"],
            "covered_user_priority_sections": normalized_covered,
            "source_rationale": d["source_rationale"],
        }
        normalized_direction_dicts.append(normalized_direction)
        new_directions.append(
            Direction(
                direction_id=normalized_direction["direction_id"],
                body=normalized_direction["body"],
                behavior_facet=normalized_direction["behavior_facet"],
                testing_lens=normalized_direction["testing_lens"],
                covered_user_priority_sections=normalized_direction[
                    "covered_user_priority_sections"
                ],
                source_rationale=normalized_direction["source_rationale"],
                human_instruction=HumanFeedback(""),
            )
        )

    _validate_coverage(
        owner="Readiness reconciliation directions",
        entities=normalized_direction_dicts,
        required_section_ids=required_section_ids,
        conflicts=conflicts,
    )

    # Build new cases (feedback reset to empty — human reviews again).
    new_cases: list[Case] = []
    normalized_case_dicts: list[dict] = []
    for index, c in enumerate(case_dicts):
        owner = f"cases[{index}]"
        normalized_covered = _parse_covered_priority_sections(
            c["covered_user_priority_sections"],
            owner=owner,
            required_section_ids=required_section_ids,
        )
        normalized_case = {
            "case_id": c["case_id"],
            "input": c["input"],
            "target_directions": c["target_directions"],
            "expected_behavior": c["expected_behavior"],
            "behavior_facet": c["behavior_facet"],
            "testing_lens": c["testing_lens"],
            "covered_user_priority_sections": normalized_covered,
            "source_rationale": c["source_rationale"],
            "context": c.get("context"),
            "notes": c.get("notes"),
        }
        normalized_case_dicts.append(normalized_case)
        new_cases.append(
            Case(
                case_id=normalized_case["case_id"],
                input=normalized_case["input"],
                target_directions=normalized_case["target_directions"],
                expected_behavior=normalized_case["expected_behavior"],
                behavior_facet=normalized_case["behavior_facet"],
                testing_lens=normalized_case["testing_lens"],
                covered_user_priority_sections=normalized_case["covered_user_priority_sections"],
                source_rationale=normalized_case["source_rationale"],
                context=normalized_case["context"],
                notes=normalized_case["notes"],
                human_instruction=HumanFeedback(""),
            )
        )

    _validate_coverage(
        owner="Readiness reconciliation cases",
        entities=normalized_case_dicts,
        required_section_ids=required_section_ids,
        conflicts=conflicts,
    )

    workbook.directions = new_directions
    workbook.cases = new_cases
    workbook.run_readiness = RunReadiness(
        run_ready=run_ready,
        readiness_note=readiness_note,
    )

    return workbook
