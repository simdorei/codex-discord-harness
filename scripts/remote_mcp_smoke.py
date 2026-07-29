from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import codex_discord_runtime_config as runtime_config
from codex_remote_mcp_bridge import RemoteMcpBridge
from codex_remote_mcp_bridge_config import load_remote_mcp_config

MCP_URL = "https://simdorei.duckdns.org/mcp"


def main() -> int:
    runtime_config.load_local_env(REPO_ROOT / ".env")
    config = load_remote_mcp_config()
    if config is None:
        raise RuntimeError("Remote MCP is disabled in .env.")
    bridge = RemoteMcpBridge(config, log=lambda _: None)
    try:
        ticket = bridge.issue_binding("remote-mcp-smoke", REPO_ROOT)
        session = f"smoke-{uuid4().hex}"
        subject = f"local-{uuid4().hex}"
        tools = _rpc("tools/list", {})
        raw_tools = tools.get("tools")
        if not isinstance(raw_tools, list):
            raise TypeError("MCP tools/list did not return a tool list.")
        names: set[str] = set()
        for tool in raw_tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                raise TypeError("MCP tools/list returned an invalid tool.")
            names.add(tool["name"])
        expected = {
            "bind_project",
            "list_project_files",
            "project_info",
            "read_project_file",
            "write_project_file",
        }
        if names != expected:
            raise RuntimeError(f"Unexpected MCP tool set: {sorted(names)}")
        meta = {"openai/session": session, "openai/subject": subject}
        _rpc(
            "tools/call",
            {
                "name": "bind_project",
                "arguments": {"binding_code": ticket.binding_code},
                "_meta": meta,
            },
        )
        project = _rpc(
            "tools/call",
            {
                "name": "project_info",
                "arguments": {},
                "_meta": meta,
            },
        )
        structured = project.get("structuredContent")
        if not isinstance(structured, dict) or not isinstance(structured.get("root"), str):
            raise TypeError("MCP project_info returned invalid structured content.")
        root = Path(structured["root"])
        if root != REPO_ROOT.resolve():
            raise RuntimeError("MCP project root did not match the local repository.")
    finally:
        bridge.close()
    print("Remote MCP smoke passed: HTTPS tools and local project round trip are ready.")
    return 0


def _rpc(method: str, params: dict[str, object]) -> dict[str, object]:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": uuid4().hex,
            "method": method,
            "params": params,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        MCP_URL,
        data=payload,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    if "error" in body:
        raise RuntimeError(f"MCP JSON-RPC error: {body['error']}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise TypeError("MCP response did not contain an object result.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
