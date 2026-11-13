#!/usr/bin/env python3
"""MCP stdio server for TIER docs. No dependencies.

    python -m tier.mcp /path/to/docs        # serves every *.tier under that dir

Tools:
  tier_list     - one line per doc: path, ~tokens, title - summary    (corpus-level L0)
  tier_index    - L0+L1 for one doc: summary, global rules, section index
  tier_find     - hybrid identifier/fuzzy search -> best section ids
  tier_get      - fetch section(s), with session dedup and optional table rendering
  tier_slice    - fetch exact L3 source lines for named symbols
  tier_impact   - reverse/forward dependency and Git blast-radius analysis
  tier_sync     - incrementally refresh a repo.tier from disk
  tier_lint     - validate a doc
  tier_ingest   - compress Markdown/JSON/log streams, optionally within a token budget
  tier_repo     - index a repository directory into a layered TIER architecture map
  tier_remember - persist rules, sections, keys, or references into a .tier memory file
"""
import json
import os
import sys
import time

from ..lint import lint
from ..memory import remember
from ..models import Doc, VERSION, _atomic_write, _inside
from ..parser import _repo_file_meta, dumps, is_repo_doc, parse
from ..pipe import from_stream, pipe_compress
from ..repo import from_repo, impact, render_impact, slice_symbols, sync_repo
from ..retrieval import Session, find, index
from ..tokens import count_tokens

ROOT = os.path.realpath(sys.argv[1] if len(sys.argv) > 1 else ".")
PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = {"2025-06-18", "2025-03-26", "2024-11-05"}
SESSION = Session()
AUTO_SYNC_TTL = float(os.environ.get("TIER_AUTO_SYNC_TTL", "1.5"))
_SYNC_STATE = {}



TOOLS = [
    {
        "name": "tier_list",
        "description": "List available TIER docs with a one-line summary each. Call first to discover docs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tier_index",
        "description": "Get a doc's summary, global rules, and section index (id, token cost, rule count, abstract). "
        "Read this BEFORE fetching sections; then fetch only what you need with tier_get.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "doc path relative to root"}},
            "required": ["path"],
        },
    },
    {
        "name": "tier_find",
        "description": "Hybrid search over a doc: identifier splitting, synonyms, and character n-gram "
        "fallback. Returns best-matching section ids with scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 3},
            },
            "required": ["path", "query"],
        },
    },
    {
        "name": "tier_get",
        "description": "Fetch one or more sections by id. Set follow=true to also pull sections they reference. "
        "Sections already returned earlier in this session are replaced by a one-line stub; set force=true "
        "to resend them, or reset=true to clear the session cache for this doc first. table_format=anchored "
        "renders tables with row anchors and column labels for better field retrieval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "ids": {"type": "array", "items": {"type": "string"}},
                "follow": {"type": "boolean", "default": False},
                "table_format": {"type": "string", "enum": ["raw", "aligned", "anchored"], "default": "raw"},
                "force": {"type": "boolean", "default": False},
                "reset": {"type": "boolean", "default": False},
            },
            "required": ["path", "ids"],
        },
    },
    {
        "name": "tier_slice",
        "description": "Fetch exact L3 source lines for one or more symbols recorded in a repo.tier "
        "symbol table. Returns @module#symbol [Lstart-Lend] plus a fenced source slice. Already-served "
        "slices are stubbed unless force=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "module": {"type": "string", "description": "module section id or relative file path"},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "context_lines": {"type": "integer", "default": 0},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["path", "module", "symbols"],
        },
    },
    {
        "name": "tier_impact",
        "description": "Analyze direct/transitive dependents and dependencies for a section or symbol. "
        "With git=true, includes changed modules, changed symbols, and downstream blast radius.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "target": {"type": "string", "description": "section id, file path, or unique symbol name"},
                "git": {"type": "boolean", "default": False},
            },
            "required": ["path", "target"],
        },
    },
    {
        "name": "tier_sync",
        "description": "Incrementally refresh a repo.tier from disk: re-parse only changed or added files, "
        "remove deleted modules, and invalidate changed sections and L3 slices in the session cache.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "tier_lint",
        "description": "Validate a TIER doc (table row counts, dangling refs, duplicate ids).",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "tier_ingest",
        "description": "Compress raw Markdown, JSON, or command/log output into TIER on the fly. "
        "Auto-detects the format. Returns the full TIER document, its L0 index, or a budgeted view "
        "with only priority sections. Use max_tokens to cap context; save_as persists the full artifact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "raw Markdown, JSON, or log text"},
                "format": {"type": "string", "enum": ["auto", "md", "json", "log"], "default": "auto"},
                "index_only": {"type": "boolean", "default": False},
                "max_tokens": {"type": "integer", "description": "optional context budget"},
                "save_as": {"type": "string", "description": "optional .tier path under the server root"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "tier_repo",
        "description": "Index a repository directory into a layered TIER architecture map. "
        "Each module section contains exported symbol signatures with line ranges and '>' edges to "
        "local dependencies. Use index_only=true for just the L0 map.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": ".", "description": "directory relative to server root"},
                "index_only": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "tier_remember",
        "description": "Persist verified agent memory into a .tier file. Actions: rule (global rule), "
        "section (upsert a section with abstract/body), append-rule, append-kv, or append-ref. "
        "The write is lint-checked and atomic; the section is invalidated in the session cache.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": ".tier memory file relative to root"},
                "action": {"type": "string", "enum": ["rule", "section", "append-rule", "append-kv", "append-ref"]},
                "section": {"type": "string"},
                "title": {"type": "string"},
                "abstract": {"type": "string"},
                "body": {"type": "array", "items": {"type": "string"}},
                "text": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "target": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["path", "action"],
        },
    },
]


def safe(path: str) -> str:
    p = os.path.realpath(os.path.join(ROOT, path))
    if not _inside(ROOT, p):
        raise ValueError("path escapes root")
    if not p.endswith(".tier") or not os.path.isfile(p):
        raise ValueError(f"no such .tier doc: {path}")
    return p


def safe_dir(path: str) -> str:
    p = os.path.realpath(os.path.join(ROOT, path))
    if not _inside(ROOT, p):
        raise ValueError("path escapes root")
    if not os.path.isdir(p):
        raise ValueError(f"no such directory: {path}")
    return p


def safe_write(path: str) -> str:
    p = os.path.realpath(os.path.join(ROOT, path))
    if not _inside(ROOT, p):
        raise ValueError("path escapes root")
    if not p.endswith(".tier"):
        raise ValueError("memory path must end with .tier")
    if os.path.isdir(p):
        raise ValueError(f"not a file: {path}")
    return p


def load(path: str) -> Doc:
    with open(safe(path), encoding="utf-8") as f:
        return parse(f.read())


def _repo_signature(p: str, doc: Doc) -> dict:
    """Cheap change detector: recorded file stats plus their directory stats."""
    root = os.path.dirname(p)
    files, dirs = {}, {}
    for sec in doc.sections:
        meta = _repo_file_meta(sec)
        rel = meta.get("path") if meta else ""
        if not rel:
            continue
        full = os.path.join(root, rel)
        try:
            st = os.stat(full)
            files[rel] = (st.st_mtime_ns, st.st_size)
        except OSError:
            files[rel] = None
        parent = os.path.dirname(full)
        try:
            dirs[parent] = os.stat(parent).st_mtime_ns
        except OSError:
            dirs[parent] = None
    return {"files": files, "dirs": dirs}


def _signature_matches(sig: dict) -> bool:
    for rel, expected in sig["files"].items():
        try:
            st = os.stat(rel if os.path.isabs(rel) else os.path.join(sig["root"], rel))
        except OSError:
            return False
        if expected is None or (st.st_mtime_ns, st.st_size) != expected:
            return False
    for parent, expected in sig["dirs"].items():
        try:
            if os.stat(parent).st_mtime_ns != expected:
                return False
        except OSError:
            return False
    return True


def _load_doc(path: str, auto_sync: bool = True):
    p = safe(path)
    cached = _SYNC_STATE.get(p)
    if auto_sync and cached and time.monotonic() - cached["at"] < AUTO_SYNC_TTL:
        sig = cached["sig"]
        if sig["root"] and _signature_matches(sig):
            return p, cached["doc"]
    with open(p, encoding="utf-8") as f:
        doc = parse(f.read())
    if auto_sync and is_repo_doc(doc):
        try:
            res = sync_repo(
                p, root=os.path.dirname(p), session=SESSION, session_key=p, write=True
            )
            doc = res["doc"]
            sig = _repo_signature(p, doc)
            sig["root"] = os.path.dirname(p)
            _SYNC_STATE[p] = {"doc": doc, "sig": sig, "at": time.monotonic()}
        except (OSError, ValueError):
            pass
    elif is_repo_doc(doc):
        sig = _repo_signature(p, doc)
        sig["root"] = os.path.dirname(p)
        _SYNC_STATE[p] = {"doc": doc, "sig": sig, "at": time.monotonic()}
    return p, doc


def call(name: str, a: dict) -> str:
    if not isinstance(a, dict):
        raise ValueError("arguments must be an object")
    if name == "tier_list":
        docs = []
        for dp, dirnames, fs in os.walk(ROOT):
            dirnames.sort()
            for f in sorted(fs):
                if not f.endswith(".tier"):
                    continue
                full = os.path.join(dp, f)
                if os.path.islink(full) or not _inside(ROOT, os.path.realpath(full)):
                    continue
                rel = os.path.relpath(full, ROOT)
                with open(full, encoding="utf-8", errors="replace") as fh:
                    docs.append((rel, parse(fh.read())))
        texts = [dumps(d) for _rel, d in docs]
        counts = count_tokens(texts)[0] if texts else []
        rows = []
        for (rel, d), toks in zip(docs, counts):
            t = d.meta.get("title", "")
            sm = d.meta.get("sum", "")
            rows.append(f"{rel} ~{toks}t {t}" + (f" - {sm}" if sm else ""))
        return "\n".join(rows) or "(no .tier docs found)"
    if name == "tier_index":
        _, d = _load_doc(a["path"])
        return index(d)
    if name == "tier_find":
        _, d = _load_doc(a["path"])
        hits = find(d, a["query"], int(a.get("k", 3)))
        return "\n".join(f"@{i} {s} {ab}" for i, s, ab in hits) or "no matches"
    if name == "tier_get":
        path = safe(a["path"])
        if a.get("reset"):
            SESSION.reset(path)
        _, d = _load_doc(a["path"])
        ids = a.get("ids") or []
        if isinstance(ids, str):
            ids = [ids]
        text, missing = SESSION.get(
            d, ids, bool(a.get("follow")), a.get("table_format", "raw"),
            key=path, force=bool(a.get("force")),
        )
        if missing:
            text += ("\n" if text else "") + f"unknown id(s): {', '.join(missing)}. available: " + ", ".join(
                s.id for s in d.sections
            )
        return text
    if name == "tier_slice":
        path, d = _load_doc(a["path"])
        symbols = a.get("symbols") or []
        if isinstance(symbols, str):
            symbols = [symbols]
        text, missing = slice_symbols(
            d, a["module"], symbols, root=os.path.dirname(path),
            context_lines=int(a.get("context_lines", 0)), session=SESSION,
            session_key=path, force=bool(a.get("force")),
        )
        if missing:
            text += ("\n" if text else "") + f"unknown symbol/file: {', '.join(missing)}"
        return text or "no symbols found"
    if name == "tier_sync":
        path = safe(a["path"])
        res = sync_repo(
            path, root=os.path.dirname(path), session=SESSION, session_key=path, write=True
        )
        sig = _repo_signature(path, res["doc"])
        sig["root"] = os.path.dirname(path)
        _SYNC_STATE[path] = {"doc": res["doc"], "sig": sig, "at": time.monotonic()}
        return (
            f"sync {path}: {len(res['changed'])} changed, {len(res['added'])} added, "
            f"{len(res['deleted'])} deleted, {res['unchanged']} unchanged"
            + (" (written)" if res["written"] else " (no changes)")
        )
    if name == "tier_impact":
        path, d = _load_doc(a["path"], auto_sync=False)
        res = impact(d, a["target"], root=os.path.dirname(path), git=bool(a.get("git")))
        return render_impact(res)
    if name == "tier_lint":
        iss = lint(load(a["path"]))
        return "\n".join(f"{l}: {m}" for l, m in iss) or "ok"
    if name == "tier_ingest":
        content = a.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        fmt = a.get("format", "auto")
        out_path = safe_write(a["save_as"]) if a.get("save_as") else None
        max_tokens = a.get("max_tokens")
        if max_tokens is not None:
            return pipe_compress(content, fmt, int(max_tokens), out_path)
        d = from_stream(content, fmt)
        full = dumps(d)
        if out_path:
            _atomic_write(out_path, full)
        return index(d) if a.get("index_only") else full
    if name == "tier_repo":
        d = from_repo(safe_dir(a.get("path", ".")))
        return index(d) if a.get("index_only") else dumps(d)
    if name == "tier_remember":
        path = safe_write(a["path"])
        res = remember(
            path, a["action"], section=a.get("section", ""), title=a.get("title", ""),
            abstract=a.get("abstract", ""), body=a.get("body"), text=a.get("text", ""),
            key=a.get("key", ""), value=a.get("value", ""), target=a.get("target", ""),
            note=a.get("note", ""),
        )
        if res.get("section"):
            SESSION.invalidate(path, res["section"])
        _SYNC_STATE.pop(path, None)
        return res["message"]
    raise ValueError(f"unknown tool {name}")


def handle(msg: dict):
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        requested = params.get("protocolVersion")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tier", "version": VERSION},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        try:
            args = p.get("arguments") or {}
            text, err = call(p.get("name"), args), False
        except Exception as e:  # report tool errors in-band, per MCP
            text, err = f"error: {e}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": err}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is not None:  # unknown request
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None  # notifications (e.g. notifications/initialized)


def main():
    try:
        sys.stdin.reconfigure(errors="replace")
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
