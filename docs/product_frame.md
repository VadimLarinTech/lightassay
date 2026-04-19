# Product Frame

## What this project is

`lightassay` is a standalone open-source tool that turns a user's testing intention into a structured evaluation process for applied LLM workflows.

It is **not** part of any specific application. It works with any LLM workflow the user wants to test or compare.

## The problem it solves

When you have an LLM workflow in production or development, you need a way to:
- systematically test that it behaves as intended
- compare different models, providers, or workflow variants
- do this without writing test code or relying on coded quality metrics

Today, most practitioners do this ad-hoc: they run prompts manually, compare outputs in their head, and maintain informal notes. This project makes that process structured, repeatable, and agent-friendly.

## What it builds

A lightweight tool that guides the user through:

```
intention → directions → cases → run → analysis → compare
```

- **intention**: the human describes what they want to test in a guided brief
- **directions**: an LLM expands the brief into testing directions (behavior obligations × test lenses)
- **cases**: an LLM builds concrete test cases from directions and human feedback
- **run**: code executes the workflow under test, saves raw facts per case
- **analysis**: an LLM produces a semantic analysis of the run
- **compare**: an LLM compares two or more completed runs

## What it is not

- Not a test framework with coded quality assertions
- Not a formal metric platform
- Not an API-key-first SaaS tool
- Not a tool for stage-level pipeline testing (future direction, not v1)
- Not a history database of all past evaluations
- Not a tool for any single application or domain

## Defining principles

1. Human is the source of truth on intent, constraints, and final interpretation.
2. LLM does semantic reasoning: directions, cases, analysis, compare.
3. Code only orchestrates: guides flow, calls workflow, saves artifacts, measures raw facts.
4. Code never judges quality. Code never makes semantic decisions.
5. File-based and agent-friendly: workbook = markdown, run artifact = JSON, analysis = markdown, compare = markdown.
6. No fallbacks, no guessed values, no auto-fix. Ambiguity stops the process.
7. AI-native: designed for CLI and agent-driven use from day one.
8. Each run is independent. Compare is a separate operation on completed runs.

## Non-goals for v1

- Stage-level testing of pipeline internals
- Partial run / resume
- Coded quality-check mechanics
- Formal universal quality metrics as a core feature
- Global container for all evaluations across projects
- Heavy workbook versioning system
- Full history of all runs with snapshots
- UI layer
- API-key-first framing as the primary onboarding path
- Compare-inside-run (compare is always a separate step)
