"""Health and configuration diagnostics for `tier doctor`."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .installer import (
    SCHEMA_SKILL, ClientSpec, _is_tier_hook, _load_json_soft, client_specs, probe_entry,
)
from .lint import lint
from .parser import _load, is_repo_doc
from .repo import sync_repo
from .tokens import _count_js
from .ui import UI


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _check_entry(spec: ClientSpec, root: str, find_executable) -> Check:
    """Validate one client's TIER entry, whatever schema it uses."""
    probe = probe_entry(spec)
    if probe.get("error"):
        return Check(f"client {spec.id}", "FAIL", f"{spec.config_path}: {probe['error']}")
    if not probe.get("present"):
        return Check(f"client {spec.id}", "INFO", "config present; TIER is not registered")
    if spec.schema == SCHEMA_SKILL:
        return Check(f"client {spec.id}", "PASS", spec.config_path)

    entry = probe.get("entry") or {}
    command = entry.get("command")
    args = entry.get("args", [])
    if not isinstance(command, str) or not command:
        return Check(f"client {spec.id}", "FAIL", "TIER entry has no command")
    if not isinstance(args, list):
        return Check(f"client {spec.id}", "FAIL", "TIER entry args is not a list")
    if os.path.isabs(command):
        command_ok = os.path.exists(command)
    else:
        command_ok = bool(find_executable(command))
    if not command_ok:
        return Check(f"client {spec.id}", "FAIL", f"command not found: {command}")
    target = ""
    for value in reversed(args):
        if isinstance(value, str) and value not in ("-m", "tier.mcp", "tier", "hook"):
            target = value
            break
    if target and os.path.isdir(target):
        if os.path.realpath(target) != os.path.realpath(root):
            return Check(f"client {spec.id}", "WARN",
                         f"registered, but root points to {target}")
        return Check(f"client {spec.id}", "PASS", f"{spec.config_path} -> {command}")
    if target and (os.sep in target or "/" in target or "\\" in target):
        return Check(f"client {spec.id}", "FAIL", f"registered root does not exist: {target}")
    return Check(f"client {spec.id}", "WARN", "registered; root argument could not be validated")


def _hook_check(spec: ClientSpec) -> Optional[Check]:
    if not spec.hook_path or not os.path.exists(spec.hook_path):
        return None
    data, error = _load_json_soft(spec.hook_path)
    if error:
        return Check(f"hook {spec.id}", "FAIL", f"{spec.hook_path}: {error}")
    found = False
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        post = hooks.get("PostToolUse")
        if isinstance(post, list):
            for entry in post:
                if not isinstance(entry, dict):
                    continue
                for hook in entry.get("hooks", []) if isinstance(entry.get("hooks"), list) else []:
                    if isinstance(hook, dict) and _is_tier_hook(hook.get("command")):
                        found = True
    if found:
        return Check(f"hook {spec.id}", "PASS", spec.hook_path)
    return Check(f"hook {spec.id}", "INFO", "settings exist; TIER hook is not installed")


def run_doctor(root: str = ".", home: Optional[str] = None, env: Optional[dict] = None,
               platform: Optional[str] = None, find_executable=None) -> List[Check]:
    root = os.path.abspath(root or ".")
    find_executable = find_executable or shutil.which
    checks: List[Check] = []

    ok_py = sys.version_info >= (3, 10)
    checks.append(Check("python", "PASS" if ok_py else "FAIL",
                        f"{sys.version.split()[0]} (requires >=3.10)"))

    tier_exe = find_executable("tier")
    mcp_exe = find_executable("tier-mcp")
    if tier_exe and mcp_exe:
        checks.append(Check("entrypoints", "PASS", f"tier={tier_exe}; tier-mcp={mcp_exe}"))
    elif importlib.util.find_spec("tier") is not None:
        missing = ", ".join(x for x, v in (("tier", tier_exe), ("tier-mcp", mcp_exe)) if not v)
        checks.append(Check("entrypoints", "WARN", f"package importable; missing on PATH: {missing}"))
    else:
        checks.append(Check("entrypoints", "FAIL", "tier package is not importable"))

    js = _count_js()
    node = find_executable("node")
    token_ok = bool(js and node and os.path.isdir(os.path.join(os.path.dirname(js), "node_modules")))
    if token_ok:
        checks.append(Check("tokenizer", "PASS", f"o200k via {js}"))
    else:
        checks.append(Check("tokenizer", "WARN", "Node/gpt-tokenizer unavailable; using ~chars/4 estimate"))

    if os.path.isdir(root):
        checks.append(Check("root", "PASS", root))
    else:
        checks.append(Check("root", "FAIL", f"not a directory: {root}"))
        return checks

    repo_path = os.path.join(root, "repo.tier")
    if not os.path.isfile(repo_path):
        checks.append(Check("repo.tier", "WARN", "missing; run `tier from-repo . -o repo.tier`"))
    else:
        try:
            doc = _load(repo_path)
        except (OSError, ValueError) as e:
            checks.append(Check("repo.tier", "FAIL", str(e)))
        else:
            if not is_repo_doc(doc):
                checks.append(Check("repo.tier", "FAIL", "not a repository map (no = file metadata)"))
            else:
                checks.append(Check("repo.tier", "PASS", f"{len(doc.sections)} modules"))
                issues = lint(doc, max_section_tokens=10**9)
                errors = [m for level, m in issues if level == "error"]
                warnings = [m for level, m in issues if level == "warn"]
                if errors:
                    checks.append(Check("repo lint", "FAIL", "; ".join(errors[:3])))
                elif warnings:
                    checks.append(Check("repo lint", "WARN", "; ".join(warnings[:3])))
                else:
                    checks.append(Check("repo lint", "PASS", "0 errors, 0 warnings"))
                try:
                    result = sync_repo(repo_path, root=root, write=False)
                except (OSError, ValueError) as e:
                    checks.append(Check("repo sync", "FAIL", str(e)))
                else:
                    stale = result["changed"] + result["added"] + result["deleted"]
                    if stale:
                        checks.append(Check("repo sync", "WARN",
                                            f"stale: {', '.join(stale[:5])}; run `tier sync repo.tier`"))
                    else:
                        checks.append(Check("repo sync", "PASS", "up to date"))

    specs = client_specs(root, home=home, env=env, platform=platform,
                         find_executable=find_executable)
    existing = [s for s in specs if os.path.exists(s.config_path)]
    if not existing:
        checks.append(Check("clients", "INFO", "no supported client configs detected"))
    else:
        configured = [s.id for s in specs if s.configured]
        checks.append(Check(
            "clients",
            "PASS" if configured else "INFO",
            f"{len(existing)} config(s) found; TIER registered in "
            + (", ".join(configured) if configured else "none"),
        ))
    for spec in existing:
        checks.append(_check_entry(spec, root, find_executable))
    for spec in specs:
        hook = _hook_check(spec)
        if hook is not None:
            checks.append(hook)

    if importlib.util.find_spec("mcp") is not None:
        checks.append(Check("mcp extra", "PASS", "official mcp SDK installed (SDK adapter available)"))
    else:
        checks.append(Check("mcp extra", "INFO", 'optional; install with `pip install "tier-ctx[mcp]"`'))
    return checks


def exit_code(checks: Sequence[Check]) -> int:
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def render(checks: Sequence[Check], ui: UI) -> None:
    rows = [[c.name, c.status, c.detail] for c in checks]
    ui.table(["check", "status", "detail"], rows)
    fails = sum(1 for c in checks if c.status == "FAIL")
    warns = sum(1 for c in checks if c.status == "WARN")
    ui.panel("Diagnostics", [
        f"{len(checks)} checks: {fails} failed, {warns} warnings",
        "Overall: " + ("FAIL" if fails else "PASS"),
    ])


def render_json(checks: Sequence[Check]) -> str:
    return json.dumps([c.__dict__ for c in checks], indent=2)
