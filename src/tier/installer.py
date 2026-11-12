"""Interactive/non-interactive MCP installer with safe config merging."""
from __future__ import annotations

import difflib
import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import Doc, VERSION, _atomic_write, _read_text
from .parser import _load, dumps, is_repo_doc
from .plugin import TIER_BEGIN, TIER_END, _plugin_instructions
from .repo import from_repo, repo_source_files, sync_repo
from .retrieval import index
from .tokens import count_tokens
from .ui import UI, default_ui


@dataclass
class ClientSpec:
    id: str
    label: str
    scope: str
    config_path: str
    config_key: str
    hook_path: str = ""
    instruction_paths: Tuple[str, ...] = ()
    detected: bool = False
    configured: bool = False
    parse_error: str = ""
    entry: dict = field(default_factory=dict)


@dataclass
class Change:
    path: str
    action: str
    backup: str = ""
    diff: str = ""


ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude-code-user": "claude-code-user",
    "claude-user": "claude-code-user",
    "claude-desktop": "claude-desktop",
    "desktop": "claude-desktop",
    "cursor": "cursor",
    "cursor-global": "cursor-global",
    "vscode": "vscode",
    "vs-code": "vscode",
    "code": "vscode",
    "windsurf": "windsurf",
}


def _home(home: Optional[str]) -> str:
    return os.path.abspath(home if home is not None else os.path.expanduser("~"))


def _desktop_path(home: str, env: dict, platform: str) -> str:
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json")
    if platform.startswith("win"):
        base = env.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, "Claude", "claude_desktop_config.json")
    return os.path.join(home, ".config", "Claude", "claude_desktop_config.json")


def _load_json_soft(path: str):
    if not os.path.exists(path):
        return {}, ""
    try:
        data = json.loads(_read_text(path) or "{}")
    except ValueError as e:
        return None, str(e)
    if not isinstance(data, dict):
        return None, "config root is not a JSON object"
    return data, ""


def client_specs(root: str, home: Optional[str] = None, env: Optional[dict] = None,
                 platform: Optional[str] = None) -> List[ClientSpec]:
    root = os.path.abspath(root)
    home = _home(home)
    env = env if env is not None else os.environ
    platform = platform or sys.platform
    raw = [
        ClientSpec("claude-code", "Claude Code (project)", "project",
                   os.path.join(root, ".mcp.json"), "mcpServers",
                   os.path.join(root, ".claude", "settings.json"),
                   (os.path.join(root, "CLAUDE.md"), os.path.join(root, "AGENTS.md"))),
        ClientSpec("claude-code-user", "Claude Code (user)", "user",
                   os.path.join(home, ".claude.json"), "mcpServers",
                   os.path.join(home, ".claude", "settings.json"),
                   (os.path.join(home, ".claude", "CLAUDE.md"),)),
        ClientSpec("claude-desktop", "Claude Desktop", "user",
                   _desktop_path(home, env, platform), "mcpServers"),
        ClientSpec("cursor", "Cursor (project)", "project",
                   os.path.join(root, ".cursor", "mcp.json"), "mcpServers"),
        ClientSpec("cursor-global", "Cursor (global)", "user",
                   os.path.join(home, ".cursor", "mcp.json"), "mcpServers"),
        ClientSpec("vscode", "VS Code (project)", "project",
                   os.path.join(root, ".vscode", "mcp.json"), "servers"),
        ClientSpec("windsurf", "Windsurf", "user",
                   os.path.join(home, ".codeium", "windsurf", "mcp_config.json"), "mcpServers"),
    ]
    for spec in raw:
        parent = os.path.dirname(spec.config_path)
        if spec.id == "claude-code":
            spec.detected = os.path.exists(spec.config_path) or os.path.isdir(os.path.join(root, ".claude"))
        elif spec.id == "cursor":
            spec.detected = os.path.exists(spec.config_path) or os.path.isdir(os.path.join(root, ".cursor"))
        elif spec.id == "vscode":
            spec.detected = os.path.exists(spec.config_path) or os.path.isdir(os.path.join(root, ".vscode"))
        else:
            spec.detected = os.path.exists(spec.config_path) or os.path.isdir(parent)
        data, error = _load_json_soft(spec.config_path)
        if error:
            spec.parse_error = error
            continue
        servers = data.get(spec.config_key, {})
        if isinstance(servers, dict) and isinstance(servers.get("tier"), dict):
            spec.configured = True
            spec.entry = servers["tier"]
    return raw


def spec_by_id(specs: Sequence[ClientSpec], client_id: str) -> Optional[ClientSpec]:
    for spec in specs:
        if spec.id == client_id:
            return spec
    return None


def parse_clients(value: Optional[str], specs: Sequence[ClientSpec]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        names = [str(x).strip().lower() for x in value if str(x).strip()]
    else:
        names = [x.strip().lower() for x in str(value).split(",") if x.strip()]
    if not names:
        return []
    if names == ["all"]:
        return [s.id for s in specs]
    if names == ["none"]:
        return []
    out = []
    for name in names:
        client_id = ALIASES.get(name)
        if client_id is None or spec_by_id(specs, client_id) is None:
            raise ValueError(f"unknown client '{name}' (choose from {', '.join(s.id for s in specs)})")
        if client_id not in out:
            out.append(client_id)
    return out


def default_clients(specs: Sequence[ClientSpec]) -> List[str]:
    out = [s.id for s in specs if s.configured]
    if "claude-code" not in out:
        out.insert(0, "claude-code")
    return out


def server_entry(root: str, client_id: str = "", find_executable=None) -> dict:
    find_executable = find_executable or shutil.which
    exe = find_executable("tier-mcp")
    if exe:
        entry = {"command": os.path.abspath(exe), "args": [root]}
    else:
        python = sys.executable or find_executable("python3") or find_executable("python") or "python3"
        if os.path.isabs(python) or os.path.sep in python:
            python = os.path.abspath(python)
        entry = {"command": python, "args": ["-m", "tier.mcp", root]}
    if client_id == "vscode":
        entry["type"] = "stdio"
    return entry


def _json_text(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _unified(path: str, before: str, after: str) -> str:
    return "\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile=path, tofile=path, lineterm=""
    ))


def _backup(path: str) -> str:
    if not os.path.exists(path):
        return ""
    backup = path + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        return backup
    return ""


def _save_text(path: str, after: str, dry_run: bool, changes: List[Change],
               backup: bool = True) -> None:
    before = _read_text(path) if os.path.exists(path) else ""
    if before == after:
        return
    backup_path = ""
    if not dry_run:
        if backup and before:
            backup_path = _backup(path)
        _atomic_write(path, after)
    changes.append(Change(path, "create" if not before else "update", backup_path,
                          _unified(path, before, after)))


def _save_json(path: str, data: dict, dry_run: bool, changes: List[Change]) -> None:
    _save_text(path, _json_text(data), dry_run, changes, backup=True)


def _merge_server(spec: ClientSpec, root: str, dry_run: bool, changes: List[Change],
                  find_executable=None) -> None:
    data, error = _load_json_soft(spec.config_path)
    if error:
        raise ValueError(f"cannot read {spec.config_path}: {error}")
    servers = data.setdefault(spec.config_key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{spec.config_path}: '{spec.config_key}' is not an object")
    servers["tier"] = server_entry(root, spec.id, find_executable)
    _save_json(spec.config_path, data, dry_run, changes)


def _is_tier_hook(cmd) -> bool:
    if not isinstance(cmd, str):
        return False
    return "-m tier hook" in cmd or "tier.py hook" in cmd or "tier hook" in cmd


def _hook_command(find_executable=None) -> str:
    find_executable = find_executable or shutil.which
    exe = find_executable("tier")
    if exe:
        return shlex.join([os.path.abspath(exe), "hook", "--max-tokens", "600"])
    python = sys.executable or find_executable("python3") or "python3"
    return shlex.join([os.path.abspath(python), "-m", "tier", "hook", "--max-tokens", "600"])


def _filter_hooks(post: list, desired: str, uninstall: bool = False):
    new_post, present = [], False
    for entry in list(post):
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            new_post.append(entry)
            continue
        kept = []
        for hook in entry["hooks"]:
            if not isinstance(hook, dict):
                kept.append(hook)
                continue
            cmd = hook.get("command")
            if _is_tier_hook(cmd):
                if not uninstall and cmd == desired and not present:
                    kept.append(hook)
                    present = True
                continue
            kept.append(hook)
        if kept:
            copy = dict(entry)
            copy["hooks"] = kept
            new_post.append(copy)
    return new_post, present


def _merge_hook(path: str, dry_run: bool, changes: List[Change], find_executable=None) -> None:
    data, error = _load_json_soft(path)
    if error:
        raise ValueError(f"cannot read {path}: {error}")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{path}: 'hooks' is not an object")
    post = hooks.setdefault("PostToolUse", [])
    if not isinstance(post, list):
        raise ValueError(f"{path}: 'hooks.PostToolUse' is not a list")
    command = _hook_command(find_executable)
    filtered, present = _filter_hooks(post, command)
    if not present:
        filtered.append({
            "matcher": "Bash|Read|Grep|Glob|WebFetch",
            "hooks": [{"type": "command", "command": command}],
        })
    hooks["PostToolUse"] = filtered
    _save_json(path, data, dry_run, changes)


def _remove_hook(path: str, dry_run: bool, changes: List[Change]) -> None:
    data, error = _load_json_soft(path)
    if error:
        raise ValueError(f"cannot read {path}: {error}")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    post = hooks.get("PostToolUse")
    if not isinstance(post, list):
        return
    filtered, _present = _filter_hooks(post, "", uninstall=True)
    hooks["PostToolUse"] = filtered
    _save_json(path, data, dry_run, changes)


def _marked_text(existing: str, block: str) -> str:
    marked = f"{TIER_BEGIN}\n{block.rstrip()}\n{TIER_END}\n"
    if TIER_BEGIN in existing and TIER_END in existing:
        before = existing.split(TIER_BEGIN, 1)[0]
        after = existing.split(TIER_END, 1)[1]
        return before + marked + after.lstrip("\n")
    if existing.strip():
        return existing.rstrip() + "\n\n" + marked
    return marked


def _strip_marked(existing: str) -> str:
    if TIER_BEGIN not in existing or TIER_END not in existing:
        return existing
    before = existing.split(TIER_BEGIN, 1)[0]
    after = existing.split(TIER_END, 1)[1].lstrip("\n")
    return (before.rstrip() + ("\n" if after else "") + after).rstrip() + "\n"


def _generic_instructions() -> str:
    return (
        "## TIER repository context\n"
        "This repository is indexed as `repo.tier`. Use `tier index` for the L0 map, "
        "`tier find` to locate a module, `tier get` for signatures, and `tier slice` for exact source. "
        "Persist verified decisions with `tier remember`.\n"
    )


def _write_instructions(path: str, block: str, dry_run: bool, changes: List[Change]) -> None:
    existing = _read_text(path) if os.path.exists(path) else ""
    _save_text(path, _marked_text(existing, block), dry_run, changes, backup=False)


def _remove_instructions(path: str, dry_run: bool, changes: List[Change]) -> None:
    if not os.path.exists(path):
        return
    existing = _read_text(path)
    _save_text(path, _strip_marked(existing), dry_run, changes, backup=False)


def _instruction_paths(specs: Sequence[ClientSpec], selected: Sequence[str], mode: str) -> List[str]:
    mode = (mode or "both").lower()
    if mode == "none":
        return []
    out = []
    for spec in specs:
        if spec.id not in selected:
            continue
        for path in spec.instruction_paths:
            base = os.path.basename(path).lower()
            if mode == "claude" and base != "claude.md":
                continue
            if mode == "agents" and base != "agents.md":
                continue
            if path not in out:
                out.append(path)
    return out


def _repo_stats(root: str, doc: Doc) -> dict:
    files = repo_source_files(root)
    raw = "\n".join(_read_text(full) for _rel, full, _ext in files)
    raw_t = count_tokens([raw])[0][0] if raw else 0
    l0 = index(doc, tokens=False)
    l0_t = count_tokens([l0])[0][0] if l0 else 0
    saved = 100.0 * (raw_t - l0_t) / raw_t if raw_t else 0.0
    return {"files": len(files), "raw_tokens": raw_t, "l0_tokens": l0_t, "saved_percent": saved}


def _generate_repo(root: str, dry_run: bool, changes: List[Change]) -> dict:
    path = os.path.join(root, "repo.tier")
    action = "created"
    doc = None
    if os.path.exists(path):
        try:
            existing = _load(path)
            if is_repo_doc(existing):
                result = sync_repo(path, root=root, write=not dry_run)
                doc = result["doc"]
                action = "would sync" if dry_run else "synced"
        except (OSError, ValueError):
            doc = None
    if doc is None:
        doc = from_repo(root)
        if not dry_run:
            _atomic_write(path, dumps(doc))
        action = "would create" if dry_run else "created"
    stats = _repo_stats(root, doc)
    stats.update({"path": path, "action": action, "_doc": doc})
    return stats


def _ask(ask: Optional[Callable[[str], str]], prompt: str, default: str) -> str:
    ask = ask or input
    suffix = f" [{default}]" if default else ""
    answer = ask(f"{prompt}{suffix}: ")
    return (answer or "").strip() or str(default)


def _ask_bool(ask, prompt: str, default: bool) -> bool:
    answer = _ask(ask, prompt + " (y/n)", "y" if default else "n").lower()
    return answer in ("y", "yes", "1", "true")


def _ask_instructions(ask, default: str = "both") -> str:
    answer = _ask(ask, "Instructions (both/claude/agents/none)", default).lower()
    return answer if answer in ("both", "claude", "agents", "none") else default


def _interactive_clients(specs: Sequence[ClientSpec], selected: Sequence[str], ask, ui: UI) -> List[str]:
    rows = []
    for i, spec in enumerate(specs, 1):
        flags = []
        if spec.configured:
            flags.append("configured")
        if spec.detected:
            flags.append("detected")
        rows.append([str(i), spec.label, spec.scope, ", ".join(flags) or "-", spec.config_path])
    ui.table(["#", "client", "scope", "state", "config"], rows)
    default = ",".join(str(i) for i, s in enumerate(specs, 1) if s.id in selected)
    answer = _ask(ask, "Select clients by number (comma separated), Enter accepts defaults", default)
    if not answer:
        return list(selected)
    if answer.lower() == "none":
        return []
    if answer.lower() == "all":
        return [s.id for s in specs]
    indices = []
    for token in answer.replace(" ", ",").split(","):
        if token.strip().isdigit():
            idx = int(token.strip())
            if 1 <= idx <= len(specs):
                indices.append(idx)
    if not indices:
        ui.warn("no valid client numbers; keeping defaults")
        return list(selected)
    return [specs[i - 1].id for i in sorted(set(indices))]


def _summary(ui: UI, result: dict) -> None:
    lines = [f"Target: {result['target']}"]
    lines.append("Clients: " + (", ".join(result["clients"]) or "none"))
    repo = result.get("repo", {})
    if repo:
        lines.append(f"repo.tier: {repo.get('path', '')} ({repo.get('action', '')})")
        if repo.get("raw_tokens"):
            if repo["l0_tokens"] <= repo["raw_tokens"]:
                detail = f"{repo['saved_percent']:.1f}% saved"
            else:
                detail = "L0 overhead on a tiny repository"
            lines.append(
                f"Tokens: raw source {repo['raw_tokens']} -> L0 {repo['l0_tokens']} ({detail})"
            )
    if result.get("hook"):
        lines.append("Hook: " + ", ".join(result["hook"]))
    if result.get("instructions"):
        lines.append("Instructions: " + ", ".join(result["instructions"]))
    if result.get("changes"):
        lines.append("Changed files:")
        for change in result["changes"]:
            suffix = f" (backup {change['backup']})" if change["backup"] else ""
            lines.append(f"  {change['path']}{suffix}")
    if result.get("dry_run"):
        lines.append("Dry run: no files were changed.")
    ui.panel("Installation summary", lines)
    if result.get("dry_run") and result.get("changes"):
        ui.info("Preview of config changes:")
        for change in result["changes"]:
            if change.get("diff"):
                ui.raw(change["diff"])


def install(target: str = ".", clients=None, *, yes: bool = False, dry_run: bool = False,
            uninstall: bool = False, repo: bool = True, hook: bool = True,
            instructions: str = "both", interactive: Optional[bool] = None,
            home: Optional[str] = None, env: Optional[dict] = None,
            platform: Optional[str] = None, ask=None, ui: Optional[UI] = None,
            find_executable=None) -> dict:
    """Install or remove TIER MCP entries across supported clients."""
    root = os.path.abspath(target or ".")
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {root}")
    ui = ui or UI()
    specs = client_specs(root, home=home, env=env, platform=platform)
    if uninstall:
        selected = parse_clients(clients, specs) if clients is not None else [s.id for s in specs]
        return _uninstall(root, specs, selected, dry_run, ui)
    ui.header(f"TIER v{VERSION} — MCP & Agent Installer",
              "Zero-dependency core; optional extras: tier[ui], tier[mcp]")
    if interactive is None:
        interactive = clients is None and not yes and sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        chosen_root = _ask(ask, "Target repository/docs root", root)
        root = os.path.abspath(chosen_root)
        if not os.path.isdir(root):
            raise ValueError(f"not a directory: {root}")
        specs = client_specs(root, home=home, env=env, platform=platform)
        selected = _interactive_clients(specs, default_clients(specs), ask, ui)
        repo = _ask_bool(ask, "Generate/sync repo.tier", repo)
        if any(s.hook_path for s in specs if s.id in selected):
            hook = _ask_bool(ask, "Install the Claude Code PostToolUse hook", hook)
        else:
            hook = False
        instructions = _ask_instructions(ask, instructions)
    else:
        selected = parse_clients(clients, specs) if clients is not None else default_clients(specs)
        if clients is None and yes:
            selected = ["claude-code"]

    changes: List[Change] = []
    result = {
        "target": root, "clients": selected, "changes": [], "hook": [],
        "instructions": [], "repo": {}, "dry_run": dry_run, "uninstalled": False,
    }
    if repo:
        result["repo"] = _generate_repo(root, dry_run, changes)
    chosen_specs = [s for s in specs if s.id in selected]
    for spec in chosen_specs:
        _merge_server(spec, root, dry_run, changes, find_executable)
    hook_paths = []
    if hook:
        for spec in chosen_specs:
            if spec.hook_path and spec.hook_path not in hook_paths:
                _merge_hook(spec.hook_path, dry_run, changes, find_executable)
                hook_paths.append(spec.hook_path)
    result["hook"] = hook_paths
    instr = []
    doc = result.get("repo", {}).pop("_doc", None)
    if instructions and instructions.lower() != "none":
        block = _plugin_instructions(doc) if doc is not None else _generic_instructions()
        for path in _instruction_paths(specs, selected, instructions):
            _write_instructions(path, block, dry_run, changes)
            instr.append(path)
    result["instructions"] = instr
    result["changes"] = [c.__dict__ for c in changes]
    _summary(ui, result)
    return result


def _uninstall(root: str, specs: Sequence[ClientSpec], selected: Sequence[str],
               dry_run: bool, ui: UI) -> dict:
    changes: List[Change] = []
    for spec in specs:
        if spec.id not in selected:
            continue
        data, error = _load_json_soft(spec.config_path)
        if error:
            raise ValueError(f"cannot read {spec.config_path}: {error}")
        if os.path.exists(spec.config_path):
            servers = data.get(spec.config_key)
            if isinstance(servers, dict) and "tier" in servers:
                del servers["tier"]
                _save_json(spec.config_path, data, dry_run, changes)
        if spec.hook_path:
            _remove_hook(spec.hook_path, dry_run, changes)
        for path in spec.instruction_paths:
            _remove_instructions(path, dry_run, changes)
    result = {
        "target": root, "clients": list(selected), "changes": [c.__dict__ for c in changes],
        "hook": [], "instructions": [], "repo": {}, "dry_run": dry_run, "uninstalled": True,
    }
    ui.header(f"TIER v{VERSION} — Uninstall")
    _summary(ui, result)
    return result


def setup(*args, **kwargs) -> dict:
    """Alias for install()."""
    return install(*args, **kwargs)
