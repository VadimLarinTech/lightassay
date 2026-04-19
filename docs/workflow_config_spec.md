# Workflow Config Spec (v1)

## Purpose

A workflow config file tells the `run` command how to call the workflow under test.
It is a JSON file that the user writes once per workflow variant and passes via `--workflow-config`.

## Required fields

| Field | Type | Description |
|-------|------|-------------|
| `workflow_id` | string, non-empty | Stable human-readable identifier for this workflow variant (e.g. `"sentence-checker-gpt4o"`) |

Plus exactly one of:

| Field | Type | Description |
|-------|------|-------------|
| `adapter` | string, non-empty | Path to the adapter executable (absolute or relative to config file location) |
| `driver` | object | First-party adapter driver config (see below and `adapter_pack_spec.md`) |

Both `adapter` and `driver` present, or both absent, is a validation error.

## Legacy adapter example

```json
{
  "workflow_id": "sentence-checker-gpt4o",
  "adapter": "./adapters/sentence_checker.py"
}
```

## Optional LLM metadata

When the workflow under test is itself an LLM call, the config may also
include model metadata. This metadata is optional and separate from the
execution binding.

Legacy top-level form:

```json
{
  "workflow_id": "sentence-checker-gpt4o",
  "provider": "openai",
  "model": "gpt-4o",
  "adapter": "./adapters/sentence_checker.py"
}
```

Preferred nested form:

```json
{
  "workflow_id": "sentence-checker-gpt4o",
  "llm_metadata": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "adapter": "./adapters/sentence_checker.py"
}
```

## First-party driver example

```json
{
  "workflow_id": "sentence-checker-gpt4o",
  "driver": {
    "type": "python-callable",
    "module": "my_adapters.sentence_checker",
    "function": "handle_request"
  }
}
```

See [`adapter_pack_spec.md`](adapter_pack_spec.md) for full driver type documentation.

### Supported driver types

| Type | Required fields | Optional fields |
|------|----------------|-----------------|
| `python-callable` | `module`, `function` | — |
| `http` | `url`, `method` | `headers`, `timeout_seconds` |
| `command` | `command` | — |

## Adapter path resolution

The `adapter` path is resolved relative to the directory containing the workflow config file.
If the path is absolute, it is used as-is.

## Validation rules

- File must be valid JSON.
- `workflow_id` must be present and a non-empty string.
- Exactly one of `adapter` or `driver` must be present.
- `adapter`, if present, must be a non-empty string.
- `driver`, if present, must be a valid driver config object with a known `type`.
- `provider` / `model`, when present at top level, must be non-empty strings.
- `llm_metadata`, when present, must be an object containing only `provider`
  and/or `model`, each as a non-empty string.
- No additional keys beyond `workflow_id`, `provider`, `model`,
  `llm_metadata`, `adapter`, `driver` are allowed.
- If any rule is violated, the tool raises an explicit configuration error and refuses to proceed.

## Runtime semantics

All config fields participate in run execution:

- **`adapter`** — resolved to an absolute path (relative to config file directory) and used as the subprocess executable for each case.
- **`driver`** ��� dispatched to the appropriate first-party driver module for each case. For `command` drivers, the subprocess runs with `cwd` set to the directory containing this config file (**config-origin semantics**). This means relative paths in the command array resolve against the config file location, not the caller's cwd — matching the resolution behavior of the legacy `adapter` path.
- **`workflow_id`** — included in every adapter request JSON.
- **`provider`**, **`model`** — included only when LLM metadata is present.
  They are metadata about the workflow under test, not part of the
  execution binding itself.

`provider` / `model` are not required for plain Python-callable / HTTP /
command workflows. The config should not invent placeholder values just
to satisfy a schema.

## SHA-256

The `workflow_config_sha256` field in the run artifact is the hex-encoded SHA-256 digest of the raw bytes of the workflow config file at the moment the run starts.
