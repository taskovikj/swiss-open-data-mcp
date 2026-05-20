# swissdatamcp Architecture

## Purpose

`swissdatamcp` is a local MCP server that provides safe, structured tools for Swiss open-data exploration.

The MCP server provides deterministic access to public data, local tables, calculations, charts, reports, and citations.

## System Shape

```text
MCP client
  |
  | MCP stdio
  v
swissdatamcp server
  |
  | tools
  v
opendata.swiss CKAN API
DuckDB local analytics
local chart/report artifacts
interactive session dashboards
```

## Layers

### MCP Server

File:

- `src/swissdatamcp/server.py`

Responsibilities:

- define MCP tools
- define MCP resources
- define reusable prompts
- convert internal exceptions into MCP tool errors
- expose a stdio entry point

### Catalog Connector

File:

- `src/swissdatamcp/catalog.py`

Responsibilities:

- call opendata.swiss CKAN Action API
- search datasets
- fetch package metadata
- normalize dataset/resource metadata

### Local Store

File:

- `src/swissdatamcp/store.py`

Responsibilities:

- download public resources
- load CSV/TSV/JSON/JSONL/Parquet files into DuckDB
- list and inspect local tables
- safely query local tables with simple filters
- compute summary statistics

### Chart Service

File:

- `src/swissdatamcp/charts.py`

Responsibilities:

- create PNG line/bar/scatter charts from local tables
- return chart-ready preview rows
- support lightweight static artifacts for reports

### Label Helpers

File:

- `src/swissdatamcp/labels.py`

Responsibilities:

- convert technical column names into readable labels
- label common Swiss statistical codes such as canton, child sex, and age bands
- normalize canton code, abbreviation, and name variants

### Report Service

File:

- `src/swissdatamcp/reports.py`

Responsibilities:

- create local HTML/Markdown reports
- attach chart path, narrative, table name, and citation metadata

### Session Service

File:

- `src/swissdatamcp/sessions.py`

Responsibilities:

- create persistent local analysis sessions
- store chart specs in `manifest.json`
- add, update, and remove charts through MCP tools
- render interactive Plotly dashboards
- render line, bar, scatter, map, and heatmap cards
- add readable chart explanations, metric pills, labels, and hover values
- show source table metadata and source URLs on chart cards where available
- store advanced analysis results

### Compatibility and Planning Helpers

File:

- `src/swissdatamcp/analytics.py`

Responsibilities:

- recommend useful chart types from schema semantics
- suggest join keys between two local tables
- compare table granularity and duplicate dimension groups
- check whether two tables are ready for joined correlation
- calculate normalized per-capita/rate metrics
- generate long-form correlation matrices for heatmap rendering

### Citation Helpers

File:

- `src/swissdatamcp/citations.py`

Responsibilities:

- build citation objects from dataset/resource metadata

## First-Version Data Flow

```text
1. A request enters through an MCP client.
2. Dataset discovery runs against opendata.swiss metadata.
3. Dataset resources are inspected and ranked.
4. A machine-readable resource is loaded into DuckDB.
5. Local table inspection, profiling, querying, and analysis tools run deterministic operations.
6. Chart, report, dashboard, and citation artifacts are created locally.
```

Interactive sessions are stored as local manifests and rendered as static HTML:

```text
.swissdatamcp/sessions/<session-id>/index.html
```

## Runtime Model

The package runs as a local MCP server. Generated artifacts are local static
files that can be served with the CLI.

- MCP tools provide the real data and computations.
- Charts/reports are generated as local files.
