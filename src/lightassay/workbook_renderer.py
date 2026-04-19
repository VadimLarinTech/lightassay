"""Renders Workbook domain models to canonical workbook markdown.

The rendered output is guaranteed to round-trip through workbook_parser.parse()
back to an equivalent Workbook model.
"""

from __future__ import annotations

import re

from .workbook_models import (
    ArtifactReferences,
    ContinuationBlock,
    ContinuationFields,
    HumanFeedback,
    RunReadiness,
    Target,
    Workbook,
)

# ── Init brief template ───────────────────────────────────────────────────────

_INIT_TARGET_TEMPLATE = """\
<!-- Fill only the target fields below.
     Text outside these target fields is not part of the contract and may be ignored.
     Use TARGET_NOTES for any extra target-specific comments. -->

### TARGET_KIND
<!-- What kind of target is this?
     Examples:
     - workflow
     - http-api
     - python-callable
     - prompt
     - hidden-flow -->

### TARGET_NAME
<!-- Short human-readable target name.
     Examples:
     - summarize
     - moderation_pipeline
     - classification prompt -->

### TARGET_LOCATOR
<!-- Where is the target defined or entered?
     Examples:
     - myapp.pipeline.run
     - POST /api/predict
     - myapp/prompts/classifier.py::build_prompt -->

### TARGET_BOUNDARY
<!-- What is the real execution boundary for evaluation?
     Examples:
     - high-level pipeline boundary
     - public API endpoint boundary
     - prompt-construction-only boundary -->

### TARGET_SOURCES
<!-- Which files/modules should planning inspect?
     Use one bullet per source.
     Examples:
     - myapp/pipeline.py
     - myapp/prompts/classifier.py -->

### TARGET_NOTES
<!-- Optional scope notes, hidden flow details, dependencies, or constraints. -->"""

_INIT_BRIEF_TEMPLATE = """\
<!-- Fill only the brief fields below.
     Text outside these brief fields or under custom headings is not part of
     the planning contract and may be ignored.
     Use "Additional context (optional)" for anything that does not fit elsewhere. -->

### What is being tested
<!-- Describe the workflow under test. What does it do? What is the scope of this evaluation?
     (whole workflow / specific mode / specific function / specific behavior) -->

### What matters in the output
<!-- What aspects of the output are important to verify?
     (all behavior / selected important parts / critical required properties) -->

### Aspects that are especially significant
<!-- Which aspects of behavior are most important to you?
     Mark as: primary / secondary / risky / already suspicious -->

### Failure modes and problem classes that matter
<!-- Which kinds of problems are important to catch?
     Examples: missed problems, false positives, poor decisions, poor explanations,
     poor transformations, instability, boundary cases, weak spots, critical failures,
     other significant groups. -->

### What must not break
<!-- List any invariants or behaviors that are non-negotiable. -->

### Additional context (optional)
<!-- Any of the following that are relevant:
     - real examples from production or testing
     - known production observations
     - known weak spots
     - constraints on the evaluation
     - preferences on evaluation scale (depth vs breadth) -->"""

# ── Brief readiness validator ─────────────────────────────────────────────────

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_CANONICAL_BRIEF_HEADINGS = {
    "what is being tested",
    "what matters in the output",
    "aspects that are especially significant",
    "failure modes and problem classes that matter",
    "what must not break",
    "additional context (optional)",
}


def brief_has_user_content(brief: str) -> bool:
    """Check whether the brief contains human-authored content.

    Template scaffolding — ``### `` headings, ``<!-- -->`` HTML comments,
    and blank lines — is stripped first. If canonical brief headings are
    present, only content written under those supported headings counts.
    Content under unsupported headings is outside the contract and may be
    ignored. If no headings are present at all, plain freeform text still
    counts as human-authored content.

    This is a deterministic structural gate.  It does not perform semantic
    interpretation or heuristic scoring of the brief text.
    """
    # Remove HTML comments (may span multiple lines).
    text = _HTML_COMMENT_RE.sub("", brief)
    lines = text.splitlines()

    saw_any_heading = False
    current_heading_is_canonical = False
    saw_content_under_canonical_heading = False
    saw_freeform_content = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            saw_any_heading = True
            heading = stripped[4:].strip().lower()
            current_heading_is_canonical = heading in _CANONICAL_BRIEF_HEADINGS
            continue
        if saw_any_heading:
            if current_heading_is_canonical:
                saw_content_under_canonical_heading = True
            continue
        saw_freeform_content = True

    if saw_content_under_canonical_heading:
        return True
    if saw_any_heading:
        return False
    return saw_freeform_content


def _target_is_blank(target: Target) -> bool:
    """Return True when the target is still in the empty starter state."""
    return (
        not target.kind.strip()
        and not target.name.strip()
        and not target.locator.strip()
        and not target.boundary.strip()
        and not target.sources
        and not target.notes.strip()
    )


# ── Public API ────────────────────────────────────────────────────────────────


def render(workbook: Workbook) -> str:
    """Render a Workbook domain model to canonical workbook markdown."""
    parts: list[str] = []

    # Title
    parts += ["# Eval Workbook", ""]

    # Continuation block (always emitted so the human-editable fields
    # remain visible at the top of the workbook).
    parts += _render_continuation(workbook.continuation)

    # Target
    parts += ["## Target", ""]
    if _target_is_blank(workbook.target):
        parts.extend(_INIT_TARGET_TEMPLATE.splitlines())
        parts.append("")
    else:
        parts += _render_target(workbook.target)

    # Brief
    parts += ["## Brief", ""]
    if workbook.brief:
        parts.append(workbook.brief)
    parts.append("")

    # Directions
    parts += ["## Directions", ""]
    parts.append("### HUMAN:global_instruction")
    if workbook.directions_global_instruction.text:
        parts.append("")
        parts.append(workbook.directions_global_instruction.text)
    parts.append("")

    for direction in workbook.directions:
        parts.append(f"### Direction: {direction.direction_id}")
        parts.append("")
        if direction.body:
            parts.append(direction.body)
            parts.append("")
        parts.append(f"**Behavior facet:** {direction.behavior_facet}")
        parts.append(f"**Testing lens:** {direction.testing_lens}")
        parts.append(
            f"**Covered user priorities:** {', '.join(direction.covered_user_priority_sections)}"
        )
        parts.append(f"**Source rationale:** {direction.source_rationale}")
        parts.append("")
        parts.append("HUMAN:instruction")
        if direction.human_instruction.text:
            parts.append(direction.human_instruction.text)
        parts.append("")

    # Cases
    parts += ["## Cases", ""]
    parts.append("### HUMAN:global_instruction")
    if workbook.cases_global_instruction.text:
        parts.append("")
        parts.append(workbook.cases_global_instruction.text)
    parts.append("")

    for case in workbook.cases:
        parts.append(f"### Case: {case.case_id}")
        parts.append("")
        parts.append("**Input:**")
        parts.append(case.input)
        parts.append("")
        if case.context is not None:
            parts.append("**Context:**")
            parts.append(case.context)
            parts.append("")
        if case.notes is not None:
            parts.append("**Notes:**")
            parts.append(case.notes)
            parts.append("")
        parts.append(f"**Target directions:** {', '.join(case.target_directions)}")
        parts.append("")
        parts.append("**Expected behavior:**")
        parts.append(case.expected_behavior)
        parts.append("")
        parts.append(f"**Behavior facet:** {case.behavior_facet}")
        parts.append(f"**Testing lens:** {case.testing_lens}")
        parts.append(
            f"**Covered user priorities:** {', '.join(case.covered_user_priority_sections)}"
        )
        parts.append(f"**Source rationale:** {case.source_rationale}")
        parts.append("")
        parts.append("HUMAN:instruction")
        if case.human_instruction.text:
            parts.append(case.human_instruction.text)
        parts.append("")

    # Run readiness
    parts.append("## Run readiness")
    parts.append(f"RUN_READY: {'yes' if workbook.run_readiness.run_ready else 'no'}")
    if workbook.run_readiness.readiness_note:
        parts.append(f"READINESS_NOTE: {workbook.run_readiness.readiness_note}")
    else:
        parts.append("READINESS_NOTE:")
    parts.append("")

    # Artifact references
    parts.append("## Artifact references")
    for key, val in [
        ("run", workbook.artifact_references.run),
        ("analysis", workbook.artifact_references.analysis),
        ("compare", workbook.artifact_references.compare),
    ]:
        parts.append(f"- {key}: {val}" if val else f"- {key}:")
    parts.append("")

    return "\n".join(parts)


_CONTINUATION_SLOT_LABELS = (
    ("general_instruction", "general instruction"),
    ("direction_instruction", "direction instruction"),
    ("case_instruction", "case instruction"),
)


def _render_continuation(block: ContinuationBlock) -> list[str]:
    parts: list[str] = []
    parts.append("## Continue Next Run")
    parts.append("")
    parts.append("Fields below are consumed on the next `continue` run.")
    parts.append("")
    parts.extend(_render_current_continuation_slots(block.current))

    for entry in block.history:
        parts.extend(_render_history_continuation_entry(entry.version, entry))

    return parts


def _render_current_continuation_slots(fields: ContinuationFields) -> list[str]:
    parts: list[str] = []
    for attr, label in _CONTINUATION_SLOT_LABELS:
        parts.append(f"### Current continuation: {label}")
        parts.append("")
        value = getattr(fields, attr)
        if value:
            parts.append(value)
            parts.append("")
    return parts


def _render_history_continuation_entry(version: int, entry) -> list[str]:
    """Render a full historical continuation entry.

    Every slot (general / direction / case / CLI message) is emitted
    regardless of whether the human filled it — the plan requires the
    visible history to show exactly which slots the user used for each
    iteration, so empty slots remain present as empty blocks rather than
    being omitted.
    """
    prefix = f"Continuation v{version}"
    parts: list[str] = []
    for attr, label in _CONTINUATION_SLOT_LABELS:
        parts.append(f"### {prefix}: {label}")
        parts.append("")
        value = getattr(entry.fields, attr)
        if value:
            parts.append(value)
            parts.append("")
    parts.append(f"### {prefix}: CLI message")
    parts.append("")
    if entry.cli_message:
        parts.append(entry.cli_message)
        parts.append("")
    return parts


def _render_target(target: Target) -> list[str]:
    """Render the canonical target section without template comments."""
    parts: list[str] = []
    parts += ["### TARGET_KIND", ""]
    if target.kind:
        parts.append(target.kind)
    parts.append("")

    parts += ["### TARGET_NAME", ""]
    if target.name:
        parts.append(target.name)
    parts.append("")

    parts += ["### TARGET_LOCATOR", ""]
    if target.locator:
        parts.append(target.locator)
    parts.append("")

    parts += ["### TARGET_BOUNDARY", ""]
    if target.boundary:
        parts.append(target.boundary)
    parts.append("")

    parts += ["### TARGET_SOURCES", ""]
    for source in target.sources:
        parts.append(f"- {source}")
    parts.append("")

    parts += ["### TARGET_NOTES", ""]
    if target.notes:
        parts.append(target.notes)
    parts.append("")

    return parts


def render_init_workbook(name: str) -> str:  # noqa: ARG001  (name reserved for future use)
    """Render a fresh skeleton workbook for a new eval session.

    The returned markdown is valid per the grammar and can be round-tripped
    through workbook_parser.parse() without errors.
    """
    workbook = Workbook(
        target=Target(
            kind="",
            name="",
            locator="",
            boundary="",
            sources=[],
            notes="",
        ),
        brief=_INIT_BRIEF_TEMPLATE,
        directions_global_instruction=HumanFeedback(""),
        directions=[],
        cases_global_instruction=HumanFeedback(""),
        cases=[],
        run_readiness=RunReadiness(run_ready=False, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )
    return render(workbook)
