import asyncio
import os
import socket
import subprocess
import sys

import httpx
import pytest
from mcp import Client


@pytest.fixture
async def http_server(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log_path = tmp_path / "server.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "swissdatamcp.cli", "http", "--port", str(port)],
            env={**os.environ, "SWISSDATAMCP_HOME": str(tmp_path / "workspace")},
            stdout=log,
            stderr=log,
        )
        try:
            url = f"http://127.0.0.1:{port}/mcp"
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(200):
                    if process.poll() is not None:
                        pytest.fail(log_path.read_text(encoding="utf-8"))
                    try:
                        async with client.stream("GET", url):
                            pass
                        break
                    except httpx.ConnectError:
                        await asyncio.sleep(0.05)
                else:
                    pytest.fail("MCP HTTP server did not start in 10 seconds.")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_http_protocol_and_structured_results(http_server, mode):
    async with Client(http_server, mode=mode, read_timeout_seconds=10) as client:
        tools = await client.list_tools()
        assert any(tool.name == "export_local_table" for tool in tools.tools)
        result = await client.call_tool("get_mcp_tool_guide")
        assert result.structured_content["recommended_workflow"]
        resource = await client.read_resource("swissdatamcp://guide")
        assert resource.contents[0].mime_type == "application/json"


async def test_http_rejects_untrusted_host_origin_and_large_requests(http_server):
    async with httpx.AsyncClient(trust_env=False) as client:
        bad_host = await client.post(http_server, headers={"host": "attacker.example"}, json={})
        assert bad_host.status_code == 421
        bad_origin = await client.post(
            http_server, headers={"origin": "https://attacker.example"}, json={}
        )
        assert bad_origin.status_code == 403
        large = await client.post(
            http_server,
            content=b"x" * (4 * 1024 * 1024 + 1),
            headers={"content-type": "application/json"},
        )
        assert large.status_code == 413
