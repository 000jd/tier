"""TIER: layered context, repository graphs, code slicing, and agent memory."""
from __future__ import annotations

from .models import (
    VERSION, Doc, Section, SIGIL_CHARS, SEC_RE, HDR_RE, TABLE_RE, DATA_OPEN_RE,
    RULE_START, STOP, SHELL_LANGS, TABLE_MODES, _W, _atomic_write, _clip, _inside,
    _read_text, _table_count,
)
from .tokens import count_tokens
from .parser import (
    _csv_cells, _data_closes, _data_open, _kv_split, _live_lines, _load, _parse_kv,
    _render_table, _repo_file_meta, _table_rows, dumps, is_repo_doc, nodes, parse,
    render_lines, render_section, to_obj, wrap_untrusted,
)
from .lint import lint
from .converter import (
    _auto_abstract, _clip as _converter_clip, _csv_row, _esc, _json_body, _json_cell,
    _json_kv_lines, _json_scalar, _make_id, _md_cells, _one_line_text, _records_table,
    _ref_topics, _table_name, from_json, from_md, from_payload, to_md,
)
from .retrieval import (
    QSTOP, SYNONYMS, Session, _find_legacy, _index_tokens, _is_navigation_section,
    _is_test_module, _norm, _query_terms, _rule_count, _synonym_map, _trigram_sim,
    _trigrams, find, get, index, resolve, split_identifier,
)
from .repo import (
    JS_CONTROL, LANG_BY_EXT, REPO_ENTRY_NAMES, REPO_SKIP_DIRS, REPO_SOURCE_EXTS,
    _brace_end, _build_graph, _changed_symbols, _extract_symbols, _git_changed_paths,
    _go_module, _go_symbols, _js_arrow_name, _js_expr_end, _js_rhs_arrow, _js_symbols,
    _leading_comment, _py_module_name, _py_symbols, _reachable, _repo_add_deps, _repo_id,
    _repo_info, _repo_section, _repo_summary, _resolve_go_imports, _resolve_impact_target,
    _resolve_js_imports, _resolve_module, _resolve_py_imports, _resolve_rust_mods,
    _rust_symbols, _sha256, _sig_line, _slice_range, _strip_code_strings, _symbol_rows,
    _symbol_table, _top_symbol_names, from_repo, impact, render_impact, repo_source_files,
    slice, slice_symbols, sync_repo,
)
from .memory import _validate_section_body, remember
from .pipe import (
    LOG_CMD_RE, LOG_ERR_RE, LOG_WARN_RE, _fit_text, _log_shape, _text_fields, _tokens,
    detect_stream_format, from_stream, hook_process, log_doc, pipe_compress,
)
from .plugin import (
    TIER_BEGIN, TIER_END, _hook_commands, _looks_like_tier_hook, _merge_hook_settings,
    _plugin_instructions, _write_marked_instructions, init_plugin,
)

__version__ = VERSION
__all__ = [
    "VERSION", "Doc", "Section", "parse", "dumps", "index", "find", "get", "slice",
    "slice_symbols", "impact", "sync_repo", "from_repo", "remember", "Session",
    "wrap_untrusted", "from_md", "from_json", "from_payload", "to_md", "lint", "nodes",
    "count_tokens", "pipe_compress", "hook_process", "init_plugin",
]
