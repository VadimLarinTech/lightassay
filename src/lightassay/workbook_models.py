"""Domain models for workbook contracts (v1).

These dataclasses represent the parsed state of a workbook file.
They are produced by workbook_parser.parse() and consumed by workbook_renderer.render().
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Target:
    """Structured evaluation target definition.

    This is the first-class object that defines what is being evaluated,
    where the real boundary lives, and which sources should be examined
    during planning.
    """

    kind: str
    name: str
    locator: str
    boundary: str
    sources: list[str]
    notes: str


@dataclass
class HumanFeedback:
    """A human feedback field.

    Empty text means 'no objections, approved.'
    Any non-empty text is a strict instruction for the LLM's next workbook update.
    """

    text: str


@dataclass
class Direction:
    """A single direction block in the workbook."""

    direction_id: str
    body: str
    behavior_facet: str
    testing_lens: str
    covered_user_priority_sections: list[str]
    source_rationale: str
    human_instruction: HumanFeedback


@dataclass
class Case:
    """A single case block in the workbook."""

    case_id: str
    input: str
    target_directions: list[str]
    expected_behavior: str
    behavior_facet: str
    testing_lens: str
    covered_user_priority_sections: list[str]
    source_rationale: str
    context: str | None
    notes: str | None
    human_instruction: HumanFeedback


@dataclass
class ArtifactReferences:
    """File paths to produced artifacts.

    None means the reference key is present in the workbook but has no value yet
    (artifact has not been produced).
    """

    run: str | None
    analysis: str | None
    compare: str | None


@dataclass
class RunReadiness:
    """Run readiness signal written by the LLM after workbook reconciliation."""

    run_ready: bool
    readiness_note: str


@dataclass
class ContinuationFields:
    """Three human-editable continuation instruction fields.

    Empty strings mean "no follow-up input for this slot."
    """

    general_instruction: str = ""
    direction_instruction: str = ""
    case_instruction: str = ""

    def is_empty(self) -> bool:
        return not (
            self.general_instruction.strip()
            or self.direction_instruction.strip()
            or self.case_instruction.strip()
        )


@dataclass
class HistoricalContinuation:
    """A versioned historical continuation block.

    Each entry preserves the full human-editable continuation slots plus
    the literal ``--message`` used on that run, so the workbook carries
    a truthful record of how each iteration was requested. An empty
    ``cli_message`` simply means the user did not pass ``--message`` for
    that iteration — empty slots are kept present rather than omitted.
    """

    version: int
    fields: ContinuationFields
    cli_message: str = ""


@dataclass
class ContinuationBlock:
    """Top-of-workbook continuation block — human-editable follow-up input.

    ``current`` is the single editable active block used on the next
    ``continue`` call.  ``history`` is the versioned list of prior
    continuation fields rotated out after each successful continue.
    """

    current: ContinuationFields = field(default_factory=ContinuationFields)
    history: list[HistoricalContinuation] = field(default_factory=list)


@dataclass
class Workbook:
    """The complete workbook domain model — the single source of truth for one eval session."""

    target: Target
    brief: str
    directions_global_instruction: HumanFeedback
    directions: list[Direction]
    cases_global_instruction: HumanFeedback
    cases: list[Case]
    run_readiness: RunReadiness
    artifact_references: ArtifactReferences
    continuation: ContinuationBlock = field(default_factory=ContinuationBlock)
