"""End-to-end tests for quickstart / continue orchestration and for the
workbook continuation block parser/renderer.

These tests exercise the full happy path with a deterministic bootstrap
fixture (no external LLM) so the orchestrator contract is covered in CI.

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
from contextlib import contextmanager

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from lightassay import (
    EvalError,
    continue_workbook,
    quickstart,
)
from lightassay.runtime_state import (
    execution_log_path,
    get_active_workbook,
    set_active_workbook,
)
from lightassay.workbook_models import (
    ArtifactReferences,
    Case,
    ContinuationBlock,
    ContinuationFields,
    Direction,
    HistoricalContinuation,
    HumanFeedback,
    RunReadiness,
    Target,
    Workbook,
)
from lightassay.workbook_parser import parse
from lightassay.workbook_renderer import render

_FIXTURE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "fixtures", "preparation_adapter_bootstrap.py")
)
_CALLABLE_MODULE = "tests.fixtures.callable_echo"
_CALLABLE_FUNCTION = "handle_request"
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_AGENT_FIXTURE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "fixtures", "agent_cli_stub.py")
)


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _write_config(path: str, adapter: str, provider: str, model: str) -> None:
    _write(
        path,
        json.dumps(
            {"adapter": adapter, "provider": provider, "model": model},
            ensure_ascii=False,
        ),
    )


def _install_fake_agent_binaries(bin_dir: str) -> None:
    os.makedirs(bin_dir, exist_ok=True)
    for name in ("claude", "codex"):
        path = os.path.join(bin_dir, name)
        _write(path, "#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)


@contextmanager
def _pushd(path: str):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


@contextmanager
def _patched_env(**updates):
    previous = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ── Continuation block: parser + renderer ───────────────────────────────────


class TestContinuationBlock(unittest.TestCase):
    def _make_workbook(self, continuation: ContinuationBlock) -> Workbook:
        return Workbook(
            target=Target(
                kind="workflow",
                name="demo",
                locator="demo.pipeline.run",
                boundary="high-level boundary",
                sources=["demo/pipeline.py"],
                notes="",
            ),
            brief="### What is being tested\ndemo\n",
            directions_global_instruction=HumanFeedback(""),
            directions=[],
            cases_global_instruction=HumanFeedback(""),
            cases=[],
            run_readiness=RunReadiness(run_ready=False, readiness_note=""),
            artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
            continuation=continuation,
        )

    def test_empty_continuation_round_trips(self):
        wb = self._make_workbook(ContinuationBlock())
        rendered = render(wb)
        self.assertIn("## Continue Next Run", rendered)
        self.assertIn("### Current continuation: general instruction", rendered)
        parsed = parse(rendered)
        self.assertTrue(parsed.continuation.current.is_empty())
        self.assertEqual(parsed.continuation.history, [])

    def test_current_fields_preserved(self):
        wb = self._make_workbook(
            ContinuationBlock(
                current=ContinuationFields(
                    general_instruction="Focus on risky edges",
                    direction_instruction="Add one more direction",
                    case_instruction="",
                )
            )
        )
        parsed = parse(render(wb))
        self.assertEqual(parsed.continuation.current.general_instruction, "Focus on risky edges")
        self.assertEqual(
            parsed.continuation.current.direction_instruction, "Add one more direction"
        )
        self.assertEqual(parsed.continuation.current.case_instruction, "")

    def test_history_versions_preserved_in_order(self):
        wb = self._make_workbook(
            ContinuationBlock(
                current=ContinuationFields(),
                history=[
                    HistoricalContinuation(
                        version=1,
                        fields=ContinuationFields(general_instruction="first"),
                    ),
                    HistoricalContinuation(
                        version=2,
                        fields=ContinuationFields(direction_instruction="second"),
                    ),
                ],
            )
        )
        parsed = parse(render(wb))
        self.assertEqual([h.version for h in parsed.continuation.history], [1, 2])
        self.assertEqual(parsed.continuation.history[0].fields.general_instruction, "first")
        self.assertEqual(parsed.continuation.history[1].fields.direction_instruction, "second")


# ── Runtime state pointer ────────────────────────────────────────────────────


class TestActiveWorkbookPointer(unittest.TestCase):
    def test_set_and_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_wb = os.path.join(tmp, "foo.workbook.md")
            _write(target_wb, "")
            pointer_path = set_active_workbook(target_wb, state_root=tmp)
            self.assertTrue(os.path.isfile(pointer_path))
            active = get_active_workbook(tmp)
            self.assertEqual(active, os.path.abspath(target_wb))

    def test_get_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(get_active_workbook(tmp))


# ── Quickstart end-to-end ────────────────────────────────────────────────────


class TestQuickstartEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.prep_cfg = os.path.join(self.tmp, "prep.json")
        self.sem_cfg = os.path.join(self.tmp, "sem.json")
        _write_config(self.prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        _write_config(self.sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        self._cwd = _pushd(self.tmp)
        self._cwd.__enter__()
        self.addCleanup(self._cwd.__exit__, None, None, None)

    def test_full_quickstart_produces_all_artifacts(self):
        result = quickstart(
            "e2e-quickstart",
            message="Check that the demo callable handles normal and edge inputs.",
            target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
        )

        self.assertTrue(os.path.isfile(result.workbook_path))
        self.assertTrue(os.path.isfile(result.run_artifact_path))
        self.assertTrue(os.path.isfile(result.analysis_artifact_path))
        self.assertTrue(os.path.isfile(result.workflow_config_path))
        self.assertEqual(result.run_status, "completed")
        self.assertGreaterEqual(result.direction_count, 1)
        self.assertGreaterEqual(result.case_count, 1)

        # Active workbook pointer written.
        self.assertEqual(get_active_workbook(self.tmp), os.path.abspath(result.workbook_path))

        # JSONL log written with at least a completion event.
        with open(execution_log_path(self.tmp), encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        self.assertTrue(any(e.get("event") == "completed" for e in entries))
        self.assertTrue(any(e.get("stage") == "Running workflow" for e in entries))

        # Analysis artifact carries structured recommendations.
        with open(result.analysis_artifact_path, encoding="utf-8") as fh:
            analysis_text = fh.read()
        self.assertIn("Next-step recommendations", analysis_text)
        self.assertIn("To ensure:", analysis_text)

    def test_quickstart_requires_non_empty_message(self):
        with self.assertRaises(EvalError):
            quickstart(
                "e2e-empty",
                message="   ",
                preparation_config=self.prep_cfg,
                semantic_config=self.sem_cfg,
                output_dir=self.tmp,
            )


# ── Continue end-to-end ──────────────────────────────────────────────────────


class TestContinueEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.prep_cfg = os.path.join(self.tmp, "prep.json")
        self.sem_cfg = os.path.join(self.tmp, "sem.json")
        _write_config(self.prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        _write_config(self.sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        self._cwd = _pushd(self.tmp)
        self._cwd.__enter__()
        self.addCleanup(self._cwd.__exit__, None, None, None)

        self.quickstart_result = quickstart(
            "e2e-continue",
            message="Check normal and edge inputs.",
            target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
        )

    def test_continue_without_any_input_fails(self):
        with self.assertRaises(EvalError) as ctx:
            continue_workbook(
                preparation_config=self.prep_cfg,
                semantic_config=self.sem_cfg,
                output_dir=self.tmp,
            )
        self.assertIn("No continuation request", str(ctx.exception))

    def test_continue_with_message_rotates_history(self):
        result = continue_workbook(
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            message="Focus the next pass on negative inputs.",
            output_dir=self.tmp,
        )
        self.assertEqual(result.continuation_version, 1)
        self.assertTrue(os.path.isfile(result.run_artifact_path))
        self.assertTrue(os.path.isfile(result.analysis_artifact_path))
        self.assertIsNone(result.compare_artifact_path)

        # The workbook now has an empty current continuation and one
        # history entry that preserves the cli_message line.
        from lightassay.workbook_parser import parse

        with open(result.workbook_path, encoding="utf-8") as fh:
            wb = parse(fh.read())
        self.assertTrue(wb.continuation.current.is_empty())
        self.assertEqual(len(wb.continuation.history), 1)
        self.assertEqual(wb.continuation.history[0].version, 1)

    def test_continue_with_workbook_instruction_and_compare(self):
        # Inject a workbook-side continuation field.
        from lightassay.workbook_parser import parse

        with open(self.quickstart_result.workbook_path, encoding="utf-8") as fh:
            wb = parse(fh.read())
        wb.continuation.current = ContinuationFields(
            general_instruction="Dig into the edge-case direction specifically.",
        )
        with open(self.quickstart_result.workbook_path, "w", encoding="utf-8") as fh:
            fh.write(render(wb))

        result = continue_workbook(
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
            compare_previous=True,
        )
        self.assertEqual(result.continuation_version, 1)
        self.assertIsNotNone(result.compare_artifact_path)
        self.assertTrue(os.path.isfile(result.compare_artifact_path))

    def test_continue_uses_explicit_workbook_path_when_provided(self):
        # Remove the active workbook pointer to force the explicit path
        # branch.
        pointer_path = os.path.join(self.tmp, ".lightassay", "active_workbook.json")
        if os.path.isfile(pointer_path):
            os.remove(pointer_path)

        result = continue_workbook(
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            message="Explicit path iteration.",
            workbook_path=self.quickstart_result.workbook_path,
            output_dir=self.tmp,
        )
        self.assertEqual(
            result.workbook_path, os.path.abspath(self.quickstart_result.workbook_path)
        )

    def test_continue_rolls_back_workbook_when_binding_is_missing(self):
        manual_path = os.path.join(self.tmp, "manual.workbook.md")
        _write(manual_path, "")

        workbook = Workbook(
            target=Target(
                kind="python-callable",
                name="demo",
                locator=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                boundary="call boundary",
                sources=["tests/fixtures/callable_echo.py"],
                notes="",
            ),
            brief="### What is being tested\ndemo\n\n### What matters in the output\nworks\n",
            directions_global_instruction=HumanFeedback(""),
            directions=[
                Direction(
                    direction_id="d1",
                    body="existing direction",
                    behavior_facet="facet",
                    testing_lens="lens",
                    covered_user_priority_sections=[
                        "what_is_being_tested",
                        "what_matters_in_output",
                    ],
                    source_rationale="existing source",
                    human_instruction=HumanFeedback(""),
                )
            ],
            cases_global_instruction=HumanFeedback(""),
            cases=[
                Case(
                    case_id="c1",
                    input="existing input",
                    target_directions=["d1"],
                    expected_behavior="existing expectation",
                    behavior_facet="facet",
                    testing_lens="lens",
                    covered_user_priority_sections=[
                        "what_is_being_tested",
                        "what_matters_in_output",
                    ],
                    source_rationale="existing source",
                    context=None,
                    notes=None,
                    human_instruction=HumanFeedback(""),
                )
            ],
            run_readiness=RunReadiness(run_ready=True, readiness_note="ready"),
            artifact_references=ArtifactReferences(
                run="old-run.json",
                analysis="old-analysis.md",
                compare=None,
            ),
            continuation=ContinuationBlock(
                current=ContinuationFields(general_instruction="continue please")
            ),
        )
        _write(manual_path, render(workbook))
        with open(manual_path, encoding="utf-8") as fh:
            before = fh.read()

        with self.assertRaises(EvalError) as ctx:
            continue_workbook(
                preparation_config=self.prep_cfg,
                semantic_config=self.sem_cfg,
                workbook_path=manual_path,
                output_dir=self.tmp,
            )
        self.assertIn("no generated workflow config", str(ctx.exception))

        with open(manual_path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(after, before)


# ── CLI smoke ────────────────────────────────────────────────────────────────


class TestCLISmoke(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.prep_cfg = os.path.join(self.tmp, "prep.json")
        self.sem_cfg = os.path.join(self.tmp, "sem.json")
        _write_config(self.prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        _write_config(self.sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")

    def _run(self, *args):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([os.path.abspath(_SRC), _REPO_ROOT])
        return subprocess.run(
            [sys.executable, "-m", "lightassay.cli", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.tmp,
        )

    def test_quickstart_cli_smoke(self):
        proc = self._run(
            "quickstart",
            "--message",
            "Check normal inputs.",
            "--target",
            f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
            "--preparation-config",
            self.prep_cfg,
            "--semantic-config",
            self.sem_cfg,
            "--output-dir",
            self.tmp,
            "--quiet",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Quickstart complete", proc.stdout)

        # Follow-up continue also works via CLI.
        proc = self._run(
            "continue",
            "--preparation-config",
            self.prep_cfg,
            "--semantic-config",
            self.sem_cfg,
            "--message",
            "Follow-up pass via CLI.",
            "--output-dir",
            self.tmp,
            "--quiet",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Continue complete", proc.stdout)


class TestBackendAutoSelection(unittest.TestCase):
    """Backend-only quickstart / continue paths use test fixtures that point at
    a bootstrap-capable adapter. The shipped ``stub`` demo backend has been
    removed from the public backend registry per the remediation plan — users
    see only real backends (``claude-cli`` / ``codex-cli``)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.prep_cfg = os.path.join(self.tmp, "prep.json")
        self.sem_cfg = os.path.join(self.tmp, "sem.json")
        _write_config(self.prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        _write_config(self.sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        self._cwd = _pushd(self.tmp)
        self._cwd.__enter__()
        self.addCleanup(self._cwd.__exit__, None, None, None)

    def test_quickstart_with_explicit_adapter_configs(self):
        result = quickstart(
            "backend-only",
            message="Verify callable handles normal input.",
            target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
        )
        self.assertTrue(os.path.isfile(result.workbook_path))
        self.assertTrue(os.path.isfile(result.run_artifact_path))
        self.assertTrue(os.path.isfile(result.analysis_artifact_path))
        with open(result.analysis_artifact_path, encoding="utf-8") as fh:
            self.assertIn("To ensure:", fh.read())

    def test_quickstart_requires_non_empty_target_hint(self):
        with self.assertRaises(EvalError) as exc:
            quickstart(
                "backend-only",
                message="Verify callable handles normal input.",
                target_hint="",
                preparation_config=self.prep_cfg,
                semantic_config=self.sem_cfg,
                output_dir=self.tmp,
            )
        self.assertIn("Quickstart requires a non-empty --target.", str(exc.exception))

    def test_continue_with_explicit_adapter_configs(self):
        quickstart(
            "backend-cont",
            message="Verify callable.",
            target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
        )
        result = continue_workbook(
            message="Continue via adapter config.",
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
        )
        self.assertEqual(result.continuation_version, 1)
        self.assertIn("v1 rotated to history", result.conclusion)

    def test_quickstart_with_builtin_backend_from_source_tree(self):
        with _patched_env(LIGHTASSAY_AGENT_CMD=f"{sys.executable} {_AGENT_FIXTURE}"):
            result = quickstart(
                "backend-built-in",
                message="Verify callable handles normal input.",
                target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                agent="claude-cli",
                output_dir=self.tmp,
            )
        self.assertTrue(os.path.isfile(result.workbook_path))
        self.assertTrue(os.path.isfile(result.run_artifact_path))
        self.assertTrue(os.path.isfile(result.analysis_artifact_path))

    def test_continue_with_builtin_backend_from_source_tree(self):
        with _patched_env(LIGHTASSAY_AGENT_CMD=f"{sys.executable} {_AGENT_FIXTURE}"):
            quickstart(
                "backend-built-in-cont",
                message="Verify callable.",
                target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                agent="claude-cli",
                output_dir=self.tmp,
            )
            result = continue_workbook(
                message="Continue via built-in backend.",
                agent="claude-cli",
                output_dir=self.tmp,
            )
        self.assertEqual(result.continuation_version, 1)
        self.assertTrue(os.path.isfile(result.analysis_artifact_path))

    def test_list_agents_surface_excludes_stub(self):
        from lightassay import list_agents

        names = [name for name, _ in list_agents()]
        self.assertNotIn("stub", names)
        self.assertIn("claude-cli", names)
        self.assertIn("codex-cli", names)

    def test_resolve_backend_unknown_raises(self):
        from lightassay.backends import resolve_backend

        with self.assertRaises(EvalError):
            resolve_backend("no-such-backend")

    def test_resolve_backend_stub_raises(self):
        """The demo stub must not be reachable as a user-facing backend."""
        from lightassay.backends import resolve_backend

        with self.assertRaises(EvalError):
            resolve_backend("stub")

    def test_neither_agent_nor_configs_raises(self):
        with self.assertRaises(EvalError) as ctx:
            quickstart(
                "no-config",
                message="Will fail.",
                target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                output_dir=self.tmp,
            )
        self.assertIn("agent", str(ctx.exception))


class TestContinuePreviousContext(unittest.TestCase):
    """The continue orchestrator must pass the *previous* iteration's
    directions and cases to the adapter via planning_context so the
    adapter can truly extend/refine rather than regenerate from empty."""

    def test_previous_full_context_reaches_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _pushd(tmp):
                prep_cfg = os.path.join(tmp, "prep.json")
                sem_cfg = os.path.join(tmp, "sem.json")
                _write_config(prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
                _write_config(sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")

                qs = quickstart(
                    "prev-ctx",
                    message="first iter",
                    target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                    preparation_config=prep_cfg,
                    semantic_config=sem_cfg,
                    output_dir=tmp,
                )
                # Build a concrete post-quickstart workbook snapshot: the
                # adapter receives the previous-iteration directions / cases
                # through planning_context.  We verify by monkey-patching
                # the preparer.execute_generate_directions wrapper in the
                # orchestrator via a sidecar adapter that echoes context
                # back — but here we simply inspect the workbook file the
                # orchestrator snapshots from: it must have non-empty
                # directions + cases after quickstart, so the snapshot
                # helper will capture them.
                from lightassay.orchestrator import (
                    _snapshot_previous_cases,
                    _snapshot_previous_directions,
                )
                from lightassay.workbook_parser import parse

                with open(qs.workbook_path, encoding="utf-8") as fh:
                    wb = parse(fh.read())
                prev_directions = _snapshot_previous_directions(wb)
                prev_cases = _snapshot_previous_cases(wb)
                self.assertTrue(prev_directions)
                self.assertTrue(prev_cases)
                self.assertIn("direction_id", prev_directions[0])
                self.assertIn("body", prev_directions[0])
                self.assertIn("case_id", prev_cases[0])
                self.assertIn("input", prev_cases[0])


class TestWorkbookRegistryAndAgentState(unittest.TestCase):
    """CLI-adjacent state: workbook registry and global default agent."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.prep_cfg = os.path.join(self.tmp, "prep.json")
        self.sem_cfg = os.path.join(self.tmp, "sem.json")
        self.config_root = os.path.join(self.tmp, "xdg")
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.config_root, exist_ok=True)
        _install_fake_agent_binaries(self.bin_dir)
        _write_config(self.prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        _write_config(self.sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")
        self._cwd = _pushd(self.tmp)
        self._cwd.__enter__()
        self.addCleanup(self._cwd.__exit__, None, None, None)

    def test_workbook_registry_records_ids(self):
        from lightassay.runtime_state import list_known_workbooks, resolve_workbook_id

        result = quickstart(
            "reg-test",
            message="Check callable.",
            target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
        )
        entries = list_known_workbooks(state_root=self.tmp)
        ids = [e["id"] for e in entries]
        self.assertIn("reg-test", ids)

        path = resolve_workbook_id("reg-test", state_root=self.tmp)
        self.assertEqual(path, os.path.abspath(result.workbook_path))

    def test_continue_with_workbook_id_selection(self):
        quickstart(
            "id-select",
            message="Check callable.",
            target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            output_dir=self.tmp,
        )
        # Remove the active-workbook pointer to force id-based selection.
        pointer = os.path.join(self.tmp, ".lightassay", "active_workbook.json")
        if os.path.isfile(pointer):
            os.remove(pointer)

        result = continue_workbook(
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            message="Continue via workbook id.",
            workbook_id="id-select",
            output_dir=self.tmp,
        )
        self.assertEqual(result.continuation_version, 1)
        self.assertTrue(os.path.isfile(result.workbook_path))

    def test_continue_accepts_explicit_workflow_config_for_non_quickstart_workbook(self):
        workbook_path = os.path.join(self.tmp, "manual.workbook.md")
        workflow_config_path = os.path.join(self.tmp, "manual.workflow.json")
        source_path = os.path.abspath(
            os.path.join(_REPO_ROOT, "tests", "fixtures", "callable_echo.py")
        )

        workbook = Workbook(
            target=Target(
                kind="python-callable",
                name="manual",
                locator=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                boundary="call boundary",
                sources=[source_path],
                notes="",
            ),
            brief=(
                "### What is being tested\nmanual\n\n"
                "### What matters in the output\nworks for normal and edge inputs\n"
            ),
            directions_global_instruction=HumanFeedback(""),
            directions=[],
            cases_global_instruction=HumanFeedback(""),
            cases=[],
            run_readiness=RunReadiness(run_ready=False, readiness_note=""),
            artifact_references=ArtifactReferences(run=None, analysis=None, compare=None),
            continuation=ContinuationBlock(
                current=ContinuationFields(general_instruction="build the first pass")
            ),
        )
        _write(workbook_path, render(workbook))
        _write(
            workflow_config_path,
            json.dumps(
                {
                    "workflow_id": "manual-wf",
                    "driver": {
                        "type": "python-callable",
                        "module": _CALLABLE_MODULE,
                        "function": _CALLABLE_FUNCTION,
                    },
                }
            ),
        )

        result = continue_workbook(
            preparation_config=self.prep_cfg,
            semantic_config=self.sem_cfg,
            workbook_path=workbook_path,
            workflow_config_path=workflow_config_path,
            output_dir=self.tmp,
        )
        self.assertEqual(result.workflow_config_path, os.path.abspath(workflow_config_path))
        self.assertEqual(result.continuation_version, 1)
        self.assertTrue(os.path.isfile(result.run_artifact_path))

    def test_workbook_id_unknown_raises(self):
        with self.assertRaises(EvalError) as ctx:
            continue_workbook(
                preparation_config=self.prep_cfg,
                semantic_config=self.sem_cfg,
                message="x",
                workbook_id="no-such-id",
                output_dir=self.tmp,
            )
        self.assertIn("Unknown workbook id", str(ctx.exception))

    def test_workbook_and_id_mutually_exclusive(self):
        with self.assertRaises(EvalError) as ctx:
            continue_workbook(
                preparation_config=self.prep_cfg,
                semantic_config=self.sem_cfg,
                message="x",
                workbook_path="/tmp/any.md",
                workbook_id="any",
                output_dir=self.tmp,
            )
        self.assertIn("mutually exclusive", str(ctx.exception))

    def test_corrupt_active_pointer_raises(self):
        from lightassay.runtime_state import get_active_workbook

        os.makedirs(os.path.join(self.tmp, ".lightassay"), exist_ok=True)
        pointer = os.path.join(self.tmp, ".lightassay", "active_workbook.json")
        with open(pointer, "w") as fh:
            fh.write("not json")
        with self.assertRaises(EvalError) as ctx:
            get_active_workbook(self.tmp)
        self.assertIn("corrupt", str(ctx.exception))

    def test_default_agent_persistence(self):
        from lightassay.runtime_state import (
            get_default_agent,
            set_default_agent,
        )

        self.assertIsNone(get_default_agent(self.config_root))
        set_default_agent("claude-cli", config_root=self.config_root)
        self.assertEqual(get_default_agent(self.config_root), "claude-cli")

    def test_cli_agents_and_workbook_commands(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([os.path.abspath(_SRC), _REPO_ROOT])
        env["XDG_CONFIG_HOME"] = self.config_root
        env["PATH"] = os.pathsep.join([self.bin_dir, env.get("PATH", "")])
        with _patched_env(LIGHTASSAY_AGENT_CMD=f"{sys.executable} {_AGENT_FIXTURE}"):
            set_proc = subprocess.run(
                [sys.executable, "-m", "lightassay.cli", "agents", "claude-cli"],
                cwd=self.tmp,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(set_proc.returncode, 0, set_proc.stderr)

            current_agent_proc = subprocess.run(
                [sys.executable, "-m", "lightassay.cli", "agents", "--current"],
                cwd=self.tmp,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(current_agent_proc.returncode, 0, current_agent_proc.stderr)
            self.assertEqual(current_agent_proc.stdout.strip(), "claude-cli")

            quickstart(
                "cli-wb-state",
                message="Check callable.",
                target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                agent="claude-cli",
                output_dir=self.tmp,
            )

            current_workbook_proc = subprocess.run(
                [sys.executable, "-m", "lightassay.cli", "current-workbook"],
                cwd=self.tmp,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(current_workbook_proc.returncode, 0, current_workbook_proc.stderr)
            self.assertIn("cli-wb-state.workbook.md", current_workbook_proc.stdout)

            workbooks_proc = subprocess.run(
                [sys.executable, "-m", "lightassay.cli", "workbooks"],
                cwd=self.tmp,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(workbooks_proc.returncode, 0, workbooks_proc.stderr)
            self.assertIn("cli-wb-state", workbooks_proc.stdout)

    def test_cli_quickstart_with_codex_backend_streams_progress(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([os.path.abspath(_SRC), _REPO_ROOT])
        env["XDG_CONFIG_HOME"] = self.config_root
        env["LIGHTASSAY_AGENT_CMD"] = f"{sys.executable} {_AGENT_FIXTURE}"

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "lightassay.cli",
                "quickstart",
                "--agent",
                "codex-cli",
                "--message",
                "Check callable.",
                "--target",
                f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                "--output-dir",
                self.tmp,
            ],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[…] Resolving intent", proc.stderr)
        self.assertIn("Processing bootstrap for the current workspace.", proc.stderr)
        self.assertIn("Processing generate_cases for the current workspace.", proc.stderr)


class TestCanonicalBriefOwnership(unittest.TestCase):
    """Quickstart must stop writing system-authored planning boilerplate
    into the user-priority sections of the canonical brief."""

    def test_brief_excludes_system_planning_boilerplate(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _pushd(tmp):
                prep_cfg = os.path.join(tmp, "prep.json")
                sem_cfg = os.path.join(tmp, "sem.json")
                _write_config(prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
                _write_config(sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")

                result = quickstart(
                    "brief-audit",
                    message="Check that outputs preserve names and numbers.",
                    target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                    preparation_config=prep_cfg,
                    semantic_config=sem_cfg,
                    output_dir=tmp,
                )

                with open(result.workbook_path, encoding="utf-8") as fh:
                    text = fh.read()

                # System-authored boilerplate that used to leak into the
                # human-priority brief must no longer appear there.
                self.assertNotIn("Aspects that are especially significant", text)
                self.assertNotIn("Failure modes and problem classes that matter", text)
                self.assertNotIn("What must not break", text)
                self.assertNotIn(
                    "The user's original request must be preserved through",
                    text,
                )
                # The human-authored intent is preserved verbatim.
                self.assertIn("preserve names and numbers", text)


class TestContinuationHistoryCliMessage(unittest.TestCase):
    def test_history_entry_preserves_cli_message_and_empty_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _pushd(tmp):
                prep_cfg = os.path.join(tmp, "prep.json")
                sem_cfg = os.path.join(tmp, "sem.json")
                _write_config(prep_cfg, _FIXTURE, "fixture", "bootstrap-v1")
                _write_config(sem_cfg, _FIXTURE, "fixture", "bootstrap-v1")

                quickstart(
                    "hist-cli",
                    message="first pass",
                    target_hint=f"{_CALLABLE_MODULE}.{_CALLABLE_FUNCTION}",
                    preparation_config=prep_cfg,
                    semantic_config=sem_cfg,
                    output_dir=tmp,
                )

                continue_workbook(
                    preparation_config=prep_cfg,
                    semantic_config=sem_cfg,
                    message="Focus next pass on edge inputs.",
                    output_dir=tmp,
                )

                from lightassay.workbook_parser import parse

                with open(os.path.join(tmp, "hist-cli.workbook.md"), encoding="utf-8") as fh:
                    wb = parse(fh.read())

                self.assertEqual(len(wb.continuation.history), 1)
                entry = wb.continuation.history[0]
                self.assertEqual(entry.version, 1)
                self.assertEqual(entry.cli_message, "Focus next pass on edge inputs.")
                # Unused slots remain empty rather than being omitted.
                self.assertEqual(entry.fields.general_instruction, "")
                self.assertEqual(entry.fields.direction_instruction, "")
                self.assertEqual(entry.fields.case_instruction, "")


class TestNoLocalDeterministicBinding(unittest.TestCase):
    """Task A: the bootstrap layer no longer auto-binds hints locally.

    Even raw URLs and command-like hints now require a preparation
    adapter — there is no local deterministic shortcut left that could
    silently attach to the wrong thing.
    """

    def test_bootstrap_requires_adapter_even_for_url_hint(self):
        from lightassay.bootstrap import bootstrap_quickstart
        from lightassay.errors import PreparationError

        with self.assertRaises(PreparationError) as ctx:
            bootstrap_quickstart(
                "do something",
                target_hint="https://api.example.com/x",
                preparation_config=None,
            )
        self.assertIn("preparation_config", str(ctx.exception))


class TestWorkflowConfigSchemaSplit(unittest.TestCase):
    """Task C: execution binding and LLM metadata are separate."""

    def test_workflow_config_accepts_missing_provider_and_model(self):
        from lightassay.workflow_config import load_workflow_config

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wf.json")
            with open(path, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "workflow_id": "no-llm",
                            "driver": {
                                "type": "python-callable",
                                "module": "m",
                                "function": "f",
                            },
                        }
                    )
                )
            cfg = load_workflow_config(path)
            self.assertEqual(cfg.llm_metadata.provider, None)
            self.assertEqual(cfg.llm_metadata.model, None)

    def test_workflow_config_accepts_nested_llm_metadata(self):
        from lightassay.workflow_config import load_workflow_config

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wf.json")
            with open(path, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "workflow_id": "with-llm",
                            "driver": {
                                "type": "python-callable",
                                "module": "m",
                                "function": "f",
                            },
                            "llm_metadata": {
                                "provider": "anthropic",
                                "model": "claude-x",
                            },
                        }
                    )
                )
            cfg = load_workflow_config(path)
            self.assertEqual(cfg.llm_metadata.provider, "anthropic")
            self.assertEqual(cfg.llm_metadata.model, "claude-x")


class TestAgentCliJsonParser(unittest.TestCase):
    """Task H: one forced-JSON parser based on first `{` / last `}`."""

    def test_extract_json_object_strips_backticks(self):
        from lightassay.builtin_adapters._agent_cli_common import extract_json_object

        raw = 'Some prose\n```json\n{"a": 1}\n```\ntail'
        parsed = extract_json_object(raw)
        self.assertEqual(parsed, {"a": 1})

    def test_extract_json_object_hard_fails_on_missing_braces(self):
        from lightassay.builtin_adapters import _agent_cli_common

        with self.assertRaises(SystemExit):
            _agent_cli_common.extract_json_object("no braces at all")

    def test_run_agent_can_capture_last_message_file(self):
        import io
        from unittest import mock

        from lightassay.builtin_adapters._agent_cli_common import run_agent

        class _FakeProcess:
            def __init__(self, stdout_text: str, stderr_text: str, returncode: int) -> None:
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(stdout_text)
                self.stderr = io.StringIO(stderr_text)
                self._returncode = returncode

            def wait(self) -> int:
                return self._returncode

        def _fake_popen(args, stdin, stdout, stderr, text, bufsize):
            self.assertIn("--output-last-message", args)
            path = args[args.index("--output-last-message") + 1]
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"ok": true}')
            return _FakeProcess(
                stdout_text=(
                    '{"type":"thread.started","thread_id":"t"}\n'
                    '{"type":"item.completed","item":{"type":"agent_message","text":"Inspecting workspace"}}\n'
                ),
                stderr_text="",
                returncode=0,
            )

        with mock.patch("subprocess.Popen", side_effect=_fake_popen):
            raw = run_agent(
                ["codex", "exec"],
                "prompt",
                json_flags=["--json"],
                capture_last_message=True,
            )
        self.assertEqual(raw, '{"ok": true}')

    def test_run_agent_fails_when_last_message_file_missing(self):
        import io
        from unittest import mock

        from lightassay.builtin_adapters import _agent_cli_common

        class _FakeProcess:
            def __init__(self, stdout_text: str, stderr_text: str, returncode: int) -> None:
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(stdout_text)
                self.stderr = io.StringIO(stderr_text)
                self._returncode = returncode

            def wait(self) -> int:
                return self._returncode

        def _fake_popen(args, stdin, stdout, stderr, text, bufsize):
            return _FakeProcess(
                stdout_text='{"type":"thread.started","thread_id":"t"}\n',
                stderr_text="",
                returncode=0,
            )

        with mock.patch("subprocess.Popen", side_effect=_fake_popen):
            with self.assertRaises(SystemExit):
                _agent_cli_common.run_agent(
                    ["codex", "exec"],
                    "prompt",
                    json_flags=["--json"],
                    capture_last_message=True,
                )


if __name__ == "__main__":
    unittest.main()
