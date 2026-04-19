"""Subprocess-based semantic analyzer implementing the semantic adapter protocol.

See docs/semantic_adapter_spec.md for the full specification.
No fallback, no best-effort recovery.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from ._subprocess_capture import run_text_subprocess
from .errors import AnalysisError
from .run_artifact_io import run_artifact_to_dict
from .run_models import RunArtifact
from .semantic_config import SemanticConfig

_ALLOWED_RECOMMENDATION_SECTIONS = {
    "broader_coverage",
    "weak_spots",
    "why_they_matter",
}
_ALLOWED_RECOMMENDATION_SOURCES = {
    "user_intent",
    "prompt_design",
    "workflow_design",
    "observed_behavior",
}


def execute_analysis(
    run_artifact: RunArtifact,
    run_artifact_path: str,
    semantic_config: SemanticConfig,
    *,
    analysis_profile: str | None = None,
    analysis_context: dict | None = None,
) -> tuple[str, str]:
    """Execute semantic analysis: call the adapter, return the analysis markdown artifact.

    Accepts run artifacts with any status (completed or failed).
    The completed-only restriction applies to compare, not analysis.

    Returns the complete analysis artifact as a markdown string
    (metadata header + adapter analysis body + structured next-step
    recommendations when the adapter returns them).

    Raises AnalysisError on any protocol violation.
    """
    command = semantic_config.invocation()

    # File-backed adapters: verify path is executable before the
    # subprocess call.  Command-backed adapters (built-in backends)
    # skip this and rely on subprocess to surface startup errors.
    if not semantic_config.command:
        adapter = semantic_config.adapter
        if not os.path.exists(adapter):
            raise AnalysisError(f"Semantic adapter not found: {adapter!r}")
        if not os.access(adapter, os.X_OK):
            raise AnalysisError(f"Semantic adapter not executable: {adapter!r}")

    # Build request payload.
    request_data: dict = {
        "operation": "analyze",
        "run_artifact": run_artifact_to_dict(run_artifact),
    }
    if analysis_profile is not None:
        request_data["analysis_profile"] = analysis_profile
    if analysis_context is not None:
        request_data["analysis_context"] = analysis_context
    request_data["recommendation_schema"] = {
        "explicit_to_ensure_what": True,
        "sections": sorted(_ALLOWED_RECOMMENDATION_SECTIONS),
        "sources": sorted(_ALLOWED_RECOMMENDATION_SOURCES),
        "no_cap": True,
    }
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
        raise AnalysisError(f"Semantic adapter not found: {command[0]!r}") from None
    except PermissionError:
        raise AnalysisError(f"Semantic adapter not executable: {command[0]!r}") from None

    if result.returncode != 0:
        raise AnalysisError(
            f"Semantic adapter exited with code {result.returncode}: "
            f"{(result.stderr or '').strip()[:400]}"
        )

    # Parse stdout as JSON.
    stdout = result.stdout
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        raise AnalysisError("Semantic adapter stdout is not valid JSON") from None

    if not isinstance(response, dict):
        raise AnalysisError(
            f"Semantic adapter response must be a JSON object, got {type(response).__name__}"
        )

    # Validate required field.
    if "analysis_markdown" not in response:
        raise AnalysisError("Semantic adapter response missing required field: 'analysis_markdown'")

    analysis_markdown = response["analysis_markdown"]

    if not isinstance(analysis_markdown, str):
        raise AnalysisError(
            "Semantic adapter response field 'analysis_markdown' must be a string, "
            f"got {type(analysis_markdown).__name__}"
        )

    if not analysis_markdown.strip():
        raise AnalysisError("Semantic adapter response field 'analysis_markdown' must be non-empty")

    recommendations = _validate_recommendations(response.get("recommendations"))

    # Build complete analysis artifact.
    analysis_id = uuid.uuid4().hex[:12]
    analyzed_at = datetime.now(timezone.utc).isoformat()

    artifact_text = _render_analysis_artifact(
        analysis_id=analysis_id,
        run_id=run_artifact.run_id,
        workflow_id=run_artifact.workflow_id,
        analyzer_provider=semantic_config.provider,
        analyzer_model=semantic_config.model,
        analyzed_at=analyzed_at,
        run_artifact_path=run_artifact_path,
        analysis_body=analysis_markdown,
        recommendations=recommendations,
    )

    return artifact_text, analysis_id


def _validate_recommendations(payload) -> list[dict]:
    """Validate the optional ``recommendations`` array from the adapter.

    Each recommendation must carry an explicit ``to_ensure`` reason —
    the "to ensure what?" principle is the contract.
    """
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise AnalysisError(
            "Semantic adapter response field 'recommendations' must be a list or null"
        )

    results: list[dict] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise AnalysisError(
                f"Semantic adapter response: recommendations[{index}] must be an object"
            )
        for field_name in ("title", "to_ensure", "section"):
            if field_name not in item:
                raise AnalysisError(
                    f"Semantic adapter response: recommendations[{index}] "
                    f"missing required field: {field_name!r}"
                )
            value = item[field_name]
            if not isinstance(value, str) or not value.strip():
                raise AnalysisError(
                    f"Semantic adapter response: recommendations[{index}].{field_name} "
                    "must be a non-empty string"
                )
        section = item["section"].strip()
        if section not in _ALLOWED_RECOMMENDATION_SECTIONS:
            raise AnalysisError(
                f"Semantic adapter response: recommendations[{index}].section "
                f"must be one of {sorted(_ALLOWED_RECOMMENDATION_SECTIONS)}, "
                f"got {section!r}"
            )
        source = item.get("source")
        if source is not None:
            if not isinstance(source, str) or source.strip() not in _ALLOWED_RECOMMENDATION_SOURCES:
                raise AnalysisError(
                    f"Semantic adapter response: recommendations[{index}].source "
                    f"must be null or one of {sorted(_ALLOWED_RECOMMENDATION_SOURCES)}"
                )
            source = source.strip()
        detail = item.get("detail")
        if detail is not None and (not isinstance(detail, str) or not detail.strip()):
            raise AnalysisError(
                f"Semantic adapter response: recommendations[{index}].detail "
                "must be null or a non-empty string"
            )
        results.append(
            {
                "title": item["title"].strip(),
                "to_ensure": item["to_ensure"].strip(),
                "section": section,
                "source": source,
                "detail": detail.strip() if detail else None,
            }
        )
    return results


_RECOMMENDATION_SECTION_HEADERS = {
    "broader_coverage": "Important next directions for broader coverage",
    "weak_spots": "If you want to probe weak spots next",
    "why_they_matter": "Why these directions matter",
}


_RECOMMENDATION_SOURCE_LABELS = {
    "user_intent": "Based on original user intent",
    "prompt_design": "Based on prompt/workflow design",
    "workflow_design": "Based on prompt/workflow design",
    "observed_behavior": "Based on observed run behavior",
}


def _render_recommendations(recommendations: list[dict]) -> list[str]:
    if not recommendations:
        return []

    # Preserve the adapter-declared order inside each section and keep
    # the canonical section ordering.
    grouped: dict[str, list[dict]] = {key: [] for key in _RECOMMENDATION_SECTION_HEADERS}
    for rec in recommendations:
        grouped[rec["section"]].append(rec)

    lines = ["", "---", "", "## Next-step recommendations", ""]
    for section_key, header in _RECOMMENDATION_SECTION_HEADERS.items():
        items = grouped[section_key]
        if not items:
            continue
        lines.append(f"### {header}")
        lines.append("")
        for item in items:
            lines.append(f"- **{item['title']}**")
            lines.append(f"  - To ensure: {item['to_ensure']}")
            if item.get("source"):
                lines.append(
                    f"  - {_RECOMMENDATION_SOURCE_LABELS.get(item['source'], item['source'])}"
                )
            if item.get("detail"):
                lines.append(f"  - {item['detail']}")
        lines.append("")
    return lines


def _render_analysis_artifact(
    *,
    analysis_id: str,
    run_id: str,
    workflow_id: str,
    analyzer_provider: str,
    analyzer_model: str,
    analyzed_at: str,
    run_artifact_path: str,
    analysis_body: str,
    recommendations: list[dict] | None = None,
) -> str:
    """Render the complete analysis artifact markdown."""
    lines = [
        f"# Analysis: {analysis_id}",
        "",
        f"- **run_id:** {run_id}",
        f"- **workflow_id:** {workflow_id}",
        f"- **analyzer_provider:** {analyzer_provider}",
        f"- **analyzer_model:** {analyzer_model}",
        f"- **analyzed_at:** {analyzed_at}",
        f"- **run_artifact_path:** {run_artifact_path}",
        "",
        "---",
        "",
        analysis_body,
        "",
    ]
    lines.extend(_render_recommendations(recommendations or []))
    return "\n".join(lines)
