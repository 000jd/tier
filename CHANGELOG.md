# Changelog

Notable changes to TIER, newest first. Entries follow the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/): one `##` heading per released version, `### Added` / `### Changed` / `### Fixed` subsections, and bullets that describe behavior rather than line counts. Versions follow [semantic versioning](https://semver.org/); dates are omitted because releases are cut from tags.

The distribution is published as `tier-ctx`; the import package, the CLI command, and the format extension are all `tier`.

## [1.2.1]

### Changed
- `tier_context` routing now promotes the immediate call neighbours of the strongest symbol matches, not just symbols whose names share a query term. A question phrased in terms of a callee reaches the caller that invokes it, and a search for a symbol still reaches what it calls. The one-turn arm of `benchmarks/agent_ab.py` reaches full symbol recall at a 1500-token budget.
- `tier_context` sizes the dependency summary before packing section bodies and holds its room, so the block that answers "what calls X" is no longer dropped when module bodies fill the budget first.
- Symbol name matching ignores stopwords and treats a leading underscore as a privacy marker rather than part of the name, so `_symbol_table` matches "symbol table" and `is` no longer prefix-matches every `is_*` helper.

### Fixed
- Over-budget `tier_ingest` no longer writes its artifact to the system temp directory, where `tier_get`, `tier_find`, `tier_index`, and `tier_lint` rejected it as outside the root. The artifact is now written content-addressed into the repository's cache directory, readable by every read tool, and pruned to the newest 32 files. This also stops the unbounded accumulation of `tier-pipe-*.tier` files in `/tmp`.
- `build_context` kept its section and slice bookkeeping in two lists that trimming updated inconsistently, so `slices` could name a block that had been dropped from the rendered text. Block metadata now travels with the rendered parts and both lists are derived from the surviving blocks.
- The tool-surface test asserted a subset of names that omitted `tier_context`, so a dropped tool would have passed. It now asserts exact equality with `TOOLS` and pins the count at 12.

## [1.2.0]

### Added

- `glama.json` and a `Dockerfile`, so TIER can be deployed through Glama and run as a containerised MCP server.
- PyPI Trusted Publishing release workflow: tagged releases publish `tier-ctx` with a short-lived OIDC token instead of a stored API key.
- `benchmarks/agent_ab.py`, an agent-workflow comparison (Track J) that runs the same task against a plain repository and against its TIER map and reports the tokens each turn spends.
- `SKILL.md` at the repository root as the canonical Agent Skill; `tier install --clients skills` writes a per-project copy of it.

### Changed

- `tier_context` narrows `|calls` tables at render time: by default it keeps only edges whose caller is one of the matched symbols, leaving more of the budget for L2 bodies and L3 slices. `include_calls="all"` emits every recorded edge and `"none"` omits the table. The stored map and `tier impact` are unchanged and always carry the full caller and callee set.
- The format specification moved from `SPEC.md` to `docs/spec.md`. `SPEC.tier` is built from the new path and every link now points there.

### Fixed

- The repository cache resolves through a `.git` pointer file. Linked `git worktree` checkouts and submodules previously fell through to the per-root user cache because `.git` is not a directory there; they now place the map under their own git directory, and the owning root is recovered from the `.owner` manifest rather than from a literal `.git/info/tier/` path. Each checkout keeps its own map on purpose: worktrees hold different branches, so a shared map would be invalidated on every switch.

## [1.1.0]

### Added

- `tier_context` MCP tool and `tier context` CLI command: one-turn composite retrieval that resolves the target, routes with the hybrid retriever, packs matched sections, L3 slices, and a compact impact summary, and stops at a token budget. `--budget-tokens`, `--no-impact`, and `--no-dedup` are exposed on the CLI.
- Invisible, auto-managed repository cache (`src/tier/cache.py`). A map resolves to a tracked `<root>/repo.tier` when one exists, otherwise to `<root>/.git/info/tier/repo.tier` inside a worktree, otherwise to a per-root directory under `~/.cache/tier/`. `repo.tier` no longer needs to be tracked, and `git status` stays clean either way.
- Symbol-level call-graph edges, emitted as a `|calls[N]{caller,callee,module}` table per module, and symbol impact reporting: `tier impact module#symbol` reports exact symbol callers and callees in addition to module-level blast radius.
- Optional `[treesitter]` extra (`tree-sitter-language-pack`) for AST symbol extraction across TypeScript, JavaScript, Go, Rust, C, C++, Java, and Ruby. The stdlib extractors remain the default and the fallback; `tier doctor` reports which is in use.
- Installer catalog expands from five host families to 25 clients across seven schemas (`mcpServers`, `servers`, `opencode`, `context_servers`, `codex-toml`, `continue-yaml`, `skill-md`), covering JSON, TOML, YAML, and `SKILL.md` targets.
- Documentation split: `docs/cli.md`, `docs/mcp.md`, `benchmarks/README.md`, `CONTRIBUTING.md`, and this file now carry reference material that had been accumulating in the README.

### Changed

- `tier install` warms the resolved cache location instead of writing a visible `repo.tier` into the working tree.
- Path arguments on `context`, `slice`, `impact`, `sync`, and `install` are optional and default to the cache.
- The MCP tool surface grows from 11 to 12 tools. Both servers (`mcp/server.py` and `mcp/sdk_server.py`) expose the same set, and `tier_context` is registered first.

## [1.0.1]

### Changed

- `tier hook` treats its token budget as a contract. The previous characters/4 gate could under-count dense code by up to 2.34x and pass a payload over budget; the hook now counts exactly, emits only a budget-fitted compression, and reports whether the count was exact or estimated. `--fast` opts back into estimator-only counting and is documented as best-effort.
- `tier sync --rehash` ignores the `mtime`+`size` fast path and compares content hashes for every file, which catches same-size edits whose timestamps were restored by `rsync -a`, `cp -p`, or a tar extraction.
- Renamed the distribution to `tier-ctx`. The `tier`, `tier-mcp`, and `tier-mcp-sdk` entry points are unchanged.
- CI runs a 12-job matrix: three operating systems by four Python versions (3.10 through 3.13).

### Fixed

- CRLF checkouts no longer produce permanent sync churn: fingerprints use on-disk byte size rather than decoded length.
- Impact analysis resolves a symlinked root correctly instead of reporting dependents against the wrong prefix.
- Hook settings written on Windows are quoted correctly for the shell that reads them.

## [1.0.0]

### Added

- The four-layer format in its current form: L0 title, summary, rules, and index; L1 abstracts; L2 signatures, metadata, key/value trees, tables, and `>` edges; L3 exact source lines for one symbol.
- Polyglot repository maps: `tier from-repo` indexes Python, JavaScript, TypeScript, Go, and Rust into a layered architecture map with exact line ranges, and `tier slice` returns the L3 lines for a named symbol.
- Impact analysis: `tier impact` inverts the dependency graph, reports direct and transitive dependents, and optionally compares against Git changes.
- Session deduplication: repeated section bodies and slices are replaced with a one-line stub instead of being resent.
- Universal installer: `tier install` detects supported hosts and configures each one, with backups, dry-run, focused uninstall, and `tier doctor` validation.
- MCP server: a dependency-free stdio JSON-RPC implementation with 11 tools, plus an optional official-SDK adapter for `tier-mcp-sdk` and Streamable HTTP.
- Benchmarks: a nine-track suite covering records, nested config, runbook retrieval, real-world Markdown, paraphrase retrieval, the self-index, the end-to-end workflow, a polyglot fixture, and blind retrieval.

## [0.3.0]

### Added

- Hybrid retrieval: identifier splitting, synonyms, BM25-style weights, trigram fallback, and table-field boosts replace exact-prefix matching.
- Stateful write-back memory through `tier remember`, with lint-checked, atomic file updates and size bounds.
- `tier pipe` and `tier hook` for compressing logs, JSON, and tool payloads.
- The installer: per-client config merging, backups, dry-run, and uninstall.

## [0.2.0]

### Added

- Sections with ids, titles, and abstracts, so a document has an addressable structure.
- Tables with declared row counts and escape rules; references (`>` edges) between sections.
- Retrieval: the L0 index, `tier find`, and `tier get` with dependency following.

## [0.1.0]

### Added

- Initial format: the line sigils, the key/value and untrusted-guard grammar, the parser, the serializer, and the linter.
