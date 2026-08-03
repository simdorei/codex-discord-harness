from remote_mcp_server.simdorei_mcp.mcp_instructions import MCP_INSTRUCTIONS


def test_mcp_instructions_allow_session_owned_terminal_control() -> None:
    assert "terminal_exec may run arbitrary user-authorized" in MCP_INSTRUCTIONS
    assert "Terminal window tools may open, list, capture, activate" in MCP_INSTRUCTIONS
    assert "Never operate ChatGPT, Codex, terminals" not in MCP_INSTRUCTIONS
    assert "Run only commands returned by command_list" not in MCP_INSTRUCTIONS


def test_mcp_instructions_keep_credentials_out_of_terminal_control() -> None:
    for forbidden_surface in (
        "passwords",
        "OTP codes",
        "API keys",
        "tokens",
        "cookies",
        "other credentials",
    ):
        assert forbidden_surface in MCP_INSTRUCTIONS
