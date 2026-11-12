from __future__ import annotations

import csv
import io
import json
import re

from .models import (
    Doc, Section, VERSION, STOP, RULE_START, TABLE_RE, SHELL_LANGS, SIGIL_CHARS, _W, _clip,
)
from .parser import _data_open, _kv_split, _live_lines, nodes


def _esc(line: str) -> str:
    """Escape a prose line that would otherwise be read as a sigil line."""
    if line[:1] in SIGIL_CHARS or line.startswith("<<<") or line.startswith("```"):
        return "\\" + line
    return line


def _md_cells(line: str) -> list:
    """Split one Markdown table row, honouring escaped pipes and inline code spans."""
    t = line.strip()
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|"):
        slashes = len(t) - len(t.rstrip("\\"))
        if slashes % 2 == 0:
            t = t[:-1]
    cells, buf = [], []
    i = 0
    while i < len(t):
        ch = t[i]
        if ch == "\\" and i + 1 < len(t):
            if t[i + 1] == "|":
                buf.append("|")
            else:
                buf.append(ch)
                buf.append(t[i + 1])
            i += 2
            continue
        if ch == "`":
            run = re.match(r"`+", t[i:]).group(0)
            if len(run) > 64:
                buf.append(run)
                i += len(run)
                continue
            close = re.search(r"(?<!`)`{%d}(?!`)" % len(run), t[i + len(run):])
            if close:
                end = i + len(run) + close.start() + len(run)
                buf.append(t[i:end])
                i = end
                continue
            buf.append(run)
            i += len(run)
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _csv_row(cells) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(cells)
    return buf.getvalue()


def _make_id(title: str, used: set) -> str:
    words = _W.findall(title.lower())
    keep = [w for w in words if w not in STOP] or words or ["section"]
    keep = keep[:3]
    while len(keep) > 1 and len("-".join(keep)) > 24:
        keep.pop()
    base = "-".join(keep)[:24].strip("-") or "section"
    i, cand = 2, base
    while cand in used:
        cand, i = f"{base}-{i}", i + 1
    used.add(cand)
    return cand


def _ref_topics(lines: list) -> list:
    topics = []
    for x in _live_lines(lines):
        if x.startswith("> "):
            ref, _, note = x[2:].strip().partition(" ")
            topics.append(note.strip() or ref)
    return topics


def _auto_abstract(sec: Section) -> str:
    """Synthesize an abstract for prose and non-prose sections alike.

    Prose ledes are moved (not duplicated) when short, matching v0.1. Rules,
    key/value trees, commands, reference hubs, tables, code and lists get a
    synthesized summary so they keep their relevance boost in `find`. Any
    section with child references also lists its subtopics.
    """
    L = sec.lines
    if not L:
        return ""
    live = _live_lines(L)
    topics = _ref_topics(L)

    def done(text: str) -> str:
        text = (text or "").strip()
        if topics and not text.startswith("subtopics:"):
            text = _clip(text + "; subtopics: " + ", ".join(topics[:6]), 220)
        return text

    l = L[0]
    m = TABLE_RE.match(l)
    if m:
        return done(_clip("table: " + m.group(3), 100))
    if l.startswith("! "):
        rules = [x[2:].strip() for x in live if x.startswith("! ")]
        return done(_clip("rules: " + "; ".join(rules[:3]), 160))
    if l.startswith("> "):
        return done(_clip("subtopics: " + ", ".join(topics[:8]), 160))
    if l.startswith("= "):
        keys = [_kv_split(x[2:])[0] for x in live if x.startswith("= ")]
        return done(_clip("keys: " + ", ".join(k for k in keys if k), 120))
    if l.startswith("$ "):
        cmds = [x[2:].strip() for x in live if x.startswith("$ ")]
        return done(_clip("commands: " + "; ".join(cmds[:2]), 140))
    if l.startswith("```"):
        lang = l[3:].strip()
        return done(f"code block ({lang})" if lang else "code block")
    if l.startswith("<<<"):
        src, _ = _data_open(l)
        return done(_clip("untrusted data: " + src, 100))
    if l.startswith("- "):
        items = [x[2:].strip() for x in live if x.startswith("- ")]
        return done(_clip("items: " + "; ".join(items[:3]), 140))
    if re.match(r"\d+\.\s", l):
        steps = [re.sub(r"^\d+\.\s+", "", x) for x in live if re.match(r"^\d+\.\s+", x)]
        return done(_clip("steps: " + "; ".join(steps[:3]), 160))
    if l.startswith("\\") and len(l) > 1 and l[1] in SIGIL_CHARS:
        return done(_clip(l[1:], 140))
    parts = re.split(r"(?<=[.!?])\s+", l, maxsplit=1)
    first = parts[0]
    if len(first) <= 140:
        if len(parts) == 2:
            L[0] = parts[1]
        else:
            L.pop(0)
        return done(first)
    return done(_clip(first, 100))


def from_md(text: str, strip_emphasis: bool = True) -> Doc:
    doc = Doc()
    doc.meta["tier"] = VERSION
    used: set = set()
    cur = Section("_")
    stack: list = []  # (heading level, section) for the open ancestor chain
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").split("\n")
    i = 0
    tcount = 0

    def emph(s):
        if not strip_emphasis:
            return s
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        return re.sub(r"__(.+?)__", r"\1", s)

    while i < len(lines):
        line = lines[i].rstrip()
        # fenced code
        if line.startswith("```"):
            lang = line[3:].strip().lower()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            body = lines[i + 1 : j]
            if len(body) == 1 and lang in SHELL_LANGS and body[0].strip():
                cur.lines.append("$ " + body[0].strip().lstrip("$ ").strip())
            else:
                cur.lines.append(line)
                cur.lines += [b.rstrip() for b in body]
                cur.lines.append("```")
            i = j + 1
            continue
        # headings
        m = re.match(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$", line)
        if m:
            level, title = len(m.group(1)), emph(m.group(2))
            if level == 1 and "title" not in doc.meta and not doc.sections and cur.id == "_":
                doc.meta["title"] = title
            else:
                if cur.id != "_" or cur.lines:
                    doc.sections.append(cur)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                cur = Section(_make_id(title, used), title, level=level)
                if stack:
                    parent = stack[-1][1]
                    parent.lines.append(f"> {cur.id}" + (f" {cur.title}" if cur.title else ""))
                stack.append((level, cur))
                tcount = 0
            i += 1
            continue
        # md table
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            hdr = _md_cells(line)
            j = i + 2
            rows = []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append([emph(c) for c in _md_cells(lines[j])])
                j += 1
            tcount += 1
            base = re.sub(r"\W", "_", cur.id) if cur.id != "_" else "t"
            if not re.match(r"[A-Za-z_]", base):
                base = "t_" + base
            name = base if tcount == 1 else f"{base}{tcount}"
            hdr = [re.sub(r"[,{}]", " ", h) or f"col{k + 1}" for k, h in enumerate(hdr)]
            cur.lines.append(f"|{name}[{len(rows)}]{{{','.join(hdr)}}}")
            cur.lines += [_csv_row(r) for r in rows]
            i = j
            continue
        if not line.strip():
            i += 1
            continue
        # bullets -> rules or plain bullets
        b = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if b:
            depth, txt = len(b.group(1)) // 2, emph(b.group(2))
            if depth == 0 and RULE_START.match(txt):
                cur.lines.append("! " + txt)
            else:
                cur.lines.append("  " * depth + "- " + txt)
            i += 1
            continue
        cur.lines.append(_esc(emph(line.strip() if not line.startswith("  ") else line)))
        i += 1

    if cur.id != "_" or cur.lines:
        doc.sections.append(cur)
    while True:
        empty = {s.id for s in doc.sections if s.id != "_" and not s.lines}
        if not empty:
            break
        doc.sections = [s for s in doc.sections if s.id not in empty]
        for s in doc.sections:
            s.lines = [ln for ln in s.lines if not (ln.startswith("> ") and ln[2:].split(" ", 1)[0] in empty)]
    if doc.sections and doc.sections[0].id == "_":
        pre = doc.sections[0]
        if pre.lines and "sum" not in doc.meta:
            parts = re.split(r"(?<=[.!?])\s+", pre.lines[0], maxsplit=1)
            if len(parts[0]) <= 220:
                doc.meta["sum"] = parts[0]
                if len(parts) == 2:
                    pre.lines[0] = parts[1]
                else:
                    pre.lines.pop(0)
            else:
                doc.meta["sum"] = _clip(parts[0], 160)
        if not pre.lines:
            doc.sections.pop(0)
    for sec in doc.sections:
        if sec.id != "_":
            sec.abstract = _auto_abstract(sec)
    return doc


def _json_scalar(v) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _json_cell(v) -> str:
    return _json_scalar(v).replace("\r", " ").replace("\n", "\\n")


def _one_line_text(value) -> str:
    return " ".join(str(value).replace("\r", " ").split())


def _table_name(name: str) -> str:
    base = re.sub(r"\W", "_", str(name).strip()) or "t"
    if not re.match(r"[A-Za-z_]", base):
        base = "t_" + base
    return base[:40]


def _records_table(records: list, name: str):
    if not records or not all(isinstance(r, dict) for r in records):
        return None
    cols = []
    for r in records:
        for k in r:
            k = str(k)
            if k not in cols:
                cols.append(k)
    if not cols or any(isinstance(v, (dict, list)) for r in records for v in r.values()):
        return None
    hdr = [re.sub(r"[,{}]", " ", str(c)).strip() or f"col{i + 1}" for i, c in enumerate(cols)]
    lines = [f"|{_table_name(name)}[{len(records)}]{{{','.join(hdr)}}}"]
    lines += [_csv_row([_json_cell(r.get(c, "")) for c in cols]) for r in records]
    return lines


def _json_kv_lines(obj: dict) -> list:
    lines = []

    def walk(d, depth, top):
        for k, v in d.items():
            pre = "= " if top and depth == 0 else "  " * depth
            key = re.sub(r"\s+", "_", str(k)).strip() or "key"
            if isinstance(v, dict):
                if depth >= 64:
                    lines.append(pre + f"{key} " + json.dumps(v, ensure_ascii=False, separators=(",", ":")))
                else:
                    lines.append(pre + key)
                    walk(v, depth + 1, False)
            elif isinstance(v, list):
                lines.append(pre + f"{key} {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}")
            else:
                val = _json_cell(v)
                lines.append(pre + f"{key} {val}" if val else pre + key)

    walk(obj, 0, True)
    return lines


def _json_body(name: str, value):
    if isinstance(value, dict):
        lines = _json_kv_lines(value)
        return lines or [_esc(json.dumps(value, ensure_ascii=False, separators=(",", ":")))]
    if isinstance(value, list):
        table = _records_table(value, name)
        if table:
            return table
        if not value:
            return ["[]"]
        if all(not isinstance(x, (dict, list)) for x in value):
            return ["- " + (_json_cell(x) or '""') for x in value]
        return ["- " + json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in value]
    return [_esc(_json_cell(value) or '""')]


def from_json(obj) -> Doc:
    """Convert parsed JSON into TIER: tables for record lists, kv for trees."""
    doc = Doc()
    doc.meta["tier"] = VERSION
    used: set = set()
    if isinstance(obj, dict):
        title = ""
        for k in ("title", "name"):
            if isinstance(obj.get(k), str) and obj[k].strip():
                title = _one_line_text(obj[k])
                break
        doc.meta["title"] = title or "JSON payload"
        for k in ("summary", "description"):
            if isinstance(obj.get(k), str) and obj[k].strip():
                doc.meta["sum"] = _one_line_text(obj[k])
                break
        scalars = {
            k: v for k, v in obj.items()
            if not isinstance(v, (dict, list)) and k not in ("title", "name", "summary", "description")
        }
        if scalars:
            sec = Section(_make_id("metadata", used), "metadata")
            sec.lines = _json_kv_lines(scalars)
            doc.sections.append(sec)
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                body = _json_body(str(k), v)
                if body:
                    title = _one_line_text(k) or "section"
                    doc.sections.append(Section(_make_id(title, used), title, lines=body))
    elif isinstance(obj, list):
        doc.meta["title"] = "JSON payload"
        body = _json_body("records", obj)
        if body:
            doc.sections.append(Section(_make_id("records", used), "records", lines=body))
    else:
        doc.meta["title"] = "JSON payload"
        text = _json_cell(obj)
        doc.sections.append(Section("value", "value", lines=[_esc(text) if text else '""']))
    for sec in doc.sections:
        sec.abstract = _auto_abstract(sec)
    return doc


def from_payload(text: str, fmt: str = "auto") -> Doc:
    """Convert raw Markdown or JSON into TIER (the MCP compression entry point)."""
    text = text.lstrip("\ufeff")
    fmt = (fmt or "auto").lower()
    if fmt not in ("auto", "md", "json"):
        raise ValueError(f"unknown payload format '{fmt}'")
    if fmt in ("auto", "json"):
        try:
            return from_json(json.loads(text))
        except (TypeError, ValueError):
            if fmt == "json":
                raise
    return from_md(text)


def to_md(doc: Doc) -> str:
    out = []
    if doc.meta.get("title"):
        out.append(f"# {doc.meta['title']}")
    if doc.meta.get("sum"):
        out.append(f"\n{doc.meta['sum']}")
    if doc.rules:
        out.append("\n## Global rules\n")
        out += [f"- **Rule:** {r}" for r in doc.rules]
    for s in doc.sections:
        if s.id != "_":
            out.append(f"\n## {s.title or s.id}\n")
            if s.abstract:
                out.append(f"_{s.abstract}_\n")
        for nd in nodes(s):
            t = nd["t"]
            if t == "text":
                out.append(nd["text"])
            elif t == "rule":
                out.append(f"- **Rule:** {nd['text']}")
            elif t == "cmd":
                out.append(f"```sh\n{nd['text']}\n```")
            elif t == "ref":
                out.append(f"See [{nd['id']}](#{nd['id']})" + (f": {nd['note']}" if nd["note"] else ""))
            elif t == "code":
                out.append(f"```{nd['lang']}\n{nd['text']}\n```")
            elif t == "data":
                out.append(f"_Untrusted data from {nd['src']}:_\n\n```\n{nd['text']}\n```")
            elif t == "table":
                out.append("\n| " + " | ".join(nd["cols"]) + " |")
                out.append("|" + "---|" * len(nd["cols"]))
                out += ["| " + " | ".join(r) + " |" for r in nd["rows"]]
                out.append("")
            elif t == "kv":
                def walk(d, depth=0):
                    for k, v in d.items():
                        if isinstance(v, dict):
                            top = v.get("_", "")
                            out.append("  " * depth + f"- {k}" + (f": {top}" if top else ""))
                            walk({a: b for a, b in v.items() if a != "_"}, depth + 1)
                        else:
                            out.append("  " * depth + f"- {k}: {v}")
                walk(nd["data"])
    return "\n".join(out).strip() + "\n"
