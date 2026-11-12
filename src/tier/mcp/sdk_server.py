"""Optional official MCP SDK adapter for TIER.

The default `tier-mcp` / `python -m tier.mcp` server is the zero-dependency stdlib
JSON-RPC implementation. This module is for users who install `tier[mcp]` and want
the official SDK, MCP Inspector (`mcp dev`), or Streamable HTTP.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import server as stdio


class MissingSDKError(RuntimeError):
    pass


def _load_server_class():
    """Return (class, label) for MCP v2 MCPServer or v1 FastMCP, else (None, '')."""
    try:
        from mcp.server import MCPServer

        return MCPServer, "MCPServer"
    except Exception:
        pass
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP, "FastMCP"
    except Exception:
        return None, ""


def _description(name: str) -> str:
    for tool in stdio.TOOLS:
        if tool["name"] == name:
            return tool.get("description", name)
    return name


def build_server(root: str = ".", server_cls=None, server_name: str = "tier"):
    """Build an official-SDK server exposing all 11 TIER tools."""
    root = os.path.realpath(root or ".")
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {root}")
    if server_cls is None:
        server_cls, _label = _load_server_class()
    if server_cls is None:
        raise MissingSDKError(
            'the official mcp SDK is required for this adapter; '
            'install it with: pip install "tier[mcp]"'
        )
    stdio.ROOT = root
    stdio.SESSION.reset()
    mcp = server_cls(server_name)

    @mcp.tool(name="tier_list", description=_description("tier_list"))
    def tier_list() -> str:
        return stdio.call("tier_list", {})

    @mcp.tool(name="tier_index", description=_description("tier_index"))
    def tier_index(path: str) -> str:
        return stdio.call("tier_index", {"path": path})

    @mcp.tool(name="tier_find", description=_description("tier_find"))
    def tier_find(path: str, query: str, k: int = 3) -> str:
        return stdio.call("tier_find", {"path": path, "query": query, "k": k})

    @mcp.tool(name="tier_get", description=_description("tier_get"))
    def tier_get(path: str, ids: List[str], follow: bool = False,
                 table_format: str = "raw", force: bool = False,
                 reset: bool = False) -> str:
        return stdio.call("tier_get", {
            "path": path, "ids": ids, "follow": follow,
            "table_format": table_format, "force": force, "reset": reset,
        })

    @mcp.tool(name="tier_slice", description=_description("tier_slice"))
    def tier_slice(path: str, module: str, symbols: List[str],
                   context_lines: int = 0, force: bool = False) -> str:
        return stdio.call("tier_slice", {
            "path": path, "module": module, "symbols": symbols,
            "context_lines": context_lines, "force": force,
        })

    @mcp.tool(name="tier_impact", description=_description("tier_impact"))
    def tier_impact(path: str, target: str, git: bool = False) -> str:
        return stdio.call("tier_impact", {"path": path, "target": target, "git": git})

    @mcp.tool(name="tier_sync", description=_description("tier_sync"))
    def tier_sync(path: str) -> str:
        return stdio.call("tier_sync", {"path": path})

    @mcp.tool(name="tier_lint", description=_description("tier_lint"))
    def tier_lint(path: str) -> str:
        return stdio.call("tier_lint", {"path": path})

    @mcp.tool(name="tier_ingest", description=_description("tier_ingest"))
    def tier_ingest(content: str, format: str = "auto", index_only: bool = False,
                    max_tokens: Optional[int] = None,
                    save_as: Optional[str] = None) -> str:
        args = {"content": content, "format": format, "index_only": index_only}
        if max_tokens is not None:
            args["max_tokens"] = max_tokens
        if save_as is not None:
            args["save_as"] = save_as
        return stdio.call("tier_ingest", args)

    @mcp.tool(name="tier_repo", description=_description("tier_repo"))
    def tier_repo(path: str = ".", index_only: bool = False) -> str:
        return stdio.call("tier_repo", {"path": path, "index_only": index_only})

    @mcp.tool(name="tier_remember", description=_description("tier_remember"))
    def tier_remember(path: str, action: str, section: str = "", title: str = "",
                      abstract: str = "", body: Optional[List[str]] = None,
                      text: str = "", key: str = "", value: str = "",
                      target: str = "", note: str = "") -> str:
        args = {
            "path": path, "action": action, "section": section, "title": title,
            "abstract": abstract, "text": text, "key": key, "value": value,
            "target": target, "note": note,
        }
        if body is not None:
            args["body"] = body
        return stdio.call("tier_remember", args)

    return mcp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="tier-mcp-sdk",
        description="Official-SDK MCP server for TIER (requires the tier[mcp] extra).",
    )
    ap.add_argument("root", nargs="?", default=".", help="docs/repository root")
    ap.add_argument("--root", dest="root_opt", default=None, help="docs/repository root")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    try:
        srv = build_server(a.root_opt or a.root)
    except MissingSDKError as e:
        print(f"error: {e}", file=sys.stderr)
        print('hint: pip install "tier[mcp]"', file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    kwargs = {}
    if a.transport != "stdio":
        kwargs.update(host=a.host, port=a.port)
    try:
        srv.run(transport=a.transport, **kwargs)
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # keep the CLI clean for broken optional environments
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


try:
    mcp = build_server(os.getcwd())
except MissingSDKError:
    mcp = None
server = mcp


if __name__ == "__main__":
    raise SystemExit(main())
