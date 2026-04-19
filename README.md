# lightassay

`lightassay` is a simple first way to test an LLM workflow.

- You describe what worries you in plain language.
- Your agent, using the LLM access you already have, helps turn that into directions, test cases, and analysis.
- You do not need to build a formal eval system first.
- The code runs the workflow and records raw facts.
- The results are analyzed in terms that make sense to you.

## Install

Inside an activated virtual environment:

```bash
python -m venv .venv && source .venv/bin/activate
pip install lightassay
```

Or install the CLI with `pipx`:

```bash
pipx install lightassay
```

### First-time setup (required once)

Run:

```bash
lightassay init
```

This is the mandatory first-run step after installation.

## Quick start

Once `init` is done:

```bash
lightassay quickstart \
  --message "Check myapp.pipeline.run. I care about obvious mistakes, over-correction, and preserving names and numbers." \
  --target "myapp.pipeline.run"
```

This creates the workbook, runs the first pass, and writes the analysis artifact.

### Follow-up `continue`

```bash
lightassay continue --compare-previous
```

Use this after editing the workbook or adding a follow-up `--message`.

### Manual workbook path

If you want an empty workbook first:

```bash
lightassay workbook
```

For the explicit stage-by-stage flow, see [`docs/quickstart.md`](docs/quickstart.md).
For a runnable example, see [`examples/quickstart/`](examples/quickstart/).

---

## Documentation

- [`quickstart.md`](docs/quickstart.md) — explicit stage-by-stage flow
- [`workbook_spec.md`](docs/workbook_spec.md) — workbook structure
- [`workflow_config_spec.md`](docs/workflow_config_spec.md) — workflow execution config
- [`semantic_adapter_spec.md`](docs/semantic_adapter_spec.md) — analysis and compare config
- [`code_architecture.md`](docs/code_architecture.md) — code structure

---

## License

MIT — see [`LICENSE`](LICENSE).
