"""TIER command-line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .converter import from_md, from_payload, to_md
from .lint import lint
from .memory import remember
from .models import TABLE_MODES, _atomic_write, _read_text
from .parser import _load, dumps, to_obj
from .pipe import hook_process, pipe_compress
from .plugin import init_plugin
from .repo import from_repo, impact, render_impact, slice_symbols, sync_repo
from .retrieval import find, get, index
from .tokens import count_tokens
from .ui import default_ui


SCHEMAS = ("mcpServers", "servers", "opencode", "context_servers",
           "codex-toml", "continue-yaml", "skill-md")


def _stdin_text() -> str:
    stream = getattr(sys.stdin, "buffer", None)
    if stream is not None:
        return stream.read().decode("utf-8", "replace")
    return sys.stdin.read()


def _resolve_target(target):
    """Resolve a .tier file or directory to (path, doc, work_root).

    Directories (and the default ``.``) go through the repository cache, so the
    invisible map is built on first use and synced afterwards.
    """
    from .cache import ensure_doc

    return ensure_doc(target or ".")

def _split_slice_args(file_arg, module, symbols):
    """Normalize the three slice forms to ``(map_target, module, [symbols])``.

    Accepted:
      ``slice MAP MODULE SYM...``  -- map first (a .tier path or a directory)
      ``slice MODULE SYM...``      -- map defaults to the repository cache

    Only a `.tier` path or an existing directory counts as the map. A module
    argument routinely names a real source file (`a.py`), so a plain file must
    not be mistaken for the map.
    """
    candidate = str(file_arg or ".")
    symbols = list(symbols or [])
    if candidate.endswith(".tier") or os.path.isdir(candidate):
        return candidate, module or "", symbols
    # Short form: argparse put MODULE in `file` and the first SYM in `module`.
    first = module or ""
    if first:
        symbols.insert(0, first)
    return ".", candidate, symbols


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    from .models import VERSION as _VERSION

    ap = argparse.ArgumentParser(prog="tier", description=__doc__.split("\n")[0])
    ap.add_argument("-V", "--version", action="version", version=f"tier {_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index", help="L0+L1: title, summary, rules, section index")
    p.add_argument("file", nargs="?", default=".", help=".tier file or directory")
    p.add_argument("--no-tokens", action="store_true")
    p = sub.add_parser("get", help="fetch section(s) by id")
    p.add_argument("file", nargs="?", default=".")
    p.add_argument("ids", nargs="+")
    p.add_argument("--follow", action="store_true", help="also pull 1-hop '>' references")
    p.add_argument("--table", choices=TABLE_MODES, default="raw",
                   help="table rendering: raw (on-disk), aligned, or anchored (LLM-optimized)")
    p = sub.add_parser("find", help="keyword search -> section ids")
    p.add_argument("file", nargs="?", default=".")
    p.add_argument("query", nargs="+")
    p.add_argument("-k", type=int, default=3)
    p = sub.add_parser("lint")
    p.add_argument("file", nargs="?", default=".")
    p = sub.add_parser("build", help="canonicalize (strip comments/blank lines)")
    p.add_argument("file")
    p.add_argument("-o")
    p.add_argument("--pretty", action="store_true")
    p = sub.add_parser("from-md", help="convert Markdown -> TIER")
    p.add_argument("file")
    p.add_argument("-o")
    p.add_argument("--keep-emphasis", action="store_true")
    p = sub.add_parser("compress", help="convert raw Markdown/JSON (file or stdin) -> TIER or L0 index")
    p.add_argument("file", nargs="?", default="-", help="input file, or - for stdin")
    p.add_argument("--format", choices=["auto", "md", "json"], default="auto")
    p.add_argument("--index", action="store_true", help="emit only the L0 index")
    p.add_argument("-o")
    p = sub.add_parser("from-repo", help="index a source repository into a TIER architecture map")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("-o")
    p.add_argument("--index", action="store_true", help="emit only the L0 index")
    p.add_argument("--no-tokens", action="store_true")
    p = sub.add_parser("remember", help="persist a rule, section, key, or reference to a .tier memory file")
    p.add_argument("file")
    p.add_argument("--rule", help="add a global rule")
    p.add_argument("--section", help="section id for upsert or append")
    p.add_argument("--title", default="")
    p.add_argument("--abstract", default="")
    p.add_argument("--body", action="append", help="body line for section upsert (repeatable)")
    p.add_argument("--append-rule", help="append a rule to --section")
    p.add_argument("--key", nargs=2, metavar=("KEY", "VALUE"), help="upsert a key/value in --section")
    p.add_argument("--ref", help="append a reference to --section")
    p.add_argument("--note", default="")
    p = sub.add_parser("pipe", help="compress stdin JSON/Markdown/log output")
    p.add_argument("--format", choices=["auto", "json", "md", "log"], default="auto")
    p.add_argument("--max-tokens", type=int)
    p.add_argument("-o", "--out", help="save the full TIER artifact here")
    p = sub.add_parser("init-plugin", help="configure .mcp.json, repo.tier, hooks, and agent instructions")
    p.add_argument("target")
    p.add_argument("--convert-docs", action="store_true")
    p.add_argument("--write-instructions", choices=["claude", "agents", "both"])
    p.add_argument("--hook", action="store_true", help="add a PostToolUse compression hook")
    p = sub.add_parser("install", aliases=["setup"],
                       help="universal MCP/agent installer for 25 supported clients")
    p.add_argument("target", nargs="?", default=".")
    p.add_argument("--clients", help="comma-separated client ids, or all/none/detected")
    p.add_argument("-y", "--yes", action="store_true", help="accept defaults; never prompt")
    p.add_argument("--dry-run", action="store_true", help="preview changes without writing")
    p.add_argument("--uninstall", action="store_true", help="remove TIER entries only")
    p.add_argument("--no-repo", action="store_true", help="skip repo.tier generation/sync")
    p.add_argument("--no-hook", action="store_true", help="skip the Claude Code PostToolUse hook")
    p.add_argument("--instructions",
                   choices=["both", "claude", "agents", "gemini", "none"], default="both")
    p.add_argument("--custom-config", metavar="PATH",
                   help="install into any custom config path (.json/.toml/.yaml/.md)")
    p.add_argument("--schema", choices=list(SCHEMAS),
                   help="schema for --custom-config (inferred from the file name by default)")
    p.add_argument("--print-config", action="store_true",
                   help="print ready-to-copy config snippets for every client schema")
    p.add_argument("--plain", action="store_true", help="disable rich/ANSI styling")
    p.add_argument("--no-color", action="store_true")
    p = sub.add_parser("config-snippet",
                       help="print ready-to-copy MCP config snippets for every client schema")
    p.add_argument("target", nargs="?", default=".")
    p = sub.add_parser("clients", help="list every supported client id and its config path")
    p.add_argument("target", nargs="?", default=".")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p = sub.add_parser("doctor", help="check environment, configs, repo.tier, and sync health")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--plain", action="store_true")
    p.add_argument("--no-color", action="store_true")
    p = sub.add_parser("slice", help="fetch L3 source lines for named symbols")
    p.add_argument("file", nargs="?", default=".",
                   help=".tier file or directory (defaults to the auto-managed cache)")
    p.add_argument("module", nargs="?")
    p.add_argument("symbols", nargs="*")
    p.add_argument("--root")
    p.add_argument("--context", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("sync", help="incrementally refresh a repo.tier from source files")
    p.add_argument("file", nargs="?", default=".")
    p.add_argument("--root")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--rehash", action="store_true",
                   help="ignore the mtime+size fast path and compare content hashes")
    p = sub.add_parser("impact", help="reverse dependency and blast-radius analysis")
    p.add_argument("file", nargs="?", default=".")
    p.add_argument("target", nargs="?")
    p.add_argument("--root")
    p.add_argument("--git", action="store_true")
    p = sub.add_parser("hook", help="Claude Code-style JSON hook filter on stdin")
    p.add_argument("--max-tokens", type=int, default=600)
    p.add_argument("--format", choices=["auto", "json", "md", "log"], default="auto")
    p.add_argument("--fast", action="store_true",
                   help="use the chars/4 estimator instead of exact o200k counts "
                        "(faster, but the token budget becomes best-effort)")
    p = sub.add_parser("to-md")
    p.add_argument("file")
    p = sub.add_parser("to-json")
    p.add_argument("file")
    p = sub.add_parser("stats", help="token cost per layer / section")
    p.add_argument("file")
    p = sub.add_parser("context",
                       help="one-call context: route, pack L2/L3, and summarize impact")
    p.add_argument("target", nargs="?", default=".",
                   help=".tier file or directory (defaults to the auto-managed cache)")
    p.add_argument("query", nargs="+")
    p.add_argument("--budget-tokens", type=int, default=3000)
    p.add_argument("--root")
    p.add_argument("--no-impact", action="store_true")
    p.add_argument("--no-dedup", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd in ("install", "setup"):
        from .installer import install as install_mcp

        ui = default_ui(plain=a.plain, no_color=a.no_color)
        try:
            install_mcp(
                a.target, clients=a.clients, yes=a.yes, dry_run=a.dry_run,
                uninstall=a.uninstall, repo=not a.no_repo, hook=not a.no_hook,
                instructions=a.instructions, ui=ui,
                custom_config=a.custom_config, schema=a.schema,
                print_config=a.print_config,
            )
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if a.cmd == "config-snippet":
        from .installer import render_snippets

        sys.stdout.write(render_snippets(a.target))
        return 0

    if a.cmd == "clients":
        from .installer import client_specs

        specs = client_specs(a.target)
        if a.json:
            print(json.dumps([
                {"id": s.id, "label": s.label, "scope": s.scope,
                 "schema": s.schema, "config_path": s.config_path}
                for s in specs
            ], indent=2))
            return 0
        width = max(len(s.id) for s in specs)
        for s in specs:
            print(f"{s.id:<{width}}  {s.scope:<8} {s.schema:<16} {s.config_path}")
        return 0

    if a.cmd == "doctor":
        from .doctor import exit_code as doctor_exit_code
        from .doctor import render as doctor_render
        from .doctor import render_json as doctor_render_json
        from .doctor import run_doctor

        checks = run_doctor(a.root)
        if a.json:
            print(doctor_render_json(checks))
        else:
            doctor_render(checks, default_ui(plain=a.plain, no_color=a.no_color))
        return doctor_exit_code(checks)

    if a.cmd == "from-md":
        out = dumps(from_md(_read_text(a.file) if a.file != "-" else _stdin_text(),
                            strip_emphasis=not a.keep_emphasis))
        if a.o:
            _atomic_write(a.o, out)
            print(f"wrote {a.o}")
        else:
            sys.stdout.write(out)
        return 0

    if a.cmd == "compress":
        src = _stdin_text() if a.file == "-" else _read_text(a.file)
        try:
            d = from_payload(src, a.format)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        out = index(d) + "\n" if a.index else dumps(d)
        if a.o:
            _atomic_write(a.o, out)
            print(f"wrote {a.o}")
        else:
            sys.stdout.write(out)
        return 0

    if a.cmd == "from-repo":
        try:
            d = from_repo(a.path)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        out = (index(d, tokens=not a.no_tokens) + "\n") if a.index else dumps(d)
        if a.o:
            _atomic_write(a.o, out)
            print(f"wrote {a.o}")
        else:
            sys.stdout.write(out)
        return 0

    if a.cmd == "remember":
        if a.rule is not None and a.section:
            print("error: use either --rule or --section", file=sys.stderr)
            return 2
        try:
            if a.rule is not None:
                res = remember(a.file, "rule", text=a.rule)
            elif not a.section:
                print("error: --section is required unless --rule is used", file=sys.stderr)
                return 2
            elif a.append_rule is not None:
                res = remember(a.file, "append-rule", section=a.section, text=a.append_rule)
            elif a.key:
                res = remember(a.file, "append-kv", section=a.section, key=a.key[0], value=a.key[1])
            elif a.ref:
                res = remember(a.file, "append-ref", section=a.section, target=a.ref, note=a.note)
            else:
                res = remember(a.file, "section", section=a.section, title=a.title,
                               abstract=a.abstract, body=a.body)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(res["message"])
        return 0

    if a.cmd == "pipe":
        try:
            out = pipe_compress(_stdin_text(), a.format, a.max_tokens, a.out)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
        return 0

    if a.cmd == "init-plugin":
        try:
            res = init_plugin(a.target, convert_docs=a.convert_docs,
                              write_instructions=a.write_instructions, hook=a.hook)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"configured {res['mcp']}")
        print(f"wrote {res['repo']}")
        for p in res["docs"]:
            print(f"wrote {p}")
        for p in res["instruction_files"]:
            print(f"updated {p}")
        if res.get("hook"):
            print(f"configured hook {res['hook']}")
        print()
        sys.stdout.write(res["instructions"])
        return 0

    if a.cmd == "context":
        from .context import build_context

        try:
            res = build_context(
                a.target, " ".join(a.query), root=a.root,
                budget_tokens=a.budget_tokens,
                include_impact=not a.no_impact, dedup=not a.no_dedup,
            )
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        sys.stdout.write(res["text"] if res["text"].endswith("\n") else res["text"] + "\n")
        return 0

    if a.cmd == "slice":
        # `tier slice repo.tier mod sym...` and `tier slice mod sym...` both work:
        # a leading token that is not an existing .tier file is the module.
        map_target, module, symbols = _split_slice_args(a.file, a.module, a.symbols)
        try:
            path, doc, work_root = _resolve_target(map_target)
            root = a.root or work_root
            text, missing = slice_symbols(doc, module, symbols, root, a.context)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if text:
            print(text)
        if missing:
            print(f"unknown symbol(s)/file: {', '.join(missing)}", file=sys.stderr)
            return 1
        return 0

    if a.cmd == "sync":
        try:
            path, doc, work_root = _resolve_target(a.file)
            res = sync_repo(path, root=a.root or work_root,
                            write=not a.no_write, rehash=a.rehash)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"sync {res['path']}: {len(res['changed'])} changed, {len(res['added'])} added, "
              f"{len(res['deleted'])} deleted, {res['unchanged']} unchanged"
              + (" (written)" if res["written"] else " (no changes)"))
        for rel in res["changed"]:
            print(f"  changed {rel}")
        for rel in res["added"]:
            print(f"  added   {rel}")
        for rel in res["deleted"]:
            print(f"  deleted {rel}")
        return 0

    if a.cmd == "impact":
        try:
            target = a.target
            if target is None:
                # `tier impact <target>`: no path given, so the first token is the target.
                target = a.file
                path, doc, work_root = _resolve_target(".")
            else:
                path, doc, work_root = _resolve_target(a.file)
            res = impact(doc, target, root=a.root or work_root, git=a.git)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(render_impact(res))
        return 0

    if a.cmd == "hook":
        try:
            response = hook_process(_stdin_text(), a.max_tokens, a.format, exact=not a.fast)
        except Exception as e:
            response = {"continue": True, "systemMessage": f"tier hook error: {e}"}
        print(json.dumps(response, ensure_ascii=False))
        return 0

    try:
        _path, doc, _work_root = _resolve_target(a.file)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.cmd == "index":
        print(index(doc, tokens=not a.no_tokens))
    elif a.cmd == "get":
        text, missing = get(doc, a.ids, a.follow, a.table)
        if text:
            print(text)
        if missing:
            print(f"unknown id(s): {', '.join(missing)}", file=sys.stderr)
            return 1
    elif a.cmd == "find":
        for i, sc, ab in find(doc, " ".join(a.query), a.k):
            print(f"@{i}  {sc}  {ab}")
    elif a.cmd == "lint":
        iss = lint(doc)
        for lvl, msg in iss:
            print(f"{lvl}: {msg}")
        print(f"{len(doc.sections)} sections, {sum(1 for l, _ in iss if l == 'error')} errors, "
              f"{sum(1 for l, _ in iss if l == 'warn')} warnings")
        return 1 if any(l == "error" for l, _ in iss) else 0
    elif a.cmd == "build":
        out = dumps(doc, pretty=a.pretty)
        if a.o:
            _atomic_write(a.o, out)
            print(f"wrote {a.o}")
        else:
            sys.stdout.write(out)
    elif a.cmd == "to-md":
        sys.stdout.write(to_md(doc))
    elif a.cmd == "to-json":
        print(json.dumps(to_obj(doc), indent=2, ensure_ascii=False))
    elif a.cmd == "stats":
        full = dumps(doc)
        idx = index(doc, tokens=False)
        counts, backend = count_tokens([full, idx] + [s.text() for s in doc.sections])
        print(f"tokenizer: {backend}")
        print(f"full doc : {counts[0]}")
        print(f"L0 index : {counts[1]}  ({100 * counts[1] / max(1, counts[0]):.0f}% of full)")
        for s, c in zip(doc.sections, counts[2:]):
            print(f"  @{s.id:<28} {c}")
    return 0
