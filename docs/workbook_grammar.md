# Workbook Grammar v1

This document is the canonical machine-readable specification for workbook files.
It is the authoritative reference for the parser, renderer, and any tool that reads or
writes workbooks.

## File conventions

- Filename: `<name>.workbook.md`
- Name must match: `[A-Za-z0-9_-]+`
  (ASCII letters, digits, hyphens, underscores; at least one character)
- Encoding: UTF-8
- Line endings: LF or CRLF (parser normalises via `str.splitlines()`)

## Top-level structure

The first non-blank line of the file must be exactly:

    # Eval Workbook

The file must contain exactly the following H2 sections, each appearing at most once.
``## Continue Next Run`` is optional for backwards compatibility with workbooks
created before the continuation feature; every other section is mandatory.

| Section header            | Required                                            |
|---------------------------|-----------------------------------------------------|
| `## Continue Next Run`    | optional on parse; always emitted by the renderer   |
| `## Target`               | yes                                                 |
| `## Brief`                | yes                                                 |
| `## Directions`           | yes                                                 |
| `## Cases`                | yes                                                 |
| `## Run readiness`        | yes                                                 |
| `## Artifact references`  | yes                                                 |

Duplicate section names are a grammar error.

## ID rules

Direction IDs and case IDs must match the pattern `[A-Za-z0-9][A-Za-z0-9_-]*`:

- Must start with an ASCII letter or digit.
- May contain ASCII letters, digits, hyphens (`-`), and underscores (`_`).
- Must be unique within their respective section (duplicate IDs are a grammar error).

## `## Continue Next Run` section

Human-editable follow-up input for the next ``lightassay continue`` run.
Always rendered at the top of the workbook (after the title, before ``## Target``),
so the editable fields are impossible to miss.

Structure:

- A single short lead-in paragraph explicitly stating these fields are consumed on
  the next ``continue`` run.  Renderer emits:

      Fields below are consumed on the next `continue` run.

- Exactly three H3 blocks for the *current* iteration, in this order:

  - `### Current continuation: general instruction`
  - `### Current continuation: direction instruction`
  - `### Current continuation: case instruction`

  Each block's body is free text (may be blank — blank means "no follow-up for this
  slot").

- Zero or more *historical* continuation blocks below the current block.  Each
  historical entry is a fixed quartet of H3 headers versioned by integer:

  - `### Continuation v<n>: general instruction`
  - `### Continuation v<n>: direction instruction`
  - `### Continuation v<n>: case instruction`
  - `### Continuation v<n>: CLI message`

  Version numbers start at 1 and increment monotonically.  Historical blocks are
  never reused as active input — only the current block participates in the next
  ``continue`` run.  The ``CLI message`` slot stores the literal ``--message``
  argument that was passed to ``continue`` for that iteration (empty string when
  no ``--message`` was supplied).  Every slot is always emitted even when empty,
  so the history shows exactly which slots the human used for each iteration.

Grammar rules:

- Every H3 inside `## Continue Next Run` must match either the
  ``Current continuation: {general|direction|case} instruction`` pattern or the
  ``Continuation v<n>: {general|direction|case} instruction | CLI message``
  pattern.  Other headers are a grammar error.
- Duplicate current or historical slots (same label twice) are a grammar error.
- Parsing a workbook without this section returns an empty continuation block
  (backwards compatibility).  The renderer always emits the section, so every
  round-trip produces it.

## `## Target` section

Contains only the following H3 subsections, each exactly once:

- `### TARGET_KIND`
- `### TARGET_NAME`
- `### TARGET_LOCATOR`
- `### TARGET_BOUNDARY`
- `### TARGET_SOURCES`
- `### TARGET_NOTES`

Rules:
- No other H3 headers are allowed in `## Target`.
- `TARGET_KIND`, `TARGET_NAME`, `TARGET_LOCATOR`, and `TARGET_BOUNDARY` are free-text
  multi-line fields and may be blank in a fresh init workbook.
- `TARGET_SOURCES` is a bullet list field. Each non-blank line in the field must start
  with `- `. Empty bullet values are a grammar error.
- Duplicate source entries are a grammar error.
- `TARGET_NOTES` is optional free text and may be blank.

## `## Brief` section

Content is free text. May contain markdown sub-headers, HTML comments, and any markdown.
The parser extracts it as raw text (stripped of leading and trailing blank lines).
No structured parsing is applied to this section.

## `## Directions` section

Contains only H3 subsections. Grammar rules:

1. Exactly one `### HUMAN:global_instruction` block must be present.
2. Zero or more `### Direction: <direction_id>` blocks may be present.
3. No other H3 headers are allowed.

### `### HUMAN:global_instruction` block

    ### HUMAN:global_instruction
    <feedback text — zero or more lines>

An empty block means "no objections, approved."

### `### Direction: <direction_id>` block

    ### Direction: <direction_id>

    <direction body — free text, zero or more lines>

    **Behavior facet:** <behavior_facet>
    **Testing lens:** <testing_lens>
    **Covered user priorities:** <section_id[, ...]>
    **Source rationale:** <source-grounded rationale>

    HUMAN:instruction
    <human feedback text — zero or more lines>

Rules:
- `direction_id` must match the ID rules above.
- The following traceability lines are required and must appear exactly once:
  - `**Behavior facet:** <non-empty text>`
  - `**Testing lens:** <non-empty text>`
  - `**Covered user priorities:** <comma-separated section IDs>`
  - `**Source rationale:** <non-empty text>`
- The literal line `HUMAN:instruction` (no leading/trailing characters except optional
  trailing whitespace) is required. It marks the boundary between body and feedback.
- Everything before the first traceability line is the direction body (free text).
- Everything after the `HUMAN:instruction` line is the human feedback (may be empty).
- Absence of a `HUMAN:instruction` line in a direction block is a grammar error.

## `## Cases` section

Contains only H3 subsections. Grammar rules:

1. Exactly one `### HUMAN:global_instruction` block must be present.
2. Zero or more `### Case: <case_id>` blocks may be present.
3. No other H3 headers are allowed.

### `### HUMAN:global_instruction` block

Same structure as in `## Directions`.

### `### Case: <case_id>` block

    ### Case: <case_id>

    **Input:**
    <input text — required, must be non-empty>

    **Context:**
    <context text — optional, may be omitted entirely>

    **Notes:**
    <notes text — optional, may be omitted entirely>

    **Target directions:** <comma-separated direction IDs — required>

    **Expected behavior:**
    <expected behavior text — required, must be non-empty>

    **Behavior facet:** <behavior_facet>
    **Testing lens:** <testing_lens>
    **Covered user priorities:** <section_id[, ...]>
    **Source rationale:** <source-grounded rationale>

    HUMAN:instruction
    <human feedback text — zero or more lines>

Rules:
- `case_id` must match the ID rules above.
- Field headers are exact string matches (case-sensitive, asterisks included).
- Required fields: `**Input:**`, `**Target directions:** <ids>`, `**Expected behavior:**`.
- Required traceability fields:
  - `**Behavior facet:**`
  - `**Testing lens:**`
  - `**Covered user priorities:**`
  - `**Source rationale:**`
- Optional fields: `**Context:**`, `**Notes:**` (may be absent entirely).
- No non-blank content is allowed before the first field header. Any non-blank line
  before the first recognized field header is a grammar error.
- `**Input:**`, `**Context:**`, `**Notes:**`, and `**Expected behavior:**` are
  multi-line fields: the header is on its own line; content follows on the next lines.
- `**Target directions:**` is a single-line field: the comma-separated ID list must
  appear on the same line as the header. Format:
  `**Target directions:** dir_id1, dir_id2`
  An empty value or the header without a value is a grammar error.
- Each direction ID in `**Target directions:**` must match the ID rules above.
- Each direction ID in `**Target directions:**` must reference a direction ID that
  exists in the `## Directions` section. A reference to a non-existent direction is
  a grammar error.
- `**Input:**` and `**Expected behavior:**` must contain non-empty text.
- The literal line `HUMAN:instruction` is required in every case block. It marks the
  end of the structured fields and the start of human feedback.
- Absence of `HUMAN:instruction` is a grammar error.

### Field header exact strings

| Field                 | Header line (exact)            | Type        |
|-----------------------|--------------------------------|-------------|
| Input                 | `**Input:**`                   | multi-line  |
| Context               | `**Context:**`                 | multi-line  |
| Notes                 | `**Notes:**`                   | multi-line  |
| Target directions     | `**Target directions:** <ids>` | single-line |
| Expected behavior     | `**Expected behavior:**`       | multi-line  |
| Behavior facet        | `**Behavior facet:** <text>`   | single-line |
| Testing lens          | `**Testing lens:** <text>`     | single-line |
| Covered user priorities | `**Covered user priorities:** <ids>` | single-line |
| Source rationale      | `**Source rationale:** <text>` | single-line |

## `## Run readiness` section

    RUN_READY: yes
    READINESS_NOTE: <optional note text>

or

    RUN_READY: no
    READINESS_NOTE: <reason the workbook is not ready>

Rules:
- `RUN_READY:` is required. The value must be exactly `yes` or `no`.
- `READINESS_NOTE:` is required. The value (after the colon) may be empty at the grammar level. This permits fresh/init workbooks where no reconciliation has occurred yet. Note: the `reconcile_readiness` response contract is stricter — when the adapter sets `run_ready` to `false`, the `readiness_note` must be non-empty (see `preparation_protocol.md` and `workbook_spec.md` Run readiness contract).
- Blank lines are allowed and ignored.
- Any other non-blank content in this section is a grammar error.
- Duplicate `RUN_READY:` or duplicate `READINESS_NOTE:` lines are a grammar error.

## `## Artifact references` section

    - run: <path or empty>
    - analysis: <path or empty>
    - compare: <path or empty>

Rules:
- All three keys (`run`, `analysis`, `compare`) are required.
- Each must appear exactly once.
- Format per line: `- <key>: <value>` where value may be empty.
- Any line that does not match `- run:`, `- analysis:`, or `- compare:` (followed by
  optional whitespace and an optional value) is a grammar error.
- Blank lines are allowed and ignored.

## Error conditions

The parser raises `WorkbookParseError` for any of the following:

- File does not begin with `# Eval Workbook`
- Missing any required section
- Duplicate section names
- `## Directions`: missing `### HUMAN:global_instruction`
- `## Target`: unexpected H3 header
- `## Target`: duplicate target subsection
- `## Target`: malformed `TARGET_SOURCES` bullet list
- `## Target`: duplicate target source entry
- `## Directions`: unexpected H3 header (not `HUMAN:global_instruction` or `Direction: <id>`)
- `## Directions`: duplicate direction ID
- `## Directions`: direction ID violates ID rules
- `### Direction: <id>`: missing `HUMAN:instruction` line
- `### Direction: <id>`: missing required traceability line
- `## Cases`: missing `### HUMAN:global_instruction`
- `## Cases`: unexpected H3 header (not `HUMAN:global_instruction` or `Case: <id>`)
- `## Cases`: duplicate case ID
- `## Cases`: case ID violates ID rules
- `### Case: <id>`: missing `HUMAN:instruction` line
- `### Case: <id>`: non-blank content before first field header
- `### Case: <id>`: duplicate field header
- `### Case: <id>`: `**Target directions:**` with no value on same line
- `### Case: <id>`: missing required field (`**Input:**`, `**Target directions:**`,
  `**Expected behavior:**`, traceability fields)
- `### Case: <id>`: required field is present but empty (`**Input:**`,
  `**Expected behavior:**`)
- `### Case: <id>`: direction ID in `**Target directions:**` violates ID rules
- `### Case: <id>`: target direction ID references a direction not present in
  `## Directions`
- `## Run readiness`: missing `RUN_READY:`
- `## Run readiness`: `RUN_READY:` value is not `yes` or `no`
- `## Run readiness`: missing `READINESS_NOTE:`
- `## Run readiness`: duplicate `RUN_READY:` or `READINESS_NOTE:`
- `## Run readiness`: unexpected non-blank content
- `## Artifact references`: missing `- run:`, `- analysis:`, or `- compare:`
- `## Artifact references`: duplicate artifact reference key
- `## Artifact references`: malformed line (does not match expected format)
- `## Continue Next Run`: unexpected H3 header (not `Current continuation: ...` or
  `Continuation v<n>: {general|direction|case} instruction | CLI message`)
- `## Continue Next Run`: duplicate current continuation slot
- `## Continue Next Run`: duplicate historical continuation slot for the same version
- `## Continue Next Run`: `Continuation v<n>` version integer must be >= 1
