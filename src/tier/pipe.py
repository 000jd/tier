from __future__ import annotations

import json
import os
import re
import tempfile

from .converter import from_json, from_md
from .models import Doc, Section, VERSION, _atomic_write, _clip
from .parser import dumps, render_section, wrap_untrusted
from .retrieval import index
from .tokens import count_tokens

LOG_ERR_RE = re.compile(r"\b(error|err|fail(?:ed|ure|ing)?|fatal|exception|traceback|panic|critical)\b", re.I)
LOG_WARN_RE = re.compile(r"\b(warn(?:ing)?|deprecated)\b", re.I)
LOG_CMD_RE = re.compile(r"^\s*(?:\$|>|❯|\+)\s+(.+)$")


def detect_stream_format(text: str) -> str:
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped[:1] in ("{", "["):
        try:
            json.loads(text)
            return "json"
        except ValueError:
            pass
    if re.search(r"^#{1,6}\s+\S", text, re.M) or "```" in text:
        return "md"
    return "log"


def _log_shape(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\]?\s*", "", s)
    s = re.sub(r"^\[?\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\]?\s*", "", s)
    s = re.sub(r"\b[0-9a-fA-F]{8,}\b", "<hex>", s)
    s = re.sub(r"\b\d+\.\d+\.\d+\.\d+\b", "<ip>", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", s)
    return re.sub(r"\s+", " ", s)


def log_doc(text: str) -> Doc:
    """Compress raw command/log/test output into structured error/command/raw sections."""
    groups, order = {}, []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        shape = _log_shape(line)
        if shape not in groups:
            groups[shape] = {"line": line.strip(), "count": 0}
            order.append(shape)
        groups[shape]["count"] += 1

    errors, warnings, commands, rest = [], [], [], []
    for shape in order:
        item = groups[shape]
        line, count = item["line"], item["count"]
        suffix = f" [x{count}]" if count > 1 else ""
        m = LOG_CMD_RE.match(line)
        if m:
            commands.append((m.group(1).strip(), count))
        elif LOG_ERR_RE.search(line):
            errors.append(line + suffix)
        elif LOG_WARN_RE.search(line):
            warnings.append(line + suffix)
        else:
            rest.append(line + suffix)

    doc = Doc(meta={"tier": VERSION, "title": "Piped output"})
    doc.meta["sum"] = (
        f"{sum(g['count'] for g in groups.values())} lines, {len(order)} unique groups, "
        f"{len(errors)} errors, {len(warnings)} warnings, {len(commands)} commands"
    )
    if errors or warnings:
        sec = Section("errors", "Errors and warnings")
        items = errors + warnings
        sec.abstract = _clip("errors: " + "; ".join(items[:2]), 180)
        sec.lines = ["- " + x for x in items]
        doc.sections.append(sec)
    if commands:
        sec = Section("commands", "Commands")
        sec.abstract = _clip("commands: " + "; ".join(x[0] for x in commands[:2]), 160)
        for cmd, count in commands:
            sec.lines.append("$ " + cmd)
            if count > 1:
                sec.lines.append(f"- command repeated {count} times")
        doc.sections.append(sec)
    if rest:
        sec = Section("output", "Output")
        sec.abstract = f"untrusted stream: {len(rest)} unique non-error/non-command lines"
        sec.lines = wrap_untrusted("stream", "\n".join(rest)).split("\n")
        doc.sections.append(sec)
    return doc


def from_stream(text: str, fmt: str = "auto") -> Doc:
    fmt = (fmt or "auto").lower()
    if fmt == "auto":
        fmt = detect_stream_format(text)
    if fmt == "json":
        return from_json(json.loads(text))
    if fmt == "md":
        return from_md(text)
    if fmt == "log":
        return log_doc(text)
    raise ValueError(f"unknown stream format '{fmt}'")


def _tokens(text: str) -> int:
    return count_tokens([text])[0][0]


def _fit_text(text: str, budget: int) -> str:
    """Keep whole leading lines of text while the exact token count fits."""
    if budget <= 0 or not text:
        return ""
    kept = []
    used = 0
    lines = text.split("\n")
    counts = count_tokens(lines)[0] if lines else []
    for line, n in zip(lines, counts):
        if used + n > budget:
            break
        kept.append(line)
        used += n
    out = "\n".join(kept)
    while kept and _tokens(out) > budget:
        kept.pop()
        out = "\n".join(kept)
    return out


def pipe_compress(text: str, fmt: str = "auto", max_tokens: int = None, out_path: str = None) -> str:
    """Compress a stream; over budget, emit L0 + priority sections and save the full artifact."""
    doc = from_stream(text, fmt)
    full = dumps(doc)
    over = False
    if max_tokens is not None:
        over = _tokens(full) > max_tokens
    artifact = ""
    if out_path:
        artifact = os.path.abspath(out_path)
        _atomic_write(artifact, full)
    elif over:
        fd, artifact = tempfile.mkstemp(prefix="tier-pipe-", suffix=".tier")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(full)
    if not over:
        return full

    budget = max(0, int(max_tokens))
    artifact_line = f"Full artifact: {artifact}"
    if budget <= 0 or _tokens(artifact_line) > budget:
        return artifact_line

    parts = [artifact_line]
    idx_text = _fit_text(index(doc), budget - _tokens(artifact_line))
    if idx_text:
        candidate = idx_text + "\n" + artifact_line
        if _tokens(candidate) <= budget:
            parts = [idx_text, artifact_line]

    def priority(sec):
        if sec.id == "errors":
            return 0
        if sec.id == "commands":
            return 1
        if any(x.startswith("! ") for x in sec.lines):
            return 2
        return 3

    omitted = []
    for sec in sorted(doc.sections, key=priority):
        body = render_section(sec)
        candidate = "\n".join(parts + [body])
        if _tokens(candidate) <= budget:
            parts.append(body)
        else:
            omitted.append(sec.id)
    if omitted:
        note = "Omitted sections: " + ", ".join(omitted) + " (fetch from the full artifact)"
        if _tokens("\n".join(parts + [note])) <= budget:
            parts.append(note)
    return "\n".join(parts)


def _text_fields(obj, prefix: str = "", depth: int = 0) -> list:
    out = []
    if depth > 64:
        return out
    if isinstance(obj, str):
        out.append((prefix, obj))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            out += _text_fields(value, f"{prefix}.{key}" if prefix else str(key), depth + 1)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            out += _text_fields(value, f"{prefix}[{i}]", depth + 1)
    return out


def hook_process(text: str, max_tokens: int = 600, fmt: str = "auto") -> dict:
    """Handle a JSON stdin hook payload without blocking or crashing."""
    max_tokens = 600 if max_tokens is None else int(max_tokens)
    malformed = False
    try:
        data = json.loads(text) if text.strip() else {}
    except (ValueError, RecursionError):
        data, malformed = {"tool_response": text}, True
    if not isinstance(data, dict):
        data, malformed = {"tool_response": text}, True
    best_path, best_text, best_tokens = "", "", 0
    try:
        fields = _text_fields(data)
    except RecursionError:
        fields = [("payload", text)]
    for path, value in fields:
        if len(value) < 200:
            continue
        count = count_tokens([value])[0][0]
        if count > best_tokens:
            best_path, best_text, best_tokens = path or "payload", value, count
    if best_tokens <= max_tokens:
        response = {"continue": True}
        if malformed:
            response["systemMessage"] = "tier hook: input was not JSON; nothing compressed"
        return response
    prefix = f"TIER compressed {best_path} ({best_tokens} tokens):\n"
    try:
        compressed = pipe_compress(best_text, fmt, max(1, max_tokens - _tokens(prefix)))
    except Exception as e:
        return {"continue": True, "systemMessage": f"tier hook error: {e}"}
    event = data.get("hook_event_name") or data.get("hookEventName") or "PostToolUse"
    if not isinstance(event, str):
        event = "PostToolUse"
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": prefix + compressed,
        },
        "systemMessage": f"TIER: compressed {best_tokens} tokens from {best_path}",
    }
