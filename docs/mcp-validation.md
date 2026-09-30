# MCP validation

SwissDataMCP uses the official Python MCP SDK 2.x (`MCPServer`). Protocol mechanics
are delegated to the SDK, including the 2026-07-28 stateless protocol and older client
negotiation. The application does not implement a parallel JSON-RPC stack.

The automated suite checks:

- Public SDK discovery of tools, resources, templates, and prompts.
- Structured results, row output schemas, effect annotations, and cache hints.
- A real subprocess over stdio, with an isolated workspace.
- Real HTTP clients using 2026-07-28 and legacy negotiation.
- Host/Origin rejection and the HTTP request-body limit.
- Export resource links that resolve to the actual manifest and hash.
- Interrupted, empty, oversized, and redirected downloads and corrupt cache recovery.
- Catalog retry/cache behavior, malformed responses, language selection, and page metadata.
- SQL name validation, embedded quotes in source columns, null filters, and pagination.
- CSV/JSON/Parquet export round trips and truncation metadata.
- Failed ingestion preserving the previous table and concurrent session saves preserving every update.

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev python -m pytest -q
uv run --frozen --extra dev python -m ruff check src tests
uv run --frozen --extra dev python -m build
uv run --frozen --extra dev python scripts/generate_tool_reference.py --check
```

CI covers Python 3.11, 3.12, and 3.14 on Windows and Linux using the committed lockfile.
Publisher behavior is mocked in regression tests; live catalog access is a separate
smoke check and may fail during publisher downtime. Passing tests are evidence for
the covered behavior, not a claim of universal client certification or zero defects.

Protocol references: [official SDK](https://github.com/modelcontextprotocol/python-sdk),
[v2 migration](https://py.sdk.modelcontextprotocol.io/migration/), and
[2026 specification](https://modelcontextprotocol.io/specification/2026-07-28).
