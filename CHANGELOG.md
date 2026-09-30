# Changelog

## 0.2.0 — 2026-09-30

### MCP integration

- Migrate to official MCP Python SDK 2.x and bound the dependency to `<3`.
  Fresh installations previously selected 2.x while importing the removed FastMCP API.
- Add local Streamable HTTP with stateless mode, Host/Origin checks, and body limits.
- Add explicit tool titles and read/write, destructive, idempotence, and network hints.
- Add cache hints for capability lists and private, immediately stale resource reads.
- Add session, dataset, and export resource templates, session summaries, and a quality-audit prompt.
- Add paginated queries with a typed output schema and CSV/JSON/Parquet exports with resource links.

### Reliability

- Download to temporary files, publish only completed files, and verify cache hashes.
- Validate public destinations and every redirect; reject credentials and HTTPS downgrades.
- Add download refresh, bounded catalog retries, and a five-minute, 128-entry metadata cache.
- Correct search page metadata and localized titles; label advanced filters as page-scoped.
- Validate table names, protect internal metadata, and quote source column identifiers.
- Match null filters correctly and serialize non-finite values as JSON null.
- Stop silently skipping malformed rows; transact ingestion with its registry update.
- Serialize database access and session mutations within one process; save manifests atomically.
- Record source SHA-256 hashes and validate runtime limits.

### Maintenance

- Add a cross-platform dependency lock and Windows/Linux CI matrix.
- Add transport, failure-path, query, export, and concurrency regression tests.
- Replace SDK-private introspection tests with public client calls.
- Rewrite setup/validation guidance and generate a tool reference checked by CI.

### Upgrade notes

- Python 3.11+ remains required. Use `uv sync --frozen --extra dev`, or reinstall with pip.
- Existing stdio client configuration remains valid. HTTP is an optional local command.
- Existing downloads without completion receipts are downloaded again on their next load.
- Table names now require a letter/underscore followed by letters, digits, or underscores,
  up to 128 characters. The `swissdatamcp_` prefix is reserved.
- Malformed CSV/JSON may now fail ingestion where 0.1 silently omitted rows.
- Use one server process per workspace. Existing local runtime data is not deleted.

## 0.1.0

Initial Swiss data discovery, local analytics, dashboards, and citation workflows.
