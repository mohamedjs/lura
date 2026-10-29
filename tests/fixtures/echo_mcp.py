"""A minimal MCP server over stdio, for the connector checks."""
import json
import sys

print("banner line that is not JSON", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "echo"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo_text", "description": "Echo text back",
                             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                                             "required": ["text"]}},
                            {"name": "open_tab", "description": "Open a new browser tab",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call" and msg["params"]["name"] == "open_tab" and "--cdp-endpoint" in sys.argv:
        # Stands in for Playwright MCP attached to the user's Chrome.
        url = sys.argv[sys.argv.index("--cdp-endpoint") + 1]
        if "chrome-is-closed" in url:
            result = {"isError": True, "content": [{"type": "text", "text": "browserType.connectOverCDP: connect ECONNREFUSED"}]}
        else:
            result = {"content": [{"type": "text", "text": "tab opened via " + url}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "echo: " + msg["params"]["arguments"].get("text", "")}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"message": "nope"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
