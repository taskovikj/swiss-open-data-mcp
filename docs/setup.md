# Setup

## Requirements

- Python 3.11 or newer
- Git
- An MCP-compatible client

## Install

Install directly from Git:

```powershell
python -m pip install git+https://github.com/taskovikj/swiss-open-data-mcp.git
```

Editable local install:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e .
```

Development install with validation tools:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## Server Command

The package exposes a stdio MCP server through the `swissdatamcp` console
command:

```powershell
.\.venv\Scripts\swissdatamcp
```

## MCP Config

Generic Windows example:

```json
{
  "mcpServers": {
    "swissdatamcp": {
      "command": "C:\\path\\to\\swiss-open-data-mcp\\.venv\\Scripts\\swissdatamcp.exe"
    }
  }
}
```

The local config can be generated with:

```powershell
.\.venv\Scripts\swissdatamcp config
```

Add the generated JSON under the `mcpServers` section of any MCP-compatible
client configuration. The `command` value should point to the installed
`swissdatamcp` executable or to `python -m swissdatamcp.server` depending on the
environment.

## Diagnostics

```powershell
.\.venv\Scripts\swissdatamcp doctor
```

The diagnostic command prints package version, local runtime paths, and the
MCP configuration snippet for the current installation.

## Validation

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
```

## Local Dashboard Server

Interactive reports are static HTML files under `.swissdatamcp/sessions/`.
They can be served locally with:

```powershell
.\.venv\Scripts\swissdatamcp serve --port 8787
```

## Runtime Paths

Default runtime directory:

```text
.swissdatamcp/
  cache/
  downloads/
  outputs/
  reports/
  sessions/
  swissdatamcp.duckdb
```

Runtime files are local-only and ignored by Git.
