# Contributing

Thanks for your interest in `lightassay`. This project is small and
intentionally kept that way — runtime code has zero third-party dependencies
and lints/tests use only `ruff` and `unittest` from stdlib.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install ruff build
```

## Running tests

```bash
python -m unittest discover -s tests
```

The test suite is stdlib `unittest` (not `pytest`). All tests must stay
deterministic and offline — HTTP tests use a local in-process server.

## Linting and formatting

```bash
ruff check .
ruff format --check .
```

Both must be clean before a PR lands. CI runs the same commands across
Python 3.9, 3.11, and 3.13.

## Opening a pull request

1. Fork the repository and create a feature branch.
2. Keep the change focused — no drive-by refactors.
3. Add or adjust tests for any behavior change.
4. Run the linter and the test suite locally.
5. Open the PR against `main`. Describe the motivation, the change, and
   any API implications. Reference an issue if there is one.

The public API surface is the set of names exported from
`lightassay.__all__`. Changes to that surface require a changelog entry and
a clear note in the PR description.
