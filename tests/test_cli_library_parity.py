"""Tests proving CLI routes through the shared library surface.

These tests verify that CLI command handlers delegate to
``init_workbook``, ``open_session``, ``compare_runs``, and
``EvalSession`` methods rather than retaining independent
orchestration logic.

The proof strategy:
- CLI imports ONLY the L1 public boundary (errors, surface, types).
- CLI command outcomes are semantically identical to direct library calls.
- Stage intent validation in prepare commands uses ``session.state()``
  + ``session.prepare()`` rather than calling preparer directly.
- CLI compare routes through ``compare_runs()`` without manufacturing
  a session or workbook context.

Run with:
    cd lightassay && python3 -m unittest discover -s tests
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from lightassay import (
    PreparationStage,
    init_workbook,
    open_session,
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
from lightassay.workbook_renderer import render

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    return os.path.join(_FIXTURES, name)


def _make_brief_only_workbook():
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


def _make_run_ready_workbook():
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
                expected_behavior="Should echo correctly.",
                behavior_facet="core_output_behavior",
                testing_lens="positive_and_regression",
                covered_user_priority_sections=["freeform_brief"],
                source_rationale="Grounded in adapter source behavior.",
                context=None,
                notes=None,
                human_instruction=HumanFeedback(""),
            ),
        ],
        run_readiness=RunReadiness(run_ready=True, readiness_note="Ready."),
        artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
    )


def _save_wb(workbook, path):
    text = render(workbook)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ── Import boundary proof ───────────────────────────────────────────────────


class TestCLIImportBoundary(unittest.TestCase):
    """Prove that cli.py only imports L1 public surface modules.

    This is the structural proof that CLI cannot have split-brain
    orchestration through lower-level engine modules.
    """

    def test_cli_has_no_engine_imports(self):
        """cli.py must not import runner, analyzer, comparer, preparer,
        workbook_parser, workbook_renderer, workflow_config,
        semantic_config, preparation_config, or run_artifact_io.
        """
        import lightassay.cli as cli_mod

        source = inspect.getsource(cli_mod)

        # These engine modules must NOT appear as imports in cli.py.
        forbidden_modules = [
            "runner",
            "analyzer",
            "comparer",
            "preparer",
            "workbook_parser",
            "workbook_renderer",
            "workflow_config",
            "semantic_config",
            "preparation_config",
            "run_artifact_io",
        ]

        for mod in forbidden_modules:
            # Check for "from .{mod}" or "import .{mod}" patterns.
            self.assertNotIn(
                f"from .{mod}",
                source,
                f"cli.py must not import engine module {mod!r}. "
                f"CLI should route through the L1 surface.",
            )

    def test_cli_imports_only_surface_errors_types(self):
        """cli.py must only import from .errors, .surface, .types."""
        import lightassay.cli as cli_mod

        source = inspect.getsource(cli_mod)

        # Find all "from .xxx import" patterns.
        import re

        imports = re.findall(r"from \.([\w]+) import", source)

        allowed = {"errors", "surface", "types"}
        for mod in imports:
            self.assertIn(
                mod,
                allowed,
                f"cli.py imports from .{mod}, which is outside the allowed L1 boundary {allowed}.",
            )


# ── Semantic parity: workbook ────────────────────────────────────────────────


class TestWorkbookParity(unittest.TestCase):
    """CLI workbook and library init_workbook produce identical results."""

    def test_workbook_parity(self):
        """CLI workbook and library init_workbook create identical workbooks."""
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as d:
            # CLI path.
            cli_rc = main(["workbook", "--output-dir", d])
            self.assertEqual(cli_rc, 0)
            cli_path = os.path.join(d, "workbook1.workbook.md")
            with open(cli_path) as fh:
                cli_content = fh.read()

            # Library path.
            lib_path = init_workbook("parity-lib", output_dir=d)
            with open(lib_path) as fh:
                lib_content = fh.read()

            # Content structure must be identical (only name differs).
            cli_lines = cli_content.replace("workbook1", "NORMALIZED")
            lib_lines = lib_content.replace("parity-lib", "NORMALIZED")
            self.assertEqual(cli_lines, lib_lines)


# ── Semantic parity: run ─────────────────────────────────────────────────────


class TestRunParity(unittest.TestCase):
    """CLI run and library session.run() produce semantically equivalent results."""

    def test_run_parity(self):
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as d:
            # Create two copies of the same workbook.
            wb_cli = os.path.join(d, "cli.workbook.md")
            wb_lib = os.path.join(d, "lib.workbook.md")
            _save_wb(_make_run_ready_workbook(), wb_cli)
            _save_wb(_make_run_ready_workbook(), wb_lib)

            wf_config = _fixture("workflow_text_ok.json")

            # CLI path.
            cli_rc = main(
                [
                    "run",
                    wb_cli,
                    "--workflow-config",
                    wf_config,
                    "--output-dir",
                    d,
                ]
            )
            self.assertEqual(cli_rc, 0)

            # Library path.
            session = open_session(wb_lib, workflow_config=wf_config)
            lib_result = session.run(output_dir=d)
            self.assertEqual(lib_result.status, "completed")

            # Both must produce run artifacts.
            run_files = sorted(
                f for f in os.listdir(d) if f.startswith("run_") and f.endswith(".json")
            )
            self.assertEqual(len(run_files), 2)

            # Both must update workbook artifact references.
            s_cli = open_session(wb_cli)
            s_lib = open_session(wb_lib)
            self.assertIsNotNone(s_cli.state().run_artifact)
            self.assertIsNotNone(s_lib.state().run_artifact)


# ── Semantic parity: prepare ─────────────────────────────────────────────────


class TestPrepareParity(unittest.TestCase):
    """CLI prepare commands and library session.prepare() produce
    equivalent state transitions."""

    def test_prepare_directions_parity(self):
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as d:
            wb_cli = os.path.join(d, "cli.workbook.md")
            wb_lib = os.path.join(d, "lib.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_cli)
            _save_wb(_make_brief_only_workbook(), wb_lib)

            prep_config = _fixture("preparation_ok.json")

            # CLI path.
            cli_rc = main(
                [
                    "prepare-directions",
                    wb_cli,
                    "--preparation-config",
                    prep_config,
                ]
            )
            self.assertEqual(cli_rc, 0)

            # Library path.
            session = open_session(wb_lib, preparation_config=prep_config)
            lib_result = session.prepare()
            self.assertEqual(
                lib_result.stage_completed,
                PreparationStage.NEEDS_DIRECTIONS,
            )

            # Both must advance to NEEDS_CASES.
            s_cli = open_session(wb_cli)
            s_lib = open_session(wb_lib)
            self.assertEqual(
                s_cli.state().preparation_stage,
                PreparationStage.NEEDS_CASES,
            )
            self.assertEqual(
                s_lib.state().preparation_stage,
                PreparationStage.NEEDS_CASES,
            )

            # Both must have directions.
            self.assertGreater(s_cli.state().direction_count, 0)
            self.assertGreater(s_lib.state().direction_count, 0)


# ── Stage intent validation ──────────────────────────────────────────────────


class TestStageIntentValidation(unittest.TestCase):
    """CLI prepare commands validate stage intent through state(),
    not through direct engine calls."""

    def test_prepare_directions_rejects_wrong_stage(self):
        """prepare-directions must reject when stage != NEEDS_DIRECTIONS."""
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            wb = _make_run_ready_workbook()
            _save_wb(wb, wb_path)

            rc = main(
                [
                    "prepare-directions",
                    wb_path,
                    "--preparation-config",
                    _fixture("preparation_ok.json"),
                ]
            )
            self.assertEqual(rc, 1)

    def test_prepare_cases_rejects_wrong_stage(self):
        """prepare-cases must reject when stage != NEEDS_CASES."""
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)

            rc = main(
                [
                    "prepare-cases",
                    wb_path,
                    "--preparation-config",
                    _fixture("preparation_ok.json"),
                ]
            )
            self.assertEqual(rc, 1)

    def test_prepare_readiness_rejects_wrong_stage(self):
        """prepare-readiness must reject when stage != NEEDS_READINESS."""
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as d:
            wb_path = os.path.join(d, "test.workbook.md")
            _save_wb(_make_brief_only_workbook(), wb_path)

            rc = main(
                [
                    "prepare-readiness",
                    wb_path,
                    "--preparation-config",
                    _fixture("preparation_ok.json"),
                ]
            )
            self.assertEqual(rc, 1)


# ── Semantic parity: compare ────────────────────────────────────────────────


class TestCompareParity(unittest.TestCase):
    """CLI compare and library compare_runs() produce semantically equivalent results.

    This proves that CLI compare routes through the shared compare_runs()
    primitive rather than a CLI-only workbook derivation workaround.
    """

    def test_compare_parity(self):
        from lightassay import compare_runs
        from lightassay.cli import main
        from lightassay.run_artifact_io import save_run_artifact
        from lightassay.run_models import (
            Aggregate,
            CaseRecord,
            CaseUsage,
            RunArtifact,
        )

        def _make_artifact(run_id, provider="test"):
            return RunArtifact(
                run_id=run_id,
                workflow_id="test-wf",
                workbook_path="/nonexistent/parity.workbook.md",
                workbook_sha256="a" * 64,
                workflow_config_sha256="b" * 64,
                provider=provider,
                model="test-model",
                target_kind="workflow",
                target_name="text_echo",
                target_locator="tests.fixtures.adapter_echo",
                target_boundary="text echo workflow boundary",
                target_sources=["tests/fixtures/adapter_echo.py"],
                started_at="2025-01-01T00:00:00+00:00",
                finished_at="2025-01-01T00:01:00+00:00",
                status="completed",
                cases=[
                    CaseRecord(
                        case_id="c1",
                        input="Hello",
                        context=None,
                        expected_behavior="Echo",
                        raw_response="Echo: Hello",
                        parsed_response={"echoed": "Hello"},
                        duration_ms=100,
                        usage=CaseUsage(input_tokens=1, output_tokens=2),
                        status="completed",
                        execution_error=None,
                    )
                ],
                aggregate=Aggregate(
                    total_cases=1,
                    completed_cases=1,
                    failed_cases=0,
                    total_duration_ms=100,
                    total_input_tokens=1,
                    total_output_tokens=2,
                ),
            )

        with tempfile.TemporaryDirectory() as d:
            a1 = _make_artifact("par_cli")
            a2 = _make_artifact("par_lib", provider="alt")

            p1 = os.path.join(d, "run_par_cli.json")
            p2 = os.path.join(d, "run_par_lib.json")
            save_run_artifact(a1, p1)
            save_run_artifact(a2, p2)

            sem_cfg = _fixture("semantic_ok.json")

            # CLI path.
            cli_out = os.path.join(d, "cli_out")
            os.makedirs(cli_out)
            cli_rc = main(
                [
                    "compare",
                    p1,
                    p2,
                    "--semantic-config",
                    sem_cfg,
                    "--output-dir",
                    cli_out,
                ]
            )
            self.assertEqual(cli_rc, 0)

            # Library path.
            lib_out = os.path.join(d, "lib_out")
            os.makedirs(lib_out)
            lib_result = compare_runs(
                [p1, p2],
                semantic_config=sem_cfg,
                output_dir=lib_out,
            )

            # Both must produce compare artifacts.
            cli_mds = [
                f for f in os.listdir(cli_out) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(len(cli_mds), 1)
            self.assertTrue(os.path.isfile(lib_result.artifact_path))

            # Both must have the same content structure.
            with open(os.path.join(cli_out, cli_mds[0])) as f:
                cli_content = f.read()
            with open(lib_result.artifact_path) as f:
                lib_content = f.read()

            self.assertIn("# Compare:", cli_content)
            self.assertIn("# Compare:", lib_content)
            self.assertIn("par_cli", cli_content)
            self.assertIn("par_cli", lib_content)

    def test_compare_needs_no_workbook(self):
        """CLI compare must not require a workbook — proving the workaround is gone."""
        from lightassay.cli import main
        from lightassay.run_artifact_io import save_run_artifact
        from lightassay.run_models import (
            Aggregate,
            CaseRecord,
            CaseUsage,
            RunArtifact,
        )

        artifact = RunArtifact(
            run_id="no_wb",
            workflow_id="test-wf",
            workbook_path="/absolutely/does/not/exist.workbook.md",
            workbook_sha256="a" * 64,
            workflow_config_sha256="b" * 64,
            provider="test",
            model="test-model",
            target_kind="workflow",
            target_name="text_echo",
            target_locator="tests.fixtures.adapter_echo",
            target_boundary="text echo workflow boundary",
            target_sources=["tests/fixtures/adapter_echo.py"],
            started_at="2025-01-01T00:00:00+00:00",
            finished_at="2025-01-01T00:01:00+00:00",
            status="completed",
            cases=[
                CaseRecord(
                    case_id="c1",
                    input="Hello",
                    context=None,
                    expected_behavior="Echo",
                    raw_response="Echo: Hello",
                    parsed_response={"echoed": "Hello"},
                    duration_ms=100,
                    usage=CaseUsage(input_tokens=1, output_tokens=2),
                    status="completed",
                    execution_error=None,
                )
            ],
            aggregate=Aggregate(
                total_cases=1,
                completed_cases=1,
                failed_cases=0,
                total_duration_ms=100,
                total_input_tokens=1,
                total_output_tokens=2,
            ),
        )

        with tempfile.TemporaryDirectory() as d:
            p1 = os.path.join(d, "run_a.json")
            p2 = os.path.join(d, "run_b.json")
            save_run_artifact(artifact, p1)
            save_run_artifact(artifact, p2)

            rc = main(
                [
                    "compare",
                    p1,
                    p2,
                    "--semantic-config",
                    _fixture("semantic_ok.json"),
                    "--output-dir",
                    d,
                ]
            )
            self.assertEqual(rc, 0)


# ── Error boundary parity ────────────────────────────────────────────────────


class TestErrorBoundaryParity(unittest.TestCase):
    """CLI error exits are produced by catching EvalError from the library,
    not by catching engine-internal exceptions directly."""

    def test_workbook_error_from_library(self):
        """CLI workbook error for a missing output dir comes from library EvalError."""
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(["workbook", "--output-dir", "/nonexistent/dir/xyz"])
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", buf.getvalue().lower())

    def test_run_error_from_library(self):
        """CLI run error for non-existent workbook comes from library EvalError."""
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(
                [
                    "run",
                    "/nonexistent/workbook.md",
                    "--workflow-config",
                    _fixture("workflow_text_ok.json"),
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("not found", buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
