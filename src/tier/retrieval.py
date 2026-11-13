from __future__ import annotations

import math
import re

from .models import Doc, Section, STOP, TABLE_RE, _W, _table_count
from .parser import _live_lines, nodes, render_section, _repo_file_meta
from .tokens import count_tokens

QSTOP = STOP | {
    "i", "me", "my", "we", "you", "it", "do", "does", "did", "how", "what", "who", "when", "where",
    "which", "why", "at", "if", "on", "by", "be", "can", "should", "need", "from", "that", "this",
    "there", "or", "as", "so", "about", "get", "us",
}
_IDENT_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+|[A-Z]+")
_IDENT_ALNUM = re.compile(r"[A-Z]?[a-z]*\d+|[A-Z]+\d+|\d+[A-Za-z]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")


SYNONYM_GROUPS = [
    ["deploy", "deployment", "release", "ship", "rollout", "promote", "launch"],
    ["rollback", "rolling", "revert", "undo", "restore", "backout", "back"],
    ["error", "failure", "failed", "fail", "fatal", "incident", "outage", "broken", "breaks", "break", "bug", "crash"],
    ["log", "logs", "logging", "print", "redact", "mask", "masking", "redaction", "secret", "secrets"],
    ["db", "database", "postgres", "postgresql", "sql", "ledger", "storage"],
    ["config", "configuration", "settings", "setting", "env", "environment", "variables"],
    ["page", "paged", "paging", "pager", "alert", "alerts", "escalate", "escalation", "oncall", "nighttime", "night", "afterhours", "ack", "acknowledge", "respond"],
    ["auth", "authentication", "authenticated", "login", "token", "tokens", "credential", "credentials"],
    ["duplicate", "double", "duplicated"],
    ["charge", "charges", "billing", "bill", "billed", "payment", "payments", "transaction", "transactions"],
    ["refund", "refunds", "reverse", "reversal", "reimburse"],
    ["latency", "slow", "slowness", "p99", "highlatencyp99"],
    ["timeout", "timeouts", "timed", "hang", "deadline"],
    ["connection", "connections", "pool", "pools", "maxconnections"],
    ["saturation", "congestion", "exhaustion", "overload", "capacity"],
    ["migration", "migrations", "schema", "ddl", "backfill"],
    ["friday", "fri", "weekend", "freeze", "embargo"],
    ["security", "secure", "protected", "pii", "card", "cards", "cvv"],
    ["replica", "replicas", "instance", "instances", "pod", "pods"],
    ["gateway", "proxy", "frontdoor", "balancer"],
    ["canary", "progressive", "staged"],
    ["command", "commands", "cmd", "shell", "cli"],
    ["warning", "warnings", "warn", "deprecated"],
    ["user", "users", "customer", "customers"],
    ["repository", "repo", "project", "codebase"],
    ["function", "functions", "fn", "method", "methods", "symbol", "symbols"],
    ["import", "imports", "dependency", "dependencies", "depends"],
]


_SINGULAR_S = {"postgres", "canvas", "atlas", "kubernetes", "chaos", "aws"}


def _norm(w: str) -> str:
    if len(w) <= 3 or not w.endswith("s"):
        return w
    if w.endswith(("ss", "us", "is")) or w in _SINGULAR_S:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    return w[:-1]


def split_identifier(token: str) -> list:
    """Split camelCase, PascalCase, snake_case, kebab-case and alnum identifiers."""
    token = str(token).strip()
    if not token:
        return []
    raw = []
    chunks = [c for c in re.split(r"[_\-.]+", token) if c]
    if len(chunks) > 1:
        raw.append("".join(chunks))
    for chunk in chunks:
        raw.append(chunk)
        raw.extend(_IDENT_PART.findall(chunk))
        raw.extend(_IDENT_ALNUM.findall(chunk))
    out, seen = [], set()
    for part in raw:
        part = part.lower()
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return out


def _index_tokens(text: str) -> list:
    out = []
    for m in _TOKEN_RE.finditer(text):
        out.extend(split_identifier(m.group(0)))
    return out


def _query_terms(query: str) -> list:
    out = []
    for token in _index_tokens(query):
        term = _norm(token)
        if term in QSTOP or (len(term) < 2 and not term.isdigit()):
            continue
        out.append(term)
    return out


def _synonym_map() -> dict:
    table = {}
    for group in SYNONYM_GROUPS:
        normed = []
        for word in group:
            for part in split_identifier(word):
                normed.append(_norm(part))
        normed = sorted(set(normed))
        for word in normed:
            table.setdefault(word, set()).update(x for x in normed if x != word)
    return table


SYNONYMS = _synonym_map()


def _trigrams(s: str) -> set:
    s = re.sub(r"[^a-z0-9]+", "", s.lower())
    if len(s) < 3:
        return {s} if s else set()
    return {s[i : i + 3] for i in range(len(s) - 2)}


def _trigram_sim(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    return 2 * len(ta & tb) / max(1, len(ta) + len(tb))


def _rule_count(sec: Section) -> int:
    return sum(1 for l in _live_lines(sec.lines) if l.startswith("! "))


def index(doc: Doc, tokens: bool = True, fast: bool = None) -> str:
    """L0 + L1: title, summary, global rules, one line per section."""
    head = doc.meta.get("title", "")
    if doc.meta.get("sum"):
        head += (" - " if head else "") + doc.meta["sum"]
    out = [head] if head else []
    out += ["! " + r for r in doc.rules]
    counts = count_tokens([s.text() for s in doc.sections], fast=fast)[0] if tokens else [None] * len(doc.sections)
    for s, c in zip(doc.sections, counts):
        rc = _rule_count(s)
        label = s.abstract or s.title
        out.append(
            f"@{s.id}"
            + (f" ~{c}t" if c is not None else "")
            + (f" !{rc}" if rc else "")
            + (f" {label}" if label else "")
        )
    return "\n".join(out)


def resolve(doc: Doc, ids: list, follow: bool = False):
    """Return (ordered ids, missing_ids) for a fetch, with optional 1-hop refs."""
    by = doc.by_id()
    order, missing = [], []
    for i in ids:
        if i in by:
            if i not in order:
                order.append(i)
        else:
            missing.append(i)
    if follow:
        for i in list(order):
            for nd in nodes(by[i]):
                if nd["t"] == "ref" and nd["id"] in by and nd["id"] not in order:
                    order.append(nd["id"])
    return order, missing


def get(doc: Doc, ids: list, follow: bool = False, table_mode: str = "raw",
        calls_filter=None):
    """Return (text, missing_ids). follow=True also pulls 1-hop `>` references.

    `calls_filter` narrows `|calls` tables to the rows it accepts; None keeps
    the stored table in full.
    """
    by = doc.by_id()
    order, missing = resolve(doc, ids, follow)
    return "\n".join(render_section(by[i], table_mode, calls_filter) for i in order), missing


class Session:
    """Tracks [doc, section] pairs already emitted in an agent conversation."""

    def __init__(self):
        self.seen: dict = {}

    def reset(self, key=None):
        if key is None:
            self.seen.clear()
        else:
            self.seen.pop(key, None)

    def invalidate(self, key: str, section_id: str):
        """Forget a section and all of its L3 symbol slices."""
        seen = self.seen.get(key)
        if seen is None:
            return
        seen.discard(section_id)
        prefix = section_id + "#"
        for entry in list(seen):
            if entry.startswith(prefix):
                seen.discard(entry)

    def mark(self, key: str, entry: str):
        self.seen.setdefault(key, set()).add(entry)

    def is_marked(self, key: str, entry: str) -> bool:
        return entry in self.seen.get(key, set())

    def get(self, doc: Doc, ids: list, follow: bool = False, table_mode: str = "raw",
            key: str = "", force: bool = False, calls_filter=None):
        """Like get(), but replaces already-served bodies with a one-line stub."""
        by = doc.by_id()
        order, missing = resolve(doc, ids, follow)
        seen = self.seen.setdefault(key, set())
        parts = []
        for i in order:
            if i in seen and not force:
                parts.append(f"@{i} (already in context; not resent)")
                continue
            parts.append(render_section(by[i], table_mode, calls_filter))
            seen.add(i)
        return "\n".join(parts), missing


def _find_legacy(doc: Doc, query: str, k: int = 3):
    """v0.2 exact-prefix search, kept for before/after benchmarks."""
    k = max(0, int(k))
    q = [_norm(w) for w in _W.findall(query.lower()) if w not in QSTOP]
    docs = []
    for s in doc.sections:
        docs.append(
            (
                s,
                _W.findall(s.title.lower()),
                _W.findall(s.abstract.lower()),
                _W.findall(" ".join(s.lines).lower()),
            )
        )
    N = max(1, len(docs))

    def cnt(toks, w):
        return sum(1 for t in toks if t.startswith(w))

    scored = []
    for s, t, a, b in docs:
        sc = 0.0
        for w in q:
            df = sum(1 for _, t2, a2, b2 in docs if cnt(t2, w) or cnt(a2, w) or cnt(b2, w))
            if not df:
                continue
            idf = math.log(1 + N / df)
            tf = 3 * cnt(t, w) + 2 * cnt(a, w) + min(cnt(b, w), 4)
            sc += idf * tf
        if sc > 0:
            scored.append((s.id, round(sc, 2), s.abstract or s.title))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def _is_test_module(sec: Section) -> bool:
    meta = _repo_file_meta(sec)
    path = (meta.get("path") or sec.title or "").lower().replace("\\", "/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return False
    base = parts[-1]
    if base.startswith(("test_", "test-")) or base.endswith(("_test.py", "_test.go")):
        return True
    if any(marker in base for marker in (".test.", ".spec.", "_test.", "_spec.")):
        return True
    return any(p in {"test", "tests", "__tests__", "spec", "specs"} for p in parts[:-1])


def _is_navigation_section(sec: Section) -> bool:
    title = (sec.title or "").strip().lower()
    return title in {"table of contents", "contents", "index", "toc"} or title.startswith("table of contents")


def find(doc: Doc, query: str, k: int = 3, legacy: bool = False):
    """Hybrid BM25 + identifier splitting + synonyms + character-trigram fallback.

    Returns [(id, score, abstract)]. Set legacy=True for the v0.2 prefix matcher.
    """
    if legacy:
        return _find_legacy(doc, query, k)
    k = max(0, int(k))

    weights = {}
    for term in _query_terms(query):
        weights[term] = max(weights.get(term, 0.0), 1.0)
        for syn in SYNONYMS.get(term, ()):
            if syn in QSTOP or len(syn) < 2:
                continue
            weights.setdefault(syn, 0.65)
    if not weights:
        return []
    test_query = any(t in {"test", "tests", "testing", "spec", "specs", "pytest", "unittest"}
                     for t in weights)

    data = []
    for s in doc.sections:
        title = _index_tokens(s.title)
        abstract = _index_tokens(s.abstract)
        live = _live_lines(s.lines)
        rules = _index_tokens(" ".join(x[2:] for x in live if x.startswith("! ")))
        body = _index_tokens(" ".join(x for x in s.lines if not x.startswith("! ")))
        table_lines, ti = [], 0
        while ti < len(s.lines):
            m = TABLE_RE.match(s.lines[ti])
            if m:
                n = _table_count(m.group(2))
                table_lines.extend(s.lines[ti + 1 : ti + 1 + n])
                ti += 1 + n
                continue
            ti += 1
        table = _index_tokens(" ".join(table_lines))
        focus = list(dict.fromkeys(title + abstract + rules))
        focus_sets = [(t, _trigrams(t)) for t in focus if len(t) >= 3]
        data.append(
            {
                "s": s,
                "title": title,
                "abstract": abstract,
                "rules": rules,
                "body": body,
                "table": table,
                "all": title + abstract + rules + body + table,
                "focus_sets": focus_sets,
                "focus_text": re.sub(r"[^a-z0-9]+", "", " ".join(focus)),
            }
        )
    N = max(1, len(data))

    def cnt(tokens, w):
        return sum(1 for t in tokens if t.startswith(w))

    def fuzzy(d, w):
        if len(w) < 4:
            return 0.0
        tw = _trigrams(w)
        best = 0.0
        for token, ts in d["focus_sets"]:
            if abs(len(token) - len(w)) > max(6, len(w)):
                continue
            sim = 2 * len(tw & ts) / max(1, len(tw) + len(ts))
            if sim > best:
                best = sim
        return best

    df_cache = {w: sum(1 for x in data if cnt(x["all"], w)) for w in weights}
    idf_cache = {w: (math.log(1 + N / df) if df else math.log(1 + N)) for w, df in df_cache.items()}

    scored = []
    for d in data:
        sc = 0.0
        for w, weight in weights.items():
            idf = idf_cache[w]
            tf = (
                8 * cnt(d["title"], w)
                + 4 * cnt(d["abstract"], w)
                + 3 * cnt(d["rules"], w)
                + min(cnt(d["body"], w), 4)
                + 1.2 * min(cnt(d["table"], w), 4)
            )
            if tf:
                sc += weight * idf * tf
                continue
            sim = fuzzy(d, w)
            if sim >= 0.55:
                sc += weight * idf * (0.8 + 2.0 * sim)
            elif len(w) >= 4 and w in d["focus_text"]:
                sc += weight * idf * 0.6
        if sc > 0:
            if not test_query and _is_test_module(d["s"]):
                sc *= 0.35
            if _is_navigation_section(d["s"]):
                sc *= 0.5
            scored.append((d["s"].id, round(sc, 2), d["s"].abstract or d["s"].title))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]
