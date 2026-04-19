"""Strict workbook parser.

Raises WorkbookParseError on any grammar violation.
No fallback behavior. No best-effort parsing.
See docs/workbook_grammar.md for the canonical grammar specification.
"""

from __future__ import annotations

import re
from typing import Callable

from .errors import WorkbookParseError
from .workbook_models import (
    ArtifactReferences,
    Case,
    ContinuationBlock,
    ContinuationFields,
    Direction,
    HistoricalContinuation,
    HumanFeedback,
    RunReadiness,
    Target,
    Workbook,
)

# ── Compiled patterns ─────────────────────────────────────────────────────────

_H1_WORKBOOK = re.compile(r"^# Eval Workbook\s*$")
_H2_SECTION = re.compile(r"^## (.+?)\s*$")
_H3_BLOCK = re.compile(r"^### (.+?)\s*$")
_HUMAN_INSTRUCTION_LINE = re.compile(r"^HUMAN:instruction\s*$")
_RUN_READY_LINE = re.compile(r"^RUN_READY: (yes|no)\s*$")
_READINESS_NOTE_LINE = re.compile(r"^READINESS_NOTE:(.*)")
_ARTIFACT_REF_LINE = re.compile(r"^- (run|analysis|compare):\s*(.*)")
_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DIRECTION_HEADER = re.compile(r"^Direction: (\S+)$")
_CASE_HEADER = re.compile(r"^Case: (\S+)$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

_CONTINUATION_CURRENT_RE = re.compile(
    r"^Current continuation: (general instruction|direction instruction|case instruction)$"
)
_CONTINUATION_HISTORY_RE = re.compile(
    r"^Continuation v(\d+): "
    r"(general instruction|direction instruction|case instruction|CLI message)$"
)
_CONTINUATION_SLOT_KEYS = {
    "general instruction": "general_instruction",
    "direction instruction": "direction_instruction",
    "case instruction": "case_instruction",
}

# Case field headers — exact line matches (rstrip applied before comparison)
_CASE_MULTILINE_FIELDS: dict[str, str] = {
    "**Input:**": "input",
    "**Context:**": "context",
    "**Notes:**": "notes",
    "**Expected behavior:**": "expected_behavior",
}
_CASE_TARGET_PAT = re.compile(r"^\*\*Target directions:\*\* (.+?)\s*$")
_CASE_TARGET_EMPTY_PAT = re.compile(r"^\*\*Target directions:\*\*\s*$")
_TRACE_LINE_PATTERNS = {
    "behavior_facet": re.compile(r"^\*\*Behavior facet:\*\* (.+?)\s*$"),
    "testing_lens": re.compile(r"^\*\*Testing lens:\*\* (.+?)\s*$"),
    "covered_user_priority_sections": re.compile(r"^\*\*Covered user priorities:\*\* (.+?)\s*$"),
    "source_rationale": re.compile(r"^\*\*Source rationale:\*\* (.+?)\s*$"),
}

_REQUIRED_SECTIONS = [
    "Target",
    "Brief",
    "Directions",
    "Cases",
    "Run readiness",
    "Artifact references",
]

_TARGET_HEADERS = [
    "TARGET_KIND",
    "TARGET_NAME",
    "TARGET_LOCATOR",
    "TARGET_BOUNDARY",
    "TARGET_SOURCES",
    "TARGET_NOTES",
]

# ── Public API ────────────────────────────────────────────────────────────────


def parse(text: str) -> Workbook:
    """Parse a workbook markdown string into a Workbook domain model.

    Raises WorkbookParseError if the text violates the grammar contract
    (see docs/workbook_grammar.md).
    """
    lines = text.splitlines()

    _require_title(lines)
    sections = _split_h2_sections(lines)

    for name in _REQUIRED_SECTIONS:
        if name not in sections:
            raise WorkbookParseError(f"Missing required section: '## {name}'")

    target = _parse_target_section(sections["Target"])
    brief = _parse_brief(sections["Brief"])
    directions_global, directions = _parse_directions_section(sections["Directions"])
    cases_global, cases = _parse_cases_section(sections["Cases"])
    run_readiness = _parse_run_readiness(sections["Run readiness"])
    artifact_refs = _parse_artifact_references(sections["Artifact references"])
    continuation = _parse_continuation_section(sections.get("Continue Next Run"))

    # Cross-reference: case target_directions must reference existing direction IDs.
    direction_ids = {d.direction_id for d in directions}
    for case in cases:
        for tid in case.target_directions:
            if tid not in direction_ids:
                raise WorkbookParseError(
                    f"Case '{case.case_id}': target direction '{tid}' "
                    f"does not exist in the '## Directions' section"
                )

    return Workbook(
        target=target,
        brief=brief,
        directions_global_instruction=directions_global,
        directions=directions,
        cases_global_instruction=cases_global,
        cases=cases,
        run_readiness=run_readiness,
        artifact_references=artifact_refs,
        continuation=continuation,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


def _require_title(lines: list[str]) -> None:
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            continue
        if _H1_WORKBOOK.match(stripped):
            return
        raise WorkbookParseError(f"Workbook must begin with '# Eval Workbook'; found: {stripped!r}")
    raise WorkbookParseError("Workbook is empty or missing '# Eval Workbook' title line")


def _split_h2_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _H2_SECTION.match(line)
        if m:
            name = m.group(1)
            if current_name is not None:
                if current_name in sections:
                    raise WorkbookParseError(f"Duplicate section: '## {current_name}'")
                sections[current_name] = current_lines
            current_name = name
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        if current_name in sections:
            raise WorkbookParseError(f"Duplicate section: '## {current_name}'")
        sections[current_name] = current_lines

    return sections


def _split_h3_blocks(
    lines: list[str],
    *,
    header_filter: Callable[[str], bool] | None = None,
) -> list[tuple[str, list[str]]]:
    """Split lines into [(header_text, content_lines), ...] by H3 headers.

    Lines before the first H3 header are ignored (section-level preamble).
    """
    blocks: list[tuple[str, list[str]]] = []
    current_header: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _H3_BLOCK.match(line)
        if m and (header_filter is None or header_filter(m.group(1))):
            if current_header is not None:
                blocks.append((current_header, current_lines))
            current_header = m.group(1)
            current_lines = []
        elif current_header is not None:
            current_lines.append(line)

    if current_header is not None:
        blocks.append((current_header, current_lines))

    return blocks


def _extract_text(lines: list[str]) -> str:
    """Strip leading and trailing blank lines; join remaining lines with newlines."""
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def _extract_text_without_comments(lines: list[str]) -> str:
    """Extract block text while ignoring HTML comments used in starter templates."""
    text = _extract_text(lines)
    text = _HTML_COMMENT_RE.sub("", text)
    return text.strip()


def _parse_target_section(lines: list[str]) -> Target:
    blocks = _split_h3_blocks(lines)
    seen_headers: set[str] = set()
    values: dict[str, object] = {}

    for header, content in blocks:
        if header not in _TARGET_HEADERS:
            raise WorkbookParseError(
                f"Unexpected H3 header in '## Target' section: '### {header}'. "
                f"Expected one of: {', '.join(f'### {name}' for name in _TARGET_HEADERS)}"
            )
        if header in seen_headers:
            raise WorkbookParseError(
                f"Duplicate target field header in '## Target' section: '### {header}'"
            )
        seen_headers.add(header)

        if header == "TARGET_SOURCES":
            values["sources"] = _parse_target_sources(content)
        else:
            text = _extract_text_without_comments(content)
            key = {
                "TARGET_KIND": "kind",
                "TARGET_NAME": "name",
                "TARGET_LOCATOR": "locator",
                "TARGET_BOUNDARY": "boundary",
                "TARGET_NOTES": "notes",
            }[header]
            values[key] = text

    for header in _TARGET_HEADERS:
        if header not in seen_headers:
            raise WorkbookParseError(
                f"Missing required target field in '## Target' section: '### {header}'"
            )

    return Target(
        kind=values["kind"],
        name=values["name"],
        locator=values["locator"],
        boundary=values["boundary"],
        sources=values["sources"],
        notes=values["notes"],
    )


def _parse_target_sources(lines: list[str]) -> list[str]:
    text = _extract_text_without_comments(lines)
    if not text:
        return []

    sources: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            raise WorkbookParseError(
                "Invalid source line in '### TARGET_SOURCES'. "
                "Expected bullet lines in the form '- path/to/source'."
            )
        source = stripped[2:].strip()
        if not source:
            raise WorkbookParseError(
                "Invalid source line in '### TARGET_SOURCES': empty source value."
            )
        if source in seen:
            raise WorkbookParseError(f"Duplicate source in '### TARGET_SOURCES': {source!r}")
        seen.add(source)
        sources.append(source)

    return sources


def _parse_brief(lines: list[str]) -> str:
    return _extract_text(lines)


def _parse_directions_section(
    lines: list[str],
) -> tuple[HumanFeedback, list[Direction]]:
    blocks = _split_h3_blocks(
        lines,
        header_filter=lambda header: (
            header == "HUMAN:global_instruction" or _DIRECTION_HEADER.match(header) is not None
        ),
    )
    global_feedback: HumanFeedback | None = None
    directions: list[Direction] = []
    seen_ids: set = set()

    for header, content in blocks:
        if header == "HUMAN:global_instruction":
            if global_feedback is not None:
                raise WorkbookParseError(
                    "Duplicate '### HUMAN:global_instruction' in '## Directions' section"
                )
            global_feedback = HumanFeedback(_extract_text(content))
        else:
            m = _DIRECTION_HEADER.match(header)
            if not m:
                raise WorkbookParseError(
                    f"Unexpected H3 header in '## Directions' section: "
                    f"'### {header}'. "
                    "Expected '### HUMAN:global_instruction' or "
                    "'### Direction: <id>'"
                )
            direction_id = m.group(1)
            if not _VALID_ID.match(direction_id):
                raise WorkbookParseError(
                    f"Invalid direction ID {direction_id!r}. "
                    "IDs must match [A-Za-z0-9][A-Za-z0-9_-]*"
                )
            if direction_id in seen_ids:
                raise WorkbookParseError(f"Duplicate direction ID: {direction_id!r}")
            seen_ids.add(direction_id)
            directions.append(_parse_direction_block(direction_id, content))

    if global_feedback is None:
        raise WorkbookParseError(
            "Missing '### HUMAN:global_instruction' in '## Directions' section"
        )

    return global_feedback, directions


def _parse_direction_block(direction_id: str, lines: list[str]) -> Direction:
    """Parse the content lines of a '### Direction: <id>' block."""
    human_idx: int | None = None
    for i, line in enumerate(lines):
        if _HUMAN_INSTRUCTION_LINE.match(line):
            human_idx = i
            break

    if human_idx is None:
        raise WorkbookParseError(f"Direction '{direction_id}': missing 'HUMAN:instruction' line")

    body_lines = lines[:human_idx]
    feedback_text = _extract_text(lines[human_idx + 1 :])
    body, trace = _parse_direction_traceability(direction_id, body_lines)

    return Direction(
        direction_id=direction_id,
        body=body,
        behavior_facet=trace["behavior_facet"],
        testing_lens=trace["testing_lens"],
        covered_user_priority_sections=trace["covered_user_priority_sections"],
        source_rationale=trace["source_rationale"],
        human_instruction=HumanFeedback(feedback_text),
    )


def _parse_direction_traceability(
    direction_id: str, lines: list[str]
) -> tuple[str, dict[str, object]]:
    body_lines: list[str] = []
    trace_values: dict[str, str] = {}
    in_trace = False

    for raw_line in lines:
        stripped = raw_line.rstrip()
        matched_key: str | None = None
        matched_value: str | None = None
        for key, pattern in _TRACE_LINE_PATTERNS.items():
            match = pattern.match(stripped)
            if match:
                matched_key = key
                matched_value = match.group(1).strip()
                break

        if matched_key is not None:
            in_trace = True
            if matched_key in trace_values:
                raise WorkbookParseError(
                    f"Direction '{direction_id}': duplicate traceability field '{matched_key}'"
                )
            if not matched_value:
                raise WorkbookParseError(
                    f"Direction '{direction_id}': traceability field '{matched_key}' must be non-empty"  # noqa: E501
                )
            trace_values[matched_key] = matched_value
            continue

        if in_trace and stripped:
            raise WorkbookParseError(
                f"Direction '{direction_id}': unexpected non-trace content after traceability fields: {raw_line!r}"  # noqa: E501
            )

        if not in_trace:
            body_lines.append(raw_line)

    body = _extract_text(body_lines)
    if not body:
        raise WorkbookParseError(f"Direction '{direction_id}': body must be non-empty")

    for key in _TRACE_LINE_PATTERNS:
        if key not in trace_values:
            raise WorkbookParseError(
                f"Direction '{direction_id}': missing required traceability field '{key}'"
            )

    return body, {
        "behavior_facet": trace_values["behavior_facet"],
        "testing_lens": trace_values["testing_lens"],
        "covered_user_priority_sections": _parse_priority_section_list(
            trace_values["covered_user_priority_sections"],
            owner=f"Direction '{direction_id}'",
        ),
        "source_rationale": trace_values["source_rationale"],
    }


def _parse_cases_section(
    lines: list[str],
) -> tuple[HumanFeedback, list[Case]]:
    blocks = _split_h3_blocks(
        lines,
        header_filter=lambda header: (
            header == "HUMAN:global_instruction" or _CASE_HEADER.match(header) is not None
        ),
    )
    global_feedback: HumanFeedback | None = None
    cases: list[Case] = []
    seen_ids: set = set()

    for header, content in blocks:
        if header == "HUMAN:global_instruction":
            if global_feedback is not None:
                raise WorkbookParseError(
                    "Duplicate '### HUMAN:global_instruction' in '## Cases' section"
                )
            global_feedback = HumanFeedback(_extract_text(content))
        else:
            m = _CASE_HEADER.match(header)
            if not m:
                raise WorkbookParseError(
                    f"Unexpected H3 header in '## Cases' section: "
                    f"'### {header}'. "
                    "Expected '### HUMAN:global_instruction' or "
                    "'### Case: <id>'"
                )
            case_id = m.group(1)
            if not _VALID_ID.match(case_id):
                raise WorkbookParseError(
                    f"Invalid case ID {case_id!r}. IDs must match [A-Za-z0-9][A-Za-z0-9_-]*"
                )
            if case_id in seen_ids:
                raise WorkbookParseError(f"Duplicate case ID: {case_id!r}")
            seen_ids.add(case_id)
            cases.append(_parse_case_block(case_id, content))

    if global_feedback is None:
        raise WorkbookParseError("Missing '### HUMAN:global_instruction' in '## Cases' section")

    return global_feedback, cases


def _parse_case_block(case_id: str, lines: list[str]) -> Case:
    """Parse the content lines of a '### Case: <id>' block."""
    human_idx: int | None = None
    for i, line in enumerate(lines):
        if _HUMAN_INSTRUCTION_LINE.match(line):
            human_idx = i
            break

    if human_idx is None:
        raise WorkbookParseError(f"Case '{case_id}': missing 'HUMAN:instruction' line")

    content_lines = lines[:human_idx]
    feedback_text = _extract_text(lines[human_idx + 1 :])

    raw_fields = _parse_case_fields(case_id, content_lines)

    # Validate required fields are present.
    _REQUIRED_CASE_FIELDS = {
        "input": "**Input:**",
        "target_directions": "**Target directions:** <ids>",
        "expected_behavior": "**Expected behavior:**",
        "behavior_facet": "**Behavior facet:** <text>",
        "testing_lens": "**Testing lens:** <text>",
        "covered_user_priority_sections": "**Covered user priorities:** <ids>",
        "source_rationale": "**Source rationale:** <text>",
    }
    for fname, label in _REQUIRED_CASE_FIELDS.items():
        if fname not in raw_fields:
            raise WorkbookParseError(f"Case '{case_id}': missing required field '{label}'")

    # Validate non-empty required text fields.
    if not raw_fields["input"].strip():
        raise WorkbookParseError(f"Case '{case_id}': '**Input:**' is present but empty")
    if not raw_fields["expected_behavior"].strip():
        raise WorkbookParseError(f"Case '{case_id}': '**Expected behavior:**' is present but empty")

    # Parse and validate target directions.
    raw_targets_str = raw_fields["target_directions"]
    if not raw_targets_str.strip():
        raise WorkbookParseError(f"Case '{case_id}': '**Target directions:**' has no value")
    target_directions = [t.strip() for t in raw_targets_str.split(",")]
    for tid in target_directions:
        if not tid:
            raise WorkbookParseError(
                f"Case '{case_id}': empty direction ID in '**Target directions:**'"
            )
        if not _VALID_ID.match(tid):
            raise WorkbookParseError(
                f"Case '{case_id}': invalid direction ID {tid!r} in "
                f"'**Target directions:**'. IDs must match [A-Za-z0-9][A-Za-z0-9_-]*"
            )

    return Case(
        case_id=case_id,
        input=raw_fields["input"],
        target_directions=target_directions,
        expected_behavior=raw_fields["expected_behavior"],
        behavior_facet=raw_fields["behavior_facet"],
        testing_lens=raw_fields["testing_lens"],
        covered_user_priority_sections=_parse_priority_section_list(
            raw_fields["covered_user_priority_sections"],
            owner=f"Case '{case_id}'",
        ),
        source_rationale=raw_fields["source_rationale"],
        context=raw_fields.get("context"),
        notes=raw_fields.get("notes"),
        human_instruction=HumanFeedback(feedback_text),
    )


def _parse_case_fields(case_id: str, lines: list[str]) -> dict[str, str]:
    """Scan case block content lines and extract structured fields.

    Returns a dict mapping field name to its text content.
    Raises WorkbookParseError for any grammar violation.
    """
    current_field: str | None = None
    found_first_field = False
    # Accumulate content lines per multi-line field.
    field_lines: dict[str, list[str]] = {}
    # Inline value for the single-line target_directions field.
    target_directions_value: str | None = None

    for line in lines:
        stripped = line.rstrip()

        # Check: **Target directions:** with no value (grammar error).
        if _CASE_TARGET_EMPTY_PAT.match(stripped):
            raise WorkbookParseError(
                f"Case '{case_id}': '**Target directions:**' requires a value "
                "on the same line. "
                "Format: '**Target directions:** dir_id1, dir_id2'"
            )

        # Check: **Target directions:** <value> (single-line field).
        m_target = _CASE_TARGET_PAT.match(stripped)
        if m_target:
            if target_directions_value is not None or "target_directions" in field_lines:
                raise WorkbookParseError(
                    f"Case '{case_id}': duplicate '**Target directions:**' field"
                )
            target_directions_value = m_target.group(1)
            current_field = "target_directions"
            found_first_field = True
            continue

        matched_trace: tuple[str, str] | None = None
        for field_name, pattern in _TRACE_LINE_PATTERNS.items():
            match = pattern.match(stripped)
            if match:
                matched_trace = (field_name, match.group(1).strip())
                break

        if matched_trace is not None:
            trace_field, trace_value = matched_trace
            if trace_field in field_lines:
                raise WorkbookParseError(f"Case '{case_id}': duplicate field '{trace_field}'")
            if not trace_value:
                raise WorkbookParseError(
                    f"Case '{case_id}': field '{trace_field}' must be non-empty"
                )
            field_lines[trace_field] = [trace_value]
            current_field = trace_field
            found_first_field = True
            continue

        # Check: multi-line field header.
        matched_multiline: str | None = None
        for header_str, fname in _CASE_MULTILINE_FIELDS.items():
            if stripped == header_str:
                matched_multiline = fname
                break

        if matched_multiline is not None:
            if matched_multiline in field_lines:
                raise WorkbookParseError(f"Case '{case_id}': duplicate field '{matched_multiline}'")
            field_lines[matched_multiline] = []
            current_field = matched_multiline
            found_first_field = True
            continue

        # Not a field header. Decide what to do based on state.
        if not found_first_field:
            # Before the first field header: only blank lines are allowed.
            if line.strip():
                raise WorkbookParseError(
                    f"Case '{case_id}': unexpected content before first field header: "
                    f"{line!r}. "
                    "Case block content must begin with a recognized field header "
                    "('**Input:**', '**Context:**', etc.)"
                )
            continue

        if current_field == "target_directions":
            # After the inline target_directions value, only blank lines are
            # allowed before the next field header.
            if line.strip():
                raise WorkbookParseError(
                    f"Case '{case_id}': unexpected non-blank content after "
                    f"'**Target directions:**' inline value: {line!r}"
                )
            continue

        if current_field is not None:
            field_lines[current_field].append(line)

    # Build the final result dict.
    result: dict[str, str] = {}
    for fname, content in field_lines.items():
        result[fname] = _extract_text(content)
    if target_directions_value is not None:
        result["target_directions"] = target_directions_value

    return result


def _parse_priority_section_list(value: str, *, owner: str) -> list[str]:
    parts = [item.strip() for item in value.split(",")]
    if not parts or any(not item for item in parts):
        raise WorkbookParseError(
            f"{owner}: '**Covered user priorities:**' must contain one or more non-empty section ids"  # noqa: E501
        )
    seen: set[str] = set()
    result: list[str] = []
    for item in parts:
        if item in seen:
            raise WorkbookParseError(
                f"{owner}: duplicate covered user priority section id {item!r}"
            )
        if not _VALID_ID.match(item):
            raise WorkbookParseError(f"{owner}: invalid covered user priority section id {item!r}")
        seen.add(item)
        result.append(item)
    return result


def _parse_continuation_section(lines: list[str] | None) -> ContinuationBlock:
    """Parse the optional ``## Continue Next Run`` section.

    Absent section → empty continuation block (backward compatibility with
    workbooks produced before the continuation feature).
    """
    if lines is None:
        return ContinuationBlock()

    blocks = _split_h3_blocks(lines)
    current_values: dict[str, str] = {}
    history_by_version: dict[int, dict[str, str]] = {}

    for header, content in blocks:
        text = _extract_text(content)

        m_current = _CONTINUATION_CURRENT_RE.match(header)
        if m_current:
            slot = _CONTINUATION_SLOT_KEYS[m_current.group(1)]
            if slot in current_values:
                raise WorkbookParseError(f"Duplicate current continuation slot: '### {header}'")
            current_values[slot] = text
            continue

        m_history = _CONTINUATION_HISTORY_RE.match(header)
        if m_history:
            version = int(m_history.group(1))
            if version <= 0:
                raise WorkbookParseError(
                    f"Invalid continuation history version in '### {header}': must be >= 1"
                )
            label = m_history.group(2)
            version_map = history_by_version.setdefault(version, {})
            if label == "CLI message":
                slot = "cli_message"
            else:
                slot = _CONTINUATION_SLOT_KEYS[label]
            if slot in version_map:
                raise WorkbookParseError(f"Duplicate historical continuation slot: '### {header}'")
            version_map[slot] = text
            continue

        raise WorkbookParseError(
            f"Unexpected H3 header in '## Continue Next Run' section: '### {header}'. "
            "Expected '### Current continuation: {general|direction|case} instruction' "
            "or '### Continuation v<n>: {general|direction|case} instruction | CLI message'."
        )

    current_fields = ContinuationFields(
        general_instruction=current_values.get("general_instruction", ""),
        direction_instruction=current_values.get("direction_instruction", ""),
        case_instruction=current_values.get("case_instruction", ""),
    )

    history: list[HistoricalContinuation] = []
    for version in sorted(history_by_version.keys()):
        slots = history_by_version[version]
        history.append(
            HistoricalContinuation(
                version=version,
                fields=ContinuationFields(
                    general_instruction=slots.get("general_instruction", ""),
                    direction_instruction=slots.get("direction_instruction", ""),
                    case_instruction=slots.get("case_instruction", ""),
                ),
                cli_message=slots.get("cli_message", ""),
            )
        )

    return ContinuationBlock(current=current_fields, history=history)


def _parse_run_readiness(lines: list[str]) -> RunReadiness:
    run_ready: bool | None = None
    readiness_note: str | None = None

    for line in lines:
        if not line.strip():
            continue

        m = _RUN_READY_LINE.match(line)
        if m:
            if run_ready is not None:
                raise WorkbookParseError("Duplicate 'RUN_READY:' in '## Run readiness' section")
            run_ready = m.group(1) == "yes"
            continue

        m = _READINESS_NOTE_LINE.match(line)
        if m:
            if readiness_note is not None:
                raise WorkbookParseError(
                    "Duplicate 'READINESS_NOTE:' in '## Run readiness' section"
                )
            readiness_note = m.group(1).strip()
            continue

        raise WorkbookParseError(
            f"Unexpected content in '## Run readiness' section: {line!r}. "
            "Only 'RUN_READY: yes|no', 'READINESS_NOTE: <text>', "
            "and blank lines are allowed."
        )

    if run_ready is None:
        raise WorkbookParseError(
            "Missing 'RUN_READY:' in '## Run readiness' section. "
            "Expected 'RUN_READY: yes' or 'RUN_READY: no'."
        )
    if readiness_note is None:
        raise WorkbookParseError("Missing 'READINESS_NOTE:' in '## Run readiness' section.")

    return RunReadiness(run_ready=run_ready, readiness_note=readiness_note)


def _parse_artifact_references(lines: list[str]) -> ArtifactReferences:
    refs: dict[str, str | None] = {}

    for line in lines:
        if not line.strip():
            continue

        m = _ARTIFACT_REF_LINE.match(line)
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            if key in refs:
                raise WorkbookParseError(
                    f"Duplicate artifact reference key '{key}' in '## Artifact references' section"
                )
            refs[key] = value if value else None
        else:
            raise WorkbookParseError(
                f"Malformed line in '## Artifact references' section: {line!r}. "
                "Expected '- run: <path>', '- analysis: <path>', "
                "or '- compare: <path>'"
            )

    for key in ("run", "analysis", "compare"):
        if key not in refs:
            raise WorkbookParseError(
                f"Missing artifact reference '- {key}:' in '## Artifact references' section"
            )

    return ArtifactReferences(
        run=refs["run"],
        analysis=refs["analysis"],
        compare=refs["compare"],
    )
