"""Command-line helpers for SwissDataMCP.

The console command runs the MCP server with no arguments, so existing MCP
client configuration keeps working. Extra subcommands support local setup and
report viewing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from swissdatamcp import __version__
from swissdatamcp.config import load_settings


def main(argv: list[str] | None = None) -> None:
    """Run the MCP server or a small setup helper command."""

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        from swissdatamcp.server import main as server_main

        server_main()
        return

    parser = build_parser()
    namespace = parser.parse_args(args)
    namespace.func(namespace)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""

    parser = argparse.ArgumentParser(
        prog="swissdatamcp",
        description="Local MCP server and setup helpers for Swiss open-data analysis.",
    )
    parser.add_argument("--version", action="version", version=f"swissdatamcp {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="Print a ready-to-paste MCP config snippet.")
    config_parser.add_argument("--name", default="swissdatamcp", help="MCP server name in the client config.")
    config_parser.set_defaults(func=print_config)

    doctor_parser = subparsers.add_parser("doctor", help="Check local paths and installation details.")
    doctor_parser.add_argument("--name", default="swissdatamcp", help="MCP server name in the client config.")
    doctor_parser.set_defaults(func=doctor)

    serve_parser = subparsers.add_parser("serve", help="Serve local reports and sessions over HTTP.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host address to bind.")
    serve_parser.add_argument("--port", type=int, default=8787, help="Port to bind.")
    serve_parser.set_defaults(func=serve_reports)

    return parser


def mcp_config(server_name: str = "swissdatamcp") -> dict[str, Any]:
    """Return an MCP server config for the current installation."""

    command = command_spec()
    return {"mcpServers": {server_name: command}}


def command_spec() -> dict[str, Any]:
    """Return the best command/args pair for starting the MCP server."""

    scripts_dir = Path(sys.executable).resolve().parent
    for candidate_name in ("swissdatamcp.exe", "swissdatamcp"):
        candidate = scripts_dir / candidate_name
        if candidate.exists():
            return {"command": str(candidate)}

    current = Path(sys.argv[0]).resolve()
    if current.exists() and current.name.lower().startswith("swissdatamcp"):
        return {"command": str(current)}

    discovered = shutil.which("swissdatamcp")
    if discovered:
        return {"command": str(Path(discovered).resolve())}

    return {"command": sys.executable, "args": ["-m", "swissdatamcp.server"]}


def print_config(args: argparse.Namespace) -> None:
    """Print a JSON MCP config snippet."""

    print(json.dumps(mcp_config(args.name), indent=2))


def doctor(args: argparse.Namespace) -> None:
    """Print setup diagnostics."""

    settings = load_settings()
    config = mcp_config(args.name)
    print("SwissDataMCP doctor")
    print("===================")
    print(f"Version: {__version__}")
    print(f"Python: {sys.executable}")
    print(f"Workspace: {settings.home}")
    print(f"Database: {settings.database_path}")
    print(f"Downloads: {settings.downloads_dir}")
    print(f"Reports: {settings.reports_dir}")
    print(f"Sessions: {settings.sessions_dir}")
    print()
    print("MCP config snippet:")
    print(json.dumps(config, indent=2))


def serve_reports(args: argparse.Namespace) -> None:
    """Serve the local SwissDataMCP workspace over HTTP."""

    settings = load_settings()
    handler = partial(SimpleHTTPRequestHandler, directory=str(settings.home))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving SwissDataMCP files from {settings.home}")
    print(f"Open http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
