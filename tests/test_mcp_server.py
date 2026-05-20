from swissdatamcp import server
from swissdatamcp.cli import mcp_config


def test_mcp_server_exposes_tools_resources_and_prompts():
    mcp = server.mcp

    assert type(mcp).__name__ == "FastMCP"
    assert len(mcp._tool_manager._tools) >= 40
    assert "recommend_charts_for_table" in mcp._tool_manager._tools
    assert "correlation_matrix_analysis" in mcp._tool_manager._tools
    assert "swissdatamcp://guide" in mcp._resource_manager._resources
    assert "swissdatamcp://table/{table_name}" in mcp._resource_manager._templates
    assert "analyze_swiss_statistical_question" in mcp._prompt_manager._prompts


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
