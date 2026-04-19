# Quickstart Example

Runnable end-to-end demo of `lightassay` with **zero external dependencies**.
The adapters in this directory are deterministic stubs — they stand in for
a real LLM adapter so the full `workbook → prepare → run → analyze` pipeline
executes on a clean machine. Replace them with real adapters for real
evaluations.

## What is in this directory

| File | Role |
|------|------|
| `stub_preparation_adapter.py` | Stub preparation adapter (generates directions / cases / readiness + answers the `bootstrap` operation). |
| `stub_semantic_adapter.py` | Stub semantic adapter (returns analysis markdown + structured recommendations). |
| `stub_workflow.py` | Stub workflow under test (echoes input back in the adapter JSON shape). |
| `preparation.json` | Preparation config pointing at the preparation stub. |
| `semantic.json` | Semantic config pointing at the semantic stub. |
| `workflow.json` | Workflow config pointing at the workflow stub. |

These stubs live here, in `examples/`, specifically because they are a
demo — the shipped `lightassay.builtin_adapters` does not register a
`stub` agent, so real users never see simulated behavior by default.

## Running the explicit flow

From the repository root:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cd examples/quickstart

lightassay workbook --output-dir .
# Fill workbook1.workbook.md (target + brief)

lightassay prepare-directions workbook1.workbook.md --preparation-config preparation.json
lightassay prepare-cases workbook1.workbook.md --preparation-config preparation.json
lightassay prepare-readiness workbook1.workbook.md --preparation-config preparation.json

lightassay run workbook1.workbook.md --workflow-config workflow.json --output-dir .
lightassay analyze run_<id>.json --semantic-config semantic.json --output-dir .
```

## Running the self-serve flow

`lightassay quickstart` drives bootstrap → preparation → run → analysis
from one plain-language message. It needs the bootstrap adapter to resolve
a real executable target; the stub workflow in this directory is a
subprocess-adapter-shaped script, so `quickstart` against it only makes
sense when you are wiring it to your own `python-callable` handler or a
real agent (`--agent claude-cli` / `--agent codex-cli`). See
[`README.md`](../../README.md) in the repo root for the self-serve
examples, and [`docs/preparation_protocol.md`](../../docs/preparation_protocol.md)
for the bootstrap contract the stub adapter here implements.

The explicit flow above is the right path for exercising `lightassay`
end-to-end against this example without any external CLI.

## Replacing the stubs

- **Real LLM**: point `preparation.json` / `semantic.json` at your own
  adapter executable, or drop the `preparation-config` / `semantic-config`
  arguments and use `--agent claude-cli` / `--agent codex-cli` (requires
  the respective CLI installed and signed in).
- **Real workflow under test**: replace `workflow.json` with a config that
  targets a `python-callable`, `http`, or `command` driver pointing at your
  workflow.

See `docs/preparation_protocol.md`, `docs/semantic_adapter_spec.md`,
`docs/workflow_config_spec.md`, and `docs/adapter_pack_spec.md` for the
full contracts.
