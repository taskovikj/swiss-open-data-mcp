# Contributing

Use Python 3.11+ and uv. Install the exact reviewed dependency graph:

```sh
uv sync --frozen --extra dev
```

Before submitting a change:

```sh
uv run --frozen --extra dev python -m ruff check src tests
uv run --frozen --extra dev python -m pytest -q
uv run --frozen --extra dev python -m build
uv run --frozen --extra dev python scripts/generate_tool_reference.py --check
```

After changing tool names, descriptions, parameters, or annotations, regenerate the
reference with `uv run python scripts/generate_tool_reference.py` and commit it.
For dependency changes, update pyproject.toml, run `uv lock`, and commit both files.

Keep MCP tool names backward compatible. Describe effects accurately in annotations.
Use deterministic calculations and preserve source metadata. Bind SQL values as
parameters, validate output table names, and quote source column identifiers.
Use temporary files and atomic replacement for runtime state. Unit tests should use
isolated workspaces and mocked publisher APIs; test transports with real local clients.

Open a pull request with the concrete problem, resulting behavior, and validation.
Use your own Git identity. Do not add unrelated co-author trailers.
