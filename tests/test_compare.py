"""Tests for the compare command: comparer, CLI compare, error paths.

Covers:
- Comparer with subprocess semantic adapter protocol (happy + failure paths)
- Completed-only restriction for compare (failed run rejected)
- Too few run artifacts rejected
- CLI compare command (happy path + error paths)
- End-to-end run -> compare pipeline

Run with:
    PYTHONPATH=src python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from lightassay.comparer import execute_compare
from lightassay.errors import CompareError
from lightassay.run_artifact_io import save_run_artifact
from lightassay.run_models import (
    Aggregate,
    CaseRecord,
    CaseUsage,
    RunArtifact,
)
from lightassay.semantic_config import SemanticConfig

# -- Helpers -----------------------------------------------------------------

_PYTHON = sys.executable
_REPO = os.path.join(os.path.dirname(__file__), "..")
_SRC_PATH = os.path.join(_REPO, "src")
_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


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


def _make_completed_artifact(
    run_id="abc123",
    workbook_path="/tmp/test.workbook.md",
    provider="test-provider",
    model="test-model",
    workflow_id="test-wf",
):
    """Build a minimal valid completed RunArtifact for testing."""
    return RunArtifact(
        run_id=run_id,
        workflow_id=workflow_id,
        workbook_path=workbook_path,
        workbook_sha256="a" * 64,
        workflow_config_sha256="b" * 64,
        provider=provider,
        model=model,
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
                expected_behavior="Echo it",
                raw_response="Echo: Hello",
                parsed_response={"echoed": "Hello"},
                duration_ms=100,
                usage=CaseUsage(input_tokens=1, output_tokens=2),
                status="completed",
                execution_error=None,
            ),
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


def _make_failed_artifact(
    run_id="fail123",
    workbook_path="/tmp/test.workbook.md",
):
    """Build a minimal valid failed RunArtifact for testing."""
    return RunArtifact(
        run_id=run_id,
        workflow_id="test-wf",
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
                case_id="c1",
                input="Hello",
                context=None,
                expected_behavior="Echo it",
                raw_response=None,
                parsed_response=None,
                duration_ms=100,
                usage=None,
                status="failed_execution",
                execution_error="Adapter exited with code 1",
            ),
        ],
        aggregate=Aggregate(
            total_cases=1,
            completed_cases=0,
            failed_cases=1,
            total_duration_ms=100,
            total_input_tokens=0,
            total_output_tokens=0,
        ),
    )


def _save_artifact(artifact, tmpdir):
    """Save a run artifact to tmpdir and return the path."""
    path = os.path.join(tmpdir, f"run_{artifact.run_id}.json")
    save_run_artifact(artifact, path)
    return path


def _ensure_workbook(tmpdir, name="test.workbook.md"):
    """Copy ready_demo fixture to tmpdir, return path.

    CLI now routes through open_session which validates the workbook exists.
    Tests that create run artifacts must ensure the workbook_path is real.
    """
    dst = os.path.join(tmpdir, name)
    shutil.copy2(_fixture("ready_demo.workbook.md"), dst)
    return dst


# -- Comparer Unit Tests -----------------------------------------------------


class TestComparerHappyPath(unittest.TestCase):
    def test_compare_two_completed_runs(self):
        a1 = _make_completed_artifact(run_id="run_aaa")
        a2 = _make_completed_artifact(run_id="run_bbb", provider="other-provider")
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)
            result_text, compare_id = execute_compare([a1, a2], [p1, p2], config)

        # Verify compare_id format.
        self.assertEqual(len(compare_id), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in compare_id))

        # Verify metadata header.
        self.assertIn(f"# Compare: {compare_id}", result_text)
        self.assertIn("**run_ids:** run_aaa, run_bbb", result_text)
        self.assertIn("**comparer_provider:** test", result_text)
        self.assertIn("**comparer_model:** echo-v1", result_text)
        self.assertIn("**compared_at:**", result_text)
        self.assertIn("---", result_text)

        # Verify compare body from adapter.
        self.assertIn("## Comparison Summary", result_text)
        self.assertIn("Compared 2 runs", result_text)
        self.assertIn("run_aaa", result_text)
        self.assertIn("run_bbb", result_text)

    def test_compare_goal_propagates_to_artifact_and_adapter_output(self):
        a1 = _make_completed_artifact(run_id="goal_a")
        a2 = _make_completed_artifact(run_id="goal_b")
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)
            result_text, _compare_id = execute_compare(
                [a1, a2],
                [p1, p2],
                config,
                compare_goal="Decide whether the new prompt is better on clarity.",
            )

        self.assertIn(
            "**compare_goal:** Decide whether the new prompt is better on clarity.",
            result_text,
        )
        self.assertIn(
            "- Goal: Decide whether the new prompt is better on clarity.",
            result_text,
        )
        self.assertIn(
            "- **Alignment summary:** Compared runs against goal: Decide whether the new prompt is better on clarity.",
            result_text,
        )

    def test_compare_three_runs(self):
        a1 = _make_completed_artifact(run_id="run_x")
        a2 = _make_completed_artifact(run_id="run_y")
        a3 = _make_completed_artifact(run_id="run_z")
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)
            p3 = _save_artifact(a3, tmpdir)
            result_text, compare_id = execute_compare([a1, a2, a3], [p1, p2, p3], config)

        self.assertIn("**run_ids:** run_x, run_y, run_z", result_text)
        self.assertIn("Compared 3 runs", result_text)


class TestComparerCompletedOnlyRestriction(unittest.TestCase):
    def test_failed_run_rejected(self):
        a1 = _make_completed_artifact(run_id="good_run")
        a2 = _make_failed_artifact(run_id="bad_run")
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1, a2], ["/tmp/a.json", "/tmp/b.json"], config)
        self.assertIn("bad_run", str(ctx.exception))
        self.assertIn("failed", str(ctx.exception))
        self.assertIn("only accepts completed", str(ctx.exception))

    def test_all_failed_rejected(self):
        a1 = _make_failed_artifact(run_id="fail_1")
        a2 = _make_failed_artifact(run_id="fail_2")
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1, a2], ["/tmp/a.json", "/tmp/b.json"], config)
        self.assertIn("only accepts completed", str(ctx.exception))


class TestComparerTooFewRuns(unittest.TestCase):
    def test_single_run_rejected(self):
        a1 = _make_completed_artifact()
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1], ["/tmp/a.json"], config)
        self.assertIn("at least 2", str(ctx.exception))

    def test_zero_runs_rejected(self):
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with self.assertRaises(CompareError) as ctx:
            execute_compare([], [], config)
        self.assertIn("at least 2", str(ctx.exception))


class TestComparerAdapterFailures(unittest.TestCase):
    def _two_completed(self):
        return (
            _make_completed_artifact(run_id="r1"),
            _make_completed_artifact(run_id="r2"),
        )

    def _config_with_adapter(self, adapter_fixture):
        return SemanticConfig(
            adapter=_fixture(adapter_fixture),
            provider="test",
            model="test",
        )

    def test_adapter_non_zero_exit(self):
        a1, a2 = self._two_completed()
        config = self._config_with_adapter("semantic_adapter_fail.py")
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1, a2], ["/tmp/a.json", "/tmp/b.json"], config)
        self.assertIn("exited with code", str(ctx.exception))

    def test_adapter_bad_json(self):
        a1, a2 = self._two_completed()
        config = self._config_with_adapter("semantic_adapter_bad_json.py")
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1, a2], ["/tmp/a.json", "/tmp/b.json"], config)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_adapter_missing_field(self):
        a1, a2 = self._two_completed()
        config = self._config_with_adapter("compare_adapter_missing_field.py")
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1, a2], ["/tmp/a.json", "/tmp/b.json"], config)
        self.assertIn("missing required field", str(ctx.exception))

    def test_adapter_empty_markdown(self):
        a1, a2 = self._two_completed()
        config = self._config_with_adapter("compare_adapter_empty_markdown.py")
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1, a2], ["/tmp/a.json", "/tmp/b.json"], config)
        self.assertIn("non-empty", str(ctx.exception))

    def test_adapter_not_found(self):
        a1, a2 = self._two_completed()
        config = SemanticConfig(
            adapter="/nonexistent/adapter.py",
            provider="test",
            model="test",
        )
        with self.assertRaises(CompareError) as ctx:
            execute_compare([a1, a2], ["/tmp/a.json", "/tmp/b.json"], config)
        self.assertIn("not found", str(ctx.exception))


# -- CLI Compare Tests -------------------------------------------------------


class TestCLICompare(unittest.TestCase):
    def test_compare_happy_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="cli_r1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="cli_r2", provider="alt", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("complete", result.stdout.lower())
            self.assertIn("cli_r1", result.stdout)
            self.assertIn("cli_r2", result.stdout)

            # Verify compare artifact was created.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(
                len(md_files),
                1,
                msg=f"Expected 1 compare artifact, found: {md_files}",
            )

            # Verify compare artifact content.
            compare_path = os.path.join(tmpdir, md_files[0])
            with open(compare_path) as f:
                content = f.read()
            self.assertIn("# Compare:", content)
            self.assertIn("## Comparison Summary", content)
            self.assertIn("cli_r1", content)
            self.assertIn("cli_r2", content)

    def test_compare_happy_path_with_goal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="cli_goal_1", workbook_path=wb_path)
            a2 = _make_completed_artifact(
                run_id="cli_goal_2", provider="alt", workbook_path=wb_path
            )
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--goal",
                "Compare whether the new prompt improves quality.",
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)

            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

            compare_path = os.path.join(tmpdir, md_files[0])
            with open(compare_path) as f:
                content = f.read()
            self.assertIn(
                "**compare_goal:** Compare whether the new prompt improves quality.",
                content,
            )
            self.assertIn(
                "- **Alignment summary:** Compared runs against goal: Compare whether the new prompt improves quality.",
                content,
            )

    def test_compare_artifact_filename_uses_compare_id_not_run_ids(self):
        """Filename must be compare_{compare_id}.md, NOT compare_{run_id_a}_vs_{run_id_b}.md.

        This pins the accepted naming convention: compare_id is an independent
        12-char hex UUID4 prefix, not derived from run_ids.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="cli_r1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="cli_r2", provider="alt", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

            filename = md_files[0]
            # Filename must NOT contain run_ids.
            self.assertNotIn(
                "cli_r1",
                filename,
                "Filename must use compare_id, not run_ids",
            )
            self.assertNotIn(
                "cli_r2",
                filename,
                "Filename must use compare_id, not run_ids",
            )
            # Filename must match compare_{12-char-hex}.md.
            self.assertRegex(
                filename,
                r"^compare_[0-9a-f]{12}\.md$",
                "Filename must be compare_{12-char-hex}.md",
            )

    def test_compare_three_runs_cli(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="tri_1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="tri_2", workbook_path=wb_path)
            a3 = _make_completed_artifact(run_id="tri_3", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)
            p3 = _save_artifact(a3, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                p3,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

    def test_compare_failed_run_rejected(self):
        """CLI compare rejects failed runs with explicit error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="ok_run", workbook_path=wb_path)
            a2 = _make_failed_artifact(run_id="bad_run", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stderr.lower())
            self.assertIn("only accepts completed", result.stderr.lower())

            # No compare artifact should be created.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 0)

    def test_compare_single_run_rejected(self):
        """CLI compare rejects fewer than 2 run artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="solo", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("at least 2", result.stderr.lower())

    def test_compare_missing_run_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="exists", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                "/nonexistent/run.json",
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not found", result.stderr.lower())

    def test_compare_missing_semantic_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="r1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="r2", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                "/nonexistent/config.json",
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)

    def test_compare_invalid_semantic_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="r1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="r2", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_bad_missing_field.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())

    def test_compare_requires_semantic_config_flag(self):
        """compare subcommand requires --semantic-config."""
        result = _run_cli(
            "compare",
            "/tmp/r1.json",
            "/tmp/r2.json",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_compare_missing_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="r1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="r2", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                "/nonexistent/dir",
            )

            self.assertNotEqual(result.returncode, 0)

    def test_compare_with_failing_adapter(self):
        """Compare fails when semantic adapter exits non-zero."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="r1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="r2", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            fail_config = {
                "adapter": _fixture("semantic_adapter_fail.py"),
                "provider": "test",
                "model": "fail",
            }
            config_path = os.path.join(tmpdir, "fail_semantic.json")
            with open(config_path, "w") as f:
                json.dump(fail_config, f)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                config_path,
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stderr.lower())

    def test_compare_does_not_update_workbook(self):
        """v1 conscious decision: compare does not update workbook."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy workbook.
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            # Read original workbook content.
            with open(wb_dst) as f:
                original_content = f.read()

            a1 = _make_completed_artifact(run_id="wr1", workbook_path=wb_dst)
            a2 = _make_completed_artifact(run_id="wr2", workbook_path=wb_dst)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)

            # Workbook must be unchanged — no automatic update in v1.
            with open(wb_dst) as f:
                after_content = f.read()
            self.assertEqual(original_content, after_content)


class TestCLICompareNoWorkbookRequired(unittest.TestCase):
    """Prove CLI compare no longer requires a workbook at the artifact's workbook_path.

    This is the "no worse than direct path" proof: the old direct engine path
    (execute_compare) never needed a workbook.  The managed CLI path must not
    introduce an irrelevant workbook dependency.
    """

    def test_compare_succeeds_with_nonexistent_workbook_path(self):
        """CLI compare must work even when the artifact's workbook_path points nowhere."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Artifacts reference a workbook path that does NOT exist.
            ghost_wb = "/nonexistent/path/to/ghost.workbook.md"
            a1 = _make_completed_artifact(run_id="no_wb_1", workbook_path=ghost_wb)
            a2 = _make_completed_artifact(run_id="no_wb_2", workbook_path=ghost_wb)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("complete", result.stdout.lower())

            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

    def test_compare_with_different_workbook_paths(self):
        """CLI compare must work when artifacts reference different (missing) workbooks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(
                run_id="cross_1",
                workbook_path="/tmp/workbook_alpha.workbook.md",
            )
            a2 = _make_completed_artifact(
                run_id="cross_2",
                workbook_path="/tmp/workbook_beta.workbook.md",
            )
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = _run_cli(
                "compare",
                p1,
                p2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("cross_1", result.stdout)
            self.assertIn("cross_2", result.stdout)


# -- compare_runs() Library Primitive Tests ----------------------------------


class TestCompareRunsPrimitive(unittest.TestCase):
    """Tests for the shared compare_runs() library primitive.

    compare_runs() is the honest shared compare path that both CLI and
    session.compare() delegate to.  It requires no session and no workbook.
    """

    def test_compare_runs_happy_path(self):
        from lightassay import compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="lib_r1")
            a2 = _make_completed_artifact(run_id="lib_r2", provider="alt")
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = compare_runs(
                [p1, p2],
                semantic_config=_fixture("semantic_ok.json"),
                output_dir=tmpdir,
            )

            self.assertTrue(os.path.isfile(result.artifact_path))
            self.assertEqual(len(result.compare_id), 12)
            self.assertIsNone(result.goal)

            with open(result.artifact_path) as f:
                content = f.read()
            self.assertIn("# Compare:", content)
            self.assertIn("lib_r1", content)
            self.assertIn("lib_r2", content)

    def test_compare_runs_with_goal(self):
        from lightassay import compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="goal_r1")
            a2 = _make_completed_artifact(run_id="goal_r2")
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = compare_runs(
                [p1, p2],
                semantic_config=_fixture("semantic_ok.json"),
                goal="Compare quality against the current baseline.",
                output_dir=tmpdir,
            )

            self.assertEqual(result.goal, "Compare quality against the current baseline.")
            with open(result.artifact_path) as f:
                content = f.read()
            self.assertIn(
                "**compare_goal:** Compare quality against the current baseline.", content
            )
            self.assertIn("- Goal: Compare quality against the current baseline.", content)
            self.assertIn(
                "- **Alignment summary:** Compared runs against goal: Compare quality against the current baseline.",
                content,
            )

    def test_compare_runs_no_workbook_needed(self):
        """compare_runs() must succeed even with nonexistent workbook_path in artifacts."""
        from lightassay import compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="nw_1", workbook_path="/nonexistent/wb.md")
            a2 = _make_completed_artifact(run_id="nw_2", workbook_path="/nonexistent/other.md")
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            result = compare_runs(
                [p1, p2],
                semantic_config=_fixture("semantic_ok.json"),
                output_dir=tmpdir,
            )

            self.assertTrue(os.path.isfile(result.artifact_path))

    def test_compare_runs_too_few_artifacts(self):
        from lightassay import EvalError, compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="solo")
            p1 = _save_artifact(a1, tmpdir)

            with self.assertRaises(EvalError) as ctx:
                compare_runs(
                    [p1],
                    semantic_config=_fixture("semantic_ok.json"),
                    output_dir=tmpdir,
                )
            self.assertIn("at least 2", str(ctx.exception))

    def test_compare_runs_failed_artifact_rejected(self):
        from lightassay import EvalError, compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="ok")
            a2 = _make_failed_artifact(run_id="bad")
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            with self.assertRaises(EvalError) as ctx:
                compare_runs(
                    [p1, p2],
                    semantic_config=_fixture("semantic_ok.json"),
                    output_dir=tmpdir,
                )
            self.assertIn("only accepts completed", str(ctx.exception).lower())

    def test_compare_runs_missing_artifact_file(self):
        from lightassay import EvalError, compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="exists")
            p1 = _save_artifact(a1, tmpdir)

            with self.assertRaises(EvalError) as ctx:
                compare_runs(
                    [p1, "/nonexistent/run.json"],
                    semantic_config=_fixture("semantic_ok.json"),
                    output_dir=tmpdir,
                )
            self.assertIn("not found", str(ctx.exception))

    def test_compare_runs_invalid_semantic_config(self):
        from lightassay import EvalError, compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="r1")
            a2 = _make_completed_artifact(run_id="r2")
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            with self.assertRaises(EvalError) as ctx:
                compare_runs(
                    [p1, p2],
                    semantic_config=_fixture("semantic_bad_missing_field.json"),
                    output_dir=tmpdir,
                )
            self.assertIn("invalid", str(ctx.exception).lower())

    def test_compare_runs_missing_output_dir(self):
        from lightassay import EvalError, compare_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = _make_completed_artifact(run_id="r1")
            a2 = _make_completed_artifact(run_id="r2")
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            with self.assertRaises(EvalError):
                compare_runs(
                    [p1, p2],
                    semantic_config=_fixture("semantic_ok.json"),
                    output_dir="/nonexistent/dir",
                )

    def test_session_compare_delegates_to_compare_runs(self):
        """EvalSession.compare() must produce the same result as compare_runs().

        This proves there is no split-brain: session.compare() delegates
        to the shared primitive.
        """
        from lightassay import compare_runs, open_session

        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="del_1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="del_2", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            # Library primitive path.
            out_lib = os.path.join(tmpdir, "lib_out")
            os.makedirs(out_lib)
            lib_result = compare_runs(
                [p1, p2],
                semantic_config=_fixture("semantic_ok.json"),
                output_dir=out_lib,
            )

            # Session path.
            out_sess = os.path.join(tmpdir, "sess_out")
            os.makedirs(out_sess)
            session = open_session(wb_path, semantic_config=_fixture("semantic_ok.json"))
            sess_result = session.compare([p1, p2], output_dir=out_sess)

            # Both must produce compare artifacts.
            self.assertTrue(os.path.isfile(lib_result.artifact_path))
            self.assertTrue(os.path.isfile(sess_result.artifact_path))

            # Both artifacts must have the same structure.
            with open(lib_result.artifact_path) as f:
                lib_content = f.read()
            with open(sess_result.artifact_path) as f:
                sess_content = f.read()

            self.assertIn("# Compare:", lib_content)
            self.assertIn("# Compare:", sess_content)
            self.assertIn("del_1", lib_content)
            self.assertIn("del_1", sess_content)

    def test_session_compare_passes_goal(self):
        from lightassay import open_session

        with tempfile.TemporaryDirectory() as tmpdir:
            wb_path = _ensure_workbook(tmpdir)
            a1 = _make_completed_artifact(run_id="goal_del_1", workbook_path=wb_path)
            a2 = _make_completed_artifact(run_id="goal_del_2", workbook_path=wb_path)
            p1 = _save_artifact(a1, tmpdir)
            p2 = _save_artifact(a2, tmpdir)

            session = open_session(wb_path, semantic_config=_fixture("semantic_ok.json"))
            result = session.compare(
                [p1, p2],
                goal="Compare cost and quality tradeoffs.",
                output_dir=tmpdir,
            )

            self.assertEqual(result.goal, "Compare cost and quality tradeoffs.")
            with open(result.artifact_path) as f:
                content = f.read()
            self.assertIn("**compare_goal:** Compare cost and quality tradeoffs.", content)
            self.assertIn(
                "- **Alignment summary:** Compared runs against goal: Compare cost and quality tradeoffs.",
                content,
            )


class TestCLICompareEndToEnd(unittest.TestCase):
    """End-to-end test: run x2 -> compare pipeline."""

    def test_run_twice_then_compare(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy workbook twice for two independent runs.
            wb_src = _fixture("ready_demo.workbook.md")
            wb_a = os.path.join(tmpdir, "a.workbook.md")
            wb_b = os.path.join(tmpdir, "b.workbook.md")
            shutil.copy2(wb_src, wb_a)
            shutil.copy2(wb_src, wb_b)

            # Run 1.
            r1 = _run_cli(
                "run",
                wb_a,
                "--workflow-config",
                _fixture("workflow_text_ok.json"),
                "--output-dir",
                tmpdir,
            )
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)

            # Run 2.
            r2 = _run_cli(
                "run",
                wb_b,
                "--workflow-config",
                _fixture("workflow_text_ok.json"),
                "--output-dir",
                tmpdir,
            )
            self.assertEqual(r2.returncode, 0, msg=r2.stderr)

            # Find run artifacts.
            run_files = sorted(
                [f for f in os.listdir(tmpdir) if f.startswith("run_") and f.endswith(".json")]
            )
            self.assertEqual(len(run_files), 2, msg=f"Expected 2 run artifacts: {run_files}")

            run_path_1 = os.path.join(tmpdir, run_files[0])
            run_path_2 = os.path.join(tmpdir, run_files[1])

            # Compare.
            cmp = _run_cli(
                "compare",
                run_path_1,
                run_path_2,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )
            self.assertEqual(cmp.returncode, 0, msg=cmp.stderr)

            # Verify compare artifact exists.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("compare_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

            # Verify compare artifact has content.
            with open(os.path.join(tmpdir, md_files[0])) as f:
                content = f.read()
            self.assertIn("# Compare:", content)
            self.assertIn("## Comparison Summary", content)


if __name__ == "__main__":
    unittest.main()
