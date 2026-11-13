# MCP server

TIER ships a Model Context Protocol server that exposes a repository or document tree to an agent
as routed sections, exact source slices, and a dependency graph rather than whole files. The
server speaks JSON-RPC 2.0 over stdio and is implemented against the Python standard library
alone, so it runs with no runtime dependencies; the optional official-SDK adapter exists only for
tooling that requires it. It exposes 12 tools, led by `tier_context`, a composite retrieval call
intended to replace most tool round-trips.

## Starting the server

```sh
tier-mcp /path/to/root              # console script
python3 -m tier.mcp /path/to/root   # same server, module entry point
tier-mcp --help                     # usage, environment note, and the tool list
tier-mcp --version                  # tier-mcp <version>
```

`ROOT` defaults to the current directory. It is resolved once at process start and every path
argument is confined to it: `safe_target`, `safe`, `safe_dir`, and `safe_write` resolve each
argument with `realpath` and reject anything that does not stay inside the root (`path escapes
root`), is not a `.tier` file where one is required, or does not exist. `tier_ingest` and
`tier_remember` write only to paths ending in `.tier` under that root.

## Path arguments

A `path` parameter is interpreted the same way in every tool that accepts one:

- **Omitted** — the server root is used.
- **A directory** — resolves through the repository cache: the map is built on first use and
  incrementally synced on every later use.
- **A `.tier` file** — used exactly as given, then synced if it is a repository map.

`tier_repo` is the one exception: it requires a directory, because indexing a map file is not
meaningful. `tier_get`, `tier_sync`, and `tier_lint` require a `.tier` file; a directory is rejected
with `no such .tier doc: <path>`. `tier_list` takes no path at all and always walks the root.

Resolution order for a directory:

1. An explicit `.tier` path, when the caller passed one.
2. `<root>/repo.tier` when it exists — a tracked map always wins, which keeps a checked-in map and
   any CI freshness gate meaningful.
3. `<gitdir>/info/tier/repo.tier` when the repository's git directory is writable. For an ordinary
   checkout that is `<root>/.git/info/tier/repo.tier`. Git ignores everything under a git directory,
   so the cache is untracked by construction: no `.gitignore` entry is needed and `git status` stays
   clean.
4. `<cache-root>/<sha256(realpath(root))[:12]>/repo.tier` otherwise, outside the worktree.
   `<cache-root>` is `$TIER_CACHE_DIR`, else the platform cache dir —
   `%LOCALAPPDATA%\tier\cache` on Windows, `$XDG_CACHE_HOME/tier`, or `~/.cache/tier`.

### Worktrees and submodules

In a `git worktree` or a submodule, `.git` is a file, not a directory. It holds a single line,
`gitdir: <path>`; a relative target resolves against the directory containing `.git`, as Git does.
The server reads that pointer before choosing a cache location, so such a checkout does not silently
fall through to the user cache.

| Checkout | Git directory | Map location |
|---|---|---|
| ordinary clone | `<root>/.git` | `<root>/.git/info/tier/repo.tier` |
| linked worktree | `<main>/.git/worktrees/<name>` | `<gitdir>/info/tier/repo.tier` |
| submodule | `<super>/.git/modules/<name>` | `<gitdir>/info/tier/repo.tier` |
| read-only git directory | not writable | `<cache-root>/<digest>/repo.tier` |

A per-checkout admin directory is used as given rather than collapsed onto the main repository's
`.git`: worktrees exist to hold different branches, so one shared map would be rebuilt on every
checkout switch and could serve the wrong branch's symbols. Where a git directory carries a
`commondir` file, that is resolved first, so the metadata directory it names is the one used. Each
checkout gets its own map, and every one of those locations is inside a git directory that Git
ignores.

When the map lives outside its repository, a `.owner` file is written beside it recording the work
root, because neither `<gitdir>/info/tier/` nor a digest directory contains the sources. That
manifest is how a later resolve recovers the checkout. `tier_list` skips these directories — it
prunes `.git` and anything `is_cache_dir` recognises, so cached maps never appear in the listing.

Reads are cheap after the first one. A repository map loaded from a `.tier` file is re-checked for
changes at most once per `TIER_AUTO_SYNC_TTL` seconds (default `1.5`), using recorded file and
directory `mtime` + `size` values as a change detector; the sync itself only re-parses files whose
fingerprint moved.

The wire protocol is one JSON object per line on stdin, one response per line on stdout. The
server answers `initialize` (negotiating `2025-06-18`, `2025-03-26`, or `2024-11-05`, defaulting to
`2025-06-18`), `tools/list`, `tools/call`, and `ping`; notifications such as
`notifications/initialized` produce no output; an unknown request method returns `-32601` and an
unparseable line returns `-32700`. Tool failures are reported in-band, per MCP: the result carries
`"isError": true` and the message as text, so a bad argument never kills the session.

## The one-turn workflow: tier_context

`tier_context` answers a question about a codebase or document in a single call. It is listed
first in `tools/list` and is the intended default entry point; the granular `tier_*` tools exist
for the cases where you already know the exact section id, symbol, or check you want.

Its internal pipeline has four steps:

1. **Resolve the target.** An omitted `path` means the server root. A directory resolves through
   the repository cache, which is built on first use and incrementally synced afterwards; an
   explicit `.tier` path is loaded as given and synced if it is a repository map.
2. **Hybrid route.** The query goes through the same retriever as `tier_find`: BM25-style field
   weights, identifier splitting, a synonym map, and a character-trigram fallback. The top 8
   section ids are kept in rank order.
3. **Symbol match.** For repository maps, symbol rows are scored against the query tokens — an
   exact leaf or full-name match scores 3, a prefix match 2, a substring match 1 — and the
   matching module/symbol pairs are merged into the routing order after the section hits.
4. **Budgeted packing.** Up to 3 section bodies (L2) are rendered first, then up to 3 L3 source
   slices for matched symbols, then a compact impact block for the top module. Each part is
   admitted only if the running token count stays within `budget_tokens`; anything rejected is
   named on a final `Omitted for budget:` line.

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "tier_context",
    "arguments": {
      "query": "how does symbol slicing resolve a module",
      "budget_tokens": 1200
    }
  }
}
```

Response, trimmed:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [{ "type": "text", "text": "## Context: how does symbol slicing…" }],
    "isError": false
  }
}
```

The same `text` field, unwrapped, is ordinary TIER output — a header, routed section bodies, L3
slices, and an impact summary, in that order:

````text
## Context: how does symbol slicing resolve a module

@src-tier-repo.py src/tier/repo.py
? src/tier/repo.py (python module); symbols: repo_source_files, from_repo, sync_repo, …
= file
  path src/tier/repo.py
  language python
  lines 1694
…

@src-tier-repo.py#slice_symbols [L1357-L1413]
```python
def slice_symbols(doc, module, symbols, root=None, context_lines=0,
                  session=None, session_key="", force=False):
    …
```

## Impact: @src-tier-repo.py
Direct dependents: @src-tier-cli.py, @src-tier-context.py, @src-tier-mcp-server.py
Transitive dependents: @src-tier-__init__.py
Dependencies: @src-tier-models.py, @src-tier-parser.py
````

Parameters:

| Parameter | Type | Default | Required | Meaning |
|---|---|---|---|---|
| `query` | string | — | yes | Natural-language question or symbol names |
| `path` | string | — | no | `.tier` file or directory; omitted means the server root's map |
| `budget_tokens` | integer | `3000` | no | Maximum tokens of context to return |
| `include_impact` | boolean | `true` | no | Include a compact dependency/impact summary for repo maps |

`budget_tokens` is clamped to at least 1 and is enforced against the running token total of the
assembled text — header, sections, slices, impact, and omission note. A part that would push the
total over is skipped and named in the omission line instead. `include_impact` has no effect on a
plain `.tier` document, which has neither a symbol table nor a dependency graph, and no effect when
routing found no sections. A blank `query` is a `ValueError`, reported in-band as
`error: query is required`; when no query term matches anything, the response is the header alone.

Call tables inside the packed bodies are narrowed to the matched symbols and their immediate 1-hop
callers and callees. The stored map keeps every edge, and `tier impact` always reads the full table.

Because the server holds one `Session` for the life of the process, a repeated `tier_context` call
with the same query returns stubs instead of re-sending the same bodies, which is why it is safe to
call on every turn.

## Tool reference

| Tool | Purpose |
|---|---|
| `tier_context` | One-call context: route, pack L2/L3, and summarize impact within a budget |
| `tier_list` | One line per `.tier` doc under the root: path, token cost, title, summary |
| `tier_index` | L0 + L1 for one doc: summary, global rules, section index |
| `tier_find` | Hybrid identifier, synonym, and trigram search returning section ids with scores |
| `tier_get` | Fetch sections by id, with 1-hop follow, table rendering, and session dedup |
| `tier_slice` | Fetch exact L3 source lines for named symbols in a repository map |
| `tier_impact` | Direct/transitive dependents and dependencies, optionally with Git blast radius |
| `tier_sync` | Incrementally refresh a repository map from disk |
| `tier_lint` | Structural validation of one `.tier` doc |
| `tier_ingest` | Compress Markdown, JSON, or log text into TIER, optionally within a budget |
| `tier_repo` | Index a directory into a layered repository map |
| `tier_remember` | Persist a rule, section, key/value, or reference into a memory file |

### tier_context

See the section above. `path` may be omitted or a directory; `query` is required.

### tier_list

No parameters. Walks the root, skipping `.git` and every directory the cache module owns, and
prints one line per doc as `<relpath> ~<tokens>t <title> - <summary>`. Symlinks and files resolving
outside the root are skipped. Returns `(no .tier docs found)` when the walk is empty.

### tier_index

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes |

Accepts a `.tier` file or a directory. Returns the L0 index: title and summary, global rules, and
one line per section carrying `@id`, `~Nt`, `!R`, and the abstract.

### tier_find

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes |
| `query` | string | — | yes |
| `k` | integer | `3` | no |

Accepts a directory. Returns `@id score abstract` lines, or `no matches`.

### tier_get

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes, must end in `.tier` |
| `ids` | array of string | — | yes |
| `follow` | boolean | `false` | no |
| `table_format` | `raw` \| `aligned` \| `anchored` | `raw` | no |
| `force` | boolean | `false` | no |
| `reset` | boolean | `false` | no |

Requires a `.tier` file; a directory is rejected with `no such .tier doc: <path>`. `follow` adds one
hop of `>` references. `table_format` changes rendering only, never the stored bytes. `reset` clears
the session cache for this path before fetching; `force` re-sends bodies that are already cached.
Unknown ids are returned with the list of available ids appended to the same text. A `|calls` table
in a fetched body is rendered whole here; narrowing applies to `tier_context` only.

### tier_slice

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes |
| `module` | string | — | yes |
| `symbols` | array of string | — | yes |
| `context_lines` | integer | `0` | no |
| `force` | boolean | `false` | no |

Accepts a directory or an explicit `.tier` path. `module` may be a section id or a
repository-relative file path. `symbols` holds symbol names, matched case-insensitively after a
leading `@` is stripped; an empty array slices every symbol in the module. Each result is
`@module#symbol [Lstart-Lend]` followed by a fenced block tagged with the file's language.

Source lines are read relative to the directory that holds the resolved map, not relative to the
root. That works when the map is a tracked `<root>/repo.tier`, which sits beside its sources. When a
directory argument resolves to a cache under a git directory or the platform cache directory, the
sources are not beside the map and slices report `unknown symbol/file: file:<path>`. Pass an
explicit `<root>/repo.tier`, or use the CLI `tier slice`, which threads the owning work root through
explicitly.

### tier_impact

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes |
| `target` | string | — | yes |
| `git` | boolean | `false` | no |

Accepts a directory or an explicit `.tier` path. `target` may be a section id, a relative file
path, a unique symbol name, or `module#symbol`. `git` adds the working-tree blast radius: changed
paths, changed modules, per-file added/removed/changed symbols, and downstream modules to re-test.
This is the only reader that skips the file-level auto-sync, so a `.tier` argument is reported
against the map exactly as it sits on disk; a directory argument still resolves through the cache
and is built on first use.

For a `module#symbol` or bare-symbol target the report adds `Symbol callers:` and `Symbol calls:`,
naming the exact calling functions with their line ranges. It reads every row of the stored `|calls`
table, so it is never narrowed the way `tier_context` narrows its rendered bodies.

### tier_sync

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes, must end in `.tier` |
| `rehash` | boolean | `false` | no |

Requires a map file. Re-parses only changed and added files, removes deleted modules, rewrites the
map atomically, and invalidates the affected sections and their slices in the session cache.
`rehash` ignores the `mtime` + `size` fast path and compares content hashes. Returns one summary
line: `sync <path>: N changed, N added, N deleted, N unchanged (written|no changes)`.

### tier_lint

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes, must end in `.tier` |

Returns `level: message` lines, or `ok` when the document is clean.

### tier_ingest

| Parameter | Type | Default | Required |
|---|---|---|---|
| `content` | string | — | yes |
| `format` | `auto` \| `md` \| `json` \| `log` | `auto` | no |
| `index_only` | boolean | `false` | no |
| `max_tokens` | integer | unset | no |
| `save_as` | string | unset | no |

`auto` sniffs the payload: valid JSON is JSON, Markdown headings or fences are Markdown, anything
else is log output. Without `max_tokens` the full TIER document (or its L0 index when `index_only`)
is returned. With `max_tokens` the call routes through the pipe compressor: the full artifact is
written to `save_as` or to a temporary file, and the response carries the artifact path, the L0
index, and as many priority sections as fit — errors first, then commands, then sections with
rules. `save_as` must end in `.tier` and stay under the root.

### tier_repo

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | `"."` | no |
| `index_only` | boolean | `false` | no |

`path` may be omitted, and it is the only tool that requires a directory rather than accepting
either form. Returns the full repository map, or just its L0 section when `index_only` is set. The
build is stateless: nothing is written, so use `tier_context`, or `tier_sync` on a persisted map,
when you want the result reused on the next call.

### tier_remember

| Parameter | Type | Default | Required |
|---|---|---|---|
| `path` | string | — | yes, must end in `.tier` |
| `action` | `rule` \| `section` \| `append-rule` \| `append-kv` \| `append-ref` | — | yes |
| `section` | string | `""` | no |
| `title` | string | `""` | no |
| `abstract` | string | `""` | no |
| `body` | array of string | unset | no |
| `text` | string | `""` | no |
| `key` | string | `""` | no |
| `value` | string | `""` | no |
| `target` | string | `""` | no |
| `note` | string | `""` | no |

`rule` appends a global rule (`text`); `section` upserts (`title`, `abstract`, `body`);
`append-rule` appends an imperative rule (`text`) to `section`; `append-kv` upserts a `= key value`
entry; `append-ref` appends a `>` edge to `target`, which must already exist. The write must
round-trip through the parser byte-for-byte, must lint without errors, and must stay within the
memory bounds (200 rules, 500 sections, 4000 tokens per section); otherwise it is refused and
reported in-band. The written section is then invalidated in the session cache.

## Repository cache

The map for a directory is a derived artifact, so the server never asks you to place one. It
resolves a location — see [Path arguments](#path-arguments) for the order and the worktree case —
builds the map on first use, and syncs it incrementally afterwards. Nothing is cached for an
explicit `.tier` path that the caller passed and that already exists.

A map under a git directory or a digest directory is not beside the sources, so a `.owner` file is
written next to it recording the work root. `tier_list` prunes `.git` and anything `is_cache_dir`
recognises, so cached maps never appear in the listing. A build is atomic: the map is written to a
temporary file and renamed into place, so an interrupted build cannot leave a half-written map.

Reads are cheap after the first one. A repository map loaded from a `.tier` file is re-checked for
changes at most once per `TIER_AUTO_SYNC_TTL` seconds (default `1.5`), using recorded file and
directory `mtime` + `size` values as a change detector; the sync itself only re-parses files whose
fingerprint moved. Set `TIER_AUTO_SYNC_TTL=0` to re-check on every call.

## Client configuration

Every client below receives the same stdio server and the same root argument. `tier install` writes
these entries for you; the snippets are the equivalent hand edits. Two launch forms exist and both
work in every client:

- `tier-mcp ROOT` — the console script, when `tier-ctx` is installed (`pip install tier-ctx`,
  `uv tool install tier-ctx`, `pipx install tier-ctx`).
- `uvx --from tier-ctx tier-mcp ROOT` — runs the published package without installing it. Use this
  where you want no global install, at the cost of a per-launch resolve.

The installer prefers an absolute `tier-mcp` path and otherwise records the absolute current
interpreter with `-m tier.mcp`, so a GUI host does not depend on a shell `PATH`. The snippets below
show the plain `tier-mcp` form for readability; substitute the absolute path that
`tier config-snippet` prints when the host does not inherit your shell environment.

Schema shapes, as the installer writes them:

| Client | Config path | Container key | Entry shape |
|---|---|---|---|
| Claude Code | `.mcp.json` (project), `~/.claude.json` (user) | `mcpServers` | `{"command": ..., "args": [...]}` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `~/.config/Claude/claude_desktop_config.json` (Linux), `%APPDATA%\Claude\claude_desktop_config.json` (Windows) | `mcpServers` | `{"command": ..., "args": [...]}` |
| Cursor | `.cursor/mcp.json` (project), `~/.cursor/mcp.json` (global) | `mcpServers` | `{"command": ..., "args": [...]}` |
| VS Code | `.vscode/mcp.json` | `servers` | `{"type": "stdio", "command": ..., "args": [...]}` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` | `{"command": ..., "args": [...]}` |
| OpenCode | `opencode.json` (project), `~/.config/opencode/opencode.json` (global) | `mcp` | `{"type": "local", "command": [cmd, ...args], "enabled": true}` |
| Command Code | `.commandcode/mcp.json` (project), `~/.commandcode/mcp.json` (user) | `mcpServers` | `{"command": ..., "args": [...]}` |

### Claude Code

```json
{
  "mcpServers": {
    "tier": {
      "command": "tier-mcp",
      "args": ["/absolute/path/to/project"]
    }
  }
}
```

With `uvx`:

```json
{
  "mcpServers": {
    "tier": {
      "command": "uvx",
      "args": ["--from", "tier-ctx", "tier-mcp", "/absolute/path/to/project"]
    }
  }
}
```

### Claude Desktop

```json
{
  "mcpServers": {
    "tier": {
      "command": "tier-mcp",
      "args": ["/absolute/path/to/project"]
    }
  }
}
```

With `uvx`:

```json
{
  "mcpServers": {
    "tier": {
      "command": "uvx",
      "args": ["--from", "tier-ctx", "tier-mcp", "/absolute/path/to/project"]
    }
  }
}
```

### Cursor

```json
{
  "mcpServers": {
    "tier": {
      "command": "tier-mcp",
      "args": ["/absolute/path/to/project"]
    }
  }
}
```

With `uvx`:

```json
{
  "mcpServers": {
    "tier": {
      "command": "uvx",
      "args": ["--from", "tier-ctx", "tier-mcp", "/absolute/path/to/project"]
    }
  }
}
```

### VS Code

The `servers` container carries a `type` field, and it must be present for VS Code to start the
process:

```json
{
  "servers": {
    "tier": {
      "type": "stdio",
      "command": "tier-mcp",
      "args": ["/absolute/path/to/project"]
    }
  }
}
```

With `uvx`:

```json
{
  "servers": {
    "tier": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "tier-ctx", "tier-mcp", "/absolute/path/to/project"]
    }
  }
}
```

### Windsurf

```json
{
  "mcpServers": {
    "tier": {
      "command": "tier-mcp",
      "args": ["/absolute/path/to/project"]
    }
  }
}
```

With `uvx`:

```json
{
  "mcpServers": {
    "tier": {
      "command": "uvx",
      "args": ["--from", "tier-ctx", "tier-mcp", "/absolute/path/to/project"]
    }
  }
}
```

### OpenCode

OpenCode uses the `mcp` container, `type: "local"`, a single `command` array that concatenates the
program and its arguments, and an `enabled` flag. There is no separate `args` key.

```json
{
  "mcp": {
    "tier": {
      "type": "local",
      "command": ["tier-mcp", "/absolute/path/to/project"],
      "enabled": true
    }
  }
}
```

With `uvx`:

```json
{
  "mcp": {
    "tier": {
      "type": "local",
      "command": ["uvx", "--from", "tier-ctx", "tier-mcp", "/absolute/path/to/project"],
      "enabled": true
    }
  }
}
```

### Command Code

```json
{
  "mcpServers": {
    "tier": {
      "command": "tier-mcp",
      "args": ["/absolute/path/to/project"]
    }
  }
}
```

With `uvx`:

```json
{
  "mcpServers": {
    "tier": {
      "command": "uvx",
      "args": ["--from", "tier-ctx", "tier-mcp", "/absolute/path/to/project"]
    }
  }
}
```

## Session deduplication

The server keeps one `Session` for the process. It records every `(key, section_id)` and
`(key, section#symbol)` pair it has already emitted, where `key` is the resolved real path of the
map. Because `tier_get`, `tier_slice`, and the section part of `tier_context` all derive that key
the same way, a body fetched by one tool is remembered by the others.

A repeat fetch returns a one-line stub in place of the body:

```text
@src-tier-repo.py (already in context; not resent)
@src-tier-repo.py#slice_symbols (already in context; not resent)
```

Control is per call:

- `tier_get` with `force: true` re-sends the requested bodies and marks them again.
- `tier_slice` with `force: true` does the same for symbol slices.
- `tier_get` with `reset: true` clears the whole cache entry for that path before fetching, so the
  next response is complete again.

`tier_sync` closes the loop on staleness: for every changed and deleted module it calls
`Session.invalidate`, which drops the module id and every slice whose key starts with `<id>#`. A
body edited on disk is therefore re-read on the next fetch rather than served from the cache as if
it were current. `tier_remember` invalidates the section it wrote for the same reason.

## Official SDK adapter

The default server is stdlib-only by design, and needs no extra. Users who want the official SDK,
MCP Inspector, or Streamable HTTP install the optional extra:

```sh
pip install "tier-ctx[mcp]"        # or uv tool install "tier-ctx[mcp] @ git+<repo>"
```

That provides the `tier-mcp-sdk` console script and the `tier.mcp.sdk_server` module, both of which
expose the same 12 tools with the same schemas against an MCP v2 `MCPServer`, falling back to the
v1 `FastMCP` class when that is what is installed.

```sh
tier-mcp-sdk /path/to/root
tier-mcp-sdk /path/to/root --transport streamable-http --host 127.0.0.1 --port 8000
python3 -m tier.mcp.sdk_server /path/to/root --root /path/to/root
mcp dev src/tier/mcp/sdk_server.py     # opens MCP Inspector against a checkout
```

| Flag | Default | Meaning |
|---|---|---|
| `root` | `.` | Docs or repository root, positional |
| `--root` | unset | Same value as the positional argument; wins when both are given |
| `--transport` | `stdio` | `stdio`, `streamable-http`, or `sse` |
| `--host` | `127.0.0.1` | Bind host, used for non-stdio transports |
| `--port` | `8000` | Bind port, used for non-stdio transports |

`--host` and `--port` are only applied when the transport is not `stdio`, and the root is resolved
with `realpath` at startup; a root that is not a directory exits `1`.

Without the extra installed, the script prints two lines to stderr and exits `1`: an `error:` line
naming the missing package and the exact `pip install "tier-ctx[mcp]"` command, then the same
command again prefixed with `hint:`.

The module-level `mcp` object is built from the current working directory at import time so that
`mcp dev` can discover it directly; when the SDK is missing that object is `None`, the module still
imports cleanly, and `tier-mcp-sdk` is the entry point that reports the problem.
