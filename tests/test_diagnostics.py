"""Tests for the L2 diagnostics/recovery layer.

Covers:
- Proactive diagnostics: open_diagnostics() returns structured reports
- Reactive diagnostics: L1 failures carry structured diagnostics on EvalError
- L1->L2 wiring: diagnostics opened from session match actual state
- Recovery actions: bounded deterministic advance_preparation
- DiagnosticsHandle API: reports, apply_recovery_action
- L2 types not in top-level exports (visibility discipline)

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
    init_workbook,
    open_session,
)

# L2 types — imported from diagnostics module, NOT from top-level.
from lightassay.diagnostics import (
    RECOVERY_ADVANCE_PREPARATION,
    DiagnosticConfidence,
    DiagnosticEvidence,
    DiagnosticReport,
    RecoveryOption,
    RecoveryResult,
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
        ],
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
                human_instruction=HumanFeedback(""),
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
                context=None,
                notes=None,
                human_instruction=HumanFeedback(""),
            ),
        ],
        run_readiness=RunReadiness(run_ready=True, readiness_note="All good."),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _make_inconsistent_workbook() -> Workbook:
    """Run-ready but no cases — inconsistent state."""
    return Workbook(
        target=Target(
            kind="workflow",
            name="inconsistent_target",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief="Test.",
        directions_global_instruction=HumanFeedback(""),
        directions=[],
        cases_global_instruction=HumanFeedback(""),
        cases=[],
        run_readiness=RunReadiness(run_ready=True, readiness_note=""),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _make_not_ready_with_note() -> Workbook:
    return Workbook(
        target=Target(
            kind="workflow",
            name="needs_review_target",
            locator="tests.fixtures.adapter_echo",
            boundary="text echo workflow boundary",
            sources=["tests/fixtures/adapter_echo.py"],
            notes="",
        ),
        brief="Test.",
        directions_global_instruction=HumanFeedback(""),
        directions=[
            Direction(
                direction_id="d1",
                body="dir.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                human_instruction=HumanFeedback(""),
            ),
        ],
        cases_global_instruction=HumanFeedback(""),
        cases=[
            Case(
                case_id="c1",
                input="in",
                target_directions=["d1"],
                expected_behavior="out",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                context=None,
                notes=None,
                human_instruction=HumanFeedback(""),
            ),
        ],
        run_readiness=RunReadiness(run_ready=False, readiness_note="Needs review."),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _save_wb(workbook: Workbook, path: str) -> None:
    text = render(workbook)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _prep_config_path() -> str:
    return _fixture("preparation_ok.json")


# ── Proactive diagnostics ────────────────────────────────────────────────────


class TestProactiveDiagnostics(unittest.TestCase):
    """open_diagnostics() returns structured L2 reports."""

    def test_returns_diagnostics_handle_with_reports(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            self.assertIsInstance(handle, DiagnosticsHandle)
            self.assertIsInstance(handle.state, EvalState)
            self.assertIsInstance(handle.issues, list)
            self.assertIsInstance(handle.reports, list)
            self.assertGreater(len(handle.reports), 0)

    def test_reports_are_diagnostic_report_instances(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            for report in handle.reports:
                self.assertIsInstance(report, DiagnosticReport)
                self.assertIsInstance(report.diagnosis, str)
                self.assertIsInstance(report.confidence, DiagnosticConfidence)
                self.assertIsInstance(report.evidence, list)
                self.assertIsInstance(report.suggested_actions, list)
                self.assertIsInstance(report.recovery_options, list)

    def test_evidence_is_structured(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            # At least one report should have evidence.
            all_evidence = [e for r in handle.reports for e in r.evidence]
            self.assertGreater(len(all_evidence), 0)
            for ev in all_evidence:
                self.assertIsInstance(ev, DiagnosticEvidence)
                self.assertIsInstance(ev.field, str)
                self.assertIsInstance(ev.observed, str)

    def test_preparation_incomplete_report(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            prep_reports = [r for r in handle.reports if "preparation" in r.diagnosis.lower()]
            self.assertGreater(len(prep_reports), 0)
            report = prep_reports[0]
            self.assertEqual(report.confidence, DiagnosticConfidence.HIGH)
            self.assertIn("needs_directions", report.diagnosis.lower())

    def test_workflow_config_issue_report(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            # No workflow_config provided.
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            config_reports = [r for r in handle.reports if "workflow config" in r.diagnosis.lower()]
            self.assertGreater(len(config_reports), 0)

    def test_clean_workbook_minimal_reports(self):
        """A prepared workbook with valid config should have few/no reports."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_config_echo.json"),
            )
            handle = session.open_diagnostics()
            # Should have no preparation incomplete, no config issue.
            self.assertEqual(len(handle.reports), 0)

    def test_inconsistent_workbook_report(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_inconsistent_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            inconsistent = [r for r in handle.reports if "no cases" in r.diagnosis.lower()]
            self.assertGreater(len(inconsistent), 0)

    def test_readiness_note_report(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_not_ready_with_note(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            note_reports = [r for r in handle.reports if "readiness note" in r.diagnosis.lower()]
            self.assertGreater(len(note_reports), 0)
            # Evidence should include the actual note.
            ev_text = " ".join(e.observed for r in note_reports for e in r.evidence)
            self.assertIn("Needs review", ev_text)


# ── Reactive diagnostics (L1 failure → structured diagnostics) ───────────────


class TestReactiveDiagnostics(unittest.TestCase):
    """L1 failures carry structured diagnostics on EvalError."""

    def test_prepare_no_config_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)  # no preparation_config

            with self.assertRaises(EvalError) as ctx:
                session.prepare()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIsInstance(exc.diagnostics, DiagnosticReport)
            self.assertIn("config", exc.diagnostics.diagnosis.lower())
            self.assertGreater(len(exc.diagnostics.evidence), 0)

    def test_prepare_already_prepared_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            with self.assertRaises(EvalError) as ctx:
                session.prepare()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("already complete", exc.diagnostics.diagnosis.lower())

    def test_prepare_empty_brief_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            from lightassay import init_workbook

            wb_path = init_workbook("test-diag", output_dir=d)
            session = open_session(wb_path, preparation_config=_prep_config_path())

            with self.assertRaises(EvalError) as ctx:
                session.prepare()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("brief", exc.diagnostics.diagnosis.lower())

    def test_run_no_config_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)  # no workflow_config

            with self.assertRaises(EvalError) as ctx:
                session.run()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("config", exc.diagnostics.diagnosis.lower())

    def test_run_not_ready_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_config_echo.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session.run()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("not run-ready", exc.diagnostics.diagnosis.lower())

    def test_prepare_adapter_failure_has_diagnostics(self):
        """Engine-level preparation failure carries reactive diagnostics."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
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

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("failed", exc.diagnostics.diagnosis.lower())

    def test_run_output_dir_missing_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session.run(output_dir="/nonexistent/dir")

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("output directory", exc.diagnostics.diagnosis.lower())

    def test_run_execution_failure_has_diagnostics(self):
        """RunError from adapter execution carries reactive diagnostics."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            # Config pointing to a non-existent adapter.
            bad_config = os.path.join(d, "bad_wf.json")
            with open(bad_config, "w") as fh:
                json.dump(
                    {
                        "workflow_id": "bad",
                        "provider": "test",
                        "model": "test",
                        "adapter": "/nonexistent/adapter.py",
                    },
                    fh,
                )

            session = open_session(wb_path, workflow_config=bad_config)

            with self.assertRaises(EvalError) as ctx:
                session.run(output_dir=d)

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("failed", exc.diagnostics.diagnosis.lower())

    def test_run_invalid_config_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            bad_config = os.path.join(d, "bad.json")
            with open(bad_config, "w") as fh:
                fh.write("not json{{{")

            session = open_session(wb_path, workflow_config=bad_config)

            with self.assertRaises(EvalError) as ctx:
                session.run(output_dir=d)

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("invalid", exc.diagnostics.diagnosis.lower())

    def test_run_no_cases_has_diagnostics(self):
        """Run-ready but no cases: edge case carries diagnostics."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_inconsistent_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session.run(output_dir=d)

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("no cases", exc.diagnostics.diagnosis.lower())

    def test_analyze_no_semantic_config_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)  # no semantic_config

            with self.assertRaises(EvalError) as ctx:
                session.analyze("/some/run.json")

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("semantic config", exc.diagnostics.diagnosis.lower())

    def test_analyze_artifact_not_found_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                semantic_config=_fixture("semantic_ok.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session.analyze("/nonexistent/run.json")

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("not found", exc.diagnostics.diagnosis.lower())

    def test_analyze_output_dir_missing_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            # Create a dummy run artifact file.
            dummy_artifact = os.path.join(d, "run_dummy.json")
            with open(dummy_artifact, "w") as fh:
                fh.write("{}")

            session = open_session(
                wb_path,
                semantic_config=_fixture("semantic_ok.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session.analyze(dummy_artifact, output_dir="/nonexistent/dir")

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("output directory", exc.diagnostics.diagnosis.lower())

    def test_analyze_workbook_mismatch_has_diagnostics(self):
        """Analyze rejects run artifact from a different workbook with diagnostics."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            # Run to produce a real artifact.
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )
            run_result = session.run(output_dir=d)

            # Open a NEW session with a DIFFERENT workbook.
            wb_path2 = os.path.join(d, "other.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path2)
            session2 = open_session(
                wb_path2,
                semantic_config=_fixture("semantic_ok.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session2.analyze(run_result.artifact_path)

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("different workbook", exc.diagnostics.diagnosis.lower())

    def test_analyze_invalid_semantic_config_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            # Run first to get a valid artifact.
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )
            run_result = session.run(output_dir=d)

            # Now re-open with a bad semantic config.
            bad_sem = os.path.join(d, "bad_sem.json")
            with open(bad_sem, "w") as fh:
                fh.write("not json{{{")
            session2 = open_session(wb_path, semantic_config=bad_sem)

            with self.assertRaises(EvalError) as ctx:
                session2.analyze(run_result.artifact_path, output_dir=d)

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("invalid", exc.diagnostics.diagnosis.lower())

    def test_analyze_execution_failure_has_diagnostics(self):
        """Analysis adapter failure carries reactive diagnostics."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            # Run first.
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )
            run_result = session.run(output_dir=d)

            # Re-open with failing semantic adapter.
            fail_sem = os.path.join(d, "fail_sem.json")
            with open(fail_sem, "w") as fh:
                json.dump(
                    {
                        "adapter": _fixture("semantic_adapter_fail.py"),
                        "provider": "test",
                        "model": "fail",
                    },
                    fh,
                )
            session2 = open_session(wb_path, semantic_config=fail_sem)

            with self.assertRaises(EvalError) as ctx:
                session2.analyze(run_result.artifact_path, output_dir=d)

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("failed", exc.diagnostics.diagnosis.lower())

    def test_compare_no_semantic_config_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)  # no semantic_config

            with self.assertRaises(EvalError) as ctx:
                session.compare(["/a.json", "/b.json"])

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("semantic config", exc.diagnostics.diagnosis.lower())

    def test_compare_too_few_artifacts_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                semantic_config=_fixture("semantic_ok.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session.compare(["/a.json"])

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("too few", exc.diagnostics.diagnosis.lower())

    def test_compare_artifact_not_found_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(
                wb_path,
                semantic_config=_fixture("semantic_ok.json"),
            )

            with self.assertRaises(EvalError) as ctx:
                session.compare(["/nonexistent/a.json", "/nonexistent/b.json"])

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("not found", exc.diagnostics.diagnosis.lower())

    def test_compare_output_dir_missing_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            # Produce two run artifacts.
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )
            r1 = session.run(output_dir=d)

            from lightassay.workbook_parser import parse as wb_parse

            with open(wb_path) as fh:
                wb = wb_parse(fh.read())
            wb.artifact_references.run = None
            _save_wb(wb, wb_path)

            r2 = session.run(output_dir=d)

            with self.assertRaises(EvalError) as ctx:
                session.compare(
                    [r1.artifact_path, r2.artifact_path],
                    output_dir="/nonexistent/dir",
                )

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("output directory", exc.diagnostics.diagnosis.lower())

    def test_compare_not_completed_has_diagnostics(self):
        """Compare rejects non-completed run artifact with diagnostics."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)

            # Produce a real completed artifact.
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_text_ok.json"),
                semantic_config=_fixture("semantic_ok.json"),
            )
            r1 = session.run(output_dir=d)

            # Build a fake failed artifact.
            from lightassay.run_artifact_io import save_run_artifact
            from lightassay.run_models import (
                Aggregate,
                CaseRecord,
                RunArtifact,
            )

            failed_artifact = RunArtifact(
                run_id="fail123",
                workflow_id="test",
                workbook_path=wb_path,
                workbook_sha256="a" * 64,
                workflow_config_sha256="b" * 64,
                provider="test",
                model="test",
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
                        case_id="c1",
                        input="x",
                        context=None,
                        expected_behavior="y",
                        raw_response=None,
                        parsed_response=None,
                        duration_ms=10,
                        usage=None,
                        status="failed_execution",
                        execution_error="adapter failed",
                    ),
                ],
                aggregate=Aggregate(
                    total_cases=1,
                    completed_cases=0,
                    failed_cases=1,
                    total_duration_ms=10,
                    total_input_tokens=0,
                    total_output_tokens=0,
                ),
            )
            failed_path = os.path.join(d, "run_fail123.json")
            save_run_artifact(failed_artifact, failed_path)

            with self.assertRaises(EvalError) as ctx:
                session.compare(
                    [r1.artifact_path, failed_path],
                    output_dir=d,
                )

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIn("not completed", exc.diagnostics.diagnosis.lower())

    def test_diagnostics_evidence_fields_populated(self):
        """Reactive diagnostic evidence has field/observed populated."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)

            with self.assertRaises(EvalError) as ctx:
                session.run()

            report = ctx.exception.diagnostics
            self.assertIsNotNone(report)
            for ev in report.evidence:
                self.assertTrue(ev.field, "evidence.field must not be empty")
                self.assertTrue(ev.observed, "evidence.observed must not be empty")


# ── L1→L2 wiring ─────────────────────────────────────────────────────────────


class TestL1ToL2Wiring(unittest.TestCase):
    """Verify that L1 operations wire correctly into L2 diagnostics."""

    def test_open_diagnostics_state_matches_session_state(self):
        """Proactive diagnostics state must match session.state()."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)

            state = session.state()
            handle = session.open_diagnostics()

            self.assertEqual(handle.state.workbook_path, state.workbook_path)
            self.assertEqual(handle.state.preparation_stage, state.preparation_stage)
            self.assertEqual(handle.state.workbook_run_ready, state.workbook_run_ready)
            self.assertEqual(handle.state.run_ready, state.run_ready)

    def test_open_diagnostics_reports_align_with_issues(self):
        """Each issue string should correspond to at least one report."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            # Issues exist => reports should exist.
            if handle.issues:
                self.assertGreater(len(handle.reports), 0)

    def test_l1_failure_then_open_diagnostics_consistent(self):
        """After an L1 failure, open_diagnostics reflects the same state."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(
                wb_path,
                workflow_config=_fixture("workflow_config_echo.json"),
            )

            # run() should fail (not ready).
            with self.assertRaises(EvalError):
                session.run()

            # Proactive diagnostics should still work and reflect the issue.
            handle = session.open_diagnostics()
            self.assertIsInstance(handle, DiagnosticsHandle)
            self.assertFalse(handle.state.workbook_run_ready)
            self.assertFalse(handle.state.run_ready)
            prep_reports = [r for r in handle.reports if "preparation" in r.diagnosis.lower()]
            self.assertGreater(len(prep_reports), 0)

    def test_reactive_diagnostic_has_suggested_actions(self):
        """Reactive diagnostics include suggested_actions."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path)

            with self.assertRaises(EvalError) as ctx:
                session.run()

            report = ctx.exception.diagnostics
            self.assertIsNotNone(report)
            self.assertGreater(len(report.suggested_actions), 0)

    def test_handle_has_apply_recovery_action_method(self):
        """DiagnosticsHandle exposes apply_recovery_action."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()
            self.assertTrue(callable(handle.apply_recovery_action))


# ── Recovery actions ──────────────────────────────────────────────────────────


class TestRecoveryActions(unittest.TestCase):
    """Bounded deterministic recovery actions via DiagnosticsHandle."""

    def test_advance_preparation_recovery_available(self):
        """When preparation is incomplete and config is valid, recovery is available."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())
            handle = session.open_diagnostics()

            # Find the advance_preparation recovery option.
            all_options = [
                opt
                for r in handle.reports
                for opt in r.recovery_options
                if opt.action_id == RECOVERY_ADVANCE_PREPARATION
            ]
            self.assertGreater(len(all_options), 0)
            opt = all_options[0]
            self.assertIsInstance(opt, RecoveryOption)
            self.assertTrue(opt.available)
            self.assertIsNone(opt.unavailable_reason)

    def test_advance_preparation_recovery_unavailable_no_config(self):
        """Without preparation_config, recovery is listed but unavailable."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)  # no preparation_config
            handle = session.open_diagnostics()

            all_options = [
                opt
                for r in handle.reports
                for opt in r.recovery_options
                if opt.action_id == RECOVERY_ADVANCE_PREPARATION
            ]
            self.assertGreater(len(all_options), 0)
            opt = all_options[0]
            self.assertFalse(opt.available)
            self.assertIsNotNone(opt.unavailable_reason)
            self.assertIn("preparation_config", opt.unavailable_reason)

    def test_advance_preparation_recovery_executes(self):
        """apply_recovery_action advances preparation one step."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())
            handle = session.open_diagnostics()

            result = handle.apply_recovery_action(RECOVERY_ADVANCE_PREPARATION)

            self.assertIsInstance(result, RecoveryResult)
            self.assertTrue(result.success)
            self.assertEqual(result.action_id, RECOVERY_ADVANCE_PREPARATION)
            self.assertIsNotNone(result.post_state)
            self.assertEqual(
                result.post_state.preparation_stage,
                PreparationStage.NEEDS_CASES,
            )

    def test_advance_preparation_recovery_persists(self):
        """Recovery action persists changes to the workbook file."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())
            handle = session.open_diagnostics()
            handle.apply_recovery_action(RECOVERY_ADVANCE_PREPARATION)

            # Verify via fresh session.
            session2 = open_session(wb_path)
            state = session2.state()
            self.assertGreater(state.direction_count, 0)

    def test_no_recovery_on_prepared_workbook(self):
        """Prepared workbook should not offer advance_preparation recovery."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_path)
            session = open_session(wb_path, preparation_config=_prep_config_path())
            handle = session.open_diagnostics()

            all_options = [
                opt
                for r in handle.reports
                for opt in r.recovery_options
                if opt.action_id == RECOVERY_ADVANCE_PREPARATION
            ]
            # Should be empty because no preparation_incomplete report.
            self.assertEqual(len(all_options), 0)

    def test_unknown_recovery_action_raises(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            handle = session.open_diagnostics()

            with self.assertRaises(EvalError) as ctx:
                handle.apply_recovery_action("nonexistent_action")
            self.assertIn("Unknown", str(ctx.exception))

    def test_unavailable_recovery_action_raises(self):
        """Executing an unavailable recovery action raises EvalError."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)  # no preparation_config
            handle = session.open_diagnostics()

            with self.assertRaises(EvalError) as ctx:
                handle.apply_recovery_action(RECOVERY_ADVANCE_PREPARATION)
            self.assertIn("not available", str(ctx.exception))


# ── Visibility discipline ────────────────────────────────────────────────────


class TestVisibilityDiscipline(unittest.TestCase):
    """L2 types must NOT be in top-level exports."""

    def test_l2_types_not_in_top_level_all(self):
        import lightassay

        l2_names = [
            "DiagnosticReport",
            "DiagnosticEvidence",
            "DiagnosticConfidence",
            "RecoveryOption",
            "RecoveryResult",
            "RECOVERY_ADVANCE_PREPARATION",
        ]
        for name in l2_names:
            self.assertNotIn(
                name,
                lightassay.__all__,
                f"{name!r} must NOT be in top-level __all__",
            )

    def test_l2_types_accessible_from_diagnostics_module(self):
        """L2 types must be importable from the diagnostics module."""
        from lightassay.diagnostics import (  # noqa: F401
            RECOVERY_ADVANCE_PREPARATION,
            DiagnosticConfidence,
            DiagnosticEvidence,
            DiagnosticReport,
            RecoveryOption,
            RecoveryResult,
        )

    def test_diagnostics_handle_not_in_top_level(self):
        """DiagnosticsHandle must NOT be in the ordinary top-level exports.

        It remains accessible via lightassay.types and is returned
        by session.open_diagnostics() and EvalError.diagnostics, but the
        ordinary L1 surface does not export it directly.
        """
        import lightassay

        self.assertNotIn("DiagnosticsHandle", lightassay.__all__)

    def test_diagnostics_handle_accessible_via_types(self):
        """DiagnosticsHandle must be importable from lightassay.types."""
        from lightassay.types import DiagnosticsHandle as DH

        self.assertTrue(isinstance(DH, type))


# ── EvalError diagnostics attribute ──────────────────────────────────────────


class TestEvalErrorDiagnosticsAttribute(unittest.TestCase):
    """EvalError carries optional diagnostics attribute."""

    def test_eval_error_without_diagnostics(self):
        exc = EvalError("plain error")
        self.assertIsNone(exc.diagnostics)

    def test_eval_error_with_diagnostics(self):
        report = DiagnosticReport(
            diagnosis="test",
            confidence=DiagnosticConfidence.HIGH,
            evidence=[],
            suggested_actions=[],
            recovery_options=[],
        )
        exc = EvalError("error with diagnostics", diagnostics=report)
        self.assertIs(exc.diagnostics, report)
        self.assertEqual(str(exc), "error with diagnostics")


# ── Residual public entry/guard/file-truth diagnostics ─────────────────────


class TestResidualPublicEntryDiagnostics(unittest.TestCase):
    """Residual bare-EvalError paths on public L1 entry/guard/file-truth
    now carry structured diagnostics."""

    # ── init_workbook ───────────────────────────────────────────────────

    def test_init_workbook_invalid_name_has_diagnostics(self):
        with self.assertRaises(EvalError) as ctx:
            init_workbook("bad name!!")

        exc = ctx.exception
        self.assertIsNotNone(exc.diagnostics)
        self.assertIsInstance(exc.diagnostics, DiagnosticReport)
        self.assertIn("invalid", exc.diagnostics.diagnosis.lower())
        self.assertGreater(len(exc.diagnostics.evidence), 0)

    def test_init_workbook_missing_output_dir_has_diagnostics(self):
        with self.assertRaises(EvalError) as ctx:
            init_workbook("good-name", output_dir="/nonexistent/path/abc123")

        exc = ctx.exception
        self.assertIsNotNone(exc.diagnostics)
        self.assertIsInstance(exc.diagnostics, DiagnosticReport)
        self.assertIn("directory", exc.diagnostics.diagnosis.lower())

    def test_init_workbook_file_exists_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            # Create the workbook first.
            init_workbook("existing", output_dir=d)
            # Try to create again — should fail with diagnostics.
            with self.assertRaises(EvalError) as ctx:
                init_workbook("existing", output_dir=d)

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIsInstance(exc.diagnostics, DiagnosticReport)
            self.assertIn("already exists", exc.diagnostics.diagnosis.lower())

    # ── open_session ────────────────────────────────────────────────────

    def test_open_session_missing_workbook_has_diagnostics(self):
        with self.assertRaises(EvalError) as ctx:
            open_session("/nonexistent/path/test.workbook.md")

        exc = ctx.exception
        self.assertIsNotNone(exc.diagnostics)
        self.assertIsInstance(exc.diagnostics, DiagnosticReport)
        self.assertIn("not found", exc.diagnostics.diagnosis.lower())

    # ── released session guard ──────────────────────────────────────────

    def test_released_session_guard_has_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            session.release()

            with self.assertRaises(EvalError) as ctx:
                session.state()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIsInstance(exc.diagnostics, DiagnosticReport)
            self.assertIn("released", exc.diagnostics.diagnosis.lower())

    # ── _read_workbook file-truth failures ──────────────────────────────

    def test_workbook_file_not_found_has_diagnostics(self):
        """Workbook file-not-found surfaced through a public method."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            # Remove the workbook after opening the session.
            os.remove(wb_path)

            with self.assertRaises(EvalError) as ctx:
                session.state()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIsInstance(exc.diagnostics, DiagnosticReport)
            self.assertIn("not found", exc.diagnostics.diagnosis.lower())

    def test_workbook_parse_failed_has_diagnostics(self):
        """Workbook parse failure surfaced through a public method."""
        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)
            session = open_session(wb_path)
            # Corrupt the workbook after opening the session.
            with open(wb_path, "w", encoding="utf-8") as fh:
                fh.write("This is not a valid workbook at all.")

            with self.assertRaises(EvalError) as ctx:
                session.state()

            exc = ctx.exception
            self.assertIsNotNone(exc.diagnostics)
            self.assertIsInstance(exc.diagnostics, DiagnosticReport)
            self.assertIn("parse", exc.diagnostics.diagnosis.lower())


if __name__ == "__main__":
    unittest.main()
