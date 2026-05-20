# MCP Validation

## What Makes This A Real MCP Server

`swissdatamcp` uses the official Python MCP SDK and exposes a stdio server
through the `swissdatamcp` console command.

Validation checks:

- the server object is a `FastMCP` instance
- running the console command with no subcommand starts `mcp.run()`
- tools are registered with the MCP tool manager
- resources are registered with the MCP resource manager
- prompts are registered with the MCP prompt manager
- `swissdatamcp config` prints a client configuration snippet

## Current MCP Surface

- tools for catalog search, loading, profiling, querying, analysis, charting,
  reporting, and citation export
- resources:
  - `swissdatamcp://workspace`
  - `swissdatamcp://guide`
  - `swissdatamcp://tables`
  - `swissdatamcp://table/{table_name}`
- prompts:
  - `analyze_swiss_statistical_question`
  - `compare_swiss_geographies`

## Local Validation Commands

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\swissdatamcp config
.\.venv\Scripts\swissdatamcp doctor
```

The tests include MCP introspection checks for tools, resources, prompts, stdio
entrypoint behavior, and client configuration generation.
