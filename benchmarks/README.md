# Benchmarks

`bench.py` measures three things: **tokens** with the `o200k_base` BPE tokenizer, **retrieval hit rates** (whether the answer-bearing section is the one returned for a query), and **exact-slice coverage** (whether the recorded L3 line ranges match the source they claim to describe). It does not measure LLM answer accuracy. No model is called anywhere in the script, and no judgment is made about the text a fetch returns: a "hit" means the expected answer substring appears in the retrieved text, which is a proxy for routing quality, not for whether an agent answers well.

Claude's tokenizer is not public. Every count here is OpenAI `o200k_base` via the `gpt-tokenizer` Node package. Absolute numbers differ from what Claude will bill; relative ordering usually transfers, but verify against the Anthropic token-counting API before depending on a specific figure.

Track A-I output for the last full run is checked in as [BENCH.txt](BENCH.txt). [agent_ab.py](agent_ab.py) is a separate script for Track J and prints to stdout only — it writes no file and is not part of `BENCH.txt`.

## Running

```sh
./benchmarks/fetch_corpus.sh     # optional: five real GitHub Markdown docs -> corpus/
python3 benchmarks/bench.py      # runs Tracks A-I, rewrites benchmarks/BENCH.txt
python3 benchmarks/agent_ab.py   # runs Track J (agent A/B), prints a table, exits 0
```

Exact BPE counts require `node` on `PATH` and an `npm install` inside this directory:

```sh
cd benchmarks
npm install
```

`gpt-tokenizer` is the only dependency (`benchmarks/package.json`). `count.mjs` is the one-shot counter and `count-server.mjs` is the long-lived stdio worker that `tier/tokens.py` keeps alive for a whole session.

Without `node` or `node_modules`, the script prints a notice and every count comes from a deterministic characters/4 estimator. Numbers stay comparable among themselves but are not real BPE counts, and the run is not the one that produced `BENCH.txt`. The first line of the output always states which backend was used (`tokenizer backend: o200k` or `~chars/4 estimate!`).

`bench.py` writes `BENCH.txt` on every completed run; there is no dry-run flag. It needs no arguments and takes the repository root from its own location.

`corpus/` is optional. Track D returns silently when it is empty, and Track I then indexes only the runbook, handbook, and specification. `fetch_corpus.sh` downloads `pytorch/pytorch/CONTRIBUTING.md`, `nodejs/node/BUILDING.md`, `redis/redis/README.md`, `expressjs/express/Readme.md`, and `anthropics/claude-code/README.md`, and reports any file it could not fetch without failing.

## Workloads

Each track answers a question the previous one does not. Tracks A and B are surrogate compression workloads; C, E, and I are retrieval workloads; D is a converter stress test; F and H are repository-map workloads; G is the workflow they compose into; J is an agent-cost simulation over this repository.

### A. Uniform records

Sixty generated order records with six fields each (`id`, `customer`, `status`, `items`, `total`, `created`), built from a fixed seed so the fixture is identical on every run. The same records are serialized seven ways: pretty JSON, compact JSON, YAML, CSV, a Markdown table, a TOON-style tabular block, and a TIER table. The measure is total tokens per format, plus the delta against pretty JSON.

Two caveats are printed with the table and matter for reading it. The TOON-style fixture is a local re-implementation of the documented tabular syntax, not the official library. The YAML fixture uses PyYAML when it is installed and otherwise a built-in emitter that is byte-identical to PyYAML on this fixture only.

### B. Nested configuration

A four-level payments-service config (roughly 25 leaves: limits, processor with mutual TLS, database with a replica, Redis with a cluster, feature flags) serialized as pretty JSON, compact JSON, YAML, TOON-style, and TIER key/value. The measure is total tokens and the delta against pretty JSON. The fixture also asserts that the TIER key/value block round-trips through `tier.nodes` — every scalar is compared as a string — so a cheaper encoding that loses structure fails the run rather than the reader's trust.

### C. Payments runbook retrieval

`examples/payments-runbook.md` converted to TIER, then interrogated with eight hand-written questions ("how do I roll back a bad deploy", "who do I page at night if nobody acks", …), each paired with an expected answer string. The measure is whether the answer appears in the top-1 fetched section and in the top-2 fetch set, the token cost of each fetch, and the cumulative session cost: TIER's L0 index loaded once plus deduplicated fetches per question, compared against loading the whole Markdown document once. Session deduplication is exercised through two `tier.Session` objects, one for the top-1 path and one for the top-2 path. The printed check confirms that L0 plus top-1 plus dedup stays below the full Markdown document at every question.

### D. Real-world Markdown

The five documents from `corpus/`, each converted from GitHub Markdown to TIER. Per file: Markdown tokens, TIER tokens and the delta, L0 index tokens and the L0/Markdown ratio, section count, lint error count, round-trip stability (`dumps(parse(x)) == x`), and word recall — the share of original alphanumeric words still present after conversion. Emphasis markers are stripped by design, so recall below 100% is expected and 99-100% is the working range. Auto-abstracts here are heuristic; hand-written or model-written abstracts route better. Lint runs with `max_section_tokens=10**9` so that section-size warnings do not mask real errors.

### E. Paraphrase and compound-identifier retrieval

Ten queries against the payments runbook that do not share literal prefixes with their target section ids: paraphrases ("how to undo a broken release"), compound identifiers ("postgres max_connections saturation"), and symptom descriptions ("ledger write failure stop deploys"). Each query runs through `find(..., legacy=True)` and through hybrid `find(...)`, and the track reports hit@1 for both. This is the comparison that motivated the hybrid retriever: identifier splitting, synonyms, trigram fallback, and a table-field boost.

### F. Codebase to TIER graph index

A self-index of `src/tier`. The track collects every source file, extracts symbols directly to build the expected set, then builds the map with `from_repo` and measures raw source tokens against full map tokens, L0 index tokens, and an `L0 + parser.py + 1-hop` fetch. It also counts symbols present in the map against symbols the extractors found, counts internal `>` edges, lints the result, and checks round-trip stability. The printed checks are the contract: L0 plus one hop must be at least 70% smaller than raw source, and every exported symbol must survive.

### G. End-to-end four-layer workflow

The sequence an agent actually performs, priced step by step: (1) L0 repository index, (2) a hybrid `find` for one module, (3) L2 signatures plus one-hop dependencies, (4) L3 slices for `parse`, `from_md`, and `find`, and (5) impact plus an incremental sync. The track verifies that the three L3 slices are exact — the header and the full source snippet for the recorded line range both appear — that the impact result contains all ten expected dependent modules, and that a sync after appending a function to a copied `parser.py` detects the change, picks up the new symbol, and invalidates the marked slice in a live session. The sum is compared against raw source and against the full map.

### H. Polyglot repository map

A written-to-a-tempdir fixture with real Python, TypeScript, Go, and Rust modules plus 24 generated service modules, six per language: dataclasses and decorated async methods in Python, interfaces and arrow-function consts in TypeScript, structs and receiver methods in Go, traits, impls, and lifetimes in Rust. Each expected symbol carries a marker string that must appear inside its recorded line range, an optional decorator requirement, an optional exclusion, and an optional bodyless flag — so a range that is off by a line or that swallows the next definition fails. The track reports symbol recall, range correctness, cross-file `>` edge recall against ten expected pairs, the number of symbol-level call edges, and three token totals: L0 plus full L2 plus seven L3 slices, L0 plus the target modules' L2 with the full `|calls` tables, and L0 plus the target modules' L2 with those tables narrowed to one hop.

**The surgical measurement now uses the 1-hop-filtered `|calls` render.** The target-module L2 body is rendered through `render_lines(..., calls_filter=...)`, where the filter is built from the sliced symbols plus their immediate callers and callees (`tier.context._calls_filter_for(doc, sliced_keys, "auto")`). A module's `|calls` table is the single largest block it carries, and most of it is irrelevant to the symbols actually being read, so narrowing it to one hop is what keeps the surgical figure honest about what an agent needs rather than about what the map stores. Filtering only ever removes rows from `|calls`; every other line and table in the body is untouched, and the on-disk `repo.tier` keeps the whole graph — `tier impact` still reads every edge.

Because that narrowing changes the number, **the full unfiltered table is reported alongside it**: the row `L0 + target L2 (unfiltered calls) + 7 L3 slices` sits directly above `L0 + target L2 (1-hop calls) + 7 L3 slices` in the same Track H table, measured against the same baseline, so the difference between the two rows is exactly the cost of the call edges the filter dropped. The printed check gates on the filtered figure (`surgical L0+L2+L3 under 35% of raw`); the unfiltered row is there so the saving cannot be read as coming from anywhere else.

### I. Blind retrieval over merged corpora

A merged corpus, assembled by joining `examples/payments-runbook.tier`, `examples/handbook.tier`, and `SPEC.tier` (minus its benchmark and sources sections) with the five `corpus/*.md` documents converted at run time. The section count is computed rather than fixed: the checked-in run has 198, and it drops when external documents are missing. Section ids are prefixed per source (`rb-`, `hb-`, `spec-`, `express_`, …) and intra-document `>` references are rewritten to the prefixed ids, so the graph stays coherent across the merge and no query can be answered from a filename. Fifteen queries target all four sources, three of them accepting either of two valid sections rather than a single id. The measure is hit@1 and hit@2 against an accepted-id set, whether the answer substring appears in the top-1 fetch, and the token cost of the top-1 fetch versus loading the whole corpus.

### J. Agent A/B on this repository

`agent_ab.py` is a separate script, not part of `bench.py` and not part of `BENCH.txt`. It asks a different question from every track above: not *how small can this text get* but *how many tokens does an agent burn, and in how many turns, to localize a symbol in this repository*. The subject is `src/tier` — the same package Tracks F and G index — so the fixture is live code that moves with the project, not a generated one.

Three realistic tasks, each naming real symbols in this repo:

| Task | Symbols that should come back |
|---|---|
| "how does the repository cache resolve git worktrees" | `cache_path`, `resolve_doc`, `ensure_doc`, `_git_dir`, `cache_kind` |
| "where is the context budget packing done" | `build_context`, `_render_section`, `_count` |
| "what calls the symbol table renderer" | `_symbol_table`, `_repo_section` |

Each task is priced three ways:

- **A. Baseline (2 turns).** A naive grep for the query's content words (`grep -l` semantics) surfaces a set of files; the agent then reads every one of them in full. The token count is the concatenated full text of those files — no slicing, no relevance ranking. This is the "just read the codebase" arm, and its cost is dominated by file count, so it grows with the repository while the other two arms grow with the answer.
- **B. Four-turn TIER (4 turns).** The sequence an agent run through the MCP tools actually performs: `tier_index` → `tier_find` → `tier_impact` → `tier_slice`. The reported number is the concatenation of the real text each step returns. The `tier_slice` step is given the *correct* symbol list, so arm B is the best case for multi-turn use — a real agent would sometimes pick wrong and pay for a second slice round.
- **C. One-turn TIER (1 turn).** A single `tier.build_context(..., budget_tokens=1500)` call: resolve, route, match symbols, pack L2/L3/impact, stop at the budget. The reported number is `res["tokens"]`, the tokenizer count of the exact text handed back.

What the columns mean:

- **tokens** — context-window cost of what the arm puts in front of the model. This is the whole point: it is paid on every task, and it is what crowds out the agent's working context.
- **turns** — model round-trips needed to get those tokens. A turn is an extra tool call/response pair, which costs latency and a chance to derail, none of which the token count captures. A is 2 (grep, then read), B is 4 (one per tool), C is 1.
- **recall** — of the symbols the task is really about, how many are named in the returned text, matched on identifier boundaries (so `_count` is not credited for `count_tokens`). Token savings are worthless if the symbol never shows up, so a low token count with low recall is a regression, not a win.

The summary line reports the mean token reduction of C against A across the three tasks, plus the exact-token guarantee: `build_context` returns the tokenizer count of the text it actually produces and that text is `<= budget_tokens` by construction, because blocks that do not fit are dropped rather than truncated mid-slice. So C's cost is bounded before the call is made, which is a different property from B's, where the cost is discovered after the fourth tool returns.

**This is a simulation of agent behaviour, not a live LLM measurement.** No model is called, no tool is dispatched over MCP, no agent loop runs. The arms are the token counts of the text each strategy *would* place in the context window, computed by calling the same library functions the real tools call. Turn counts are design parameters read off the protocol (`grep`+`read` = 2, four tools = 4, `tier_context` = 1), not measured latencies. What the numbers do establish is the cost per strategy on fixed inputs and a fixed tokenizer, which is the part that does not depend on a model version. What they do not establish is whether an agent given arm C's text answers better than an agent given arm A's — recall is a proxy for "the answer is present", never for "the agent got it right".

The track exits 0 unconditionally. It needs no arguments, writes no files, and puts the repository cache it triggers in a temporary directory (`TIER_CACHE_DIR`) rather than inside the checkout. It works with or without `benchmarks/node_modules`: without Node, `tier.count_tokens` falls back to the chars/4 estimator and the first line says `estimate`, which keeps the run comparable to itself but not to an `o200k` run.

## Methodology

- **No model calls.** Every number is a tokenizer count or a string match. Nothing is generated, so nothing here can drift with a model version.
- **Fixed fixtures.** Track A seeds `random` at 7. Track H writes its fixture from literal strings. Tracks C, E, and I read from checked-in documents. Track D and the corpus half of Track I read downloaded files, which is the one input that can change under you. Track J reads live `src/tier`, so its absolute numbers track the current size of the package and are only comparable between runs of the same commit.
- **Synthetic beside real.** Tracks A and B are synthetic and exist to price format choices under controlled density. Tracks D and I use real documentation because synthetic text flatters any format that removes structural noise. Track J uses the real package for the same reason: a generated fixture would not have this project's actual call graph.
- **Means, not medians.** Aggregates are arithmetic means: `sum(costs) / len(costs)` for per-question fetch cost, and a running cumulative sum for the session table. Nothing here is timing-sensitive, so a mean over a small fixed workload is stable and easier to audit than a median. No track repeats a measurement.
- **One backend, declared in the output.** `tier.count_tokens` returns `(counts, backend)`; the script prints the backend on the first line. If that line says `estimate`, the counts are characters/4 and are not comparable to a `BENCH.txt` produced with `o200k`.
- **Checks are printed, not asserted.** Tracks C, F, G, H, and I print `Y`/`N` verdicts next to their claims rather than raising, so one regression does not hide the rest of the run. `tests/test_tier.py` re-runs Tracks H and I as assertions, which is where they actually gate. Track B's key/value round-trip is a real `assert`, because a broken fixture would invalidate the whole track.

Limits worth stating plainly:

- **Small fixtures.** Track A has 60 rows and Track H has 36 files. Compressibility and symbol recall measured at this size do not extrapolate linearly to a large repository.
- **Heuristic extraction.** Python symbols come from the `ast` module and are reliable. JavaScript, TypeScript, Go, and Rust go through regex and brace matching and are heuristic, not a parser; a construct the regexes do not model is silently absent from the map and therefore from the symbol recall figure. The optional `[treesitter]` extra replaces this path with real grammars for the languages it covers.
- **Tokenizer mismatch.** `o200k` is not Claude's tokenizer. Treat every percentage as a direction, not a target.
- **Retrieval is not comprehension.** "Answer string present" does not mean an agent would answer correctly from that section. It means the section was fetched. Track J's `recall` column has the same limit: a symbol named in the text is not a symbol the agent understood.
- **TOON-style is a re-implementation.** The Track A and B figures for that column are against a local encoder written from the published description, not against the official package.
- **Track J is a simulation.** Its arms are token counts for what each strategy would place in a context window, not a measured agent trajectory. No model is called and no turn count is timed; see the Track J section above.

## Reading BENCH.txt

The file is a plain transcript: everything the script printed, in order, one line per `say()` call. Format conventions:

- `N. Title` starts a track.
- A block of two or more lines separated by a dashes row is a table. The first row is the header.
- Lines beginning with three spaces are notes, checks, or summaries belonging to the track above.
- Deliberately blank lines separate tracks.

Table columns by track:

| Track | Columns |
|---|---|
| A, B | `format`, `tokens`, `vs JSON pretty` (signed percent) |
| C, first table | `question`, `top-1 section`, `hit@1`, `tok@1`, `hit@2`, `tok@2` |
| C, second table | `questions` (cumulative count), `markdown`, `tier top-1`, `delta`, `tier top-2`, `delta` |
| D | `file`, `md tok`, `tier tok`, `delta`, `L0 tok`, `L0/md`, `secs`, `lint err`, `stable`, `word recall` |
| E | `query`, `v0.2 top-1`, `hit`, `v0.3 top-1`, `hit` |
| F | `measure`, `sections`, `tokens`, `vs raw source` |
| G | `step`, `tokens` |
| H | `measure`, `sections`, `tokens`, `vs raw` |
| I | `query`, `top-1 section`, `hit@1`, `tok@1`, `hit@2`, `tok@2` |

`Y`/`N` in a `hit` or `stable` column is a boolean result, not a count. A `-` in the comparison column marks the baseline row itself — the raw-source totals in Tracks F and H, which every other row in that table is measured against. In Track F, `vs raw source` is phrased as "N% fewer" so the direction cannot be misread; in Tracks H and I the same column drops the word and stays a plain percentage reduction.

Track J is not in `BENCH.txt`. `agent_ab.py` prints the same table shape to stdout and exits without writing a file, so its table is never part of the checked-in transcript:

| Track J | Columns |
|---|---|
| J | `task`, `arm`, `tokens`, `turns`, `recall` |

## Adding a benchmark

Write a `bench_<name>()` function and call it from the `__main__` block after the existing tracks. Use `say()` for every line you want in `BENCH.txt` — it prints and appends to the module-level `OUT` list, which is joined into the file at the end of the run — and `table(rows, headers)` for tabular output, which computes column widths from the data and header. Tokens come from `toks([...])`, a thin wrapper over `tier.count_tokens` that returns counts only.

Conventions the existing tracks follow and a new one should keep:

- Print a numbered title first, and a trailing blank `say()` so the next track is visually separated.
- Print the fixture's parameters (row counts, levels, file counts) in or directly under the title, so a reader can tell what the number was measured against.
- If the track makes a claim, print the claim as an explicit `check: ... Y/N` line rather than embedding it in prose.
- Use a temporary directory for any fixture you write, and check the residue you expect (missing symbols, missing slices) rather than only the successes.
- Add a corresponding assertion to `tests/` if the track is meant to gate; a `Y/N` line is a report, not a test.
- Regenerate `BENCH.txt` and commit it with the change. The numbers in the README and in this directory's history refer to specific runs.

A track that needs its own harness rather than another column belongs in its own script, the way Track J does. `agent_ab.py` reuses `tier.count_tokens` and the same fixed-width table shape, keeps everything in the stdlib, prints the tokenizer backend on the first line, and exits 0 whether or not `node_modules` is present — no `OUT` list, no file written, so it cannot corrupt `BENCH.txt`.
