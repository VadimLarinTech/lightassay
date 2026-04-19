# Compare Artifact Specification

Version: 0.2.0

## Purpose

A compare artifact is the output of the `compare` command. It is a markdown
file containing LLM-generated semantic comparison of two or more completed run
artifacts. The tool adds a metadata header; the comparison body comes from the
semantic adapter.

Compare is always a separate, explicitly initiated operation. It is never part
of a run.

## Preconditions

- At least 2 run artifacts are required.
- All run artifacts must have status `"completed"`. Failed runs (status
  `"failed"`) are strictly rejected with an explicit error.

## File Naming

```
compare_{compare_id}.md
```

Where `compare_id` is a 12-character lowercase hex string (UUID4 prefix).

## Format

```markdown
# Compare: {compare_id}

- **run_ids:** {run_id_1}, {run_id_2}[, ...]
- **comparer_provider:** {provider}
- **comparer_model:** {model}
- **compared_at:** {ISO 8601 timestamp}
- **run_artifact_paths:** {path_1}, {path_2}[, ...]

---

{compare_markdown from semantic adapter}
```

### Metadata Fields

| Field | Source | Description |
|-------|--------|-------------|
| `compare_id` | Generated | Unique ID for this comparison (UUID4 hex prefix, 12 chars). |
| `run_ids` | Run artifacts | Comma-separated list of run IDs that were compared. |
| `comparer_provider` | Semantic config | LLM provider used for comparison. |
| `comparer_model` | Semantic config | LLM model used for comparison. |
| `compared_at` | Generated | ISO 8601 UTC timestamp of when comparison was produced. |
| `run_artifact_paths` | CLI arguments | Comma-separated paths to the run artifact JSON files. |

### Compare Body

Everything below the `---` separator is the comparison content produced by the
semantic adapter. The tool does not inspect, validate, or modify this content
beyond ensuring it is a non-empty string.

The compare body is expected to address:
- Which runs were compared and what configuration differences are significant
- Where one variant showed stronger behavior quality
- Different failure patterns across runs
- Raw fact differences (tokens, latency, etc.)
- Where conclusions are clear vs. where uncertainty remains
- Recommendations where they legitimately follow

However, the specific structure is determined by the semantic adapter (and the
LLM it uses), not by the tool.

## Workbook Update

In v1, the `compare` command does **not** automatically update any workbook's
`## Artifact references` section. This is a conscious v1 decision:

- Compare operates across runs that may originate from different workbooks.
- Automatic workbook update would require choosing which workbook(s) to update,
  which introduces ambiguity the tool should not resolve silently.
- The user can manually update the workbook's `- compare:` reference if desired.

This behavior is explicitly documented and tested.

## Constraints

- Only completed runs are accepted. The completed-only restriction applies to
  compare, not analysis.
- Compare is a multi-run operation. Single-run comparison is not meaningful and
  is rejected.
- The tool does not judge quality. The LLM (via the adapter) does.
- Compare is never initiated from within a run. It is always a separate explicit
  step.
