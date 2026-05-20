# swissdatamcp

Local MCP server for source-grounded Swiss open-data analysis.

`swissdatamcp` exposes tools for discovering public datasets on
opendata.swiss, loading machine-readable resources into DuckDB, profiling and
querying local tables, creating charts and reports, rendering interactive
analysis sessions, and producing citation packs.

## Overview

```text
MCP client
  -> swissdatamcp tools
  -> opendata.swiss metadata and public resources
  -> local DuckDB tables
  -> deterministic analytics
  -> local charts, dashboards, reports, and citations
```

The package provides a local data and analytics layer for MCP-compatible clients.

## Visualization Stack

- DuckDB powers local table queries and derived analysis tables.
- Plotly.js powers interactive HTML analysis sessions with line, bar, scatter,
  map, and heatmap cards.
- Matplotlib powers static PNG chart artifacts for simple reports.
- Chart manifests store readable column labels, Swiss code value labels, filters,
  source table metadata, and source URLs when available.

## Design Principles

- Source data and local table rows are the factual source of truth.
- Generated answers should cite dataset metadata and source URLs.
- Numeric results should come from deterministic table operations.
- Runtime downloads, dashboards, reports, and DuckDB files stay local by default.
- Protected endpoints, private APIs, credentials, login flows, and scraping bypasses are out of scope.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e .
```

## CLI

Run the MCP server over stdio:

```powershell
.\.venv\Scripts\swissdatamcp
```

Print a local MCP configuration snippet:

```powershell
.\.venv\Scripts\swissdatamcp config
```

Check local paths and installation details:

```powershell
.\.venv\Scripts\swissdatamcp doctor
```

Serve generated local dashboards and reports:

```powershell
.\.venv\Scripts\swissdatamcp serve --port 8787
```

## MCP Configuration

Example client configuration:

```json
{
  "mcpServers": {
    "swissdatamcp": {
      "command": "C:\\path\\to\\swiss-open-data-mcp\\.venv\\Scripts\\swissdatamcp.exe"
    }
  }
}
```

A generic template is available at:

```text
examples/mcp-config.example.json
```

## Tool Groups

Dataset and catalog tools:

- `catalog_status`
- `get_mcp_tool_guide`
- `search_swiss_datasets`
- `search_datasets_advanced`
- `get_dataset_metadata`
- `list_dataset_resources`
- `recommend_best_resource`
- `get_dataset_citation`

Data loading and inspection tools:

- `load_dataset_resource`
- `load_resource_url`
- `list_local_tables`
- `inspect_local_table`
- `query_local_table`
- `analyze_local_table`
- `profile_dataset`
- `detect_schema_semantics`
- `clean_table_for_analysis`
- `normalize_canton_codes`
- `answer_from_table`

Chart and compatibility planning tools:

- `recommend_charts_for_table`
- `suggest_join_keys`
- `compare_table_granularity`
- `can_correlate_tables`

Interactive analysis tools:

- `create_analysis_session`
- `list_analysis_sessions`
- `get_analysis_session`
- `add_chart_to_session`
- `remove_chart_from_session`
- `update_chart_in_session`
- `render_interactive_report`
- `calculate_change_analysis`
- `create_dashboard_from_question`
- `create_map_layer`
- `spatial_summary`
- `compare_datasets`
- `calculate_per_capita_metric`
- `correlation_analysis`
- `correlation_matrix_analysis`
- `time_series_analysis`
- `outlier_detection`
- `create_analysis_plan`

Artifact tools:

- `preview_chart_data`
- `create_chart`
- `create_report`
- `generate_citation_pack`
- `export_session_bundle`
- `get_workspace_info`

## MCP Resources

- `swissdatamcp://guide`
- `swissdatamcp://workspace`
- `swissdatamcp://tables`
- `swissdatamcp://table/{table_name}`

## Supported Formats

Loadable formats:

- CSV
- TSV
- JSON
- JSONL / NDJSON
- Parquet

Detected but not fully automated yet:

- Excel
- ZIP
- GeoPackage
- Shapefile
- WFS/ArcGIS services

## Local Runtime Data

Runtime data is written under `.swissdatamcp/` by default:

```text
.swissdatamcp/
  cache/
  downloads/
  outputs/
  reports/
  sessions/
  swissdatamcp.duckdb
```

Supported environment overrides:

- `SWISSDATAMCP_HOME`
- `SWISSDATAMCP_CACHE_DIR`
- `SWISSDATAMCP_DOWNLOADS_DIR`
- `SWISSDATAMCP_OUTPUTS_DIR`
- `SWISSDATAMCP_REPORTS_DIR`
- `SWISSDATAMCP_DB`
- `SWISSDATAMCP_MAX_DOWNLOAD_MB`

Runtime data is ignored by Git.

## Development

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m compileall src
```

## Documentation

- [Setup guide](docs/setup.md)
- [Architecture](docs/swissdatamcp-architecture.md)

## License

MIT
