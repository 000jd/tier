<div align="center">

# TIER

**Layered context for AI coding agents: a 4-layer document and repository format, a code graph
indexer, and a zero-dependency MCP server.**

[![CI](https://github.com/000jd/tier/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![PyPI](https://img.shields.io/badge/pypi-tier--ctx-blue)](https://pypi.org/project/tier-ctx/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

---

`tier` keeps an agent from loading whole repositories and documents into its context. Every
document carries a small always-loadable index, one-line abstracts, structured bodies with symbol
signatures and dependency edges, and exact source-line ranges it can fetch on demand. A hybrid
retriever routes a question to the right section, an incremental repository map keeps fingerprints
and line ranges current, and a reverse-dependency graph answers what an edit affects.

The core is pure Python standard library: `dependencies = []`, no network calls, no telemetry.
It runs entirely offline on Python 3.10 and later. Optional extras add terminal styling, the
official MCP SDK adapter, and Tree-sitter grammars.

## Installation

```sh
uv tool install tier-ctx
pipx install tier-ctx
uvx --from tier-ctx tier install
```

Until the PyPI release is live, install from Git:

```sh
uv tool install git+https://github.com/000jd/tier
uv tool install "tier-ctx[all] @ git+https://github.com/000jd/tier"   # + rich UI, MCP SDK
```

From a checkout, inside a virtual environment:

```sh
git clone https://github.com/000jd/tier && cd tier
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[all]"
```

> The distribution is named `tier-ctx`; the import package and console scripts are `tier`,
> `tier-mcp`, and `tier-mcp-sdk`. The bare PyPI name `tier` belongs to an unrelated package.

## How it works

TIER splits every document into four layers, and an agent reads only the ones it needs.

- **L0 — index.** Document metadata, global rules, and one line per section carrying that
  section's token cost and rule count. Always loaded; a few hundred tokens.
- **L1 — abstracts.** A one-line `?` summary per section saying *when* to read it, folded into
  the L0 index.
- **L2 — bodies.** Section contents; for codebases, a `|symbols` table with exact
  `[start-end]` line ranges and `>` edges to local dependencies.
- **L3 — slices.** Exact source lines for one symbol, fetched by name.

`tier_context` collapses the whole loop into one call: resolve or build the repository index,
route the query with the hybrid retriever, match symbol names, then pack L2 bodies, L3 slices,
and a dependency summary until the token budget is spent. The granular commands remain available
when you need a specific section id or symbol.

## Example

```console
$ cd myproject && tier install --clients claude-code --yes
configured /home/me/myproject/.mcp.json
cache (.git/info/tier): /home/me/myproject/.git/info/tier/repo.tier (created)
Instructions: /home/me/myproject/CLAUDE.md, /home/me/myproject/AGENTS.md

$ tier context . "how does the retry backoff work" --budget-tokens 800
## Context: how does the retry backoff work

@src-http-client.py src/http/client.py
? HTTP client with retry and backoff; read when debugging request failures
|symbols[3]{kind,name,signature,lines}
function,request,def request(url: str, retries: int = 3),18-41
function,backoff_delay,def backoff_delay(attempt: int) -> float,44-49
|calls[1]{caller,callee,module}
request,backoff_delay,.

## Impact: @src-http-client.py

Direct dependents: @src-api-client.py
Dependencies: @src-config.py

$ tier impact src/http/client.py#backoff_delay
Impact target: @src-http-client.py
Direct dependents: @src-api-client.py
Symbol: src/http/client.py#backoff_delay
Symbol callers: src/http/client.py#request [18-41]

$ tier slice src/http/client.py backoff_delay
@src-http-client.py#backoff_delay [L44-L49]
```

## Architecture highlights

**Header-once tables and indented key/value trees.** A record set is a header plus exactly N CSV
rows, and nested data is a two-space-indented tree. Both keep the schema and drop the repeated
punctuation that JSON and Markdown tables spend tokens on.

**Deterministic line-start sigils.** Every line is classified by its first character: `%` header,
`@` section, `?` abstract, `!` rule, `=` key/value, `|` counted table, `>` reference, `$` command.
Fenced code, untrusted blocks, and table rows are opaque, so no sigil inside them is reinterpreted.

**Untrusted content guards.** Text from the outside world is wrapped in `<<<source #guard ...
>>>guard`. The guards are chosen so that the payload cannot contain its own terminator, which makes
embedded instructions, fake headers, and rule sigils inert data.

**Stateful session deduplication.** A section or source slice already delivered in the conversation
is replaced by a one-line stub on the next request. `sync` evicts exactly the sections whose files
changed, and their slices.

**Symbol-level blast radius.** Repository maps record `>` edges between modules and a `|calls`
table of caller/callee pairs. `tier impact` reports file-level importers and, given
`module#symbol`, the exact functions that call it.

**Invisible repository cache.** The map is a derived artifact, so it lives in
`.git/info/tier/repo.tier` inside a worktree (which Git ignores) or in a user cache directory
outside it. A tracked `repo.tier` is still honoured when one exists. Nothing is written into your
working tree, so `git status` stays clean.

**Hybrid search with precomputed IDF.** BM25-style term scoring over split identifiers, a compact
synonym map, character 3-gram fallback for typos, and navigation priors. Document frequencies are
computed once per query, which keeps a search over 800 sections near 200 ms with no index to build.

## Performance

Measured with the `o200k_base` tokenizer. These are token counts and retrieval hit rates, not LLM
answer accuracy. Full methodology and reproduction steps are in
[`benchmarks/README.md`](benchmarks/README.md); raw output is in
[`benchmarks/BENCH.txt`](benchmarks/BENCH.txt).

| Workload | Measure | Result |
|---|---|---|
| 60 records × 6 fields | TIER table vs pretty JSON | 1,331 vs 3,393 tokens (−61%) |
| Nested config, 4 levels | TIER key/value vs pretty JSON / YAML | 143 vs 234 / 165 tokens |
| `src/tier` self-index | raw source → full map → L0 → L0 + one module | 72,859 → 14,375 → 828 → 1,784 tokens (−98%) |
| Bug-localization workflow | five steps: index, find, L2, L3 slices, impact + sync | 5,003 tokens (−93.1%), 3/3 exact slices |
| 36-file polyglot repo | surgical L0 + target L2 + 7 slices | 3,008 vs 8,249 tokens (−64%), 116/116 symbols |
| Payments runbook | L0 index vs whole Markdown | 352 vs 1,463 tokens |
| 197-section corpus | blind retrieval, 15 queries | 15/15 hit@1, mean top-1 fetch 134 tokens |

## Supported clients

`tier install` configures 25 agent clients across JSON, TOML, YAML, and `SKILL.md` schemas. It
backs up every file it touches, merges instead of overwriting, supports `--dry-run` and
`--uninstall`, and validates the result with `tier doctor`.

<details>
<summary><b>Client list and config paths</b></summary>

| Client | Scope | Config |
|---|---|---|
| Claude Code | project / user | `.mcp.json` + `.claude/settings.json`, `~/.claude.json` |
| Claude Desktop | user | `claude_desktop_config.json` |
| Command Code | project / user | `.commandcode/mcp.json`, `~/.commandcode/mcp.json` |
| OpenCode | project / global | `opencode.json`, `~/.config/opencode/opencode.json` |
| OpenAI Codex CLI | project / user | `.codex/config.toml`, `~/.codex/config.toml` |
| Gemini CLI | project / user | `.gemini/settings.json` + `GEMINI.md` |
| Cursor | project / global | `.cursor/mcp.json`, `~/.cursor/mcp.json` |
| VS Code Copilot | project | `.vscode/mcp.json` |
| Windsurf | user | `~/.codeium/windsurf/mcp_config.json` |
| Roo Code / Cline | project | `.roo/mcp.json` |
| Trae | project | `.trae/mcp.json` |
| JetBrains Junie | project / user | `.junie/mcp/mcp.json`, `~/.junie/mcp/mcp.json` |
| Kiro | project | `.kiro/settings/mcp.json` |
| Warp | user | `~/.warp/.mcp.json` |
| Zed | user | `~/.config/zed/settings.json` (`context_servers`) |
| Continue.dev | project | `.continue/mcpServers/tier.yaml` |
| Agent Skills | project / user | `.agents/skills/tier/SKILL.md` |

Any other client can be configured with `--custom-config PATH --schema NAME`, and
`tier config-snippet` prints ready-to-copy JSON, TOML, YAML, and `SKILL.md` snippets plus native
CLI one-liners.

</details>

<details>
<summary><b>Optional extras</b></summary>

```sh
uv tool install "tier-ctx[ui] @ git+https://github.com/000jd/tier"          # rich terminal UI
uv tool install "tier-ctx[mcp] @ git+https://github.com/000jd/tier"         # official MCP SDK
uv tool install "tier-ctx[treesitter] @ git+https://github.com/000jd/tier"  # multi-language AST
uv tool install "tier-ctx[all] @ git+https://github.com/000jd/tier"
```

- `[ui]` adds `rich` rendering for `tier install` and `tier doctor`.
- `[mcp]` adds the official SDK adapter (`tier-mcp-sdk`), Streamable HTTP, and MCP Inspector.
- `[treesitter]` adds Tree-sitter grammars for TypeScript, JavaScript, Go, Rust, C, C++, Java,
  and Ruby. Without it the stdlib extractors are used, and repository maps are byte-identical.

</details>

## Documentation

- [`SPEC.md`](SPEC.md) — the format, parser rules, and retrieval protocol
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — module boundaries, layer flow, and invariants
- [`docs/cli.md`](docs/cli.md) — every command and flag
- [`docs/mcp.md`](docs/mcp.md) — the 12 MCP tools, one-turn workflow, and cache behaviour
- [`benchmarks/README.md`](benchmarks/README.md) — workload definitions and methodology
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development setup and extension guides
- [`CHANGELOG.md`](CHANGELOG.md) — release history

## Limits

Only token counts, retrieval identifiers, and exact-slice coverage are measured — not LLM answer
accuracy. Claude's tokenizer is not public, so counts use OpenAI `o200k` via a persistent Node
worker when available, and a characters/4 estimate otherwise. `tier hook` treats its token budget
as a contract: it skips a payload only when it is provably within budget, otherwise it counts
exactly and verifies the output. `tier sync` trusts recorded mtime and size as a cache; use
`tier sync --rehash` after tooling that restores timestamps. JS/TS/Go/Rust symbol extraction is
heuristic without the `[treesitter]` extra.

## License

MIT © 2026 000jd. See [LICENSE](LICENSE).
