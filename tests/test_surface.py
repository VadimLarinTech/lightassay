"""Tests for the L1 public control surface.

Covers:
- Top-level import surface (all L1 exports reachable from package root)
- init_workbook: create workbook via L1
- EvalSession.state(): file-truth-based state snapshot
- EvalSession.release(): explicit lifecycle, file-safety
- EvalSession.prepare(): semantic one-step progression through all stages
- EvalSession.can_run() / why_not(): readiness checks
- EvalSession.open_diagnostics(): diagnostics door returns handle
- EvalError: L1 public error boundary (engine errors do not leak)

Run with:
    cd lightassay && python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from lightassay import (
    AnalyzeResult,
    CompareResult,
    EvalError,
    EvalSession,
    EvalState,
    EvalTarget,
    ExploreResult,
    PreparationStage,
    PrepareResult,
    QuickTryResult,
    RefineResult,
    RunResult,
    __version__,
    explore_workbook,
    init_workbook,
    open_session,
    quick_try,
    quick_try_workbook,
    refine_workbook,
)
from lightassay.run_artifact_io import save_run_artifact
from lightassay.run_models import Aggregate, CaseRecord, CaseUsage, RunArtifact
from lightassay.types import DiagnosticsHandle

# Also import internal helpers needed to build workbook files for tests.
from lightassay.workbook_models import (
    ArtifactReferences,
    Case,
    Direction,
    HumanFeedback,
    RunReadiness,
    Target,
    Workbook,
)
from lightassay.workbook_renderer import render

# ── Helpers ──────────────────────────────────────────────────────────────────

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    return os.path.join(_FIXTURES, name)


def _make_brief_only_workbook() -> Workbook:
    return Workbook(
        target=Target(
            kind="workflow",
            name="text_echo",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief="Test the text echo workflow for correctness and edge cases.",
        directions_global_instruction=HumanFeedback(""),
        directions=[],
        cases_global_instruction=HumanFeedback(""),
        cases=[],
        run_readiness=RunReadiness(run_ready=False, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _make_workbook_with_directions() -> Workbook:
    return Workbook(
        target=Target(
            kind="workflow",
            name="text_echo",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief="Test the text echo workflow for correctness and edge cases.",
        directions_global_instruction=HumanFeedback(""),
        directions=[
            Direction(
                direction_id="correctness",
                body="Verify output correctness.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                human_instruction=HumanFeedback(""),
            ),
            Direction(
                direction_id="edge-cases",
                body="Test boundary inputs.",
                behavior_facet="edge_case_behavior",
                testing_lens="boundary_and_negative",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                human_instruction=HumanFeedback(""),
            ),
        ],
        cases_global_instruction=HumanFeedback(""),
        cases=[],
        run_readiness=RunReadiness(run_ready=False, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _make_workbook_with_directions_and_cases() -> Workbook:
    return Workbook(
        target=Target(
            kind="workflow",
            name="text_echo",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief="Test the text echo workflow for correctness and edge cases.",
        directions_global_instruction=HumanFeedback(""),
        directions=[
            Direction(
                direction_id="correctness",
                body="Verify output correctness.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                human_instruction=HumanFeedback(""),
            ),
            Direction(
                direction_id="edge-cases",
                body="Test boundary inputs.",
                behavior_facet="edge_case_behavior",
                testing_lens="boundary_and_negative",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
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
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
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
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                context=None,
                notes=None,
                human_instruction=HumanFeedback(""),
            ),
        ],
        run_readiness=RunReadiness(run_ready=False, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _make_run_ready_workbook() -> Workbook:
    wb = _make_workbook_with_directions_and_cases()
    wb.run_readiness = RunReadiness(run_ready=True, readiness_note="All cases reconciled.")
    return wb


def _save_wb(workbook: Workbook, path: str) -> None:
    text = render(workbook)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_workbook_for_test(path: str) -> Workbook:
    from lightassay.workbook_parser import parse

    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())


def _prep_config_path() -> str:
    return _fixture("preparation_ok.json")


def _make_completed_run_artifact(workbook_path: str) -> RunArtifact:
    return RunArtifact(
        run_id="seed_run_1",
        workflow_id="echo-wf",
        workbook_path=workbook_path,
        workbook_sha256="a" * 64,
        workflow_config_sha256="b" * 64,
        provider="test-provider",
        model="test-model",
        target_kind="workflow",
        target_name="text_echo",
        target_locator="tests.fixtures.adapter_echo",
        target_boundary="text echo workflow boundary",
        target_sources=["tests/fixtures/adapter_echo.py"],
        started_at="2025-01-01T00:00:00+00:00",
        finished_at="2025-01-01T00:01:00+00:00",
        status="failed",
        cases=[
            CaseRecord(
                case_id="case-ok",
                input="hello",
                context=None,
                expected_behavior="Echo hello",
                raw_response="Echo: hello",
                parsed_response={"echoed": "hello"},
                duration_ms=10,
                usage=CaseUsage(input_tokens=1, output_tokens=2),
                status="completed",
                execution_error=None,
            ),
            CaseRecord(
                case_id="case-fail",
                input="",
                context=None,
                expected_behavior="Reject empty input",
                raw_response=None,
                parsed_response=None,
                duration_ms=5,
                usage=None,
                status="failed_execution",
                execution_error="Adapter exited with code 1",
            ),
        ],
        aggregate=Aggregate(
            total_cases=2,
            completed_cases=1,
            failed_cases=1,
            total_duration_ms=15,
            total_input_tokens=1,
            total_output_tokens=2,
        ),
    )


# ── Import surface ───────────────────────────────────────────────────────────


class TestImportSurface(unittest.TestCase):
    """Verify that all L1 exports are reachable from the package root."""

    def test_version_exists(self):
        self.assertIsInstance(__version__, str)

    def test_open_session_callable(self):
        self.assertTrue(callable(open_session))

    def test_init_workbook_callable(self):
        self.assertTrue(callable(init_workbook))

    def test_quick_try_callable(self):
        self.assertTrue(callable(quick_try))

    def test_refine_workbook_callable(self):
        self.assertTrue(callable(refine_workbook))

    def test_explore_workbook_callable(self):
        self.assertTrue(callable(explore_workbook))

    def test_eval_session_class(self):
        self.assertTrue(isinstance(EvalSession, type))

    def test_types_importable(self):
        # All L1 types must be importable from the top-level package.
        self.assertTrue(isinstance(EvalState, type))
        self.assertTrue(isinstance(PreparationStage, type))
        self.assertTrue(isinstance(PrepareResult, type))
        self.assertTrue(isinstance(QuickTryResult, type))
        self.assertTrue(isinstance(RefineResult, type))
        self.assertTrue(isinstance(ExploreResult, type))
        self.assertTrue(isinstance(RunResult, type))
        self.assertTrue(isinstance(AnalyzeResult, type))
        self.assertTrue(isinstance(CompareResult, type))

    def test_diagnostics_handle_not_in_top_level_all(self):
        """DiagnosticsHandle must NOT be in the ordinary top-level __all__."""
        import lightassay

        self.assertNotIn("DiagnosticsHandle", lightassay.__all__)

    def test_diagnostics_handle_accessible_via_types_module(self):
        """DiagnosticsHandle must still be accessible via the types module."""
        from lightassay.types import DiagnosticsHandle as DH

        self.assertTrue(isinstance(DH, type))

    def test_eval_error_importable(self):
        self.assertTrue(issubclass(EvalError, Exception))

    def test_preparation_stage_values(self):
        self.assertEqual(PreparationStage.NEEDS_DIRECTIONS.value, "needs_directions")
        self.assertEqual(PreparationStage.NEEDS_CASES.value, "needs_cases")
        self.assertEqual(PreparationStage.NEEDS_READINESS.value, "needs_readiness")
        self.assertEqual(PreparationStage.PREPARED.value, "prepared")

    def test_all_exports_listed(self):
        import lightassay

        for name in lightassay.__all__:
            self.assertTrue(
                hasattr(lightassay, name),
                f"{name!r} listed in __all__ but not importable",
            )


# ── init_workbook ────────────────────────────────────────────────────────────


class TestInitWorkbook(unittest.TestCase):
    def test_creates_workbook_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = init_workbook("test-wb", output_dir=d)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(path.endswith("test-wb.workbook.md"))

    def test_returns_absolute_path(self):
        with tempfile.TemporaryDirectory() as d:
            path = init_workbook("test-abs", output_dir=d)
            self.assertTrue(os.path.isabs(path))

    def test_rejects_invalid_name(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(EvalError) as ctx:
                init_workbook("bad name!", output_dir=d)
            self.assertIn("invalid", str(ctx.exception))

    def test_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            init_workbook("dup", output_dir=d)
            with self.assertRaises(EvalError) as ctx:
                init_workbook("dup", output_dir=d)
            self.assertIn("already exists", str(ctx.exception))

    def test_rejects_missing_dir(self):
        with self.assertRaises(EvalError) as ctx:
            init_workbook("test", output_dir="/nonexistent-dir-12345")
        self.assertIn("does not exist", str(ctx.exception))


class TestQuickTry(unittest.TestCase):
    def test_quick_try_creates_real_workbook_shape(self):
        with tempfile.TemporaryDirectory() as d:
            result = quick_try(
                "quick-test",
                target=EvalTarget(
                    kind="workflow",
                    name="text_echo",
                    locator="tests.fixtures.adapter_echo",
                    boundary="text echo workflow boundary",
                    sources=["tests/fixtures/adapter_echo.py"],
                ),
                user_request="Проверь корректность echo workflow.",
                preparation_config=_prep_config_path(),
                output_dir=d,
            )

            self.assertIsInstance(result, QuickTryResult)
            self.assertTrue(os.path.isfile(result.workbook_path))
            self.assertEqual(result.state.direction_count, 1)
            self.assertEqual(result.state.case_count, 1)
            self.assertTrue(result.state.workbook_run_ready)
            self.assertFalse(result.state.run_ready)
            self.assertEqual(len(result.assumptions), 3)

            with open(result.workbook_path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("## Target", text)
            self.assertIn("## Brief", text)
            self.assertIn("## Directions", text)
            self.assertIn("## Cases", text)
            self.assertIn(
                "Quick try is intentionally limited to one direction and one case.",
                text,
            )

    def test_quick_try_workbook_bootstraps_existing_start_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            workbook_path = init_workbook("quick-existing", output_dir=d)
            seeded = _read_workbook_for_test(workbook_path)
            seeded.target = Target(
                kind="workflow",
                name="text_echo",
                locator="tests.fixtures.adapter_echo",
                boundary="text echo workflow boundary",
                sources=["tests/fixtures/adapter_echo.py"],
                notes="",
            )
            _save_wb(seeded, workbook_path)

            result = quick_try_workbook(
                workbook_path,
                user_request="Покажи минимальный quick try по уже выбранному target.",
                preparation_config=_prep_config_path(),
            )

            self.assertIsInstance(result, QuickTryResult)
            self.assertEqual(os.path.abspath(workbook_path), result.workbook_path)
            self.assertEqual(result.state.direction_count, 1)
            self.assertEqual(result.state.case_count, 1)
            self.assertTrue(result.state.workbook_run_ready)


class TestRefineWorkbook(unittest.TestCase):
    def test_refine_workbook_creates_new_planning_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            source_path = os.path.join(d, "source.workbook.md")
            _save_wb(_make_run_ready_workbook(), source_path)

            result = refine_workbook(
                source_path,
                name="refined-suite",
                refinement_request="Добавь больше пограничных кейсов и усили негативные сценарии.",
                output_dir=d,
            )

            self.assertIsInstance(result, RefineResult)
            self.assertTrue(os.path.isfile(result.workbook_path))
            self.assertEqual(result.inherited_direction_count, 2)
            self.assertEqual(result.inherited_case_count, 2)
            self.assertTrue(result.state.planning_ready)
            self.assertEqual(result.state.direction_count, 2)
            self.assertEqual(result.state.case_count, 2)
            self.assertFalse(result.state.workbook_run_ready)

            with open(result.workbook_path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("Refinement context from existing suite:", text)
            self.assertIn("Refinement request: Добавь больше пограничных кейсов", text)
            self.assertIn("### Direction: correctness", text)
            self.assertIn("### Case: case-1", text)


class TestExploreWorkbook(unittest.TestCase):
    def test_explore_workbook_creates_bounded_follow_up_suite(self):
        with tempfile.TemporaryDirectory() as d:
            source_path = os.path.join(d, "source.workbook.md")
            _save_wb(_make_run_ready_workbook(), source_path)

            run_artifact = _make_completed_run_artifact(source_path)
            run_artifact_path = os.path.join(d, "seed_run.json")
            save_run_artifact(run_artifact, run_artifact_path)

            result = explore_workbook(
                source_path,
                run_artifact_path=run_artifact_path,
                workflow_config=_fixture("workflow_config_echo.json"),
                name="explore-suite",
                exploration_goal="Ищи слабые места вокруг неуспешных кейсов.",
                preparation_config=_prep_config_path(),
                max_cases=1,
                max_iterations=2,
                output_dir=d,
            )

            self.assertIsInstance(result, ExploreResult)
            self.assertTrue(os.path.isfile(result.workbook_path))
            self.assertEqual(result.seeded_from_run_id, "seed_run_1")
            self.assertEqual(result.failed_case_count, 1)
            self.assertEqual(result.iteration_count, 2)
            self.assertEqual(len(result.iteration_run_artifact_paths), 2)
            for artifact_path in result.iteration_run_artifact_paths:
                self.assertTrue(os.path.isfile(artifact_path))
            self.assertGreaterEqual(result.state.direction_count, 1)
            self.assertEqual(result.state.case_count, 1)
            self.assertTrue(result.state.workbook_run_ready)
            self.assertTrue(result.state.execution_binding_ready)
            self.assertTrue(result.state.run_ready)

            with open(result.workbook_path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("Exploratory investigation context:", text)
            self.assertIn("Exploration goal: Ищи слабые места вокруг неуспешных кейсов.", text)
            self.assertIn("Bounded case budget: 1", text)
            self.assertIn("case-fail", text)
            self.assertIn("Exploration iteration trace:", text)
            self.assertIn("Iteration 1:", text)
            self.assertIn("Iteration 2:", text)


# ── EvalSession.state() ─────────────────────────────────────────────────────


class TestState(unittest.TestCase):
    def test_brief_only_state(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            state = session.state()

            self.assertEqual(state.workbook_path, os.path.abspath(wb_path))
            self.assertEqual(state.preparation_stage, PreparationStage.NEEDS_DIRECTIONS)
            self.assertTrue(state.has_target_content)
            self.assertEqual(state.source_reference_count, 1)
            self.assertTrue(state.has_brief_content)
            self.assertTrue(state.planning_ready)
            self.assertFalse(state.execution_binding_ready)
            self.assertEqual(state.direction_count, 0)
            self.assertEqual(state.case_count, 0)
            self.assertFalse(state.workbook_run_ready)
            self.assertFalse(state.run_ready)
            self.assertIsNone(state.run_artifact)
            self.assertIsNone(state.analysis_artifact)
            self.assertIsNone(state.compare_artifact)

    def test_directions_state(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_workbook_with_directions(), wb_path)
            session = open_session(wb_path)
            state = session.state()

            self.assertEqual(state.preparation_stage, PreparationStage.NEEDS_CASES)
            self.assertEqual(state.direction_count, 2)
            self.assertEqual(state.case_count, 0)

    def test_directions_and_cases_state(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_workbook_with_directions_and_cases(), wb_path)
            session = open_session(wb_path)
            state = session.state()

            self.assertEqual(state.preparation_stage, PreparationStage.NEEDS_READINESS)
            self.assertEqual(state.direction_count, 2)
            self.assertEqual(state.case_count, 2)

    def test_run_ready_state(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)
            state = session.state()

            self.assertEqual(state.preparation_stage, PreparationStage.PREPARED)
            self.assertTrue(state.workbook_run_ready)
            self.assertFalse(state.execution_binding_ready)
            self.assertFalse(state.run_ready)

    def test_run_ready_state_with_binding(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_config_echo.json"),
            )
            state = session.state()

            self.assertTrue(state.workbook_run_ready)
            self.assertTrue(state.execution_binding_ready)
            self.assertTrue(state.run_ready)

    def test_state_is_frozen(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            state = session.state()
            with self.assertRaises(AttributeError):
                state.run_ready = True  # type: ignore


# ── EvalSession.release() ───────────────────────────────────────────────────


class TestRelease(unittest.TestCase):
    def test_release_prevents_state(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            session.release()
            with self.assertRaises(EvalError) as ctx:
                session.state()
            self.assertIn("released", str(ctx.exception))

    def test_release_prevents_prepare(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())
            session.release()
            with self.assertRaises(EvalError):
                session.prepare()

    def test_release_prevents_can_run(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            session.release()
            with self.assertRaises(EvalError):
                session.can_run()

    def test_release_prevents_open_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            session.release()
            with self.assertRaises(EvalError):
                session.open_diagnostics()

    def test_double_release_raises(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            session.release()
            with self.assertRaises(EvalError):
                session.release()

    def test_released_property(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            self.assertFalse(session.released)
            session.release()
            self.assertTrue(session.released)

    def test_release_does_not_mutate_workbook(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            with open(wb_path) as fh:
                before = fh.read()
            session = open_session(wb_path)
            session.release()
            with open(wb_path) as fh:
                after = fh.read()
            self.assertEqual(before, after)


# ── EvalSession.prepare() ───────────────────────────────────────────────────


class TestPrepare(unittest.TestCase):
    """Test semantic one-step prepare progression."""

    def test_prepare_generates_directions(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            result = session.prepare()
            self.assertIsInstance(result, PrepareResult)
            self.assertEqual(result.stage_completed, PreparationStage.NEEDS_DIRECTIONS)
            self.assertGreater(result.state.direction_count, 0)
            self.assertEqual(result.state.case_count, 0)
            self.assertEqual(result.state.preparation_stage, PreparationStage.NEEDS_CASES)

    def test_prepare_generates_cases(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_workbook_with_directions(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            result = session.prepare()
            self.assertEqual(result.stage_completed, PreparationStage.NEEDS_CASES)
            self.assertGreater(result.state.case_count, 0)
            self.assertEqual(result.state.preparation_stage, PreparationStage.NEEDS_READINESS)

    def test_prepare_reconciles_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_workbook_with_directions_and_cases(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            result = session.prepare()
            self.assertEqual(result.stage_completed, PreparationStage.NEEDS_READINESS)
            self.assertTrue(result.state.workbook_run_ready)
            self.assertFalse(result.state.run_ready)
            self.assertEqual(result.state.preparation_stage, PreparationStage.PREPARED)

    def test_full_prepare_progression(self):
        """Three sequential prepare() calls advance through all stages."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            # Step 1: directions.
            r1 = session.prepare()
            self.assertEqual(r1.stage_completed, PreparationStage.NEEDS_DIRECTIONS)
            self.assertEqual(r1.state.preparation_stage, PreparationStage.NEEDS_CASES)

            # Step 2: cases.
            r2 = session.prepare()
            self.assertEqual(r2.stage_completed, PreparationStage.NEEDS_CASES)
            self.assertEqual(r2.state.preparation_stage, PreparationStage.NEEDS_READINESS)

            # Step 3: readiness.
            r3 = session.prepare()
            self.assertEqual(r3.stage_completed, PreparationStage.NEEDS_READINESS)
            self.assertEqual(r3.state.preparation_stage, PreparationStage.PREPARED)
            self.assertTrue(r3.state.workbook_run_ready)
            self.assertFalse(r3.state.run_ready)

    def test_prepare_when_already_prepared_raises(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            with self.assertRaises(EvalError) as ctx:
                session.prepare()
            self.assertIn("already complete", str(ctx.exception))

    def test_prepare_without_config_raises(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)  # no preparation_config

            with self.assertRaises(EvalError) as ctx:
                session.prepare()
            self.assertIn("preparation_config", str(ctx.exception))

    def test_prepare_with_empty_brief_raises(self):
        with tempfile.TemporaryDirectory() as d:
            os.path.join(d, "test.workbook.md")
            # Create workbook from init (has template scaffolding only).
            wb_path_created = init_workbook("test-empty", output_dir=d)
            session = open_session(wb_path_created, preparation_config=_prep_config_path())

            with self.assertRaises(EvalError) as ctx:
                session.prepare()
            self.assertIn("brief", str(ctx.exception).lower())

    def test_prepare_with_artifact_refs_raises(self):
        """Workbook with artifact references cannot be prepared."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            wb = _make_brief_only_workbook()
            wb.artifact_references.run = "/some/path.json"
            _save_wb(wb, wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            with self.assertRaises(EvalError) as ctx:
                session.prepare()
            self.assertIn("already complete", str(ctx.exception))

    def test_prepare_persists_to_file(self):
        """prepare() must write changes back to the workbook file."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())
            session.prepare()

            # Open a fresh session on the same file and verify state.
            session2 = open_session(wb_path)
            state = session2.state()
            self.assertGreater(state.direction_count, 0)
            self.assertEqual(state.preparation_stage, PreparationStage.NEEDS_CASES)


# ── EvalSession.can_run() / why_not() ───────────────────────────────────────


class TestCanRun(unittest.TestCase):
    def test_can_run_true(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path, workflow_config=_fixture("workflow_config_echo.json"))
            self.assertTrue(session.can_run())

    def test_can_run_false_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, workflow_config=_fixture("workflow_config_echo.json"))
            self.assertFalse(session.can_run())

    def test_can_run_false_no_config(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)  # no workflow_config
            self.assertFalse(session.can_run())

    def test_can_run_false_missing_config_file(self):
        """can_run() must be False when workflow_config path points to a missing file."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config="/nonexistent/workflow.json",
            )
            self.assertFalse(session.can_run())

    def test_can_run_false_invalid_config_file(self):
        """can_run() must be False when workflow_config file is malformed."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            bad_config = os.path.join(d, "bad_wf.json")
            with open(bad_config, "w") as fh:
                fh.write("not json{{{")
            session = open_session(wb_path, workflow_config=bad_config)
            self.assertFalse(session.can_run())

    def test_can_run_false_config_missing_fields(self):
        """can_run() must be False when workflow_config is missing required fields."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            bad_config = os.path.join(d, "incomplete_wf.json")
            with open(bad_config, "w") as fh:
                json.dump({"adapter": "./a.py"}, fh)
            session = open_session(wb_path, workflow_config=bad_config)
            self.assertFalse(session.can_run())

    def test_why_not_reports_missing_config_file(self):
        """why_not() must explain missing workflow_config file."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config="/nonexistent/workflow.json",
            )
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("not found", reason_text)

    def test_why_not_reports_invalid_config_file(self):
        """why_not() must explain invalid workflow_config file."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            bad_config = os.path.join(d, "bad_wf.json")
            with open(bad_config, "w") as fh:
                fh.write("not json{{{")
            session = open_session(wb_path, workflow_config=bad_config)
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("invalid", reason_text)

    def test_why_not_empty_when_ready(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path, workflow_config=_fixture("workflow_config_echo.json"))
            reasons = session.why_not()
            self.assertEqual(reasons, [])

    def test_why_not_lists_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)  # no configs
            reasons = session.why_not()
            self.assertGreater(len(reasons), 0)
            reason_text = " ".join(reasons).lower()
            self.assertIn("run-ready", reason_text)
            self.assertIn("cases", reason_text)
            self.assertIn("workflow_config", reason_text)


# ── Structural viability: can_run() / why_not() with non-viable targets ─────


class TestStructuralViability(unittest.TestCase):
    """Verify that can_run()/why_not() detect structurally non-viable
    adapter/driver targets, not just config parseability."""

    def _make_session_with_config(self, tmpdir, config_dict):
        """Helper: save a run-ready workbook and a workflow config, return session."""
        wb_path = os.path.join(tmpdir, "test.workbook.md")
        _save_wb(_make_run_ready_workbook(), wb_path)
        cfg_path = os.path.join(tmpdir, "wf.json")
        with open(cfg_path, "w") as fh:
            json.dump(config_dict, fh)
        return open_session(wb_path, workflow_config=cfg_path)

    # ── Legacy adapter ─────────────────────────────────────────────────

    def test_legacy_adapter_nonexistent_file(self):
        """can_run() must be False when the legacy adapter file does not exist."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "adapter": "/nonexistent/adapter_xyz.py",
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("not found", reason_text)

    def test_legacy_adapter_not_executable(self):
        """can_run() must be False when the legacy adapter file is not executable."""
        with tempfile.TemporaryDirectory() as d:
            adapter_path = os.path.join(d, "not_executable.py")
            with open(adapter_path, "w") as fh:
                fh.write("#!/usr/bin/env python3\n")
            os.chmod(adapter_path, 0o644)  # readable, not executable
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "adapter": adapter_path,
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("not executable", reason_text)

    # ── command driver ──────────────────────────────────────────────────

    def test_command_driver_nonexistent_command(self):
        """can_run() must be False when command[0] does not exist."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "driver": {
                        "type": "command",
                        "command": ["/nonexistent/binary_xyz_12345", "--flag"],
                    },
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("not found", reason_text)

    # ── python-callable driver ──────────────────────────────────────────

    def test_python_callable_nonexistent_module(self):
        """can_run() must be False when the module cannot be imported."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "driver": {
                        "type": "python-callable",
                        "module": "nonexistent_module_xyz_12345",
                        "function": "handle",
                    },
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("cannot be imported", reason_text)

    def test_python_callable_missing_function(self):
        """can_run() must be False when the function does not exist in the module."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "driver": {
                        "type": "python-callable",
                        "module": "os",
                        "function": "nonexistent_function_xyz_12345",
                    },
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("has no attribute", reason_text)

    def test_python_callable_not_callable(self):
        """can_run() must be False when the attribute is not callable."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "driver": {
                        "type": "python-callable",
                        "module": "os",
                        "function": "sep",  # os.sep is a string, not callable
                    },
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("not callable", reason_text)

    # ── http driver ─────────────────────────────────────────────────────

    def test_http_driver_no_scheme(self):
        """can_run() must be False when the http driver URL has no valid structure."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "driver": {
                        "type": "http",
                        "url": "not-a-url",
                        "method": "POST",
                    },
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("no scheme", reason_text)

    def test_http_driver_no_host(self):
        """can_run() must be False when the http driver URL has no host."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "driver": {
                        "type": "http",
                        "url": "localhost:8080/api",
                        "method": "POST",
                    },
                },
            )
            self.assertFalse(session.can_run())
            reasons = session.why_not()
            reason_text = " ".join(reasons).lower()
            self.assertIn("no host", reason_text)

    def test_http_driver_valid_url_passes_structural_check(self):
        """can_run() must be True for an http driver with valid URL structure
        (no runtime reachability check)."""
        with tempfile.TemporaryDirectory() as d:
            session = self._make_session_with_config(
                d,
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "v1",
                    "driver": {
                        "type": "http",
                        "url": "http://127.0.0.1:9999/unreachable",
                        "method": "POST",
                    },
                },
            )
            # URL is structurally valid; no runtime reachability check.
            self.assertTrue(session.can_run())
            self.assertEqual(session.why_not(), [])

    # ── Positive: viable targets still pass ─────────────────────────────

    def test_viable_legacy_adapter_passes(self):
        """can_run() must be True for a structurally viable legacy adapter."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_config_echo.json"),
            )
            self.assertTrue(session.can_run())


# ── EvalSession.open_diagnostics() ──────────────────────────────────────────


class TestOpenDiagnostics(unittest.TestCase):
    def test_returns_diagnostics_handle(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()
            self.assertIsInstance(handle, DiagnosticsHandle)
            self.assertIsInstance(handle.state, EvalState)
            self.assertIsInstance(handle.issues, list)

    def test_diagnostics_on_incomplete_workbook(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()
            # Should report that preparation is incomplete.
            issues_text = " ".join(handle.issues).lower()
            self.assertIn("preparation", issues_text)

    def test_diagnostics_proactive(self):
        """open_diagnostics works without a prior failure (proactive entry)."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()
            self.assertIsInstance(handle, DiagnosticsHandle)
            # Prepared workbook should have fewer issues.
            self.assertIsInstance(handle.issues, list)

    def test_diagnostics_handle_frozen(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()
            with self.assertRaises(AttributeError):
                handle.state = None  # type: ignore


# ── EvalError boundary ──────────────────────────────────────────────────────


class TestErrorBoundary(unittest.TestCase):
    def test_open_session_nonexistent_raises_eval_error(self):
        with self.assertRaises(EvalError):
            open_session("/nonexistent/workbook.md")

    def test_prepare_failure_raises_eval_error_not_engine_error(self):
        """Engine errors are wrapped in EvalError, not leaked."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            # Point to an adapter that always fails.
            fail_config = os.path.join(d, "fail.json")
            with open(fail_config, "w") as fh:
                json.dump(
                    {
                        "adapter": _fixture("preparation_adapter_fail.py"),
                        "provider": "test",
                        "model": "fail-v1",
                    },
                    fh,
                )
            session = open_session(wb_path, preparation_config=fail_config)
            with self.assertRaises(EvalError) as ctx:
                session.prepare()
            # Must be EvalError, not PreparationError.
            self.assertIsInstance(ctx.exception, EvalError)
            # Original cause preserved.
            self.assertIsNotNone(ctx.exception.__cause__)

    def test_run_without_config_raises_eval_error(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)
            with self.assertRaises(EvalError) as ctx:
                session.run()
            self.assertIn("workflow_config", str(ctx.exception))

    def test_run_not_ready_raises_eval_error(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, workflow_config=_fixture("workflow_config_echo.json"))
            with self.assertRaises(EvalError) as ctx:
                session.run()
            self.assertIn("not run-ready", str(ctx.exception))


# ── open_session validation ──────────────────────────────────────────────────


class TestOpenSession(unittest.TestCase):
    def test_open_session_returns_session(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            self.assertIsInstance(session, EvalSession)

    def test_open_session_resolves_absolute_path(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            state = session.state()
            self.assertTrue(os.path.isabs(state.workbook_path))


# ── L1 happy-path: run(), analyze(), compare() ──────────────────────────────


class TestRunHappyPath(unittest.TestCase):
    """Direct L1 execution test for run() via the library surface."""

    def test_run_produces_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
            )
            result = session.run(output_dir=d)

            self.assertIsInstance(result, RunResult)
            self.assertTrue(os.path.isfile(result.artifact_path))
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.total_cases, 2)
            self.assertEqual(result.completed_cases, 2)
            self.assertEqual(result.failed_cases, 0)
            self.assertTrue(result.run_id)

            # Verify workbook artifact reference was updated.
            session2 = open_session(wb_path)
            state = session2.state()
            self.assertIsNotNone(state.run_artifact)
            self.assertIn("run_", state.run_artifact)


class TestAnalyzeHappyPath(unittest.TestCase):
    """Direct L1 execution test for analyze() via the library surface."""

    def test_analyze_produces_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )

            # First run to produce a run artifact.
            run_result = session.run(output_dir=d)
            self.assertEqual(run_result.status, "completed")

            # Then analyze.
            analyze_result = session.analyze(run_result.artifact_path, output_dir=d)

            self.assertIsInstance(analyze_result, AnalyzeResult)
            self.assertTrue(os.path.isfile(analyze_result.artifact_path))
            self.assertTrue(analyze_result.analysis_id)

            # Verify analysis content.
            with open(analyze_result.artifact_path) as fh:
                content = fh.read()
            self.assertIn("# Analysis:", content)
            self.assertIn("## Summary", content)

            # Verify workbook artifact reference was updated.
            state = session.state()
            self.assertIsNotNone(state.analysis_artifact)
            self.assertIn("analysis_", state.analysis_artifact)


class TestCompareHappyPath(unittest.TestCase):
    """Direct L1 execution test for compare() via the library surface."""

    def test_compare_produces_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )

            # Produce two run artifacts.
            run1 = session.run(output_dir=d)
            self.assertEqual(run1.status, "completed")

            # Reset workbook run reference so second run can proceed.
            # (re-read, clear run ref, re-save — simulates a fresh run)
            from lightassay.workbook_parser import parse as wb_parse

            with open(wb_path) as fh:
                wb = wb_parse(fh.read())
            wb.artifact_references.run = None
            _save_wb(wb, wb_path)

            run2 = session.run(output_dir=d)
            self.assertEqual(run2.status, "completed")

            # Compare.
            compare_result = session.compare(
                [run1.artifact_path, run2.artifact_path],
                output_dir=d,
            )

            self.assertIsInstance(compare_result, CompareResult)
            self.assertTrue(os.path.isfile(compare_result.artifact_path))
            self.assertTrue(compare_result.compare_id)

            # Verify compare content.
            with open(compare_result.artifact_path) as fh:
                content = fh.read()
            self.assertIn("# Compare:", content)


if __name__ == "__main__":
    unittest.main()
