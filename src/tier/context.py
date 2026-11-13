"""One-turn composite retrieval: index, route, and pack in a single call.

`build_context` answers a question about a document or repository without the
caller having to sequence `find` -> `get` -> `slice` -> `impact` itself. It
resolves the target (building or syncing the map if needed), routes with the
hybrid retriever, matches symbols by name, then packs L2 bodies, L3 slices,
and a dependency summary until the token budget is reached.
"""
from __future__ import annotations

from .cache import ensure_doc
from .models import Doc, _clip
from .parser import is_repo_doc, render_section
from .repo import _symbol_rows, impact, slice_symbols
from .retrieval import Session, _index_tokens, find
from .tokens import count_tokens

DEFAULT_BUDGET = 3000
HIT_COUNT = 8
MAX_SYMBOLS = 3
MAX_SECTIONS = 3

IMPACT_DIRECT = 6
IMPACT_TRANSITIVE = 10


def _count(text: str, fast=None) -> int:
    return count_tokens([text], fast=fast)[0][0] if text else 0


def _symbol_hits(doc: Doc, query: str) -> list:
    """Rank (section_id, symbol_name) pairs by query-token overlap with names."""
    terms = [t for t in _index_tokens(query) if len(t) >= 2]
    if not terms:
        return []
    scored = []
    for sec in doc.sections:
        for row in _symbol_rows(sec):
            name = str(row.get("name") or "")
            if not name:
                continue
            lowered = name.lower()
            leaf = lowered.rsplit(".", 1)[-1]
            score = 0.0
            for term in terms:
                if leaf == term or lowered == term:
                    score += 3.0
                elif leaf.startswith(term) or lowered.startswith(term):
                    score += 2.0
                elif term in lowered:
                    score += 1.0
            if score:
                scored.append((round(score, 2), sec.id, name))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    out, seen = [], set()
    for _score, sid, name in scored:
        key = (sid, name)
        if key in seen:
            continue
        seen.add(key)
        out.append((sid, name))
    return out


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
                  session_key: str = "", dedup: bool = True, fast=None) -> dict:
    """Return ``{"text", "path", "doc", "sections", "slices", "tokens"}``."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required")
    budget = max(1, int(budget_tokens if budget_tokens is not None else DEFAULT_BUDGET))

    path, doc, work_root = ensure_doc(target, root=root, session=session,
                                      session_key=session_key or "", write=True)
    key = session_key or path

    hits = find(doc, query, HIT_COUNT)
    symbols = _symbol_hits(doc, query) if is_repo_doc(doc) else []
    repo_mode = is_repo_doc(doc)

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

    parts, used, omitted = [], 0, []
    sections_out, slices_out = [], []

    header = ["## Context: " + _clip(query, 120)]
    head_text = "\n".join(header)
    parts.append(head_text)
    used += _count(head_text, fast=fast)

    SEP = "\n\n"

    def fits(candidate: str) -> bool:
        """Token cost of appending `candidate`, including its separator."""
        return used + _count(SEP + candidate, fast=fast) <= budget

    for sid in order:
        body = _render_section(doc, sid, session, key, dedup)
        if not fits(body):
            omitted.append(f"@{sid}")
            continue
        parts.append(body)
        used += _count(SEP + body, fast=fast)
        sections_out.append(sid)

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
            if not fits(text):
                omitted.append(f"@{sid}#{name}")
                continue
            parts.append(text)
            used += _count(SEP + text, fast=fast)
            slices_out.append(f"{sid}#{name}")
            used_syms += 1

    if include_impact and repo_mode and order:
        top_symbol = ""
        for sid, name in symbols:
            if sid == order[0]:
                top_symbol = name
                break
        block = _impact_block(doc, work_root, order[0], top_symbol)
        if block and fits(block):
            parts.append(block)
            used += _count(SEP + block, fast=fast)

    if omitted:
        note = "Omitted for budget: " + ", ".join(omitted[:8])
        if fits(note):
            parts.append(note)
            used += _count(SEP + note, fast=fast)

    text = "\n\n".join(p for p in parts if p)
    if _count(text, fast=fast) > budget:
        # Separator or join drift: drop trailing blocks until the exact count fits.
        while parts and _count("\n\n".join(p for p in parts if p), fast=fast) > budget:
            parts.pop()
            if sections_out:
                sections_out.pop()
        text = "\n\n".join(p for p in parts if p)
    return {
        "text": text,
        "path": path,
        "doc": doc,
        "sections": sections_out,
        "slices": slices_out,
        "tokens": _count(text, fast=fast),
        "budget": budget,
    }


def _render_section(doc: Doc, sid: str, session: Session, key: str, dedup: bool) -> str:
    """Render one section, or its stub when Session already delivered it."""
    if dedup and session is not None and session.is_marked(key, sid):
        return f"@{sid} (already in context; not resent)"
    body = render_section(doc.by_id()[sid])
    if dedup and session is not None:
        session.mark(key, sid)
    return body
