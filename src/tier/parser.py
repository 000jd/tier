from __future__ import annotations

import csv
import os
import re

from .models import (
    Doc, Section, VERSION, SEC_RE, HDR_RE, TABLE_RE, TABLE_HEAD_RE, DATA_OPEN_RE, TABLE_MODES,
    SIGIL_CHARS, _csv_cells, _render_table, _table_rows, _table_count, render_lines,
)


def _data_open(line: str):
    """Return (source, guard) for a `<<<source [#guard]` opening line."""
    m = DATA_OPEN_RE.match(line)
    if not m:
        return line[3:].strip(), ""
    return m.group(1).strip(), (m.group(2) or "")


def _data_closes(line: str, guard: str) -> bool:
    if guard:
        return line.rstrip() == ">>>" + guard
    return line.startswith(">>>")


def wrap_untrusted(source: str, content: str) -> str:
    """Wrap arbitrary external text in an untrusted block that cannot be escaped.

    A deterministic named guard is chosen that does not occur as a closing
    line inside the payload, so embedded `>>>` sequences stay inert data.
    """
    src = re.sub(r"\s+", "-", str(source).strip()) or "data"
    lines = content.replace("\r\n", "\n").split("\n")
    closers = {l.rstrip() for l in lines}
    i = 1
    while f">>>g{i}" in closers:
        i += 1
    guard = f"g{i}"
    return "\n".join([f"<<<{src} #{guard}", *lines, ">>>" + guard])


def parse(text: str) -> Doc:
    doc = Doc()
    cur = Section("_")
    mode = None  # None | "fence" | "data"
    mode_guard = ""
    pending = 0  # table rows still to consume verbatim (declared count is authoritative)
    pending_capped = False  # declared count exceeded the remaining input
    body = text.lstrip("\ufeff").replace("\r\n", "\n")
    if body.endswith("\n"):
        body = body[:-1]
    raw_lines = body.split("\n")
    for n, raw in enumerate(raw_lines, 1):
        line = raw.rstrip()
        if pending:
            if pending_capped and (SEC_RE.match(line) or HDR_RE.match(line)):
                pending, pending_capped = 0, False
            else:
                cur.lines.append(line)
                pending -= 1
                if not pending:
                    pending_capped = False
                continue
        if mode == "fence":
            cur.lines.append(line)
            if line.startswith("```"):
                mode = None
            continue
        if mode == "data":
            cur.lines.append(line)
            if _data_closes(line, mode_guard):
                mode = None
                mode_guard = ""
            continue
        if not line.strip() or line.startswith(";"):
            continue
        if line.startswith("```"):
            mode = "fence"
            cur.lines.append(line)
            continue
        if line.startswith("<<<"):
            mode = "data"
            mode_guard = _data_open(line)[1]
            cur.lines.append(line)
            continue
        c = line[0]
        if c == "%":
            m = HDR_RE.match(line)
            if not m:
                doc.warnings.append(f"line {n}: malformed header")
                continue
            k, v = m.group(1), (m.group(2) or "").strip()
            if k == "rule":
                doc.rules.append(v)
            else:
                doc.meta[k] = v
            continue
        if c == "@":
            m = SEC_RE.match(line)
            if m:
                if cur.id != "_" or cur.lines:
                    doc.sections.append(cur)
                cur = Section(m.group(1), (m.group(2) or "").strip(), line_no=n)
                continue
            doc.warnings.append(f"line {n}: bad section line (use \\@ to escape)")
        if c == "?" and cur.id != "_" and not cur.abstract and not cur.lines:
            cur.abstract = line[1:].strip()
            continue
        cur.lines.append(line)
        tm = TABLE_RE.match(line)
        if tm:
            declared = _table_count(tm.group(2))
            remaining = len(raw_lines) - n
            pending = min(declared, remaining)
            pending_capped = declared > remaining
            if pending_capped:
                doc.warnings.append(
                    f"line {n}: table '{tm.group(1)}' declares {declared} rows, "
                    f"only {remaining} line(s) remain; capped to {remaining}"
                )
        elif TABLE_HEAD_RE.match(line):
            doc.warnings.append(
                f"line {n}: malformed table header (expected |name[N]{{cols}}); treated as prose"
            )
    if mode:
        doc.warnings.append(f"unterminated {mode} block")
    if cur.id != "_" or cur.lines:
        doc.sections.append(cur)
    return doc


def _kv_split(s: str):
    k, _, v = s.strip().partition(" ")
    return k, v.strip()


def _parse_kv(first: str, kids: list) -> dict:
    items = [(0, *_kv_split(first))]
    for x in kids:
        depth = min((len(x) - len(x.lstrip(" "))) // 2, 64)
        items.append((depth, *_kv_split(x)))

    def build(idx, depth):
        d = {}
        while idx < len(items):
            dep, k, v = items[idx]
            if dep < depth:
                break
            if dep > depth:  # malformed jump in indentation: skip safely
                idx += 1
                continue
            idx += 1
            if idx < len(items) and items[idx][0] > dep:
                sub, idx = build(idx, dep + 1)
                d[k] = ({"_": v, **sub} if v else sub)
            else:
                d[k] = v
        return d, idx

    return build(0, 0)[0]


def nodes(sec: Section) -> list:
    """Parse a section body into typed nodes."""
    L, out, i = sec.lines, [], 0
    while i < len(L):
        l = L[i]
        m = TABLE_RE.match(l)
        if m:
            name, n = m.group(1), _table_count(m.group(2))
            cols = [c.strip() for c in m.group(3).split(",")]
            rows = [_csv_cells(r) for r in L[i + 1 : i + 1 + n]]
            out.append({"t": "table", "name": name, "cols": cols, "rows": rows, "n": n})
            i += 1 + n
            continue
        if l.startswith("```"):
            j = i + 1
            while j < len(L) and not L[j].startswith("```"):
                j += 1
            out.append({"t": "code", "lang": l[3:].strip(), "text": "\n".join(L[i + 1 : j])})
            i = j + 1
            continue
        if l.startswith("<<<"):
            src, guard = _data_open(l)
            j = i + 1
            while j < len(L) and not _data_closes(L[j], guard):
                j += 1
            out.append({"t": "data", "src": src, "text": "\n".join(L[i + 1 : j]), "guard": guard})
            i = j + 1
            continue
        if l.startswith("= "):
            j = i + 1
            while j < len(L) and L[j].startswith("  "):
                j += 1
            out.append({"t": "kv", "data": _parse_kv(l[2:], L[i + 1 : j])})
            i = j
            continue
        if l.startswith("! "):
            out.append({"t": "rule", "text": l[2:]})
        elif l.startswith("$ "):
            out.append({"t": "cmd", "text": l[2:]})
        elif l.startswith("> "):
            ref, _, note = l[2:].strip().partition(" ")
            out.append({"t": "ref", "id": ref, "note": note.strip()})
        elif l.startswith("\\") and len(l) > 1 and l[1] in SIGIL_CHARS:
            out.append({"t": "text", "text": l[1:]})
        else:
            out.append({"t": "text", "text": l})
        i += 1
    return out


def _live_lines(lines: list) -> list:
    """Lines at structural level: skips opaque fences, data blocks and table rows."""
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            i = j + 1
            continue
        if lines[i].startswith("<<<"):
            _, guard = _data_open(lines[i])
            j = i + 1
            while j < len(lines) and not _data_closes(lines[j], guard):
                j += 1
            i = j + 1
            continue
        m = TABLE_RE.match(lines[i])
        if m:
            out.append(lines[i])
            i += 1 + _table_count(m.group(2))
            continue
        out.append(lines[i])
        i += 1
    return out


def render_section(sec: Section, table_mode: str = "raw", calls_filter=None) -> str:
    return sec.text(table_mode, calls_filter)


def to_obj(doc: Doc) -> dict:
    return {
        "meta": doc.meta,
        "rules": doc.rules,
        "sections": [
            {"id": s.id, "title": s.title, "abstract": s.abstract, "nodes": nodes(s)}
            for s in doc.sections
        ],
    }


def dumps(doc: Doc, pretty: bool = False) -> str:
    def one_line(value) -> str:
        return str(value).replace("\r", " ").replace("\n", " ").strip()

    out = ["%tier " + VERSION]
    meta = dict(doc.meta)
    meta.pop("tier", None)
    for k in ("title", "sum"):
        if k in meta:
            out.append(f"%{k} {one_line(meta.pop(k))}")
    for k, v in meta.items():
        out.append(f"%{k} {one_line(v)}")
    for r in doc.rules:
        out.append(f"%rule {one_line(r)}")
    for s in doc.sections:
        if pretty:
            out.append("")
        if s.id != "_":
            title = one_line(s.title)
            out.append(f"@{s.id}" + (f" {title}" if title else ""))
            abstract = one_line(s.abstract)
            if abstract:
                out.append("? " + abstract)
        out += s.lines
    return "\n".join(out) + "\n"


def _repo_file_meta(sec: Section) -> dict:
    for nd in nodes(sec):
        if nd["t"] != "kv":
            continue
        data = nd["data"]
        if isinstance(data.get("file"), dict):
            data = data["file"]
        if isinstance(data, dict) and data.get("path"):
            return data
    return {}


def is_repo_doc(doc: Doc) -> bool:
    return any(bool(_repo_file_meta(s)) for s in doc.sections)


def _load(path: str) -> Doc:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())
