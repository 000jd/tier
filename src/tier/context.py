"""One-turn composite retrieval: index, route, and pack in a single call.

`build_context` answers a question about a document or repository without the
caller having to sequence `find` -> `get` -> `slice` -> `impact` itself. It
resolves the target (building or syncing the map if needed), routes with the
hybrid retriever, matches symbols by name, then packs L2 bodies, L3 slices,
and a dependency summary until the token budget is reached.
"""
from __future__ import annotations

import re

from .cache import ensure_doc
from .models import Doc, _clip
from .parser import _repo_file_meta, is_repo_doc, render_section
from .repo import (_call_graph, _resolve_module, _symbol_key, _symbol_rows, impact,
                   slice_symbols)
from .retrieval import QSTOP, Session, _index_tokens, find
from .tokens import count_tokens

DEFAULT_BUDGET = 3000
HIT_COUNT = 8
MAX_SYMBOLS = 3
MAX_SECTIONS = 3

IMPACT_DIRECT = 6
IMPACT_TRANSITIVE = 10

# A 1-hop collaborator inherits this fraction of its anchor's score. Kept below
# 1.0 so a neighbour never outranks the symbol that raised it, but above the
# 1.0-2.0 substring band so it beats a name that merely contains a query word.
NEIGHBOUR_RATIO = 0.75
_SPAN_SUFFIX_RE = re.compile(r"\s*\[\d+-\d+\]\s*$")


def _count(text: str, fast=None) -> int:
    return count_tokens([text], fast=fast)[0][0] if text else 0


def _module_of(doc: Doc, section_id: str) -> str:
    """The repository-relative module path a section describes."""
    sec = doc.by_id().get(section_id)
    meta = _repo_file_meta(sec) if sec is not None else {}
    return (meta.get("path") if meta else "") or (sec.title if sec is not None else "")


def _symbol_scores(doc: Doc, query: str) -> dict:
    """Score every symbol by query-token overlap with its name.

    Returns ``{(section_id, symbol_name): score}``. Exact and prefix name
    matches outrank substring ones, which is what keeps a symbol that *is* the
    subject above one that merely contains the word.
    """
    terms = [t for t in _index_tokens(query) if len(t) >= 2 and t not in QSTOP]
    if not terms:
        return {}
    scored = {}
    for sec in doc.sections:
        for row in _symbol_rows(sec):
            name = str(row.get("name") or "")
            if not name:
                continue
            lowered = name.lower()
            leaf = lowered.rsplit(".", 1)[-1]
            # Match the bare name too: a leading underscore is a privacy marker,
            # not part of the identifier's meaning, so `_symbol_table` should
            # prefix-match "symbol" exactly like a public name would.
            bare = leaf.lstrip("_") or leaf
            score = 0.0
            for term in terms:
                best = 0.0
                for cand in (leaf, bare):
                    if cand == term:
                        best = max(best, 3.0)
                    elif cand.startswith(term):
                        best = max(best, 2.0)
                    elif term in cand:
                        best = max(best, 1.0)
                if not best and term in lowered:
                    best = 1.0
                score += best
            if score:
                scored[(sec.id, name)] = round(score, 2)
    return scored


def _symbol_hits(doc: Doc, query: str, anchors: int = MAX_SYMBOLS) -> list:
    """Rank (section_id, symbol_name) pairs, then admit 1-hop collaborators.

    `anchors` is how many of the strongest direct matches are allowed to pull in
    their call neighbours. A neighbour inherits a fraction of its anchor's score,
    so it ranks below the symbol that raised it but above names that match only
    incidentally: asking "what calls X" reaches X's callers even when their names
    share no query term at all. Only the top few anchors contribute, because a
    weakly matched symbol's neighbourhood is mostly noise.
    """
    direct = _symbol_scores(doc, query)
    if not direct:
        return []
    ranked = sorted(direct.items(), key=lambda kv: (-kv[1], kv[0]))
    scored = dict(direct)

    neighbours = {}
    for (sid, name), score in ranked[:max(0, int(anchors))]:
        key = _symbol_key(doc, sid, name)
        for other in _one_hop(doc, key):
            neighbours.setdefault(other, score)
    for key, score in neighbours.items():
        module, _, name = key.rpartition("#")
        sec = _resolve_module(doc, module)
        if sec is not None:
            scored.setdefault((sec.id, name), round(score * NEIGHBOUR_RATIO, 2))

    out, seen = [], set()
    for (sid, name), _score in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0])):
        if (sid, name) in seen:
            continue
        seen.add((sid, name))
        out.append((sid, name))
    return out


def _one_hop(doc: Doc, key: str) -> list:
    """Keys for everything `key` calls, plus everything that calls it."""
    callees, callers = _call_graph(doc)
    out = [_strip_span(k) for k in callees.get(key, ())]
    out.extend(_strip_span(label) for label in callers.get(key, ()))
    return out


def _call_neighbourhood(doc: Doc, targets: list) -> set:
    """Keys (module_rel#symbol) for the targets plus their immediate callers/callees.

    One hop in each direction: what the target calls, and what calls the target.
    Deeper edges stay out of the packed body; `tier impact` still reads the whole
    on-disk table when a caller needs the full graph.
    """
    if not targets:
        return set()
    wanted = set(targets)
    callees, callers = _call_graph(doc)
    for key in list(targets):
        wanted.update(callees.get(key, ()))
        wanted.update(_strip_span(label) for label in callers.get(key, ()))
    return wanted


def _strip_span(label: str) -> str:
    """Drop the trailing `` [start-end]`` that caller labels carry."""
    return _SPAN_SUFFIX_RE.sub("", label)


def _calls_filter_for(doc: Doc, targets: list, mode: str = "auto", module: str = "",
                      reach=None):
    """Build a row predicate for `|calls` tables, or None to keep them whole.

    Modes:
      ``auto``  keep only edges that touch the target symbols or their 1-hop
                neighbourhood, in either direction. That is the set a reader needs
                to see what the fetched symbol invokes *and* what invokes it,
                without dragging in every unrelated edge in the module.
      ``all``   leave the stored table untouched.
      ``none``  drop the table entirely.

    `module` is the repository-relative path of the section being rendered, used
    to attribute rows whose module cell is empty (a same-module call). `reach` is
    the precomputed ``_call_neighbourhood`` result, so a caller rendering several
    sections walks the graph once.
    """
    mode = (mode or "auto").lower()
    if mode == "all":
        return None
    if mode == "none":
        return lambda row: False
    wanted = set(targets)
    if not wanted:
        return None
    if reach is None:
        reach = _call_neighbourhood(doc, list(wanted))

    def keep(row, _reach=reach, _module=module):
        if len(row) < 3:
            return True
        caller, callee, target = row[0], row[1], row[2]
        home = target if target not in ("", ".") else _module
        if f"{home}#{callee}" in _reach:
            return True
        if f"{_module}#{caller}" in _reach:
            return True
        # The section's module is unknown when the helper is called directly, so
        # fall back to matching the caller against any module in reach.
        return not _module and any(k.endswith("#" + caller) for k in _reach)

    return keep


def _impact_block(doc: Doc, root: str, target_id: str, symbol: str = "") -> str:
    """Compact dependency summary for one module (and symbol, when given)."""
    try:
        res = impact(doc, f"{target_id}#{symbol}" if symbol else target_id, root=root)
    except (OSError, ValueError):
        return ""
    lines = []
    direct = [f"@{x}" for x in res.get("direct_dependents", [])]
    transitive = [f"@{x}" for x in res.get("transitive_dependents", [])]
    deps = [f"@{x}" for x in res.get("dependencies", [])]
    if direct:
        lines.append("Direct dependents: " + ", ".join(direct[:IMPACT_DIRECT]))
    if transitive:
        lines.append("Transitive dependents: " + ", ".join(transitive[:IMPACT_TRANSITIVE]))
    if deps:
        lines.append("Dependencies: " + ", ".join(deps[:IMPACT_DIRECT]))
    callers = res.get("symbol_dependents") or []
    if callers:
        lines.append("Symbol callers: " + ", ".join(callers[:IMPACT_DIRECT]))
    calls = res.get("call_dependencies") or []
    if calls:
        lines.append("Symbol calls: " + ", ".join(calls[:IMPACT_DIRECT]))
    if not lines:
        return ""
    head = f"## Impact: @{target_id}" + (f"#{symbol}" if symbol else "")
    return head + "\n" + "\n".join(lines)


def build_context(target=None, query: str = "", *, root=None, budget_tokens: int = DEFAULT_BUDGET,
                  include_impact: bool = True, session: Session = None,
                  session_key: str = "", dedup: bool = True, fast=None,
                  include_calls: str = "auto") -> dict:
    """Return ``{"text", "path", "doc", "sections", "slices", "tokens"}``."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required")
    budget = max(1, int(budget_tokens if budget_tokens is not None else DEFAULT_BUDGET))

    path, doc, work_root = ensure_doc(target, root=root, session=session,
                                      session_key=session_key or "", write=True)
    key = session_key or path

    hits = find(doc, query, HIT_COUNT)
    repo_mode = is_repo_doc(doc)

    # Route to symbols by name, then admit the 1-hop call collaborators of the
    # strongest matches. Without the second step a question phrased in terms of a
    # callee can never reach the caller, whose name may share no query term.
    symbols = _symbol_hits(doc, query) if repo_mode else []

    by_id = doc.by_id()
    order, seen_sections = [], set()
    for sid, _score, _abstract in hits:
        if sid in by_id and sid not in seen_sections:
            seen_sections.add(sid)
            order.append(sid)
    for sid, _name in symbols:
        if sid in by_id and sid not in seen_sections:
            seen_sections.add(sid)
            order.append(sid)
    order = order[:MAX_SECTIONS]

    # Narrow `|calls` tables to the matched symbols and their 1-hop neighbourhood
    # so a packed body carries the relevant edges, not every edge in the module.
    target_keys = [_symbol_key(doc, sid, name) for sid, name in symbols]
    reach = _call_neighbourhood(doc, target_keys) if target_keys else set()

    parts, used, omitted = [], 0, []
    # Metadata for each entry in `parts`, kept in lockstep so trimming can never
    # leave `slices`/`sections` advertising a block that was dropped from `text`.
    kinds = []

    # Build the impact summary first and hold its space. It is the block that
    # answers a "what calls X" question, so it must not be crowded out by module
    # bodies that happen to be packed earlier.
    impact_block = ""
    if include_impact and repo_mode and order:
        target_id, top_symbol = (symbols[0] if symbols else (order[0], ""))
        impact_block = _impact_block(doc, work_root, target_id, top_symbol)
    reserve = _count("\n\n" + impact_block, fast=fast) if impact_block else 0
    room = max(0, budget - reserve)

    header = ["## Context: " + _clip(query, 120)]
    head_text = "\n".join(header)
    parts.append(head_text)
    kinds.append(("header", None))
    used += _count(head_text, fast=fast)

    SEP = "\n\n"

    def fits(candidate: str, limit: int = None) -> bool:
        """Token cost of appending `candidate`, including its separator."""
        cap = budget if limit is None else limit
        return used + _count(SEP + candidate, fast=fast) <= cap

    for sid in order:
        calls_filter = _calls_filter_for(doc, target_keys, include_calls,
                                         module=_module_of(doc, sid), reach=reach)
        body = _render_section(doc, sid, session, key, dedup, calls_filter)
        if not fits(body, room):
            omitted.append(f"@{sid}")
            continue
        parts.append(body)
        kinds.append(("section", sid))
        used += _count(SEP + body, fast=fast)

    if repo_mode:
        used_syms = 0
        for sid, name in symbols:
            if used_syms >= MAX_SYMBOLS:
                break
            try:
                text, missing = slice_symbols(
                    doc, sid, [name], root=work_root, session=session,
                    session_key=key, force=not dedup,
                )
            except (OSError, ValueError):
                continue
            if not text or missing:
                continue
            if not fits(text, room):
                omitted.append(f"@{sid}#{name}")
                continue
            parts.append(text)
            kinds.append(("slice", f"{sid}#{name}"))
            used += _count(SEP + text, fast=fast)
            used_syms += 1

    if impact_block and fits(impact_block):
        parts.append(impact_block)
        kinds.append(("impact", None))
        used += _count(SEP + impact_block, fast=fast)

    if omitted:
        note = "Omitted for budget: " + ", ".join(omitted[:8])
        if fits(note):
            parts.append(note)
            kinds.append(("omitted", None))
            used += _count(SEP + note, fast=fast)

    def assemble():
        return "\n\n".join(p for p in parts if p), \
            [v for k, v in kinds if k == "section"], \
            [v for k, v in kinds if k == "slice"]

    text, sections_out, slices_out = assemble()
    if _count(text, fast=fast) > budget:
        # Separator or join drift: drop trailing blocks until the exact count fits.
        # `kinds` is trimmed with `parts`, so the reported metadata stays true.
        while parts and _count("\n\n".join(p for p in parts if p), fast=fast) > budget:
            parts.pop()
            kinds.pop()
        text, sections_out, slices_out = assemble()
    return {
        "text": text,
        "path": path,
        "doc": doc,
        "sections": sections_out,
        "slices": slices_out,
        "tokens": _count(text, fast=fast),
        "budget": budget,
    }


def _render_section(doc: Doc, sid: str, session: Session, key: str, dedup: bool,
                    calls_filter=None) -> str:
    """Render one section, or its stub when Session already delivered it."""
    if dedup and session is not None and session.is_marked(key, sid):
        return f"@{sid} (already in context; not resent)"
    body = render_section(doc.by_id()[sid], "raw", calls_filter)
    if dedup and session is not None:
        session.mark(key, sid)
    return body
