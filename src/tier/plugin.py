from __future__ import annotations

import json
import os
import shlex
import sys

from .converter import from_md
from .models import Doc, _atomic_write, _read_text
from .parser import dumps
from .repo import from_repo
from .retrieval import index

TIER_BEGIN = "<!-- TIER:BEGIN -->"
TIER_END = "<!-- TIER:END -->"


def _plugin_instructions(doc: Doc) -> str:
    idx = index(doc, tokens=False)
    return (
        "## TIER repository context\n"
        "This repository is indexed as `repo.tier`. Use the TIER MCP tools instead of reading entire large files:\n"
        "- `tier_list` and `tier_index`: discover docs and read the L0 map.\n"
        "- `tier_find`: locate modules, symbols, rules, or memory entries.\n"
        "- `tier_get`: fetch one module's signatures; set `follow: true` for direct local dependencies.\n"
        "- `tier_slice`: fetch exact L3 source lines for a symbol; `tier_impact` reports blast radius and "
        "`tier_sync` refreshes the map.\n"
        "- `tier_remember`: persist verified rules, decisions, and state to `memory.tier`.\n\n"
        "L0 repository index:\n```\n" + idx + "\n```\n"
    )


def _write_marked_instructions(path: str, block: str):
    marked = f"{TIER_BEGIN}\n{block.rstrip()}\n{TIER_END}\n"
    if os.path.exists(path):
        text = _read_text(path)
        if TIER_BEGIN in text and TIER_END in text:
            before = text.split(TIER_BEGIN, 1)[0]
            after = text.split(TIER_END, 1)[1]
            out = before + marked + after.lstrip("\n")
        else:
            out = text.rstrip() + "\n\n" + marked
    else:
        out = marked
    _atomic_write(path, out)


def _hook_commands(value) -> list:
    out = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out += _hook_commands(v)
    elif isinstance(value, list):
        for v in value:
            out += _hook_commands(v)
    return out


def _merge_hook_settings(target: str) -> str:
    path = os.path.join(target, ".claude", "settings.json")
    if os.path.exists(path):
        try:
            data = json.loads(_read_text(path))
        except ValueError as e:
            raise ValueError(f"existing .claude/settings.json is not valid JSON: {e}")
        if not isinstance(data, dict):
            raise ValueError("existing .claude/settings.json must contain an object")
    else:
        data = {}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("existing hooks entry is not an object")
    post = hooks.setdefault("PostToolUse", [])
    if not isinstance(post, list):
        raise ValueError("existing PostToolUse hooks entry is not a list")
    command = shlex.join([sys.executable or "python3", "-m", "tier", "hook", "--max-tokens", "600"])
    if not any(("-m tier hook" in c) or ("tier hook" in c) for c in _hook_commands(post)):
        post.append({
            "matcher": "Bash|Read|Grep|Glob|WebFetch",
            "hooks": [{"type": "command", "command": command}],
        })
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def init_plugin(target_dir: str, convert_docs: bool = False, write_instructions: str = None,
                hook: bool = False) -> dict:
    """Bootstrap .mcp.json, repo.tier, optional docs, instructions, and hooks."""
    target = os.path.abspath(target_dir)
    if os.path.exists(target) and not os.path.isdir(target):
        raise ValueError(f"not a directory: {target}")
    os.makedirs(target, exist_ok=True)
    json_path = os.path.join(target, ".mcp.json")
    if os.path.exists(json_path):
        try:
            data = json.loads(_read_text(json_path))
        except ValueError as e:
            raise ValueError(f"existing .mcp.json is not valid JSON: {e}")
        if not isinstance(data, dict):
            raise ValueError("existing .mcp.json must contain a JSON object")
    else:
        data = {}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("existing .mcp.json has a non-object mcpServers entry")
    servers["tier"] = {
        "command": sys.executable or "python3",
        "args": ["-m", "tier.mcp", target],
    }
    _atomic_write(json_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    doc = from_repo(target)
    repo_path = os.path.join(target, "repo.tier")
    _atomic_write(repo_path, dumps(doc))

    converted = []
    if convert_docs:
        for name in ("README.md", "CONTRIBUTING.md"):
            src = os.path.join(target, name)
            if os.path.isfile(src):
                out = os.path.join(target, os.path.splitext(name)[0] + ".tier")
                _atomic_write(out, dumps(from_md(_read_text(src))))
                converted.append(out)

    block = _plugin_instructions(doc)
    written = []
    mode = (write_instructions or "").lower()
    if mode in ("claude", "agents", "both"):
        names = ["CLAUDE.md", "AGENTS.md"] if mode == "both" else (
            ["CLAUDE.md"] if mode == "claude" else ["AGENTS.md"]
        )
        for name in names:
            p = os.path.join(target, name)
            _write_marked_instructions(p, block)
            written.append(p)
    hook_path = _merge_hook_settings(target) if hook else ""
    return {
        "target": target, "mcp": json_path, "repo": repo_path,
        "docs": converted, "instruction_files": written, "instructions": block,
        "hook": hook_path,
    }
