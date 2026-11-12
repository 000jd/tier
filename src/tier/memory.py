from __future__ import annotations

import os

from .lint import lint
from .models import Doc, Section, VERSION, SEC_RE, TABLE_RE, _atomic_write, _table_count
from .parser import _data_closes, _data_open, _load, dumps, parse


def _validate_section_body(lines: list):
    mode, guard, pending = None, "", 0
    for line in lines:
        if pending:
            pending -= 1
            continue
        if mode == "fence":
            if line.startswith("```"):
                mode = None
            continue
        if mode == "data":
            if _data_closes(line, guard):
                mode, guard = None, ""
            continue
        if line.startswith("```"):
            mode = "fence"
            continue
        if line.startswith("<<<"):
            mode, guard = "data", _data_open(line)[1]
            continue
        m = TABLE_RE.match(line)
        if m:
            pending = _table_count(m.group(2))
            continue
        if line.startswith(("@", "%", "?")):
            raise ValueError("section body must not contain @, %, or ? structural lines")
    if mode:
        raise ValueError(f"section body has an unterminated {mode} block")


def remember(path: str, action: str, section: str = "", title: str = "", abstract: str = "",
             body=None, text: str = "", key: str = "", value: str = "", target: str = "",
             note: str = "") -> dict:
    """Atomically add a rule, upsert a section, or append a rule/key/ref."""
    path = os.path.abspath(path)
    action = (action or "").strip().lower().replace("_", "-")
    aliases = {"global-rule": "rule", "key": "append-kv", "ref": "append-ref",
               "append-key": "append-kv", "upsert-section": "section"}
    action = aliases.get(action, action)
    if action not in ("rule", "section", "append-rule", "append-kv", "append-ref"):
        raise ValueError(f"unknown memory action '{action}'")

    exists = os.path.exists(path)
    if not exists and action.startswith("append-"):
        raise ValueError(f"no such TIER memory file: {path}")
    if exists:
        doc = _load(path)
    else:
        doc = Doc(meta={"tier": VERSION, "title": os.path.splitext(os.path.basename(path))[0].replace("-", " ").title()})

    def one_line(s):
        return " ".join(str(s or "").split())

    sid = ""
    if action == "rule":
        rule = one_line(text).lstrip("! ").strip()
        if not rule:
            raise ValueError("rule text is required")
        if rule not in doc.rules:
            doc.rules.append(rule)
        message = f"added global rule to {path}"
    elif action == "section":
        if not SEC_RE.match("@" + section):
            raise ValueError("section id must match [a-z0-9][a-z0-9_.-]*")
        by = doc.by_id()
        old = by.get(section)
        abstract = one_line(abstract) or (old.abstract if old else "")
        if not abstract:
            raise ValueError("section upsert requires an abstract")
        body_lines = None
        if body is not None:
            raw_lines = body.splitlines() if isinstance(body, str) else list(body)
            body_lines = [str(x).replace("\r", "").rstrip() for x in raw_lines]
            body_lines = [x for x in body_lines if x.strip()]
            _validate_section_body(body_lines)
        if old is None:
            doc.sections.append(Section(section, one_line(title) or section, abstract, lines=body_lines or []))
            message = f"added @{section} to {path}"
        else:
            if title:
                old.title = one_line(title)
            old.abstract = abstract
            if body_lines is not None:
                old.lines = body_lines
            message = f"updated @{section} in {path}"
        sid = section
    elif action == "append-rule":
        sec = doc.by_id().get(section)
        if sec is None:
            raise ValueError(f"no such section: {section}")
        rule = one_line(text).lstrip("! ").strip()
        if not rule:
            raise ValueError("rule text is required")
        line = "! " + rule
        if line not in sec.lines:
            sec.lines.append(line)
        sid, message = section, f"appended rule to @{section} in {path}"
    elif action == "append-kv":
        sec = doc.by_id().get(section)
        if sec is None:
            raise ValueError(f"no such section: {section}")
        key = one_line(key)
        if not key:
            raise ValueError("key is required")
        line = "= " + key + ((" " + one_line(value)) if one_line(value) else "")
        for i, existing in enumerate(sec.lines):
            if existing == f"= {key}" or existing.startswith(f"= {key} "):
                sec.lines[i] = line
                break
        else:
            sec.lines.append(line)
        sid, message = section, f"updated key '{key}' in @{section} in {path}"
    else:  # append-ref
        sec = doc.by_id().get(section)
        if sec is None:
            raise ValueError(f"no such section: {section}")
        target = one_line(target)
        if target not in doc.by_id():
            raise ValueError(f"reference target does not exist: {target}")
        line = "> " + target + ((" " + one_line(note)) if one_line(note) else "")
        if line not in sec.lines:
            sec.lines.append(line)
        sid, message = section, f"appended reference to @{section} in {path}"

    out = dumps(doc)
    checked = parse(out)
    if dumps(checked) != out:
        raise ValueError("refusing to write: document is not round-trip stable")
    errors = [m for lvl, m in lint(checked) if lvl == "error"]
    if errors:
        raise ValueError("refusing to write: " + "; ".join(errors[:3]))
    _atomic_write(path, out)
    return {"path": path, "section": sid, "action": action, "message": message}
