# Setup

Requires Python 3.11+. The server does not require a separate LLM API key.

## Install from Git

```sh
python -m pip install "git+https://github.com/taskovikj/swiss-open-data-mcp.git"
swissdatamcp config
```

Use the printed executable path in your stdio client's `mcpServers` configuration.
Set `SWISSDATAMCP_HOME` to a persistent absolute directory in the client's environment.

## Editable installation on Windows

```powershell
git clone https://github.com/taskovikj/swiss-open-data-mcp.git
cd swiss-open-data-mcp
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\swissdatamcp config
.\.venv\Scripts\swissdatamcp doctor
```

On Linux/macOS, use `.venv/bin/python` and `.venv/bin/swissdatamcp`.
For an exact development environment, install uv and run `uv sync --frozen --extra dev`.
The pip workflow resolves current compatible versions; the uv workflow uses the lockfile.

## Transports

`swissdatamcp` without arguments starts stdio. Do not add diagnostic commands to the
client's stdio arguments: stdout is reserved for protocol traffic.

```sh
swissdatamcp http --port 8000
```

Connect a Streamable HTTP client to `http://127.0.0.1:8000/mcp`. The endpoint is local
and unauthenticated. Use one process for each workspace; stop a stdio instance before
starting another instance against the same DuckDB file.

```sh
swissdatamcp serve --port 8787
```

This separate command serves the workspace's HTML reports and session dashboards.
Do not make the workspace server accessible to untrusted networks.

## Troubleshooting

- **Server cannot import MCP:** reinstall this revision. Version 0.2 uses SDK 2.x;
  0.1 imports SDK 1.x APIs and is incompatible with an unbounded 2.x installation.
- **Wrong Python environment:** use the absolute executable returned by `config`.
- **Missing tables in another client:** both clients must use the same absolute
  `SWISSDATAMCP_HOME`, with one active server process at a time.
- **Unsupported download:** choose CSV/TSV/JSON/JSONL/NDJSON/Parquet. XLS, ZIP, and
  map service endpoints are not ingestible merely because resource ranking found them.
- **Changed publisher file:** call the loading tool with `refresh: true`.
- **Strict parser rejects a resource:** choose another distribution or repair the
  source outside the server. Rows are not silently discarded.
- **HTTP 403/421:** use the exact local URL and a permitted local Origin. Untrusted
  Host/Origin headers are rejected intentionally.
- **DuckDB lock error:** stop the other process accessing this workspace.

See the [README](../README.md) for all environment settings and the
[tool reference](tools.md) for argument names.
