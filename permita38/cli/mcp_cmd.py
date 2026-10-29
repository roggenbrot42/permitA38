"""`permita38 mcp`: the MCP server on stdio (docs/specs/mcp-server.md). It talks to the running server over HTTP."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

INSTALL_HINT = "The MCP server needs the optional SDK. Install it with: pip install 'permita38[mcp]'"


def register(sub) -> None:
    p = sub.add_parser("mcp", help="MCP server on stdio: read-only tools for AI assistants (needs permita38[mcp])")
    p.add_argument("--url", default=None, help="permitA38 server (default: $PERMITA38_URL or http://127.0.0.1:8765)")
    p.add_argument("--token-file", type=Path, help="file with the API token (mode 600); else $PERMITA38_TOKEN")
    p.add_argument(
        "--read-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="register no write tools (default; version 1 has none)",
    )


def run(args) -> None:
    from permita38.mcp.client import DEFAULT_URL, ApiError, check_url, load_token

    try:
        url = check_url(args.url or os.environ.get("PERMITA38_URL") or DEFAULT_URL)
        token = load_token(args.token_file, os.environ)
    except ValueError as e:
        sys.exit(f"permita38 mcp: {e}")
    try:
        from permita38.mcp import server
    except ModuleNotFoundError as e:
        if (e.name or "").split(".")[0] in ("mcp", "mcp_types"):
            sys.exit(INSTALL_HINT)
        raise
    server.configure_logging()
    try:
        asyncio.run(server.serve_stdio(url, token, args.read_only))
    except ApiError as e:
        sys.exit(f"permita38 mcp: {e.message}")
    except KeyboardInterrupt:
        pass
