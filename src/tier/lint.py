from __future__ import annotations

import csv

from .models import TABLE_RE, _table_count
from .parser import _csv_cells, _data_closes, _data_open, _live_lines, nodes
from .tokens import count_tokens


def lint(doc: Doc, max_section_tokens: int = 800):
    issues = [("warn", w) for w in doc.warnings]
    seen, ids = set(), set(s.id for s in doc.sections)
    counts = count_tokens([s.text() for s in doc.sections])[0]
    for s, c in zip(doc.sections, counts):
        if s.id in seen:
            issues.append(("error", f"@{s.id}: duplicate section id"))
        seen.add(s.id)
        if s.id != "_" and not s.abstract:
            issues.append(("warn", f"@{s.id}: no '?' abstract (index falls back to title)"))
        if c > max_section_tokens:
            issues.append(("warn", f"@{s.id}: ~{c} tokens, consider splitting (>{max_section_tokens})"))
        L = s.lines
        i = 0
        while i < len(L):
            if L[i].startswith("```"):
                j = i + 1
                while j < len(L) and not L[j].startswith("```"):
                    j += 1
                i = j + 1
                continue
            if L[i].startswith("<<<"):
                _, guard = _data_open(L[i])
                j = i + 1
                while j < len(L) and not _data_closes(L[j], guard):
                    j += 1
                i = j + 1
                continue
            m = TABLE_RE.match(L[i])
            if m:
                n, ncols = _table_count(m.group(2)), len(m.group(3).split(","))
                rows = L[i + 1 : i + 1 + n]
                if len(rows) < n:
                    issues.append(("error", f"@{s.id}: table '{m.group(1)}' declares {n} rows, found fewer"))
                for r in rows:
                    try:
                        cells = _csv_cells(r, strict=True)
                    except csv.Error:
                        issues.append(("error", f"@{s.id}: table '{m.group(1)}' unparsable row: {r[:40]}"))
                        continue
                    if len(cells) != ncols:
                        issues.append(("error", f"@{s.id}: table '{m.group(1)}' row has wrong column count: {r[:40]}"))
                i += 1 + n
                continue
            i += 1
        for nd in nodes(s):
            if nd["t"] == "ref" and nd["id"] not in ids:
                issues.append(("error", f"@{s.id}: reference to unknown section '{nd['id']}'"))
    return issues
