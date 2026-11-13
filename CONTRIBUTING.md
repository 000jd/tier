# Contributing

TIER is a stdlib-only Python package: a line-oriented context format plus a local parser, retrieval engine, repository graph, and installer. Changes are expected to keep the format round-trip stable, keep the import graph acyclic, and keep the checked-in indexes fresh — the CI job enforces the last one.

## Development setup

```sh
git clone https://github.com/000jd/tier
cd tier
python3 -m venv .venv
```

Activate the environment:

```sh
# POSIX (sh, bash, zsh)
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bat
:: Windows cmd.exe
.venv\Scripts\activate.bat
```

Install the package in editable mode with every optional extra:

```sh
pip install -e ".[all]"
```

**Zero required dependencies.** `pyproject.toml` keeps `dependencies = []`. Everything installable is an extra: `[ui]` (`rich`), `[mcp]` (the official `mcp` SDK), `[treesitter]` (`tree-sitter-language-pack`), and `[all]` for the three together. The core must import and run with none of them present; `rich` and the `mcp` package are imported lazily, and the Tree-sitter pack is probed at call time and falls back to the stdlib extractors. CI runs `python -m pip install -e .` with no extras, which is the configuration that matters.

## Running the tests

```sh
python3 -m unittest discover -s tests -q
```

The suite is 159 test methods across 12 `unittest.TestCase` classes and runs in a few seconds:

| File | Tests | Covers |
|---|---|---|
| `tests/test_tier.py` | 94 | Parser, serializer, lint, conversion, retrieval, repo maps, slices, sync, impact, memory, pipe, hook, plugin, the MCP server over stdio, plus the `Audit` and `PerfAndSafety` red-team classes |
| `tests/test_installer.py` | 34 | Client catalog and paths, per-schema merging, backup and uninstall, dry-run, doctor, SDK adapter registration |
| `tests/test_v110.py` | 31 | Composite `context`, repository cache resolution, call graph, Tree-sitter fallback, installer skill |

`benchmarks/node_modules` is optional. When it (or `node`) is absent, `tier.count_tokens` falls back to a deterministic characters/4 estimator and the tests adapt: `test_count_server_worker_roundtrip` skips outright, `test_token_worker_and_cache_exactness` asserts the estimator backend instead of comparing against the Node worker, and the hook-budget tests assert only that the emitted context fits its cap. `corpus/` is likewise optional — fetch it with `./benchmarks/fetch_corpus.sh` if you want `test_blind_retrieval_track_i` to run rather than skip.

Run one module, or one test method, while iterating:

```sh
python3 -m unittest discover -s tests -p "test_v110.py" -v
cd tests && python3 -m unittest test_tier.T.test_roundtrip_stable -v
```

## Architecture invariants

These are the rules from [ARCHITECTURE.md](ARCHITECTURE.md). They are not stylistic preferences; CI and the red-team classes enforce most of them.

- **Zero required Python dependencies.** `pyproject.toml` keeps `dependencies = []`; `rich` and `mcp` are optional extras and must be imported lazily. A module-level `import rich` in core code is a bug.
- **Python 3.10+.** 3.9 reached end of life in October 2025, and the official `mcp` SDK requires 3.10+. Do not add version-gated shims for older interpreters.
- **No import cycles.** Dependencies point one way, in this layer order:

  ```text
  models / tokens / ui
    -> parser / converter / lint
    -> retrieval
    -> repo
    -> cache
    -> context
    -> memory / pipe / plugin / installer
    -> doctor / cli / mcp
  ```

  `models`, `tokens`, and `ui` import nothing from `tier`. Public names are re-exported from `tier/__init__.py`, so anything above a module in the chain may import it. `ARCHITECTURE.md` carries a per-module import table alongside the layer list.
- **Round-trip stability.** `dumps(parse(text))` must be stable for valid documents, and malformed input must never raise an unhandled exception. Every new parser rule or serializer behavior comes with a round-trip test and a fuzz or malformed-input test.
- **Sandbox rules.** MCP paths, symbol slices, repository scans, and memory writes must resolve inside the configured root; symlinks pointing outside it are rejected. `repo_source_files` does not follow symlinks at all, and `slice_symbols` re-checks the real path against the root before reading.
- **Test every boundary.** Add a unit or red-team test for every parser, guard, path, symbol-range, installer, or config-merge change. The `Audit` class in `tests/test_tier.py` exists for adversarial cases: guard collision, path traversal, parser state-machine abuse, and slice boundaries across languages.
- **Refresh the indexes.** Regenerate `repo.tier`, `SPEC.tier`, and `benchmarks/BENCH.txt` when source or benchmarks change. See [Refreshing checked-in artifacts](#refreshing-checked-in-artifacts).

### Repository cache resolution

A repository map is a derived artifact, so `src/tier/cache.py` resolves its location in a fixed order: an explicit `.tier` path, then a tracked `<root>/repo.tier`, then `.git/info/tier/repo.tier`, and finally a per-root directory under `~/.cache/tier/`. The `.git/info` case is the normal one, and it is why `git status` stays clean without a `.gitignore` entry.

`_git_dir` handles both layouts Git can produce. When `.git` is a directory it is read directly; when `.git` is a pointer file — as in a `git worktree` checkout, where it holds `gitdir: <main>/.git/worktrees/<name>`, or in a submodule, where it holds `gitdir: <super>/.git/modules/<name>` — the target is resolved relative to the directory containing `.git`, the same rule Git itself applies. The per-checkout admin directory is used as-is rather than collapsed to the shared common directory: worktrees exist to hold different branches, so one shared map would be invalidated on every checkout switch.

Because a linked worktree's gitdir lives outside the checkout entirely, the path alone no longer identifies the repository the map describes. `_work_root_for` therefore reads the `.owner` manifest written beside the map, and `cache_kind` and `is_cache_dir` match on the `info/tier` segment rather than on a literal `.git`. Keep both properties when touching this module: a map must be found again from its cache path, and directory listings must skip the cache.

## Adding a language extractor

Symbol extraction has two paths and one shared contract.

**The stdlib path.** `_extract_symbols(rel, text)` in `src/tier/repo.py` dispatches on the file extension to `_py_symbols` (an `ast` walk), `_js_symbols`, `_go_symbols`, or `_rust_symbols` (regex and brace matching over lines). Every extractor returns a three-tuple:

```python
(purpose, symbols, imports)
```

- `purpose` is a clipped one-line docstring or leading comment, used as the section abstract.
- `symbols` is a list of 5-tuples `(kind, name, sig, start, end)`. The stdlib extractors emit `function`, `method`, `class`, `struct`, `interface`, `trait`, `enum`, `type`, and `union`; the Tree-sitter path can additionally emit `impl` and `module`. `name` is dotted for members (`Service.fetch`, `Store.Get`); `start` and `end` are 1-indexed inclusive line numbers; `sig` is the declaration, collapsed onto one line and clipped at 160 characters by `_sig_line`.
- `imports` is the raw import record for that language, consumed by the matching `_resolve_*_imports` helper through `_repo_add_deps`. Python emits `(module, level, names)`; JS, Go, and Rust re-scan the text in their own resolvers and return `[]`.

To add a language on this path: write `_xx_symbols(text)`, add the extension to `LANG_BY_EXT` for the rendered language name, add it to `REPO_SOURCE_EXTS`, and wire the dispatch in `_extract_symbols`. Line ranges are load-bearing — `tier slice` reads exactly `[start, end]` from disk, so an off-by-one corrupts every L3 slice for that file.

**The optional Tree-sitter path.** `_TS_LANG_BY_EXT` maps an extension to a grammar name, and `_ts_symbols(text, lang)` returns the same three-tuple (or `None` when the pack is missing or the parse fails). `_extract_symbols` prefers it whenever `_ts_available()`, then falls through to the stdlib extractor. Extensions that only work with the extra belong in `REPO_TS_ONLY_EXTS`, not `REPO_SOURCE_EXTS`: `_active_source_exts()` unions them only when the pack imports, so a repo map is identical with and without the extra.

**A new extension must not change `repo.tier` for existing repos.** That is the hard constraint, and it is why the Tree-sitter-only set is gated rather than merged into `REPO_SOURCE_EXTS`. CI installs no extras and compares the committed map against a fresh sync, so a language that only appears when the pack is present would either break that gate or make the map depend on the environment. After adding an extension, run `tier sync repo.tier --rehash` and confirm `git diff -- repo.tier` is empty. `tests/test_v110.py::TreeSitterTests` asserts the two extension sets never intersect.

**Call edges** ride on the same symbol spans. `_extract_calls(rel, text, symbols, ext)` returns raw `(caller, callee, line)` triples: Python goes through the AST, everything else through `_lexical_call_names`, which skips the declaration line and only accepts attribute calls on `self`/`cls`/`this`/`super`. `_resolve_calls` then maps each callee to the one module that defines it and drops names several modules define rather than guessing. `_call_table` renders the result as a `|calls[N]{caller,callee,module}` table whose rows are `(caller, callee, module_rel)`, with `.` meaning the callee lives in the same module. The table is emitted before the `>` reference lines on purpose: `sync_repo`'s re-link pass rebuilds a section as body plus refs, so anything placed after the refs would migrate on the next sync.

## Adding an MCP tool

A tool is one addition in three places plus documentation.

1. **`src/tier/mcp/server.py`** — append an entry to `TOOLS` with `name`, `description`, and a JSON Schema `inputSchema`, then add a branch to `call(name, a)`. `call` receives already-validated-but-untrusted arguments; coerce and reject there, and remember that every path must be confined with `safe_target`/`_inside`.
2. **`src/tier/mcp/sdk_server.py`** — add `@mcp.tool(name=..., description=_description(...))` to the decorated function set inside `build_server`. `_description()` reads the same `TOOLS` entry, so the description is not duplicated. A tool present in one server and absent from the other is a defect: the two servers are advertised as exposing the same surface.
3. **Docs** — the MCP section of `docs/spec.md`, the tool table in `README.md`, and the `SKILL.md` tool table that `installer.py` generates.
4. **A test** — extend the stdio surface test in `tests/test_tier.py::T.test_mcp_tool_surface_and_repo`, and update the count assertion in `tests/test_installer.py::SDKAdapterTests::test_all_twelve_tools_register_and_call`, which asserts `len(srv.tools) == 12` and that `tier_context` registers first. `tests/test_v110.py::CacheTests::test_mcp_tools_include_context_first` encodes the same ordering assumption.

MCP server code must stay dependency-free and must not import `src/tier/mcp/sdk_server.py`.

### Call-table filtering in `tier context`

`build_context` takes an `include_calls` argument, surfaced as `include_calls` on the `tier_context` MCP tool, that controls how `|calls` tables are rendered. The default (`"auto"`) keeps only edges inside the 1-hop symbol neighbourhood of the matched sections, which is what a single question needs and what leaves the token budget for L2 bodies and L3 slices. Pass `include_calls="all"` to emit every edge the module's map records, or `"none"` for the header alone.

The filter runs at render time only: the stored map keeps every edge, `tier sync` preserves them, and the emitted row count is rewritten so the table stays valid. `tier impact` never goes through this path — it reads the parsed document directly — so `module#symbol` callers and calls are always reported in full. A blast-radius report that silently dropped edges would be worse than useless.

## Adding an installer client

`src/tier/installer.py::_catalog(root, home, env, platform)` returns every supported agent as a `ClientSpec`:

| Field | Meaning |
|---|---|
| `id` | Stable client identifier, also the `--clients` value |
| `label` | Human-readable name shown in selection and doctor output |
| `scope` | `project` or `user`; decides whether `root` or `home` prefixes the path |
| `config_path` | Absolute path to the MCP config file |
| `config_key` | Top-level key that holds server entries (`mcpServers`, `servers`, `mcp`, `mcp_servers`) |
| `hook_path` | Settings file for the hook entry, or `""` |
| `instruction_paths` | Markdown files that receive the marked instruction block |
| `schema` | One of the seven renderers listed below |

The seven schemas are `mcpServers` (a JSON object holding `{command, args}` per server), `servers` (VS Code: the same, plus `"type": "stdio"`), `opencode` (a `mcp` object with `"type": "local"`, a command *array*, and `enabled`), `context_servers` (Zed: a nested `{command: {path, args}, settings}`), `codex-toml` (a `[mcp_servers.tier]` table), `continue-yaml` (a standalone YAML server file), and `skill-md` (a `SKILL.md` document with YAML front matter). `server_entry()` builds the per-schema entry; `config_snippets()` prints all seven for hand-copying. Add an alias to `ALIASES` if the id should be spellable more than one way, and to `SCHEMA_ALIASES` if the schema accepts a short name.

A new client needs four tests, each in `tests/test_installer.py`:

- **Path and schema** — add the id to `MultiAgentTests.test_exact_paths_and_schemas` and to the expected set in `test_catalog_covers_all_documented_clients`. Build the expected path with `os.path.join` under `os.path.abspath("/p")` or `"/h"` so it holds on all three CI platforms.
- **Merge** — extend `test_every_json_client_installs_and_uninstalls`, or add a schema-specific case modelled on `test_codex_toml_preserves_other_tables_and_uninstalls_cleanly`, `test_zed_context_servers_shape`, or `test_continue_yaml_written_and_removed`. The test must show that unrelated user content survives and that a backup is written.
- **Uninstall** — the same tests assert that uninstall removes only the TIER entry and leaves the rest of the file byte-identical.
- **Doctor** — `DoctorTests.test_doctor_healthy_and_json` must report the client as configured, and `test_doctor_broken_config_and_stale_repo` must surface a malformed config rather than crash.

## Benchmarks

```sh
./benchmarks/fetch_corpus.sh    # optional: the five real Markdown docs used by Tracks D and I
python3 benchmarks/bench.py     # runs Tracks A-I and rewrites benchmarks/BENCH.txt
```

Exact `o200k_base` counts require `node` and `npm install` inside `benchmarks/`; without them the script prints a notice and falls back to the characters/4 estimator. Track D is skipped silently when `corpus/` is empty.

Track descriptions, methodology, and how to read the output are in [benchmarks/README.md](benchmarks/README.md). The checked-in result of the last run is [benchmarks/BENCH.txt](benchmarks/BENCH.txt).

`benchmarks/agent_ab.py` is a separate comparison (Track J): it runs the same agent workflow against a plain repository and against its TIER map and reports the tokens each turn spends. It writes to stdout and does not touch the checked-in transcript.

## Publishing a release

Tagging `v*` runs `.github/workflows/release.yml`: it verifies the tree, builds the sdist and
wheel, then publishes to PyPI through Trusted Publishing. No API token is stored.

The publish job fails with `invalid-publisher` until the project has a Trusted Publisher on PyPI.
Configure it once at <https://pypi.org/manage/account/publishing/> with:

| Field | Value |
|---|---|
| PyPI project name | `tier-ctx` |
| Owner | `000jd` |
| Repository | `tier` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

The first release may use a "pending publisher": the project does not have to exist on PyPI yet.
Until it is configured, the verify and build jobs still pass, so a red Release run with those two
green means only the publisher is missing.

## Refreshing checked-in artifacts

Four `.tier` files and one benchmark transcript are checked in. Regenerate them after changing parsers, converters, symbol extraction, repository behavior, the CLI surface, or the benchmarks:

```sh
# Rebuild the format specification and the self-index.
tier from-md docs/spec.md -o SPEC.tier
tier from-repo . -o repo.tier

# Bring the map to the current tree (twice, so the stat cache cannot mask an edit).
tier sync repo.tier
tier sync repo.tier --rehash

# Lint every checked-in document.
tier lint SPEC.tier
tier lint repo.tier
tier lint examples/payments-runbook.tier
tier lint examples/handbook.tier

# Environment, config, repo, and sync diagnostics.
tier doctor .

# Track A-I, rewriting benchmarks/BENCH.txt.
python3 benchmarks/bench.py
```

The version string moves in one step: `VERSION` in `src/tier/models.py`, `version` in `pyproject.toml` and `benchmarks/package.json`, the `%tier` header and title of `SPEC.tier`, `repo.tier`, and `examples/payments-runbook.tier`, and the version cited in `README.md` and `docs/spec.md`. `tests/test_tier.py::T.test_pyproject_and_version_metadata` asserts each of those in a single test, so a partial bump fails immediately.

**CI fails if `repo.tier` is stale.** On Linux the workflow runs `tier sync repo.tier`, then `tier sync repo.tier --rehash`, then `git diff --exit-code -- repo.tier`. Any difference fails the job with a pointer to `tier from-repo . -o repo.tier`. The same job runs `tier doctor .` and lints all four documents. Note that CI installs no extras, so a map that changes depending on whether Tree-sitter is present will fail this gate.
