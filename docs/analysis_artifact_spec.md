# Analysis Artifact Specification

Version: 0.2.0

## Purpose

An analysis artifact is the output of the `analyze` command. It is a markdown
file containing LLM-generated semantic analysis of a single run artifact
(completed or failed). The tool adds a metadata header; the analysis body comes
from the semantic adapter.

## File Naming

```
analysis_{analysis_id}.md
```

Where `analysis_id` is a 12-character lowercase hex string (UUID4 prefix).

## Format

```markdown
# Analysis: {analysis_id}

- **run_id:** {run_id}
- **workflow_id:** {workflow_id}
- **analyzer_provider:** {provider}
- **analyzer_model:** {model}
- **analyzed_at:** {ISO 8601 timestamp}
- **run_artifact_path:** {path}

---

{analysis_markdown from semantic adapter}
```

### Metadata Fields

| Field | Source | Description |
|-------|--------|-------------|
| `analysis_id` | Generated | Unique ID for this analysis (UUID4 hex prefix, 12 chars). |
| `run_id` | Run artifact | The run that was analyzed. |
| `workflow_id` | Run artifact | The workflow under test. |
| `analyzer_provider` | Semantic config | LLM provider used for analysis. |
| `analyzer_model` | Semantic config | LLM model used for analysis. |
| `analyzed_at` | Generated | ISO 8601 UTC timestamp of when analysis was produced. |
| `run_artifact_path` | CLI argument | Path to the run artifact JSON file that was analyzed. |

### Analysis Body

Everything below the `---` separator and above the optional
`## Next-step recommendations` section is the analysis content produced by
the semantic adapter. The tool does not inspect, validate, or modify this
content beyond ensuring it is a non-empty string.

The analysis body is expected to contain:
- Identification of successes and failures
- Pattern recognition across cases
- Weak spot identification
- Narrative observations

However, the specific structure is determined by the semantic adapter (and the
LLM it uses), not by the tool.

### Next-step recommendations (optional, structured)

If the semantic adapter returns a non-empty `recommendations` array, the tool
appends a canonical `## Next-step recommendations` section to the artifact,
rendered below the analysis body and a horizontal rule. The schema is strict
and validated before the artifact is written:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string, non-empty | yes | Short headline of the recommendation. |
| `to_ensure` | string, non-empty | yes | Answers the "to ensure what?" question — the product reason the recommendation exists. |
| `section` | enum | yes | One of `broader_coverage`, `weak_spots`, `why_they_matter`. Any other value is a validation error. |
| `source` | enum or null | no | One of `user_intent`, `prompt_design`, `workflow_design`, `observed_behavior`. `observed_behavior` must only be used when the recommendation is actually grounded in evidence from the run artifact. |
| `detail` | string or null | no | Optional freeform follow-up line. Must be non-empty when provided. |

Rendering:

- The three sections are always emitted in the canonical order
  `broader_coverage` → `weak_spots` → `why_they_matter`. Sections without
  entries are omitted.
- Each item is rendered as `- **<title>**` followed by `To ensure: <to_ensure>`
  and, when set, a source label and the optional `detail` line.
- There is no cap on the number of recommendations. Adapters must not pad the
  list with filler; they should return only valuable entries.

## Workbook Update

After producing the analysis artifact, the `analyze` command updates the
workbook's `## Artifact references` section:

```markdown
- analysis: {path_to_analysis_artifact}
```

The workbook path is read from the run artifact's `workbook_path` field.
The workbook must exist and be parseable. If the workbook file does not exist
or cannot be parsed, the `analyze` command fails with an explicit error.
No warning-based fallback.

## Constraints

- Both completed and failed runs can be analyzed. The completed-only
  restriction applies to compare, not analysis.
- Analysis is a single-run operation. No multi-run semantics.
- The tool does not judge quality. The LLM (via the adapter) does.
- No implicit compare behavior in analysis.
- The workbook referenced by the run artifact must exist and be parseable.
