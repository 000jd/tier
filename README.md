<div align="center">

# TIER v1.0.1 — Tiered Information for Efficient Retrieval

**A 4-layer context format, codebase graph indexer, and zero-dependency MCP server that cuts AI agent token usage by 86%–97%.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Zero Dependencies](https://img.shields.io/badge/Zero%20Dependencies-none-brightgreen)](pyproject.toml)
[![MIT License](https://img.shields.io/badge/MIT-License-blue)](LICENSE)
[![Tests 112/112](https://img.shields.io/badge/Tests-112%2F112-brightgreen)](tests/)
[![CI](https://github.com/000jd/tier/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![MCP 2.3 Compatible](https://img.shields.io/badge/MCP-2.3%20Compatible-8A2BE2)](src/tier/mcp/sdk_server.py)

[Install](#installation--quickstart) · [Why TIER?](#why-tier-how-it-works-under-the-hood) · [See it in action](#see-it-in-action) · [Benchmarks](#verified-benchmarks) · [Architecture](ARCHITECTURE.md) · [Format spec](SPEC.md)

</div>

---

TIER keeps AI agents from loading whole repositories and documents into context. It gives every document an always-loadable **L0 index**, one-line **L1 abstracts**, **L2 signatures and dependency edges**, and surgical **L3 source-line slices**. A hybrid retriever, incremental repository map, reverse-dependency impact graph, and write-back memory keep context small and current.

The core is **100% Python standard library** — no runtime dependencies, no API calls, no telemetry. Optional extras add terminal styling and the official MCP SDK adapter.

## Installation & Quickstart

### Prerequisites

| Requirement | Why it matters | Check |
|---|---|---|
| **Python 3.10+** | Required runtime. The official `mcp` SDK also requires 3.10+; 3.9 reached end of life in October 2025. | `python3 --version` |
| **`uv` or `pipx`** *(recommended)* | Installs the CLI into an isolated environment, so you never hit PEP 668 "externally managed environment" errors on Linux or fight a global site-packages tree. | `uv --version` / `pipx --version` |
| **Node 18+** *(optional)* | Enables exact `o200k_base` BPE counts through a persistent worker. Without it TIER falls back to a deterministic characters/4 estimate and says so in `tier doctor`. | `node --version` |
| **Git** | Only for installing from the Git URL below. | `git --version` |

### Step 1 — Install the CLI

Install straight from Git into an isolated tool environment. No virtualenv and no PEP 668 workaround needed:

```sh
# recommended
uv tool install git+https://github.com/000jd/tier

# or with pipx
pipx install git+https://github.com/000jd/tier
```

Add the optional extras (`rich` terminal styling + the official `mcp` SDK adapter) with:

```sh
uv tool install "tier-ctx[all] @ git+https://github.com/000jd/tier"
# or:  pipx install "tier-ctx[all] @ git+https://github.com/000jd/tier"
```

Or install from a local clone inside a virtual environment:

```sh
git clone https://github.com/000jd/tier
cd tier
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[all]"
```

> **Package name — read this once.** The distribution is published as **`tier-ctx`**; the import
> package and the console scripts are `tier`, `tier-mcp`, and `tier-mcp-sdk`. The bare name `tier`
> on PyPI is an unrelated 2022 stub package, so **`pip install tier` installs the wrong project** —
> always install `tier-ctx` or the Git URL above. A bare `pip install .` inside a system interpreter
> also fails on PEP 668 externally-managed distributions; use `uv tool`, `pipx`, or a venv.

Verify the entrypoints resolve:

```sh
tier --version          # tier 1.0.1
tier-mcp --version      # tier-mcp 1.0.1
tier-mcp --help         # usage + the 11 tools (needs no dependencies)
```

### Step 2 — Connect your AI assistant in 10 seconds

Point TIER at your project root. The wizard lists every supported agent, marks the ones detected on your machine, previews a diff, backs up every file it touches, and merges rather than overwrites:

```sh
cd /path/to/your/project
tier install        # 25 clients: Claude, Command Code, OpenCode, Codex, Gemini, Cursor, Zed, …
```

The selection prompt accepts numbers, or one of four shortcuts:

| Input | Selects |
|---|---|
| `1,3,8` | Those rows |
| `d` / `detected` | Every client detected on this machine (config dir or binary present) |
| `a` / `all` | Every supported client |
| `p` / `project` | All project-scoped clients (writes inside your repo) |
| `c` / `custom` | Type any config path and pick a schema |

For scripts and CI, skip the prompts:

```sh
tier install . --clients claude-code,cursor,vscode --yes
tier install . --clients detected --yes          # everything found on this machine
tier install . --clients all --dry-run           # preview every client
tier install . --custom-config my/harness.json --schema mcpServers --yes
```

Then confirm everything is wired up correctly:

```sh
tier doctor .
```

`tier doctor` validates each registered client **according to its own schema** — JSON `mcpServers`, VS Code `servers`, OpenCode `mcp`, Zed `context_servers`, Codex TOML, Continue YAML, and `SKILL.md` — and exits nonzero only on a `FAIL`. The fixed rows cover the Python version, entrypoints, tokenizer backend, target root, `repo.tier` presence, repo lint, and repo sync freshness; detected client configs, TIER hooks, and the optional `mcp` extra add one row each.

Browse the catalog and copy config by hand when you need to:

```sh
tier clients                    # every client id, scope, schema, and config path
tier clients --json             # same, machine-readable
tier config-snippet             # ready-to-copy JSON/TOML/YAML/SKILL.md + native CLI one-liners
tier install --print-config     # same snippets, via the installer
```

What the installer guarantees:

- Every existing config is backed up to `<file>.bak` before an atomic merge — including TOML and YAML.
- Unrelated servers, tables, hooks, and keys are preserved. Re-running is idempotent.
- Uninstall removes **only** TIER: other `[mcp_servers.*]` tables, `mcpServers` entries, and OpenCode/Zed servers survive untouched. A Codex `config.toml` is restored byte-for-byte.
- `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md` use `TIER:BEGIN`/`TIER:END` markers; unbalanced markers are never sliced through, so your text is preserved and a warning is printed.
- The server entry prefers an absolute `tier-mcp` executable and otherwise falls back to the absolute current interpreter with `-m tier.mcp`, so GUI hosts do not depend on shell `PATH`.

Use `--dry-run` to print unified diffs without touching disk, and `--uninstall` to remove only TIER entries:

```sh
tier install . --clients all --dry-run
tier install . --uninstall --clients all
tier install . --no-repo --no-hook --instructions none
```

<details>
<summary><b>Supported AI agents, exact config paths &amp; schemas (25 clients)</b></summary>

Every client below is configured automatically with `tier install . --clients <id> --yes`.
Replace `/abs/path/to/project` with your project root.

| Client id | Display | Scope | Config file | Schema |
|---|---|---|---|---|
| `claude-code` | Claude Code | project | `.mcp.json` (+ `.claude/settings.json` hook, `CLAUDE.md`, `AGENTS.md`) | `mcpServers` |
| `claude-code-user` | Claude Code | user | `~/.claude.json` (+ `~/.claude/settings.json`) | `mcpServers` |
| `claude-desktop` | Claude Desktop | user | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`<br>Linux `~/.config/Claude/claude_desktop_config.json`<br>Windows `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` |
| `command-code` | Command Code | project | `.commandcode/mcp.json` | `mcpServers` |
| `command-code-user` | Command Code | user | `~/.commandcode/mcp.json` | `mcpServers` |
| `opencode` | OpenCode | project | `opencode.json` | `mcp` + `type: local`, `command` **array** |
| `opencode-user` | OpenCode | user | `~/.config/opencode/opencode.json` | `mcp` + `type: local` |
| `codex` | OpenAI Codex CLI | project | `.codex/config.toml` | TOML `[mcp_servers.tier]` |
| `codex-user` | OpenAI Codex CLI | user | `~/.codex/config.toml` | TOML `[mcp_servers.tier]` |
| `gemini` | Gemini CLI | project | `.gemini/settings.json` (+ `GEMINI.md`) | `mcpServers` |
| `gemini-user` | Gemini CLI | user | `~/.gemini/settings.json` | `mcpServers` |
| `cursor` | Cursor | project | `.cursor/mcp.json` | `mcpServers` |
| `cursor-user` | Cursor | user | `~/.cursor/mcp.json` | `mcpServers` |
| `vscode` | VS Code Copilot | project | `.vscode/mcp.json` | `servers` + `type: stdio` |
| `windsurf` | Windsurf | user | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |
| `roo` | Roo Code / Cline | project | `.roo/mcp.json` | `mcpServers` |
| `trae` | Trae | project | `.trae/mcp.json` | `mcpServers` |
| `junie` | JetBrains Junie | project | `.junie/mcp/mcp.json` | `mcpServers` |
| `junie-user` | JetBrains Junie | user | `~/.junie/mcp/mcp.json` | `mcpServers` |
| `kiro` | Kiro | project | `.kiro/settings/mcp.json` | `mcpServers` |
| `warp` | Warp Terminal | user | `~/.warp/.mcp.json` | `mcpServers` |
| `zed` | Zed Editor | user | `~/.config/zed/settings.json` | `context_servers` → `command.path` |
| `continue` | Continue.dev | project | `.continue/mcpServers/tier.yaml` | YAML `mcpServers:` |
| `skills` | Agent Skills | project | `.agents/skills/tier/SKILL.md` | `SKILL.md` frontmatter |
| `skills-user` | Agent Skills | user | `~/.agents/skills/tier/SKILL.md` | `SKILL.md` frontmatter |

**Aliases** accepted by `--clients`: `claude`, `claude-user`, `commandcode`, `cmd`, `codex-cli`,
`gemini-cli`, `cursor-global`, `vs-code`, `code`, `cline`, `roo-code`, `skill`, `opencode-global`.

#### Schema examples

Standard JSON — Claude Code, Command Code, Claude Desktop, Cursor, Gemini CLI, Windsurf, Roo, Trae, Junie, Kiro, Warp:

```json
{ "mcpServers": { "tier": { "command": "/abs/bin/tier-mcp", "args": ["/abs/path/to/project"] } } }
```

VS Code (`.vscode/mcp.json`) uses `servers` and an explicit stdio type:

```json
{ "servers": { "tier": { "type": "stdio", "command": "/abs/bin/tier-mcp", "args": ["/abs/path/to/project"] } } }
```

OpenCode (`opencode.json`) keeps your `$schema`, `model`, and other keys; the command is a single array:

```json
{
  "$schema": "https://opencode.ai/schema.json",
  "mcp": {
    "tier": { "type": "local", "command": ["/abs/bin/tier-mcp", "/abs/path/to/project"], "enabled": true }
  }
}
```

Zed (`~/.config/zed/settings.json`) nests command and args:

```json
{ "context_servers": { "tier": { "command": { "path": "/abs/bin/tier-mcp", "args": ["/abs/path/to/project"] }, "settings": {} } } }
```

Codex CLI (`.codex/config.toml`) — other tables such as `[features]` and `[mcp_servers.*]` are preserved:

```toml
[mcp_servers.tier]
command = "/abs/bin/tier-mcp"
args = ["/abs/path/to/project"]
```

Continue.dev (`.continue/mcpServers/tier.yaml`):

```yaml
name: tier
version: 0.0.1
schema: v1
mcpServers:
  - name: tier
    command: "/abs/bin/tier-mcp"
    args:
      - /abs/path/to/project
```

Agent Skills (`.agents/skills/tier/SKILL.md`) — a full skill with frontmatter that teaches both the
MCP tools and the CLI:

```markdown
---
name: tier
description: Navigate this repository with TIER's 4-layer index instead of reading whole files.
---
# TIER — layered context for this repository
… tier index / tier find / tier slice / tier impact tables, MCP tool reference, working rules …
```

#### Native CLI one-liners

`tier config-snippet` prints these with your real paths filled in:

```sh
claude mcp add tier -- /abs/bin/tier-mcp /abs/path/to/project
cmd mcp add tier -- /abs/bin/tier-mcp /abs/path/to/project
opencode mcp add tier -- /abs/bin/tier-mcp /abs/path/to/project
codex mcp add tier -- /abs/bin/tier-mcp /abs/path/to/project
gemini mcp add tier /abs/bin/tier-mcp /abs/path/to/project
tier-mcp /abs/path/to/project          # any generic stdio client
```

#### Claude Code

```sh
tier install . --clients claude-code --yes

# or register the server yourself
claude mcp add tier -- tier-mcp /abs/path/to/project
claude mcp add --scope project tier -- tier-mcp /abs/path/to/project   # shared via .mcp.json
claude mcp list
```

`tier install` writes this into `.mcp.json`:

```json
{
  "mcpServers": {
    "tier": {
      "command": "/usr/local/bin/tier-mcp",
      "args": ["/abs/path/to/project"]
    }
  }
}
```

…and registers the output-compression hook in `.claude/settings.json`. The hook runs on `PostToolUse` for `Bash|Read|Grep|Glob|WebFetch`, keeps tool output inside a 600-token budget, and is a no-op when the payload already fits:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash|Read|Grep|Glob|WebFetch",
        "hooks": [
          { "type": "command", "command": "/usr/local/bin/tier hook --max-tokens 600" }
        ]
      }
    ]
  }
}
```

If you would rather not install the hook, pass `--no-hook`.

#### MCP Inspector (browser, interactive testing)

The Inspector needs the `[mcp]` extra, because it drives the official-SDK adapter:

```sh
# one command from a checkout, opens the Inspector UI
mcp dev src/tier/mcp/sdk_server.py

# or serve the same 11 tools over Streamable HTTP and point the Inspector at it
tier-mcp-sdk /abs/path/to/project --transport streamable-http --host 127.0.0.1 --port 8000
```

You can also inspect the zero-dependency server with no Python extras at all:

```sh
npx @modelcontextprotocol/inspector tier-mcp /abs/path/to/project
```

Each server exposes the same 11 tools: `tier_list`, `tier_index`, `tier_find`, `tier_get`, `tier_slice`, `tier_impact`, `tier_sync`, `tier_lint`, `tier_ingest`, `tier_repo`, `tier_remember`.

> **Windows note.** Prefer the absolute `tier-mcp` path that `tier install` writes. Quoted
> executable paths are handled on every platform, and hook commands use `list2cmdline` on Windows.

</details>

## Why TIER? (How It Works Under the Hood)

Seven design decisions, each tied to a measured number in [`benchmarks/BENCH.txt`](benchmarks/BENCH.txt). TIER measures **tokens and retrieval identifiers**, not LLM answer accuracy.

### 1. High token efficiency: header-once tables and indented key/value trees

Pretty JSON spends context on quotes, braces, colons, and a repeated copy of every key. Markdown tables spend it on repeated pipe-and-dash borders.

**The TIER advantage.** A TIER table is a header plus exactly `N` CSV rows:

```
|orders[60]{id,customer,status,items,total,created}
1000,Ana Silva,paid,7,262.12,2026-02-27
```

That is **1,331 tokens vs 3,393 for pretty JSON (−61%)** — the same compactness as raw CSV (1,327) while keeping the column schema and an explicit row count `N` that lets a parser detect truncation. On retrieval, `--table aligned` renders a spaced Markdown table and `--table anchored` emits per-row primary-key anchors (`[1000] id: 1000 | customer: …`) with the column list repeated every 20 rows for long tables, without changing the stored bytes.

Nested data becomes an indented key/value tree instead of a brace nest:

```
= service
  name payments-api
  limits
    replicas
      min 8
```

At **143 tokens**, that is **−39% vs pretty JSON and −13% vs YAML** (`Bench A & B`).

### 2. Four-layer progressive disclosure, L0 through L3

Markdown and JSON are all-or-nothing: an agent that needs one function typically pulls in the whole file. TIER makes the layers explicit:

| Layer | Contents | When an agent reads it |
|---|---|---|
| **L0** | Document metadata, global `%rule` constraints, and the section map, each line carrying its token cost (`~Nt`) and rule count (`!R`) | Always — a few hundred tokens |
| **L1** | One-line `?` abstracts saying *when* to fetch the section | Always, folded into the L0 index |
| **L2** | Full section bodies; for repositories, `\|symbols` tables with exact `[Lstart-Lend]` ranges and `>` dependency edges | On demand, per module |
| **L3** | Exact function, class, or method source lines via `tier slice` / `tier_slice` | Surgically, per symbol |

**The TIER advantage.** On the payments runbook an agent loads a **352-token L0 index instead of the 1,463-token Markdown file (−76%)**. For a codebase, this repository's `src/tier` maps to a **717-token L0** and **1,484 tokens for L0 plus one module plus its one-hop dependencies — 97% fewer than the 54,766 raw source tokens**. Across indexing and a full five-step edit workflow, progressive disclosure cuts consumption by **86% to 97%** (`Bench F & G`).

### 3. Deterministic line-start sigils, so structure is never ambiguous

In Markdown, a `#` heading inside a pasted log or README is indistinguishable from a document heading, and section boundaries blur.

Every TIER line is classified by its first character, with one meaning and no exceptions:

| Sigil | Meaning |
|---|---|
| `%` | document header (`%title`, `%sum`, `%rule`) |
| `@` | section boundary |
| `?` | abstract — when to read the section |
| `!` | imperative rule |
| `=` | key/value tree (2 spaces per level) |
| `\|` | counted table (`\|name[N]{cols}` + `N` rows) |
| `>` | graph reference edge |
| `$` | shell command |
| `<<<` ... `>>>` | opaque untrusted data block (optional `#guard` on the close line) |
| triple backtick | opens and closes a verbatim fenced block; contents are never parsed |
| `\` | escape: the next line is prose even if it starts with a sigil |

**The TIER advantage.** The parser and the model agree on the same rule, so hard constraints (`!`), traversable graph edges (`>`), and prose stay separable without decorative markup. Fenced code, untrusted blocks, and table rows are opaque — no sigil inside them is ever reinterpreted.

### 4. Untrusted data guards that are provably contained

When an agent ingests a log, a web scrape, or a ticket, embedded instructions (`Ignore previous instructions…`) or fake section headers can turn into indirect prompt injection.

**The TIER advantage.** External text is wrapped in a named guard block, and only the matching close line ends it:

```
<<<web:example.com #g1
...arbitrary external text; @, !, %, and | here are all inert...
>>>g1
```

`wrap_untrusted()` in [`parser.py`](src/tier/parser.py) scans the payload's closing lines and picks the first guard `g1`, `g2`, … that does **not** occur in it, so a payload cannot forge its own terminator. The test suite fuzzes this with **120 randomized payloads drawn from 27 adversarial escape patterns** — bare `>>>`, spoofed `>>>g1`, trailing whitespace, NUL / non-breaking-space / zero-width-space tricks, `<<<x #g99`, and fake `@`, `%`, `!`, `|`, and `$` lines — and asserts that each stays inside a single opaque block with every payload line preserved. Running that fuzz reports **0 escapes**. The v0.1 unguarded form still parses, closing at the first `>>>`.

### 5. Stateful session deduplication

In a multi-turn loop an agent re-reads the same section, or pulls overlapping `>` hops, and pays for the same tokens twice.

**The TIER advantage.** `Session` in [`retrieval.py`](src/tier/retrieval.py) records every `[document, section]` pair and every `section#symbol` L3 slice already delivered. A repeat request returns a one-line stub — `@section-id (already in context; not resent)` — instead of the body; `force=True` re-sends it, and `reset()` clears the cache. On the eight-question runbook session, **L0 + top-1 + dedup stays below the full Markdown document at every single question**, and dedup avoids re-sending **843 tokens** on the top-2 path (`Bench C`).

When `tier sync` detects an on-disk edit it evicts exactly the affected sections and their slices, including the MCP server's shared cache, so a stale body is never served as if it were current.

### 6. Polyglot codebase graph and blast-radius impact analysis

Flat text formats treat a repository as a pile of files. TIER indexes it as a graph:

- **Python** symbols come from the built-in `ast` module, with decorator-inclusive start lines so a slice never begins mid-definition.
- **TypeScript / JavaScript / Go / Rust** symbols come from brace matchers that ignore braces inside strings, template literals, and comments, and handle multi-line signatures, arrow bodies, and Rust lifetimes.
- Each module records a `= file` fingerprint tree (`path`, `language`, `lines`, `sha256`, `mtime`, `size`) and a `|symbols[N]{kind,name,signature,lines}` table.
- Local Python imports, relative JS/TS imports, Go imports resolved through `go.mod`, and Rust `mod`/`use crate::` paths become `>` edges.

**The TIER advantage.** `tier impact <repo.tier> <module>` inverts that graph to report direct callers, transitive dependents, dependencies, and — with `--git` — changed symbols plus every downstream module that must be re-tested. `tier_impact` exposes the same report over MCP.

On this repository the map preserves **230/230 symbols** and **76 internal edges** with 0 lint errors; on the 36-file polyglot fixture it preserves **116/116 symbols**, **116/116 accurate line ranges**, and **10/10 cross-file edges**, and the surgical L0 + target L2 + 7 L3 slices path costs **2,854 tokens against 8,249 raw (−65%)** (`Bench F & H`). The end-to-end workflow retrieves **3/3 exact slices** and **10/10 expected dependents** (`Bench G`).

### 7. Hybrid search with zero external vector databases

Conventional RAG needs an embedding API and a vector store, which means network calls, keys, and per-query cost.

**The TIER advantage.** [`retrieval.py`](src/tier/retrieval.py) is an in-memory hybrid retriever:

- BM25-style scoring with field weights (title 8×, abstract 4×, rules 3×, body and table capped).
- Identifier splitting for `camelCase`, `PascalCase`, `snake_case`, and `kebab-case`.
- A compact engineering/operations synonym map, with conservative plural normalization (`queries → query`, but `process`, `status`, and `analysis` keep their `s`).
- Character 3-gram fallback for typos, plus a table-field boost.
- Navigation priors: test modules are down-weighted unless the query mentions tests, and table-of-contents sections are down-weighted for content queries.

Document frequencies and IDF are computed **once per query**, which removes the extra factor of section count from the scoring loop: a query over **800 sections completes in about 210 ms** (median of 5 runs) and is linear in section count for a fixed query. Accuracy is **10/10 hit@1 on paraphrase queries** (`Bench E`, up from 7/10 for the exact-prefix v0.2 matcher) and **15/15 hit@1 and hit@2 across a 197-section, multi-document blind corpus** (`Bench I`), with no embeddings, no network, and no dependencies.

## See It in Action

```text
$ tier from-repo . -o repo.tier
wrote repo.tier

$ tier index repo.tier | head -4
tier repository map - 25 source files; languages: javascript 2, python 23; entrypoints: ...
! Read the repo.tier module map before opening source files; fetch only the listed line ranges.
! Use tier_get with follow=true to pull a module and its direct local dependencies.
! Use tier_slice for exact L3 source slices instead of reading whole files.

$ tier find repo.tier "parse TIER document" -k 1
@src-tier-parser.py  48.0  src/tier/parser.py (python module); symbols: wrap_untrusted, parse, nodes, ...

$ tier slice repo.tier src/tier/parser.py parse
@src-tier-parser.py#parse [L43-L130]
    def parse(text: str) -> Doc:
        doc = Doc()
        ...

$ tier impact repo.tier src/tier/parser.py
Impact target: @src-tier-parser.py
Direct dependents: @src-tier-__init__.py, @src-tier-cli.py, @src-tier-converter.py, ...
Dependencies: @src-tier-models.py
```

**Measured on this repository:** 54,766 raw `src/tier` tokens → **717-token L0** → **1,484-token L0 + one-hop**. The complete five-step workflow costs **4,687 tokens (−91.4%)** while retrieving 3/3 exact slices and 10/10 dependents.

## Core Capabilities

| Capability | What it does |
|---|---|
| **4-Layer Progressive Disclosure** | L0 index, L1 abstracts, L2 signatures and `>` edges, L3 exact source lines. Load only the layer you need. |
| **Polyglot Repo Maps** | Python AST plus JS/TS/Go/Rust symbol extractors, exact line ranges, fingerprints, and local import edges. Verified **230/230 symbols** and **76 edges** on this repo. |
| **Hybrid Search** | Identifier splitting, BM25-style field weights, synonyms, trigrams, table boosts, and navigation priors. **15/15 hit@1** on a 197-section blind corpus. |
| **Collision-Free Untrusted Guards** | `wrap_untrusted` picks a guard that cannot be forged in the payload; `<<<source #g1 ... >>>g1` keeps external text opaque. |
| **Stateful Write-Back Memory** | `tier_remember` persists verified rules, decisions, keys, and references with atomic, lint-checked writes and configurable size bounds. |
| **Stream & Hook Compression** | `tier pipe` and `tier hook` collapse noisy logs, JSON, and tool output; the hook enforces its token budget exactly, and malformed or binary input is handled safely. |
| **Universal Agent Installer** | `tier install` configures **25 clients** across JSON, TOML, YAML, and `SKILL.md` schemas, backs up every file, previews diffs, and uninstalls only TIER entries. `tier doctor` validates each one in its native schema. |

## Verified Benchmarks

All numbers below are from [`benchmarks/BENCH.txt`](benchmarks/BENCH.txt), produced by [`benchmarks/bench.py`](benchmarks/bench.py) with the `o200k_base` tokenizer. TIER measures tokens and retrieval identifiers, **not LLM answer accuracy**.

| Track | What it measures | Verified result |
|---|---|---|
| **A** | 60 uniform records × 6 fields | TIER table **1,331 tokens**, **−61%** vs pretty JSON; same as CSV |
| **B** | Nested config, 4 levels | TIER key/value **143 tokens**, **−39%** vs pretty JSON, **−13%** vs YAML |
| **C** | Payments runbook retrieval | **8/8 hit@1**, **8/8 hit@2**; 352-token L0; mean top-1 fetch 124 tokens |
| **D** | 5 real GitHub Markdown docs | **+2% to +15%** vs Markdown; **99.2–100%** word recall; 0 lint errors |
| **E** | Paraphrase retrieval | **10/10 hit@1** (v0.2 exact-prefix: 7/10) |
| **F** | Repository map (`src/tier`) | 54,766 raw → **7,834 full map (−86%)**; **717 L0**; **1,484 L0 + 1-hop (−97%)**; **230/230 symbols**, **76 edges**, 0 lint errors |
| **G** | End-to-end 4-layer workflow | **4,687 tokens (−91.4%)**; 3/3 exact slices; 10/10 expected dependents |
| **H** | Polyglot fixture (Python/TS/Go/Rust) | **116/116 symbols**, **116/116 ranges**, **10/10 edges**; surgical context **2,854 vs 8,249 (−65%)** |
| **I** | 197-section blind retrieval | **15/15 hit@1**, **15/15 hit@2**; mean top-1 **134 tokens**; **6,038 vs 52,803 (−89%)** |

The checked-in self-index [`repo.tier`](repo.tier) maps 25 source files in **12,700 tokens** with a **963-token L0** (8%). Reproduce the full suite:

```sh
./benchmarks/fetch_corpus.sh     # optional: Tracks D and I corpus
python3 benchmarks/bench.py      # writes benchmarks/BENCH.txt
```

<details>
<summary><b>Optional Extras (<code>[ui]</code>, <code>[mcp]</code>, <code>[all]</code>)</b></summary>

The default runtime is **stdlib-only**, with `dependencies = []`. Extras install from the same Git URL:

```sh
uv tool install "tier-ctx[ui] @ git+https://github.com/000jd/tier"    # rich terminal rendering
uv tool install "tier-ctx[mcp] @ git+https://github.com/000jd/tier"   # official mcp v2 SDK adapter
uv tool install "tier-ctx[all] @ git+https://github.com/000jd/tier"   # both
# inside a venv from a checkout: pip install -e ".[ui]" / ".[mcp]" / ".[all]"
```

- `[ui]` adds `rich` styling. Without it, the installer and doctor use ANSI colors and box drawing with a clean non-TTY fallback.
- `[mcp]` adds the official `mcp>=2.0` SDK and the `tier-mcp-sdk` command / `python -m tier.mcp.sdk_server` module. It wraps all 11 tools with MCP v2 `MCPServer` (v1 `FastMCP` fallback), supports `mcp dev` for MCP Inspector, and serves Streamable HTTP:

```sh
tier-mcp-sdk /path/to/docs
python3 -m tier.mcp.sdk_server /path/to/docs --transport streamable-http --host 127.0.0.1 --port 8000
mcp dev src/tier/mcp/sdk_server.py
```

Without the extra, `tier-mcp-sdk` prints the exact `pip install "tier-ctx[mcp]"` command and exits cleanly. The default `tier-mcp` / `python -m tier.mcp` server remains the zero-dependency stdlib JSON-RPC implementation.

</details>

<details>
<summary><b>Full CLI & MCP Tool Reference</b></summary>

### CLI

| Command | Purpose |
|---|---|
| `tier install`, `tier setup` | Universal installer for 25 MCP/agent clients; `--clients`, `--custom-config`, `--schema`, `--print-config` |
| `tier clients` | List every supported client id, scope, schema, and config path (`--json` for machines) |
| `tier config-snippet` | Print ready-to-copy JSON/TOML/YAML/`SKILL.md` snippets and native CLI one-liners |
| `tier doctor` | Environment, config, repo, and sync diagnostics |
| `tier index FILE` | L0 + L1 index with token and rule counts |
| `tier find FILE QUERY` | Hybrid identifier/synonym/trigram search |
| `tier get FILE ID...` | Fetch sections; `--follow`, `--table raw\|aligned\|anchored` |
| `tier slice FILE MODULE SYMBOL...` | Exact L3 source lines |
| `tier impact FILE TARGET` | Forward/reverse dependency and Git blast radius |
| `tier from-repo PATH` | Build a polyglot repository map |
| `tier sync FILE` | Incremental fingerprint-based refresh; `--rehash` for a content comparison |
| `tier remember FILE` | Atomic write-back memory |
| `tier pipe` | Compress stdin JSON/Markdown/logs with an optional budget |
| `tier hook` | Claude Code-style `PostToolUse` JSON filter; `--fast` for estimator-only counting |
| `tier lint FILE` | Structural validation |
| `tier from-md FILE` / `to-md` / `to-json` / `compress` | Format conversion |
| `tier init-plugin TARGET` | Legacy one-command MCP + repo + hook setup |
| `tier stats FILE` | Token cost per layer/section |

### MCP tools

`tier_list`, `tier_index`, `tier_find`, `tier_get`, `tier_slice`, `tier_impact`, `tier_sync`, `tier_lint`, `tier_ingest`, `tier_repo`, `tier_remember`.

Start the default server with `tier-mcp /path/to/root` or `python3 -m tier.mcp /path/to/root`. All paths are confined to the configured root; live repository edits auto-sync before reads.

</details>

## Privacy, Zero Telemetry & Contributing

TIER runs entirely on your machine. There are **no telemetry, analytics, API keys, or network calls** in the core runtime. The MCP server is path-sandboxed, repository scans reject symlinks escaping the root, and memory writes are validated before an atomic replace. The optional `[mcp]` Streamable HTTP transport is the only network listener, and only when you explicitly start it.

- Format and safety rules: [SPEC.md](SPEC.md)
- Module boundaries, layer flow, and contributor guide: [ARCHITECTURE.md](ARCHITECTURE.md)
- Examples: [examples/](examples/)
- Full benchmark output: [benchmarks/BENCH.txt](benchmarks/BENCH.txt)

```sh
python3 -m unittest discover -s tests -q     # 112 tests
tier doctor .                                # checks: 0 failed, 0 warnings
python3 benchmarks/bench.py                  # Tracks A-I; writes benchmarks/BENCH.txt
tier from-repo . -o repo.tier                # refresh the checked-in self-index
tier lint repo.tier && tier lint SPEC.tier
```

**Limits.** Only token counts, retrieval identifiers, and exact-slice coverage are measured — not LLM answer accuracy. Claude's tokenizer is not public; counts use OpenAI `o200k` via a persistent Node worker with an in-memory cache (or a character estimate when Node is unavailable). `tier hook` treats its token budget as a contract: it skips a payload only when it is provably within budget, otherwise it counts exactly and verifies the emitted context. `--fast` opts into estimator-only counting, which is best-effort. `tier sync` trusts recorded `mtime`+`size` as a cache; use `tier sync --rehash` to compare content hashes after tooling that restores timestamps. JS/TS/Go/Rust symbol extraction is heuristic, not a full parser. A `--max-tokens` budget below the saved artifact-path cost is best-effort.

## License

MIT © 2026 000jd. See [LICENSE](LICENSE).
