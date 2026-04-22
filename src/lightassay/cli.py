"""CLI entrypoint for lightassay.

CLI is a secondary human/debug/admin surface.  All command handlers
route through the shared L1 library primitives (``init_workbook``,
``open_session``, ``compare_runs``, ``EvalSession`` methods) rather
than calling lower-level engine modules directly.

``compare`` routes through ``compare_runs()`` directly (no
session/workbook required) because compare semantically operates
across runs from potentially different workbooks.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys

from . import __version__
from .errors import EvalError
from .surface import (
    agent_cli_requirement,
    compare_runs,
    continue_workbook,
    current_agent,
    current_workbook,
    explore_workbook,
    init_workbook,
    known_workbooks,
    open_session,
    quick_try,
    quick_try_workbook,
    quickstart,
    refine_workbook,
    set_agent,
)
from .surface import (
    list_agents as _list_agents,
)
from .types import EvalTarget, PreparationStage

_QUICKSTART_USAGE = (
    "lightassay quickstart [-h] --message MESSAGE --target TARGET_HINT "
    "[--agent AGENT] [--full-intent] "
    "[--preparation-config PREPARATION_CONFIG] "
    "[--semantic-config SEMANTIC_CONFIG] [--output-dir OUTPUT_DIR] [--quiet]"
)
_EXECUTION_BINDING_STAGE = "Execution binding"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lightassay",
        description=(
            "File-based orchestrator for structured evaluation of applied LLM workflows.\n"
            "Humans declare intent, LLMs do the semantic reasoning, code orchestrates\n"
            "execution and measures raw facts — and never judges output quality.\n\n"
            "Flow: target -> sources -> intention -> directions -> cases -> run -> "
            "analysis -> compare\n\n"
            "The tool guides you through the flow:\n"
            "  1. You define a target and fill a guided brief in a workbook (markdown).\n"
            "  2. An LLM builds directions from the target, bounded source context, "
            "and your brief.\n"
            "  3. You give feedback; LLM builds cases.\n"
            "  4. You give feedback; LLM reconciles the workbook and sets RUN_READY.\n"
            "  5. After a valid execution binding exists, the tool runs your workflow\n"
            "     against approved cases and saves a run artifact (JSON).\n"
            "  6. An LLM produces a semantic analysis artifact (markdown).\n"
            "  7. Optionally, an LLM compares multiple run artifacts (markdown)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # init
    subparsers.add_parser(
        "init",
        help="Interactive onboarding: pick the default agent",
        description=(
            "Interactive onboarding for the normal lightassay flow.\n"
            "Pick the default agent used for planning and analysis, save it once,\n"
            "then start evaluations with `lightassay quickstart --message ... --target ...`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # agents
    agents_parser = subparsers.add_parser(
        "agents",
        help="Show or change the default agent",
        description=(
            "Manage the default agent used for planning and analysis.\n"
            "Run with no arguments in a TTY to choose interactively."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    agents_parser.add_argument(
        "agent",
        nargs="?",
        help="Agent name to save directly without interactive prompting.",
    )
    agents_parser.add_argument(
        "--list",
        action="store_true",
        help="List available agents.",
    )
    agents_parser.add_argument(
        "--current",
        action="store_true",
        help="Print the current default agent.",
    )

    # workbook
    workbook_parser = subparsers.add_parser(
        "workbook",
        help="Create a fresh empty workbook with the next free auto-numbered name",
        description=(
            "Create a canonical empty workbook named `workbookN.workbook.md`,\n"
            "where N is the next free number in the output directory."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    workbook_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to create the workbook in (default: current directory)",
    )

    # quickstart
    quickstart_parser = subparsers.add_parser(
        "quickstart",
        help="End-to-end quickstart: message → workbook → run → analysis",
        description=(
            "Quickstart is the main self-serve entrypoint. Starting from one\n"
            "plain-language message plus a concrete target hint, it bootstraps\n"
            "the target, preparation / workflow configs, runs the workflow, and\n"
            "writes an analysis artifact. Compare is not part of quickstart."
        ),
        usage=_QUICKSTART_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    quickstart_parser.set_defaults(_command_parser=quickstart_parser)
    quickstart_parser.add_argument(
        "--message",
        required=True,
        help="Plain-language message describing what should be evaluated.",
    )
    quickstart_parser.add_argument(
        "--target",
        dest="target_hint",
        required=True,
        help="Concrete target hint identifying what should be evaluated.",
    )
    quickstart_parser.add_argument(
        "--agent",
        help=(
            "Built-in agent name (e.g. 'claude-cli', 'codex-cli'). "
            "Resolves preparation + semantic adapters automatically without "
            "hand-authored configs."
        ),
    )
    quickstart_parser.add_argument(
        "--full-intent",
        action="store_true",
        help=(
            "Disable the default minimal first-pass narrowing on suite "
            "breadth/selection. Use when the human request genuinely asks "
            "the first pass to be non-minimal."
        ),
    )
    quickstart_parser.add_argument(
        "--preparation-config",
        help=(
            "Optional path to a preparation config JSON file. "
            "Overrides the preparation adapter resolved from --agent. "
            "Required when --agent is not supplied."
        ),
    )
    quickstart_parser.add_argument(
        "--semantic-config",
        help=(
            "Optional path to a semantic adapter config JSON file. "
            "Overrides the semantic adapter resolved from --agent. "
            "Required when --agent is not supplied."
        ),
    )
    quickstart_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for the workbook, generated workflow config, and artifacts (default: .).",
    )
    quickstart_parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress incremental stage status output. "
            "The resolved execution binding summary and final summary still print."
        ),
    )

    # continue
    continue_parser = subparsers.add_parser(
        "continue",
        help="Continue iteration: extend/refine directions + cases, run, analyze, optional compare",
        description=(
            "Continue runs one full next iteration on the active workbook. It consumes\n"
            "the three current continuation fields in the workbook and/or a --message,\n"
            "rotates them into versioned history, re-plans, runs, and analyzes again.\n"
            "Use --compare-previous to compare the fresh run against the prior one."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    continue_parser.add_argument(
        "--message",
        help="Follow-up message to merge with workbook continuation instructions.",
    )
    continue_parser.add_argument(
        "--workbook",
        help=(
            "Explicit workbook path (default: active workbook pointer in the "
            "current working directory)."
        ),
    )
    continue_parser.add_argument(
        "--workbook-id",
        help=(
            "Continue against a known workbook id (see `lightassay workbooks`). "
            "Mutually exclusive with --workbook."
        ),
    )
    continue_parser.add_argument(
        "--workflow-config",
        help=(
            "Explicit workflow config path for the continued workbook. "
            "Use this when the workbook did not originate from quickstart "
            "or when the generated sibling config should not be reused."
        ),
    )
    continue_parser.add_argument(
        "--agent",
        help=(
            "Built-in agent name (e.g. 'claude-cli', 'codex-cli'). "
            "Resolves preparation + semantic adapters automatically."
        ),
    )
    continue_parser.add_argument(
        "--preparation-config",
        help=(
            "Optional path to a preparation config JSON file. "
            "Overrides --agent. Required when --agent is not supplied."
        ),
    )
    continue_parser.add_argument(
        "--semantic-config",
        help=(
            "Optional path to a semantic adapter config JSON file. "
            "Overrides --agent. Required when --agent is not supplied."
        ),
    )
    continue_parser.add_argument(
        "--compare-previous",
        action="store_true",
        help="Also produce a compare artifact between the prior run and the new run.",
    )
    continue_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for new artifacts written by continue (default: .).",
    )
    continue_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress incremental stage status output (final summary still prints).",
    )

    # workbook inspection
    subparsers.add_parser(
        "current-workbook",
        help="Print the active workbook path for the current working directory.",
    )

    subparsers.add_parser(
        "workbooks",
        help="List known workbook ids for the current working directory.",
    )

    # quick-try
    quick_parser = subparsers.add_parser(
        "quick-try",
        help="Run a minimal quick try from either inline target data or an existing workbook",
        description=(
            "Quick try is a bridge into the full workbook model.\n"
            "Use one of two paths:\n"
            "  1. create a new workbook from inline target data, or\n"
            "  2. use an existing canonical start workbook that already has a Target block.\n"
            "In both cases quick try writes a minimal brief and generates one\n"
            "representative direction and one representative case in the normal workbook shape."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    quick_parser.add_argument(
        "name",
        nargs="?",
        help="Workbook name / output filename prefix when creating a new workbook",
    )
    quick_parser.add_argument(
        "--workbook",
        help="Existing workbook path to bootstrap in-place instead of creating a new workbook",
    )
    quick_parser.add_argument(
        "--target-kind",
        help="Target kind, e.g. workflow, http-api, python-callable, prompt",
    )
    quick_parser.add_argument("--target-name", help="Short target name")
    quick_parser.add_argument("--target-locator", help="Target locator")
    quick_parser.add_argument("--target-boundary", help="Real execution boundary")
    quick_parser.add_argument(
        "--target-source",
        dest="target_sources",
        action="append",
        help="Target source reference. Repeat for multiple files/modules.",
    )
    quick_parser.add_argument(
        "--user-request",
        required=True,
        help="Natural-language request describing what should be evaluated",
    )
    quick_parser.add_argument(
        "--preparation-config",
        required=True,
        help="Path to preparation config JSON file (see docs/preparation_protocol.md)",
    )
    quick_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to create the workbook in (default: current directory)",
    )

    # refine-suite
    refine_parser = subparsers.add_parser(
        "refine-suite",
        help="Create a new planning workbook from an existing suite",
        description=(
            "Create a new workbook that preserves the target and the existing\n"
            "directions/cases as first-class structure, then adds explicit refinement context.\n"
            "This lets you continue from an existing suite without editing or copying files by hand."  # noqa: E501
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    refine_parser.add_argument(
        "source_workbook", help="Path to the existing workbook markdown file"
    )
    refine_parser.add_argument("name", help="New workbook name / output filename prefix")
    refine_parser.add_argument(
        "--refinement-request",
        required=True,
        help="Natural-language request describing what should be refined or explored next",
    )
    refine_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to create the new workbook in (default: current directory)",
    )

    # explore-workbook
    explore_parser = subparsers.add_parser(
        "explore-workbook",
        help="Create a bounded exploratory workbook from an existing workbook and run artifact",
        description=(
            "Create a new workbook seeded from an existing suite and one prior run artifact.\n"
            "The new workbook records the exploration goal, limits, and observed failures,\n"
            "then performs bounded iterative planning and execution so each iteration can\n"
            "learn from fresh run evidence rather than only the original seed run."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    explore_parser.add_argument(
        "source_workbook", help="Path to the existing workbook markdown file"
    )
    explore_parser.add_argument("run_artifact", help="Path to the seed run artifact JSON file")
    explore_parser.add_argument("name", help="New workbook name / output filename prefix")
    explore_parser.add_argument(
        "--exploration-goal",
        required=True,
        help="Natural-language exploration goal",
    )
    explore_parser.add_argument(
        "--preparation-config",
        required=True,
        help="Path to preparation config JSON file (see docs/preparation_protocol.md)",
    )
    explore_parser.add_argument(
        "--workflow-config",
        required=True,
        help="Path to workflow config JSON file used to execute each exploratory iteration",
    )
    explore_parser.add_argument(
        "--max-cases",
        type=int,
        required=True,
        help="Bounded number of follow-up cases to generate",
    )
    explore_parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help="Bounded number of exploratory iterations recorded in the artifact (default: 1)",
    )
    explore_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to create the new workbook in (default: current directory)",
    )

    # run
    run_parser = subparsers.add_parser(
        "run",
        help="Execute an independent run against approved cases in a workbook",
        description=(
            "Run the workflow under test against all cases in the workbook.\n"
            "The workbook must have RUN_READY: yes before running.\n"
            "Each run is independent. No partial/resume in v1.\n"
            "Saves a run artifact (JSON) with raw execution facts per case.\n\n"
            "Requires a workflow config JSON file (see docs/workflow_config_spec.md)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument(
        "workbook",
        help="Path to the workbook markdown file",
    )
    run_parser.add_argument(
        "--workflow-config",
        required=True,
        help="Path to workflow config JSON file (see docs/workflow_config_spec.md)",
    )
    run_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to save the run artifact JSON (default: current directory)",
    )

    # analyze
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Produce an LLM-driven semantic analysis from a run artifact",
        description=(
            "Pass a run artifact (JSON) to an LLM for semantic analysis.\n"
            "The LLM identifies successes, failures, patterns, weak spots, and recommendations.\n"
            "Both completed and failed runs can be analyzed.\n"
            "Code does not judge quality. LLM does.\n"
            "Saves an analysis artifact (markdown)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze_parser.add_argument(
        "run_artifact",
        help="Path to the run artifact JSON file",
    )
    analyze_parser.add_argument(
        "--semantic-config",
        required=True,
        help="Path to semantic adapter config JSON file (see docs/semantic_adapter_spec.md)",
    )
    analyze_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to save the analysis artifact markdown (default: current directory)",
    )

    # compare
    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare two or more completed run artifacts via LLM",
        description=(
            "Compare multiple completed run artifacts via LLM semantic reasoning.\n"
            "Only completed runs (status: completed) are accepted.\n"
            "Compare is a separate operation, not part of any single run.\n"
            "Saves a compare artifact (markdown)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    compare_parser.set_defaults(_command_parser=compare_parser)
    compare_parser.add_argument(
        "run_artifacts",
        nargs="+",
        metavar="RUN_ARTIFACT",
        help="Paths to run artifact JSON files (at least two required)",
    )
    compare_parser.add_argument(
        "--semantic-config",
        required=True,
        help="Path to semantic adapter config JSON file (see docs/semantic_adapter_spec.md)",
    )
    compare_parser.add_argument(
        "--goal",
        help="High-level comparison goal in natural language",
    )
    compare_parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to save the compare artifact markdown (default: current directory)",
    )

    # prepare-directions
    prep_dir_parser = subparsers.add_parser(
        "prepare-directions",
        help="Generate directions from a workbook brief via LLM",
        description=(
            "Read the brief from a workbook and call a preparation adapter\n"
            "to generate testing directions.\n"
            "The workbook is updated in-place with the generated directions.\n"
            "Each direction gets an empty HUMAN:instruction for human feedback.\n\n"
            "Requires a preparation config JSON file (see docs/preparation_protocol.md)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    prep_dir_parser.add_argument(
        "workbook",
        help="Path to the workbook markdown file",
    )
    prep_dir_parser.add_argument(
        "--preparation-config",
        required=True,
        help="Path to preparation config JSON file (see docs/preparation_protocol.md)",
    )

    # prepare-cases
    prep_cases_parser = subparsers.add_parser(
        "prepare-cases",
        help="Generate cases from workbook brief, directions, and feedback via LLM",
        description=(
            "Read the brief, directions, and human feedback from a workbook\n"
            "and call a preparation adapter to generate test cases.\n"
            "The workbook is updated in-place with the generated cases.\n"
            "Each case gets an empty HUMAN:instruction for human feedback.\n\n"
            "The workbook must already have directions (run prepare-directions first)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    prep_cases_parser.add_argument(
        "workbook",
        help="Path to the workbook markdown file",
    )
    prep_cases_parser.add_argument(
        "--preparation-config",
        required=True,
        help="Path to preparation config JSON file (see docs/preparation_protocol.md)",
    )

    # prepare-readiness
    prep_ready_parser = subparsers.add_parser(
        "prepare-readiness",
        help="Reconcile workbook and set RUN_READY via LLM",
        description=(
            "Read the full workbook state (brief, directions, cases, all feedback)\n"
            "and call a preparation adapter to reconcile the workbook.\n"
            "The adapter incorporates human feedback, resolves contradictions,\n"
            "and sets RUN_READY: yes or no.\n\n"
            "The workbook is updated in-place with reconciled directions, cases,\n"
            "and the RUN_READY status."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    prep_ready_parser.add_argument(
        "workbook",
        help="Path to the workbook markdown file",
    )
    prep_ready_parser.add_argument(
        "--preparation-config",
        required=True,
        help="Path to preparation config JSON file (see docs/preparation_protocol.md)",
    )

    return parser


def _make_reporter(quiet: bool):
    class _QuietReporter:
        def __init__(self, stream) -> None:
            self._stream = stream

        def __call__(self, stage: str, status: str, detail: str) -> None:
            if stage != _EXECUTION_BINDING_STAGE or status != "done":
                return
            marker = "✓"
            suffix = f" — {detail}" if detail else ""
            self._stream.write(f"[{marker}] {stage}{suffix}\n")
            self._stream.flush()

    if quiet:
        return _QuietReporter(sys.stderr)
    from .surface import make_terminal_reporter

    return make_terminal_reporter(sys.stderr)


def _suggest_next_action(message: str) -> str | None:
    lower = message.lower()
    if "preparation config" in lower:
        return "Check the preparation config path and JSON fields, then retry."
    if "semantic config" in lower:
        return "Check the semantic config path and JSON fields, then retry."
    if "workflow config" in lower:
        return "Check the workflow config path and driver binding, then retry."
    if "planning foundation" in lower:
        return "Fix the target/source references or brief, rerun preparation, then run again."
    if "run_ready" in lower or "run-ready" in lower:
        return "Inspect READINESS_NOTE and complete preparation before running."
    if "not executable" in lower:
        return "Make the referenced binding executable or point the config at a valid one."
    if "not found" in lower or "missing file" in lower:
        return "Check the referenced path from the current project directory."
    if "no active workbook" in lower:
        return "Run quickstart first, or pass --workbook / --workbook-id explicitly."
    if "agent" in lower and "configured" in lower:
        return "Run lightassay init, pass --agent, or provide both config paths."
    return None


def _print_error(exc: EvalError) -> None:
    message = str(exc)
    print(f"Error: {message}", file=sys.stderr)
    next_action = _suggest_next_action(message)
    if next_action:
        print(f"Next: {next_action}", file=sys.stderr)


def _stdin_is_tty() -> bool:
    checker = getattr(sys.stdin, "isatty", None)
    return bool(checker and checker())


def _print_agents(current: str | None = None) -> None:
    for name, _description in _list_agents():
        marker = "*" if name == current else " "
        print(f"{marker} {name}")


def _validate_agent_cli(name: str) -> None:
    override = os.environ.get("LIGHTASSAY_AGENT_CMD")
    if override:
        parts = shlex.split(override)
        if not parts:
            raise EvalError("LIGHTASSAY_AGENT_CMD is set but empty.")
        if shutil.which(parts[0]) is not None:
            return
        raise EvalError(
            "Cannot use the configured agent command: "
            f"{parts[0]!r} from LIGHTASSAY_AGENT_CMD is not available in PATH."
        )
    required_cli = agent_cli_requirement(name)
    if required_cli is None:
        return
    if shutil.which(required_cli) is not None:
        return
    raise EvalError(
        f"Cannot use agent {name!r}: required CLI {required_cli!r} is not available in PATH."
    )


def _validate_known_agent(name: str) -> None:
    known = [agent_name for agent_name, _ in _list_agents()]
    if name in known:
        return
    raise EvalError(f"Unknown agent: {name!r}. Known agents: {', '.join(known)}.")


def _persist_agent(name: str) -> tuple[str, str]:
    stripped = (name or "").strip()
    if not stripped:
        raise EvalError("Agent name must be a non-empty string.")
    _validate_known_agent(stripped)
    _validate_agent_cli(stripped)
    path = set_agent(stripped)
    return stripped, path


def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    response = input(f"{prompt} {suffix}: ").strip().lower()
    if not response:
        return default
    return response in {"y", "yes"}


def _choose_agent_interactively(
    *,
    header: str,
    current: str | None = None,
) -> tuple[str, str]:
    options = _list_agents()
    if not options:
        raise EvalError("No built-in agents are available.")
    names = [name for name, _ in options]
    default_index = names.index(current) if current in names else 0

    while True:
        print(header)
        for idx, (name, _description) in enumerate(options, start=1):
            marker = " (current)" if name == current else ""
            print(f"  {idx}) {name}{marker}")

        raw_choice = input(f"Choice [{default_index + 1}]: ").strip()
        if not raw_choice:
            selected = names[default_index]
        elif raw_choice.isdigit() and 1 <= int(raw_choice) <= len(names):
            selected = names[int(raw_choice) - 1]
        elif raw_choice in names:
            selected = raw_choice
        else:
            print("Invalid choice. Enter a number from the list or an exact agent name.")
            continue

        try:
            return _persist_agent(selected)
        except EvalError as exc:
            _print_error(exc)


def _resolve_agent_arg(args: argparse.Namespace) -> str | None:
    """Prefer explicit ``--agent`` over the persisted default."""
    explicit = getattr(args, "agent", None)
    if explicit is not None and explicit.strip():
        return explicit.strip()
    return current_agent() or None


def _require_agent_or_configs(args: argparse.Namespace) -> str | None:
    agent = _resolve_agent_arg(args)
    if agent is not None:
        _validate_known_agent(agent)
        _validate_agent_cli(agent)
        return agent

    prep = getattr(args, "preparation_config", None)
    sem = getattr(args, "semantic_config", None)
    if prep and sem:
        return None

    print(
        "No agent configured. Run `lightassay init` first.",
        file=sys.stderr,
    )
    print(
        "Or pass `--agent`; or provide both `--preparation-config` and `--semantic-config`.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _raw_option_value(argv: list[str], flag: str) -> str | None:
    for index, token in enumerate(argv):
        if token == flag:
            if index + 1 >= len(argv):
                return None
            next_token = argv[index + 1]
            if next_token.startswith("-"):
                return None
            return next_token
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


def _quickstart_has_agent_or_configs(argv: list[str]) -> bool:
    if _raw_option_value(argv, "--agent"):
        return True
    return bool(
        _raw_option_value(argv, "--preparation-config")
        and _raw_option_value(argv, "--semantic-config")
    )


def _print_quickstart_required_args_error(missing: list[str]) -> int:
    print(f"usage: {_QUICKSTART_USAGE}", file=sys.stderr)
    print(
        "lightassay quickstart: error: the following arguments are required: " + ", ".join(missing),
        file=sys.stderr,
    )
    return 2


def _preparse_quickstart(argv: list[str]) -> int | None:
    if not argv or argv[0] != "quickstart":
        return None
    tail = argv[1:]
    if any(token in ("-h", "--help") for token in tail):
        return None

    if not _quickstart_has_agent_or_configs(tail) and current_agent() is None:
        print(
            "No agent configured. Run `lightassay init` first.",
            file=sys.stderr,
        )
        print(
            "Or pass `--agent`; or provide both `--preparation-config` and `--semantic-config`.",
            file=sys.stderr,
        )
        return 2

    missing: list[str] = []
    message = _raw_option_value(tail, "--message")
    target_hint = _raw_option_value(tail, "--target")
    if not isinstance(message, str) or not message.strip():
        missing.append("--message")
    if not isinstance(target_hint, str) or not target_hint.strip():
        missing.append("--target")
    if missing:
        return _print_quickstart_required_args_error(missing)
    return None


def _auto_workbook_name(output_dir: str) -> str:
    """Return the next free ``workbook<N>`` slot in *output_dir*.

    Scans existing ``workbook<N>.workbook.md`` files and returns
    ``workbook<max+1>``. Starts at 1 when there are none.
    """
    import re

    pattern = re.compile(r"^workbook(\d+)\.workbook\.md$")
    used: list[int] = []
    if os.path.isdir(output_dir):
        for fname in os.listdir(output_dir):
            match = pattern.match(fname)
            if match:
                used.append(int(match.group(1)))
    next_n = (max(used) + 1) if used else 1
    return f"workbook{next_n}"


def _require_quickstart_inputs(args: argparse.Namespace) -> tuple[str, str]:
    message = getattr(args, "message", None)
    target_hint = getattr(args, "target_hint", None)
    missing: list[str] = []
    resolved_message = message.strip() if isinstance(message, str) and message.strip() else ""
    resolved_target = (
        target_hint.strip() if isinstance(target_hint, str) and target_hint.strip() else ""
    )
    if not resolved_message:
        missing.append("--message")
    if not resolved_target:
        missing.append("--target")
    if not missing:
        return resolved_message, resolved_target

    parser = getattr(args, "_command_parser", None)
    if parser is not None:
        parser.print_usage(sys.stderr)
        print(
            "lightassay quickstart: error: the following arguments are required: "
            + ", ".join(missing),
            file=sys.stderr,
        )
    else:
        print(
            "Error: quickstart requires " + " and ".join(missing) + ".",
            file=sys.stderr,
        )
    raise SystemExit(2)


def _cmd_quickstart(args: argparse.Namespace) -> int:
    reporter = _make_reporter(args.quiet)
    try:
        agent = _require_agent_or_configs(args)
        message, target_hint = _require_quickstart_inputs(args)
        name = _auto_workbook_name(args.output_dir)
        result = quickstart(
            name,
            message=message,
            target_hint=target_hint,
            preparation_config=args.preparation_config,
            semantic_config=args.semantic_config,
            output_dir=args.output_dir,
            agent=agent,
            full_intent=args.full_intent,
            reporter=reporter,
        )
    except SystemExit as exc:
        return int(exc.code)
    except EvalError as exc:
        _print_error(exc)
        return 1

    quickstart_label = (
        "Quickstart complete"
        if result.run_status == "completed"
        else f"Quickstart finished with run status {result.run_status}"
    )
    print(f"{quickstart_label}: {result.workbook_path}")
    print(f"  Directions: {result.direction_count}")
    print(f"  Cases: {result.case_count}")
    print(
        f"  Run: {result.run_artifact_path} ({result.run_status}: "
        f"{result.completed_cases}/{result.total_cases} completed, "
        f"{result.failed_cases} failed)"
    )
    print(f"  Analysis: {result.analysis_artifact_path}")
    print(f"  Workflow config: {result.workflow_config_path}")
    print(f"  Active workbook pointer: {result.active_workbook_pointer_path}")
    print(f"  Execution log: {result.execution_log_path}")
    print(f"  Conclusion: {result.conclusion}")
    return 0 if result.run_status == "completed" else 1


def _cmd_continue(args: argparse.Namespace) -> int:
    reporter = _make_reporter(args.quiet)
    try:
        agent = _require_agent_or_configs(args)
        result = continue_workbook(
            preparation_config=args.preparation_config,
            semantic_config=args.semantic_config,
            message=args.message,
            workbook_path=args.workbook,
            workbook_id=args.workbook_id,
            workflow_config_path=args.workflow_config,
            output_dir=args.output_dir,
            compare_previous=args.compare_previous,
            agent=agent,
            reporter=reporter,
        )
    except SystemExit as exc:
        return int(exc.code)
    except EvalError as exc:
        _print_error(exc)
        return 1

    continue_label = (
        "Continue complete"
        if result.run_status == "completed"
        else f"Continue finished with run status {result.run_status}"
    )
    print(f"{continue_label}: {result.workbook_path}")
    print(f"  Continuation version rotated: v{result.continuation_version}")
    print(f"  Directions: {result.direction_count}")
    print(f"  Cases: {result.case_count}")
    print(
        f"  Run: {result.run_artifact_path} ({result.run_status}: "
        f"{result.completed_cases}/{result.total_cases} completed, "
        f"{result.failed_cases} failed)"
    )
    print(f"  Analysis: {result.analysis_artifact_path}")
    if result.compare_artifact_path is not None:
        print(f"  Compare: {result.compare_artifact_path}")
    print(f"  Active workbook pointer: {result.active_workbook_pointer_path}")
    print(f"  Execution log: {result.execution_log_path}")
    print(f"  Conclusion: {result.conclusion}")
    return 0 if result.run_status == "completed" else 1


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        current = current_agent()
    except EvalError as exc:
        _print_error(exc)
        return 1

    if not _stdin_is_tty():
        print("lightassay init: cannot run interactive onboarding without a TTY.", file=sys.stderr)
        print(
            "Set the agent non-interactively with `lightassay agents claude-cli` or "
            "`lightassay agents codex-cli`.",
            file=sys.stderr,
        )
        return 2

    print("Welcome to lightassay.")
    if current is not None:
        print(f"Current agent: {current}.")
        if not _prompt_yes_no("Change it?", default=False):
            print(f"Agent unchanged: {current}")
            print("Next:")
            print(
                '  lightassay quickstart --message "describe what to test" '
                '--target "myapp.pipeline.run"'
            )
            return 0

    try:
        name, path = _choose_agent_interactively(
            header="Select the agent:",
            current=current,
        )
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Agent saved: {name} ({path})")
    print("Next:")
    print('  lightassay quickstart --message "describe what to test" --target "myapp.pipeline.run"')
    return 0


def _cmd_agents(args: argparse.Namespace) -> int:
    try:
        if args.agent and (args.list or args.current):
            raise EvalError("agents: positional agent cannot be combined with --list or --current.")
        current = current_agent()
    except EvalError as exc:
        _print_error(exc)
        return 1

    if args.list:
        _print_agents(current=current)
        return 0
    if args.current:
        if current is None:
            print("No agent configured. Run `lightassay init` first.")
            return 0
        print(current)
        return 0
    if args.agent is not None:
        try:
            name, path = _persist_agent(args.agent)
        except EvalError as exc:
            _print_error(exc)
            return 1
        print(f"Agent saved: {name} ({path})")
        return 0
    if not _stdin_is_tty():
        print("lightassay agents: cannot prompt without a TTY.", file=sys.stderr)
        print(
            "Use `lightassay agents --list`, `lightassay agents --current`, "
            "or `lightassay agents <name>`.",
            file=sys.stderr,
        )
        return 2

    if current is not None:
        print(f"Current agent: {current}.")
    try:
        name, path = _choose_agent_interactively(
            header="Select the agent:",
            current=current,
        )
    except EvalError as exc:
        _print_error(exc)
        return 1
    print(f"Agent saved: {name} ({path})")
    return 0


def _cmd_workbook(args: argparse.Namespace) -> int:
    try:
        name = _auto_workbook_name(args.output_dir)
        path = init_workbook(name, output_dir=args.output_dir)
    except EvalError as exc:
        _print_error(exc)
        return 1
    print(f"Created workbook: {path}")
    return 0


def _cmd_current_workbook(args: argparse.Namespace) -> int:
    try:
        path = current_workbook()
    except EvalError as exc:
        _print_error(exc)
        return 1
    if path is None:
        print("No active workbook. Run `lightassay quickstart` first.")
        return 0
    print(path)
    return 0


def _cmd_workbooks(args: argparse.Namespace) -> int:
    try:
        entries = known_workbooks()
    except EvalError as exc:
        _print_error(exc)
        return 1
    if not entries:
        print("No known workbooks under this state root.")
        return 0
    for entry in entries:
        wb_id = entry.get("id") or ""
        wb_path = entry.get("workbook_path") or ""
        updated = entry.get("updated_at") or ""
        print(f"{wb_id}\t{wb_path}\t{updated}")
    return 0


def _cmd_quick_try(args: argparse.Namespace) -> int:
    try:
        if args.workbook is not None:
            if args.name is not None:
                raise EvalError("quick-try with --workbook must not also provide a workbook name.")
            if any(
                value is not None
                for value in (
                    args.target_kind,
                    args.target_name,
                    args.target_locator,
                    args.target_boundary,
                    args.target_sources,
                )
            ):
                raise EvalError(
                    "quick-try with --workbook must not also provide inline target fields."
                )
            result = quick_try_workbook(
                args.workbook,
                user_request=args.user_request,
                preparation_config=args.preparation_config,
            )
        else:
            if args.name is None:
                raise EvalError("quick-try without --workbook requires a workbook name.")
            missing = [
                label
                for label, value in (
                    ("--target-kind", args.target_kind),
                    ("--target-name", args.target_name),
                    ("--target-locator", args.target_locator),
                    ("--target-boundary", args.target_boundary),
                )
                if value is None
            ]
            if not args.target_sources:
                missing.append("--target-source")
            if missing:
                raise EvalError(
                    "quick-try without --workbook requires inline target fields: "
                    + ", ".join(missing)
                )
            result = quick_try(
                args.name,
                target=EvalTarget(
                    kind=args.target_kind,
                    name=args.target_name,
                    locator=args.target_locator,
                    boundary=args.target_boundary,
                    sources=args.target_sources,
                    notes="",
                ),
                user_request=args.user_request,
                preparation_config=args.preparation_config,
                output_dir=args.output_dir,
            )
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Quick try workbook created: {result.workbook_path}")
    print(f"  Directions: {result.state.direction_count}")
    print(f"  Cases: {result.state.case_count}")
    print(f"  Workbook RUN_READY: {'yes' if result.state.workbook_run_ready else 'no'}")
    return 0


def _cmd_refine_suite(args: argparse.Namespace) -> int:
    try:
        result = refine_workbook(
            args.source_workbook,
            name=args.name,
            refinement_request=args.refinement_request,
            output_dir=args.output_dir,
        )
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Refinement workbook created: {result.workbook_path}")
    print(f"  Inherited directions: {result.inherited_direction_count}")
    print(f"  Inherited cases: {result.inherited_case_count}")
    return 0


def _cmd_explore_workbook(args: argparse.Namespace) -> int:
    try:
        result = explore_workbook(
            args.source_workbook,
            run_artifact_path=args.run_artifact,
            workflow_config=args.workflow_config,
            name=args.name,
            exploration_goal=args.exploration_goal,
            preparation_config=args.preparation_config,
            max_cases=args.max_cases,
            max_iterations=args.max_iterations,
            output_dir=args.output_dir,
        )
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Exploratory workbook created: {result.workbook_path}")
    print(f"  Seed run: {result.seeded_from_run_id}")
    print(f"  Failed cases observed: {result.failed_case_count}")
    print(f"  Iteration runs: {len(result.iteration_run_artifact_paths)}")
    print(f"  Directions: {result.state.direction_count}")
    print(f"  Cases: {result.state.case_count}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    workbook_path: str = args.workbook
    config_path: str = args.workflow_config
    output_dir: str = args.output_dir

    try:
        session = open_session(
            workbook_path,
            workflow_config=config_path,
        )
        result = session.run(output_dir=output_dir)
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Run {result.run_id} {result.status}.")
    print(
        f"  Cases: {result.total_cases} total, "
        f"{result.completed_cases} completed, "
        f"{result.failed_cases} failed."
    )
    print(f"  Artifact: {result.artifact_path}")
    print(f"  Workbook updated: {workbook_path}")

    return 0 if result.status == "completed" else 1


def _extract_workbook_path_from_artifact(artifact_path: str) -> str:
    """Extract workbook_path from a run artifact JSON file."""
    if not os.path.isfile(artifact_path):
        raise EvalError(f"Run artifact file not found: {artifact_path!r}")

    try:
        with open(artifact_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise EvalError(f"Run artifact is not valid JSON: {artifact_path!r}: {exc}") from exc

    workbook_path = data.get("workbook_path")
    if not isinstance(workbook_path, str) or not workbook_path:
        raise EvalError(f"Run artifact {artifact_path!r} has no valid workbook_path field.")

    return workbook_path


def _cmd_analyze(args: argparse.Namespace) -> int:
    run_artifact_path: str = args.run_artifact
    semantic_config_path: str = args.semantic_config
    output_dir: str = args.output_dir

    try:
        workbook_path = _extract_workbook_path_from_artifact(run_artifact_path)
        session = open_session(
            workbook_path,
            semantic_config=semantic_config_path,
        )
        result = session.analyze(run_artifact_path, output_dir=output_dir)
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Analysis {result.analysis_id} complete.")
    print(f"  Artifact: {result.artifact_path}")
    print(f"  Workbook updated: {workbook_path}")

    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    run_artifact_paths: list[str] = args.run_artifacts
    semantic_config_path: str = args.semantic_config
    goal: str | None = args.goal
    output_dir: str = args.output_dir

    if len(run_artifact_paths) < 2:
        parser = getattr(args, "_command_parser", None)
        if parser is not None:
            parser.print_usage(sys.stderr)
        print(
            "lightassay compare: error: at least 2 RUN_ARTIFACT paths are required.",
            file=sys.stderr,
        )
        return 2

    try:
        result = compare_runs(
            run_artifact_paths,
            semantic_config=semantic_config_path,
            goal=goal,
            output_dir=output_dir,
        )
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Compare {result.compare_id} complete.")
    print(f"  Runs compared: {', '.join(run_artifact_paths)}")
    print(f"  Artifact: {result.artifact_path}")

    return 0


def _cmd_prepare_directions(args: argparse.Namespace) -> int:
    workbook_path: str = args.workbook
    config_path: str = args.preparation_config

    try:
        session = open_session(
            workbook_path,
            preparation_config=config_path,
        )
        state = session.state()

        if state.preparation_stage != PreparationStage.NEEDS_DIRECTIONS:
            print(
                f"Error: workbook preparation stage is "
                f"{state.preparation_stage.value!r}, "
                f"but prepare-directions requires "
                f"{PreparationStage.NEEDS_DIRECTIONS.value!r}.",
                file=sys.stderr,
            )
            return 1

        result = session.prepare()
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Generated {result.state.direction_count} directions.")
    print(f"  Workbook updated: {workbook_path}")
    return 0


def _cmd_prepare_cases(args: argparse.Namespace) -> int:
    workbook_path: str = args.workbook
    config_path: str = args.preparation_config

    try:
        session = open_session(
            workbook_path,
            preparation_config=config_path,
        )
        state = session.state()

        if state.preparation_stage != PreparationStage.NEEDS_CASES:
            print(
                f"Error: workbook preparation stage is "
                f"{state.preparation_stage.value!r}, "
                f"but prepare-cases requires "
                f"{PreparationStage.NEEDS_CASES.value!r}.",
                file=sys.stderr,
            )
            return 1

        result = session.prepare()
    except EvalError as exc:
        _print_error(exc)
        return 1

    print(f"Generated {result.state.case_count} cases.")
    print(f"  Workbook updated: {workbook_path}")
    return 0


def _cmd_prepare_readiness(args: argparse.Namespace) -> int:
    workbook_path: str = args.workbook
    config_path: str = args.preparation_config

    try:
        session = open_session(
            workbook_path,
            preparation_config=config_path,
        )
        state = session.state()

        if state.preparation_stage != PreparationStage.NEEDS_READINESS:
            print(
                f"Error: workbook preparation stage is "
                f"{state.preparation_stage.value!r}, "
                f"but prepare-readiness requires "
                f"{PreparationStage.NEEDS_READINESS.value!r}.",
                file=sys.stderr,
            )
            return 1

        result = session.prepare()
    except EvalError as exc:
        _print_error(exc)
        return 1

    status = "yes" if result.state.workbook_run_ready else "no"
    print(f"Readiness reconciled. RUN_READY: {status}")
    print(f"  Directions: {result.state.direction_count}")
    print(f"  Cases: {result.state.case_count}")
    print(f"  Workbook updated: {workbook_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    preparse_code = _preparse_quickstart(argv)
    if preparse_code is not None:
        return preparse_code

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    dispatch: dict[str, object] = {
        "init": _cmd_init,
        "agents": _cmd_agents,
        "workbook": _cmd_workbook,
        "quickstart": _cmd_quickstart,
        "continue": _cmd_continue,
        "quick-try": _cmd_quick_try,
        "refine-suite": _cmd_refine_suite,
        "explore-workbook": _cmd_explore_workbook,
        "run": _cmd_run,
        "analyze": _cmd_analyze,
        "compare": _cmd_compare,
        "prepare-directions": _cmd_prepare_directions,
        "prepare-cases": _cmd_prepare_cases,
        "prepare-readiness": _cmd_prepare_readiness,
        "current-workbook": _cmd_current_workbook,
        "workbooks": _cmd_workbooks,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        print(
            f"Command '{args.command}' is not yet implemented.",
            file=sys.stderr,
        )
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
