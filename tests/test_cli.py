from swissdatamcp.cli import build_parser, mcp_config


def test_mcp_config_contains_server_name():
    config = mcp_config("sample")

    assert "mcpServers" in config
    assert "sample" in config["mcpServers"]
    assert "command" in config["mcpServers"]["sample"]


def test_cli_parser_accepts_helper_commands():
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["config"]).command == "config"
    assert parser.parse_args(["serve", "--port", "9999"]).port == 9999
