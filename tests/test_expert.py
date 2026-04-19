"""Tests for the L3 expert surface.

Covers:
- L2→L3 escalation: DiagnosticsHandle.open_expert() returns ExpertHandle
- Visibility discipline: expert types NOT in top-level exports
- Deep inspection: workbook source, config bindings, run artifact
- Bounded low-level control: rebind_config
- Released session guard on expert opener
- Reactive diagnostics handle has no expert opener

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
    EvalError,
    EvalState,
    PreparationStage,
    open_session,
)

# L3 types — imported from expert module, NOT from top-level.
from lightassay.expert import (
    CaseRecordView,
    CaseView,
    ConfigBindingEntry,
    ConfigBindingsView,
    DirectionView,
    ExpertHandle,
    RunArtifactView,
    WorkbookSourceView,
)
from lightassay.types import DiagnosticsHandle

# Internal helpers for building workbook files.
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


def _make_run_ready_workbook() -> Workbook:
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
                human_instruction=HumanFeedback("Focus on edge cases."),
            ),
        ],
        cases_global_instruction=HumanFeedback(""),
        cases=[
            Case(
                case_id="case-1",
                input="Test input",
                target_directions=["correctness"],
                expected_behavior="Should echo.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                context="Some context.",
                notes="Important note.",
                human_instruction=HumanFeedback("Check carefully."),
            ),
        ],
        run_readiness=RunReadiness(run_ready=True, readiness_note="All good."),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _save_wb(workbook: Workbook, path: str) -> None:
    text = render(workbook)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_run_artifact_json(workbook_path: str) -> dict:
    """Return a minimal valid run artifact dict for testing."""
    return {
        "run_id": "test-run-001",
        "workflow_id": "test-wf",
        "workbook_path": workbook_path,
        "workbook_sha256": "abc123",
        "workflow_config_sha256": "def456",
        "provider": "test-provider",
        "model": "test-model",
        "target_kind": "workflow",
        "target_name": "text_echo",
        "target_locator": "tests.fixtures.adapter_echo",
        "target_boundary": "text echo workflow boundary",
        "target_sources": ["tests/fixtures/adapter_echo.py"],
        "started_at": "2025-01-01T00:00:00Z",
        "finished_at": "2025-01-01T00:01:00Z",
        "status": "failed",
        "cases": [
            {
                "case_id": "case-1",
                "input": "Test input",
                "context": None,
                "expected_behavior": "Should echo.",
                "raw_response": "Test input echoed.",
                "parsed_response": "Test input echoed.",
                "duration_ms": 150,
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "status": "completed",
                "execution_error": None,
            },
            {
                "case_id": "case-2",
                "input": "Fail input",
                "context": "ctx",
                "expected_behavior": "Should fail.",
                "raw_response": None,
                "parsed_response": None,
                "duration_ms": 50,
                "usage": None,
                "status": "failed_execution",
                "execution_error": "adapter crashed",
            },
        ],
        "aggregate": {
            "total_cases": 2,
            "completed_cases": 1,
            "failed_cases": 1,
            "total_duration_ms": 200,
            "total_input_tokens": 10,
            "total_output_tokens": 20,
        },
    }


# ── L2→L3 escalation tests ──────────────────────────────────────────────────


class TestL2ToL3Escalation(unittest.TestCase):
    """Test that DiagnosticsHandle.open_expert() is the deliberate L2→L3 entry."""

    def test_open_expert_from_diagnostics(self):
        """open_diagnostics() -> open_expert() yields an ExpertHandle."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            diag = session.open_diagnostics()
            expert = diag.open_expert()

            self.assertIsInstance(expert, ExpertHandle)
            session.release()

    def test_open_expert_multiple_times(self):
        """open_expert() can be called multiple times on the same handle."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            diag = session.open_diagnostics()
            expert1 = diag.open_expert()
            expert2 = diag.open_expert()

            self.assertIsInstance(expert1, ExpertHandle)
            self.assertIsInstance(expert2, ExpertHandle)
            session.release()

    def test_open_expert_on_released_session_raises(self):
        """open_expert() raises EvalError when the session is released."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            diag = session.open_diagnostics()
            session.release()

            with self.assertRaises(EvalError) as ctx:
                diag.open_expert()
            self.assertIn("released", str(ctx.exception).lower())

    def test_reactive_diagnostics_handle_has_no_expert_opener(self):
        """DiagnosticsHandle from EvalError.diagnostics cannot open_expert()."""
        handle = DiagnosticsHandle(
            state=EvalState(
                workbook_path="/tmp/fake.workbook.md",
                preparation_stage=PreparationStage.NEEDS_DIRECTIONS,
                has_target_content=False,
                source_reference_count=0,
                has_brief_content=False,
                planning_ready=False,
                execution_binding_ready=False,
                direction_count=0,
                case_count=0,
                workbook_run_ready=False,
                run_ready=False,
                run_artifact=None,
                analysis_artifact=None,
                compare_artifact=None,
            ),
            issues=["some issue"],
            reports=[],
            recovery_executor=None,
            expert_opener=None,
        )

        with self.assertRaises(EvalError) as ctx:
            handle.open_expert()
        self.assertIn("no expert opener", str(ctx.exception).lower())


# ── Visibility discipline tests ──────────────────────────────────────────────


class TestExpertVisibilityDiscipline(unittest.TestCase):
    """Expert types must NOT be in the ordinary top-level package exports."""

    def test_expert_handle_not_in_top_level(self):
        import lightassay

        self.assertFalse(hasattr(lightassay, "ExpertHandle"))

    def test_expert_types_not_in_all(self):
        import lightassay

        all_exports = lightassay.__all__
        expert_names = [
            "ExpertHandle",
            "WorkbookSourceView",
            "ConfigBindingsView",
            "ConfigBindingEntry",
            "RunArtifactView",
            "CaseRecordView",
            "DirectionView",
            "CaseView",
            "RunReadinessView",
            "ArtifactReferencesView",
        ]
        for name in expert_names:
            self.assertNotIn(name, all_exports, f"{name} should not be in top-level __all__")

    def test_expert_types_importable_from_expert_module(self):
        """All expert types are importable from lightassay.expert."""
        from lightassay.expert import (
            ExpertHandle,
            WorkbookSourceView,
        )

        # Just verify they are real types.
        self.assertTrue(callable(ExpertHandle))
        self.assertTrue(callable(WorkbookSourceView))


# ── Deep inspection: workbook source ─────────────────────────────────────────


class TestInspectWorkbookSource(unittest.TestCase):
    """Test ExpertHandle.inspect_workbook_source()."""

    def test_inspect_brief_only_workbook(self):
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()
            view = expert.inspect_workbook_source()

            self.assertIsInstance(view, WorkbookSourceView)
            self.assertEqual(view.workbook_path, os.path.abspath(wb_path))
            self.assertIn("Test the text echo workflow", view.brief)
            self.assertIsInstance(view.raw_text, str)
            self.assertGreater(len(view.raw_text), 0)
            self.assertEqual(view.directions, [])
            self.assertEqual(view.cases, [])
            self.assertFalse(view.run_readiness.run_ready)
            self.assertIsNone(view.artifact_references.run)
            session.release()

    def test_inspect_run_ready_workbook_with_details(self):
        """Deep view exposes direction/case content that L1/L2 hide."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_run_ready_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()
            view = expert.inspect_workbook_source()

            # Directions
            self.assertEqual(len(view.directions), 1)
            dir0 = view.directions[0]
            self.assertIsInstance(dir0, DirectionView)
            self.assertEqual(dir0.direction_id, "correctness")
            self.assertEqual(dir0.body, "Verify output correctness.")
            self.assertEqual(dir0.human_instruction, "Focus on edge cases.")

            # Cases
            self.assertEqual(len(view.cases), 1)
            case0 = view.cases[0]
            self.assertIsInstance(case0, CaseView)
            self.assertEqual(case0.case_id, "case-1")
            self.assertEqual(case0.input, "Test input")
            self.assertEqual(case0.target_directions, ["correctness"])
            self.assertEqual(case0.expected_behavior, "Should echo.")
            self.assertEqual(case0.context, "Some context.")
            self.assertEqual(case0.notes, "Important note.")
            self.assertEqual(case0.human_instruction, "Check carefully.")

            # Readiness
            self.assertTrue(view.run_readiness.run_ready)
            self.assertEqual(view.run_readiness.readiness_note, "All good.")

            session.release()

    def test_inspect_missing_workbook_raises(self):
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()

            # Delete the workbook file.
            os.unlink(wb_path)

            with self.assertRaises(EvalError):
                expert.inspect_workbook_source()

            # Cannot release since workbook gone, but session is still alive.


# ── Deep inspection: config bindings ─────────────────────────────────────────


class TestInspectConfigBindings(unittest.TestCase):
    """Test ExpertHandle.inspect_config_bindings()."""

    def test_all_unbound(self):
        """Session with no configs bound."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()
            view = expert.inspect_config_bindings()

            self.assertIsInstance(view, ConfigBindingsView)
            self.assertEqual(len(view.bindings), 3)

            for entry in view.bindings:
                self.assertIsInstance(entry, ConfigBindingEntry)
                self.assertFalse(entry.bound)
                self.assertIsNone(entry.path)
                self.assertIsNone(entry.file_exists)
                self.assertIsNone(entry.valid)
                self.assertIsNone(entry.validation_error)

            session.release()

    def test_all_bound_and_valid(self):
        """Session with all three valid configs."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(
                wb_path,
                preparation_config=_fixture("preparation_ok.json"),
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )
            expert = session.open_diagnostics().open_expert()
            view = expert.inspect_config_bindings()

            for entry in view.bindings:
                self.assertTrue(entry.bound, f"{entry.config_type} should be bound")
                self.assertTrue(entry.file_exists, f"{entry.config_type} should exist")
                self.assertTrue(entry.valid, f"{entry.config_type} should be valid")
                self.assertIsNone(entry.validation_error)

            session.release()

    def test_bound_but_missing_file(self):
        """Session with a config path that points to a non-existent file."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(
                wb_path,
                workflow_config="/tmp/nonexistent_config_12345.json",
            )
            expert = session.open_diagnostics().open_expert()
            view = expert.inspect_config_bindings()

            wf = [e for e in view.bindings if e.config_type == "workflow"][0]
            self.assertTrue(wf.bound)
            self.assertFalse(wf.file_exists)
            self.assertFalse(wf.valid)
            self.assertIn("not found", wf.validation_error.lower())

            session.release()

    def test_bound_but_invalid_config(self):
        """Session with a config that exists but fails validation."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_bad_missing_field.json"),
            )
            expert = session.open_diagnostics().open_expert()
            view = expert.inspect_config_bindings()

            wf = [e for e in view.bindings if e.config_type == "workflow"][0]
            self.assertTrue(wf.bound)
            self.assertTrue(wf.file_exists)
            self.assertFalse(wf.valid)
            self.assertIsNotNone(wf.validation_error)

            session.release()


# ── Deep inspection: run artifact ────────────────────────────────────────────


class TestInspectRunArtifact(unittest.TestCase):
    """Test ExpertHandle.inspect_run_artifact()."""

    def test_inspect_valid_run_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            # Write a run artifact.
            artifact_data = _make_run_artifact_json(os.path.abspath(wb_path))
            artifact_path = os.path.join(d, "run_test.json")
            with open(artifact_path, "w") as f:
                json.dump(artifact_data, f)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()
            view = expert.inspect_run_artifact(artifact_path)

            self.assertIsInstance(view, RunArtifactView)
            self.assertEqual(view.run_id, "test-run-001")
            self.assertEqual(view.workflow_id, "test-wf")
            self.assertEqual(view.provider, "test-provider")
            self.assertEqual(view.model, "test-model")
            self.assertEqual(view.status, "failed")
            self.assertEqual(view.total_cases, 2)
            self.assertEqual(view.completed_cases, 1)
            self.assertEqual(view.failed_cases, 1)
            self.assertEqual(view.total_duration_ms, 200)

            # Case-level detail.
            self.assertEqual(len(view.cases), 2)

            c0 = view.cases[0]
            self.assertIsInstance(c0, CaseRecordView)
            self.assertEqual(c0.case_id, "case-1")
            self.assertEqual(c0.status, "completed")
            self.assertEqual(c0.duration_ms, 150)
            self.assertEqual(c0.raw_response, "Test input echoed.")
            self.assertIsNone(c0.execution_error)

            c1 = view.cases[1]
            self.assertEqual(c1.case_id, "case-2")
            self.assertEqual(c1.status, "failed_execution")
            self.assertEqual(c1.execution_error, "adapter crashed")
            self.assertIsNone(c1.raw_response)

            session.release()

    def test_inspect_missing_artifact_raises(self):
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()

            with self.assertRaises(EvalError):
                expert.inspect_run_artifact("/tmp/nonexistent_run_artifact.json")

            session.release()

    def test_inspect_invalid_artifact_raises(self):
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            # Write invalid JSON as artifact.
            artifact_path = os.path.join(d, "bad_run.json")
            with open(artifact_path, "w") as f:
                f.write("not valid json")

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()

            with self.assertRaises(EvalError):
                expert.inspect_run_artifact(artifact_path)

            session.release()


# ── Bounded low-level control: rebind_config ─────────────────────────────────


class TestRebindConfig(unittest.TestCase):
    """Test ExpertHandle.rebind_config()."""

    def test_rebind_workflow_config(self):
        """Rebind workflow_config and verify the session uses the new path."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()

            # Initially unbound.
            view = expert.inspect_config_bindings()
            wf = [e for e in view.bindings if e.config_type == "workflow"][0]
            self.assertFalse(wf.bound)

            # Rebind to a valid config.
            new_view = expert.rebind_config(
                workflow_config=_fixture("workflow_text_ok.json"),
            )

            wf = [e for e in new_view.bindings if e.config_type == "workflow"][0]
            self.assertTrue(wf.bound)
            self.assertTrue(wf.file_exists)
            self.assertTrue(wf.valid)

            # Verify the session itself reflects the rebinding.
            # can_run now has a workflow config (though workbook may not be ready).
            reasons = session.why_not()
            # Should no longer contain "No workflow_config provided".
            for reason in reasons:
                self.assertNotIn("No workflow_config provided", reason)

            session.release()

    def test_rebind_does_not_mutate_files(self):
        """rebind_config changes in-memory state only."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            # Record file modification times.
            wb_mtime_before = os.path.getmtime(wb_path)
            dir_contents_before = set(os.listdir(d))

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()
            expert.rebind_config(
                workflow_config=_fixture("workflow_text_ok.json"),
                preparation_config=_fixture("preparation_ok.json"),
            )

            wb_mtime_after = os.path.getmtime(wb_path)
            dir_contents_after = set(os.listdir(d))

            self.assertEqual(wb_mtime_before, wb_mtime_after)
            self.assertEqual(dir_contents_before, dir_contents_after)

            session.release()

    def test_unbind_config_with_empty_string(self):
        """Passing empty string unbinds the config."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
            )
            expert = session.open_diagnostics().open_expert()

            # Initially bound.
            view = expert.inspect_config_bindings()
            wf = [e for e in view.bindings if e.config_type == "workflow"][0]
            self.assertTrue(wf.bound)

            # Unbind with empty string.
            new_view = expert.rebind_config(workflow_config="")
            wf = [e for e in new_view.bindings if e.config_type == "workflow"][0]
            self.assertFalse(wf.bound)

            session.release()

    def test_rebind_preserves_other_configs(self):
        """Rebinding one config does not affect others."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(
                wb_path,
                preparation_config=_fixture("preparation_ok.json"),
            )
            expert = session.open_diagnostics().open_expert()

            # Rebind only workflow.
            new_view = expert.rebind_config(
                workflow_config=_fixture("workflow_text_ok.json"),
            )

            # Preparation should still be bound.
            prep = [e for e in new_view.bindings if e.config_type == "preparation"][0]
            self.assertTrue(prep.bound)
            self.assertTrue(prep.valid)

            # Workflow should now be bound.
            wf = [e for e in new_view.bindings if e.config_type == "workflow"][0]
            self.assertTrue(wf.bound)

            session.release()

    def test_rebind_on_released_session_raises(self):
        """rebind_config raises EvalError after session release."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_brief_only_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            session = open_session(wb_path)
            expert = session.open_diagnostics().open_expert()
            session.release()

            with self.assertRaises(EvalError):
                expert.rebind_config(
                    workflow_config=_fixture("workflow_text_ok.json"),
                )


# ── Full escalation path test ────────────────────────────────────────────────


class TestFullEscalationPath(unittest.TestCase):
    """Test the complete L1→L2→L3 escalation path."""

    def test_l1_to_l2_to_l3_round_trip(self):
        """L1 session → L2 diagnostics → L3 expert → deep inspection."""
        with tempfile.TemporaryDirectory() as d:
            wb = _make_run_ready_workbook()
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(wb, wb_path)

            # L1: open session and inspect state.
            session = open_session(
                wb_path,
                preparation_config=_fixture("preparation_ok.json"),
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )
            state = session.state()
            self.assertEqual(state.preparation_stage, PreparationStage.PREPARED)

            # L2: open diagnostics.
            diag = session.open_diagnostics()
            self.assertIsInstance(diag, DiagnosticsHandle)

            # L3: escalate to expert.
            expert = diag.open_expert()
            self.assertIsInstance(expert, ExpertHandle)

            # Deep inspection: workbook.
            wb_view = expert.inspect_workbook_source()
            self.assertEqual(len(wb_view.directions), 1)
            self.assertEqual(len(wb_view.cases), 1)
            self.assertTrue(wb_view.run_readiness.run_ready)

            # Deep inspection: config bindings.
            cfg_view = expert.inspect_config_bindings()
            self.assertEqual(len(cfg_view.bindings), 3)
            for entry in cfg_view.bindings:
                self.assertTrue(entry.bound)
                self.assertTrue(entry.valid)

            session.release()


if __name__ == "__main__":
    unittest.main()
