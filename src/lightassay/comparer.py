"""Subprocess-based semantic comparer implementing the semantic adapter protocol.

See docs/semantic_adapter_spec.md for the full specification.
See docs/compare_artifact_spec.md for the compare artifact format.

Compare is a separate, explicitly initiated operation over 2+ completed run
artifacts. It is never part of a run. Failed runs are strictly rejected.

No fallback, no best-effort recovery.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from ._subprocess_capture import run_text_subprocess
from .errors import CompareError
from .run_artifact_io import run_artifact_to_dict
from .run_models import RunArtifact
from .semantic_config import SemanticConfig


def execute_compare(
    run_artifacts: list[RunArtifact],
    run_artifact_paths: list[str],
    semantic_config: SemanticConfig,
    compare_goal: str | None = None,
) -> tuple[str, str]:
    """Execute semantic compare: call the adapter, return the compare markdown artifact.

    Only completed run artifacts are accepted. Failed runs cause CompareError.
    At least 2 run artifacts are required.

    Returns the complete compare artifact as a markdown string
    (metadata header + adapter compare body) and the compare_id.

    Raises CompareError on any contract violation.
    """
    # Validate minimum count.
    if len(run_artifacts) < 2:
        raise CompareError(f"Compare requires at least 2 run artifacts, got {len(run_artifacts)}")

    if len(run_artifacts) != len(run_artifact_paths):
        raise CompareError(
            f"Mismatch: {len(run_artifacts)} artifacts but {len(run_artifact_paths)} paths"
        )

    # Validate all runs are completed — strict, no exceptions.
    for i, artifact in enumerate(run_artifacts):
        if artifact.status != "completed":
            raise CompareError(
                f"Run artifact [{i}] (run_id={artifact.run_id!r}) has status "
                f"{artifact.status!r}. Compare only accepts completed runs."
            )

    command = semantic_config.invocation()

    # File-backed adapters: pre-check path is executable for specific
    # error messages.  Command-backed adapters (built-in backends) rely
    # on subprocess to surface startup errors.
    if not semantic_config.command:
        adapter = semantic_config.adapter
        if not os.path.exists(adapter):
            raise CompareError(f"Semantic adapter not found: {adapter!r}")
        if not os.access(adapter, os.X_OK):
            raise CompareError(f"Semantic adapter not executable: {adapter!r}")

    # Build request payload.
    request_data = {
        "operation": "compare",
        "run_artifacts": [run_artifact_to_dict(a) for a in run_artifacts],
    }
    if compare_goal is not None:
        request_data["compare_goal"] = compare_goal
    request_json = json.dumps(request_data, ensure_ascii=False)

    # Call adapter via subprocess.
    try:
        result = run_text_subprocess(
            command,
            input_text=request_json,
            env=semantic_config.subprocess_env(),
            live_stderr=bool(semantic_config.command),
        )
    except FileNotFoundError:
        raise CompareError(f"Semantic adapter not found: {command[0]!r}") from None
    except PermissionError:
        raise CompareError(f"Semantic adapter not executable: {command[0]!r}") from None

    if result.returncode != 0:
        raise CompareError(
            f"Semantic adapter exited with code {result.returncode}: "
            f"{(result.stderr or '').strip()[:400]}"
        )

    # Parse stdout as JSON.
    stdout = result.stdout
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        raise CompareError("Semantic adapter stdout is not valid JSON") from None

    if not isinstance(response, dict):
        raise CompareError(
            f"Semantic adapter response must be a JSON object, got {type(response).__name__}"
        )

    # Validate required field.
    if "compare_markdown" not in response:
        raise CompareError("Semantic adapter response missing required field: 'compare_markdown'")

    compare_markdown = response["compare_markdown"]

    if not isinstance(compare_markdown, str):
        raise CompareError(
            "Semantic adapter response field 'compare_markdown' must be a string, "
            f"got {type(compare_markdown).__name__}"
        )

    if not compare_markdown.strip():
        raise CompareError("Semantic adapter response field 'compare_markdown' must be non-empty")

    goal_alignment_summary = None
    if compare_goal is not None:
        if "goal_alignment_summary" not in response:
            raise CompareError(
                "Semantic adapter response missing required field for goal-aware compare: 'goal_alignment_summary'"  # noqa: E501
            )
        goal_alignment_summary = response["goal_alignment_summary"]
        if not isinstance(goal_alignment_summary, str) or not goal_alignment_summary.strip():
            raise CompareError(
                "Semantic adapter response field 'goal_alignment_summary' must be a non-empty string when compare_goal is provided"  # noqa: E501
            )

    # Build complete compare artifact.
    compare_id = uuid.uuid4().hex[:12]
    compared_at = datetime.now(timezone.utc).isoformat()

    artifact_text = _render_compare_artifact(
        compare_id=compare_id,
        run_ids=[a.run_id for a in run_artifacts],
        comparer_provider=semantic_config.provider,
        comparer_model=semantic_config.model,
        compared_at=compared_at,
        run_artifact_paths=run_artifact_paths,
        compare_goal=compare_goal,
        goal_alignment_summary=goal_alignment_summary,
        compare_body=compare_markdown,
    )

    return artifact_text, compare_id


def _render_compare_artifact(
    *,
    compare_id: str,
    run_ids: list[str],
    comparer_provider: str,
    comparer_model: str,
    compared_at: str,
    run_artifact_paths: list[str],
    compare_goal: str | None,
    goal_alignment_summary: str | None,
    compare_body: str,
) -> str:
    """Render the complete compare artifact markdown."""
    lines = [
        f"# Compare: {compare_id}",
        "",
        f"- **run_ids:** {', '.join(run_ids)}",
        f"- **comparer_provider:** {comparer_provider}",
        f"- **comparer_model:** {comparer_model}",
        f"- **compared_at:** {compared_at}",
        f"- **run_artifact_paths:** {', '.join(run_artifact_paths)}",
    ]
    if compare_goal is not None:
        lines.append(f"- **compare_goal:** {compare_goal}")
    if goal_alignment_summary is not None:
        lines.append(f"- **goal_alignment_summary:** {goal_alignment_summary}")
    lines.extend(["", "---", ""])
    if compare_goal is not None:
        lines.extend(
            [
                "## Goal alignment",
                "",
                f"- **Goal:** {compare_goal}",
            ]
        )
        if goal_alignment_summary is not None:
            lines.append(f"- **Alignment summary:** {goal_alignment_summary}")
        lines.extend(["", "---", ""])
    lines.extend([compare_body, ""])
    return "\n".join(lines)
