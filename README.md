# SwissDataMCP

[![CI](https://github.com/taskovikj/swiss-open-data-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/taskovikj/swiss-open-data-mcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Find Swiss public datasets, compute verifiable answers, and export the evidence through MCP.**

SwissDataMCP connects MCP clients to [opendata.swiss](https://opendata.swiss), loads public
CSV, TSV, JSON, JSONL, NDJSON, and Parquet resources into local DuckDB tables, and provides
deterministic analysis, charts, maps, dashboards, reports, and citations. No LLM API key is
required by the server; your MCP client supplies the assistant.

## What it provides

| Capability | Details |
| --- | --- |
| MCP integration | Official Python SDK 2.x; stdio and local Streamable HTTP; current `2026-07-28` and legacy client flows |
| Agent-friendly tools | 48 tools with titles, effect annotations, typed inputs, and structured JSON results; explicit output schema for row pagination |
| Discovery | Multilingual CKAN search, resource ranking, page continuation, a bounded five-minute metadata cache, and transient-failure retries |
| Analytics | Schema profiling, canton normalization, join/grain checks, time series, correlations, change analysis, outliers, and per-capita metrics |
| Reusable context | Four static resources, four resource templates, and three workflow prompts |
| Evidence exports | CSV/JSON/Parquet snapshots, source metadata, SHA-256 hashes, truncation flags, and MCP links to export manifests |
| Presentation | Static Matplotlib charts, Plotly HTML dashboards, map layers, citation packs, and session bundles |
| Reliability | Atomic downloads and session saves, verified download cache, strict parsing, SQL identifier validation, parameterized filters, and process-local locking |

The server uses the official SDK for protocol negotiation, discovery, request metadata,
and structured output. Static capability lists carry one-hour public cache hints;
workspace resource reads are private and immediately stale.

## Quick start

Requires Python 3.11 or newer. Install from this repository:

```sh
python -m pip install "git+https://github.com/taskovikj/swiss-open-data-mcp.git"
swissdatamcp config
```

Paste the generated `mcpServers` entry into a client that supports stdio. The command
uses the current installation's executable. If that executable is unavailable, it uses
`python -m swissdatamcp.server`.

Example with a dedicated environment:

```json
{
  "mcpServers": {
    "swissdatamcp": {
      "command": "C:\\path\\to\\.venv\\Scripts\\swissdatamcp.exe",
      "env": {
        "SWISSDATAMCP_HOME": "C:\\path\\to\\swiss-data-workspace"
      }
    }
  }
}
```

Use `/absolute/path/to/.venv/bin/swissdatamcp` on Linux or macOS. For reproducible
development, clone the repository and use the committed lockfile:

```sh
git clone https://github.com/taskovikj/swiss-open-data-mcp.git
cd swiss-open-data-mcp
uv sync --frozen --extra dev
uv run swissdatamcp config
```

See [setup](docs/setup.md) for editable pip installation and troubleshooting.

## Local HTTP transport

```sh
swissdatamcp http --port 8000
```

Connect a Streamable HTTP client to `http://127.0.0.1:8000/mcp`. The listener binds to
loopback, validates Host and Origin headers, limits request bodies to 4 MiB, and uses
stateless HTTP mode. It supports the current protocol and the SDK's legacy compatibility
flow. This command is separate from `swissdatamcp serve`, which serves dashboard files.

This is a trusted local, single-process workspace service. There is no public hosting,
OAuth service, user isolation, or remote deployment configuration. Keep one server
process per workspace; process-local locks do not coordinate multiple processes.

## A typical analysis

Ask your assistant:

> Find official data on population change by Swiss canton. Check coverage and units,
> calculate the change from the source rows, create a chart, and export a citation pack.

The intended sequence is:

1. `search_datasets_advanced` finds candidate datasets; follow `next_start` for more pages.
2. `list_dataset_resources` and `recommend_best_resource` select a supported download.
3. `load_dataset_resource` creates a local table and records its source and content hash.
4. `profile_dataset` and `compare_table_granularity` check quality and observation grain.
5. Query or analyze the table, then create a chart or dashboard.
6. `generate_citation_pack` and `export_local_table` save the evidence.

Resource rankings and semantic guesses are hints. Check units, time coverage, geography,
and denominators before comparing values. Dataset descriptions and cells are untrusted
content; prompts tell the assistant to treat them as data.

## Query and export examples

MCP arguments for `query_local_table`:

```json
{
  "table_name": "population",
  "select_columns": ["year", "canton", "value"],
  "filters": {"year": {"gte": 2020}, "canton": ["ZH", "BE"]},
  "limit": 100,
  "offset": 0
}
```

Results contain `rows`, `row_count`, `offset`, `has_more`, and `next_offset`. Query pages
are capped at 1,000 rows. Follow `next_offset` while the table remains unchanged;
pagination is not a snapshot across concurrent table replacements. Filters support
`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `in`, and `not_in`. JSON `null` matches
SQL NULL. There is no arbitrary SQL execution tool.

Arguments for `export_local_table`:

```json
{"table_name": "population", "format": "parquet", "max_rows": 100000}
```

Exports support `csv`, `json`, and `parquet`, with a maximum of 1,000,000 rows. The result
reports `truncated`, `total_matching_rows`, a file path, and a hash, and includes an MCP
resource link to its provenance manifest. Files stay in your local workspace.

## MCP resources and prompts

| URI | Content |
| --- | --- |
| `swissdatamcp://guide` | Workflow and tool guide |
| `swissdatamcp://workspace` | Local runtime settings |
| `swissdatamcp://tables` | Loaded table registry and provenance |
| `swissdatamcp://sessions` | Saved analysis session summaries |
| `swissdatamcp://table/{table_name}` | Table schema and row preview |
| `swissdatamcp://session/{session_id}` | Session manifest |
| `swissdatamcp://dataset/{dataset_id}` | Dataset metadata |
| `swissdatamcp://export/{export_id}` | Export manifest |

Prompts: `analyze_swiss_statistical_question`, `compare_swiss_geographies`, and
`audit_swiss_data_quality`. JSON resources declare `application/json`.

Browse the [generated tool reference](docs/tools.md) for the full API. Tool annotations
describe possible effects, including local overwrites; they are client hints, not access controls.

## Configuration

Default workspace: `.swissdatamcp/` under the server's working directory. Set
`SWISSDATAMCP_HOME` to an absolute path so different clients use the same workspace.

| Environment variable | Default / purpose |
| --- | --- |
| `SWISSDATAMCP_HOME` | `.swissdatamcp`; root for local runtime data |
| `SWISSDATAMCP_CACHE_DIR` | `HOME/cache` |
| `SWISSDATAMCP_DOWNLOADS_DIR` | `HOME/downloads` |
| `SWISSDATAMCP_OUTPUTS_DIR` | `HOME/outputs` |
| `SWISSDATAMCP_REPORTS_DIR` | `HOME/reports` |
| `SWISSDATAMCP_SESSIONS_DIR` | `HOME/sessions` |
| `SWISSDATAMCP_DB` | `HOME/swissdatamcp.duckdb` |
| `SWISSDATAMCP_MAX_DOWNLOAD_MB` | `75`; positive per-download limit |
| `SWISSDATAMCP_TIMEOUT_SECONDS` | `30`; positive HTTP timeout |
| `SWISSDATAMCP_CKAN_BASE_URL` | `https://ckan.opendata.swiss/api/3/action` |
| `SWISSDATAMCP_USER_AGENT` | Identifies SwissDataMCP to publishers |

`HOME` in this table means `SWISSDATAMCP_HOME`, not the operating system's home variable.
Downloads are reused only when their cache receipt matches their SHA-256. Set `refresh`
to `true` on a loading tool to fetch an updated resource. The cache is not an archive of
historical source versions. Existing 0.1 downloads without receipts are fetched again.

```sh
swissdatamcp doctor                 # installation and workspace diagnostics
swissdatamcp --version
swissdatamcp serve --port 8787       # browse local dashboards and reports
```

## Supported boundaries

- XLS/XLSX, ZIP archives, WFS/WMS ingestion, and arbitrary GeoJSON ingestion are not supported loaders.
- Maps use detected coordinates or supported geography fields; rankings do not guarantee ingestion.
- HTML dashboards use Plotly from a public CDN. They are local HTML artifacts, not the MCP Apps extension.
- Downloads reject credentials, private/local DNS answers, unsafe redirects, and oversized streams.
  DNS validation is a preflight check; use egress controls for adversarial deployments.
- Strict ingestion reports malformed data instead of silently discarding rows.
- Analytics estimate relationships; correlation is not causation. Counts require a denominator to become rates.

## Development and validation

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev python -m pytest -q
uv run --frozen --extra dev python -m ruff check src tests
uv run --frozen --extra dev python -m build
uv run --frozen --extra dev python scripts/generate_tool_reference.py --check
```

Tests exercise real stdio and HTTP clients, current and legacy protocol flows, structured
responses, resource links, Host/Origin rejection, download failures, SQL escaping, export
round trips, and concurrent session updates. CI runs the locked environment on Windows
and Linux with Python 3.11, 3.12, and 3.14. Unit tests do not depend on publisher uptime.

See [validation](docs/mcp-validation.md), [architecture](docs/swissdatamcp-architecture.md),
[contributing](CONTRIBUTING.md), [security](SECURITY.md), and [release notes](CHANGELOG.md).

## License

MIT. Maintained by [Branislav Taskovikj](https://github.com/taskovikj).
Dataset rights and publisher attribution remain governed by each source's terms.
