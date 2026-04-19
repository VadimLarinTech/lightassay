# Eval Workbook

## Target

### TARGET_KIND
workflow

### TARGET_NAME
text_echo

### TARGET_LOCATOR
tests.fixtures.adapter_echo

### TARGET_BOUNDARY
text echo workflow boundary

### TARGET_SOURCES
- tests/fixtures/adapter_echo.py

### TARGET_NOTES

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

### Direction: edge-cases

Test boundary inputs.

**Behavior facet:** edge_case_behavior
**Testing lens:** boundary_and_negative
**Covered user priorities:** freeform_brief
**Source rationale:** Grounded in neighboring explicit source behavior.

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

### Case: edge-empty

**Input:**
A single word.

**Context:**
Minimal context for testing.

**Target directions:** correctness, edge-cases

**Expected behavior:**
Should handle minimal input correctly.

**Behavior facet:** edge_case_behavior
**Testing lens:** boundary_and_negative
**Covered user priorities:** freeform_brief
**Source rationale:** Grounded in neighboring explicit source behavior.

HUMAN:instruction

## Run readiness
RUN_READY: yes
READINESS_NOTE: All cases are ready.

## Artifact references
- run:
- analysis:
- compare:
