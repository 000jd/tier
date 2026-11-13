#!/usr/bin/env python3
"""Token benchmarks for TIER vs Markdown / JSON / YAML / CSV / TOON-style.

Tokenizer: o200k_base BPE (OpenAI) via node `gpt-tokenizer` when available, else ~chars/4.
NOTE: Claude's own tokenizer is not public. Absolute counts will differ; relative ordering
usually transfers but verify with the Anthropic token-counting API before betting on it.
Measures TOKENS and RETRIEVAL HITS only - not LLM answer accuracy.
"""
import csv
import glob
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src", "tier")   # the package directory, what Tracks F/G index
_SRC_PATH = os.path.join(ROOT, "src")     # importable src/ layout for a fresh clone
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

import tier

try:
    import yaml
except ImportError:
    yaml = None


_NEEDS_QUOTE = re.compile(
    r"""^(?:|true|false|null|yes|no|on|off|~|[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?|"""
    r"""\d{4}-\d{1,2}-\d{1,2}(?:[Tt ].*)?)$""",
    re.I,
)


def _yaml_scalar(value) -> str:
    """Format one scalar the way PyYAML's default flow-block emitter would."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if _NEEDS_QUOTE.match(text):
        return "'" + text.replace("'", "''") + "'"
    return text


def _yaml_lines(value, indent: int = 0) -> list:
    pad = "  " * indent
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out += _yaml_lines(v, indent + 1)
            elif isinstance(v, (dict, list)):
                empty = "{}" if isinstance(v, dict) else "[]"
                out.append(f"{pad}{k}: {empty}")
            else:
                out.append(f"{pad}{k}: {_yaml_scalar(v)}")
        return out
    if isinstance(value, list):
        seq_pad = pad[:-2] if indent and pad.endswith("  ") else pad
        for item in value:
            if isinstance(item, dict) and item:
                inner = seq_pad + "  "
                first = True
                for k, v in item.items():
                    lead = f"{seq_pad}- " if first else inner
                    first = False
                    if isinstance(v, (dict, list)) and v:
                        out.append(f"{lead}{k}:")
                        out += _yaml_lines(v, indent + 2)
                    elif isinstance(v, (dict, list)):
                        empty = "{}" if isinstance(v, dict) else "[]"
                        out.append(f"{lead}{k}: {empty}")
                    else:
                        out.append(f"{lead}{k}: {_yaml_scalar(v)}")
            elif isinstance(item, list):
                out.append(f"{seq_pad}-")
                out += _yaml_lines(item, indent + 1)
            else:
                out.append(f"{seq_pad}- {_yaml_scalar(item)}")
        return out
    out.append(f"{pad}{_yaml_scalar(value)}")
    return out


def _yaml_dump(value) -> str:
    if yaml is not None:
        return yaml.safe_dump(value, sort_keys=False)
    return "\n".join(_yaml_lines(value)) + "\n"

OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def toks(strings):
    c, backend = tier.count_tokens(strings)
    return c


def table(rows, headers):
    w = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
    say("  ".join(str(h).ljust(w[i]) for i, h in enumerate(headers)))
    say("  ".join("-" * x for x in w))
    for r in rows:
        say("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


# ---------------------------------------------------------------- A: records
def bench_records():
    random.seed(7)
    names = ["Ana Silva", "Ben Okoye", "Chen Wei", "Dara Patel", "Eli Novak", "Fatima Khan", "Gus Berg", "Hana Sato"]
    status = ["paid", "pending", "shipped", "refunded"]
    recs = [
        {
            "id": 1000 + i,
            "customer": random.choice(names),
            "status": random.choice(status),
            "items": random.randint(1, 9),
            "total": round(random.uniform(5, 400), 2),
            "created": f"2026-{random.randint(1, 11):02d}-{random.randint(1, 28):02d}",
        }
        for i in range(60)
    ]
    cols = list(recs[0])
    n = len(recs)

    def rows_csv(sep_quote=True):
        b = io.StringIO()
        w = csv.writer(b, lineterminator="\n")
        for r in recs:
            w.writerow([r[c] for c in cols])
        return b.getvalue()

    f = {}
    f["JSON (pretty)"] = json.dumps({"orders": recs}, indent=2)
    f["JSON (compact)"] = json.dumps({"orders": recs}, separators=(",", ":"))
    f["YAML"] = _yaml_dump({"orders": recs})
    f["CSV"] = ",".join(cols) + "\n" + rows_csv()
    f["Markdown table"] = (
        "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
        + "\n".join("| " + " | ".join(str(r[c]) for c in cols) + " |" for r in recs)
    )
    f["TOON-style*"] = f"orders[{n}]{{{','.join(cols)}}}:\n" + "\n".join("  " + l for l in rows_csv().splitlines())
    f["TIER table"] = f"|orders[{n}]{{{','.join(cols)}}}\n" + rows_csv().rstrip("\n")
    names_ = list(f)
    counts = toks([f[k] for k in names_])
    base = counts[0]
    say("A. 60 uniform records x 6 fields")
    table(
        [(k, c, f"{100 * (c - base) / base:+.0f}%") for k, c in zip(names_, counts)],
        ["format", "tokens", "vs JSON pretty"],
    )
    say("   * TOON-style = my re-implementation of the documented tabular syntax, not the official library.")
    say()


# ---------------------------------------------------------------- B: nested config
def bench_nested():
    cfg = {
        "service": {
            "name": "payments-api",
            "env": "prod",
            "limits": {"cpu": "2", "memory": "4Gi", "replicas": {"min": 8, "max": 24}},
            "processor": {"timeout_ms": 3000, "retries": 2, "mtls": {"cert": "/etc/certs/processor.pem", "verify": "strict"}},
            "database": {"pool_size": 40, "statement_timeout": "5s", "replica": {"host": "db-ro.internal", "port": 5432}},
            "redis": {"idempotency_ttl": "24h", "cluster": {"nodes": 6, "tls": "on"}},
            "flags": {"fallback_processor": "off", "reject_without_idempotency": "off"},
        }
    }

    def toon(d, ind=0):
        out = []
        for k, v in d.items():
            if isinstance(v, dict):
                out.append("  " * ind + f"{k}:")
                out += toon(v, ind + 1)
            else:
                out.append("  " * ind + f"{k}: {v}")
        return out

    def tier_kv(d, ind=0, top=True):
        out = []
        for k, v in d.items():
            pre = ("= " if (top and ind == 0) else "  " * ind)
            if isinstance(v, dict):
                out.append(f"{pre}{k}")
                out += tier_kv(v, ind + 1, False)
            else:
                out.append(f"{pre}{k} {v}")
        return out

    f = {"JSON (pretty)": json.dumps(cfg, indent=2), "JSON (compact)": json.dumps(cfg, separators=(",", ":"))}
    f["YAML"] = _yaml_dump(cfg)
    f["TOON-style*"] = "\n".join(toon(cfg))
    f["TIER kv"] = "\n".join(tier_kv(cfg))
    # sanity: our kv parser must round-trip the structure (all scalars become strings)
    def stringify(d):
        return {k: stringify(v) if isinstance(v, dict) else str(v) for k, v in d.items()}

    sec = tier.Section("x", lines=f["TIER kv"].split("\n"))
    parsed = tier.nodes(sec)[0]["data"]
    assert parsed == stringify(cfg), "TIER kv round-trip failed"
    names_ = list(f)
    counts = toks([f[k] for k in names_])
    say("B. nested config (4 levels, ~25 leaves)")
    table([(k, c, f"{100 * (c - counts[0]) / counts[0]:+.0f}%") for k, c in zip(names_, counts)], ["format", "tokens", "vs JSON pretty"])
    say()


# ---------------------------------------------------------------- C: runbook retrieval
QUERIES = [
    ("how do I roll back a bad deploy", "deployctl rollback"),
    ("who do I page at night if nobody acks", "payments-secondary"),
    ("what is the connection pool size per replica", "40 connections"),
    ("can I deploy on friday", "Fridays after 14:00 UTC"),
    ("what is the HighLatencyP99 alert threshold", "800 ms"),
    ("duplicate charges what do I do", "payments.reject_without_idempotency"),
    ("what must I never log", "card numbers"),
    ("how many replicas in prod-eu", "payments.eu.internal,8"),
]


def bench_runbook():
    md = open(os.path.join(ROOT, "examples/payments-runbook.md"), encoding="utf-8").read()
    doc = tier.from_md(md)
    full_tier = tier.dumps(doc)
    idx = tier.index(doc)
    md_t, tier_t, idx_t = toks([md, full_tier, idx])
    say("C. hand-written payments runbook (Markdown -> TIER via converter)")
    say(f"   Markdown full load : {md_t} tokens")
    say(f"   TIER full load     : {tier_t} tokens  ({100 * (tier_t - md_t) / md_t:+.0f}% vs Markdown)")
    say(f"   TIER L0 index only : {idx_t} tokens  ({100 * idx_t / md_t:.0f}% of Markdown)")
    say()
    rows, hit1, hit2, c1, c2 = [], 0, 0, [], []
    ded1, ded2 = [], []
    s1, s2 = tier.Session(), tier.Session()
    for q, ans in QUERIES:
        hits = tier.find(doc, q, 2)
        t1 = tier.get(doc, [h[0] for h in hits[:1]])[0]
        t2 = tier.get(doc, [h[0] for h in hits[:2]])[0]
        a1, a2 = ans.lower() in t1.lower(), ans.lower() in t2.lower()
        n1, n2 = toks([t1, t2])
        hit1 += a1
        hit2 += a2
        c1.append(n1)
        c2.append(n2)
        d1, _ = s1.get(doc, [h[0] for h in hits[:1]], key="runbook-top1")
        d2, _ = s2.get(doc, [h[0] for h in hits[:2]], key="runbook-top2")
        ded1.append(toks([d1])[0])
        ded2.append(toks([d2])[0])
        rows.append((q[:42], hits[0][0] if hits else "-", "Y" if a1 else "N", n1, "Y" if a2 else "N", n2))
    table(rows, ["question", "top-1 section", "hit@1", "tok@1", "hit@2", "tok@2"])
    say(f"   retrieval hit@1 {hit1}/{len(QUERIES)}, hit@2 {hit2}/{len(QUERIES)}  (answer string present in fetched text)")
    mean1, mean2 = sum(c1) / len(c1), sum(c2) / len(c2)
    say(f"   per-question fetch: mean {mean1:.0f} tok (top-1), {mean2:.0f} tok (top-2) vs {md_t} for the whole Markdown doc")
    # session cost: MD loaded once vs TIER index once + deduplicated fetches
    say()
    say("   cumulative session cost (tokens in context), Markdown loaded once vs TIER L0 + fetch per question:")
    srows = []
    for q in range(1, len(QUERIES) + 1):
        t1c = round(idx_t + sum(ded1[:q]))
        t2c = round(idx_t + sum(ded2[:q]))
        srows.append((q, md_t, t1c, f"{100 * (t1c - md_t) / md_t:+.0f}%", t2c, f"{100 * (t2c - md_t) / md_t:+.0f}%"))
    table(srows, ["questions", "markdown", "tier top-1", "delta", "tier top-2", "delta"])
    saved1, saved2 = sum(c1) - sum(ded1), sum(c2) - sum(ded2)
    say(f"   session dedup avoided re-sending {saved1} tokens (top-1) and {saved2} tokens (top-2) over the session.")
    under = all(idx_t + sum(ded1[:q]) < md_t for q in range(1, len(QUERIES) + 1))
    say(f"   check: L0 + top-1 + dedup < full Markdown at every question: {'Y' if under else 'N'}")
    say("   top-1 + dedup stays under full Markdown for every question on this runbook; the margin grows with larger docs.")
    say()


# ---------------------------------------------------------------- D: real-world markdown
def words(s):
    return Counter(re.findall(r"[a-z0-9]+", s.lower()))


def bench_corpus():
    files = sorted(glob.glob(os.path.join(ROOT, "corpus", "*.md")))
    if not files:
        return
    say("D. real-world Markdown from GitHub (converter stress test)")
    rows = []
    for p in files:
        md = open(p, encoding="utf-8").read()
        doc = tier.from_md(md)
        full = tier.dumps(doc)
        again = tier.dumps(tier.parse(full))  # round-trip stable?
        stable = again == full
        errs = [m for l, m in tier.lint(doc, max_section_tokens=10**9) if l == "error"]
        idx = tier.index(doc, tokens=False)
        md_t, full_t, idx_t = toks([md, full, idx])
        a, b = words(md), words(full)
        recall = sum(min(a[w], b[w]) for w in a) / max(1, sum(a.values()))
        rows.append(
            (os.path.basename(p)[:24], md_t, full_t, f"{100 * (full_t - md_t) / md_t:+.0f}%", idx_t, f"{100 * idx_t / md_t:.0f}%", len(doc.sections), len(errs), "Y" if stable else "N", f"{100 * recall:.1f}%")
        )
    table(rows, ["file", "md tok", "tier tok", "delta", "L0 tok", "L0/md", "secs", "lint err", "stable", "word recall"])
    say("   word recall = share of original alphanumeric words still present (emphasis markers are stripped by design).")
    say("   Auto-abstracts here are heuristics (prose ledes plus synthesized rule, key, command and topic summaries).")
    say("   Hand or LLM-written abstracts will route better still.")
    say()


# ---------------------------------------------------------------- E: paraphrase retrieval
PARAPHRASE_QUERIES = [
    ("how to undo a broken release", "rolling-back"),
    ("double billing customer refund", "duplicate-charges"),
    ("HighLatencyP99", "alerts"),
    ("nighttime paging escalation", "on-call-escalation"),
    ("postgres max_connections saturation", "database-connection"),
    ("authenticated gateway rate limit", "architecture"),
    ("mask card digits before logging", "security-rules"),
    ("schema change table lock", "migrations"),
    ("friday release freeze", "deploying"),
    ("ledger write failure stop deploys", "alerts"),
]


def bench_paraphrase():
    doc = tier.from_md(open(os.path.join(ROOT, "examples/payments-runbook.md"), encoding="utf-8").read())
    rows, legacy_hits, hybrid_hits = [], 0, 0
    for query, expected in PARAPHRASE_QUERIES:
        old = tier.find(doc, query, 1, legacy=True)
        new = tier.find(doc, query, 1)
        old_id = old[0][0] if old else "-"
        new_id = new[0][0] if new else "-"
        ok_old = old_id == expected
        ok_new = new_id == expected
        legacy_hits += ok_old
        hybrid_hits += ok_new
        rows.append((query[:44], old_id, "Y" if ok_old else "N", new_id, "Y" if ok_new else "N"))
    say("E. paraphrase and compound-identifier retrieval (payments runbook)")
    table(rows, ["query", "v0.2 top-1", "hit", "v0.3 top-1", "hit"])
    say(f"   v0.2 exact-prefix hit@1 {legacy_hits}/{len(PARAPHRASE_QUERIES)}, "
        f"v0.3 hybrid hit@1 {hybrid_hits}/{len(PARAPHRASE_QUERIES)}")
    say("   v0.3 adds identifier splitting, synonyms, trigram fallback, and a table-field boost.")
    say()


def bench_repo():
    files = tier.repo_source_files(SRC)
    raw_parts, expected_symbols = [], set()
    for rel, full, _ext in files:
        text = open(full, encoding="utf-8", errors="replace").read()
        raw_parts.append(text)
        _purpose, symbols, _imports = tier._extract_symbols(rel, text)
        expected_symbols.update((rel, name) for _kind, name, _sig, _a, _b in symbols)
    raw = "\n".join(raw_parts)
    doc = tier.from_repo(SRC)
    full = tier.dumps(doc)
    l0 = tier.index(doc)
    raw_t, full_t, l0_t = toks([raw, full, l0])
    target = next((s.id for s in doc.sections if s.title.endswith("/parser.py") or s.title == "parser.py"),
                  doc.sections[0].id if doc.sections else "")
    fetch, _missing = tier.get(doc, [target], follow=True) if target else ("", [])
    fetch_t = toks([fetch])[0]
    combo_t = l0_t + fetch_t
    found, edges = set(), 0
    for sec in doc.sections:
        for nd in tier.nodes(sec):
            if nd["t"] == "table" and nd["name"] == "symbols":
                for row in nd["rows"]:
                    found.add((sec.title, row[1]))
            if nd["t"] == "ref":
                edges += 1
    missing = expected_symbols - found
    errs = [m for l, m in tier.lint(doc, max_section_tokens=10**9) if l == "error"]
    stable = tier.dumps(tier.parse(full)) == full
    rows = [
        ("raw source files", len(files), raw_t, "-"),
        ("repo.tier full", len(doc.sections), full_t, f"{100 * (raw_t - full_t) / max(1, raw_t):.0f}% fewer"),
        ("repo.tier L0", len(doc.sections), l0_t, f"{100 * (raw_t - l0_t) / max(1, raw_t):.0f}% fewer"),
        (f"L0 + {target} + 1-hop", 1, combo_t, f"{100 * (raw_t - combo_t) / max(1, raw_t):.0f}% fewer"),
    ]
    say("F. codebase -> TIER graph index (this TIER project)")
    table(rows, ["measure", "sections", "tokens", "vs raw source"])
    say(f"   symbols: {len(expected_symbols)} expected, {len(found)} in map, missing {len(missing)}; "
        f"internal edges: {edges}; lint errors: {len(errs)}; round-trip stable: {'Y' if stable else 'N'}")
    say(f"   check: L0 + 1-hop >=70% fewer than raw: {'Y' if combo_t <= raw_t * 0.30 else 'N'}; "
        f"exported names preserved: {'Y' if not missing else 'N'}")
    say("   Modules keep exact line ranges and '>' edges, so tier_get --follow returns dependency signatures.")
    say()


# ---------------------------------------------------------------- G: 4-layer workflow
def bench_workflow():
    repo_doc = tier.from_repo(SRC)
    files = tier.repo_source_files(SRC)
    raw = "\n".join(open(full, encoding="utf-8", errors="replace").read() for _rel, full, _ext in files)
    raw_t = toks([raw])[0]
    full_repo_t = toks([tier.dumps(repo_doc)])[0]

    l0 = tier.index(repo_doc)
    step1 = toks([l0])[0]
    hits = tier.find(repo_doc, "parse TIER document", 1)
    target = hits[0][0] if hits else "-"
    hit_line = f"@{hits[0][0]} {hits[0][1]} {hits[0][2]}" if hits else ""
    step2 = toks([hit_line])[0]
    l2, _missing = tier.get(repo_doc, [target], follow=True)
    step3 = toks([l2])[0]

    targets = [("parser.py", ["parse"]), ("converter.py", ["from_md"]), ("retrieval.py", ["find"])]
    slice_parts, missing = [], []
    for module, names in targets:
        text, miss = tier.slice_symbols(repo_doc, module, names, root=SRC)
        slice_parts.append(text)
        missing += miss
    slices = "\n\n".join(slice_parts)
    step4 = toks([slices])[0]
    exact = 0
    for module, names in targets:
        sec = repo_doc.by_id().get(module)
        rows = {}
        if sec is not None:
            for node in tier.nodes(sec):
                if node["t"] == "table" and node["name"] == "symbols":
                    for row in node["rows"]:
                        rows[row[1]] = row
        source_lines = open(os.path.join(SRC, module), encoding="utf-8").read().splitlines()
        for name in names:
            row = rows.get(name)
            if not row:
                continue
            m = re.match(r"^(\d+)-(\d+)$", row[3])
            if not m:
                continue
            start, end = int(m.group(1)), int(m.group(2))
            snippet = "\n".join(source_lines[start - 1:end])
            if f"@{module}#{name} [L{start}-L{end}]" in slices and snippet in slices:
                exact += 1

    impact_res = tier.impact(repo_doc, target, root=SRC)
    impact_text = tier.render_impact(impact_res)
    dependent_ids = set(impact_res["direct_dependents"])
    expected_dependents = {
        "m-__init__.py", "cli.py", "converter.py", "lint.py", "memory.py",
        "mcp-server.py", "pipe.py", "plugin.py", "repo.py", "retrieval.py",
    }
    dependents_ok = len(expected_dependents & dependent_ids)

    with tempfile.TemporaryDirectory() as td:
        for rel, full, _ext in files:
            dest = os.path.join(td, rel)
            if os.path.dirname(dest):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(full, dest)
        temp_doc = tier.from_repo(td)
        temp_repo = os.path.join(td, "repo.tier")
        open(temp_repo, "w", encoding="utf-8").write(tier.dumps(temp_doc))
        with open(os.path.join(td, "parser.py"), "a", encoding="utf-8") as f:
            f.write("\n\ndef v1_sync_probe() -> int:\n    return 1\n")
        session = tier.Session()
        session.mark(temp_repo, "parser.py")
        session.mark(temp_repo, "parser.py#parse")
        sync_res = tier.sync_repo(temp_repo, root=td, session=session, session_key=temp_repo)
        sync_ok = (
            "parser.py" in sync_res["changed"]
            and any(row[1] == "v1_sync_probe" for node in tier.nodes(sync_res["doc"].by_id()["parser.py"])
                    if node["t"] == "table" for row in node["rows"])
            and not session.is_marked(temp_repo, "parser.py#parse")
        )
        sync_text = (
            f"sync changed={','.join(sync_res['changed'])} added={','.join(sync_res['added'])} "
            f"deleted={','.join(sync_res['deleted'])} invalidated={'Y' if sync_ok else 'N'}"
        )
    step5 = toks([impact_text, sync_text])
    total = step1 + step2 + step3 + step4 + sum(step5)
    saving = 100 * (raw_t - total) / max(1, raw_t)
    saving_full = 100 * (full_repo_t - total) / max(1, full_repo_t)
    rows = [
        ("1. L0 repo index", step1),
        ("2. hybrid find -> module", step2),
        ("3. L2 signatures + 1-hop", step3),
        ("4. L3 slices: parse, from_md, find", step4),
        ("5. impact + incremental sync", sum(step5)),
        ("TOTAL workflow", total),
        ("raw source baseline", raw_t),
        ("full repo.tier baseline", full_repo_t),
    ]
    say("G. end-to-end 4-layer coding workflow (read, locate, signatures, slices, impact/sync)")
    table(rows, ["step", "tokens"])
    say(f"   savings vs raw source: {saving:.1f}%; vs full repo.tier: {saving_full:+.1f}%")
    say(f"   exact L3 slices: {exact}/3; impact dependents: {dependents_ok}/{len(expected_dependents)}; "
        f"incremental sync after edit: {'Y' if sync_ok else 'N'}")
    say(f"   check: total workflow >85% under raw source: {'Y' if saving > 85 else 'N'}")
    say()


# ---------------------------------------------------------------- H: polyglot repo
POLYGLOT_FILES = {
    "README.md": "# Polyglot Fixture\n\nService with Python, TypeScript, Go, and Rust modules.\n",
    "app/__init__.py": '"""Polyglot app package."""\n',
    "app/models.py": '''"""Domain models."""
from dataclasses import dataclass
from typing import List


@dataclass
class Item:
    """A line item."""

    name: str
    price: float
    quantity: int = 1

    def total(self) -> float:
        """Return the line total."""
        return self.price * self.quantity


def make_item(
    name: str,
    price: float,
    quantity: int = 1,
) -> Item:
    """Build an item."""
    return Item(name=name, price=price, quantity=quantity)


def summarize(items: List[Item]) -> float:
    """Sum every line total."""
    return sum(item.total() for item in items)
''',
    "app/core.py": '''"""Core service operations."""
import asyncio
from functools import wraps

from .models import Item, make_item


def retry(fn):
    """Retry a coroutine once."""

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except RuntimeError:
            return await fn(*args, **kwargs)

    return wrapper


class Service:
    """Coordinates item retrieval."""

    def __init__(self, source: str = "memory") -> None:
        self.source = source
        self.cache = {}

    @retry
    async def fetch(
        self,
        key: str,
        timeout: float = 1.5,
    ) -> Item:
        """Fetch one item, retrying once."""
        if key in self.cache:
            return self.cache[key]
        await asyncio.sleep(0)
        item = make_item(key, 9.99, 2)
        self.cache[key] = item
        return item


@retry
async def load_items(
    keys: list,
    source: str = "memory",
) -> list:
    """Load several items."""
    svc = Service(source)
    return [await svc.fetch(key) for key in keys]
''',
    "app/cli.py": '''"""Command line entry point."""
import asyncio

from .core import load_items
from .models import summarize


def main(argv=None) -> int:
    """Run the demo."""
    items = asyncio.run(load_items(["alpha", "beta"]))
    print(summarize(items))
    return 0
''',
    "web/util.ts": '''// Shared web utilities.
export interface Identifiable {
  id: string;
}

export type Money = {
  amount: number;
  currency: string;
};

export function formatMoney(
  value: Money,
  locale: string = "en-US",
): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: value.currency,
  }).format(value.amount);
}

export const clamp = (
  value: number,
  low: number,
  high: number,
): number => {
  return Math.min(high, Math.max(low, value));
};

export class Store {
  private rows: Identifiable[] = [];

  get(
    id: string,
  ): Identifiable | undefined {
    return this.rows.find((row) => row.id === id);
  }
}
''',
    "web/api.ts": '''// API client.
import { Identifiable, Money, Store, formatMoney } from "./util";

export class ApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export class Client {
  private store = new Store();

  constructor(private baseUrl: string) {}

  async fetchItem(
    id: string,
    timeout: number = 1000,
  ): Promise<Identifiable> {
    const row = this.store.get(id);
    if (!row) {
      throw new ApiError(`missing ${id}`);
    }
    return row;
  }
}

export function describe(
  value: Money,
): string {
  return formatMoney(value);
}
''',
    "web/index.ts": '''// Web entry point.
import { Client } from "./api";
import { Money } from "./util";

export const defaultTimeout = (): number => 1000;

export function createClient(
  baseUrl: string,
  timeout: number = defaultTimeout(),
): Client {
  return new Client(baseUrl);
}

export function renderMoney(value: Money): string {
  return `${value.currency} ${value.amount}`;
}
''',
    "go.mod": "module example.com/poly\n\ngo 1.22\n",
    "main.go": '''// Command poly runs the demo service.
package main

import (
	"fmt"

	"example.com/poly/store"
)

func main() {
	s := store.New()
	run(s)
	fmt.Println("done")
}

func run(
	s *store.Store,
) error {
	s.Put("alpha", 1)
	value, ok := s.Get("alpha")
	if !ok {
		return fmt.Errorf("missing alpha")
	}
	fmt.Println(value)
	return nil
}
''',
    "store/store.go": '''// Package store keeps in-memory values.
package store

import "sync"

// Reader is a read-only store.
type Reader interface {
	Get(key string) (int, bool)
}

// Store is a concurrency-safe map.
type Store struct {
	mu   sync.RWMutex
	data map[string]int
}

// New builds an empty store.
func New() *Store {
	return &Store{data: make(map[string]int)}
}

// Put stores a value.
func (s *Store) Put(
	key string,
	value int,
) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data[key] = value
}

// Get reads a value.
func (s *Store) Get(
	key string,
) (int, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	value, ok := s.data[key]
	return value, ok
}
''',
    "src/lib.rs": '''//! Polyglot Rust crate.
pub mod store;
pub mod util;

pub fn boot() -> store::Store {
    store::Store::new()
}
''',
    "src/util.rs": '''//! Utility helpers.

/// A configuration value.
pub struct Config {
    pub name: String,
    pub retries: usize,
}

/// Format a name and retry count.
pub fn helper(
    name: &str,
    retries: usize,
) -> String {
    format!("{}:{}", name, retries)
}

impl Config {
    pub fn new(name: &str) -> Self {
        Config { name: name.to_string(), retries: 3 }
    }

    pub fn label<'a>(&'a self) -> &'a str {
        &self.name
    }
}
''',
    "src/store.rs": '''//! In-memory store.
use crate::util::helper;

/// A stored value.
pub struct Store {
    pub name: String,
}

/// Operations supported by a store.
pub trait StoreOps {
    fn get(&self) -> String;
}

impl Store {
    pub fn new(
        name: &str,
    ) -> Self {
        Store { name: name.to_string() }
    }
}

impl StoreOps for Store {
    fn get(&self) -> String {
        helper(&self.name, 1)
    }
}

impl std::fmt::Display for Store {
    fn fmt(
        &self,
        f: &mut std::fmt::Formatter,
    ) -> std::fmt::Result {
        write!(f, "{}", self.name)
    }
}

pub async fn load<'a>(
    name: &'a str,
) -> Store {
    Store::new(name)
}
''',
    "docs/ops.md": "# Operations\n\n## Rotation\n\nRotate credentials every 30 days.\n\n## Escalation\n\nPage the primary, then the secondary.\n",
}

_POLY_PY = '''"""Generated billing service module @@I@@."""
from dataclasses import dataclass


@dataclass
class Invoice@@I@@:
    """Invoice record."""

    key: str
    amount: int
    currency: str = "USD"


def normalize@@I@@(rows):
    """Normalize invoice rows."""
    out = []
    for row in rows:
        key = str(row.get("key", "")).strip()
        amount = int(row.get("amount", 0))
        currency = str(row.get("currency", "USD"))
        if amount < 0:
            amount = 0
        if not key:
            continue
        if amount > 1000:
            amount -= amount // 100
        if currency != "USD":
            amount = amount * 2
        if amount == 0:
            amount = 1
        if key.startswith("tmp-"):
            continue
        if key.endswith("-legacy"):
            amount = amount - 1
        # marker gen_py_@@I@@_normalize
        out.append({"key": key, "amount": amount, "currency": currency})
    return out


def total@@I@@(rows):
    """Sum normalized invoices."""
    total = 0
    for row in normalize@@I@@(rows):
        amount = row["amount"]
        if amount > 500:
            amount -= 5
        if amount < 0:
            amount = 0
        if amount == 0:
            amount = 1
        # marker gen_py_@@I@@_total
        total += amount
    return total
'''

_POLY_TS = '''// Generated billing service module @@I@@.
export interface Invoice@@I@@ {
  key: string;
  amount: number;
  currency: string;
}

export function normalize@@I@@(rows: Invoice@@I@@[]): Invoice@@I@@[] {
  const out: Invoice@@I@@[] = [];
  for (const row of rows) {
    const key = row.key.trim();
    let amount = row.amount;
    if (amount < 0) {
      amount = 0;
    }
    if (!key) {
      continue;
    }
    if (amount > 1000) {
      amount = amount - Math.floor(amount / 100);
    }
    if (key.startsWith("tmp-")) {
      continue;
    }
    if (key.endsWith("-legacy")) {
      amount -= 1;
    }
    // marker gen_ts_@@I@@_normalize
    out.push({ key, amount, currency: row.currency || "USD" });
  }
  return out;
}

export const total@@I@@ = (rows: Invoice@@I@@[]): number => {
  let total = 0;
  for (const row of normalize@@I@@(rows)) {
    let amount = row.amount;
    if (amount > 500) {
      amount -= 5;
    }
    if (amount < 0) {
      amount = 0;
    }
    // marker gen_ts_@@I@@_total
    total += amount;
  }
  return total;
};
'''

_POLY_GO = '''// Generated billing service module @@I@@.
package services

type Invoice@@I@@ struct {
	Key      string
	Amount   int
	Currency string
}

func Normalize@@I@@(rows []Invoice@@I@@) []Invoice@@I@@ {
	out := make([]Invoice@@I@@, 0, len(rows))
	for _, row := range rows {
		key := row.Key
		amount := row.Amount
		if amount < 0 {
			amount = 0
		}
		if amount > 1000 {
			amount -= amount / 100
		}
		if key == "" {
			continue
		}
		if len(key) > 64 {
			key = key[:64]
		}
		// marker gen_go_@@I@@_normalize
		out = append(out, Invoice@@I@@{Key: key, Amount: amount, Currency: row.Currency})
	}
	return out
}

func Total@@I@@(rows []Invoice@@I@@) int {
	total := 0
	for _, row := range Normalize@@I@@(rows) {
		amount := row.Amount
		if amount > 500 {
			amount -= 5
		}
		if amount < 0 {
			amount = 0
		}
		// marker gen_go_@@I@@_total
		total += amount
	}
	return total
}
'''

_POLY_RS = '''//! Generated billing service module @@I@@.

pub struct Invoice@@I@@ {
    pub key: String,
    pub amount: i64,
    pub currency: String,
}

pub fn normalize@@I@@(rows: &[Invoice@@I@@]) -> Vec<(String, i64)> {
    let mut out = Vec::new();
    for row in rows {
        let key = row.key.trim().to_string();
        let mut amount = row.amount;
        if amount < 0 {
            amount = 0;
        }
        if amount > 1000 {
            amount -= amount / 100;
        }
        if key.is_empty() {
            continue;
        }
        if key.len() > 64 {
            continue;
        }
        // marker gen_rs_@@I@@_normalize
        out.push((key, amount));
    }
    out
}

pub fn total@@I@@(rows: &[Invoice@@I@@]) -> i64 {
    let mut total = 0;
    for (_key, amount) in normalize@@I@@(rows) {
        let mut value = amount;
        if value > 500 {
            value -= 5;
        }
        if value < 0 {
            value = 0;
        }
        // marker gen_rs_@@I@@_total
        total += value;
    }
    total
}
'''

_GEN_FILES, _GEN_SPECS = {}, {}
for _i in range(6):
    _t = str(_i)
    _GEN_FILES[f"services/py_{_i}.py"] = _POLY_PY.replace("@@I@@", _t)
    _GEN_SPECS[f"services/py_{_i}.py"] = [
        (f"Invoice{_i}", "amount: int", True, f"def normalize{_i}", False),
        (f"normalize{_i}", f"marker gen_py_{_i}_normalize", False, f"def total{_i}", False),
        (f"total{_i}", f"marker gen_py_{_i}_total", False, None, False),
    ]
    _GEN_FILES[f"services/ts_{_i}.ts"] = _POLY_TS.replace("@@I@@", _t)
    _GEN_SPECS[f"services/ts_{_i}.ts"] = [
        (f"Invoice{_i}", "amount: number;", False, f"export function normalize{_i}", False),
        (f"normalize{_i}", f"marker gen_ts_{_i}_normalize", False, f"export const total{_i}", False),
        (f"total{_i}", f"marker gen_ts_{_i}_total", False, None, False),
    ]
    _GEN_FILES[f"services/go_{_i}.go"] = _POLY_GO.replace("@@I@@", _t)
    _GEN_SPECS[f"services/go_{_i}.go"] = [
        (f"Invoice{_i}", "Amount   int", False, f"func Normalize{_i}", False),
        (f"Normalize{_i}", f"marker gen_go_{_i}_normalize", False, f"func Total{_i}", False),
        (f"Total{_i}", f"marker gen_go_{_i}_total", False, None, False),
    ]
    _GEN_FILES[f"services/rs_{_i}.rs"] = _POLY_RS.replace("@@I@@", _t)
    _GEN_SPECS[f"services/rs_{_i}.rs"] = [
        (f"Invoice{_i}", "pub amount: i64", False, f"pub fn normalize{_i}", False),
        (f"normalize{_i}", f"marker gen_rs_{_i}_normalize", False, f"pub fn total{_i}", False),
        (f"total{_i}", f"marker gen_rs_{_i}_total", False, None, False),
    ]
POLYGLOT_FILES.update(_GEN_FILES)

POLYGLOT_EXPECTED = {
    "app/models.py": [
        ("Item", "price: float", True, "def make_item", False),
        ("Item.total", "return self.price * self.quantity", False, "def make_item", False),
        ("make_item", "return Item(name=name", False, "def summarize", False),
        ("summarize", "return sum(item.total()", False, None, False),
    ],
    "app/core.py": [
        ("retry", "async def wrapper", False, "class Service", False),
        ("Service", "self.cache = {}", False, "async def load_items", False),
        ("Service.__init__", "self.source = source", False, "@retry", False),
        ("Service.fetch", "item = make_item(key", True, "async def load_items", False),
        ("load_items", "return [await svc.fetch", True, None, False),
    ],
    "app/cli.py": [("main", "items = asyncio.run", False, None, False)],
    "web/util.ts": [
        ("Identifiable", "id: string;", False, "export type Money", False),
        ("Money", "currency: string;", False, "export function formatMoney", False),
        ("formatMoney", 'style: "currency"', False, "export const clamp", False),
        ("clamp", "return Math.min(high", False, "export class Store", False),
        ("Store", "private rows: Identifiable[]", False, None, False),
        ("Store.get", "return this.rows.find", False, None, False),
    ],
    "web/api.ts": [
        ("ApiError", 'this.name = "ApiError"', False, "export class Client", False),
        ("ApiError.constructor", "super(message)", False, "export class Client", False),
        ("Client", "private store = new Store", False, "export function describe", False),
        ("Client.constructor", "constructor(private baseUrl", False, "async fetchItem", False),
        ("Client.fetchItem", "throw new ApiError", False, "export function describe", False),
        ("describe", "return formatMoney(value)", False, None, False),
    ],
    "web/index.ts": [
        ("defaultTimeout", "=> 1000", False, "export function createClient", False),
        ("createClient", "return new Client(baseUrl)", False, "export function renderMoney", False),
        ("renderMoney", "return `${value.currency}", False, None, False),
    ],
    "main.go": [
        ("main", "s := store.New()", False, "func run(", False),
        ("run", 'return fmt.Errorf("missing alpha")', False, None, False),
    ],
    "store/store.go": [
        ("Reader", "Get(key string) (int, bool)", False, "type Store struct", False),
        ("Store", "mu   sync.RWMutex", False, "func New()", False),
        ("New", "return &Store{data", False, "func (s *Store) Put(", False),
        ("Store.Put", "s.data[key] = value", False, "func (s *Store) Get(", False),
        ("Store.Get", "value, ok := s.data[key]", False, None, False),
    ],
    "src/lib.rs": [("boot", "store::Store::new()", False, None, False)],
    "src/util.rs": [
        ("Config", "pub retries: usize", False, "pub fn helper", False),
        ("helper", 'format!("{}:{}", name, retries)', False, "impl Config", False),
        ("Config.new", "Config { name: name.to_string()", False, "pub fn label", False),
        ("Config.label", "&self.name", False, None, False),
    ],
    "src/store.rs": [
        ("Store", "pub name: String", False, "pub trait StoreOps", False),
        ("StoreOps", "fn get(&self) -> String;", False, "impl Store {", False),
        ("StoreOps.get", "fn get(&self) -> String;", False, "impl Store {", True),
        ("Store.new", "Store { name: name.to_string() }", False, "impl StoreOps", False),
        ("Store.get", "helper(&self.name, 1)", False, "impl std::fmt::Display", False),
        ("Store.fmt", 'write!(f, "{}", self.name)', False, "pub async fn load", False),
        ("load", "Store::new(name)", False, None, False),
    ],
}
POLYGLOT_EXPECTED.update(_GEN_SPECS)

POLYGLOT_EDGES = {
    ("app/core.py", "app/models.py"),
    ("app/cli.py", "app/core.py"),
    ("app/cli.py", "app/models.py"),
    ("web/api.ts", "web/util.ts"),
    ("web/index.ts", "web/api.ts"),
    ("web/index.ts", "web/util.ts"),
    ("main.go", "store/store.go"),
    ("src/lib.rs", "src/store.rs"),
    ("src/lib.rs", "src/util.rs"),
    ("src/store.rs", "src/util.rs"),
}

POLYGLOT_SLICES = [
    ("app/core.py", ["Service.fetch", "load_items"]),
    ("web/util.ts", ["formatMoney"]),
    ("web/api.ts", ["Client.fetchItem"]),
    ("store/store.go", ["Store.Get"]),
    ("src/store.rs", ["Store.new", "load"]),
]


def write_polyglot(root):
    for rel, text in POLYGLOT_FILES.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return root


def _poly_rows(doc, rel):
    for sec in doc.sections:
        meta = tier._repo_file_meta(sec)
        if meta.get("path") == rel:
            rows = {}
            for nd in tier.nodes(sec):
                if nd["t"] == "table" and nd["name"] == "symbols":
                    for row in nd["rows"]:
                        rows[row[1]] = row
            return sec, rows
    return None, {}


def _poly_range_check(root, doc):
    good = total = 0
    for rel, specs in POLYGLOT_EXPECTED.items():
        _sec, rows = _poly_rows(doc, rel)
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            lines = f.read().splitlines()
        for name, marker, decorated, exclude, bodyless in specs:
            total += 1
            row = rows.get(name)
            if not row:
                continue
            start, end = map(int, row[3].split("-"))
            snippet = "\n".join(lines[start - 1:end])
            if marker not in snippet:
                continue
            if exclude and exclude in snippet:
                continue
            first = lines[start - 1].strip()
            if decorated and not first.startswith("@"):
                continue
            if not decorated:
                simple = name.split(".")[-1]
                if simple not in first and name not in first:
                    continue
            if rel.endswith((".py", ".pyi")):
                good += 1
                continue
            last = lines[end - 1].strip()
            if bodyless:
                if last.endswith(";"):
                    good += 1
            elif "=>" in last and "{" not in last:
                if last.endswith(";"):
                    good += 1
            elif last.rstrip(";").rstrip().endswith("}"):
                good += 1
    return good, total


def bench_polyglot():
    with tempfile.TemporaryDirectory() as td:
        write_polyglot(td)
        doc = tier.from_repo(td)
        doc.meta["title"] = "polyglot fixture repository map"
        full = tier.dumps(doc)
        l0 = tier.index(doc)
        files = tier.repo_source_files(td)
        raw = "\n".join(open(full_path, encoding="utf-8", errors="replace").read()
                        for _rel, full_path, _ext in files)
        slice_parts, missing = [], []
        for rel, symbols in POLYGLOT_SLICES:
            text, miss = tier.slice_symbols(doc, rel, symbols, root=td)
            slice_parts.append(text)
            missing += miss
        slices = "\n\n".join(slice_parts)
        target_ids = [s.id for s in doc.sections
                      if s.title in {rel for rel, _syms in POLYGLOT_SLICES}]
        l2, _ = tier.get(doc, target_ids, follow=True)
        raw_t, full_t, l0_t, slices_t, l2_t = toks([raw, full, l0, slices, l2])
        full_l2_l3_t = toks([l0 + "\n" + full + "\n" + slices])[0]
        surgical_t = toks([l0 + "\n" + l2 + "\n" + slices])[0]

        found, edges = set(), set()
        for sec in doc.sections:
            rel = tier._repo_file_meta(sec).get("path", "")
            for nd in tier.nodes(sec):
                if nd["t"] == "table" and nd["name"] == "symbols":
                    for row in nd["rows"]:
                        found.add((rel, row[1]))
                elif nd["t"] == "ref":
                    target = doc.by_id().get(nd["id"])
                    if target is not None:
                        edges.add((rel, tier._repo_file_meta(target).get("path", "")))
        expected = {(rel, name) for rel, specs in POLYGLOT_EXPECTED.items() for name, *_ in specs}
        missing_symbols = expected - found
        found_edges = edges & POLYGLOT_EDGES
        range_good, range_total = _poly_range_check(td, doc)
        errors = [m for lvl, m in tier.lint(doc, max_section_tokens=10**9) if lvl == "error"]
        stable = tier.dumps(tier.parse(full)) == full
        rows = [
            ("raw polyglot source", len(files), raw_t, "-"),
            ("repo.tier full L2", len(doc.sections), full_t,
             f"{100 * (raw_t - full_t) / max(1, raw_t):.0f}% fewer"),
            ("L0 index", len(doc.sections), l0_t,
             f"{100 * (raw_t - l0_t) / max(1, raw_t):.0f}% fewer"),
            ("L0 + full L2 + 7 L3 slices", len(doc.sections), full_l2_l3_t,
             f"{100 * (raw_t - full_l2_l3_t) / max(1, raw_t):.0f}% fewer"),
            ("L0 + target L2 + 7 L3 slices", len(target_ids), surgical_t,
             f"{100 * (raw_t - surgical_t) / max(1, raw_t):.0f}% fewer"),
        ]
        say("H. polyglot repository map (Python, TypeScript, Go, Rust)")
        table(rows, ["measure", "sections", "tokens", "vs raw"])
        say(f"   symbol recall: {len(expected) - len(missing_symbols)}/{len(expected)}"
            f" (missing {len(missing_symbols)}); ranges: {range_good}/{range_total};"
            f" edge recall: {len(found_edges)}/{len(POLYGLOT_EDGES)};"
            f" slices: {len(POLYGLOT_SLICES)} groups, missing {len(missing)}")
        say(f"   lint errors: {len(errors)}; round-trip stable: {'Y' if stable else 'N'}")
        say(f"   check: all exported symbols captured: {'Y' if not missing_symbols else 'N'};"
            f" all cross-file edges captured: {'Y' if found_edges == POLYGLOT_EDGES else 'N'};"
            f" surgical L0+L2+L3 under 35% of raw: {'Y' if surgical_t < raw_t * 0.35 else 'N'}")
        say()


# ---------------------------------------------------------------- I: blind retrieval
BLIND_QUERIES = [
    ("how do I roll back a bad deploy", {"rb-rolling-back"}, "deployctl rollback"),
    ("who do I page at night if nobody acks", {"rb-on-call-escalation"}, "payments-secondary"),
    ("what is the connection pool size per replica", {"rb-database-connection"}, "40 connections per replica"),
    ("can I deploy on friday", {"rb-deploying", "hb-deploy"}, "14:00"),
    ("what is the HighLatencyP99 alert threshold", {"rb-alerts"}, "800 ms"),
    ("duplicate charges what do I do", {"rb-duplicate-charges"}, "payments.reject_without_idempotency"),
    ("what must I never log", {"rb-security-rules"}, "card numbers"),
    ("how many replicas in prod-eu", {"rb-environments"}, "payments.eu.internal,8"),
    ("session deduplication invalidates slices", {"spec-session-deduplication"},
     "already in context; not resent"),
    ("what closes a named guard block", {"spec-untrusted-blocks", "spec-safety"}, "guard"),
    ("what does tier_impact report for blast radius", {"spec-impact-analysis", "spec-mcp-server"}, "blast"),
    ("how do I run the test suite", {"express_readme-running-tests"}, "npm test"),
    ("how do I install prerequisites on Windows", {"node_building-windows-prerequisites"}, "WinGet"),
    ("max size of ccache cache", {"pytorch_contributing-max-size-cache"}, "ccache -M 25Gi"),
    ("skip CI while iterating PR title", {"pytorch_contributing-skip-ci-while"}, "[no-ci]"),
]


def _merge_docs(docs):
    out = tier.Doc(meta={
        "tier": tier.VERSION,
        "title": "TIER retrieval stress corpus",
        "sum": "Combined runbook, handbook, specification, and GitHub documentation",
    })
    rules = []
    for prefix, doc in docs:
        for rule in doc.rules:
            if rule not in rules:
                rules.append(rule)
        ids = {s.id for s in doc.sections if s.id != "_"}
        for sec in doc.sections:
            if sec.id == "_":
                continue
            lines = []
            for line in sec.lines:
                if line.startswith("> "):
                    ref, _, note = line[2:].strip().partition(" ")
                    if ref in ids:
                        line = "> " + f"{prefix}-{ref}" + ((" " + note) if note else "")
                lines.append(line)
            out.sections.append(tier.Section(
                f"{prefix}-{sec.id}", sec.title, sec.abstract, lines, sec.line_no, sec.level
            ))
    out.rules = rules
    return out


def _blind_corpus():
    docs = []
    for name, prefix in (("examples/payments-runbook.tier", "rb"),
                         ("examples/handbook.tier", "hb"),
                         ("SPEC.tier", "spec")):
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            doc = tier._load(path)
            if prefix == "spec":
                doc.sections = [s for s in doc.sections if s.id not in ("benchmarks", "sources")]
            docs.append((prefix, doc))
    for path in sorted(glob.glob(os.path.join(ROOT, "corpus", "*.md"))):
        prefix = os.path.splitext(os.path.basename(path))[0].replace("-", "_")
        with open(path, encoding="utf-8") as f:
            docs.append((prefix, tier.from_md(f.read())))
    return _merge_docs(docs)


def bench_blind_retrieval():
    doc = _blind_corpus()
    full = tier.dumps(doc)
    l0 = tier.index(doc)
    full_t, l0_t = toks([full, l0])
    rows, hit1 = [], 0
    cost1, cost2, answer_ok = [], [], 0
    for query, accepted, needle in BLIND_QUERIES:
        hits = tier.find(doc, query, 2)
        ids = [h[0] for h in hits]
        top = ids[0] if ids else "-"
        ok1 = top in accepted
        ok2 = bool(accepted & set(ids[:2]))
        text1 = tier.get(doc, [top])[0] if top != "-" else ""
        text2 = tier.get(doc, ids[:2])[0] if ids else ""
        n1 = toks([text1])[0]
        n2 = toks([text2])[0]
        answer = needle.lower() in text1.lower()
        hit1 += ok1
        answer_ok += answer
        cost1.append(n1)
        cost2.append(l0_t + n1)
        rows.append((query[:40], top, "Y" if ok1 else "N", n1, "Y" if ok2 else "N", n2))
    say("I. blind retrieval over a merged 50+ section corpus (runbook + handbook + SPEC + GitHub docs)")
    table(rows, ["query", "top-1 section", "hit@1", "tok@1", "hit@2", "tok@2"])
    mean1 = sum(cost1) / max(1, len(cost1))
    mean2 = sum(cost2) / max(1, len(cost2))
    say(f"   corpus sections: {len(doc.sections)}; full corpus {full_t} tokens; L0 index {l0_t} tokens")
    say(f"   hit@1 {hit1}/{len(BLIND_QUERIES)}, hit@2 {sum(1 for r in rows if r[4] == 'Y')}/{len(BLIND_QUERIES)},"
        f" answer string present in top-1: {answer_ok}/{len(BLIND_QUERIES)}")
    say(f"   mean top-1 fetch {mean1:.0f} tokens; mean L0 + top-1 {mean2:.0f} tokens"
        f" ({100 * (full_t - mean2) / max(1, full_t):.0f}% below full-corpus loading)")
    say(f"   check: hit@1 >= 14/15: {'Y' if hit1 >= 14 else 'N'};"
        f" mean L0+top-1 under 20% of full corpus: {'Y' if mean2 < full_t * 0.20 else 'N'}")
    say()


if __name__ == "__main__":
    js_modules = os.path.join(HERE, "node_modules")
    if not os.path.isdir(js_modules) or not shutil.which("node"):
        print(
            "notice: benchmarks/node_modules or node is absent; using the deterministic "
            "chars/4 token estimator instead of o200k BPE. Run `npm install` in benchmarks/ "
            "for exact counts.\n"
        )
    if yaml is None:
        print("notice: PyYAML is not installed; using the built-in stdlib YAML formatter.\n")
    be = tier.count_tokens(["x"])[1]
    say(f"tokenizer backend: {be}" + ("  (o200k_base BPE)" if be == "o200k" else "  (~chars/4 estimate!)"))
    say()
    bench_records()
    bench_nested()
    bench_runbook()
    bench_corpus()
    bench_paraphrase()
    bench_repo()
    bench_workflow()
    bench_polyglot()
    bench_blind_retrieval()
    open(os.path.join(HERE, "BENCH.txt"), "w").write("\n".join(OUT) + "\n")
