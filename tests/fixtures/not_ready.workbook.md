# Eval Workbook

## Target

### TARGET_KIND

workflow

### TARGET_NAME

text_echo

### TARGET_LOCATOR

tests.fixtures.adapter_echo

### TARGET_BOUNDARY

text echo adapter boundary

### TARGET_SOURCES

- tests/fixtures/adapter_echo.py

### TARGET_NOTES

Minimal fixture for not-ready run-path validation.

## Brief

Test the text echo workflow.

## Directions

### HUMAN:global_instruction

### Direction: correctness

Verify output correctness.

**Behavior facet:** core_output_behavior
**Testing lens:** positive_and_regression
**Covered user priorities:** freeform_brief
**Source rationale:** Grounded in the explicit target source.

HUMAN:instruction

## Cases

### HUMAN:global_instruction

### Case: simple-echo

**Input:**
Hello world

**Target directions:** correctness

**Expected behavior:**
Should echo the input text back.

**Behavior facet:** core_output_behavior
**Testing lens:** positive_and_regression
**Covered user priorities:** freeform_brief
**Source rationale:** Grounded in the explicit target source.

HUMAN:instruction

## Run readiness
RUN_READY: no
READINESS_NOTE: Cases need human review.

## Artifact references
- run:
- analysis:
- compare:
