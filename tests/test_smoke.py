"""Smoke tests for lightassay.

These tests verify that the package is importable, the version is present,
and the CLI responds correctly to basic invocations with the full command surface.

Run with:
    PYTHONPATH=src python3 -m unittest discover -s tests
"""

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


class TestPackageImport(unittest.TestCase):
    """Verify the package and its public surface are importable."""

    def test_package_importable(self):
        mod = importlib.import_module("lightassay")
        self.assertIsNotNone(mod)

    def test_version_present(self):
        from lightassay import __version__

        self.assertIsInstance(__version__, str)
        self.assertGreater(len(__version__), 0)

    def test_cli_module_importable(self):
        mod = importlib.import_module("lightassay.cli")
        self.assertIsNotNone(mod)

    def test_main_callable(self):
        from lightassay.cli import main

        self.assertTrue(callable(main))

    def test_build_parser_callable(self):
        from lightassay.cli import build_parser

        self.assertTrue(callable(build_parser))

    def test_build_parser_returns_parser(self):
        import argparse

        from lightassay.cli import build_parser

        parser = build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)


class TestCLIBehavior(unittest.TestCase):
    """Verify CLI subprocess invocations behave as specified."""

    def _run_cli(self, args):
        env = dict(os.environ)
        # Ensure PYTHONPATH includes src/ relative to this test file.
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
        src_dir = os.path.abspath(src_dir)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
        return subprocess.run(
            [sys.executable, "-m", "lightassay.cli"] + args,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_no_args_exits_zero(self):
        result = self._run_cli([])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_no_args_prints_help_content(self):
        result = self._run_cli([])
        combined = result.stdout + result.stderr
        self.assertIn("lightassay", combined)

    def test_help_flag_exits_zero(self):
        result = self._run_cli(["--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_help_flag_mentions_commands(self):
        result = self._run_cli(["--help"])
        combined = result.stdout + result.stderr
        for cmd in (
            "init",
            "agents",
            "workbook",
            "quick-try",
            "refine-suite",
            "explore-workbook",
            "run",
            "analyze",
            "compare",
            "prepare-directions",
            "prepare-cases",
            "prepare-readiness",
        ):
            self.assertIn(cmd, combined, msg=f"Expected '{cmd}' in help output")

    def test_version_flag(self):
        result = self._run_cli(["--version"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        combined = result.stdout + result.stderr
        self.assertIn("0.3.2", combined)

    def test_unknown_command_exits_nonzero(self):
        result = self._run_cli(["nonexistent-command"])
        self.assertNotEqual(result.returncode, 0)

    def test_subcommand_init_help(self):
        result = self._run_cli(["init", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("agent", result.stdout + result.stderr)

    def test_subcommand_agents_help(self):
        result = self._run_cli(["agents", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("agent", result.stdout + result.stderr)

    def test_subcommand_workbook_help(self):
        result = self._run_cli(["workbook", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("workbook", result.stdout + result.stderr)

    def test_subcommand_run_help(self):
        result = self._run_cli(["run", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_subcommand_quick_try_help(self):
        result = self._run_cli(["quick-try", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_subcommand_refine_suite_help(self):
        result = self._run_cli(["refine-suite", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_subcommand_explore_workbook_help(self):
        result = self._run_cli(["explore-workbook", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_subcommand_analyze_help(self):
        result = self._run_cli(["analyze", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_subcommand_compare_help(self):
        result = self._run_cli(["compare", "--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class TestMainDirectCall(unittest.TestCase):
    """Verify main() returns correct exit codes when called directly."""

    def test_main_no_args_returns_zero(self):
        from lightassay.cli import main

        result = main([])
        self.assertEqual(result, 0)

    def test_main_help_returns_zero(self):
        import io
        from contextlib import redirect_stdout

        from lightassay.cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                result = main(["--help"])
            except SystemExit as e:
                result = e.code
        self.assertEqual(result, 0)

    def test_main_workbook_creates_workbook(self):
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["workbook", "--output-dir", tmpdir])
            self.assertEqual(result, 0)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "workbook1.workbook.md")))

    def test_main_init_non_tty_returns_two(self):
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        buf = io.StringIO()
        with redirect_stderr(buf), mock.patch("lightassay.cli._stdin_is_tty", return_value=False):
            result = main(["init"])
        self.assertEqual(result, 2)
        self.assertIn("cannot run interactive onboarding", buf.getvalue())

    def test_main_init_interactively_saves_agent(self):
        import io
        from contextlib import redirect_stdout

        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as config_root:
            buf = io.StringIO()
            with (
                redirect_stdout(buf),
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_root}, clear=False),
                mock.patch("lightassay.cli._stdin_is_tty", return_value=True),
                mock.patch("builtins.input", side_effect=["1"]),
                mock.patch("lightassay.cli.shutil.which", return_value="/tmp/claude"),
            ):
                result = main(["init"])
            self.assertEqual(result, 0)
            agent_path = os.path.join(config_root, "lightassay", "agent.json")
            self.assertTrue(os.path.isfile(agent_path))
            with open(agent_path, encoding="utf-8") as fh:
                self.assertIn('"agent": "claude-cli"', fh.read())

    def test_main_quickstart_without_init_prompts_init_before_message_error(self):
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as config_root:
            buf = io.StringIO()
            with (
                redirect_stderr(buf),
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_root}, clear=False),
            ):
                result = main(["quickstart"])
            self.assertEqual(result, 2)
            text = buf.getvalue()
            self.assertIn("No agent configured. Run `lightassay init` first.", text)
            self.assertNotIn("the following arguments are required: --message", text)
            self.assertNotIn("lightassay agents", text)

    def test_main_quickstart_requires_message_and_target_after_agent_exists(self):
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as config_root:
            buf = io.StringIO()
            with (
                redirect_stderr(buf),
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_root}, clear=False),
                mock.patch("lightassay.cli.shutil.which", return_value="/tmp/claude"),
            ):
                main(["agents", "claude-cli"])
                result = main(["quickstart"])
            self.assertEqual(result, 2)
            text = buf.getvalue()
            self.assertIn(
                "lightassay quickstart: error: the following arguments are required: --message, --target",
                text,
            )

    def test_main_quickstart_requires_target_after_message_when_agent_exists(self):
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as config_root:
            buf = io.StringIO()
            with (
                redirect_stderr(buf),
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_root}, clear=False),
                mock.patch("lightassay.cli.shutil.which", return_value="/tmp/claude"),
            ):
                main(["agents", "claude-cli"])
                result = main(["quickstart", "--message", "check this"])
            self.assertEqual(result, 2)
            self.assertIn(
                "lightassay quickstart: error: the following arguments are required: --target",
                buf.getvalue(),
            )

    def test_main_quickstart_requires_message_after_target_when_agent_exists(self):
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as config_root:
            buf = io.StringIO()
            with (
                redirect_stderr(buf),
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_root}, clear=False),
                mock.patch("lightassay.cli.shutil.which", return_value="/tmp/claude"),
            ):
                main(["agents", "claude-cli"])
                result = main(["quickstart", "--target", "myapp.pipeline.run"])
            self.assertEqual(result, 2)
            self.assertIn(
                "lightassay quickstart: error: the following arguments are required: --message",
                buf.getvalue(),
            )

    def test_main_quickstart_missing_message_value_still_mentions_missing_target(self):
        import io
        from contextlib import redirect_stderr

        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as config_root:
            buf = io.StringIO()
            with (
                redirect_stderr(buf),
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_root}, clear=False),
                mock.patch("lightassay.cli.shutil.which", return_value="/tmp/claude"),
            ):
                main(["agents", "claude-cli"])
                result = main(["quickstart", "--message"])
            self.assertEqual(result, 2)
            self.assertIn(
                "lightassay quickstart: error: the following arguments are required: --message, --target",
                buf.getvalue(),
            )

    def test_main_agents_respects_lightassay_agent_cmd_override(self):
        from lightassay.cli import main

        with tempfile.TemporaryDirectory() as config_root:
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "XDG_CONFIG_HOME": config_root,
                        "LIGHTASSAY_AGENT_CMD": "/bin/true",
                    },
                    clear=False,
                ),
                mock.patch("lightassay.cli.shutil.which", side_effect=lambda cmd: cmd),
            ):
                result = main(["agents", "claude-cli"])
            self.assertEqual(result, 0)

    def test_main_init_repeat_can_leave_agent_unchanged(self):
        from lightassay.cli import main

        with (
            mock.patch("lightassay.cli._stdin_is_tty", return_value=True),
            mock.patch("lightassay.cli.current_agent", return_value="claude-cli"),
            mock.patch("builtins.input", side_effect=[""]),
        ):
            result = main(["init"])
        self.assertEqual(result, 0)

    def test_main_quick_try_creates_workbook(self):
        from lightassay.cli import main

        fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
        prep_config = os.path.join(fixtures, "preparation_ok.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(
                [
                    "quick-try",
                    "quick-smoke",
                    "--target-kind",
                    "workflow",
                    "--target-name",
                    "text_echo",
                    "--target-locator",
                    "tests.fixtures.adapter_echo",
                    "--target-boundary",
                    "text echo workflow boundary",
                    "--target-source",
                    "tests/fixtures/adapter_echo.py",
                    "--user-request",
                    "Покажи быстрый демонстрационный прогон.",
                    "--preparation-config",
                    prep_config,
                    "--output-dir",
                    tmpdir,
                ]
            )
            self.assertEqual(result, 0)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "quick-smoke.workbook.md")))

    def test_main_quick_try_workbook_bootstraps_existing_workbook(self):
        from lightassay.cli import main
        from lightassay.workbook_parser import parse
        from lightassay.workbook_renderer import render

        fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
        prep_config = os.path.join(fixtures, "preparation_ok.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            workbook_path = os.path.join(tmpdir, "seed.workbook.md")
            rc = main(["workbook", "--output-dir", tmpdir])
            self.assertEqual(rc, 0)
            workbook_path = os.path.join(tmpdir, "workbook1.workbook.md")

            with open(workbook_path, encoding="utf-8") as fh:
                workbook = parse(fh.read())
            workbook.target.kind = "workflow"
            workbook.target.name = "text_echo"
            workbook.target.locator = "tests.fixtures.adapter_echo"
            workbook.target.boundary = "text echo workflow boundary"
            workbook.target.sources = ["tests/fixtures/adapter_echo.py"]
            with open(workbook_path, "w", encoding="utf-8") as fh:
                fh.write(render(workbook))

            result = main(
                [
                    "quick-try",
                    "--workbook",
                    workbook_path,
                    "--user-request",
                    "Покажи быстрый прогон по уже выбранному target.",
                    "--preparation-config",
                    prep_config,
                ]
            )
            self.assertEqual(result, 0)
            self.assertTrue(os.path.isfile(workbook_path))

    def test_main_refine_suite_creates_workbook(self):
        from lightassay.cli import main

        fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
        source_workbook = os.path.join(fixtures, "ready_demo.workbook.md")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(
                [
                    "refine-suite",
                    source_workbook,
                    "refined-smoke",
                    "--refinement-request",
                    "Добавь негативные кейсы.",
                    "--output-dir",
                    tmpdir,
                ]
            )
            self.assertEqual(result, 0)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "refined-smoke.workbook.md")))

    def test_main_explore_workbook_creates_workbook(self):
        from lightassay.cli import main
        from lightassay.run_artifact_io import save_run_artifact
        from lightassay.run_models import (
            Aggregate,
            CaseRecord,
            CaseUsage,
            RunArtifact,
        )

        fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
        source_workbook = os.path.join(fixtures, "ready_demo.workbook.md")
        prep_config = os.path.join(fixtures, "preparation_ok.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            run_artifact_path = os.path.join(tmpdir, "seed_run.json")
            save_run_artifact(
                RunArtifact(
                    run_id="smoke_seed",
                    workflow_id="echo-wf",
                    workbook_path=source_workbook,
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
                            case_id="seed-case",
                            input="hello",
                            context=None,
                            expected_behavior="Echo hello",
                            raw_response=None,
                            parsed_response=None,
                            duration_ms=10,
                            usage=CaseUsage(input_tokens=1, output_tokens=2),
                            status="failed_execution",
                            execution_error="Adapter exited with code 1",
                        ),
                    ],
                    aggregate=Aggregate(
                        total_cases=1,
                        completed_cases=0,
                        failed_cases=1,
                        total_duration_ms=10,
                        total_input_tokens=1,
                        total_output_tokens=2,
                    ),
                ),
                run_artifact_path,
            )

            result = main(
                [
                    "explore-workbook",
                    source_workbook,
                    run_artifact_path,
                    "explore-smoke",
                    "--exploration-goal",
                    "Ищи follow-up кейсы по сбоям.",
                    "--preparation-config",
                    prep_config,
                    "--workflow-config",
                    os.path.join(fixtures, "workflow_config_echo.json"),
                    "--max-cases",
                    "1",
                    "--max-iterations",
                    "2",
                    "--output-dir",
                    tmpdir,
                ]
            )
            self.assertEqual(result, 0)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "explore-smoke.workbook.md")))
            iter_artifacts = [
                f
                for f in os.listdir(tmpdir)
                if f.startswith("explore_iter_") and f.endswith(".json")
            ]
            self.assertEqual(len(iter_artifacts), 2)


class TestInstalledPackage(unittest.TestCase):
    """End-to-end smoke test: build a wheel, install it into a fresh venv,
    and verify that the CLI entrypoint and import path work from the
    installed distribution (not just the source tree).

    Skipped only when `python -m build` is not installed locally. Build or
    install failures inside the test are treated as real regressions, not
    environment flakiness: the wheel is built from the local source tree
    and installed with ``--no-index``, so no network is required.
    """

    def _run(self, cmd, **kwargs):
        # Scrub PYTHONPATH so the freshly-created venv does not inherit the
        # source tree's import path — otherwise pip sees lightassay as already
        # installed and skips the wheel install.
        env = kwargs.pop("env", None)
        if env is None:
            env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return subprocess.run(cmd, capture_output=True, text=True, env=env, **kwargs)

    def test_wheel_install_cli_and_import(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        with tempfile.TemporaryDirectory() as tmp:
            # Probe whether `python -m build` is actually runnable. Done as a
            # subprocess with cwd outside the repo because a local build/
            # directory (left by a prior `python -m build` run) shadows the
            # real `build` package: a bare `import build` would succeed, but
            # `python -m build` would die with "No module named
            # build.__main__". Probing from a neutral cwd gives a truthful
            # answer.
            probe = self._run(
                [sys.executable, "-m", "build", "--help"],
                cwd=tmp,
            )
            if probe.returncode != 0:
                self.skipTest(
                    "python -m build not usable in this environment: "
                    + (probe.stderr or probe.stdout)[-500:]
                )

            dist_dir = os.path.join(tmp, "dist")
            os.makedirs(dist_dir, exist_ok=True)
            # Also run the actual build with cwd=tmp so module resolution
            # does not pick up the repo-local build/ directory.
            build_result = self._run(
                [sys.executable, "-m", "build", "--wheel", "--outdir", dist_dir, repo_root],
                cwd=tmp,
            )
            self.assertEqual(
                build_result.returncode,
                0,
                msg=(
                    "wheel build failed:\n"
                    f"stdout: {build_result.stdout}\n"
                    f"stderr: {build_result.stderr}"
                ),
            )

            wheels = [f for f in os.listdir(dist_dir) if f.endswith(".whl")]
            self.assertEqual(len(wheels), 1, f"expected exactly one wheel, got {wheels}")
            wheel_path = os.path.join(dist_dir, wheels[0])

            venv_dir = os.path.join(tmp, "venv")
            venv_result = self._run([sys.executable, "-m", "venv", venv_dir])
            self.assertEqual(venv_result.returncode, 0, msg=venv_result.stderr)

            if os.name == "nt":
                venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
                venv_bin_cli = os.path.join(venv_dir, "Scripts", "lightassay.exe")
            else:
                venv_python = os.path.join(venv_dir, "bin", "python")
                venv_bin_cli = os.path.join(venv_dir, "bin", "lightassay")

            install_result = self._run(
                [venv_python, "-m", "pip", "install", "--no-index", wheel_path]
            )
            self.assertEqual(
                install_result.returncode,
                0,
                msg=(
                    "wheel install failed:\n"
                    f"stdout: {install_result.stdout}\n"
                    f"stderr: {install_result.stderr}"
                ),
            )

            import_result = self._run(
                [
                    venv_python,
                    "-c",
                    "import lightassay; print(lightassay.__version__)",
                ]
            )
            self.assertEqual(import_result.returncode, 0, msg=import_result.stderr)
            self.assertIn("0.3.2", import_result.stdout)

            venv_bin_dir = os.path.dirname(venv_bin_cli)
            self.assertTrue(
                os.path.isfile(venv_bin_cli),
                f"installed CLI not found at {venv_bin_cli}. "
                f"install stdout: {install_result.stdout!r} "
                f"install stderr: {install_result.stderr!r} "
                f"bin dir contents: {os.listdir(venv_bin_dir) if os.path.isdir(venv_bin_dir) else 'MISSING'}",
            )
            cli_result = self._run([venv_bin_cli, "--help"])
            self.assertEqual(cli_result.returncode, 0, msg=cli_result.stderr)
            self.assertIn("lightassay", cli_result.stdout + cli_result.stderr)

            version_result = self._run([venv_bin_cli, "--version"])
            self.assertEqual(version_result.returncode, 0, msg=version_result.stderr)
            self.assertIn("0.3.2", version_result.stdout + version_result.stderr)


if __name__ == "__main__":
    unittest.main()
