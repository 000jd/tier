# CLI reference

`tier` is a single entry point for indexing documents and repositories, retrieving the parts an
agent needs, and configuring agent clients. The retrieval commands read the same four-layer
structure — L0 index, L1 abstracts, L2 bodies, L3 source slices — and most of them accept either a
`.tier` document or a repository root.

```text
tier [--version] <command> [arguments] [flags]
```

Run `tier <command> --help` for the same flags at the terminal. `-V` / `--version` prints the
installed version and exits.

## Path arguments

Most commands take a positional path that may be either a `.tier` file or a directory. A `.tier`
file is used exactly as given. A directory resolves the auto-managed repository cache for that
root: the map is built on first use and incrementally synced on every later use, so no manual
`tier from-repo` step is required.

Resolution order for a directory:

1. An explicit `.tier` path, when you passed one. It always wins.
2. `<root>/repo.tier`, when it exists. A checked-in map keeps its own freshness meaningful.
3. `<gitdir>/info/tier/repo.tier`, when the repository's git directory is writable. For an ordinary
   checkout that is `<root>/.git/info/tier/repo.tier`. Git ignores everything under a git directory,
   so the cache never appears in `git status`.
4. `<cache-root>/<sha256(realpath(root))[:12]>/repo.tier`, outside the worktree entirely.
   `<cache-root>` is `$TIER_CACHE_DIR`, else the platform cache directory
   (`%LOCALAPPDATA%\tier\cache` on Windows, `$XDG_CACHE_HOME/tier`, or `~/.cache/tier`).

When a map lives outside the repository it describes, a `.owner` file beside it records the work
root, so `slice` and `impact` still read source files from the right place. All writes are atomic.

### The `git worktree` case

In a `git worktree` or a submodule, `.git` is a file instead of a directory. It holds one line,
`gitdir: <path>`, and a relative target resolves against the directory containing `.git`. TIER reads
that pointer before choosing a cache location, so such a checkout lands on tier 3 instead of falling
through to the user cache:

```sh
# ordinary checkout
<root>/.git/info/tier/repo.tier

# linked worktree: .git is a pointer to <main>/.git/worktrees/<name>
<main>/.git/worktrees/<name>/info/tier/repo.tier

# submodule: .git is a pointer to <super>/.git/modules/<name>
<super>/.git/modules/<name>/info/tier/repo.tier
```

Each checkout keeps its own map rather than sharing the main repository's. Worktrees exist to hold
different branches, so one shared map would be rebuilt on every checkout switch and could serve the
wrong branch's symbols. Where a git directory carries a `commondir` file, that is resolved first.
Every one of these locations sits inside a git directory, so `git status` stays clean in the main
tree and in each worktree. A read-only git directory falls through to tier 4.

## tier context

The composite retrieval command. It resolves the target (building or syncing the map), routes the
query with the hybrid retriever, matches symbol names, packs section bodies and L3 slices to a
token budget, and appends a compact dependency summary.

```text
tier context [TARGET] QUERY... [--budget-tokens N] [--root DIR] [--include-calls MODE]
             [--no-impact] [--no-dedup]
```

| Flag | Default | Meaning |
|---|---|---|
| `TARGET` | `.` | `.tier` file or directory; a directory resolves the repository cache |
| `QUERY` | required | One or more words, joined with spaces into a single query |
| `--budget-tokens N` | `3000` | Upper bound on the returned text, in tokens |
| `--root DIR` | target root | Repository root used to resolve source files for slices and impact |
| `--include-calls MODE` | `auto` | `\|calls` detail: `auto` keeps only edges leaving the matched symbols, `all` keeps every stored edge, `none` omits the table |
| `--no-impact` | off | Omit the dependency summary |
| `--no-dedup` | off | Disable session dedup for this call |

The `|calls` table stored in a repository map always holds every caller/callee edge.
`--include-calls` only affects what `tier context` renders: the default `auto` narrows each
table to edges whose caller is one of the matched symbols, which keeps a packed body small.
`tier impact` always reads the complete table.

`TARGET` is the first positional and `QUERY` takes everything after it, so pass the target
explicitly when the query has more than one word:

```sh
tier context . slice symbols --budget-tokens 2000
tier context repo.tier "rollback policy"
```

A bare multi-word query with no target makes the first word the target and fails on it. `QUERY`
is required; omitting it is a usage error.

`--no-dedup` exists for parity with the API. The CLI invocation is a single process with no
persistent session, so nothing is ever deduplicated: every run emits full bodies. Deduplication
applies to the long-lived MCP server, documented in [mcp.md](mcp.md).

The response is plain text in four parts, in this order:

1. A `## Context: <query>` header, clipped to 120 characters.
2. Up to three routed section bodies (L2), rendered as `@id`, title, `?` abstract, then body lines.
3. Up to three L3 source slices for matched symbols, as `@module#symbol [Lstart-Lend]` plus a
   fenced source block. Slices are only produced for repository maps.
4. An `## Impact: @id` block — `## Impact: @id#symbol` when a symbol in that section also matched —
   listing direct dependents, transitive dependents, dependencies, and, for symbol targets, symbol
   callers and symbol calls.

Anything that does not fit is named on a final `Omitted for budget: @id, @id#symbol` line. A plain
`.tier` document produces sections only; it has no symbol table and no dependency graph.

Call tables inside the packed section bodies are narrowed to the matched symbols and their
immediate 1-hop callers and callees. The stored map keeps every edge and `tier impact` always reads
the full table; the narrowing only affects what `tier context` renders. See
[spec.md](spec.md#call-graph) for the row grammar and the render modes.

## tier install

Universal installer for MCP and agent clients. Aliased as `tier setup`.

```text
tier install [TARGET] [flags]
```

| Flag | Meaning |
|---|---|
| `--clients LIST` | `all`, `none`, `detected`, `project`, or comma-separated client ids |
| `-y`, `--yes` | Accept defaults; never prompt |
| `--dry-run` | Print unified diffs and change nothing |
| `--uninstall` | Remove only TIER entries, hooks, and instruction blocks |
| `--no-repo` | Skip `repo.tier` generation and sync |
| `--no-hook` | Skip the Claude Code `PostToolUse` hook |
| `--instructions MODE` | `both`, `claude`, `agents`, `gemini`, or `none` (default `both`) |
| `--custom-config PATH` | Install into any config path (`.json`, `.toml`, `.yaml`, `.md`) |
| `--schema NAME` | Insertion schema for `--custom-config`; inferred from the file name by default |
| `--print-config` | Print ready-to-copy snippets for every schema, then stop |
| `--plain` | Disable rich/ANSI styling |
| `--no-color` | Keep the layout, drop the color |

`--schema` accepts exactly seven names: `mcpServers`, `servers`, `opencode`, `context_servers`,
`codex-toml`, `continue-yaml`, `skill-md`. Without `--schema`, the name is inferred: `SKILL.md`
maps to `skill-md`, `.toml` to `codex-toml`, `.yaml`/`.yml` to `continue-yaml`, `opencode.json` to
`opencode`, everything else to `mcpServers`.

`--clients` accepts client ids, the documented aliases (`claude`, `vs-code`, `cmd`, `cline`, …),
and the four selectors `all`, `none`, `detected`, `project`. With `--yes` and no `--clients`, only
`claude-code` is selected; without either, the default is every already-configured client plus
`claude-code`. The interactive wizard runs only when both stdin and stdout are terminals.

Interactive selection accepts numbers (`1,3,8`) or one of four shortcuts:

| Input | Selects |
|---|---|
| `d` | Every client detected on this machine (binary or config directory present) |
| `a` | Every supported client |
| `p` | Every project-scoped client |
| `c` | A custom config path, followed by a schema prompt |

```sh
tier install . --clients claude-code,cursor,vscode --yes
tier install . --clients all --dry-run
tier install . --uninstall --clients all
tier install . --custom-config harness/harness.json --schema mcpServers --yes
```

Behaviour worth knowing before running it:

- Every existing config is copied to `<file>.bak` once, then replaced atomically. Re-running is
  idempotent and leaves unrelated servers, tables, hooks, and keys untouched.
- Instruction files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) are edited only inside
  `<!-- TIER:BEGIN -->` / `<!-- TIER:END -->` markers. Unbalanced markers in surrounding text are
  reported as a warning rather than sliced through.
- The server entry prefers an absolute `tier-mcp` executable and falls back to the absolute current
  interpreter with `-m tier.mcp`, so GUI hosts do not depend on a shell `PATH`.
- `--uninstall` removes only TIER's own entries. A Codex `config.toml` is restored without the
  `[mcp_servers.tier]` table; other `[mcp_servers.*]` tables survive.
- A config file that cannot be read or parsed aborts the run with `error: ...` on stderr and exit
  code `1`; every file touched before that point has already been backed up and written.

### tier init-plugin

The earlier single-shot bootstrap, kept for compatibility. It writes `.mcp.json`, generates
`repo.tier`, optionally converts `README.md` and `CONTRIBUTING.md`, writes the marked instruction
block, and optionally registers the hook.

```text
tier init-plugin TARGET [--convert-docs] [--write-instructions {claude,agents,both}] [--hook]
```

Prefer `tier install` for new setups: it covers every client, merges safely, and supports
`--dry-run`.

## tier clients

Lists every supported client id with its scope, schema, and config path, one per line.

```text
tier clients [TARGET] [--json]
```

```sh
tier clients --json
```

`--json` emits an array of objects with `id`, `label`, `scope`, `schema`, and `config_path`.

## tier config-snippet

Prints ready-to-copy configuration for every schema — `mcpServers`, VS Code `servers`, OpenCode,
Zed `context_servers`, Codex TOML, Continue YAML, `SKILL.md` — followed by native CLI one-liners
for Claude Code, Command Code, OpenCode, Codex, Gemini, and a generic `tier-mcp` invocation.

```text
tier config-snippet [TARGET]
```

```sh
tier config-snippet /abs/path/to/project
```

The output is written to stdout and needs no flags. `tier install --print-config` produces the same
text but goes through the installer and ignores `--clients`.

## tier doctor

Health and configuration diagnostics for one root.

```text
tier doctor [ROOT] [--json] [--plain] [--no-color]
```

| Flag | Meaning |
|---|---|
| `--json` | Emit an array of `{name, status, detail}` objects |
| `--plain` | Disable rich/ANSI styling entirely |
| `--no-color` | Keep the table layout, drop the color |

Checks cover the Python version (3.10 or newer), the `tier` / `tier-mcp` entrypoints, the tokenizer
backend, the root path, `repo.tier` presence and shape, repository lint, repository freshness, the
client catalog, each detected client's TIER entry (validated in its native schema), installed
hooks, and whether the optional `mcp` SDK is importable.

Freshness is always computed with a content rehash, never from the recorded `mtime` + `size` cache,
because a same-size edit inside one timestamp tick would otherwise look current.

```sh
tier doctor .
tier doctor . --json > doctor.json
```

Exit code is `0` when no check reports `FAIL`, and `1` otherwise. `WARN` and `INFO` rows do not
affect the exit code.

## Retrieval

### tier index

L0 plus L1: title, summary, global rules, and one index line per section. Read this before fetching
anything else.

```text
tier index [FILE] [--no-tokens]
```

`--no-tokens` omits the per-section `~Nt` cost, so the command needs no tokenizer at all.

```sh
tier index repo.tier
```

Each section line is `@id ~Nt !R abstract`, where `!R` is the count of `!` rules in that section.

### tier find

Hybrid search over a document: BM25-style field weights, identifier splitting, a synonym map, and a
character-trigram fallback for typos.

```text
tier find [FILE] QUERY... [-k N]
```

`-k` is the number of hits and defaults to `3`.

```sh
tier find repo.tier "parse tier document" -k 5
```

Output is one line per hit: `@id  score  abstract`.

### tier get

Fetches sections by id.

```text
tier get [FILE] ID... [--follow] [--table {raw,aligned,anchored}]
```

`--follow` also pulls one hop of `>` references. `--table` controls table rendering without changing
the stored bytes: `raw` (on-disk, default), `aligned` (spaced Markdown), `anchored` (per-row primary
key plus a repeated column header every 20 rows).

```sh
tier get repo.tier src-tier-repo.py --follow
```

Unknown ids are reported on stderr as `unknown id(s): ...` and exit code is `1`.

### tier slice

Fetches exact L3 source lines for named symbols from a repository map.

```text
tier slice [MAP] MODULE SYMBOL... [--root DIR] [--context N] [--force]
```

Both forms are accepted:

```sh
tier slice repo.tier src/tier/repo.py slice_symbols   # slice MAP MODULE SYM...
tier slice src/tier/repo.py slice_symbols             # slice MODULE SYM...
```

The first token is treated as the map only when it ends in `.tier` or names an existing directory.
Any other leading token is the module, because a module argument routinely names a real source file
(`a.py`) that must not be mistaken for a map. In the short form the map is the repository cache for
the current directory.

`--context N` adds N lines of surrounding source on each side. `--force` is accepted but has no
effect from the CLI: the command creates no session, so there is no dedup state to bypass. It exists
for parity with the MCP tool, which does deduplicate. Unknown symbols or files go to stderr and exit
with `1`.

Output is `@module#symbol [Lstart-Lend]` followed by a fenced block tagged with the file's language.

### tier impact

Reverse dependency and blast-radius analysis over a map's `>` edges and symbol call graph.

```text
tier impact [FILE] [TARGET] [--root DIR] [--git]
```

With a single path argument the token is the impact target and the map comes from the repository
cache:

```sh
tier impact src/tier/repo.py
tier impact repo.tier src/tier/repo.py --git
tier impact repo.tier "src/tier/repo.py#slice_symbols"
```

A target may be a section id, a repository-relative file path, a unique symbol name, or
`module#symbol`. `--git` adds the working-tree blast radius: changed paths, changed modules, added
and removed symbols per file, and the downstream modules to re-test. Without `--git` the report is
graph-only. An unresolvable target exits with `1`.

The report prints `Impact target`, direct and transitive dependents, dependencies, and transitive
dependencies. A `module#symbol` target, or a bare symbol name that resolves to exactly one module,
adds two lines that read the stored `|calls` table:

```text
Symbol: src/tier/repo.py#slice_symbols
Symbol callers: src/tier/cli.py#main [65-464], src/tier/context.py#build_context [134-247], src/tier/mcp/server.py#call [354-478]
Symbol calls: src/tier/models.py#_read_text, src/tier/parser.py#_repo_file_meta, src/tier/repo.py#_resolve_module, src/tier/repo.py#_slice_range, src/tier/repo.py#_symbol_rows
```

`Symbol callers:` names every recorded function that calls the target, and `Symbol calls:` names
every symbol the target calls. Each caller carries its own `[Lstart-Lend]` range, so a line number
in the report can be sliced directly without first reading the module. The package qualifier before
`#` is the defining module's repository-relative path, not a section id.

`tier impact` reads the parsed map, so it reports every edge in the table. It is never narrowed by
the 1-hop call filtering that `tier context` applies to rendered bodies; see
[spec.md](spec.md#call-graph).

### tier sync

Incrementally refreshes a repository map from disk, re-parsing only changed and added files and
dropping deleted modules.

```text
tier sync [FILE] [--root DIR] [--no-write] [--rehash]
```

`--no-write` reports the diff without touching the file. `--rehash` ignores the `mtime` + `size`
fast path and compares content hashes, which catches same-size edits whose timestamp was restored
by `rsync -a`, `cp -p`, or a tar extraction.

```sh
tier sync
tier sync repo.tier --rehash
```

Output is one summary line — `sync <path>: N changed, N added, N deleted, N unchanged (written)` —
followed by the changed, added, and deleted paths.

## Documents

### tier lint

Structural validation: duplicate section ids, missing `?` abstracts, oversized sections, declared
table row counts that do not match the rows present, table rows with an unparsable or wrong-width
cell count, and `>` references to sections that do not exist.

```text
tier lint [FILE]
```

```sh
tier lint repo.tier
```

Prints `error: ...` and `warn: ...` lines, then a summary. Exit code is `1` when at least one error
is present, `0` otherwise; warnings never fail a lint.

### tier build

Rewrites a document in canonical form by re-serializing the parsed structure, which drops comments
and blank lines.

```text
tier build FILE [-o OUT] [--pretty]
```

`--pretty` inserts a blank line before each section. Without `-o` the result goes to stdout.

```sh
tier build notes.tier --pretty -o canonical.tier
```

### tier from-md

Converts Markdown to TIER. Headings become sections, tables become counted TIER tables, and fenced
shell blocks become `$` command lines.

```text
tier from-md FILE [-o OUT] [--keep-emphasis]
```

`--keep-emphasis` preserves `**bold**` and `__bold__` markers, which are stripped by default. Use
`-` as the file name to read stdin.

```sh
tier from-md README.md -o readme.tier
```

### tier to-md

Converts a TIER document back to Markdown.

```text
tier to-md FILE
```

### tier to-json

Emits the parsed document as JSON: `meta`, `rules`, and one object per section with `id`, `title`,
`abstract`, and typed `nodes`.

```text
tier to-json FILE
```

### tier compress

Converts raw Markdown or JSON — from a file or stdin — into a TIER document.

```text
tier compress [FILE] [--format {auto,md,json}] [--index] [-o OUT]
```

`FILE` defaults to `-` (stdin). `--format` defaults to `auto`, which tries JSON first and falls back
to Markdown. `--index` emits only the L0 index instead of the whole document.

```sh
curl -s https://example.com/api/items | tier compress --format json --index
```

`pipe` and `hook` accept an additional `log` format; `compress` does not.

### tier stats

Token cost per layer and per section, using the active tokenizer backend.

```text
tier stats FILE
```

```sh
tier stats repo.tier
```

Output names the backend (`o200k` or `estimate`), the full-document count, the L0 index count with
its percentage of the full document, then one count per section.

### tier from-repo

Indexes a source repository into a layered TIER architecture map: one section per module with a
`= file` fingerprint tree, a `|symbols` table with exact line ranges, and `>` edges to local
dependencies.

```text
tier from-repo [PATH] [--index] [--no-tokens] [-o OUT]
```

`--index` emits only the L0 map, `--no-tokens` skips per-section cost counts, and `-o` writes to a
file instead of stdout.

```sh
tier from-repo . -o repo.tier
```

## Streams and hooks

### tier pipe

Compresses a stream read from stdin into TIER, optionally within a token budget.

```text
tier pipe [--format {auto,json,md,log}] [--max-tokens N] [-o OUT | --out OUT]
```

The format is auto-detected by default: valid JSON is treated as JSON, Markdown headings or fences
as Markdown, anything else as log output. Log compression groups repeated lines by shape, then emits
`errors`, `commands`, and `output` sections; non-error output is wrapped as untrusted data.

Without `--max-tokens`, or when the result fits, the full TIER document is printed. Otherwise the
command prints the L0 index, a `Full artifact: <path>` line, and as many priority sections as fit —
`errors` first, then `commands`, then sections carrying `!` rules. The full artifact is saved to
`-o`, or to a temporary `tier-pipe-*.tier` file when no output path was given.

```sh
pytest -q 2>&1 | tier pipe --max-tokens 500 -o test-run.tier
```

Sections that do not fit are named in an `Omitted sections: ...` line when that line itself fits.
A budget smaller than the artifact-path line is best-effort: the path line is printed alone.

### tier hook

Reads a Claude Code-style JSON hook payload on stdin and prints a JSON hook response.

```text
tier hook [--max-tokens N] [--format {auto,json,md,log}] [--fast]
```

`--max-tokens` defaults to `600`. The largest text field in the payload is measured; if it fits, the
response is `{"continue": true}` and the tool output passes through unchanged. Otherwise the
response carries `hookSpecificOutput.additionalContext` with a compressed TIER view and a
`systemMessage` naming the source field and token count.

The command is designed never to break an agent session: malformed input is reported as a
`systemMessage` with `{"continue": true}`, and any internal exception is caught and reported the
same way. It always exits `0`.

`--fast` switches to the characters/4 estimator instead of exact `o200k` counts. That path is
faster but makes the token budget best-effort: budget decisions are multiplied by a safety factor
and the emitted context may be under-filled, because the estimate cannot prove the real count.

```sh
echo '{"tool_response":{"stdout":"…"}}' | tier hook --max-tokens 400
```

## Memory

### tier remember

Persists a rule, a section, a key/value pair, or a reference into a `.tier` memory file.

```text
tier remember FILE [--rule TEXT | --section ID [--title T] [--abstract A] [--body LINE]...
                    [--append-rule TEXT] [--key KEY VALUE] [--ref TARGET [--note TEXT]]]
```

| Flag | Meaning |
|---|---|
| `--rule TEXT` | Append a global rule to the document header |
| `--section ID` | Section to upsert or to append into |
| `--title T` | Title for a section upsert |
| `--abstract A` | Abstract for a section upsert; required when creating a section |
| `--body LINE` | Body line for a section upsert; repeat the flag for more lines |
| `--append-rule TEXT` | Append an imperative rule to `--section` |
| `--key KEY VALUE` | Upsert a `= KEY VALUE` entry in `--section` |
| `--ref TARGET` | Append a `>` reference to `--section`; the target must already exist |
| `--note TEXT` | Note appended after the reference target |

Exactly one mode runs per invocation. Passing `--rule` together with `--section`, or omitting both,
is a usage error and exits `2`. The `append-*` modes require an existing section, and `--ref`
requires a resolvable target.

Writes are validated before they happen: the document must round-trip through the parser unchanged,
must lint without errors, and must stay within 200 global rules, 500 sections, and 4000 tokens per
section. The file is then replaced atomically.

```sh
tier remember memory.tier --section decisions --abstract "release constraints" \
  --append-rule "Never deploy on Friday"
tier remember memory.tier --section state --key last_release 2026-02-27
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, including a lint run whose only findings are warnings |
| `1` | Runtime error or failed check (missing file, unknown id, escaped path, lint error, unresolvable impact target, `doctor` failure) |
| `2` | Usage error: unknown command, unknown flag, missing argument, or conflicting `remember` modes |

`tier hook` is the exception to this table: it always exits `0`, because a nonzero exit from a
`PostToolUse` hook can break an agent session. Malformed input and internal errors are reported in
the JSON response instead. `tier lint` exits `1` only when at least one `error:` line is present;
warnings never fail a lint.

## Environment

| Variable | Effect |
|---|---|
| `TIER_CACHE_DIR` | Root for out-of-tree caches, replacing the platform cache dir |
| `TIER_COUNT_JS` | Path to the Node count script; exact counts without `node_modules` |
| `TIER_NO_NODE` | Disables the Node tokenizer, forcing the characters/4 estimate |
| `TIER_TOKEN_FAST` | Forces estimator-only counting process-wide, as for `fast=True` |
| `TIER_AUTO_SYNC_TTL` | Seconds the MCP server trusts a loaded map before re-checking (`1.5`) |

`TIER_CACHE_DIR` also moves the digest directory used at resolution tier 4. Setting it keeps caches
out of a home directory for a sandboxed or CI run, and it is the variable to isolate in tests.

`TIER_COUNT_JS`, `TIER_NO_NODE`, and `TIER_TOKEN_FAST` govern token counting: they change reported
costs, never document content. The active backend is `o200k` or `estimate`, and `tier stats` prints
it on its first line.

`TIER_AUTO_SYNC_TTL` applies only to the MCP server. The CLI builds or syncs once per invocation and
exits, so there is no window to tune.
