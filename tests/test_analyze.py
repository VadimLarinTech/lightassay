"""Tests for the analyze command: semantic config, analyzer, CLI.

Covers:
- Semantic config loading (happy + error paths)
- Analyzer with subprocess semantic adapter protocol (happy + failure paths)
- CLI analyze command (happy path, failed run, invalid config, etc.)

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

from lightassay.analyzer import execute_analysis
from lightassay.errors import AnalysisError, SemanticConfigError
from lightassay.run_artifact_io import save_run_artifact
from lightassay.run_models import (
    Aggregate,
    CaseRecord,
    CaseUsage,
    RunArtifact,
)
from lightassay.semantic_config import SemanticConfig, load_semantic_config

# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _make_completed_artifact(workbook_path="/tmp/test.workbook.md"):
    """Build a minimal valid completed RunArtifact for testing."""
    return RunArtifact(
        run_id="abc123",
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


def _make_failed_artifact(workbook_path="/tmp/test.workbook.md"):
    """Build a minimal valid failed RunArtifact for testing."""
    return RunArtifact(
        run_id="fail123",
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


def _save_artifact_to_tmpdir(artifact, tmpdir):
    """Save a run artifact to tmpdir and return the path."""
    path = os.path.join(tmpdir, f"run_{artifact.run_id}.json")
    save_run_artifact(artifact, path)
    return path


# ── Semantic Config Tests ───────────────────────────────────────────────────


class TestSemanticConfigHappy(unittest.TestCase):
    def test_load_valid_config(self):
        config = load_semantic_config(_fixture("semantic_ok.json"))
        self.assertEqual(config.provider, "test")
        self.assertEqual(config.model, "echo-v1")
        self.assertTrue(os.path.isabs(config.adapter))
        self.assertTrue(config.adapter.endswith("semantic_adapter_echo.py"))


class TestSemanticConfigErrors(unittest.TestCase):
    def test_file_not_found(self):
        with self.assertRaises(SemanticConfigError) as ctx:
            load_semantic_config("/nonexistent/config.json")
        self.assertIn("not found", str(ctx.exception))

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json{")
            f.flush()
            try:
                with self.assertRaises(SemanticConfigError) as ctx:
                    load_semantic_config(f.name)
                self.assertIn("not valid JSON", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_missing_field(self):
        with self.assertRaises(SemanticConfigError) as ctx:
            load_semantic_config(_fixture("semantic_bad_missing_field.json"))
        self.assertIn("missing required field", str(ctx.exception))

    def test_unknown_field(self):
        with self.assertRaises(SemanticConfigError) as ctx:
            load_semantic_config(_fixture("semantic_bad_unknown_field.json"))
        self.assertIn("unknown fields", str(ctx.exception))

    def test_empty_field_value(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "adapter": "",
                    "provider": "test",
                    "model": "test",
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(SemanticConfigError) as ctx:
                    load_semantic_config(f.name)
                self.assertIn("non-empty", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_non_string_field(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "adapter": "./adapter.py",
                    "provider": 123,
                    "model": "test",
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(SemanticConfigError) as ctx:
                    load_semantic_config(f.name)
                self.assertIn("must be a string", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_not_object(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            f.flush()
            try:
                with self.assertRaises(SemanticConfigError) as ctx:
                    load_semantic_config(f.name)
                self.assertIn("must be a JSON object", str(ctx.exception))
            finally:
                os.unlink(f.name)


# ── Analyzer Tests ──────────────────────────────────────────────────────────


class TestAnalyzerHappyPath(unittest.TestCase):
    def test_execute_analysis_with_echo_adapter(self):
        artifact = _make_completed_artifact()
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)
            result_text, analysis_id = execute_analysis(artifact, artifact_path, config)

        # Verify analysis_id is a hex string.
        self.assertEqual(len(analysis_id), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in analysis_id))

        # Verify metadata header.
        self.assertIn(f"# Analysis: {analysis_id}", result_text)
        self.assertIn("**run_id:** abc123", result_text)
        self.assertIn("**workflow_id:** test-wf", result_text)
        self.assertIn("**analyzer_provider:** test", result_text)
        self.assertIn("**analyzer_model:** echo-v1", result_text)
        self.assertIn("**analyzed_at:**", result_text)
        self.assertIn("---", result_text)

        # Verify analysis body from adapter.
        self.assertIn("## Summary", result_text)
        self.assertIn("abc123", result_text)
        self.assertIn("Total cases: 1", result_text)
        self.assertIn("## Case Details", result_text)
        self.assertIn("## Conclusion", result_text)

    def test_execute_analysis_with_failed_artifact(self):
        """Analysis accepts failed run artifacts — completed-only is for compare."""
        artifact = _make_failed_artifact()
        config = SemanticConfig(
            adapter=_fixture("semantic_adapter_echo.py"),
            provider="test",
            model="echo-v1",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)
            result_text, analysis_id = execute_analysis(artifact, artifact_path, config)

        # Verify analysis_id is a hex string.
        self.assertEqual(len(analysis_id), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in analysis_id))

        # Verify metadata header references the failed run.
        self.assertIn(f"# Analysis: {analysis_id}", result_text)
        self.assertIn("**run_id:** fail123", result_text)

        # Verify analysis body from adapter includes case details.
        self.assertIn("## Summary", result_text)
        self.assertIn("Failed: 1", result_text)


class TestAnalyzerFailurePaths(unittest.TestCase):
    def _config_with_adapter(self, adapter_fixture):
        return SemanticConfig(
            adapter=_fixture(adapter_fixture),
            provider="test",
            model="test",
        )

    def test_adapter_non_zero_exit(self):
        artifact = _make_completed_artifact()
        config = self._config_with_adapter("semantic_adapter_fail.py")
        with self.assertRaises(AnalysisError) as ctx:
            execute_analysis(artifact, "/tmp/run.json", config)
        self.assertIn("exited with code", str(ctx.exception))

    def test_adapter_bad_json(self):
        artifact = _make_completed_artifact()
        config = self._config_with_adapter("semantic_adapter_bad_json.py")
        with self.assertRaises(AnalysisError) as ctx:
            execute_analysis(artifact, "/tmp/run.json", config)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_adapter_missing_field(self):
        artifact = _make_completed_artifact()
        config = self._config_with_adapter("semantic_adapter_missing_field.py")
        with self.assertRaises(AnalysisError) as ctx:
            execute_analysis(artifact, "/tmp/run.json", config)
        self.assertIn("missing required field", str(ctx.exception))

    def test_adapter_empty_markdown(self):
        artifact = _make_completed_artifact()
        config = self._config_with_adapter("semantic_adapter_empty_markdown.py")
        with self.assertRaises(AnalysisError) as ctx:
            execute_analysis(artifact, "/tmp/run.json", config)
        self.assertIn("non-empty", str(ctx.exception))

    def test_adapter_not_found(self):
        artifact = _make_completed_artifact()
        config = SemanticConfig(
            adapter="/nonexistent/adapter.py",
            provider="test",
            model="test",
        )
        with self.assertRaises(AnalysisError) as ctx:
            execute_analysis(artifact, "/tmp/run.json", config)
        self.assertIn("not found", str(ctx.exception))


# ── CLI Analyze Tests ───────────────────────────────────────────────────────


class TestCLIAnalyze(unittest.TestCase):
    def test_analyze_happy_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a completed run artifact.
            artifact = _make_completed_artifact(
                workbook_path=os.path.join(tmpdir, "demo.workbook.md")
            )
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            # Copy workbook so it can be updated.
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("complete", result.stdout.lower())

            # Verify analysis artifact was created.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("analysis_") and f.endswith(".md")
            ]
            self.assertEqual(
                len(md_files), 1, msg=f"Expected 1 analysis artifact, found: {md_files}"
            )

            # Verify analysis artifact content.
            analysis_path = os.path.join(tmpdir, md_files[0])
            with open(analysis_path) as f:
                content = f.read()
            self.assertIn("# Analysis:", content)
            self.assertIn("## Summary", content)

            # Verify workbook was updated with analysis reference.
            from lightassay.workbook_parser import parse

            with open(wb_dst) as f:
                updated_wb = parse(f.read())
            self.assertIsNotNone(updated_wb.artifact_references.analysis)
            self.assertIn("analysis_", updated_wb.artifact_references.analysis)

    def test_analyze_artifact_filename_uses_analysis_id_not_run_id(self):
        """Filename must be analysis_{analysis_id}.md, NOT analysis_{run_id}.md.

        This pins the accepted naming convention: analysis_id is an independent
        12-char hex UUID4 prefix, not derived from run_id.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = _make_completed_artifact(
                workbook_path=os.path.join(tmpdir, "demo.workbook.md")
            )
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("analysis_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

            filename = md_files[0]
            # Filename must NOT be analysis_{run_id}.md.
            self.assertNotEqual(
                filename,
                f"analysis_{artifact.run_id}.md",
                "Filename must use analysis_id, not run_id",
            )
            # Filename must match analysis_{12-char-hex}.md.
            self.assertRegex(
                filename,
                r"^analysis_[0-9a-f]{12}\.md$",
                "Filename must be analysis_{12-char-hex}.md",
            )

    def test_analyze_failed_run_accepted(self):
        """Failed runs can be analyzed — completed-only restriction is for compare."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a failed run artifact with a valid workbook.
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            artifact = _make_failed_artifact(workbook_path=wb_dst)
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("complete", result.stdout.lower())

            # Analysis artifact should be created.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("analysis_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

    def test_analyze_missing_run_artifact(self):
        result = _run_cli(
            "analyze",
            "/nonexistent/run.json",
            "--semantic-config",
            _fixture("semantic_ok.json"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr.lower())

    def test_analyze_invalid_semantic_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_dst = os.path.join(tmpdir, "test.workbook.md")
            shutil.copy2(_fixture("ready_demo.workbook.md"), wb_dst)
            artifact = _make_completed_artifact(workbook_path=wb_dst)
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                _fixture("semantic_bad_missing_field.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())

    def test_analyze_missing_semantic_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = _make_completed_artifact()
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                "/nonexistent/config.json",
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)

    def test_analyze_requires_semantic_config_flag(self):
        """analyze subcommand requires --semantic-config."""
        result = _run_cli(
            "analyze",
            "/tmp/some_run.json",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_analyze_missing_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = _make_completed_artifact()
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                "/nonexistent/dir",
            )

            self.assertNotEqual(result.returncode, 0)

    def test_analyze_with_failing_adapter(self):
        """Analyze fails when semantic adapter exits non-zero."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Provide a valid workbook so workbook validation passes.
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(_fixture("ready_demo.workbook.md"), wb_dst)

            artifact = _make_completed_artifact(workbook_path=wb_dst)
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            # Create a semantic config pointing to the failing adapter.
            fail_config = {
                "adapter": _fixture("semantic_adapter_fail.py"),
                "provider": "test",
                "model": "fail",
            }
            config_path = os.path.join(tmpdir, "fail_semantic.json")
            with open(config_path, "w") as f:
                json.dump(fail_config, f)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                config_path,
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stderr.lower())

    def test_analyze_workbook_not_found_errors(self):
        """When workbook from run artifact doesn't exist, analysis fails with error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # workbook_path points to a non-existent file.
            artifact = _make_completed_artifact(workbook_path="/nonexistent/workbook.md")
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("error", result.stderr.lower())
            self.assertIn("workbook", result.stderr.lower())

            # No analysis artifact should be created.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("analysis_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 0)

    def test_analyze_workbook_not_parseable_errors(self):
        """When workbook from run artifact is not parseable, analysis fails with error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an unparseable workbook file.
            wb_path = os.path.join(tmpdir, "bad.workbook.md")
            with open(wb_path, "w") as f:
                f.write("This is not a valid workbook")

            artifact = _make_completed_artifact(workbook_path=wb_path)
            artifact_path = _save_artifact_to_tmpdir(artifact, tmpdir)

            result = _run_cli(
                "analyze",
                artifact_path,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("error", result.stderr.lower())
            self.assertIn("parse", result.stderr.lower())

            # No analysis artifact should be created.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("analysis_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 0)


class TestCLIAnalyzeEndToEnd(unittest.TestCase):
    """End-to-end test: run → analyze pipeline."""

    def test_run_then_analyze(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy workbook.
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            # Run.
            run_result = _run_cli(
                "run",
                wb_dst,
                "--workflow-config",
                _fixture("workflow_text_ok.json"),
                "--output-dir",
                tmpdir,
            )
            self.assertEqual(run_result.returncode, 0, msg=run_result.stderr)

            # Find run artifact.
            run_files = [
                f for f in os.listdir(tmpdir) if f.startswith("run_") and f.endswith(".json")
            ]
            self.assertEqual(len(run_files), 1)
            run_path = os.path.join(tmpdir, run_files[0])

            # Analyze.
            analyze_result = _run_cli(
                "analyze",
                run_path,
                "--semantic-config",
                _fixture("semantic_ok.json"),
                "--output-dir",
                tmpdir,
            )
            self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr)

            # Verify analysis artifact exists.
            md_files = [
                f for f in os.listdir(tmpdir) if f.startswith("analysis_") and f.endswith(".md")
            ]
            self.assertEqual(len(md_files), 1)

            # Verify workbook has both run and analysis references.
            from lightassay.workbook_parser import parse

            with open(wb_dst) as f:
                wb = parse(f.read())
            self.assertIsNotNone(wb.artifact_references.run)
            self.assertIsNotNone(wb.artifact_references.analysis)


if __name__ == "__main__":
    unittest.main()
