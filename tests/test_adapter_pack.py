"""Tests for the first-party adapter pack.

Covers:
- Driver config validation (strict, per-type)
- python-callable driver: happy path, import errors, bad return, exceptions
- http driver: happy path via local test server, connection errors
- command driver: happy path, non-zero exit, bad JSON output
- Full run-path integration through execute_run for each driver

Run with:
    PYTHONPATH=src python3 -m unittest discover -s tests
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

# Add fixtures to sys.path so callable_echo can be imported by the driver.
_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
if _FIXTURES not in sys.path:
    sys.path.insert(0, os.path.abspath(_FIXTURES))

from lightassay.adapter_pack import (
    CommandDriverConfig,
    DriverError,
    HttpDriverConfig,
    PythonCallableDriverConfig,
    execute_driver,
    validate_driver_config,
)
from lightassay.errors import WorkflowConfigError
from lightassay.runner import execute_run
from lightassay.workbook_parser import parse
from lightassay.workflow_config import LLMMetadata, WorkflowConfig, load_workflow_config

_PYTHON = sys.executable
_REPO = os.path.join(os.path.dirname(__file__), "..")
_SRC_PATH = os.path.join(_REPO, "src")


def _fixture(name):
    return os.path.join(_FIXTURES, name)


def _load_ready_workbook():
    path = _fixture("ready_demo.workbook.md")
    with open(path) as f:
        return parse(f.read()), path


# ── Driver Config Validation Tests ──────────────────────────────────────────


class TestDriverConfigValidation(unittest.TestCase):
    """Strict validation of driver config dicts."""

    # ── python-callable ──

    def test_python_callable_valid(self):
        cfg = validate_driver_config(
            {
                "type": "python-callable",
                "module": "my_module",
                "function": "my_func",
            }
        )
        self.assertIsInstance(cfg, PythonCallableDriverConfig)
        self.assertEqual(cfg.module, "my_module")
        self.assertEqual(cfg.function, "my_func")

    def test_python_callable_missing_module(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "python-callable",
                    "function": "my_func",
                }
            )
        self.assertIn("missing required field", str(ctx.exception))
        self.assertIn("module", str(ctx.exception))

    def test_python_callable_missing_function(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "python-callable",
                    "module": "my_module",
                }
            )
        self.assertIn("missing required field", str(ctx.exception))
        self.assertIn("function", str(ctx.exception))

    def test_python_callable_unknown_field(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "python-callable",
                    "module": "my_module",
                    "function": "my_func",
                    "extra": "bad",
                }
            )
        self.assertIn("unknown fields", str(ctx.exception))

    def test_python_callable_empty_module(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "python-callable",
                    "module": "  ",
                    "function": "my_func",
                }
            )
        self.assertIn("non-empty", str(ctx.exception))

    def test_python_callable_non_string_module(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "python-callable",
                    "module": 123,
                    "function": "my_func",
                }
            )
        self.assertIn("must be a string", str(ctx.exception))

    # ── http ──

    def test_http_valid_minimal(self):
        cfg = validate_driver_config(
            {
                "type": "http",
                "url": "http://localhost:8080/api",
                "method": "POST",
            }
        )
        self.assertIsInstance(cfg, HttpDriverConfig)
        self.assertEqual(cfg.url, "http://localhost:8080/api")
        self.assertEqual(cfg.method, "POST")
        self.assertIsNone(cfg.headers)
        self.assertIsNone(cfg.timeout_seconds)

    def test_http_valid_full(self):
        cfg = validate_driver_config(
            {
                "type": "http",
                "url": "http://localhost:8080/api",
                "method": "POST",
                "headers": {"Authorization": "Bearer token123"},
                "timeout_seconds": 30,
            }
        )
        self.assertIsInstance(cfg, HttpDriverConfig)
        self.assertEqual(cfg.headers, {"Authorization": "Bearer token123"})
        self.assertEqual(cfg.timeout_seconds, 30)

    def test_http_missing_url(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "method": "POST",
                }
            )
        self.assertIn("missing required field", str(ctx.exception))
        self.assertIn("url", str(ctx.exception))

    def test_http_missing_method(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                }
            )
        self.assertIn("missing required field", str(ctx.exception))
        self.assertIn("method", str(ctx.exception))

    def test_http_unknown_field(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                    "method": "POST",
                    "extra": "bad",
                }
            )
        self.assertIn("unknown fields", str(ctx.exception))

    def test_http_bad_headers_type(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                    "method": "POST",
                    "headers": "not-a-dict",
                }
            )
        self.assertIn("headers", str(ctx.exception))
        self.assertIn("JSON object", str(ctx.exception))

    def test_http_bad_headers_values(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                    "method": "POST",
                    "headers": {"key": 123},
                }
            )
        self.assertIn("headers", str(ctx.exception))
        self.assertIn("string", str(ctx.exception))

    def test_http_bad_timeout_type(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                    "method": "POST",
                    "timeout_seconds": "30",
                }
            )
        self.assertIn("timeout_seconds", str(ctx.exception))
        self.assertIn("integer", str(ctx.exception))

    def test_http_zero_timeout(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                    "method": "POST",
                    "timeout_seconds": 0,
                }
            )
        self.assertIn("positive", str(ctx.exception))

    def test_http_negative_timeout(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                    "method": "POST",
                    "timeout_seconds": -5,
                }
            )
        self.assertIn("positive", str(ctx.exception))

    def test_http_boolean_timeout_rejected(self):
        """bool is a subclass of int; must be explicitly rejected."""
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "http",
                    "url": "http://localhost:8080",
                    "method": "POST",
                    "timeout_seconds": True,
                }
            )
        self.assertIn("timeout_seconds", str(ctx.exception))

    # ── command ──

    def test_command_valid(self):
        cfg = validate_driver_config(
            {
                "type": "command",
                "command": ["python3", "my_script.py"],
            }
        )
        self.assertIsInstance(cfg, CommandDriverConfig)
        self.assertEqual(cfg.command, ["python3", "my_script.py"])

    def test_command_missing_command(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config({"type": "command"})
        self.assertIn("missing required field", str(ctx.exception))
        self.assertIn("command", str(ctx.exception))

    def test_command_empty_list(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "command",
                    "command": [],
                }
            )
        self.assertIn("non-empty", str(ctx.exception))

    def test_command_non_list(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "command",
                    "command": "python3 my_script.py",
                }
            )
        self.assertIn("JSON array", str(ctx.exception))

    def test_command_non_string_element(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "command",
                    "command": ["python3", 42],
                }
            )
        self.assertIn("must be a string", str(ctx.exception))

    def test_command_empty_element(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "command",
                    "command": ["python3", "  "],
                }
            )
        self.assertIn("non-empty", str(ctx.exception))

    def test_command_unknown_field(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config(
                {
                    "type": "command",
                    "command": ["python3"],
                    "extra": "bad",
                }
            )
        self.assertIn("unknown fields", str(ctx.exception))

    # ── Common validation ──

    def test_missing_type(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config({"module": "x", "function": "y"})
        self.assertIn("missing required field", str(ctx.exception))
        self.assertIn("type", str(ctx.exception))

    def test_unknown_type(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config({"type": "magic-driver"})
        self.assertIn("Unknown driver type", str(ctx.exception))

    def test_non_dict(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config("not a dict")
        self.assertIn("JSON object", str(ctx.exception))

    def test_non_string_type(self):
        with self.assertRaises(ValueError) as ctx:
            validate_driver_config({"type": 123})
        self.assertIn("must be a string", str(ctx.exception))


# ── Workflow Config with Driver Tests ───────────────────────────────────────


class TestWorkflowConfigWithDriver(unittest.TestCase):
    """Test workflow config loading with the driver field."""

    def test_load_python_callable_config(self):
        config = load_workflow_config(_fixture("workflow_driver_python_callable.json"))
        self.assertEqual(config.workflow_id, "callable-echo-test")
        self.assertEqual(config.provider, "test")
        self.assertEqual(config.model, "echo-v1")
        self.assertIsNone(config.adapter)
        self.assertIsNotNone(config.driver)
        self.assertIsInstance(config.driver, PythonCallableDriverConfig)
        self.assertEqual(config.driver.module, "callable_echo")
        self.assertEqual(config.driver.function, "handle_request")

    def test_legacy_adapter_still_works(self):
        config = load_workflow_config(_fixture("workflow_text_ok.json"))
        self.assertEqual(config.workflow_id, "text-echo-test")
        self.assertIsNotNone(config.adapter)
        self.assertIsNone(config.driver)

    def test_both_adapter_and_driver_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "test",
                    "adapter": "./test.py",
                    "driver": {"type": "command", "command": ["test"]},
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(WorkflowConfigError) as ctx:
                    load_workflow_config(f.name)
                self.assertIn("exactly one", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_neither_adapter_nor_driver_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "test",
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(WorkflowConfigError) as ctx:
                    load_workflow_config(f.name)
                self.assertIn("must have either", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_invalid_driver_config_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "test",
                    "driver": {"type": "unknown-driver"},
                },
                f,
            )
            f.flush()
            try:
                with self.assertRaises(WorkflowConfigError) as ctx:
                    load_workflow_config(f.name)
                self.assertIn("driver", str(ctx.exception).lower())
            finally:
                os.unlink(f.name)


# ── Python-Callable Driver Tests ────────────────────────────────────────────


class TestPythonCallableDriver(unittest.TestCase):
    """Direct driver execution tests (not through runner)."""

    def test_happy_path(self):
        cfg = PythonCallableDriverConfig(module="callable_echo", function="handle_request")
        request = {
            "case_id": "c1",
            "input": "hello world",
            "context": None,
            "workflow_id": "test",
            "provider": "test",
            "model": "test",
        }
        response = execute_driver(cfg, request)
        self.assertEqual(response["raw_response"], "Echo: hello world")
        self.assertEqual(response["parsed_response"]["echoed"], "hello world")
        self.assertEqual(response["usage"]["input_tokens"], 2)
        self.assertEqual(response["usage"]["output_tokens"], 3)

    def test_import_error(self):
        cfg = PythonCallableDriverConfig(module="nonexistent_module_xyz", function="func")
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("failed to import", str(ctx.exception))

    def test_missing_function(self):
        cfg = PythonCallableDriverConfig(module="callable_echo", function="nonexistent_func")
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("has no attribute", str(ctx.exception))

    def test_bad_return_type(self):
        cfg = PythonCallableDriverConfig(module="callable_echo", function="bad_return_type")
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("must return a dict", str(ctx.exception))

    def test_function_raises(self):
        cfg = PythonCallableDriverConfig(module="callable_echo", function="raises_error")
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("raised RuntimeError", str(ctx.exception))


# ── Command Driver Tests ────────────────────────────────────────────────────


class TestCommandDriver(unittest.TestCase):
    """Direct driver execution tests for the command driver."""

    def test_happy_path(self):
        cfg = CommandDriverConfig(command=[_PYTHON, _fixture("adapter_echo.py")])
        request = {
            "case_id": "c1",
            "input": "hello world",
            "context": None,
            "workflow_id": "test",
            "provider": "test",
            "model": "test",
        }
        response = execute_driver(cfg, request)
        self.assertEqual(response["raw_response"], "Echo: hello world")
        self.assertEqual(response["parsed_response"]["echoed"], "hello world")

    def test_nonzero_exit(self):
        cfg = CommandDriverConfig(command=[_PYTHON, _fixture("adapter_fail.py")])
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("exited with code", str(ctx.exception))

    def test_bad_json_output(self):
        cfg = CommandDriverConfig(command=[_PYTHON, _fixture("adapter_bad_json.py")])
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_command_not_found(self):
        cfg = CommandDriverConfig(command=["/nonexistent/binary"])
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("not found", str(ctx.exception))

    def test_nonzero_exit_with_stdout(self):
        """Non-zero exit must include bounded stdout excerpt in error."""
        cfg = CommandDriverConfig(command=[_PYTHON, _fixture("adapter_fail_with_output.py")])
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        err = str(ctx.exception)
        self.assertIn("exited with code", err)
        self.assertIn("stdout:", err)
        self.assertIn("diagnostic:", err)

    def test_nonzero_exit_without_stdout(self):
        """Non-zero exit with no stdout should not include stdout field."""
        cfg = CommandDriverConfig(command=[_PYTHON, _fixture("adapter_fail.py")])
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        err = str(ctx.exception)
        self.assertIn("exited with code", err)
        self.assertNotIn("stdout:", err)

    def test_config_dir_cwd(self):
        """Command with relative script path resolves against config_dir, not caller cwd.

        Creates a temp directory tree:
            tmpdir/
                scripts/
                    echo_adapter.py   (copy of adapter_echo.py)
        Sets config_dir=tmpdir, command=["python3", "scripts/echo_adapter.py"].
        The caller's cwd is irrelevant — the subprocess must find the script
        via config_dir.
        """
        import shutil as sh

        with tempfile.TemporaryDirectory() as tmpdir:
            scripts_dir = os.path.join(tmpdir, "scripts")
            os.makedirs(scripts_dir)
            sh.copy2(_fixture("adapter_echo.py"), os.path.join(scripts_dir, "echo_adapter.py"))

            cfg = CommandDriverConfig(
                command=[_PYTHON, "scripts/echo_adapter.py"],
                config_dir=tmpdir,
            )
            request = {
                "case_id": "c1",
                "input": "cwd test",
                "context": None,
                "workflow_id": "test",
                "provider": "test",
                "model": "test",
            }
            # Execute from a directory that does NOT contain scripts/.
            # Without config_dir cwd, this would fail.
            original_cwd = os.getcwd()
            try:
                os.chdir("/tmp")
                response = execute_driver(cfg, request)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(response["raw_response"], "Echo: cwd test")

    def test_config_dir_none_uses_caller_cwd(self):
        """Without config_dir, subprocess uses caller's cwd (legacy behavior)."""
        cfg = CommandDriverConfig(
            command=[_PYTHON, _fixture("adapter_echo.py")],
            config_dir=None,
        )
        response = execute_driver(
            cfg,
            {
                "case_id": "c1",
                "input": "no config_dir",
                "context": None,
                "workflow_id": "test",
                "provider": "test",
                "model": "test",
            },
        )
        self.assertEqual(response["raw_response"], "Echo: no config_dir")


# ── Command Driver Config-Origin Tests ─────────────────────────────────────


class TestCommandDriverConfigOrigin(unittest.TestCase):
    """Test that load_workflow_config injects config_dir into CommandDriverConfig."""

    def test_config_dir_set_on_load(self):
        """load_workflow_config must set config_dir on CommandDriverConfig."""
        config = load_workflow_config(_fixture("workflow_driver_command.json"))
        self.assertIsInstance(config.driver, CommandDriverConfig)
        self.assertIsNotNone(config.driver.config_dir)
        # config_dir must be the directory containing the config file.
        expected_dir = os.path.dirname(os.path.abspath(_fixture("workflow_driver_command.json")))
        self.assertEqual(config.driver.config_dir, expected_dir)

    def test_config_dir_not_set_on_non_command_driver(self):
        """Non-command drivers should not have config_dir."""
        config = load_workflow_config(_fixture("workflow_driver_python_callable.json"))
        self.assertIsInstance(config.driver, PythonCallableDriverConfig)
        # PythonCallableDriverConfig has no config_dir attribute.
        self.assertFalse(hasattr(config.driver, "config_dir"))

    def test_relative_command_execution_independent_of_caller_cwd(self):
        """Full integration: relative command path works from any caller cwd.

        Creates a temp tree with a workflow config and adapter script,
        loads the config, and executes a run from a different cwd.
        """
        import shutil as sh

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create adapter script in a subdirectory.
            adapter_dir = os.path.join(tmpdir, "adapters")
            os.makedirs(adapter_dir)
            sh.copy2(
                _fixture("adapter_echo.py"),
                os.path.join(adapter_dir, "my_echo.py"),
            )

            # Create workflow config with relative command path.
            config_data = {
                "workflow_id": "origin-test",
                "provider": "test",
                "model": "test",
                "driver": {
                    "type": "command",
                    "command": [_PYTHON, "adapters/my_echo.py"],
                },
            }
            config_path = os.path.join(tmpdir, "workflow.json")
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            # Load config — config_dir should be tmpdir.
            config = load_workflow_config(config_path)
            self.assertEqual(config.driver.config_dir, tmpdir)

            # Execute from a completely different directory.
            workbook, workbook_path = _load_ready_workbook()
            original_cwd = os.getcwd()
            try:
                os.chdir("/tmp")
                artifact = execute_run(
                    workbook,
                    workbook_path,
                    config,
                    config_path,
                )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(artifact.status, "completed")
            for cr in artifact.cases:
                self.assertEqual(cr.status, "completed")
                self.assertTrue(cr.raw_response.startswith("Echo: "))


# ── HTTP Driver Tests ───────────────────────────────────────────────────────


class _EchoHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Simple test HTTP handler that implements the adapter echo contract."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        request = json.loads(body)

        response = {
            "raw_response": "Echo: " + request.get("input", ""),
            "parsed_response": {"echoed": request.get("input", "")},
            "usage": {
                "input_tokens": len(request.get("input", "").split()),
                "output_tokens": len(request.get("input", "").split()) + 1,
            },
        }

        response_body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format, *args):
        """Suppress default log output during tests."""
        pass


class _ErrorHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Test HTTP handler that returns a 500 error."""

    def do_POST(self):
        self.send_response(500)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Internal Server Error")

    def log_message(self, format, *args):
        pass


class _BadJsonHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Test HTTP handler that returns invalid JSON."""

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"not valid json{{{")

    def log_message(self, format, *args):
        pass


def _start_test_server(handler_class):
    """Start a local HTTP test server and return (server, port)."""
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_class)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


class TestHttpDriver(unittest.TestCase):
    """Direct driver execution tests for the http driver.

    All tests use local HTTP test servers — no external calls.
    """

    def test_happy_path(self):
        server, port = _start_test_server(_EchoHTTPHandler)
        try:
            cfg = HttpDriverConfig(
                url=f"http://127.0.0.1:{port}/echo",
                method="POST",
                headers=None,
                timeout_seconds=None,
            )
            request = {
                "case_id": "c1",
                "input": "hello world",
                "context": None,
                "workflow_id": "test",
                "provider": "test",
                "model": "test",
            }
            response = execute_driver(cfg, request)
            self.assertEqual(response["raw_response"], "Echo: hello world")
            self.assertEqual(response["parsed_response"]["echoed"], "hello world")
            self.assertEqual(response["usage"]["input_tokens"], 2)
            self.assertEqual(response["usage"]["output_tokens"], 3)
        finally:
            server.shutdown()
            server.server_close()

    def test_with_custom_headers(self):
        server, port = _start_test_server(_EchoHTTPHandler)
        try:
            cfg = HttpDriverConfig(
                url=f"http://127.0.0.1:{port}/echo",
                method="POST",
                headers={"X-Custom-Header": "test-value"},
                timeout_seconds=5,
            )
            response = execute_driver(
                cfg,
                {
                    "case_id": "c1",
                    "input": "test",
                    "context": None,
                    "workflow_id": "test",
                    "provider": "test",
                    "model": "test",
                },
            )
            self.assertEqual(response["raw_response"], "Echo: test")
        finally:
            server.shutdown()
            server.server_close()

    def test_http_error(self):
        server, port = _start_test_server(_ErrorHTTPHandler)
        try:
            cfg = HttpDriverConfig(
                url=f"http://127.0.0.1:{port}/echo",
                method="POST",
                headers=None,
                timeout_seconds=None,
            )
            with self.assertRaises(DriverError) as ctx:
                execute_driver(cfg, {"input": "test"})
            self.assertIn("HTTP 500", str(ctx.exception))
        finally:
            server.shutdown()
            server.server_close()

    def test_bad_json_response(self):
        server, port = _start_test_server(_BadJsonHTTPHandler)
        try:
            cfg = HttpDriverConfig(
                url=f"http://127.0.0.1:{port}/echo",
                method="POST",
                headers=None,
                timeout_seconds=None,
            )
            with self.assertRaises(DriverError) as ctx:
                execute_driver(cfg, {"input": "test"})
            self.assertIn("not valid JSON", str(ctx.exception))
        finally:
            server.shutdown()
            server.server_close()

    def test_connection_refused(self):
        cfg = HttpDriverConfig(
            url="http://127.0.0.1:1/echo",
            method="POST",
            headers=None,
            timeout_seconds=1,
        )
        with self.assertRaises(DriverError) as ctx:
            execute_driver(cfg, {"input": "test"})
        self.assertIn("connection failed", str(ctx.exception).lower())


# ── Full Run-Path Integration Tests ─────────────────────────────────────────


class TestRunPathPythonCallable(unittest.TestCase):
    """Execute a full run through the python-callable driver."""

    def test_full_run_with_python_callable(self):
        workbook, workbook_path = _load_ready_workbook()

        config = WorkflowConfig(
            workflow_id="callable-echo-test",
            llm_metadata=LLMMetadata(provider="test", model="echo-v1"),
            adapter=None,
            driver=PythonCallableDriverConfig(
                module="callable_echo",
                function="handle_request",
            ),
        )

        # We need a real config file for SHA computation.
        config_path = _fixture("workflow_driver_python_callable.json")

        artifact = execute_run(workbook, workbook_path, config, config_path)

        self.assertEqual(artifact.status, "completed")
        self.assertEqual(artifact.workflow_id, "callable-echo-test")
        self.assertEqual(artifact.provider, "test")
        self.assertEqual(artifact.model, "echo-v1")
        self.assertEqual(len(artifact.cases), 2)
        self.assertEqual(artifact.aggregate.total_cases, 2)
        self.assertEqual(artifact.aggregate.completed_cases, 2)
        self.assertEqual(artifact.aggregate.failed_cases, 0)

        for cr in artifact.cases:
            self.assertEqual(cr.status, "completed")
            self.assertIsNotNone(cr.raw_response)
            self.assertTrue(cr.raw_response.startswith("Echo: "))
            self.assertIsNotNone(cr.usage)
            self.assertGreater(cr.usage.input_tokens, 0)
            self.assertGreater(cr.usage.output_tokens, 0)

    def test_full_run_with_bad_callable(self):
        """Driver error should produce failed_execution, not crash."""
        workbook, workbook_path = _load_ready_workbook()

        config = WorkflowConfig(
            workflow_id="bad-callable-test",
            llm_metadata=LLMMetadata(provider="test", model="echo-v1"),
            adapter=None,
            driver=PythonCallableDriverConfig(
                module="callable_echo",
                function="raises_error",
            ),
        )

        config_path = _fixture("workflow_driver_python_callable.json")

        artifact = execute_run(workbook, workbook_path, config, config_path)

        self.assertEqual(artifact.status, "failed")
        self.assertEqual(artifact.aggregate.total_cases, 2)
        self.assertEqual(artifact.aggregate.failed_cases, 2)
        self.assertEqual(artifact.aggregate.completed_cases, 0)

        for cr in artifact.cases:
            self.assertEqual(cr.status, "failed_execution")
            self.assertIsNotNone(cr.execution_error)
            self.assertIn("RuntimeError", cr.execution_error)


class TestRunPathCommand(unittest.TestCase):
    """Execute a full run through the command driver."""

    def test_full_run_with_command(self):
        workbook, workbook_path = _load_ready_workbook()

        config = WorkflowConfig(
            workflow_id="command-echo-test",
            llm_metadata=LLMMetadata(provider="test", model="echo-v1"),
            adapter=None,
            driver=CommandDriverConfig(
                command=[_PYTHON, _fixture("adapter_echo.py")],
            ),
        )

        config_path = _fixture("workflow_driver_command.json")

        artifact = execute_run(workbook, workbook_path, config, config_path)

        self.assertEqual(artifact.status, "completed")
        self.assertEqual(artifact.workflow_id, "command-echo-test")
        self.assertEqual(len(artifact.cases), 2)
        self.assertEqual(artifact.aggregate.completed_cases, 2)
        self.assertEqual(artifact.aggregate.failed_cases, 0)

        for cr in artifact.cases:
            self.assertEqual(cr.status, "completed")
            self.assertTrue(cr.raw_response.startswith("Echo: "))

    def test_full_run_with_failing_command(self):
        workbook, workbook_path = _load_ready_workbook()

        config = WorkflowConfig(
            workflow_id="fail-test",
            llm_metadata=LLMMetadata(provider="test", model="echo-v1"),
            adapter=None,
            driver=CommandDriverConfig(
                command=[_PYTHON, _fixture("adapter_fail.py")],
            ),
        )

        config_path = _fixture("workflow_driver_command.json")

        artifact = execute_run(workbook, workbook_path, config, config_path)

        self.assertEqual(artifact.status, "failed")
        for cr in artifact.cases:
            self.assertEqual(cr.status, "failed_execution")


class TestRunPathHttp(unittest.TestCase):
    """Execute a full run through the http driver via local test server."""

    def test_full_run_with_http(self):
        server, port = _start_test_server(_EchoHTTPHandler)
        try:
            workbook, workbook_path = _load_ready_workbook()

            config = WorkflowConfig(
                workflow_id="http-echo-test",
                llm_metadata=LLMMetadata(provider="test", model="echo-v1"),
                adapter=None,
                driver=HttpDriverConfig(
                    url=f"http://127.0.0.1:{port}/echo",
                    method="POST",
                    headers=None,
                    timeout_seconds=None,
                ),
            )

            config_path = _fixture("workflow_driver_http.json")

            artifact = execute_run(workbook, workbook_path, config, config_path)

            self.assertEqual(artifact.status, "completed")
            self.assertEqual(artifact.workflow_id, "http-echo-test")
            self.assertEqual(len(artifact.cases), 2)
            self.assertEqual(artifact.aggregate.completed_cases, 2)
            self.assertEqual(artifact.aggregate.failed_cases, 0)

            for cr in artifact.cases:
                self.assertEqual(cr.status, "completed")
                self.assertTrue(cr.raw_response.startswith("Echo: "))
                self.assertIsNotNone(cr.usage)
        finally:
            server.shutdown()
            server.server_close()

    def test_full_run_with_http_error(self):
        server, port = _start_test_server(_ErrorHTTPHandler)
        try:
            workbook, workbook_path = _load_ready_workbook()

            config = WorkflowConfig(
                workflow_id="http-error-test",
                llm_metadata=LLMMetadata(provider="test", model="echo-v1"),
                adapter=None,
                driver=HttpDriverConfig(
                    url=f"http://127.0.0.1:{port}/echo",
                    method="POST",
                    headers=None,
                    timeout_seconds=None,
                ),
            )

            config_path = _fixture("workflow_driver_http.json")

            artifact = execute_run(workbook, workbook_path, config, config_path)

            self.assertEqual(artifact.status, "failed")
            for cr in artifact.cases:
                self.assertEqual(cr.status, "failed_execution")
                self.assertIn("HTTP 500", cr.execution_error)
        finally:
            server.shutdown()
            server.server_close()


# ── Response Validation Through Drivers ─────────────────────────────────────


class TestDriverResponseValidation(unittest.TestCase):
    """Verify that the shared response validation catches bad driver output.

    The python-callable driver can return dicts that are missing required
    fields — the runner's shared validation must still catch these.
    """

    def test_missing_raw_response(self):
        """Driver returns a dict without raw_response — should fail validation."""
        workbook, workbook_path = _load_ready_workbook()

        # Create a temporary callable module that returns an incomplete response.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            dir=_FIXTURES,
        ) as f:
            f.write(
                "def handle(req):\n"
                "    return {'parsed_response': None, "
                "'usage': {'input_tokens': 0, 'output_tokens': 0}}\n"
            )
            f.flush()
            temp_module = os.path.basename(f.name)[:-3]  # Remove .py

        try:
            config = WorkflowConfig(
                workflow_id="bad-response-test",
                llm_metadata=LLMMetadata(provider="test", model="test"),
                adapter=None,
                driver=PythonCallableDriverConfig(
                    module=temp_module,
                    function="handle",
                ),
            )
            config_path = _fixture("workflow_driver_python_callable.json")

            artifact = execute_run(workbook, workbook_path, config, config_path)

            self.assertEqual(artifact.status, "failed")
            for cr in artifact.cases:
                self.assertEqual(cr.status, "failed_execution")
                self.assertIn("raw_response", cr.execution_error)
        finally:
            os.unlink(f.name)

    def test_negative_tokens(self):
        """Driver returns negative token counts — should fail validation."""
        workbook, workbook_path = _load_ready_workbook()

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            dir=_FIXTURES,
        ) as f:
            f.write(
                "def handle(req):\n"
                "    return {'raw_response': 'ok', 'parsed_response': None, "
                "'usage': {'input_tokens': -1, 'output_tokens': 0}}\n"
            )
            f.flush()
            temp_module = os.path.basename(f.name)[:-3]

        try:
            config = WorkflowConfig(
                workflow_id="neg-tokens-test",
                llm_metadata=LLMMetadata(provider="test", model="test"),
                adapter=None,
                driver=PythonCallableDriverConfig(
                    module=temp_module,
                    function="handle",
                ),
            )
            config_path = _fixture("workflow_driver_python_callable.json")

            artifact = execute_run(workbook, workbook_path, config, config_path)

            self.assertEqual(artifact.status, "failed")
            for cr in artifact.cases:
                self.assertEqual(cr.status, "failed_execution")
                self.assertIn("negative", cr.execution_error)
        finally:
            os.unlink(f.name)


# ── Legacy Adapter Still Works ──────────────────────────────────────────────


class TestLegacyAdapterStillWorks(unittest.TestCase):
    """Verify the legacy subprocess adapter path is not broken."""

    def test_legacy_run(self):
        workbook, workbook_path = _load_ready_workbook()
        config_path = _fixture("workflow_text_ok.json")
        config = load_workflow_config(config_path)

        artifact = execute_run(workbook, workbook_path, config, config_path)

        self.assertEqual(artifact.status, "completed")
        self.assertEqual(len(artifact.cases), 2)
        for cr in artifact.cases:
            self.assertEqual(cr.status, "completed")
            self.assertTrue(cr.raw_response.startswith("Echo: "))


if __name__ == "__main__":
    unittest.main()
