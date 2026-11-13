<div align="center">

# TIER v1.0.0 — Tiered Information for Efficient Retrieval

**A 4-layer context format, codebase graph indexer, and zero-dependency MCP server that cuts AI agent token usage by 86%–97%.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Zero Dependencies](https://img.shields.io/badge/Zero%20Dependencies-none-brightgreen)](pyproject.toml)
[![MIT License](https://img.shields.io/badge/MIT-License-blue)](LICENSE)
[![Tests 103/103](https://img.shields.io/badge/Tests-103%2F103-brightgreen)](tests/)
[![CI](https://github.com/000jd/tier/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![MCP 2.3 Compatible](https://img.shields.io/badge/MCP-2.3%20Compatible-8A2BE2)](src/tier/mcp/sdk_server.py)

[Quickstart](#30-second-quickstart) · [See it in action](#see-it-in-action) · [Benchmarks](#verified-benchmarks) · [Architecture](ARCHITECTURE.md) · [Format spec](SPEC.md)

</div>

---

TIER keeps AI agents from loading whole repositories and documents into context. It gives every document an always-loadable **L0 index**, one-line **L1 abstracts**, **L2 signatures and dependency edges**, and surgical **L3 source-line slices**. A hybrid retriever, incremental repository map, reverse-dependency impact graph, and write-back memory keep context small and current.

The core is **100% Python standard library** — no runtime dependencies, no API calls, no telemetry. Optional extras add terminal styling and the official MCP SDK adapter.

## 30-Second Quickstart

Requires Python 3.10+ (the official `mcp` SDK also requires 3.10+). Install into an isolated
tool environment straight from Git — no virtualenv or PEP 668 workaround needed:

```sh
uv tool install git+https://github.com/000jd/tier      # recommended
pipx install git+https://github.com/000jd/tier         # equivalent with pipx
```

For development inside a virtualenv:

```sh
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e .
```

> **Package name.** The bare name `tier` on PyPI is an unrelated 2022 stub package, and
> `pip install tier` will install the wrong project. Install from the Git URL above (the
> distribution is published as `tier-ctx` when installing from an index), or from a local
> checkout. A bare `pip install .` inside a system interpreter also fails on PEP 668
> externally-managed Linux distributions — use `uv tool`/`pipx` or a venv.

Then run the two commands that configure and verify everything:

```sh
tier install      # interactive wizard: Claude Code, Claude Desktop, Cursor, VS Code, Windsurf
tier doctor       # environment, configs, repo.tier, lint, and sync checks
```

`tier install` writes safe `.bak` backups, preserves other MCP servers, and is idempotent. Use `--clients`, `--yes`, `--dry-run`, or `--uninstall` for scripts and CI. `tier setup` is an alias.

## See It in Action

```text
$ tier from-repo . -o repo.tier
wrote repo.tier

$ tier index repo.tier | head -4
tier repository map - 24 source files; languages: javascript 1, python 23; entrypoints: ...
! Read the repo.tier module map before opening source files; fetch only the listed line ranges.
! Use tier_get with follow=true to pull a module and its direct local dependencies.
! Use tier_slice for exact L3 source slices instead of reading whole files.

$ tier find repo.tier "parse TIER document" -k 1
@src-tier-parser.py  46.75  src/tier/parser.py (python module); symbols: wrap_untrusted, parse, nodes, ...

$ tier slice repo.tier src/tier/parser.py parse
@src-tier-parser.py#parse [L43-L111]
    def parse(text: str) -> Doc:
        doc = Doc()
        ...

$ tier impact repo.tier src/tier/parser.py
Impact target: @src-tier-parser.py
Direct dependents: @src-tier-__init__.py, @src-tier-cli.py, @src-tier-converter.py, ...
Dependencies: @src-tier-models.py
```

**Measured on this repository:** 49,725 raw `src/tier` tokens → **695-token L0** → **1,460-token L0 + one-hop**. The complete five-step workflow costs **4,441 tokens (−91.1%)** while retrieving 3/3 exact slices and 10/10 dependents.

## Core Capabilities

| Capability | What it does |
|---|---|
| **4-Layer Progressive Disclosure** | L0 index, L1 abstracts, L2 signatures and `>` edges, L3 exact source lines. Load only the layer you need. |
| **Polyglot Repo Maps** | Python AST plus JS/TS/Go/Rust symbol extractors, exact line ranges, fingerprints, and local import edges. Verified **203/203 symbols** and **75 edges** on this repo. |
| **Hybrid Search** | Identifier splitting, BM25-style field weights, synonyms, trigrams, table boosts, and navigation priors. **15/15 hit@1** on a 197-section blind corpus. |
| **Collision-Free Untrusted Guards** | `wrap_untrusted` picks a guard that cannot be forged in the payload; `<<<source #g1 ... >>>g1` keeps external text opaque. |
| **Stateful Write-Back Memory** | `tier_remember` persists verified rules, decisions, keys, and references with atomic, lint-checked writes. |
| **Stream & Hook Compression** | `tier pipe` and `tier hook` collapse noisy logs, JSON, and tool output; malformed and binary input are handled safely. |
| **Safe Interactive Installer** | `tier install` detects five host families, backs up configs, previews diffs, and uninstalls only TIER entries. `tier doctor` verifies the result. |

## Verified Benchmarks

All numbers below are from [`benchmarks/BENCH.txt`](benchmarks/BENCH.txt), produced by [`benchmarks/bench.py`](benchmarks/bench.py) with the `o200k_base` tokenizer. TIER measures tokens and retrieval identifiers, **not LLM answer accuracy**.

| Track | What it measures | Verified result |
|---|---|---|
| **A** | 60 uniform records × 6 fields | TIER table **1,331 tokens**, **−61%** vs pretty JSON; same as CSV |
| **B** | Nested config, 4 levels | TIER key/value **143 tokens**, **−39%** vs pretty JSON, **−13%** vs YAML |
| **C** | Payments runbook retrieval | **8/8 hit@1**, **8/8 hit@2**; 352-token L0; mean top-1 fetch 124 tokens |
| **D** | 5 real GitHub Markdown docs | **+2% to +15%** vs Markdown; **99.2–100%** word recall; 0 lint errors |
| **E** | Paraphrase retrieval | **10/10 hit@1** (v0.2 exact-prefix: 7/10) |
| **F** | Repository map (`src/tier`) | 49,725 raw → **7,200 full map (−86%)**; **695 L0**; **1,460 L0 + 1-hop (−97%)**; **203/203 symbols**, **75 edges**, 0 lint errors |
| **G** | End-to-end 4-layer workflow | **4,441 tokens (−91.1%)**; 3/3 exact slices; 10/10 expected dependents |
| **H** | Polyglot fixture (Python/TS/Go/Rust) | **116/116 symbols**, **116/116 ranges**, **10/10 edges**; surgical context **2,854 vs 8,249 (−65%)** |
| **I** | 197-section blind retrieval | **15/15 hit@1**, **15/15 hit@2**; mean top-1 **134 tokens**; **6,039 vs 52,275 (−88%)** |

The checked-in self-index [`repo.tier`](repo.tier) maps 24 source files in **11,034 tokens** with an **893-token L0** (8%). Reproduce the full suite:

```sh
./benchmarks/fetch_corpus.sh     # optional: Tracks D and I corpus
python3 benchmarks/bench.py      # writes benchmarks/BENCH.txt
```

<details>
<summary><b>Supported AI Clients & Install Commands</b></summary>

`tier install` / `tier setup` detects and configures:

| Client | Config paths |
|---|---|
| **Claude Code (project)** | `.mcp.json`, `.claude/settings.json` (hook), `CLAUDE.md`, `AGENTS.md` |
| **Claude Code (user)** | `~/.claude.json`, `~/.claude/settings.json`, `~/.claude/CLAUDE.md` |
| **Claude Desktop** | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Linux `~/.config/Claude/claude_desktop_config.json`; Windows `%APPDATA%\Claude\claude_desktop_config.json` |
| **Cursor** | project `.cursor/mcp.json` or global `~/.cursor/mcp.json` |
| **VS Code (Copilot MCP)** | project `.vscode/mcp.json` (`servers` key, `stdio` entry) |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` |

```sh
tier install                                      # interactive
tier install --clients claude-code,cursor --yes   # scripted
tier install --clients all --dry-run              # preview diffs only
tier install --uninstall --clients all            # remove TIER entries only
tier install --no-repo --no-hook --instructions none
```

Every existing JSON config and every `CLAUDE.md`/`AGENTS.md` is backed up to `<file>.bak` before an atomic merge. Existing servers, hooks, and keys are preserved. Instruction files whose `TIER:BEGIN`/`TIER:END` markers are unbalanced are never sliced: the installer appends after the user's text and reports a warning instead of destroying content. The installer prefers an absolute `tier-mcp` executable, falling back to the absolute current interpreter with `-m tier.mcp`, so GUI hosts do not depend on shell `PATH`.

</details>

<details>
<summary><b>Optional Extras (<code>[ui]</code>, <code>[mcp]</code>, <code>[all]</code>)</b></summary>

The default runtime is **stdlib-only**, with `dependencies = []`. Extras install from the same Git URL:

```sh
uv tool install "tier[ui] @ git+https://github.com/000jd/tier"    # rich terminal rendering
uv tool install "tier[mcp] @ git+https://github.com/000jd/tier"   # official mcp v2 SDK adapter
uv tool install "tier[all] @ git+https://github.com/000jd/tier"   # both
# inside a venv from a checkout: pip install -e ".[ui]" / ".[mcp]" / ".[all]"
```

- `[ui]` adds `rich` styling. Without it, the installer and doctor use ANSI colors and box drawing with a clean non-TTY fallback.
- `[mcp]` adds the official `mcp>=2.0` SDK and the `tier-mcp-sdk` command / `python -m tier.mcp.sdk_server` module. It wraps all 11 tools with MCP v2 `MCPServer` (v1 `FastMCP` fallback), supports `mcp dev` for MCP Inspector, and serves Streamable HTTP:

```sh
tier-mcp-sdk /path/to/docs
python3 -m tier.mcp.sdk_server /path/to/docs --transport streamable-http --host 127.0.0.1 --port 8000
mcp dev src/tier/mcp/sdk_server.py
```

Without the extra, `tier-mcp-sdk` prints the exact `pip install "tier[mcp]"` command and exits cleanly. The default `tier-mcp` / `python -m tier.mcp` server remains the zero-dependency stdlib JSON-RPC implementation.

</details>

<details>
<summary><b>Full CLI & MCP Tool Reference</b></summary>

### CLI

| Command | Purpose |
|---|---|
| `tier install`, `tier setup` | Interactive/non-interactive MCP client installer |
| `tier doctor` | Environment, config, repo, and sync diagnostics |
| `tier index FILE` | L0 + L1 index with token and rule counts |
| `tier find FILE QUERY` | Hybrid identifier/synonym/trigram search |
| `tier get FILE ID...` | Fetch sections; `--follow`, `--table raw\|aligned\|anchored` |
| `tier slice FILE MODULE SYMBOL...` | Exact L3 source lines |
| `tier impact FILE TARGET` | Forward/reverse dependency and Git blast radius |
| `tier from-repo PATH` | Build a polyglot repository map |
| `tier sync FILE` | Incremental fingerprint-based refresh |
| `tier remember FILE` | Atomic write-back memory |
| `tier pipe` | Compress stdin JSON/Markdown/logs with an optional budget |
| `tier hook` | Claude Code-style `PostToolUse` JSON filter |
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
python3 -m unittest discover -s tests -q     # 103 tests
tier doctor .                                # 10 checks: 0 failed, 0 warnings
python3 benchmarks/bench.py                  # Tracks A-I; writes benchmarks/BENCH.txt
tier from-repo . -o repo.tier                # refresh the checked-in self-index
tier lint repo.tier && tier lint SPEC.tier
```

**Limits.** Only token counts, retrieval identifiers, and exact-slice coverage are measured — not LLM answer accuracy. Claude's tokenizer is not public; counts use OpenAI `o200k` via a persistent Node worker with an in-memory cache (or a character estimate when Node is unavailable). `tier hook` uses the deterministic estimator so it never pays Node startup. JS/TS/Go/Rust symbol extraction is heuristic, not a full parser. A `--max-tokens` budget below the saved artifact-path cost is best-effort.

## License

MIT © 2026 000jd. See [LICENSE](LICENSE).
