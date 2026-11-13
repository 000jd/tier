from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess

from .converter import _csv_row
from .models import Doc, Section, VERSION, _atomic_write, _clip, _read_text
from .parser import _load, _repo_file_meta, dumps, is_repo_doc, nodes, parse
from .retrieval import Session

REPO_SOURCE_EXTS = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs"}
REPO_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    "node_modules", "bower_components", "venv", ".venv", "env", ".env", "virtualenv",
    "dist", "build", "target", "out", "coverage", ".next", ".nuxt", "site-packages",
    "vendor", "tmp", "temp",
}
REPO_ENTRY_NAMES = {
    "main.py", "__main__.py", "cli.py", "app.py", "server.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.js", "main.ts", "main.go", "main.rs", "lib.rs",
}
LANG_BY_EXT = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
}


def repo_source_files(root: str, max_file_bytes: int = 512 * 1024, max_files: int = 2000):
    """Yield (relative_path, absolute_path, extension) for source files under root.

    Symbolic links are not followed, so a repo map can never read outside the root.
    """
    root = os.path.abspath(root)
    root_real = os.path.realpath(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in REPO_SKIP_DIRS)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in REPO_SOURCE_EXTS:
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            real = os.path.realpath(full)
            if real != root_real and not real.startswith(root_real + os.sep):
                continue
            if not os.access(full, os.R_OK):
                continue
            try:
                if os.path.getsize(full) > max_file_bytes:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found.append((rel, full, ext))
            if len(found) >= max_files:
                return found
    return found


def _repo_id(rel: str, used: set) -> str:
    base = rel.lower().replace("\\", "/")
    base = re.sub(r"[^a-z0-9_.-]+", "-", base).strip("-") or "module"
    if not re.match(r"[a-z0-9]", base):
        base = "m-" + base
    base = base[:64]
    cand, i = base, 2
    while cand in used:
        cand, i = f"{base}-{i}", i + 1
    used.add(cand)
    return cand


def _py_module_name(rel: str) -> str:
    p = rel[:-3] if rel.endswith(".py") else rel
    parts = [x for x in p.split("/") if x]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _register_py_module(module_to_rel: dict, rel: str) -> None:
    name = _py_module_name(rel)
    module_to_rel.setdefault(name, rel)
    parts = name.split(".")
    if parts and parts[0] == "src" and len(parts) > 1:
        module_to_rel.setdefault(".".join(parts[1:]), rel)


def _leading_comment(text: str, prefix: str = "//") -> str:
    lines = text.splitlines()
    i, out = 0, []
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("#!"):
        i += 1
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith(prefix):
            out.append(s[len(prefix) :].strip())
            i += 1
            continue
        if prefix != "#" and s.startswith("/*"):
            while i < len(lines):
                t = lines[i].strip().lstrip("/*").strip()
                if "*/" in t:
                    out.append(t.split("*/")[0].strip())
                    i += 1
                    break
                out.append(t)
                i += 1
            continue
        break
    return _clip(" ".join(x for x in out if x), 180)


def _strip_code_strings(line: str, in_block_comment: bool = False, rust: bool = False):
    """Blank out string and comment text so braces inside them do not count."""
    out, quote, i = [], "", 0
    while i < len(line):
        if in_block_comment:
            end = line.find("*/", i)
            if end < 0:
                return "".join(out), True
            i = end + 2
            in_block_comment = False
            continue
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if line.startswith("/*", i):
            in_block_comment = True
            i += 2
            continue
        if line.startswith("//", i):
            break
        if ch == "'" and rust:
            if re.match(r"'(?:\\.|[^'\\])'", line[i:]):
                m = re.match(r"'(?:\\.|[^'\\])'", line[i:])
                i += len(m.group(0))
            else:
                i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), in_block_comment


def _brace_end(lines: list, start: int, rust: bool = False) -> int:
    depth, opened, in_block = 0, False, False
    for j in range(start, min(len(lines), start + 1000)):
        code, in_block = _strip_code_strings(lines[j], in_block, rust)
        for k, ch in enumerate(code):
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}" and opened:
                depth -= 1
                if depth == 0:
                    if "{" in code[k + 1 :]:
                        opened = False
                        continue
                    return j + 1
    return start + 1


def _sig_line(lines: list, start: int, end: int) -> str:
    chunk_lines = []
    for i in range(start, min(end, start + 12)):
        raw = lines[i].strip()
        chunk_lines.append(raw)
        code, _ = _strip_code_strings(raw)
        if "{" in code or "{" in raw or "=>" in raw or raw.endswith(";"):
            break
    chunk = " ".join(chunk_lines).strip()
    for marker in ("{", "=>"):
        if marker in chunk:
            chunk = chunk.split(marker)[0].strip()
    return _clip(" ".join(chunk.strip(" ;").split()), 160)


def _py_symbols(text: str):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        symbols = []
        lines = text.splitlines()
        pending, class_indent, class_name = None, None, ""
        for n, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if stripped.startswith("@"):
                if pending is None:
                    pending = n
                continue
            kind = name = ""
            m = re.match(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)", stripped)
            if m:
                kind, name = "function", m.group(1)
                if class_name and class_indent is not None and indent > class_indent:
                    kind, name = "method", f"{class_name}.{name}"
            else:
                m = re.match(r"^class\s+([A-Za-z_]\w*)", stripped)
                if m:
                    kind, name = "class", m.group(1)
            if not kind:
                pending = None
                continue
            start = pending if pending is not None else n
            pending = None
            end = n
            for j in range(n, len(lines)):
                s = lines[j]
                if not s.strip():
                    continue
                if len(s) - len(s.lstrip(" ")) <= indent:
                    break
                end = j + 1
            symbols.append((kind, name, stripped, start, end))
            if kind == "class":
                class_indent, class_name = indent, name
            elif class_indent is not None and indent <= class_indent:
                class_indent, class_name = None, ""
        return _leading_comment(text, "#"), symbols, []

    purpose = ast.get_docstring(tree) or ""
    purpose = _clip(" ".join(purpose.split()), 180) or _leading_comment(text, "#")
    symbols, imports = [], []

    def decorated_start(node):
        starts = [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
        return min(starts)

    def signature(node, prefix):
        try:
            args = ast.unparse(node.args)
        except Exception:
            args = ""
        ret = ""
        if getattr(node, "returns", None) is not None:
            try:
                ret = " -> " + ast.unparse(node.returns)
            except Exception:
                pass
        return f"{prefix} {node.name}({args}){ret}"

    for node in tree.body:
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            symbols.append(("function", node.name, signature(node, prefix), decorated_start(node), end))
        elif isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    pass
            sig = "class " + node.name + (f"({', '.join(bases)})" if bases else "")
            symbols.append(("class", node.name, sig, decorated_start(node), end))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cend = getattr(child, "end_lineno", child.lineno) or child.lineno
                    cprefix = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                    symbols.append(
                        ("method", f"{node.name}.{child.name}", signature(child, cprefix),
                         decorated_start(child), cend)
                    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, 0, ()))
        elif isinstance(node, ast.ImportFrom):
            imports.append((node.module or "", node.level, tuple(a.name for a in node.names)))
    return purpose, symbols, imports


JS_CONTROL = {
    "if", "for", "while", "switch", "catch", "function", "return", "new", "await",
    "typeof", "super", "do", "else", "try", "finally", "throw", "void", "delete",
    "instanceof", "in", "of", "yield", "with", "case",
}


def _js_expr_end(lines: list, start: int) -> int:
    depth, in_block = 0, False
    for j in range(start, min(len(lines), start + 50)):
        code, in_block = _strip_code_strings(lines[j], in_block)
        for ch in code:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
        raw = lines[j].strip()
        if j == start and depth == 0 and raw.split("=>")[-1].strip():
            return j + 1
        if depth == 0 and (raw.endswith(";") or (j > start and raw and not raw.startswith((")", "]", ",")))):
            return j + 1
    return start + 1


def _js_rhs_arrow(text: str, pos: int):
    """Return the index of a top-level `=>` that starts the assigned RHS, else None."""
    i = pos
    while i < len(text) and text[i].isspace():
        i += 1
    if re.match(r"async\b", text[i:]):
        i += 5
        while i < len(text) and text[i].isspace():
            i += 1
    if i < len(text) and text[i] == "<":
        depth, j = 0, i
        while j < len(text):
            if text[j] == "<":
                depth += 1
            elif text[j] == ">":
                depth -= 1
                if depth == 0:
                    i = j + 1
                    break
            j += 1
        else:
            return None
        while i < len(text) and text[i].isspace():
            i += 1
    if i >= len(text):
        return None
    if text[i] == "(":
        depth, j = 0, i
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    i = j + 1
                    break
            j += 1
        if depth != 0:
            return None
        depth = 0
        while i < len(text):
            if text.startswith("=>", i) and depth == 0:
                return i
            ch = text[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif ch == ";" and depth == 0:
                return None
            i += 1
        return None
    m = re.match(r"[A-Za-z_$][\w$]*", text[i:])
    if not m:
        return None
    j = i + len(m.group(0))
    if j < len(text) and text[j] == "?":
        j += 1
    while j < len(text) and text[j].isspace():
        j += 1
    return j if text.startswith("=>", j) else None


def _js_top_level_semicolon(text: str, pos: int) -> bool:
    depth = 0
    for ch in text[pos:]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            return True
    return False


def _js_arrow_name(lines: list, idx: int):
    """Return (name, arrow_line_index, braced_body) for an arrow assignment."""
    first, _ = _strip_code_strings(lines[idx])
    m = re.match(
        r"^\s*(?:export\s+)?(?:(?:const|let|var)\s+)?(?:public|private|protected|static|readonly|"
        r"override)?\s*([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?",
        first,
    )
    if not m or m.group(1) in JS_CONTROL:
        return None, None, False
    text = ""
    rhs_pos = m.end()
    for j in range(idx, min(len(lines), idx + 20)):
        code, _ = _strip_code_strings(lines[j])
        text += code
        arrow = _js_rhs_arrow(text, rhs_pos)
        if arrow is not None:
            tail = text[arrow + 2:].strip()
            braced = tail.startswith("{")
            if not braced and not tail:
                for nxt in range(j + 1, min(len(lines), j + 4)):
                    nxt_code, _ = _strip_code_strings(lines[nxt])
                    if nxt_code.strip():
                        braced = nxt_code.strip().startswith("{")
                        break
            return m.group(1), j, braced
        if _js_top_level_semicolon(text, rhs_pos):
            return None, None, False
        if j > idx and re.match(
                r"^\s*(?:export|const|let|var|function|class|import)\b", code):
            return None, None, False
    return None, None, False


def _js_symbols(text: str):
    lines = text.splitlines()
    symbols, class_spans = [], []
    class_re = re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?"
        r"class\s+([A-Za-z_$][\w$]*)"
    )
    for idx, line in enumerate(lines):
        m = class_re.match(line)
        if not m:
            continue
        name = m.group(1)
        end = _brace_end(lines, idx)
        symbols.append(("class", name, _sig_line(lines, idx, end), idx + 1, end))
        class_spans.append((idx, end, name))
        for j in range(idx + 1, min(end, len(lines))):
            code, _ = _strip_code_strings(lines[j])
            mm = re.match(
                r"^\s+(?:(?:public|private|protected|static|async|readonly|get|set|override|"
                r"abstract|declare)\s+)*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(",
                code,
            )
            if mm and mm.group(1) not in JS_CONTROL:
                e = _brace_end(lines, j)
                header = " ".join(lines[j : min(e, j + 20)])
                if "{" in header and ";" not in header.split("{", 1)[0]:
                    symbols.append(("method", f"{name}.{mm.group(1)}", _sig_line(lines, j, e), j + 1, e))
                    continue
            arrow, arrow_line, braced = _js_arrow_name(lines, j)
            if arrow:
                e = _brace_end(lines, arrow_line) if braced else _js_expr_end(lines, j)
                symbols.append(("method", f"{name}.{arrow}", _sig_line(lines, j, e), j + 1, e))
    for idx, line in enumerate(lines):
        if any(a <= idx < b for a, b, _ in class_spans):
            continue
        code, _ = _strip_code_strings(line)
        m = re.match(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*"
            r"([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(",
            code,
        )
        if m:
            e = _brace_end(lines, idx)
            symbols.append(("function", m.group(1), _sig_line(lines, idx, e), idx + 1, e))
            continue
        arrow, arrow_line, braced = _js_arrow_name(lines, idx)
        if arrow:
            e = _brace_end(lines, arrow_line) if braced else _js_expr_end(lines, idx)
            symbols.append(("function", arrow, _sig_line(lines, idx, e), idx + 1, e))
            continue
        m = re.match(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(interface|type|enum)\s+"
            r"([A-Za-z_$][\w$]*)",
            code,
        )
        if m:
            e = _brace_end(lines, idx)
            symbols.append((m.group(1), m.group(2), _sig_line(lines, idx, e), idx + 1, e))
    return _leading_comment(text, "//"), symbols, []


def _go_symbols(text: str):
    lines = text.splitlines()
    symbols = []
    for idx, line in enumerate(lines):
        code, _ = _strip_code_strings(line)
        m = re.match(
            r"^\s*func\s+(?:\(([^)]*)\)\s*)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?\(",
            code,
        )
        if m:
            recv = m.group(1) or ""
            name = m.group(2)
            kind = "function"
            if recv:
                rtype = recv.strip().split()[-1].lstrip("*")
                rtype = re.sub(r"\[.*$", "", rtype)
                name, kind = f"{rtype}.{name}", "method"
            e = _brace_end(lines, idx)
            symbols.append((kind, name, _sig_line(lines, idx, e), idx + 1, e))
            continue
        m = re.match(
            r"^\s*type\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*=?\s*(struct|interface)\b",
            code,
        )
        if m:
            e = _brace_end(lines, idx)
            symbols.append((m.group(2), m.group(1), _sig_line(lines, idx, e), idx + 1, e))
    return _leading_comment(text, "//"), symbols, []


def _rust_decl_end(lines: list, start: int):
    """Return the end line of a bodyless trait method declaration, else None."""
    depth = 0
    for j in range(start, min(len(lines), start + 30)):
        code, _ = _strip_code_strings(lines[j], rust=True)
        for ch in code:
            if ch == "{":
                if depth == 0:
                    return None
                depth += 1
            elif ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            elif ch == "}":
                depth = max(0, depth - 1)
            elif ch == ";" and depth == 0:
                return j + 1
    return None


def _rust_symbols(text: str):
    lines = text.splitlines()
    symbols, impl_spans, trait_spans = [], [], []
    for idx, line in enumerate(lines):
        code, _ = _strip_code_strings(line, rust=True)
        m = re.match(
            r"^\s*impl(?:<[^>]*>)?\s+(?:.*?\s+for\s+)?([A-Za-z_]\w*)",
            code,
        )
        if m:
            impl_spans.append((idx, _brace_end(lines, idx, rust=True), m.group(1)))
            continue
        m = re.match(r"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z_]\w*)", code)
        if m:
            trait_spans.append((idx, _brace_end(lines, idx, rust=True), m.group(1)))
    for idx, line in enumerate(lines):
        code, _ = _strip_code_strings(line, rust=True)
        m = re.match(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait|union)\s+([A-Za-z_]\w*)",
            code,
        )
        if m:
            e = _brace_end(lines, idx, rust=True)
            symbols.append((m.group(1), m.group(2), _sig_line(lines, idx, e), idx + 1, e))
            continue
        m = re.match(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
            r"(?:extern\s+\"[^\"]*\"\s+)?fn\s+([A-Za-z_]\w*)",
            code,
        )
        if m:
            owner = ""
            for a, b, name in impl_spans:
                if a < idx < b:
                    owner = name
                    break
            if not owner:
                for a, b, name in trait_spans:
                    if a < idx < b:
                        owner = name
                        break
            kind = "method" if owner else "function"
            name = f"{owner}.{m.group(1)}" if owner else m.group(1)
            declaration = _rust_decl_end(lines, idx)
            e = declaration if declaration else _brace_end(lines, idx, rust=True)
            symbols.append((kind, name, _sig_line(lines, idx, e), idx + 1, e))
    return _leading_comment(text, "//"), symbols, []


def _extract_symbols(rel: str, text: str):
    ext = os.path.splitext(rel)[1].lower()
    if ext in (".py", ".pyi"):
        return _py_symbols(text)
    if ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        return _js_symbols(text)
    if ext == ".go":
        return _go_symbols(text)
    if ext == ".rs":
        return _rust_symbols(text)
    return "", [], []


def _symbol_table(symbols: list) -> list:
    if not symbols:
        return []
    lines = [f"|symbols[{len(symbols)}]{{kind,name,signature,lines}}"]
    for kind, name, sig, start, end in symbols:
        sig = " ".join(str(sig).replace("\r", " ").replace("\n", " ").replace("\x00", "\ufffd").split())
        lines.append(_csv_row([kind, str(name).replace("\x00", "\ufffd"), sig, f"{start}-{end}"]))
    return lines


def _top_symbol_names(symbols: list, ext: str) -> list:
    names = []
    for kind, name, sig, _start, _end in symbols:
        if "." in name or name.startswith("_"):
            continue
        if ext == ".go" and not name[:1].isupper():
            continue
        if ext == ".rs" and not str(sig).lstrip().startswith("pub"):
            continue
        if name not in names:
            names.append(name)
    return names[:8]


def _resolve_py_imports(rel: str, imports: list, module_to_rel: dict) -> set:
    pkg = rel.split("/")[:-1]
    out = set()

    def lookup(cand):
        parts = [p for p in cand.split(".") if p]
        while parts:
            target = module_to_rel.get(".".join(parts))
            if target and target != rel:
                return target
            parts.pop()
        return None

    for module, level, names in imports:
        candidates = []
        if level:
            base = pkg[: max(0, len(pkg) - (level - 1))]
            stem = ".".join(base + ([module] if module else []))
            candidates.append(stem)
            candidates += [(stem + "." + n) if stem else n for n in names]
        else:
            if module:
                candidates.append(module)
            candidates += [(module + "." + n) if module else n for n in names]
        for cand in candidates:
            target = lookup(cand)
            if target:
                out.add(target)
    return out


def _resolve_js_imports(rel: str, sources: list, rel_set: set) -> set:
    out = set()
    js_exts = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]
    for src in sources:
        if not src.startswith("."):
            continue
        base = os.path.normpath(os.path.join(os.path.dirname(rel), src)).replace(os.sep, "/")
        candidates = [base]
        if os.path.splitext(base)[1]:
            stem = os.path.splitext(base)[0]
            candidates += [stem + e for e in js_exts]
        else:
            candidates += [base + e for e in js_exts]
            candidates += [base + "/index" + e for e in js_exts]
        for cand in candidates:
            if cand in rel_set and cand != rel:
                out.add(cand)
                break
    return out


def _resolve_go_imports(rel: str, imports: list, rel_set: set, go_module: str) -> set:
    out = set()
    if not go_module:
        return out
    for path in imports:
        if path != go_module and not path.startswith(go_module + "/"):
            continue
        sub = path[len(go_module) :].strip("/")
        for other in rel_set:
            if other.endswith(".go") and os.path.dirname(other) == sub:
                out.add(other)
    out.discard(rel)
    return out


def _resolve_rust_mods(rel: str, mods: list, rel_set: set) -> set:
    out = set()
    d = os.path.dirname(rel)
    for name in mods:
        for cand in (f"{d}/{name}.rs".lstrip("/"), f"{d}/{name}/mod.rs".lstrip("/")):
            if cand in rel_set and cand != rel:
                out.add(cand)
                break
    return out


def _js_import_sources(text: str) -> list:
    srcs = []
    for pat in (
        r"""(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""",
        r"""require\(\s*['"]([^'"]+)['"]\s*\)""",
        r"""import\(\s*['"]([^'"]+)['"]\s*\)""",
    ):
        srcs += re.findall(pat, text)
    return list(dict.fromkeys(srcs))


def _go_import_paths(text: str) -> list:
    paths, in_block = [], False
    for line in text.splitlines():
        if re.match(r"^\s*import\s*\(", line):
            in_block = True
        if in_block or re.match(r"^\s*import\s+", line):
            paths += re.findall(r'"([^"]+)"', line)
        if in_block and ")" in line:
            in_block = False
    return list(dict.fromkeys(paths))


def _rust_mods(text: str) -> list:
    mods = re.findall(r"^\s*(?:pub\s+)?mod\s+([A-Za-z_]\w*)\s*;", text, re.M)
    for m in re.finditer(r"^\s*(?:pub\s+)?use\s+(?:crate|self)::\{([^}]*)\}", text, re.M):
        for part in m.group(1).split(","):
            name = part.strip().split("::")[0].strip()
            if name and name != "self":
                mods.append(name)
    for m in re.finditer(r"^\s*(?:pub\s+)?use\s+(?:crate|self)::([A-Za-z_]\w*)", text, re.M):
        mods.append(m.group(1))
    return list(dict.fromkeys(mods))


def _go_module(root: str) -> str:
    p = os.path.join(root, "go.mod")
    if not os.path.isfile(p):
        return ""
    m = re.search(r"^\s*module\s+(\S+)", _read_text(p), re.M)
    return m.group(1) if m else ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _repo_info(rel: str, full: str, ext: str, text: str = None) -> dict:
    if text is None:
        text = _read_text(full)
    purpose, symbols, raw_imports = _extract_symbols(rel, text)
    try:
        st = os.stat(full)
        mtime_ns, size = st.st_mtime_ns, st.st_size
    except OSError:
        mtime_ns, size = 0, len(text.encode("utf-8", "replace"))
    return {
        "rel": rel, "ext": ext, "text": text, "purpose": purpose,
        "symbols": symbols, "raw_imports": raw_imports,
        "sha256": _sha256(text), "mtime_ns": mtime_ns,
        # On-disk byte size, not len(text): the reader uses universal newlines,
        # so a CRLF checkout would otherwise never match its own fingerprint.
        "size": size,
    }


def _repo_add_deps(info: dict, rel_set: set, module_to_rel: dict, go_module: str):
    rel, ext, text = info["rel"], info["ext"], info["text"]
    if ext in (".py", ".pyi"):
        deps = _resolve_py_imports(rel, info.get("raw_imports", []), module_to_rel)
    elif ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        deps = _resolve_js_imports(rel, _js_import_sources(text), rel_set)
    elif ext == ".go":
        deps = _resolve_go_imports(rel, _go_import_paths(text), rel_set, go_module)
    elif ext == ".rs":
        deps = _resolve_rust_mods(rel, _rust_mods(text), rel_set)
    else:
        deps = set()
    deps.discard(rel)
    info["deps"] = deps


def _repo_section(info: dict, rel_to_id: dict) -> Section:
    rel, ext = info["rel"], info["ext"]
    top = _top_symbol_names(info["symbols"], ext)
    methods = sum(1 for kind, _name, _sig, _s, _e in info["symbols"] if kind == "method")
    purpose = info["purpose"] or f"{rel} ({LANG_BY_EXT.get(ext, ext.lstrip('.'))} module)"
    bits = [_clip(purpose, 140), "symbols: " + (", ".join(top) if top else "none")]
    if methods and len(top) < 4:
        bits.append(f"methods: {methods}")
    lines = [
        "= file",
        f"  path {rel}",
        f"  language {LANG_BY_EXT.get(ext, ext.lstrip('.'))}",
        f"  lines {len(info['text'].splitlines())}",
        f"  sha256 {info['sha256']}",
        f"  mtime {info['mtime_ns']}",
        f"  size {info['size']}",
    ]
    lines += _symbol_table(info["symbols"])
    for dep in sorted(info.get("deps", ())):
        target = rel_to_id.get(dep)
        if target:
            lines.append(f"> {target}")
    return Section(rel_to_id[rel], rel, abstract=_clip("; ".join(bits), 240), lines=lines)


def _repo_summary(rels: list, lang_counts: dict, entries: list) -> str:
    langs = ", ".join(f"{k} {v}" for k, v in sorted(lang_counts.items())) or "none"
    return (
        f"{len(rels)} source files; languages: {langs}; "
        f"entrypoints: {', '.join(entries[:6]) if entries else 'none'}"
    )


def from_repo(root: str, max_files: int = 2000) -> Doc:
    """Build a layered TIER architecture map of a source repository."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {root}")
    files = repo_source_files(root, max_files=max_files)
    rel_set = {rel for rel, _full, _ext in files}
    module_to_rel = {}
    for rel, _full, ext in files:
        if ext in (".py", ".pyi"):
            _register_py_module(module_to_rel, rel)
    go_module = _go_module(root)
    infos = [_repo_info(rel, full, ext) for rel, full, ext in files]
    for info in infos:
        _repo_add_deps(info, rel_set, module_to_rel, go_module)

    doc = Doc()
    doc.meta["tier"] = VERSION
    repo_name = os.path.basename(root.rstrip(os.sep)) or "repository"
    doc.meta["title"] = f"{repo_name} repository map"
    incoming = {d for info in infos for d in info["deps"]}
    entries = [i["rel"] for i in infos if os.path.basename(i["rel"]) in REPO_ENTRY_NAMES]
    if not entries:
        entries = [i["rel"] for i in infos if i["rel"] not in incoming]
    lang_counts = {}
    for info in infos:
        lang = LANG_BY_EXT.get(info["ext"], info["ext"].lstrip("."))
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
    doc.meta["sum"] = _repo_summary([i["rel"] for i in infos], lang_counts, entries)
    doc.rules = [
        "Read the repo.tier module map before opening source files; fetch only the listed line ranges.",
        "Use tier_get with follow=true to pull a module and its direct local dependencies.",
        "Use tier_slice for exact L3 source slices instead of reading whole files.",
    ]
    used, rel_to_id = set(), {}
    for info in infos:
        rel_to_id[info["rel"]] = _repo_id(info["rel"], used)
    for info in infos:
        doc.sections.append(_repo_section(info, rel_to_id))
    return doc


def sync_repo(path: str, root: str = None, session: Session = None,
              session_key: str = None, write: bool = True, rehash: bool = False) -> dict:
    """Incrementally refresh a repo.tier from disk, preserving unchanged sections.

    Files whose recorded `mtime` and `size` still match are not re-read: that is
    a cache, not the source of truth. `rehash=True` ignores the cache and
    compares content hashes for every file, which catches same-size edits with
    a restored timestamp (rsync -a, cp -p, tar extraction).
    """
    path = os.path.abspath(path)
    root = os.path.abspath(root or os.path.dirname(path) or ".")
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {root}")
    doc = _load(path)
    if not is_repo_doc(doc):
        raise ValueError("not a repository map (no = file metadata)")
    existing, existing_order = {}, []
    for sec in doc.sections:
        meta = _repo_file_meta(sec)
        if meta and meta.get("path"):
            existing[meta["path"]] = sec
            existing_order.append(meta["path"])
    try:
        map_mtime = os.stat(path).st_mtime_ns
    except OSError:
        map_mtime = 0
    files = repo_source_files(root)
    current = {}
    for rel, full, ext in files:
        old = existing.get(rel)
        meta = _repo_file_meta(old) if old else {}
        try:
            st = os.stat(full)
            stat_mtime, stat_size = st.st_mtime_ns, st.st_size
        except OSError:
            stat_mtime, stat_size = 0, -1
        recorded_mtime = str(meta.get("mtime", ""))
        trust_stat = (
            not rehash
            and old is not None
            and meta.get("sha256")
            and recorded_mtime == str(stat_mtime)
            and str(meta.get("size", "")) == str(stat_size)
            # A file touched before the map was written cannot have changed
            # without also having a newer mtime, so the cache is only trusted
            # when the recorded mtime is at or before the map's own mtime.
            and recorded_mtime.isdigit()
            and int(recorded_mtime) <= map_mtime
        )
        if trust_stat:
            current[rel] = {
                "full": full, "ext": ext, "text": None, "sha256": meta["sha256"],
                "unchanged": True,
            }
            continue
        text = _read_text(full)
        current[rel] = {
            "full": full, "ext": ext, "text": text, "sha256": _sha256(text),
            "unchanged": False,
        }
    rel_set = set(current)
    module_to_rel = {}
    for rel, item in current.items():
        if item["ext"] in (".py", ".pyi"):
            _register_py_module(module_to_rel, rel)
    go_module = _go_module(root)

    changed, added, deleted = [], [], []
    infos = {}
    used = set(s.id for s in doc.sections)
    rel_to_id = {rel: sec.id for rel, sec in existing.items()}
    for rel in sorted(rel_set):
        old = existing.get(rel)
        old_sha = (_repo_file_meta(old).get("sha256", "") if old else "")
        if old is None:
            added.append(rel)
            rel_to_id[rel] = _repo_id(rel, used)
            info = _repo_info(rel, current[rel]["full"], current[rel]["ext"], text=current[rel]["text"])
            infos[rel] = info
        elif old_sha != current[rel]["sha256"]:
            changed.append(rel)
            info = _repo_info(rel, current[rel]["full"], current[rel]["ext"], text=current[rel]["text"])
            infos[rel] = info
    for rel in existing_order:
        if rel not in rel_set:
            deleted.append(rel)
    for info in infos.values():
        _repo_add_deps(info, rel_set, module_to_rel, go_module)

    replaced = {rel: _repo_section(info, rel_to_id) for rel, info in infos.items()}
    deleted_ids = {existing[rel].id for rel in deleted}
    new_sections = []
    for sec in doc.sections:
        meta = _repo_file_meta(sec)
        rel = meta.get("path") if meta else ""
        if rel in deleted:
            continue
        if rel in replaced:
            new_sections.append(replaced[rel])
            continue
        if rel:
            sec.lines = [
                line for line in sec.lines
                if not (line.startswith("> ") and line[2:].split(" ", 1)[0] in deleted_ids)
            ]
        new_sections.append(sec)
    for rel in added:
        new_sections.append(replaced[rel])
    if added or deleted:
        for sec in new_sections:
            meta = _repo_file_meta(sec)
            rel = meta.get("path") if meta else ""
            if not rel or rel in infos or rel not in current:
                continue
            item = current[rel]
            text = item["text"]
            if text is None:
                text = _read_text(item["full"])
                item["text"] = text
            info = {"rel": rel, "ext": item["ext"], "text": text, "raw_imports": []}
            if item["ext"] in (".py", ".pyi"):
                try:
                    ast.parse(text)
                except SyntaxError:
                    continue
                info["raw_imports"] = _extract_symbols(rel, text)[2]
            _repo_add_deps(info, rel_set, module_to_rel, go_module)
            dep_ids = {rel_to_id[d] for d in info["deps"] if d in rel_to_id}
            body = [line for line in sec.lines if not line.startswith("> ")]
            sec.lines = body + [f"> {i}" for i in sorted(dep_ids)]
    doc.sections = new_sections
    doc.meta["tier"] = VERSION

    lang_counts = {}
    for rel, item in current.items():
        lang = LANG_BY_EXT.get(item["ext"], item["ext"].lstrip("."))
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
    entries = [rel for rel in sorted(rel_set) if os.path.basename(rel) in REPO_ENTRY_NAMES]
    if not entries:
        id_to_rel = {rel_to_id[rel]: rel for rel in rel_set if rel in rel_to_id}
        incoming = set()
        for sec in doc.sections:
            for nd in nodes(sec):
                if nd["t"] == "ref" and nd["id"] in id_to_rel:
                    incoming.add(id_to_rel[nd["id"]])
        entries = [rel for rel in sorted(rel_set) if rel not in incoming]
    doc.meta["sum"] = _repo_summary(sorted(rel_set), lang_counts, entries)

    changed_ids = [existing[rel].id for rel in changed]
    deleted_ids_list = [existing[rel].id for rel in deleted]
    if session is not None:
        key = session_key or path
        for sid in changed_ids + deleted_ids_list:
            session.invalidate(key, sid)
    did_write = False
    if write and (changed or added or deleted):
        _atomic_write(path, dumps(doc))
        did_write = True
    return {
        "path": path, "changed": sorted(changed), "added": sorted(added),
        "deleted": sorted(deleted), "unchanged": len(existing) - len(changed) - len(deleted),
        "changed_ids": changed_ids, "deleted_ids": deleted_ids_list,
        "written": did_write, "doc": doc,
    }


def _symbol_rows(sec: Section) -> list:
    rows = []
    for nd in nodes(sec):
        if nd["t"] == "table" and nd["name"] == "symbols":
            for row in nd["rows"]:
                if len(row) >= 4:
                    rows.append({"kind": row[0], "name": row[1], "signature": row[2], "lines": row[3]})
    return rows


def _resolve_module(doc: Doc, module: str):
    module = str(module or "").strip().lstrip("@")
    by = doc.by_id()
    if module in by:
        return by[module]
    norm = os.path.normpath(module).replace(os.sep, "/")
    for sec in doc.sections:
        title = sec.title.replace(os.sep, "/")
        if title == module or os.path.normpath(title).replace(os.sep, "/") == norm:
            return sec
        if title.endswith("/" + module):
            return sec
    return None


def _slice_range(text: str):
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(text))
    if not m:
        return None
    try:
        start, end = int(m.group(1)), int(m.group(2))
    except ValueError:
        return None
    return (start, end) if start <= end else None


def slice_symbols(doc: Doc, module: str, symbols, root: str = None, context_lines: int = 0,
                  session: Session = None, session_key: str = "", force: bool = False):
    """Return (text, missing) with exact L3 source lines for named symbols."""
    sec = _resolve_module(doc, module)
    if sec is None:
        return "", [f"module:{module}"]
    root = os.path.abspath(root or os.getcwd())
    meta = _repo_file_meta(sec)
    rel = meta.get("path") or sec.title
    full = os.path.realpath(os.path.join(root, rel))
    root_real = os.path.realpath(root)
    if full != root_real and not full.startswith(root_real + os.sep):
        raise ValueError("slice path escapes repository root")
    if not os.path.isfile(full):
        return "", [f"file:{rel}"]
    lines = _read_text(full).splitlines()
    rows = _symbol_rows(sec)
    by_name = {}
    for row in rows:
        by_name.setdefault(row["name"], row)
    if isinstance(symbols, str):
        symbols = [symbols]
    if not symbols:
        symbols = [row["name"] for row in rows]
    context_lines = max(0, int(context_lines or 0))
    lang = LANG_BY_EXT.get(os.path.splitext(rel)[1].lower(), "")
    parts, missing, seen = [], [], set()
    key = session_key or ""
    for symbol in symbols:
        symbol = str(symbol).strip().lstrip("@")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        row = by_name.get(symbol)
        if row is None:
            for name, candidate in by_name.items():
                if name.lower() == symbol.lower():
                    row = candidate
                    break
        if row is None:
            missing.append(symbol)
            continue
        bounds = _slice_range(row["lines"])
        if bounds is None:
            missing.append(symbol)
            continue
        start = max(1, bounds[0] - context_lines)
        end = min(len(lines), bounds[1] + context_lines)
        entry = f"{sec.id}#{symbol}"
        if session is not None and not force and session.is_marked(key, entry):
            parts.append(f"@{sec.id}#{symbol} (already in context; not resent)")
            continue
        snippet = "\n".join(lines[start - 1 : end])
        parts.append(f"@{sec.id}#{symbol} [L{start}-L{end}]\n```{lang}\n{snippet}\n```")
        if session is not None:
            session.mark(key, entry)
    return "\n\n".join(parts), missing


slice = slice_symbols


def _build_graph(doc: Doc):
    by = doc.by_id()
    forward = {}
    reverse = {sid: [] for sid in by}
    for sec in doc.sections:
        refs = []
        for nd in nodes(sec):
            if nd["t"] == "ref" and nd["id"] in by and nd["id"] != sec.id:
                refs.append(nd["id"])
                reverse[nd["id"]].append(sec.id)
        forward[sec.id] = list(dict.fromkeys(refs))
    for key in reverse:
        reverse[key] = list(dict.fromkeys(reverse[key]))
    return forward, reverse


def _reachable(graph: dict, start: str) -> list:
    seen, order, queue = {start}, [], [start]
    while queue:
        current = queue.pop(0)
        for nxt in graph.get(current, []):
            if nxt not in seen:
                seen.add(nxt)
                order.append(nxt)
                queue.append(nxt)
    return order


def _resolve_impact_target(doc: Doc, target: str) -> str:
    target = str(target or "").strip().lstrip("@")
    by = doc.by_id()
    if target in by:
        return target
    norm = os.path.normpath(target).replace(os.sep, "/")
    for sec in doc.sections:
        title = sec.title.replace(os.sep, "/")
        if title == target or os.path.normpath(title).replace(os.sep, "/") == norm:
            return sec.id
    if "#" in target:
        return _resolve_impact_target(doc, target.split("#", 1)[0])
    hits = []
    for sec in doc.sections:
        if any(row["name"] == target for row in _symbol_rows(sec)):
            hits.append(sec.id)
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"unknown impact target: {target}")


def _git_changed_paths(root: str):
    try:
        top_proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=20,
        )
        if top_proc.returncode != 0:
            return None
        top = top_proc.stdout.strip()
        proc = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "-z", "--", "."],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    entries = proc.stdout.split("\0")
    paths, i = [], 0
    # `git rev-parse --show-toplevel` reports a resolved path, while `root` may
    # still contain symlinked components (macOS /var -> /private/var, /tmp).
    # Comparing one against the other drops every file, so resolve both.
    root_real = os.path.realpath(root)
    top_real = os.path.realpath(top)
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[:1] in ("R", "C"):
            i += 1  # the following NUL entry is the old path
        full = os.path.realpath(os.path.join(top_real, path))
        rel = os.path.relpath(full, root_real)
        if rel == ".." or rel.startswith(".." + os.sep) or rel == ".":
            continue
        paths.append(rel.replace(os.sep, "/"))
    return sorted(set(paths))


def _changed_symbols(old_rows: list, ext: str, text: str):
    old = {row["name"]: (row["signature"], row["lines"]) for row in old_rows}
    _purpose, symbols, _imports = _extract_symbols("x" + ext, text)
    new = {name: (sig, f"{start}-{end}") for _kind, name, sig, start, end in symbols}
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(name for name in set(old) & set(new) if old[name] != new[name]),
    }


def impact(doc: Doc, target: str, root: str = None, git: bool = False) -> dict:
    """Analyze forward dependencies, reverse dependents, and optional Git blast radius."""
    root = os.path.abspath(root or os.getcwd())
    sid = _resolve_impact_target(doc, target)
    forward, reverse = _build_graph(doc)
    direct = reverse.get(sid, [])
    transitive = _reachable(reverse, sid)
    deps = forward.get(sid, [])
    transitive_deps = _reachable(forward, sid)
    result = {
        "target": sid,
        "direct_dependents": direct,
        "transitive_dependents": transitive,
        "dependencies": deps,
        "transitive_dependencies": transitive_deps,
        "changed_modules": [],
        "changed_symbols": {},
        "affected": [],
        "source": "graph",
    }
    if not git:
        return result

    path_to_sec, meta_by_sec = {}, {}
    for sec in doc.sections:
        meta = _repo_file_meta(sec)
        if meta and meta.get("path"):
            path_to_sec[meta["path"]] = sec
            meta_by_sec[sec.id] = meta
    changed_paths = _git_changed_paths(root)
    source = "git"
    if changed_paths is None:
        source = "fingerprint"
        changed_paths, seen = [], set()
        for sec in doc.sections:
            meta = _repo_file_meta(sec)
            if not meta or not meta.get("path"):
                continue
            seen.add(meta["path"])
            full = os.path.join(root, meta["path"])
            if not os.path.isfile(full):
                changed_paths.append(meta["path"])
                continue
            text = _read_text(full)
            if meta.get("sha256") and meta["sha256"] != _sha256(text):
                changed_paths.append(meta["path"])
        current = {rel for rel, _full, _ext in repo_source_files(root)}
        changed_paths += sorted(current - seen)
        changed_paths = sorted(set(changed_paths))

    changed_ids = []
    for rel in changed_paths:
        sec = path_to_sec.get(rel)
        if sec is None:
            continue
        changed_ids.append(sec.id)
        full = os.path.join(root, rel)
        if os.path.isfile(full):
            result["changed_symbols"][rel] = _changed_symbols(
                _symbol_rows(sec), os.path.splitext(rel)[1].lower(), _read_text(full)
            )
        else:
            result["changed_symbols"][rel] = {
                "added": [], "removed": [row["name"] for row in _symbol_rows(sec)], "changed": []
            }
    affected = set(changed_ids)
    for cid in changed_ids:
        affected.update(reverse.get(cid, []))
        affected.update(_reachable(reverse, cid))
    result.update({
        "changed_modules": changed_ids,
        "affected": sorted(affected),
        "source": source,
        "changed_paths": changed_paths,
    })
    return result


def render_impact(result: dict) -> str:
    out = [f"Impact target: @{result['target']}"]
    out.append("Direct dependents: " + (", ".join("@" + x for x in result["direct_dependents"]) or "none"))
    out.append("Transitive dependents: " + (", ".join("@" + x for x in result["transitive_dependents"]) or "none"))
    out.append("Dependencies: " + (", ".join("@" + x for x in result["dependencies"]) or "none"))
    out.append("Transitive dependencies: " + (", ".join("@" + x for x in result["transitive_dependencies"]) or "none"))
    if result.get("changed_modules") or result.get("changed_paths"):
        out.append("Git blast radius (" + result.get("source", "graph") + "):")
        out.append("Changed paths: " + (", ".join(result.get("changed_paths", [])) or "none"))
        out.append("Changed modules: " + (", ".join("@" + x for x in result["changed_modules"]) or "none"))
        for rel, diff in sorted(result.get("changed_symbols", {}).items()):
            bits = []
            for key in ("added", "removed", "changed"):
                if diff.get(key):
                    bits.append(f"{key}: {', '.join(diff[key])}")
            out.append(f"  {rel}: " + ("; ".join(bits) if bits else "no symbol changes"))
        out.append("Downstream to re-test: " + (", ".join("@" + x for x in result.get("affected", [])) or "none"))
    return "\n".join(out)
