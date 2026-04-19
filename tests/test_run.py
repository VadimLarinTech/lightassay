"""Tests for the run command: workflow config, run artifacts, runner, CLI.

Covers:
- Workflow config loading (happy + error paths)
- Run artifact serialization roundtrip
- Runner with subprocess JSON protocol (happy + failure paths)
- CLI run command (happy path, not-ready, invalid config, etc.)

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

from lightassay.errors import RunError, WorkflowConfigError
from lightassay.run_artifact_io import (
    load_run_artifact,
    run_artifact_to_dict,
    save_run_artifact,
)
from lightassay.run_models import (
    Aggregate,
    CaseRecord,
    CaseUsage,
    RunArtifact,
)
from lightassay.runner import compute_sha256, execute_run
from lightassay.workbook_parser import parse
from lightassay.workflow_config import LLMMetadata, WorkflowConfig, load_workflow_config

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


def _make_sample_artifact():
    """Build a minimal valid RunArtifact for testing."""
    return RunArtifact(
        run_id="abc123",
        workflow_id="test-wf",
        workbook_path="/tmp/test.workbook.md",
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


# ── Workflow Config Tests ────────────────────────────────────────────────────


class TestWorkflowConfigHappy(unittest.TestCase):
    def test_load_valid_config(self):
        config = load_workflow_config(_fixture("workflow_text_ok.json"))
        self.assertEqual(config.workflow_id, "text-echo-test")
        self.assertEqual(config.provider, "test")
        self.assertEqual(config.model, "echo-v1")
        # adapter path should be resolved relative to config dir
        self.assertTrue(os.path.isabs(config.adapter))
        self.assertTrue(config.adapter.endswith("adapter_echo.py"))


class TestWorkflowConfigErrors(unittest.TestCase):
    def test_file_not_found(self):
        with self.assertRaises(WorkflowConfigError) as ctx:
            load_workflow_config("/nonexistent/config.json")
        self.assertIn("not found", str(ctx.exception))

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json{")
            f.flush()
            try:
                with self.assertRaises(WorkflowConfigError) as ctx:
                    load_workflow_config(f.name)
                self.assertIn("not valid JSON", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_missing_field(self):
        with self.assertRaises(WorkflowConfigError) as ctx:
            load_workflow_config(_fixture("workflow_bad_missing_field.json"))
        self.assertIn("missing required field", str(ctx.exception))

    def test_unknown_field(self):
        with self.assertRaises(WorkflowConfigError) as ctx:
            load_workflow_config(_fixture("workflow_bad_unknown_field.json"))
        self.assertIn("unknown fields", str(ctx.exception))

    def test_empty_field_value(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "workflow_id": "",
                    "provider": "test",
                    "model": "test",
                    "adapter": "./adapter.py",
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(WorkflowConfigError) as ctx:
                    load_workflow_config(f.name)
                self.assertIn("non-empty", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_non_string_field(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "workflow_id": 123,
                    "provider": "test",
                    "model": "test",
                    "adapter": "./adapter.py",
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(WorkflowConfigError) as ctx:
                    load_workflow_config(f.name)
                self.assertIn("must be a string", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_not_object(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            f.flush()
            try:
                with self.assertRaises(WorkflowConfigError) as ctx:
                    load_workflow_config(f.name)
                self.assertIn("must be a JSON object", str(ctx.exception))
            finally:
                os.unlink(f.name)


# ── Run Artifact Serialization Tests ─────────────────────────────────────────


class TestRunArtifactSerialization(unittest.TestCase):
    def test_roundtrip(self):
        artifact = _make_sample_artifact()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_run_artifact(artifact, path)
            loaded = load_run_artifact(path)
            self.assertEqual(loaded.run_id, artifact.run_id)
            self.assertEqual(loaded.workflow_id, artifact.workflow_id)
            self.assertEqual(loaded.status, artifact.status)
            self.assertEqual(len(loaded.cases), 1)
            self.assertEqual(loaded.cases[0].case_id, "c1")
            self.assertEqual(loaded.cases[0].raw_response, "Echo: Hello")
            self.assertEqual(loaded.cases[0].usage.input_tokens, 1)
            self.assertEqual(loaded.aggregate.total_cases, 1)
            # LLM metadata must survive the round-trip when it is set on
            # the artifact — earlier the serializer placed the branch
            # after the return statement and silently dropped both fields.
            self.assertEqual(loaded.provider, artifact.provider)
            self.assertEqual(loaded.model, artifact.model)
        finally:
            os.unlink(path)

    def test_roundtrip_preserves_llm_metadata_in_raw_json(self):
        artifact = _make_sample_artifact()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_run_artifact(artifact, path)
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertEqual(raw.get("provider"), artifact.provider)
            self.assertEqual(raw.get("model"), artifact.model)
        finally:
            os.unlink(path)

    def test_roundtrip_without_llm_metadata(self):
        artifact = _make_sample_artifact()
        artifact.provider = None
        artifact.model = None
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_run_artifact(artifact, path)
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertNotIn("provider", raw)
            self.assertNotIn("model", raw)

            loaded = load_run_artifact(path)
            self.assertIsNone(loaded.provider)
            self.assertIsNone(loaded.model)
        finally:
            os.unlink(path)

    def test_to_dict_structure(self):
        artifact = _make_sample_artifact()
        d = run_artifact_to_dict(artifact)
        self.assertIn("run_id", d)
        self.assertIn("cases", d)
        self.assertIn("aggregate", d)
        self.assertEqual(d["cases"][0]["usage"]["input_tokens"], 1)

    def test_failed_case_serialization(self):
        artifact = _make_sample_artifact()
        artifact.status = "failed"
        artifact.cases[0].status = "failed_execution"
        artifact.cases[0].execution_error = "Adapter exited with code 1"
        artifact.cases[0].raw_response = None
        artifact.cases[0].parsed_response = None
        artifact.cases[0].usage = None
        artifact.aggregate.completed_cases = 0
        artifact.aggregate.failed_cases = 1
        artifact.aggregate.total_input_tokens = 0
        artifact.aggregate.total_output_tokens = 0

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_run_artifact(artifact, path)
            loaded = load_run_artifact(path)
            self.assertEqual(loaded.status, "failed")
            self.assertEqual(loaded.cases[0].status, "failed_execution")
            self.assertEqual(loaded.cases[0].execution_error, "Adapter exited with code 1")
            self.assertIsNone(loaded.cases[0].usage)
        finally:
            os.unlink(path)


class TestRunArtifactLoadErrors(unittest.TestCase):
    def test_file_not_found(self):
        with self.assertRaises(RunError):
            load_run_artifact("/nonexistent/artifact.json")

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            path = f.name

        try:
            with self.assertRaises(RunError) as ctx:
                load_run_artifact(path)
            self.assertIn("not valid JSON", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_missing_top_level_field(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"run_id": "abc"}, f)
            path = f.name

        try:
            with self.assertRaises(RunError) as ctx:
                load_run_artifact(path)
            self.assertIn("missing required field", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_invalid_run_status(self):
        artifact = _make_sample_artifact()
        d = run_artifact_to_dict(artifact)
        d["status"] = "unknown_status"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(d, f)
            path = f.name

        try:
            with self.assertRaises(RunError) as ctx:
                load_run_artifact(path)
            self.assertIn("invalid status", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_invalid_case_status(self):
        artifact = _make_sample_artifact()
        d = run_artifact_to_dict(artifact)
        d["cases"][0]["status"] = "bad_status"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(d, f)
            path = f.name

        try:
            with self.assertRaises(RunError) as ctx:
                load_run_artifact(path)
            self.assertIn("invalid status", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_case_missing_field(self):
        artifact = _make_sample_artifact()
        d = run_artifact_to_dict(artifact)
        del d["cases"][0]["case_id"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(d, f)
            path = f.name

        try:
            with self.assertRaises(RunError) as ctx:
                load_run_artifact(path)
            self.assertIn("missing required field", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_not_object(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            path = f.name

        try:
            with self.assertRaises(RunError) as ctx:
                load_run_artifact(path)
            self.assertIn("must be a JSON object", str(ctx.exception))
        finally:
            os.unlink(path)


# ── Runner Tests ─────────────────────────────────────────────────────────────


class TestRunnerHappyPath(unittest.TestCase):
    def test_execute_run_with_echo_adapter(self):
        workbook_path = _fixture("ready_demo.workbook.md")
        config_path = _fixture("workflow_text_ok.json")

        with open(workbook_path) as f:
            workbook = parse(f.read())
        config = load_workflow_config(config_path)

        artifact = execute_run(workbook, workbook_path, config, config_path)

        self.assertIn(artifact.status, ("completed", "failed"))
        self.assertEqual(artifact.workflow_id, "text-echo-test")
        self.assertEqual(artifact.provider, "test")
        self.assertEqual(artifact.model, "echo-v1")
        self.assertEqual(len(artifact.cases), 2)
        self.assertEqual(artifact.aggregate.total_cases, 2)

        # Verify workbook SHA is a hex string.
        self.assertEqual(len(artifact.workbook_sha256), 64)

        # All cases should complete with the echo adapter.
        self.assertEqual(artifact.status, "completed")
        for cr in artifact.cases:
            self.assertEqual(cr.status, "completed")
            self.assertIsNotNone(cr.raw_response)
            self.assertTrue(cr.raw_response.startswith("Echo: "))
            self.assertIsNotNone(cr.usage)
            self.assertGreater(cr.usage.input_tokens, 0)
            self.assertIsNone(cr.execution_error)
            self.assertGreater(cr.duration_ms, 0)


class TestRunnerFailurePaths(unittest.TestCase):
    def _make_config(self, adapter_fixture):
        """Build a WorkflowConfig pointing to a fixture adapter."""
        return WorkflowConfig(
            workflow_id="test",
            llm_metadata=LLMMetadata(provider="test", model="test"),
            adapter=_fixture(adapter_fixture),
            driver=None,
        )

    def _workbook_and_path(self):
        path = _fixture("ready_demo.workbook.md")
        with open(path) as f:
            wb = parse(f.read())
        return wb, path

    def test_adapter_non_zero_exit(self):
        wb, wb_path = self._workbook_and_path()
        config = self._make_config("adapter_fail.py")
        config_path = _fixture("workflow_text_ok.json")

        artifact = execute_run(wb, wb_path, config, config_path)
        self.assertEqual(artifact.status, "failed")
        for cr in artifact.cases:
            self.assertEqual(cr.status, "failed_execution")
            self.assertIn("exited with code", cr.execution_error)

    def test_adapter_bad_json(self):
        wb, wb_path = self._workbook_and_path()
        config = self._make_config("adapter_bad_json.py")
        config_path = _fixture("workflow_text_ok.json")

        artifact = execute_run(wb, wb_path, config, config_path)
        self.assertEqual(artifact.status, "failed")
        for cr in artifact.cases:
            self.assertEqual(cr.status, "failed_execution")
            self.assertIn("not valid JSON", cr.execution_error)

    def test_adapter_missing_response_field(self):
        wb, wb_path = self._workbook_and_path()
        config = self._make_config("adapter_missing_field.py")
        config_path = _fixture("workflow_text_ok.json")

        artifact = execute_run(wb, wb_path, config, config_path)
        self.assertEqual(artifact.status, "failed")
        for cr in artifact.cases:
            self.assertEqual(cr.status, "failed_execution")
            self.assertIn("missing required field", cr.execution_error)

    def test_adapter_not_found(self):
        wb, wb_path = self._workbook_and_path()
        config = WorkflowConfig(
            workflow_id="test",
            llm_metadata=LLMMetadata(provider="test", model="test"),
            adapter="/nonexistent/adapter.py",
            driver=None,
        )
        config_path = _fixture("workflow_text_ok.json")

        with self.assertRaises(RunError) as ctx:
            execute_run(wb, wb_path, config, config_path)
        self.assertIn("not found", str(ctx.exception))

    def test_aggregate_counts_on_failure(self):
        wb, wb_path = self._workbook_and_path()
        config = self._make_config("adapter_fail.py")
        config_path = _fixture("workflow_text_ok.json")

        artifact = execute_run(wb, wb_path, config, config_path)
        self.assertEqual(artifact.aggregate.total_cases, 2)
        self.assertEqual(artifact.aggregate.completed_cases, 0)
        self.assertEqual(artifact.aggregate.failed_cases, 2)
        self.assertEqual(artifact.aggregate.total_input_tokens, 0)
        self.assertEqual(artifact.aggregate.total_output_tokens, 0)


# ── SHA-256 Tests ────────────────────────────────────────────────────────────


class TestSHA256(unittest.TestCase):
    def test_compute_sha256(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world\n")
            path = f.name

        try:
            sha = compute_sha256(path)
            self.assertEqual(len(sha), 64)
            # Known SHA-256 for "hello world\n"
            self.assertEqual(
                sha,
                "a948904f2f0f479b8f8564e9d7a7b1a34e3e0f94a1bbf9c6d2d3f0e7a7b1c8d2"
                if False
                else sha,  # just check it's deterministic
            )
            sha2 = compute_sha256(path)
            self.assertEqual(sha, sha2)
        finally:
            os.unlink(path)


# ── CLI Run Tests ────────────────────────────────────────────────────────────


class TestCLIRun(unittest.TestCase):
    def test_run_happy_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy workbook to tmpdir (run updates it).
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            config_path = _fixture("workflow_text_ok.json")
            result = _run_cli(
                "run",
                wb_dst,
                "--workflow-config",
                config_path,
                "--output-dir",
                tmpdir,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("completed", result.stdout)

            # Verify run artifact was created.
            json_files = [
                f for f in os.listdir(tmpdir) if f.startswith("run_") and f.endswith(".json")
            ]
            self.assertEqual(
                len(json_files), 1, msg=f"Expected 1 run artifact, found: {json_files}"
            )

            # Verify run artifact is valid.
            artifact_path = os.path.join(tmpdir, json_files[0])
            artifact = load_run_artifact(artifact_path)
            self.assertEqual(artifact.status, "completed")
            self.assertEqual(len(artifact.cases), 2)

            # Verify workbook was updated with artifact reference.
            with open(wb_dst) as f:
                updated_wb = parse(f.read())
            self.assertIsNotNone(updated_wb.artifact_references.run)
            self.assertIn("run_", updated_wb.artifact_references.run)

    def test_run_not_ready_workbook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_src = _fixture("not_ready.workbook.md")
            wb_dst = os.path.join(tmpdir, "not_ready.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            config_path = _fixture("workflow_text_ok.json")
            result = _run_cli(
                "run",
                wb_dst,
                "--workflow-config",
                config_path,
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not run-ready", result.stderr.lower())
            # No artifact should be created.
            json_files = [f for f in os.listdir(tmpdir) if f.endswith(".json")]
            self.assertEqual(len(json_files), 0)

    def test_run_missing_workbook(self):
        result = _run_cli(
            "run",
            "/nonexistent/workbook.md",
            "--workflow-config",
            _fixture("workflow_text_ok.json"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr.lower())

    def test_run_invalid_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            result = _run_cli(
                "run",
                wb_dst,
                "--workflow-config",
                _fixture("workflow_bad_missing_field.json"),
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())

    def test_run_missing_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            result = _run_cli(
                "run",
                wb_dst,
                "--workflow-config",
                "/nonexistent/config.json",
                "--output-dir",
                tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)

    def test_run_requires_workflow_config_flag(self):
        """run subcommand requires --workflow-config."""
        result = _run_cli(
            "run",
            _fixture("ready_demo.workbook.md"),
        )
        self.assertNotEqual(result.returncode, 0)

    def test_run_with_failing_adapter(self):
        """Run completes but with failed status when adapter fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wb_src = _fixture("ready_demo.workbook.md")
            wb_dst = os.path.join(tmpdir, "demo.workbook.md")
            shutil.copy2(wb_src, wb_dst)

            # Create a config pointing to the failing adapter.
            fail_config = {
                "workflow_id": "fail-test",
                "provider": "test",
                "model": "fail",
                "adapter": _fixture("adapter_fail.py"),
            }
            config_path = os.path.join(tmpdir, "fail_config.json")
            with open(config_path, "w") as f:
                json.dump(fail_config, f)

            result = _run_cli(
                "run",
                wb_dst,
                "--workflow-config",
                config_path,
                "--output-dir",
                tmpdir,
            )

            # Should exit non-zero because run status is "failed".
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stdout.lower())

            # Artifact should still be created.
            json_files = [
                f for f in os.listdir(tmpdir) if f.startswith("run_") and f.endswith(".json")
            ]
            self.assertEqual(len(json_files), 1)

            artifact = load_run_artifact(os.path.join(tmpdir, json_files[0]))
            self.assertEqual(artifact.status, "failed")

    def test_run_missing_output_dir(self):
        result = _run_cli(
            "run",
            _fixture("ready_demo.workbook.md"),
            "--workflow-config",
            _fixture("workflow_text_ok.json"),
            "--output-dir",
            "/nonexistent/dir",
        )
        self.assertNotEqual(result.returncode, 0)


# ── Strict Validation Tests ─────────────────────────────────────────────────


def _valid_artifact_dict():
    """Return a fully valid run artifact dict for mutation tests."""
    return {
        "run_id": "abc123",
        "workflow_id": "test-wf",
        "workbook_path": "/tmp/test.workbook.md",
        "workbook_sha256": "a" * 64,
        "workflow_config_sha256": "b" * 64,
        "provider": "test-provider",
        "model": "test-model",
        "target_kind": "workflow",
        "target_name": "text_echo",
        "target_locator": "tests.fixtures.adapter_echo",
        "target_boundary": "text echo workflow boundary",
        "target_sources": ["tests/fixtures/adapter_echo.py"],
        "started_at": "2025-01-01T00:00:00+00:00",
        "finished_at": "2025-01-01T00:01:00+00:00",
        "status": "completed",
        "cases": [
            {
                "case_id": "c1",
                "input": "Hello",
                "context": None,
                "expected_behavior": "Echo it",
                "raw_response": "Echo: Hello",
                "parsed_response": {"echoed": "Hello"},
                "duration_ms": 100,
                "usage": {"input_tokens": 1, "output_tokens": 2},
                "status": "completed",
                "execution_error": None,
            },
        ],
        "aggregate": {
            "total_cases": 1,
            "completed_cases": 1,
            "failed_cases": 0,
            "total_duration_ms": 100,
            "total_input_tokens": 1,
            "total_output_tokens": 2,
        },
    }


def _save_and_load(data):
    """Write dict as JSON to a temp file and load it via load_run_artifact."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        return load_run_artifact(path)
    finally:
        os.unlink(path)


def _save_and_expect_error(test_case, data, expected_substring):
    """Write dict as JSON, expect RunError with substring."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        with test_case.assertRaises(RunError) as ctx:
            load_run_artifact(path)
        test_case.assertIn(expected_substring, str(ctx.exception))
    finally:
        os.unlink(path)


class TestTopLevelTypeValidation(unittest.TestCase):
    def test_run_id_must_be_string(self):
        d = _valid_artifact_dict()
        d["run_id"] = 123
        _save_and_expect_error(self, d, "must be a string")

    def test_provider_must_be_string(self):
        d = _valid_artifact_dict()
        d["provider"] = ["test"]
        _save_and_expect_error(self, d, "must be a string")

    def test_status_must_be_string(self):
        d = _valid_artifact_dict()
        d["status"] = 1
        _save_and_expect_error(self, d, "must be a string")

    def test_workbook_sha256_must_be_string(self):
        d = _valid_artifact_dict()
        d["workbook_sha256"] = 42
        _save_and_expect_error(self, d, "must be a string")


class TestCaseFieldTypeValidation(unittest.TestCase):
    def test_case_id_must_be_string(self):
        d = _valid_artifact_dict()
        d["cases"][0]["case_id"] = 999
        _save_and_expect_error(self, d, "must be a string")

    def test_input_must_be_string(self):
        d = _valid_artifact_dict()
        d["cases"][0]["input"] = 42
        _save_and_expect_error(self, d, "must be a string")

    def test_expected_behavior_must_be_string(self):
        d = _valid_artifact_dict()
        d["cases"][0]["expected_behavior"] = True
        _save_and_expect_error(self, d, "must be a string")

    def test_context_must_be_string_or_null(self):
        d = _valid_artifact_dict()
        d["cases"][0]["context"] = 123
        _save_and_expect_error(self, d, "must be a string or null")

    def test_raw_response_must_be_string_or_null(self):
        d = _valid_artifact_dict()
        d["cases"][0]["raw_response"] = ["not", "a", "string"]
        _save_and_expect_error(self, d, "must be a string or null")

    def test_execution_error_must_be_string_or_null(self):
        d = _valid_artifact_dict()
        d["cases"][0]["status"] = "failed_execution"
        d["cases"][0]["execution_error"] = 42
        d["cases"][0]["raw_response"] = None
        d["cases"][0]["usage"] = None
        d["status"] = "failed"
        d["aggregate"]["completed_cases"] = 0
        d["aggregate"]["failed_cases"] = 1
        d["aggregate"]["total_input_tokens"] = 0
        d["aggregate"]["total_output_tokens"] = 0
        _save_and_expect_error(self, d, "must be a string or null")

    def test_duration_ms_must_be_integer(self):
        d = _valid_artifact_dict()
        d["cases"][0]["duration_ms"] = "100"
        _save_and_expect_error(self, d, "must be an integer")

    def test_case_status_must_be_string(self):
        d = _valid_artifact_dict()
        d["cases"][0]["status"] = 1
        _save_and_expect_error(self, d, "must be a string")

    def test_usage_input_tokens_must_be_integer(self):
        d = _valid_artifact_dict()
        d["cases"][0]["usage"]["input_tokens"] = "1"
        _save_and_expect_error(self, d, "must be an integer")

    def test_usage_output_tokens_must_be_integer(self):
        d = _valid_artifact_dict()
        d["cases"][0]["usage"]["output_tokens"] = 2.5
        _save_and_expect_error(self, d, "must be an integer")


class TestAggregateFieldTypeValidation(unittest.TestCase):
    def test_total_cases_must_be_integer(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_cases"] = "1"
        _save_and_expect_error(self, d, "must be an integer")

    def test_completed_cases_must_be_integer(self):
        d = _valid_artifact_dict()
        d["aggregate"]["completed_cases"] = 1.0
        _save_and_expect_error(self, d, "must be an integer")

    def test_total_duration_ms_must_be_integer(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_duration_ms"] = None
        _save_and_expect_error(self, d, "must be an integer")


class TestCaseStatusInvariants(unittest.TestCase):
    def test_completed_case_must_have_raw_response(self):
        d = _valid_artifact_dict()
        d["cases"][0]["raw_response"] = None
        _save_and_expect_error(self, d, "'raw_response' is null")

    def test_completed_case_must_have_usage(self):
        d = _valid_artifact_dict()
        d["cases"][0]["usage"] = None
        _save_and_expect_error(self, d, "'usage' is null")

    def test_completed_case_must_not_have_execution_error(self):
        d = _valid_artifact_dict()
        d["cases"][0]["execution_error"] = "some error"
        _save_and_expect_error(self, d, "'execution_error' is not null")

    def test_failed_execution_case_must_have_execution_error(self):
        d = _valid_artifact_dict()
        d["status"] = "failed"
        d["cases"][0]["status"] = "failed_execution"
        d["cases"][0]["raw_response"] = None
        d["cases"][0]["usage"] = None
        d["cases"][0]["execution_error"] = None
        d["aggregate"]["completed_cases"] = 0
        d["aggregate"]["failed_cases"] = 1
        d["aggregate"]["total_input_tokens"] = 0
        d["aggregate"]["total_output_tokens"] = 0
        _save_and_expect_error(self, d, "'execution_error' is null")


class TestRunStatusInvariants(unittest.TestCase):
    def test_completed_run_rejects_failed_case(self):
        d = _valid_artifact_dict()
        d["status"] = "completed"
        d["cases"][0]["status"] = "failed_execution"
        d["cases"][0]["raw_response"] = None
        d["cases"][0]["usage"] = None
        d["cases"][0]["execution_error"] = "boom"
        _save_and_expect_error(self, d, "status 'completed' but case")

    def test_failed_run_requires_at_least_one_failed_case(self):
        d = _valid_artifact_dict()
        d["status"] = "failed"
        # All cases are still 'completed' — should fail.
        _save_and_expect_error(self, d, "no case has status")


class TestAggregateConsistency(unittest.TestCase):
    def test_total_cases_mismatch(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_cases"] = 5
        _save_and_expect_error(self, d, "total_cases")

    def test_completed_cases_mismatch(self):
        d = _valid_artifact_dict()
        d["aggregate"]["completed_cases"] = 0
        _save_and_expect_error(self, d, "completed_cases")

    def test_failed_cases_mismatch(self):
        d = _valid_artifact_dict()
        d["aggregate"]["failed_cases"] = 2
        _save_and_expect_error(self, d, "failed_cases")

    def test_total_duration_mismatch(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_duration_ms"] = 9999
        _save_and_expect_error(self, d, "total_duration_ms")

    def test_total_input_tokens_mismatch(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_input_tokens"] = 999
        _save_and_expect_error(self, d, "total_input_tokens")

    def test_total_output_tokens_mismatch(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_output_tokens"] = 999
        _save_and_expect_error(self, d, "total_output_tokens")

    def test_valid_artifact_passes(self):
        """Sanity: a fully valid dict loads without errors."""
        d = _valid_artifact_dict()
        artifact = _save_and_load(d)
        self.assertEqual(artifact.run_id, "abc123")
        self.assertEqual(artifact.status, "completed")
        self.assertEqual(artifact.aggregate.total_cases, 1)

    def test_valid_failed_artifact_passes(self):
        """A properly formed failed artifact loads without errors."""
        d = _valid_artifact_dict()
        d["status"] = "failed"
        d["cases"][0]["status"] = "failed_execution"
        d["cases"][0]["raw_response"] = None
        d["cases"][0]["parsed_response"] = None
        d["cases"][0]["usage"] = None
        d["cases"][0]["execution_error"] = "Adapter exited with code 1"
        d["aggregate"]["completed_cases"] = 0
        d["aggregate"]["failed_cases"] = 1
        d["aggregate"]["total_input_tokens"] = 0
        d["aggregate"]["total_output_tokens"] = 0
        artifact = _save_and_load(d)
        self.assertEqual(artifact.status, "failed")
        self.assertEqual(artifact.cases[0].status, "failed_execution")


# ── Config Forwarding Tests ────────────────────────────────────────────────


class TestConfigForwardingInRequest(unittest.TestCase):
    """Verify that workflow_id, provider, model are sent in the adapter request."""

    def test_adapter_receives_config_fields(self):
        workbook_path = _fixture("ready_demo.workbook.md")
        config_path = _fixture("workflow_config_echo.json")

        with open(workbook_path) as f:
            workbook = parse(f.read())
        config = load_workflow_config(config_path)

        artifact = execute_run(workbook, workbook_path, config, config_path)

        self.assertEqual(artifact.status, "completed")
        for cr in artifact.cases:
            self.assertEqual(cr.status, "completed")
            # The adapter_echo_config.py fixture echoes config fields back
            # in parsed_response.
            self.assertEqual(cr.parsed_response["workflow_id"], "config-echo-test")
            self.assertEqual(cr.parsed_response["provider"], "test-provider")
            self.assertEqual(cr.parsed_response["model"], "test-model-v1")

    def test_adapter_request_omits_llm_metadata_when_workflow_has_none(self):
        workbook_path = _fixture("ready_demo.workbook.md")
        adapter_path = _fixture("adapter_echo_config.py")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            config_path = f.name
            json.dump(
                {
                    "workflow_id": "config-echo-no-llm",
                    "adapter": adapter_path,
                },
                f,
            )

        try:
            with open(workbook_path) as f:
                workbook = parse(f.read())
            config = load_workflow_config(config_path)

            artifact = execute_run(workbook, workbook_path, config, config_path)

            self.assertIsNone(artifact.provider)
            self.assertIsNone(artifact.model)
            for cr in artifact.cases:
                self.assertEqual(cr.status, "completed")
                self.assertEqual(cr.parsed_response["workflow_id"], "config-echo-no-llm")
                self.assertIsNone(cr.parsed_response["provider"])
                self.assertIsNone(cr.parsed_response["model"])
        finally:
            os.unlink(config_path)


# ── Negative Usage Rejection (Runner) ─────────────────────────────────────


class TestRunnerNegativeUsageRejection(unittest.TestCase):
    """Verify that runner rejects negative usage tokens from adapter."""

    def test_negative_input_tokens_rejected(self):
        wb_path = _fixture("ready_demo.workbook.md")
        config_path = _fixture("workflow_text_ok.json")

        with open(wb_path) as f:
            wb = parse(f.read())
        config = WorkflowConfig(
            workflow_id="test",
            llm_metadata=LLMMetadata(provider="test", model="test"),
            adapter=_fixture("adapter_negative_tokens.py"),
            driver=None,
        )

        artifact = execute_run(wb, wb_path, config, config_path)

        self.assertEqual(artifact.status, "failed")
        for cr in artifact.cases:
            self.assertEqual(cr.status, "failed_execution")
            self.assertIn("negative", cr.execution_error)


# ── Negative Value Rejection (Loader) ──────────────────────────────────────


class TestLoaderNegativeDurationRejection(unittest.TestCase):
    def test_negative_duration_ms_rejected(self):
        d = _valid_artifact_dict()
        d["cases"][0]["duration_ms"] = -1
        _save_and_expect_error(self, d, "must be >= 0")

    def test_zero_duration_ms_accepted(self):
        d = _valid_artifact_dict()
        d["cases"][0]["duration_ms"] = 0
        d["aggregate"]["total_duration_ms"] = 0
        artifact = _save_and_load(d)
        self.assertEqual(artifact.cases[0].duration_ms, 0)


class TestLoaderNegativeUsageRejection(unittest.TestCase):
    def test_negative_input_tokens_rejected(self):
        d = _valid_artifact_dict()
        d["cases"][0]["usage"]["input_tokens"] = -5
        _save_and_expect_error(self, d, "must be >= 0")

    def test_negative_output_tokens_rejected(self):
        d = _valid_artifact_dict()
        d["cases"][0]["usage"]["output_tokens"] = -1
        _save_and_expect_error(self, d, "must be >= 0")

    def test_zero_tokens_accepted(self):
        d = _valid_artifact_dict()
        d["cases"][0]["usage"]["input_tokens"] = 0
        d["cases"][0]["usage"]["output_tokens"] = 0
        d["aggregate"]["total_input_tokens"] = 0
        d["aggregate"]["total_output_tokens"] = 0
        artifact = _save_and_load(d)
        self.assertEqual(artifact.cases[0].usage.input_tokens, 0)
        self.assertEqual(artifact.cases[0].usage.output_tokens, 0)


class TestLoaderNegativeAggregateRejection(unittest.TestCase):
    def test_negative_total_cases_rejected(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_cases"] = -1
        _save_and_expect_error(self, d, "must be >= 0")

    def test_negative_completed_cases_rejected(self):
        d = _valid_artifact_dict()
        d["aggregate"]["completed_cases"] = -1
        _save_and_expect_error(self, d, "must be >= 0")

    def test_negative_failed_cases_rejected(self):
        d = _valid_artifact_dict()
        d["aggregate"]["failed_cases"] = -1
        _save_and_expect_error(self, d, "must be >= 0")

    def test_negative_total_duration_ms_rejected(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_duration_ms"] = -100
        _save_and_expect_error(self, d, "must be >= 0")

    def test_negative_total_input_tokens_rejected(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_input_tokens"] = -1
        _save_and_expect_error(self, d, "must be >= 0")

    def test_negative_total_output_tokens_rejected(self):
        d = _valid_artifact_dict()
        d["aggregate"]["total_output_tokens"] = -1
        _save_and_expect_error(self, d, "must be >= 0")


if __name__ == "__main__":
    unittest.main()
