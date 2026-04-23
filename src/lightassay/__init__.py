"""lightassay: file-based orchestrator for structured evaluation of applied LLM workflows.

One rule runs through the whole design: humans declare intent, LLMs do the
semantic reasoning, code orchestrates execution and measures raw facts — and
never judges output quality.  The workbook (markdown), run artifact (JSON),
and analysis/compare artifacts (markdown) are the source of truth; the
library is an orchestrator around them.

The ordinary public entrypoint is the L1 library surface.  Start here::

    from lightassay import (
        open_session,
        init_workbook,
        quick_try,
        quick_try_workbook,
        refine_workbook,
        explore_workbook,
        compare_runs,
    )

    # Create a workbook (or use an existing one).
    wb_path = init_workbook("my-eval", output_dir=".")

    # Or run a one-shot quick try to see the full workbook shape.
    quick = quick_try(
        "my-quick-try",
        target=EvalTarget(
            kind="workflow",
            name="summarize",
            locator="myapp.pipeline.run",
            boundary="high-level pipeline boundary",
            sources=["myapp/pipeline.py", "myapp/prompts/summarize.py"],
        ),
        user_request="Check how the pipeline handles obvious failures without over-correcting.",
        preparation_config="prep.json",
        output_dir=".",
    )

    # Open a session.
    session = open_session(
        wb_path,
        preparation_config="prep.json",
        workflow_config="wf.json",
        semantic_config="sem.json",
    )

    # Inspect state, prepare, run, analyze.
    state = session.state()
    result = session.prepare()
    ...

    # Compare runs (no session/workbook required).
    compare_result = compare_runs(
        ["run_a.json", "run_b.json"],
        semantic_config="sem.json",
    )

Deeper engine internals are not part of the ordinary L1 surface.
Use ``open_diagnostics()`` on a session to enter the L2
diagnostics/recovery layer with structured reports, evidence, and
bounded recovery actions.  The ``DiagnosticsHandle`` type returned
by ``open_diagnostics()`` lives in ``lightassay.types`` but
is not part of the ordinary top-level export set.  L2 detail types
live in ``lightassay.diagnostics``.

For deep inspection and bounded low-level control, escalate from
L2 to L3 via ``diag.open_expert()``.  L3 types live in
``lightassay.expert``.
"""

__version__ = "0.3.2"

# L1 public surface ──────────────────────────────────────────────────────────

from .errors import EvalError
from .surface import (
    EvalSession,
    compare_runs,
    continue_workbook,
    current_agent,
    explore_workbook,
    init_workbook,
    list_agents,
    open_session,
    quick_try,
    quick_try_workbook,
    quickstart,
    refine_workbook,
    set_agent,
)
from .types import (
    AnalyzeResult,
    CompareResult,
    ContinueResult,
    EvalState,
    EvalTarget,
    ExploreResult,
    PreparationStage,
    PrepareResult,
    QuickstartResult,
    QuickTryResult,
    RefineResult,
    RunResult,
)

__all__ = [
    # Version
    "__version__",
    # L1 control
    "open_session",
    "init_workbook",
    "quick_try",
    "quick_try_workbook",
    "refine_workbook",
    "explore_workbook",
    "compare_runs",
    "quickstart",
    "continue_workbook",
    "list_agents",
    "current_agent",
    "set_agent",
    "EvalSession",
    # L1 types
    "EvalTarget",
    "EvalState",
    "ExploreResult",
    "PreparationStage",
    "PrepareResult",
    "QuickstartResult",
    "QuickTryResult",
    "ContinueResult",
    "RefineResult",
    "RunResult",
    "AnalyzeResult",
    "CompareResult",
    # L1 error boundary
    "EvalError",
]
