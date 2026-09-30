import json
import os
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server import MCPServer
from mcp_types import ResourceLink

from swissdatamcp import server
from swissdatamcp.cli import mcp_config


async def test_mcp_server_exposes_tools_resources_and_prompts():
    mcp = server.mcp

    assert isinstance(mcp, MCPServer)
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        assert len(names) == len(set(names))
        assert {"export_local_table", "query_local_table", "get_mcp_tool_guide"} <= set(names)
        assert tools.ttl_ms == 3600000
        assert tools.cache_scope == "public"
        for tool in tools.tools:
            assert tool.title and tool.annotations is not None
        resources = await client.list_resources()
        assert "swissdatamcp://guide" in {r.uri for r in resources.resources}
        templates = await client.list_resource_templates()
        assert "swissdatamcp://session/{session_id}" in {
            t.uri_template for t in templates.resource_templates
        }
        prompt = await client.get_prompt("audit_swiss_data_quality", {"table_name": "sample"})
        assert "sample" in prompt.messages[0].content.text
        guide = await client.call_tool("get_mcp_tool_guide")
        assert not guide.is_error
        assert guide.structured_content["recommended_workflow"]
        invalid = await client.call_tool("query_local_table", {"table_name": "missing"})
        assert invalid.is_error
        assert "Unknown table" in invalid.content[0].text


async def test_export_resource_links_can_be_read(data_store, settings, monkeypatch):
    path = settings.home / "sample.csv"
    path.write_text("year,value\n2020,2\n2021,3\n", encoding="utf-8")
    data_store.load_file_as_table(path, table_name="sample")
    monkeypatch.setattr(server, "store", data_store)
    monkeypatch.setattr(server, "settings", settings)
    async with Client(server.mcp) as client:
        result = await client.call_tool("export_local_table", {"table_name": "sample"})
        assert not result.is_error
        link = next(content for content in result.content if isinstance(content, ResourceLink))
        resource = await client.read_resource(link.uri)
        assert (
            json.loads(resource.contents[0].text)["sha256"] == result.structured_content["sha256"]
        )


async def test_real_stdio_subprocess(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "swissdatamcp.server"],
        env={**os.environ, "SWISSDATAMCP_HOME": str(tmp_path)},
    )
    async with Client(params, read_timeout_seconds=20) as client:
        assert (await client.list_tools()).tools
        result = await client.call_tool("get_workspace_info")
        assert result.structured_content["home"] == str(tmp_path.resolve())


def test_mcp_stdio_entrypoint_calls_run(monkeypatch):
    called = {"run": False}

    def fake_run():
        called["run"] = True

    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.main()

    assert called["run"] is True


def test_mcp_client_config_uses_swissdatamcp_server_name():
    config = mcp_config("swissdatamcp")

    assert "mcpServers" in config
    assert "swissdatamcp" in config["mcpServers"]
    assert "command" in config["mcpServers"]["swissdatamcp"]
