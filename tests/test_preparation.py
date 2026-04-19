"""Tests for the preparation pipeline: config, preparer, CLI commands.

Covers:
- Preparation config loading (happy + error paths)
- Preparer operations: generate_directions, generate_cases, reconcile_readiness
- CLI prepare-directions, prepare-cases, prepare-readiness commands
- Workbook round-trip: adapter JSON → model mutation → renderer → parser

Run with:
    PYTHONPATH=src python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from lightassay import init_workbook
from lightassay.errors import PreparationConfigError, PreparationError
from lightassay.preparation_config import PreparationConfig, load_preparation_config
from lightassay.preparer import (
    execute_generate_cases,
    execute_generate_directions,
    execute_reconcile_readiness,
)
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
from lightassay.workbook_renderer import render

# ── Helpers ──────────────────────────────────────────────────────────────────

_PYTHON = sys.executable
_REPO = os.path.join(os.path.dirname(__file__), "..")
_SRC_PATH = os.path.join(_REPO, "src")
_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
_FILLED_BRIEF = """\
### What is being tested
Test the text echo workflow end to end.

### What matters in the output
Correctness and traceable behavior matter most.

### Aspects that are especially significant
Primary: correctness.
Risky: edge cases.

### Failure modes and problem classes that matter
Missed problems and false positives matter.

### What must not break
The adapter must preserve the input payload.

### Additional context (optional)
This suite validates source-grounded preparation behavior."""
_REQUIRED_PRIORITY_IDS = [
    "what_is_being_tested",
    "what_matters_in_output",
    "significant_aspects",
    "failure_modes",
    "must_not_break",
]


def _run_cli(*args):
    env = os.environ.copy()
    env["PYTHONPATH"] = _SRC_PATH
    return subprocess.run(
        [_PYTHON, "-m", "lightassay.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _fixture(name):
    return os.path.join(_FIXTURES, name)


def _make_brief_only_workbook() -> Workbook:
    """Workbook with a filled brief but no directions or cases."""
    return Workbook(
        target=Target(
            kind="workflow",
            name="text_echo",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief=_FILLED_BRIEF,
        directions_global_instruction=HumanFeedback(""),
        directions=[],
        cases_global_instruction=HumanFeedback(""),
        cases=[],
        run_readiness=RunReadiness(run_ready=False, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _make_workbook_with_directions() -> Workbook:
    """Workbook with brief + directions, no cases."""
    return Workbook(
        target=Target(
            kind="workflow",
            name="text_echo",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief=_FILLED_BRIEF,
        directions_global_instruction=HumanFeedback(""),
        directions=[
            Direction(
                direction_id="correctness",
                body="Verify output correctness.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=list(_REQUIRED_PRIORITY_IDS),
                source_rationale="Grounded in the explicit adapter source.",
                human_instruction=HumanFeedback(""),
            ),
            Direction(
                direction_id="edge-cases",
                body="Test boundary inputs.",
                behavior_facet="edge_case_behavior",
                testing_lens="boundary_and_negative",
                covered_user_priority_sections=list(_REQUIRED_PRIORITY_IDS),
                source_rationale="Grounded in neighboring explicit source behavior.",
                human_instruction=HumanFeedback(""),
            ),
        ],
        cases_global_instruction=HumanFeedback(""),
        cases=[],
        run_readiness=RunReadiness(run_ready=False, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _make_workbook_with_directions_and_cases() -> Workbook:
    """Workbook with brief + directions + cases, ready for reconciliation."""
    return Workbook(
        target=Target(
            kind="workflow",
            name="text_echo",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief=_FILLED_BRIEF,
        directions_global_instruction=HumanFeedback(""),
        directions=[
            Direction(
                direction_id="correctness",
                body="Verify output correctness.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=list(_REQUIRED_PRIORITY_IDS),
                source_rationale="Grounded in the explicit adapter source.",
                human_instruction=HumanFeedback(""),
            ),
            Direction(
                direction_id="edge-cases",
                body="Test boundary inputs.",
                behavior_facet="edge_case_behavior",
                testing_lens="boundary_and_negative",
                covered_user_priority_sections=list(_REQUIRED_PRIORITY_IDS),
                source_rationale="Grounded in neighboring explicit source behavior.",
                human_instruction=HumanFeedback(""),
            ),
        ],
        cases_global_instruction=HumanFeedback(""),
        cases=[
            Case(
                case_id="case-1",
                input="Test input for correctness",
                target_directions=["correctness"],
                expected_behavior="Should satisfy correctness direction.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=list(_REQUIRED_PRIORITY_IDS),
                source_rationale="Grounded in explicit adapter behavior.",
                context="Context for case 1",
                notes=None,
                human_instruction=HumanFeedback(""),
            ),
            Case(
                case_id="case-2",
                input="Test input for edge-cases",
                target_directions=["edge-cases"],
                expected_behavior="Should satisfy edge-cases direction.",
                behavior_facet="edge_case_behavior",
                testing_lens="boundary_and_negative",
                covered_user_priority_sections=list(_REQUIRED_PRIORITY_IDS),
                source_rationale="Grounded in explicit adapter behavior.",
                context=None,
                notes=None,
                human_instruction=HumanFeedback(""),
            ),
        ],
        run_readiness=RunReadiness(run_ready=False, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _save_workbook(workbook: Workbook, path: str) -> None:
    """Render and save a workbook to a file."""
    text = render(workbook)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _fill_target_for_init_workbook(workbook: Workbook) -> Workbook:
    workbook.target = Target(
        kind="workflow",
        name="text_echo",
        locator="tests.fixtures.adapter_echo",
        boundary="text echo workflow boundary",
        sources=["tests/fixtures/adapter_echo.py"],
        notes="",
    )
    return workbook


# ── Preparation Config Tests ────────────────────────────────────────────────


class TestPreparationConfigHappy(unittest.TestCase):
    def test_load_valid_config(self):
        config = load_preparation_config(_fixture("preparation_ok.json"))
        self.assertEqual(config.provider, "test")
        self.assertEqual(config.model, "echo-v1")
        self.assertTrue(os.path.isabs(config.adapter))
        self.assertTrue(config.adapter.endswith("preparation_adapter_echo.py"))


class TestPreparationConfigErrors(unittest.TestCase):
    def test_file_not_found(self):
        with self.assertRaises(PreparationConfigError) as ctx:
            load_preparation_config("/nonexistent/config.json")
        self.assertIn("not found", str(ctx.exception))

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json{")
            f.flush()
            try:
                with self.assertRaises(PreparationConfigError) as ctx:
                    load_preparation_config(f.name)
                self.assertIn("not valid JSON", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_missing_field(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"adapter": "./a.py", "provider": "test"}, f)
            f.flush()
            try:
                with self.assertRaises(PreparationConfigError) as ctx:
                    load_preparation_config(f.name)
                self.assertIn("missing required field", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_unknown_field(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "adapter": "./a.py",
                    "provider": "test",
                    "model": "m",
                    "extra": "bad",
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(PreparationConfigError) as ctx:
                    load_preparation_config(f.name)
                self.assertIn("unknown fields", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_empty_field_value(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"adapter": "", "provider": "test", "model": "m"}, f)
            f.flush()
            try:
                with self.assertRaises(PreparationConfigError) as ctx:
                    load_preparation_config(f.name)
                self.assertIn("non-empty", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_non_string_field(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"adapter": "./a.py", "provider": 123, "model": "m"}, f)
            f.flush()
            try:
                with self.assertRaises(PreparationConfigError) as ctx:
                    load_preparation_config(f.name)
                self.assertIn("must be a string", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_not_object(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            f.flush()
            try:
                with self.assertRaises(PreparationConfigError) as ctx:
                    load_preparation_config(f.name)
                self.assertIn("must be a JSON object", str(ctx.exception))
            finally:
                os.unlink(f.name)


# ── Preparer: generate_directions ────────────────────────────────────────────


class TestGenerateDirectionsHappy(unittest.TestCase):
    def test_generates_directions_from_brief(self):
        workbook = _make_brief_only_workbook()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        result = execute_generate_directions(workbook, config)

        self.assertEqual(len(result.directions), 2)
        self.assertEqual(result.directions[0].direction_id, "correctness")
        self.assertEqual(result.directions[1].direction_id, "edge-cases")
        self.assertIn("text_echo", result.directions[0].body)
        self.assertIn("What is being tested", result.directions[0].body)
        self.assertIn("tests/fixtures/adapter_echo.py", result.directions[0].body)
        # Each direction has an empty human instruction.
        for d in result.directions:
            self.assertEqual(d.human_instruction.text, "")
        # Body is non-empty.
        for d in result.directions:
            self.assertTrue(d.body.strip())

    def test_discovers_locator_source_for_direction_planning(self):
        workbook = _make_brief_only_workbook()
        workbook.target = Target(
            kind="workflow",
            name="preparer_target",
            locator="lightassay.preparer.execute_generate_directions",
            boundary="preparer workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        )
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )

        result = execute_generate_directions(workbook, config)

        self.assertIn(
            "src/lightassay/preparer.py",
            result.directions[1].body,
        )

    def test_roundtrip_after_generate_directions(self):
        """Workbook round-trips: generate → render → parse → equivalent model."""
        workbook = _make_brief_only_workbook()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        result = execute_generate_directions(workbook, config)
        rendered = render(result)
        parsed = parse(rendered)

        self.assertEqual(len(parsed.directions), 2)
        self.assertEqual(parsed.directions[0].direction_id, "correctness")
        self.assertEqual(parsed.directions[1].direction_id, "edge-cases")

    def test_ignores_text_outside_supported_brief_fields_for_user_priorities(self):
        workbook = _make_brief_only_workbook()
        workbook.brief = """\
Intro text outside the canonical fields that should not drive planning.

### What is being tested
Test the text echo workflow end to end.

### My custom heading
This unsupported section should be ignored by user-priority extraction.
"""
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )

        result = execute_generate_directions(workbook, config)

        for direction in result.directions:
            self.assertEqual(
                direction.covered_user_priority_sections,
                ["what_is_being_tested"],
            )
        self.assertIn("What is being tested", result.directions[0].body)


class TestGenerateDirectionsFailurePaths(unittest.TestCase):
    def _config_with_adapter(self, adapter_fixture):
        return PreparationConfig(
            adapter=_fixture(adapter_fixture),
            provider="test",
            model="test",
        )

    def test_adapter_non_zero_exit(self):
        workbook = _make_brief_only_workbook()
        config = self._config_with_adapter("preparation_adapter_fail.py")
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, config)
        self.assertIn("exited with code", str(ctx.exception))

    def test_adapter_bad_json(self):
        workbook = _make_brief_only_workbook()
        config = self._config_with_adapter("preparation_adapter_bad_json.py")
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, config)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_adapter_missing_field(self):
        workbook = _make_brief_only_workbook()
        config = self._config_with_adapter("preparation_adapter_missing_field.py")
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, config)
        self.assertIn("missing required field", str(ctx.exception))

    def test_adapter_empty_directions(self):
        workbook = _make_brief_only_workbook()
        config = self._config_with_adapter("preparation_adapter_empty_directions.py")
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, config)
        self.assertIn("non-empty", str(ctx.exception))

    def test_adapter_not_found(self):
        workbook = _make_brief_only_workbook()
        config = PreparationConfig(
            adapter="/nonexistent/adapter.py",
            provider="test",
            model="test",
        )
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, config)
        self.assertIn("not found", str(ctx.exception))


# ── Preparer: generate_cases ─────────────────────────────────────────────────


class TestGenerateCasesHappy(unittest.TestCase):
    def test_generates_cases_from_directions(self):
        workbook = _make_workbook_with_directions()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        result = execute_generate_cases(workbook, config)

        self.assertEqual(len(result.cases), 2)
        self.assertEqual(result.cases[0].case_id, "case-1")
        self.assertEqual(result.cases[1].case_id, "case-2")
        self.assertIn("text echo workflow boundary", result.cases[0].expected_behavior)
        self.assertIn("What is being tested", result.cases[0].expected_behavior)
        self.assertIn("tests/fixtures/adapter_echo.py", result.cases[0].notes)
        # Each case has an empty human instruction.
        for c in result.cases:
            self.assertEqual(c.human_instruction.text, "")
        # Target directions reference existing direction IDs.
        direction_ids = {d.direction_id for d in result.directions}
        for c in result.cases:
            for td in c.target_directions:
                self.assertIn(td, direction_ids)

    def test_roundtrip_after_generate_cases(self):
        """Workbook round-trips: generate → render → parse → equivalent model."""
        workbook = _make_workbook_with_directions()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        result = execute_generate_cases(workbook, config)
        rendered = render(result)
        parsed = parse(rendered)

        self.assertEqual(len(parsed.cases), 2)
        self.assertEqual(parsed.cases[0].case_id, "case-1")
        self.assertEqual(parsed.cases[1].case_id, "case-2")
        # Directions preserved.
        self.assertEqual(len(parsed.directions), 2)


class TestGenerateCasesFailurePaths(unittest.TestCase):
    def test_adapter_non_zero_exit(self):
        workbook = _make_workbook_with_directions()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_fail.py"),
            provider="test",
            model="test",
        )
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_cases(workbook, config)
        self.assertIn("exited with code", str(ctx.exception))

    def test_adapter_bad_json(self):
        workbook = _make_workbook_with_directions()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_bad_json.py"),
            provider="test",
            model="test",
        )
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_cases(workbook, config)
        self.assertIn("not valid JSON", str(ctx.exception))


# ── Preparer: reconcile_readiness ────────────────────────────────────────────


class TestReconcileReadinessHappy(unittest.TestCase):
    def test_reconcile_sets_run_ready(self):
        workbook = _make_workbook_with_directions_and_cases()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        result = execute_reconcile_readiness(workbook, config)

        self.assertTrue(result.run_readiness.run_ready)
        self.assertEqual(
            result.run_readiness.readiness_note,
            "All cases reconciled and ready.",
        )
        self.assertEqual(len(result.directions), 2)
        self.assertEqual(len(result.cases), 2)

    def test_roundtrip_after_reconcile(self):
        """Workbook round-trips: reconcile → render → parse → equivalent model."""
        workbook = _make_workbook_with_directions_and_cases()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        result = execute_reconcile_readiness(workbook, config)
        rendered = render(result)
        parsed = parse(rendered)

        self.assertTrue(parsed.run_readiness.run_ready)
        self.assertEqual(len(parsed.directions), 2)
        self.assertEqual(len(parsed.cases), 2)
        self.assertEqual(
            parsed.run_readiness.readiness_note,
            "All cases reconciled and ready.",
        )


class TestReconcileReadinessNotReady(unittest.TestCase):
    """Verify the reconcile_readiness response contract for not-ready states.

    The adapter must provide a non-empty readiness_note when run_ready is false.
    This is the reconcile_readiness response contract (flow_v1.md Step 4,
    workbook_spec.md Run readiness contract), distinct from the workbook grammar
    which allows empty READINESS_NOTE for fresh/init workbooks.
    """

    def test_rejects_blank_readiness_note_when_not_ready(self):
        """Adapter returns run_ready=false with empty readiness_note -> rejected."""
        workbook = _make_workbook_with_directions_and_cases()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_not_ready_blank.py"),
            provider="test",
            model="test",
        )
        with self.assertRaises(PreparationError) as ctx:
            execute_reconcile_readiness(workbook, config)
        self.assertIn("readiness_note must be non-empty", str(ctx.exception))
        self.assertIn("run_ready is false", str(ctx.exception))

    def test_rejects_whitespace_only_readiness_note_when_not_ready(self):
        """Adapter returns run_ready=false with whitespace-only readiness_note -> rejected."""
        _make_workbook_with_directions_and_cases()
        # Use the blank adapter but patch to return whitespace — test at model level.
        from lightassay.preparer import _validate_readiness_response

        response = {
            "directions": [
                {
                    "direction_id": "correctness",
                    "body": "Verify correctness.",
                    "behavior_facet": "core_output_behavior",
                    "testing_lens": "positive_and_regression",
                    "covered_user_priority_sections": list(_REQUIRED_PRIORITY_IDS),
                    "source_rationale": "Grounded in the explicit adapter source.",
                },
            ],
            "cases": [
                {
                    "case_id": "case-1",
                    "input": "Test input",
                    "target_directions": ["correctness"],
                    "expected_behavior": "Expected behavior.",
                    "behavior_facet": "core_output_behavior",
                    "testing_lens": "positive_and_regression",
                    "covered_user_priority_sections": list(_REQUIRED_PRIORITY_IDS),
                    "source_rationale": "Grounded in the explicit adapter source.",
                    "context": None,
                    "notes": None,
                },
            ],
            "run_ready": False,
            "readiness_note": "   \t  ",
            "priority_conflicts": [],
        }
        with self.assertRaises(PreparationError) as ctx:
            _validate_readiness_response(response)
        self.assertIn("readiness_note must be non-empty", str(ctx.exception))

    def test_accepts_not_ready_with_valid_reason(self):
        """Adapter returns run_ready=false with a substantive reason -> accepted."""
        workbook = _make_workbook_with_directions_and_cases()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_not_ready_with_reason.py"),
            provider="test",
            model="test",
        )
        result = execute_reconcile_readiness(workbook, config)
        self.assertFalse(result.run_readiness.run_ready)
        self.assertEqual(
            result.run_readiness.readiness_note,
            "Cases need more detail in expected behavior.",
        )

    def test_accepts_empty_readiness_note_when_ready(self):
        """Adapter returns run_ready=true with empty readiness_note -> accepted.

        The non-empty requirement applies only when run_ready is false.
        """
        from lightassay.preparer import _validate_readiness_response

        response = {
            "directions": [
                {
                    "direction_id": "correctness",
                    "body": "Verify correctness.",
                    "behavior_facet": "core_output_behavior",
                    "testing_lens": "positive_and_regression",
                    "covered_user_priority_sections": list(_REQUIRED_PRIORITY_IDS),
                    "source_rationale": "Grounded in the explicit adapter source.",
                },
            ],
            "cases": [
                {
                    "case_id": "case-1",
                    "input": "Test input",
                    "target_directions": ["correctness"],
                    "expected_behavior": "Expected behavior.",
                    "behavior_facet": "core_output_behavior",
                    "testing_lens": "positive_and_regression",
                    "covered_user_priority_sections": list(_REQUIRED_PRIORITY_IDS),
                    "source_rationale": "Grounded in the explicit adapter source.",
                    "context": None,
                    "notes": None,
                },
            ],
            "run_ready": True,
            "readiness_note": "",
            "priority_conflicts": [],
        }
        # Must not raise.
        direction_dicts, case_dicts, run_ready, readiness_note = _validate_readiness_response(
            response
        )
        self.assertTrue(run_ready)
        self.assertEqual(readiness_note, "")

    def test_not_ready_roundtrip_preserves_reason(self):
        """Not-ready workbook round-trips: reconcile -> render -> parse preserves reason."""
        workbook = _make_workbook_with_directions_and_cases()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_not_ready_with_reason.py"),
            provider="test",
            model="test",
        )
        result = execute_reconcile_readiness(workbook, config)
        rendered = render(result)
        parsed = parse(rendered)

        self.assertFalse(parsed.run_readiness.run_ready)
        self.assertEqual(
            parsed.run_readiness.readiness_note,
            "Cases need more detail in expected behavior.",
        )

    def test_fresh_workbook_with_empty_readiness_note_is_valid(self):
        """Fresh workbook with empty READINESS_NOTE is valid at grammar level.

        The non-empty requirement is a reconcile_readiness response contract,
        not a workbook grammar rule. Fresh workbooks must remain parseable.
        """
        workbook = _make_brief_only_workbook()
        # run_ready=False, readiness_note="" — this is the fresh/init state.
        self.assertFalse(workbook.run_readiness.run_ready)
        self.assertEqual(workbook.run_readiness.readiness_note, "")
        # Must render and parse without error.
        rendered = render(workbook)
        parsed = parse(rendered)
        self.assertFalse(parsed.run_readiness.run_ready)
        self.assertEqual(parsed.run_readiness.readiness_note, "")


class TestReconcileReadinessFailurePaths(unittest.TestCase):
    def test_adapter_non_zero_exit(self):
        workbook = _make_workbook_with_directions_and_cases()
        config = PreparationConfig(
            adapter=_fixture("preparation_adapter_fail.py"),
            provider="test",
            model="test",
        )
        with self.assertRaises(PreparationError) as ctx:
            execute_reconcile_readiness(workbook, config)
        self.assertIn("exited with code", str(ctx.exception))


# ── CLI: prepare-directions ──────────────────────────────────────────────────


class TestCLIPrepareDirections(unittest.TestCase):
    def test_happy_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create workbook with brief only.
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Generated 2 directions", result.stdout)
            self.assertIn("Workbook updated", result.stdout)

            # Verify workbook was updated and is parseable.
            with open(wb_path) as f:
                updated = parse(f.read())
            self.assertEqual(len(updated.directions), 2)
            self.assertEqual(updated.directions[0].direction_id, "correctness")

    def test_missing_workbook(self):
        result = _run_cli(
            "prepare-directions",
            "/nonexistent/wb.md",
            "--preparation-config",
            _fixture("preparation_ok.json"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr.lower())

    def test_empty_brief_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create workbook with empty brief.
            wb = _make_brief_only_workbook()
            wb.brief = ""
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no user content", result.stderr.lower())

    def test_template_only_brief_rejected(self):
        """A fresh workbook template with untouched brief scaffolding must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = init_workbook("gate-test", output_dir=tmpdir)
            with open(wb_path, encoding="utf-8") as fh:
                wb = parse(fh.read())
            _fill_target_for_init_workbook(wb)
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no user content", result.stderr.lower())

    def test_minimally_filled_brief_accepted(self):
        """A brief with at least one human-authored line must pass the gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = init_workbook("filled-test", output_dir=tmpdir)
            with open(wb_path) as f:
                wb = parse(f.read())

            _fill_target_for_init_workbook(wb)
            # Add minimal user content to the template brief.
            wb.brief += "\nTesting the echo workflow."
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Generated 2 directions", result.stdout)

    def test_invalid_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            # Config with missing field.
            bad_config = os.path.join(tmpdir, "bad.json")
            with open(bad_config, "w") as f:
                json.dump({"adapter": "./a.py"}, f)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                bad_config,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())

    def test_requires_preparation_config_flag(self):
        result = _run_cli(
            "prepare-directions",
            "/tmp/some.workbook.md",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_failing_adapter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            fail_config = {
                "adapter": _fixture("preparation_adapter_fail.py"),
                "provider": "test",
                "model": "fail",
            }
            config_path = os.path.join(tmpdir, "fail.json")
            with open(config_path, "w") as f:
                json.dump(fail_config, f)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                config_path,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stderr.lower())


# ── CLI: prepare-cases ───────────────────────────────────────────────────────


class TestCLIPrepareCases(unittest.TestCase):
    def test_happy_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-cases",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Generated 2 cases", result.stdout)

            with open(wb_path) as f:
                updated = parse(f.read())
            self.assertEqual(len(updated.cases), 2)
            # Directions preserved.
            self.assertEqual(len(updated.directions), 2)

    def test_no_directions_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-cases",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: no directions → stage is needs_directions.
            self.assertIn("needs_directions", result.stderr.lower())
            self.assertIn("needs_cases", result.stderr.lower())

    def test_requires_preparation_config_flag(self):
        result = _run_cli(
            "prepare-cases",
            "/tmp/some.workbook.md",
        )
        self.assertNotEqual(result.returncode, 0)


# ── CLI: prepare-readiness ───────────────────────────────────────────────────


class TestCLIPrepareReadiness(unittest.TestCase):
    def test_happy_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions_and_cases()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-readiness",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("RUN_READY: yes", result.stdout)

            with open(wb_path) as f:
                updated = parse(f.read())
            self.assertTrue(updated.run_readiness.run_ready)
            self.assertEqual(len(updated.directions), 2)
            self.assertEqual(len(updated.cases), 2)

    def test_no_directions_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-readiness",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: no directions → stage is needs_directions.
            self.assertIn("needs_directions", result.stderr.lower())
            self.assertIn("needs_readiness", result.stderr.lower())

    def test_no_cases_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-readiness",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: no cases → stage is needs_cases.
            self.assertIn("needs_cases", result.stderr.lower())
            self.assertIn("needs_readiness", result.stderr.lower())

    def test_requires_preparation_config_flag(self):
        result = _run_cli(
            "prepare-readiness",
            "/tmp/some.workbook.md",
        )
        self.assertNotEqual(result.returncode, 0)


# ── Stage re-entry rejection ────────────────────────────────────────────────


class TestStageReentryRejection(unittest.TestCase):
    """Verify that preparation commands reject invalid stage re-entry:
    same-stage re-entry, downstream-state protection, and artifact-ref protection."""

    def _config(self):
        return PreparationConfig(
            adapter=_fixture("preparation_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )

    # ── Same-stage re-entry ──

    def test_generate_directions_rejects_when_directions_exist(self):
        """Directions already present → rejected to protect human feedback."""
        workbook = _make_workbook_with_directions()
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, self._config())
        self.assertIn("already has directions", str(ctx.exception))

    def test_generate_cases_rejects_when_cases_exist(self):
        """Cases already present → rejected to protect human feedback."""
        workbook = _make_workbook_with_directions_and_cases()
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_cases(workbook, self._config())
        self.assertIn("already has cases", str(ctx.exception))

    # ── Downstream-state protection ──

    def test_generate_directions_rejects_when_cases_exist(self):
        """Cases exist (but no directions) → rejected because cases would become stale.
        Uses a model-level workbook without directions to isolate this guard."""
        workbook = _make_brief_only_workbook()
        workbook.cases = [
            Case(
                case_id="orphan",
                input="Input",
                target_directions=["nonexistent"],
                expected_behavior="Expected.",
                behavior_facet="orphan_case_behavior",
                testing_lens="regression_guardrail",
                covered_user_priority_sections=list(_REQUIRED_PRIORITY_IDS),
                source_rationale="Grounded in the explicit adapter source.",
                context=None,
                notes=None,
                human_instruction=HumanFeedback(""),
            ),
        ]
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, self._config())
        self.assertIn("already has cases", str(ctx.exception))

    def test_generate_directions_rejects_when_run_ready(self):
        """RUN_READY: yes (but no directions) → rejected because readiness would be stale."""
        workbook = _make_brief_only_workbook()
        workbook.run_readiness = RunReadiness(run_ready=True, readiness_note="Ready.")
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, self._config())
        self.assertIn("RUN_READY: yes", str(ctx.exception))

    def test_generate_cases_rejects_when_run_ready(self):
        workbook = _make_workbook_with_directions()
        workbook.run_readiness = RunReadiness(run_ready=True, readiness_note="Ready.")
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_cases(workbook, self._config())
        self.assertIn("RUN_READY: yes", str(ctx.exception))

    # ── Artifact-reference protection ──

    def test_generate_directions_rejects_when_run_artifact_ref(self):
        workbook = _make_brief_only_workbook()
        workbook.artifact_references.run = "run_abc.json"
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, self._config())
        self.assertIn("artifact references", str(ctx.exception))
        self.assertIn("run=", str(ctx.exception))

    def test_generate_directions_rejects_when_analysis_artifact_ref(self):
        workbook = _make_brief_only_workbook()
        workbook.artifact_references.analysis = "analysis_abc.md"
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, self._config())
        self.assertIn("artifact references", str(ctx.exception))
        self.assertIn("analysis=", str(ctx.exception))

    def test_generate_cases_rejects_when_artifact_refs_set(self):
        workbook = _make_workbook_with_directions()
        workbook.artifact_references.run = "run_abc.json"
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_cases(workbook, self._config())
        self.assertIn("artifact references", str(ctx.exception))

    def test_reconcile_readiness_rejects_when_artifact_refs_set(self):
        workbook = _make_workbook_with_directions_and_cases()
        workbook.artifact_references.compare = "compare_abc.md"
        with self.assertRaises(PreparationError) as ctx:
            execute_reconcile_readiness(workbook, self._config())
        self.assertIn("artifact references", str(ctx.exception))
        self.assertIn("compare=", str(ctx.exception))

    def test_artifact_ref_check_fires_before_same_stage_guard(self):
        """Artifact-ref guard has highest priority — fires even when other guards would too."""
        workbook = _make_workbook_with_directions()
        workbook.artifact_references.run = "run_abc.json"
        # This workbook has directions (same-stage guard would fire) AND artifact refs.
        # Artifact-ref guard should fire first.
        with self.assertRaises(PreparationError) as ctx:
            execute_generate_directions(workbook, self._config())
        self.assertIn("artifact references", str(ctx.exception))

    # ── Happy paths ──

    def test_generate_directions_allowed_when_no_downstream_state(self):
        """Happy path: directions can be generated when no cases or readiness exist."""
        workbook = _make_brief_only_workbook()
        result = execute_generate_directions(workbook, self._config())
        self.assertEqual(len(result.directions), 2)

    def test_generate_cases_allowed_when_run_not_ready(self):
        """Happy path: cases can be generated when run_ready is False."""
        workbook = _make_workbook_with_directions()
        result = execute_generate_cases(workbook, self._config())
        self.assertEqual(len(result.cases), 2)

    def test_reconcile_readiness_allowed_when_no_artifact_refs(self):
        """Happy path: reconcile works with no artifact references."""
        workbook = _make_workbook_with_directions_and_cases()
        result = execute_reconcile_readiness(workbook, self._config())
        self.assertTrue(result.run_readiness.run_ready)


class TestStageReentryCLI(unittest.TestCase):
    """Verify that CLI commands reject stage re-entry with proper error messages."""

    def test_cli_prepare_directions_rejects_when_directions_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: directions exist → stage is needs_cases.
            self.assertIn("needs_cases", result.stderr.lower())
            self.assertIn("needs_directions", result.stderr.lower())

    def test_cli_prepare_cases_rejects_when_cases_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions_and_cases()
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-cases",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: cases exist → stage is needs_readiness.
            self.assertIn("needs_readiness", result.stderr.lower())
            self.assertIn("needs_cases", result.stderr.lower())

    def test_cli_prepare_cases_rejects_when_run_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions()
            wb.run_readiness = RunReadiness(run_ready=True, readiness_note="Ready.")
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-cases",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: run_ready → stage is prepared.
            self.assertIn("prepared", result.stderr.lower())
            self.assertIn("needs_cases", result.stderr.lower())

    def test_cli_prepare_directions_rejects_when_artifact_refs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_brief_only_workbook()
            wb.artifact_references.run = "run_abc.json"
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: artifact refs → stage is prepared.
            self.assertIn("prepared", result.stderr.lower())
            self.assertIn("needs_directions", result.stderr.lower())

    def test_cli_prepare_cases_rejects_when_artifact_refs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions()
            wb.artifact_references.analysis = "analysis_abc.md"
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-cases",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: artifact refs → stage is prepared.
            self.assertIn("prepared", result.stderr.lower())
            self.assertIn("needs_cases", result.stderr.lower())

    def test_cli_prepare_readiness_rejects_when_artifact_refs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_workbook_with_directions_and_cases()
            wb.artifact_references.run = "run_abc.json"
            wb_path = os.path.join(tmpdir, "test.workbook.md")
            _save_workbook(wb, wb_path)

            result = _run_cli(
                "prepare-readiness",
                wb_path,
                "--preparation-config",
                _fixture("preparation_ok.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            # CLI validates stage intent: artifact refs → stage is prepared.
            self.assertIn("prepared", result.stderr.lower())
            self.assertIn("needs_readiness", result.stderr.lower())


# ── No-normalization of empty-string optional fields ────────────────────────


class TestNoNormalizationOfEmptyOptionals(unittest.TestCase):
    """Verify that adapter-returned empty-string optional fields are preserved
    exactly, not silently coerced to None."""

    def _config_empty_optionals(self):
        return PreparationConfig(
            adapter=_fixture("preparation_adapter_empty_optionals.py"),
            provider="test",
            model="echo-v1",
        )

    def test_generate_cases_preserves_empty_string_context(self):
        workbook = _make_workbook_with_directions()
        config = self._config_empty_optionals()
        result = execute_generate_cases(workbook, config)

        for case in result.cases:
            self.assertEqual(
                case.context,
                "",
                msg=(
                    f"Case {case.case_id}: context should be empty string '', not {case.context!r}"
                ),
            )

    def test_generate_cases_preserves_empty_string_notes(self):
        workbook = _make_workbook_with_directions()
        config = self._config_empty_optionals()
        result = execute_generate_cases(workbook, config)

        for case in result.cases:
            self.assertEqual(
                case.notes,
                "",
                msg=(f"Case {case.case_id}: notes should be empty string '', not {case.notes!r}"),
            )

    def test_reconcile_preserves_empty_string_optionals(self):
        workbook = _make_workbook_with_directions_and_cases()
        config = self._config_empty_optionals()
        result = execute_reconcile_readiness(workbook, config)

        for case in result.cases:
            self.assertEqual(
                case.context,
                "",
                msg=(
                    f"Case {case.case_id}: context should be empty string '', not {case.context!r}"
                ),
            )
            self.assertEqual(
                case.notes,
                "",
                msg=(f"Case {case.case_id}: notes should be empty string '', not {case.notes!r}"),
            )

    def test_empty_optionals_roundtrip(self):
        """Verify empty-string optionals survive render → parse round-trip."""
        workbook = _make_workbook_with_directions()
        config = self._config_empty_optionals()
        result = execute_generate_cases(workbook, config)

        rendered = render(result)
        parsed = parse(rendered)

        for case in parsed.cases:
            # After round-trip, empty-string fields that render as present
            # but empty content are preserved as empty strings by the parser.
            self.assertIsNotNone(
                case.context, msg=(f"Case {case.case_id}: context should survive round-trip")
            )
            self.assertIsNotNone(
                case.notes, msg=(f"Case {case.case_id}: notes should survive round-trip")
            )


# ── End-to-end: full preparation pipeline ────────────────────────────────────


class TestPreparationPipelineEndToEnd(unittest.TestCase):
    """Full pipeline: workbook template → prepare-directions → prepare-cases → prepare-readiness."""

    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = init_workbook("e2e-test", output_dir=tmpdir)
            self.assertTrue(os.path.isfile(wb_path))

            # Fill the brief (simulates human editing).
            with open(wb_path) as f:
                wb = parse(f.read())
            _fill_target_for_init_workbook(wb)
            wb.brief = "Test the text echo workflow for correctness and edge cases."
            _save_workbook(wb, wb_path)

            config_path = _fixture("preparation_ok.json")

            # Step 2: Generate directions.
            result = _run_cli(
                "prepare-directions",
                wb_path,
                "--preparation-config",
                config_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Generated 2 directions", result.stdout)

            # Verify workbook is parseable after directions.
            with open(wb_path) as f:
                wb = parse(f.read())
            self.assertEqual(len(wb.directions), 2)
            self.assertEqual(len(wb.cases), 0)
            self.assertFalse(wb.run_readiness.run_ready)

            # Step 3: Generate cases.
            result = _run_cli(
                "prepare-cases",
                wb_path,
                "--preparation-config",
                config_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Generated 2 cases", result.stdout)

            with open(wb_path) as f:
                wb = parse(f.read())
            self.assertEqual(len(wb.directions), 2)
            self.assertEqual(len(wb.cases), 2)
            self.assertFalse(wb.run_readiness.run_ready)

            # Step 4: Reconcile readiness.
            result = _run_cli(
                "prepare-readiness",
                wb_path,
                "--preparation-config",
                config_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("RUN_READY: yes", result.stdout)

            with open(wb_path) as f:
                wb = parse(f.read())
            self.assertTrue(wb.run_readiness.run_ready)
            self.assertEqual(len(wb.directions), 2)
            self.assertEqual(len(wb.cases), 2)

            # Final workbook is valid and ready for run.
            self.assertTrue(wb.run_readiness.run_ready)
            self.assertIn(
                "All cases reconciled and ready.",
                wb.run_readiness.readiness_note,
            )


if __name__ == "__main__":
    unittest.main()
