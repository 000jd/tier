#!/usr/bin/env python3
"""Track J: agent A/B on this repository - how much does an agent read, and in how few turns.

Three realistic localization tasks are run through three arms:

  A  Baseline            grep for the query terms, then read every file it surfaced
                         (full text, no slicing). Round-trips: 2 (grep + read).
  B  Four-turn TIER      tier_index -> tier_find -> tier_impact -> tier_slice.
                         Round-trips: 4. Each step's actual return text is concatenated.
  C  One-turn TIER       tier.build_context(..., budget_tokens=1500).
                         Round-trips: 1. Exact token count of the text returned.

The measured quantity is TOKENS PUT IN THE CONTEXT WINDOW, plus the number of
model round-trips needed to get them, plus symbol recall: did the text that came
back actually name the symbols the task is about? Nothing here calls a model. This
is a simulation of agent behaviour priced with the tokenizer, not a live LLM
measurement - see benchmarks/README.md, "Track J".

Tokenizer: same convention as bench.py - `tier.count_tokens` (o200k_base BPE via
node when benchmarks/node_modules is present, else a deterministic chars/4
estimator). The backend is printed on the first line; run `npm install` in
benchmarks/ for exact counts. No third-party packages are imported here.

Usage:  python3 benchmarks/agent_ab.py
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src", "tier")        # the package directory, same target bench.py uses
_SRC_PATH = os.path.join(ROOT, "src")          # importable src/ layout for a fresh clone
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

# build_context persists the map through the repository cache. Point that at a
# throwaway directory so a benchmark run never writes inside the checkout.
_CACHE_DIR = tempfile.mkdtemp(prefix="tier-agentab-")
os.environ.setdefault("TIER_CACHE_DIR", _CACHE_DIR)

import tier  # noqa: E402  (import order is deliberate: TIER_CACHE_DIR first)

try:
    from tier.context import build_context
except ImportError:          # pragma: no cover - build_context ships from 1.2.0
    build_context = getattr(tier, "build_context", None)


# ------------------------------------------------------------------ fixtures
# Content-word filter for the naive grep arm. Function words are dropped because
# `grep -l "the"` over a Python tree is not what a baseline agent does; content
# words such as "calls" and "renderer" are kept.
_STOP = frozenset("""
a an and are as at be been but by can did do does doing done for from had has have how i if in into
is it its just me my of on or our so than that the their them then there these they this to too was
we were what when where which who whom why will with would you your
""".split())

# Every expected symbol below was verified to exist in src/tier when this track
# was written; if one is renamed the arm simply reports lower recall rather than
# failing, so the track stays runnable while the package moves.
TASKS = [
    {
        "query": "how does the repository cache resolve git worktrees",
        "expected": ["cache_path", "resolve_doc", "ensure_doc", "_git_dir", "cache_kind"],
    },
    {
        "query": "where is the context budget packing done",
        "expected": ["build_context", "_render_section", "_count"],
    },
    {
        "query": "what calls the symbol table renderer",
        "expected": ["_symbol_table", "_repo_section"],
    },
]

BUDGET = 1500


def say(s=""):
    print(s)


def toks(strings):
    """Token counts for a list of strings, using the shared bench convention."""
    return tier.count_tokens(strings)[0]


def table(headers, rows):
    """Fixed-width table: widths come from the data and the header."""
    widths = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
    say("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
    say("  ".join("-" * w for w in widths))
    for r in rows:
        say("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def terms(query):
    """Naive grep terms: content words of >= 3 chars, lowercased."""
    return [w for w in re.findall(r"[a-z0-9_]+", query.lower())
            if len(w) >= 3 and w not in _STOP]


def recall(text, expected):
    """How many expected symbols are named in `text`.

    Identifier-boundary match, so `_count` is not credited for `count_tokens`.
    """
    found = 0
    for name in expected:
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"
        if re.search(pattern, text):
            found += 1
    return found, len(expected)


def symbol_home(doc):
    """Map bare symbol name -> the section id (module) that declares it."""
    home = {}
    for sec in doc.sections:
        for row in tier._symbol_rows(sec):
            home.setdefault(row["name"], sec.id)
    return home


# ------------------------------------------------------------------ the arms
def arm_baseline(query):
    """A: grep surfaces files, agent reads all of them in full. 2 round-trips."""
    wanted = terms(query)
    picked = []
    for rel, full, _ext in tier.repo_source_files(SRC):
        text = read(full)
        low = text.lower()
        if any(t in low for t in wanted):
            picked.append((rel, text))
    body = "\n".join(text for _rel, text in picked)
    return {
        "text": body,
        "tokens": toks([body])[0] if body else 0,
        "turns": 2,
        "files": len(picked),
    }


def arm_four_turn(doc, query, expected):
    """B: tier_index -> tier_find -> tier_impact -> tier_slice. 4 round-trips."""
    home = symbol_home(doc)
    parts = []

    # 1. tier_index - L0 + L1 for the doc: summary, rules, one line per section.
    parts.append(tier.index(doc))

    # 2. tier_find - the MCP tool renders one "@id score abstract" line per hit.
    hits = tier.find(doc, query, 3)
    parts.append("\n".join(f"@{i} {s} {ab}" for i, s, ab in hits) or "no matches")
    target = hits[0][0] if hits else ""

    # 3. tier_impact - render_impact(impact(...)) on the routed module, widened to
    #    the first expected symbol that lives there so the call edges appear.
    impact_text = ""
    if target:
        sym = next((s for s in expected if home.get(s) == target), "")
        try:
            res = tier.impact(doc, f"{target}#{sym}" if sym else target, root=SRC)
            impact_text = tier.render_impact(res)
        except (OSError, ValueError):
            impact_text = ""
    parts.append(impact_text)

    # 4. tier_slice - exact source lines for the symbols the task is about. This is
    #    the best case for the arm: it assumes the agent already picked the right
    #    symbols, so its token count is a floor, not an average.
    slices = []
    for name in expected:
        module = home.get(name)
        if not module:
            continue
        try:
            text, missing = tier.slice_symbols(doc, module, [name], root=SRC)
        except (OSError, ValueError):
            continue
        if text and not missing:
            slices.append(text)
    parts.append("\n\n".join(slices))

    body = "\n".join(p for p in parts if p)
    return {"text": body, "tokens": toks([body])[0] if body else 0, "turns": 4}


def arm_one_turn(query):
    """C: a single build_context call. 1 round-trip, exact token count."""
    if build_context is None:
        return {"text": "", "tokens": 0, "turns": 1, "ok": False}
    # A fresh Session per task, so dedup stubs from an earlier query are not
    # mistaken for content: each task is priced as the first call of a session.
    res = build_context(
        SRC, query, budget_tokens=BUDGET, session=tier.Session(), session_key="agent-ab"
    )
    return {"text": res["text"], "tokens": res["tokens"], "turns": 1, "ok": True,
            "budget": res["budget"], "sections": res["sections"], "slices": res["slices"]}


# ------------------------------------------------------------------ the track
def main():
    backend = tier.count_tokens(["x"])[1]
    say(f"Track J - agent A/B on {os.path.relpath(SRC, ROOT)}"
        f" - tokenizer backend: {backend}"
        + ("  (o200k_base BPE)" if backend == "o200k" else "  (~chars/4 estimate!)"))
    say("simulation of agent behaviour priced with the tokenizer - no model is called.")
    say()

    doc = tier.from_repo(SRC)
    rows, base, four, one = [], [], [], []
    one_recalls, a_files = [], []
    c_ok_all = True

    for task in TASKS:
        q, expected = task["query"], task["expected"]
        a = arm_baseline(q)
        b = arm_four_turn(doc, q, expected)
        c = arm_one_turn(q)
        c_ok_all = c_ok_all and c["ok"]

        ra = recall(a["text"], expected)
        rb = recall(b["text"], expected)
        rc = recall(c["text"], expected)

        rows.append((q, "A baseline (grep+read)", a["tokens"], a["turns"], f"{ra[0]}/{ra[1]}"))
        rows.append(("", "B four-turn TIER", b["tokens"], b["turns"], f"{rb[0]}/{rb[1]}"))
        rows.append(("", "C one-turn TIER", c["tokens"], c["turns"], f"{rc[0]}/{rc[1]}"))

        base.append(a["tokens"])
        four.append(b["tokens"])
        one.append(c["tokens"])
        one_recalls.append(rc)
        a_files.append(a["files"])

    table(["task", "arm", "tokens", "turns", "recall"], rows)
    say()
    say(f"   recall = expected symbols named in the returned text / expected"
        f" ({sum(len(t['expected']) for t in TASKS)} symbols across {len(TASKS)} tasks).")
    say(f"   A read {sum(a_files)} file(s) in full across the three tasks"
        f" ({', '.join(str(n) for n in a_files)} per task); that count, not the query,"
        f" is what sets arm A's cost.")
    say("   B slices the symbols it needs; C asks once for a packed, budgeted context.")
    say(f"   C budget {BUDGET} tokens, honoured by construction (max observed"
        f" {max(one) if one else 0}); blocks that do not fit are dropped, never truncated"
        f" mid-slice.")
    missed = [t["query"] for t, c in zip(TASKS, one_recalls) if c[0] < c[1]]
    if missed:
        say(f"   C recall gaps: {len(missed)}/{len(TASKS)} task(s) - routing miss at"
            f" {BUDGET} tokens, not a truncation. B raises recall by naming the symbols"
            f" explicitly, which is its whole advantage over one-turn packing.")
    say()

    if base and one and c_ok_all:
        reductions = [100.0 * (a - c) / a for a, c in zip(base, one) if a]
        mean_red = sum(reductions) / len(reductions)
        say(f"SUMMARY  mean token reduction, C (1 turn) vs A (2 turns): {mean_red:.1f}%"
            f"  [{min(reductions):.1f}%..{max(reductions):.1f}% per task]")
        say(f"         tokens in: A {sum(base)} -> B {sum(four)} -> C {sum(one)}"
            f" across {len(TASKS)} tasks; turns: A 2 -> B 4 -> C 1")
        say(f"         exact-token guarantee: C returns the tokenizer count of the text it"
            f" actually hands back, and that text is <= {BUDGET} tokens by construction"
            f" (max {max(one)} <= {BUDGET}: {'Y' if max(one) <= BUDGET else 'N'});"
            f" backend {backend}.")
    else:
        say("SUMMARY  arm C unavailable (tier.context.build_context not importable);"
            " A and B were measured, the reduction is not reported.")
    say()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_CACHE_DIR, ignore_errors=True)
    sys.exit(code)
