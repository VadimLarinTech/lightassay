"""Tests for workbook grammar contracts, renderer behavior, and workbook CLI.

Covers:
- Happy path: parse valid workbooks (init skeleton, workbook with directions, full workbook)
- Roundtrip: render -> parse -> render produces an equivalent result
- All explicit error conditions from the grammar spec (workbook_grammar.md)
- CLI workbook: creates auto-numbered files and fails on invalid output dirs

Run with:
    PYTHONPATH=src python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from lightassay.errors import WorkbookParseError
from lightassay.workbook_models import (
    ArtifactReferences,
    Case,
    Direction,
    HumanFeedback,
    RunReadiness,
    Target,
    Workbook,
)
from lightassay.workbook_parser import parse
from lightassay.workbook_renderer import brief_has_user_content, render, render_init_workbook

# ── Helpers ────────────────────────────────────────────────────────────────────

_PYTHON = sys.executable
_REPO = os.path.join(os.path.dirname(__file__), "..")
_SRC_PATH = os.path.join(_REPO, "src")


def _run_cli(*args):
    env = os.environ.copy()
    env["PYTHONPATH"] = _SRC_PATH
    return subprocess.run(
        [_PYTHON, "-m", "lightassay.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

_MINIMAL_WORKBOOK = (
    "# Eval Workbook\n\n"
    "## Target\n\n"
    "### TARGET_KIND\n\nworkflow\n\n"
    "### TARGET_NAME\n\ncheck_sentence\n\n"
    "### TARGET_LOCATOR\n\nmyapp.pipeline.run\n\n"
    "### TARGET_BOUNDARY\n\nhigh-level sentence-check workflow boundary\n\n"
    "### TARGET_SOURCES\n\n- myapp/pipeline.py\n- myapp/prompts/summarize.py\n\n"
    "### TARGET_NOTES\n\nFocus on the main sentence-check entrypoint.\n\n"
    "## Brief\n\nTest brief text.\n\n"
    "## Directions\n\n### HUMAN:global_instruction\n\n"
    "## Cases\n\n### HUMAN:global_instruction\n\n"
    "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
    "## Artifact references\n- run:\n- analysis:\n- compare:\n"
)

_WORKBOOK_WITH_DIRECTIONS = (
    "# Eval Workbook\n\n"
    "## Target\n\n"
    "### TARGET_KIND\n\nworkflow\n\n"
    "### TARGET_NAME\n\ncheck_sentence\n\n"
    "### TARGET_LOCATOR\n\nmyapp.pipeline.run\n\n"
    "### TARGET_BOUNDARY\n\nhigh-level sentence-check workflow boundary\n\n"
    "### TARGET_SOURCES\n\n- myapp/pipeline.py\n\n"
    "### TARGET_NOTES\n\n\n"
    "## Brief\n\nTest brief.\n\n"
    "## Directions\n\n"
    "### HUMAN:global_instruction\n\nFocus on edge cases.\n\n"
    "### Direction: dir-one\n\nDirection one body text.\n\n"
    "**Behavior facet:** core_output_behavior\n"
    "**Testing lens:** positive_and_regression\n"
    "**Covered user priorities:** freeform_brief\n"
    "**Source rationale:** Grounded in the explicit target source.\n\n"
    "HUMAN:instruction\n\n"
    "### Direction: dir-two\n\nDirection two body.\n\n"
    "**Behavior facet:** edge_case_behavior\n"
    "**Testing lens:** boundary_and_negative\n"
    "**Covered user priorities:** freeform_brief\n"
    "**Source rationale:** Grounded in neighboring explicit source behavior.\n\n"
    "HUMAN:instruction\nPlease expand this direction.\n\n"
    "## Cases\n\n### HUMAN:global_instruction\n\n"
    "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
    "## Artifact references\n- run:\n- analysis:\n- compare:\n"
)

_FULL_WORKBOOK = (
    "# Eval Workbook\n\n"
    "## Target\n\n"
    "### TARGET_KIND\n\nworkflow\n\n"
    "### TARGET_NAME\n\nsentence_checker\n\n"
    "### TARGET_LOCATOR\n\nmyapp.pipeline.run\n\n"
    "### TARGET_BOUNDARY\n\nuser-facing sentence-check workflow boundary\n\n"
    "### TARGET_SOURCES\n\n- myapp/pipeline.py\n- myapp/prompts/summarize.py\n\n"
    "### TARGET_NOTES\n\nIncludes prompt construction and response parsing.\n\n"
    "## Brief\n\nTest the sentence checker workflow.\n\n"
    "## Directions\n\n"
    "### HUMAN:global_instruction\n\n"
    "### Direction: correctness\n\nVerify output correctness.\n\n"
    "**Behavior facet:** core_output_behavior\n"
    "**Testing lens:** positive_and_regression\n"
    "**Covered user priorities:** freeform_brief\n"
    "**Source rationale:** Grounded in the explicit target source.\n\n"
    "HUMAN:instruction\n\n"
    "### Direction: edge-cases\n\nTest boundary inputs.\n\n"
    "**Behavior facet:** edge_case_behavior\n"
    "**Testing lens:** boundary_and_negative\n"
    "**Covered user priorities:** freeform_brief\n"
    "**Source rationale:** Grounded in neighboring explicit source behavior.\n\n"
    "HUMAN:instruction\n\n"
    "## Cases\n\n"
    "### HUMAN:global_instruction\n\n"
    "### Case: case-01\n\n"
    "**Input:**\nA simple correct sentence.\n\n"
    "**Context:**\nEnglish learner context.\n\n"
    "**Notes:**\nBaseline happy path.\n\n"
    "**Target directions:** correctness\n\n"
    "**Expected behavior:**\nShould confirm the sentence is correct.\n\n"
    "**Behavior facet:** core_output_behavior\n"
    "**Testing lens:** positive_and_regression\n"
    "**Covered user priorities:** freeform_brief\n"
    "**Source rationale:** Grounded in the explicit target source.\n\n"
    "HUMAN:instruction\n\n"
    "### Case: case-02\n\n"
    "**Input:**\nHe go to school yesterday.\n\n"
    "**Target directions:** correctness, edge-cases\n\n"
    "**Expected behavior:**\nShould detect tense error.\n\n"
    "**Behavior facet:** edge_case_behavior\n"
    "**Testing lens:** boundary_and_negative\n"
    "**Covered user priorities:** freeform_brief\n"
    "**Source rationale:** Grounded in neighboring explicit source behavior.\n\n"
    "HUMAN:instruction\n\n"
    "## Run readiness\nRUN_READY: yes\n"
    "READINESS_NOTE: All cases have input and expected behavior.\n\n"
    "## Artifact references\n"
    "- run: artifacts/run_001.json\n"
    "- analysis:\n"
    "- compare:\n"
)


# ── Happy path tests ─────────────────────────────────────────────────────────


class TestParseMinimal(unittest.TestCase):
    def test_parses_minimal_workbook(self):
        wb = parse(_MINIMAL_WORKBOOK)
        self.assertEqual(wb.target.kind, "workflow")
        self.assertEqual(wb.target.name, "check_sentence")
        self.assertEqual(wb.target.locator, "myapp.pipeline.run")
        self.assertEqual(wb.target.boundary, "high-level sentence-check workflow boundary")
        self.assertEqual(wb.target.sources, ["myapp/pipeline.py", "myapp/prompts/summarize.py"])
        self.assertEqual(wb.target.notes, "Focus on the main sentence-check entrypoint.")
        self.assertEqual(wb.brief, "Test brief text.")
        self.assertEqual(wb.directions_global_instruction.text, "")
        self.assertEqual(wb.directions, [])
        self.assertEqual(wb.cases_global_instruction.text, "")
        self.assertEqual(wb.cases, [])
        self.assertFalse(wb.run_readiness.run_ready)
        self.assertEqual(wb.run_readiness.readiness_note, "")
        self.assertIsNone(wb.artifact_references.run)
        self.assertIsNone(wb.artifact_references.analysis)
        self.assertIsNone(wb.artifact_references.compare)

    def test_parses_directions(self):
        wb = parse(_WORKBOOK_WITH_DIRECTIONS)
        self.assertEqual(wb.directions_global_instruction.text, "Focus on edge cases.")
        self.assertEqual(len(wb.directions), 2)
        self.assertEqual(wb.directions[0].direction_id, "dir-one")
        self.assertEqual(wb.directions[0].body, "Direction one body text.")
        self.assertEqual(wb.directions[0].human_instruction.text, "")
        self.assertEqual(wb.directions[1].direction_id, "dir-two")
        self.assertEqual(wb.directions[1].human_instruction.text, "Please expand this direction.")

    def test_parses_full_workbook(self):
        wb = parse(_FULL_WORKBOOK)
        self.assertEqual(wb.target.kind, "workflow")
        self.assertEqual(wb.target.sources, ["myapp/pipeline.py", "myapp/prompts/summarize.py"])
        self.assertEqual(len(wb.directions), 2)
        self.assertEqual(len(wb.cases), 2)
        c1 = wb.cases[0]
        self.assertEqual(c1.case_id, "case-01")
        self.assertEqual(c1.input, "A simple correct sentence.")
        self.assertEqual(c1.context, "English learner context.")
        self.assertEqual(c1.notes, "Baseline happy path.")
        self.assertEqual(c1.target_directions, ["correctness"])
        self.assertEqual(c1.expected_behavior, "Should confirm the sentence is correct.")
        c2 = wb.cases[1]
        self.assertEqual(c2.case_id, "case-02")
        self.assertIsNone(c2.context)
        self.assertIsNone(c2.notes)
        self.assertEqual(c2.target_directions, ["correctness", "edge-cases"])
        self.assertTrue(wb.run_readiness.run_ready)
        self.assertEqual(
            wb.run_readiness.readiness_note, "All cases have input and expected behavior."
        )
        self.assertEqual(wb.artifact_references.run, "artifacts/run_001.json")
        self.assertIsNone(wb.artifact_references.analysis)

    def test_h3_like_lines_inside_direction_and_case_body_do_not_start_new_blocks(self):
        workbook = Workbook(
            target=Target(
                kind="workflow",
                name="check_sentence",
                locator="myapp.pipeline.run",
                boundary="high-level sentence-check workflow boundary",
                sources=["myapp/pipeline.py"],
                notes="",
            ),
            brief="Brief text.",
            directions_global_instruction=HumanFeedback(""),
            directions=[
                Direction(
                    direction_id="dir-one",
                    body="Direction body line.\n### This is body text, not a new block.\nAnother line.",
                    behavior_facet="core_output_behavior",
                    testing_lens="positive_and_regression",
                    covered_user_priority_sections=["freeform_brief"],
                    source_rationale="Grounded in the explicit target source.",
                    human_instruction=HumanFeedback(""),
                )
            ],
            cases_global_instruction=HumanFeedback(""),
            cases=[
                Case(
                    case_id="case-01",
                    input="Input line\n### Case body text should remain inside the field.",
                    target_directions=["dir-one"],
                    expected_behavior="Expected line.\n### Still expected behavior text.",
                    behavior_facet="core_output_behavior",
                    testing_lens="positive_and_regression",
                    covered_user_priority_sections=["freeform_brief"],
                    source_rationale="Grounded in the explicit target source.",
                    context=None,
                    notes=None,
                    human_instruction=HumanFeedback(""),
                )
            ],
            run_readiness=RunReadiness(run_ready=True, readiness_note="Ready."),
            artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
        )

        rendered = render(workbook)
        parsed = parse(rendered)

        self.assertIn("### This is body text, not a new block.", parsed.directions[0].body)
        self.assertIn("### Case body text should remain inside the field.", parsed.cases[0].input)
        self.assertIn("### Still expected behavior text.", parsed.cases[0].expected_behavior)


class TestRoundTrip(unittest.TestCase):
    def test_init_skeleton_roundtrip(self):
        md = render_init_workbook("demo")
        wb = parse(md)
        md2 = render(wb)
        wb2 = parse(md2)
        self.assertEqual(wb.target.kind, wb2.target.kind)
        self.assertEqual(wb.target.sources, wb2.target.sources)
        self.assertEqual(wb.brief, wb2.brief)
        self.assertEqual(wb.run_readiness.run_ready, wb2.run_readiness.run_ready)

    def test_minimal_roundtrip(self):
        wb = parse(_MINIMAL_WORKBOOK)
        md = render(wb)
        wb2 = parse(md)
        self.assertEqual(wb.brief, wb2.brief)
        self.assertEqual(len(wb.directions), len(wb2.directions))
        self.assertEqual(len(wb.cases), len(wb2.cases))

    def test_full_roundtrip(self):
        wb = parse(_FULL_WORKBOOK)
        md = render(wb)
        wb2 = parse(md)
        self.assertEqual(len(wb2.directions), 2)
        self.assertEqual(len(wb2.cases), 2)
        self.assertEqual(wb2.cases[0].input, wb.cases[0].input)
        self.assertEqual(wb2.cases[1].target_directions, wb.cases[1].target_directions)
        self.assertEqual(wb2.artifact_references.run, wb.artifact_references.run)


# ── Error path tests ─────────────────────────────────────────────────────────


class TestTitleErrors(unittest.TestCase):
    def test_missing_title(self):
        with self.assertRaises(WorkbookParseError):
            parse("## Brief\n\ntext\n")

    def test_wrong_title(self):
        with self.assertRaises(WorkbookParseError):
            parse("# Wrong Title\n\n## Brief\ntext\n")

    def test_empty_file(self):
        with self.assertRaises(WorkbookParseError):
            parse("")

    def test_blank_file(self):
        with self.assertRaises(WorkbookParseError):
            parse("\n\n\n")


class TestSectionErrors(unittest.TestCase):
    def _make(self, **overrides):
        sections = {
            "Brief": "## Brief\n\ntext\n",
            "Directions": "## Directions\n\n### HUMAN:global_instruction\n",
            "Cases": "## Cases\n\n### HUMAN:global_instruction\n",
            "Run readiness": "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n",
            "Artifact references": "## Artifact references\n- run:\n- analysis:\n- compare:\n",
        }
        sections.update(overrides)
        return "# Eval Workbook\n\n" + "\n".join(sections.values())

    def test_missing_brief(self):
        text = self._make(Brief="")
        # Brief section header is gone, so it will be missing
        text = text.replace("## Brief\n\ntext\n", "")
        with self.assertRaises(WorkbookParseError):
            parse(
                "# Eval Workbook\n\n## Target\n\n"
                "### TARGET_KIND\n\nworkflow\n\n"
                "### TARGET_NAME\n\nname\n\n"
                "### TARGET_LOCATOR\n\nmodule.fn\n\n"
                "### TARGET_BOUNDARY\n\nboundary\n\n"
                "### TARGET_SOURCES\n\n- module.py\n\n"
                "### TARGET_NOTES\n\n\n"
                "## Directions\n\n### HUMAN:global_instruction\n\n"
                "## Cases\n\n### HUMAN:global_instruction\n\n"
                "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
                "## Artifact references\n- run:\n- analysis:\n- compare:\n"
            )

    def test_missing_directions(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                "# Eval Workbook\n\n## Target\n\n"
                "### TARGET_KIND\n\nworkflow\n\n"
                "### TARGET_NAME\n\nname\n\n"
                "### TARGET_LOCATOR\n\nmodule.fn\n\n"
                "### TARGET_BOUNDARY\n\nboundary\n\n"
                "### TARGET_SOURCES\n\n- module.py\n\n"
                "### TARGET_NOTES\n\n\n"
                "## Brief\n\ntext\n\n"
                "## Cases\n\n### HUMAN:global_instruction\n\n"
                "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
                "## Artifact references\n- run:\n- analysis:\n- compare:\n"
            )

    def test_duplicate_section(self):
        with self.assertRaises(WorkbookParseError):
            parse(_MINIMAL_WORKBOOK + "\n## Brief\n\nDuplicate.\n")


class TestDirectionsErrors(unittest.TestCase):
    def _base(self, directions_body):
        return (
            "# Eval Workbook\n\n## Target\n\n"
            "### TARGET_KIND\n\nworkflow\n\n"
            "### TARGET_NAME\n\nname\n\n"
            "### TARGET_LOCATOR\n\nmodule.fn\n\n"
            "### TARGET_BOUNDARY\n\nboundary\n\n"
            "### TARGET_SOURCES\n\n- module.py\n\n"
            "### TARGET_NOTES\n\n\n"
            "## Brief\n\ntext\n\n"
            "## Directions\n\n" + directions_body + "\n"
            "## Cases\n\n### HUMAN:global_instruction\n\n"
            "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
            "## Artifact references\n- run:\n- analysis:\n- compare:\n"
        )

    def _valid_direction(self, direction_id="d1", body="body"):
        return (
            f"### Direction: {direction_id}\n\n{body}\n\n"
            "**Behavior facet:** core_output_behavior\n"
            "**Testing lens:** positive_and_regression\n"
            "**Covered user priorities:** freeform_brief\n"
            "**Source rationale:** Grounded in the explicit target source.\n\n"
            "HUMAN:instruction\n"
        )

    def test_missing_global_instruction(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base(self._valid_direction()))

    def test_duplicate_global_instruction(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("### HUMAN:global_instruction\n\n### HUMAN:global_instruction\n"))

    def test_h3_like_text_after_global_instruction_is_treated_as_instruction_text(self):
        wb = parse(self._base("### HUMAN:global_instruction\n\n### Something else\n\ntext\n"))
        self.assertIn("### Something else", wb.directions_global_instruction.text)

    def test_invalid_direction_id(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Direction: _bad-start\n\nbody\n\nHUMAN:instruction\n"
                )
            )

    def test_duplicate_direction_id(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Direction: dup\n\nbody\n\nHUMAN:instruction\n\n"
                    "### Direction: dup\n\nbody2\n\nHUMAN:instruction\n"
                )
            )

    def test_direction_missing_human_instruction(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Direction: d1\n\nbody only, no HUMAN:instruction\n"
                )
            )


class TestCasesErrors(unittest.TestCase):
    def _base(self, cases_body):
        return (
            "# Eval Workbook\n\n## Target\n\n"
            "### TARGET_KIND\n\nworkflow\n\n"
            "### TARGET_NAME\n\nname\n\n"
            "### TARGET_LOCATOR\n\nmodule.fn\n\n"
            "### TARGET_BOUNDARY\n\nboundary\n\n"
            "### TARGET_SOURCES\n\n- module.py\n\n"
            "### TARGET_NOTES\n\n\n"
            "## Brief\n\ntext\n\n"
            "## Directions\n\n### HUMAN:global_instruction\n\n" + self._valid_direction() + "\n"
            "## Cases\n\n" + cases_body + "\n"
            "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
            "## Artifact references\n- run:\n- analysis:\n- compare:\n"
        )

    def _valid_direction(self, direction_id="d1", body="body"):
        return (
            f"### Direction: {direction_id}\n\n{body}\n\n"
            "**Behavior facet:** core_output_behavior\n"
            "**Testing lens:** positive_and_regression\n"
            "**Covered user priorities:** freeform_brief\n"
            "**Source rationale:** Grounded in the explicit target source.\n\n"
            "HUMAN:instruction\n"
        )

    def _valid_case(self, case_id="c1", target="d1"):
        return (
            f"### Case: {case_id}\n\n"
            f"**Input:**\nSome input.\n\n"
            f"**Target directions:** {target}\n\n"
            f"**Expected behavior:**\nSome expected behavior.\n\n"
            f"**Behavior facet:** core_output_behavior\n"
            f"**Testing lens:** positive_and_regression\n"
            f"**Covered user priorities:** freeform_brief\n"
            f"**Source rationale:** Grounded in the explicit target source.\n\n"
            f"HUMAN:instruction\n"
        )

    def _trace_block(self):
        return (
            "**Behavior facet:** core_output_behavior\n"
            "**Testing lens:** positive_and_regression\n"
            "**Covered user priorities:** freeform_brief\n"
            "**Source rationale:** Grounded in the explicit target source.\n\n"
        )

    def test_missing_cases_global_instruction(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base(self._valid_case()))

    def test_h3_like_text_after_cases_global_instruction_is_treated_as_instruction_text(self):
        wb = parse(self._base("### HUMAN:global_instruction\n\n### Random: header\n\ntext\n"))
        self.assertIn("### Random: header", wb.cases_global_instruction.text)

    def test_duplicate_case_id(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    + self._valid_case("dup")
                    + "\n"
                    + self._valid_case("dup")
                    + "\n"
                )
            )

    def test_invalid_case_id(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: -bad\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:** d1\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_missing_human_instruction(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:** d1\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block()
                )
            )

    def test_case_missing_input(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Target directions:** d1\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_empty_input(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\n\n"
                    "**Target directions:** d1\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_missing_expected_behavior(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:** d1\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_empty_expected_behavior(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:** d1\n\n"
                    "**Expected behavior:**\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_target_directions_empty(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:**\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_target_directions_invalid_id(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:** _bad\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_target_directions_nonexistent(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:** nonexistent\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_content_before_first_field(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "Some stray text before fields.\n\n"
                    "**Input:**\ntext\n\n"
                    "**Target directions:** d1\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )

    def test_case_duplicate_field(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### HUMAN:global_instruction\n\n"
                    "### Case: c1\n\n"
                    "**Input:**\ntext\n\n"
                    "**Input:**\ntext again\n\n"
                    "**Target directions:** d1\n\n"
                    "**Expected behavior:**\ntext\n\n" + self._trace_block() + "HUMAN:instruction\n"
                )
            )


class TestRunReadinessErrors(unittest.TestCase):
    def _base(self, readiness_body):
        return (
            "# Eval Workbook\n\n## Target\n\n"
            "### TARGET_KIND\n\nworkflow\n\n"
            "### TARGET_NAME\n\nname\n\n"
            "### TARGET_LOCATOR\n\nmodule.fn\n\n"
            "### TARGET_BOUNDARY\n\nboundary\n\n"
            "### TARGET_SOURCES\n\n- module.py\n\n"
            "### TARGET_NOTES\n\n\n"
            "## Brief\n\ntext\n\n"
            "## Directions\n\n### HUMAN:global_instruction\n\n"
            "## Cases\n\n### HUMAN:global_instruction\n\n"
            "## Run readiness\n" + readiness_body + "\n"
            "## Artifact references\n- run:\n- analysis:\n- compare:\n"
        )

    def test_missing_run_ready(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("READINESS_NOTE:\n"))

    def test_invalid_run_ready_value(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("RUN_READY: maybe\nREADINESS_NOTE:\n"))

    def test_missing_readiness_note(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("RUN_READY: no\n"))

    def test_duplicate_run_ready(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("RUN_READY: no\nRUN_READY: yes\nREADINESS_NOTE:\n"))

    def test_unexpected_content(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("RUN_READY: no\nREADINESS_NOTE:\nSome stray line\n"))


class TestArtifactReferencesErrors(unittest.TestCase):
    def _base(self, refs_body):
        return (
            "# Eval Workbook\n\n## Target\n\n"
            "### TARGET_KIND\n\nworkflow\n\n"
            "### TARGET_NAME\n\nname\n\n"
            "### TARGET_LOCATOR\n\nmodule.fn\n\n"
            "### TARGET_BOUNDARY\n\nboundary\n\n"
            "### TARGET_SOURCES\n\n- module.py\n\n"
            "### TARGET_NOTES\n\n\n"
            "## Brief\n\ntext\n\n"
            "## Directions\n\n### HUMAN:global_instruction\n\n"
            "## Cases\n\n### HUMAN:global_instruction\n\n"
            "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
            "## Artifact references\n" + refs_body
        )


class TestTargetErrors(unittest.TestCase):
    def _base(self, target_body):
        return (
            "# Eval Workbook\n\n## Target\n\n" + target_body + "\n"
            "## Brief\n\ntext\n\n"
            "## Directions\n\n### HUMAN:global_instruction\n\n"
            "## Cases\n\n### HUMAN:global_instruction\n\n"
            "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
            "## Artifact references\n- run:\n- analysis:\n- compare:\n"
        )

    def _valid_target(self):
        return (
            "### TARGET_KIND\n\nworkflow\n\n"
            "### TARGET_NAME\n\ncheck_sentence\n\n"
            "### TARGET_LOCATOR\n\nmyapp.pipeline.run\n\n"
            "### TARGET_BOUNDARY\n\nhigh-level workflow boundary\n\n"
            "### TARGET_SOURCES\n\n- myapp/pipeline.py\n\n"
            "### TARGET_NOTES\n\n\n"
        )

    def test_missing_target_section(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                "# Eval Workbook\n\n"
                "## Brief\n\ntext\n\n"
                "## Directions\n\n### HUMAN:global_instruction\n\n"
                "## Cases\n\n### HUMAN:global_instruction\n\n"
                "## Run readiness\nRUN_READY: no\nREADINESS_NOTE:\n\n"
                "## Artifact references\n- run:\n- analysis:\n- compare:\n"
            )

    def test_missing_required_target_field(self):
        broken = self._valid_target().replace(
            "### TARGET_BOUNDARY\n\nhigh-level workflow boundary\n\n", ""
        )
        with self.assertRaises(WorkbookParseError):
            parse(self._base(broken))

    def test_unexpected_target_header(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### TARGET_KIND\n\nworkflow\n\n"
                    "### SOMETHING_ELSE\n\nbad\n\n"
                    "### TARGET_NAME\n\nname\n\n"
                    "### TARGET_LOCATOR\n\nmodule.fn\n\n"
                    "### TARGET_BOUNDARY\n\nboundary\n\n"
                    "### TARGET_SOURCES\n\n- file.py\n\n"
                    "### TARGET_NOTES\n\n\n"
                )
            )

    def test_target_sources_must_be_bullets(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### TARGET_KIND\n\nworkflow\n\n"
                    "### TARGET_NAME\n\nname\n\n"
                    "### TARGET_LOCATOR\n\nmodule.fn\n\n"
                    "### TARGET_BOUNDARY\n\nboundary\n\n"
                    "### TARGET_SOURCES\n\nmyapp/pipeline.py\n\n"
                    "### TARGET_NOTES\n\n\n"
                )
            )

    def test_target_sources_duplicate_rejected(self):
        with self.assertRaises(WorkbookParseError):
            parse(
                self._base(
                    "### TARGET_KIND\n\nworkflow\n\n"
                    "### TARGET_NAME\n\nname\n\n"
                    "### TARGET_LOCATOR\n\nmodule.fn\n\n"
                    "### TARGET_BOUNDARY\n\nboundary\n\n"
                    "### TARGET_SOURCES\n\n- myapp/pipeline.py\n- myapp/pipeline.py\n\n"
                    "### TARGET_NOTES\n\n\n"
                )
            )

    def test_missing_run_ref(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("- analysis:\n- compare:\n"))

    def test_missing_analysis_ref(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("- run:\n- compare:\n"))

    def test_missing_compare_ref(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("- run:\n- analysis:\n"))

    def test_duplicate_ref_key(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("- run:\n- run:\n- analysis:\n- compare:\n"))

    def test_malformed_line(self):
        with self.assertRaises(WorkbookParseError):
            parse(self._base("- run:\n- analysis:\n- compare:\ngarbage line\n"))


# ── CLI workbook tests ───────────────────────────────────────────────────────


class TestCLIWorkbook(unittest.TestCase):
    def test_workbook_creates_workbook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_cli("workbook", "--output-dir", tmpdir)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            path = os.path.join(tmpdir, "workbook1.workbook.md")
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                content = f.read()
            self.assertTrue(content.startswith("# Eval Workbook"))

    def test_workbook_parses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _run_cli("workbook", "--output-dir", tmpdir)
            with open(os.path.join(tmpdir, "workbook1.workbook.md")) as f:
                wb = parse(f.read())
            self.assertFalse(wb.run_readiness.run_ready)

    def test_workbook_auto_increments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r1 = _run_cli("workbook", "--output-dir", tmpdir)
            self.assertEqual(r1.returncode, 0)
            r2 = _run_cli("workbook", "--output-dir", tmpdir)
            self.assertEqual(r2.returncode, 0, msg=r2.stderr)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "workbook1.workbook.md")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "workbook2.workbook.md")))

    def test_workbook_missing_dir_fails(self):
        result = _run_cli("workbook", "--output-dir", "/nonexistent/dir/xyz")
        self.assertNotEqual(result.returncode, 0)


# ── Brief user content validator tests ───────────────────────────────────────


class TestBriefHasUserContent(unittest.TestCase):
    def test_empty_string(self):
        self.assertFalse(brief_has_user_content(""))

    def test_whitespace_only(self):
        self.assertFalse(brief_has_user_content("   \n\n  \n"))

    def test_init_template(self):
        """The exact init template must be rejected."""
        init_md = render_init_workbook("test")
        wb = parse(init_md)
        self.assertFalse(brief_has_user_content(wb.brief))

    def test_headings_only(self):
        self.assertFalse(brief_has_user_content("### What is being tested\n### What matters\n"))

    def test_html_comments_only(self):
        self.assertFalse(brief_has_user_content("<!-- some guidance -->\n<!-- more guidance -->"))

    def test_headings_and_comments_only(self):
        self.assertFalse(
            brief_has_user_content("### Heading\n<!-- comment -->\n\n### Another\n<!-- more -->\n")
        )

    def test_multiline_html_comment(self):
        self.assertFalse(brief_has_user_content("### Heading\n<!-- multi\nline\ncomment -->\n"))

    def test_minimal_user_content(self):
        self.assertTrue(brief_has_user_content("Testing the workflow."))

    def test_user_content_among_scaffolding(self):
        self.assertTrue(
            brief_has_user_content(
                "### What is being tested\n"
                "<!-- guidance -->\n"
                "The sentence checker workflow.\n"
                "### What matters\n"
            )
        )

    def test_user_content_after_all_scaffolding(self):
        self.assertFalse(
            brief_has_user_content("### Heading\n<!-- comment -->\n\nMy real content here.\n")
        )

    def test_single_word_user_content(self):
        self.assertFalse(brief_has_user_content("### Heading\n<!-- comment -->\nworkflow\n"))

    def test_unsupported_heading_is_ignored_when_canonical_fields_exist(self):
        self.assertTrue(
            brief_has_user_content(
                "### What is being tested\n"
                "The sentence checker workflow.\n"
                "### My custom heading\n"
                "This text is outside the supported brief fields.\n"
            )
        )


if __name__ == "__main__":
    unittest.main()
