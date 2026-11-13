# TIER Architecture

TIER is a four-layer context format plus a local retrieval, repository-graph, and agent-memory engine. The reference implementation is a Python 3.10+ `src/`-layout package with a **stdlib-only core**. Optional extras add terminal styling (`rich`) and the official MCP SDK adapter (`mcp`), but importing or running the core never requires them.

See [docs/spec.md](docs/spec.md) for the file format and retrieval rules.

## Layer model

| Layer | Contents | Cost |
|---|---|---|
| **L0** | Document title, summary, global rules, and one index line per section | Always loaded; a few hundred tokens |
| **L1** | One-line abstract per section, embedded in the L0 index | Loaded with L0 |
| **L2** | Symbol signatures, file metadata, key/value trees, tables, `>` dependency edges | Fetched on demand for one module |
| **L3** | Exact source lines for one named symbol | Fetched surgically after L2 identifies the target |

`tier find` routes from L0/L1 to the right section. `tier get --follow` retrieves one module plus its direct local dependencies. `tier slice` returns exact L3 lines for a symbol. `tier impact` inverts the `>` graph and reports blast radius. `tier sync` keeps fingerprints and line ranges fresh.

## Package modules

```text
src/tier/
├── __init__.py        public API re-exports
├── __main__.py        python -m tier
├── models.py          Section/Doc, constants, regexes, table rendering, atomic IO, path confinement
├── tokens.py          optional persistent Node o200k worker, content-hash cache, chars/4 fallback
├── parser.py          line parser, nodes, serializer, untrusted-block guards, repo metadata
├── lint.py            table/reference/size validation
├── converter.py       Markdown/JSON -> TIER, TIER -> Markdown
├── retrieval.py       L0 index, hybrid search, get, Session deduplication
├── cache.py           repository-index cache resolution (.git/info or user cache)
├── context.py         one-turn composite retrieval: route, pack L2/L3, impact
├── repo.py            polyglot repo maps, symbol extraction, sync, L3 slices, impact
├── memory.py          atomic, lint-checked write-back memory
├── pipe.py            stream/log compression and Claude Code JSON hook
├── plugin.py          legacy init-plugin bootstrap
├── installer.py       cross-client installer, safe JSON merging, dry-run/uninstall
├── doctor.py          environment, config, repo, and sync diagnostics
├── ui.py              ANSI/box-drawing UI with optional rich enhancement
├── cli.py             `tier` command dispatcher
└── mcp/
    ├── __init__.py
    ├── __main__.py    python -m tier.mcp
    ├── server.py      zero-dependency JSON-RPC 2.0 MCP server (12 tools)
    └── sdk_server.py  optional official MCP SDK adapter (MCPServer / FastMCP fallback)
```

## Import direction

Dependencies point one way; there are no import cycles. The optional `ui` and `mcp` extras are imported lazily or only by the adapter.

| Module | Imports from `tier` |
|---|---|
| `models` | — |
| `tokens` | — |
| `ui` | — |
| `parser` | `models` |
| `lint` | `models`, `parser`, `tokens` |
| `converter` | `models`, `parser` |
| `retrieval` | `models`, `parser`, `tokens` |
| `cache` | `models`, `parser`, `repo` |
| `context` | `cache`, `models`, `parser`, `repo`, `retrieval`, `tokens` |
| `repo` | `converter`, `models`, `parser`, `retrieval` |
| `memory` | `lint`, `models`, `parser` |
| `pipe` | `converter`, `models`, `parser`, `retrieval`, `tokens` |
| `plugin` | `converter`, `models`, `parser`, `repo`, `retrieval` |
| `installer` | `converter`, `models`, `parser`, `plugin`, `repo`, `retrieval`, `tokens`, `ui` |
| `doctor` | `installer`, `lint`, `parser`, `repo`, `tokens`, `ui` |
| `cli` | `converter`, `doctor`, `installer`, `lint`, `memory`, `models`, `parser`, `pipe`, `plugin`, `repo`, `retrieval`, `tokens` |
| `mcp.server` | `lint`, `memory`, `models`, `parser`, `pipe`, `repo`, `retrieval`, `tokens` |
| `mcp.sdk_server` | `mcp.server` (plus the optional `mcp` package) |

## Runtime flows

### Documents: parse -> retrieve
1. `parser.parse` classifies lines by sigil and treats tables, fenced code, and untrusted blocks as opaque.
2. `retrieval.index` builds L0 and per-section token/rule counts.
3. `retrieval.find` combines identifier splitting, BM25-style weights, synonyms, trigrams, table-field boosts, and navigation priors.
4. `retrieval.get` renders sections; `Session` replaces repeated bodies and L3 slices with stubs.
5. `converter` and `pipe` create TIER from Markdown, JSON, logs, and hook payloads.

### Repositories: map -> cache -> slice -> sync -> impact
1. `repo.from_repo` scans source files, extracts Python AST symbols plus JS/TS/Go/Rust regex symbols, records fingerprints, and creates `>` edges for local imports.
2. `repo.slice_symbols` resolves a module and symbol, checks the real path against the root, and reads only the recorded source lines.
3. `repo.sync_repo` reparses changed/added files, removes deleted modules and references, refreshes ranges, and invalidates affected session entries.
4. `repo.impact` inverts the module graph, reads the `calls` tables for symbol-level callers,
   and optionally compares `repo.tier` symbols with Git changes.
5. `repo.sync_repo` skips files whose recorded mtime and size are unchanged, and rehashes on demand.

### Installer and diagnostics
1. `installer.client_specs` resolves project/user config paths for Claude Code, Claude Desktop, Cursor, VS Code, and Windsurf.
2. `installer.install` merges `tier` server entries, backup files, hooks, and marked instruction blocks atomically; it supports dry-run and focused uninstall.
3. `doctor.run_doctor` checks the interpreter, entrypoints, tokenizer, repo health, sync freshness, and every detected client config.

### MCP
- `mcp/server.py` is the default stdio JSON-RPC server (`tier-mcp`, `python -m tier.mcp`). It has no dependencies and auto-syncs repository maps. `tier_context` is listed first and performs routing, L2/L3 packing, and impact summary in one call.
- `mcp/sdk_server.py` is optional. It wraps the same 12 tools with MCP v2 `MCPServer` (v1 `FastMCP` fallback) for `tier-mcp-sdk`, `mcp dev`, and Streamable HTTP.

## Contributor guide

```sh
python3 -m pip install -e . --no-build-isolation
python3 -m unittest discover -s tests -q
tier doctor .
python3 benchmarks/bench.py
```

After changing parsers, converters, symbol extraction, or repository behavior:

```sh
tier from-repo . -o repo.tier
tier from-md docs/spec.md -o SPEC.tier
tier lint repo.tier
tier lint SPEC.tier
tier lint examples/handbook.tier
tier lint examples/payments-runbook.tier
```

### Invariants

- **Zero required Python dependencies.** `pyproject.toml` keeps `dependencies = []`; `rich` and `mcp` are optional extras and must be imported lazily.
- **Python 3.10+.** Python 3.9 reached end of life in October 2025, and the official `mcp` SDK requires 3.10+.
- **No import cycles.** Keep the one-way layers above and re-export public names from `tier/__init__.py`.
- **Round-trip stability.** `dumps(parse(text))` must be stable for valid documents; malformed input must never raise an unhandled exception.
- **Sandbox rules.** MCP paths, slices, repository scans, and memory writes must resolve inside the configured root; symlinks outside it are rejected.
- **Test every boundary.** Add a unit or red-team test for every parser, guard, path, symbol-range, installer, or config-merge change.
- **Refresh the indexes.** Regenerate `repo.tier`, `SPEC.tier`, and `benchmarks/BENCH.txt` when source or benchmarks change.

### Where to change what

| Change | Module |
|---|---|
| New sigil, parser rule, serializer behavior | `models.py`, `parser.py` |
| Markdown/JSON conversion | `converter.py` |
| Search ranking, table rendering, session cache | `retrieval.py` |
| Python/JS/TS/Go/Rust symbols, sync, slices, impact | `repo.py` |
| Memory writes | `memory.py` |
| Pipe/hook behavior | `pipe.py` |
| Installer clients, backups, dry-run, uninstall | `installer.py` |
| Health checks | `doctor.py` |
| Terminal styling | `ui.py` |
| CLI commands/flags | `cli.py` |
| MCP tools | `mcp/server.py` and `mcp/sdk_server.py` |

When adding an MCP tool, add it to both servers and update `docs/spec.md`, the README tool reference, and the MCP tests. When adding an installer client, add its config path/key, a platform-path test, a merge/backup test, and a doctor test.
