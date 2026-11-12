"""Core data model, constants, and filesystem helpers for TIER."""
from __future__ import annotations

import csv
import os
import re
import tempfile
from dataclasses import dataclass, field

VERSION = "1.0.0"
SIGIL_CHARS = "%@?!=|>$;\\"
SEC_RE = re.compile(r"^@([a-z0-9][a-z0-9_.-]*)(?:[ \t]+(.*))?$")
HDR_RE = re.compile(r"^%([a-z][a-z0-9_-]*)(?:[ \t]+(.*))?$")
TABLE_RE = re.compile(r"^\|([A-Za-z_][\w.-]*)\[(\d+)\]\{([^}]*)\}\s*$")
DATA_OPEN_RE = re.compile(r"^<<<(.*?)(?:[ \t]+#([A-Za-z0-9_.-]+))?[ \t]*$")
RULE_START = re.compile(r"^(never|always|do not|don't|must not|must|only)\b", re.I)
STOP = {"and", "the", "a", "an", "of", "to", "for", "in", "with", "is", "are"}
SHELL_LANGS = {"", "bash", "sh", "shell", "console", "zsh"}
TABLE_MODES = ("raw", "aligned", "anchored")
_W = re.compile(r"[a-z0-9]+")


def _table_count(raw) -> int:
    """Parse a declared row count without large-int conversion limits or allocations."""
    text = str(raw).lstrip("0") or "0"
    if len(text) > 9:
        return 10 ** 9
    return int(text)


def _csv_cells(line: str, strict: bool = False) -> list:
    """Parse one CSV table row. NUL is legal in TIER text but not in Python's csv module."""
    if not line:
        return []
    safe = line.replace("\x00", "\ufffd")
    try:
        return next(csv.reader([safe], strict=strict))
    except (csv.Error, StopIteration):
        if strict:
            raise
        return [safe]


def _table_rows(lines: list, i: int, n: int) -> list:
    return [_csv_cells(r) for r in lines[i + 1 : i + 1 + n]]


def _render_table(name: str, cols: list, rows: list, mode: str) -> list:
    if mode == "aligned":
        widths = [max(3, len(c), *(len(r[j]) if j < len(r) else 0 for r in rows)) for j, c in enumerate(cols)]
        out = ["| " + " | ".join(c.ljust(widths[j]) for j, c in enumerate(cols)) + " |"]
        out.append("| " + " | ".join("-" * widths[j] for j in range(len(cols))) + " |")
        for r in rows:
            out.append("| " + " | ".join((r[j] if j < len(r) else "").ljust(widths[j]) for j in range(len(cols))) + " |")
        return out
    out = [f"table {name} ({len(rows)} rows; columns: {', '.join(cols)})"]
    for idx, r in enumerate(rows):
        anchor = r[0] if r and r[0] else str(idx + 1)
        cells = [f"{c}: {r[j] if j < len(r) else ''}" for j, c in enumerate(cols)]
        out.append(f"[{anchor}] " + " | ".join(cells))
        if len(rows) > 20 and (idx + 1) % 20 == 0 and idx + 1 < len(rows):
            out.append(f"columns: {', '.join(cols)}")
    return out


def render_lines(lines: list, table_mode: str = "raw") -> list:
    """Render section lines, optionally expanding tables for LLM consumption."""
    if table_mode not in TABLE_MODES:
        raise ValueError(f"unknown table mode '{table_mode}' (use {', '.join(TABLE_MODES)})")
    if table_mode == "raw":
        return list(lines)
    out, i = [], 0
    while i < len(lines):
        m = TABLE_RE.match(lines[i])
        if m:
            name, n = m.group(1), _table_count(m.group(2))
            cols = [c.strip() for c in m.group(3).split(",")]
            out += _render_table(name, cols, _table_rows(lines, i, n), table_mode)
            i += 1 + n
            continue
        out.append(lines[i])
        i += 1
    return out


def _clip(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(" ,;:.") + "..."


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _atomic_write(path: str, text: str) -> None:
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    mode = None
    if os.path.exists(path):
        try:
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(prefix=".tier-", suffix=".tmp", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _inside(root: str, path: str) -> bool:
    """True when an already-resolved path stays inside an already-resolved root."""
    prefix = root if root.endswith(os.sep) else root + os.sep
    return path == root or path.startswith(prefix)


@dataclass
class Section:
    id: str
    title: str = ""
    abstract: str = ""
    lines: list = field(default_factory=list)
    line_no: int = 0
    level: int = 0

    def text(self, table_mode: str = "raw") -> str:
        """What `get` returns for this section: header, abstract, body."""
        out = []
        if self.id != "_":
            out.append(f"@{self.id}" + (f" {self.title}" if self.title else ""))
            if self.abstract:
                out.append("? " + self.abstract)
        out += render_lines(self.lines, table_mode)
        return "\n".join(out)


@dataclass
class Doc:
    meta: dict = field(default_factory=dict)
    rules: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def by_id(self) -> dict:
        return {s.id: s for s in self.sections}
