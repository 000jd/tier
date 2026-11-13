"""Tests for v1.1.0: composite context, repository cache, call graph, tree-sitter."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
for _path in (SRC, os.path.join(ROOT, "benchmarks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import tier
from tier import cache, context, repo

ENV = dict(os.environ, PYTHONPATH=SRC)


def _put(root, name, text):
    path = os.path.join(root, name)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _tokens(text):
    return tier.count_tokens([text])[0][0]


def _two_module_repo(root):
    _put(root, "a.py",
         '"""Alpha."""\n\n\ndef helper(x):\n    return x + 1\n\n\n'
         'def caller(n):\n    return helper(n)\n')
    _put(root, "b.py",
         '"""Beta."""\nfrom a import caller\n\n\ndef top():\n    return caller(2)\n')
    return root


class ContextTests(unittest.TestCase):
    def test_context_packs_sections_slices_and_impact(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            res = context.build_context(td, "caller helper", budget_tokens=2000)
            self.assertTrue(res["sections"])
            self.assertTrue(res["slices"], "matched symbols should yield L3 slices")
            self.assertIn("#helper [L", res["text"])
            self.assertIn("## Impact:", res["text"])
            self.assertLessEqual(res["tokens"], 2000)

    def test_context_respects_budget_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            for i in range(12):
                _put(td, f"mod{i}.py",
                     f'"""Module {i}."""\n\ndef fn{i}(x):\n    return x * {i}\n\n\n'
                     f'def other{i}(y):\n    return fn{i}(y) + 1\n')
            doc = tier.from_repo(td)
            full = tier.dumps(doc)
            self.assertGreater(_tokens(full), 300)
            for budget in (120, 240, 600):
                res = context.build_context(td, "fn other module", budget_tokens=budget)
                self.assertLessEqual(res["tokens"], budget, f"budget={budget}")
                self.assertGreater(res["tokens"], 0)

    def test_context_omits_impact_when_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            res = context.build_context(td, "caller", budget_tokens=2000,
                                        include_impact=False)
            self.assertNotIn("## Impact:", res["text"])
            self.assertIn("@a.py", res["text"])

    def test_context_session_dedup_stubs_repeats(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            session = tier.Session()
            first = context.build_context(td, "caller helper", budget_tokens=4000,
                                          session=session)
            self.assertNotIn("already in context", first["text"])
            second = context.build_context(td, "caller helper", budget_tokens=4000,
                                           session=session)
            self.assertIn("already in context", second["text"])
            self.assertLess(second["tokens"], first["tokens"])

    def test_context_no_dedup_resends(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            session = tier.Session()
            context.build_context(td, "caller", budget_tokens=4000, session=session)
            again = context.build_context(td, "caller", budget_tokens=4000,
                                          session=session, dedup=False)
            self.assertNotIn("already in context", again["text"])

    def test_context_requires_query(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            with self.assertRaises(ValueError):
                context.build_context(td, "   ")

    def test_context_on_plain_tier_doc(self):
        with tempfile.TemporaryDirectory() as td:
            path = _put(td, "guide.tier",
                        "%tier 1.1.0\n%title Guide\n@rollback Rollback\n"
                        "? undoing a deploy\nRun deployctl rollback.\n"
                        "@deploy Deploy\n? shipping\nRun deployctl ship.\n")
            res = context.build_context(path, "rollback deploy", budget_tokens=500)
            self.assertIn("@rollback", res["text"])
            self.assertNotIn("## Impact:", res["text"])

    def test_cli_context_command(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            env = dict(ENV, TIER_CACHE_DIR=os.path.join(td, "cache"))
            proc = subprocess.run(
                [sys.executable, "-m", "tier", "context", td, "caller helper",
                 "--budget-tokens", "800"],
                text=True, capture_output=True, timeout=120, cwd=ROOT, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("@a.py", proc.stdout)
            self.assertIn("|calls[", proc.stdout)
            proc = subprocess.run(
                [sys.executable, "-m", "tier", "context", td, "caller", "--no-impact"],
                text=True, capture_output=True, timeout=120, cwd=ROOT, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("## Impact:", proc.stdout)


class WorktreeCacheTests(unittest.TestCase):
    """Cache resolution when `.git` is a pointer file (worktree or submodule)."""

    def test_git_pointer_file_resolves_to_its_own_gitdir(self):
        with tempfile.TemporaryDirectory() as base:
            work = os.path.join(base, "work")
            admin = os.path.join(base, "main", ".git", "worktrees", "work")
            os.makedirs(work)
            os.makedirs(admin)
            _put(work, "a.py", "def a():\n    return 1\n")
            _put(work, ".git", f"gitdir: {admin}\n")
            self.assertEqual(cache._git_dir(work), admin)
            self.assertEqual(cache.cache_path(work),
                             os.path.join(admin, "info", "tier", "repo.tier"))
            self.assertEqual(cache.cache_kind(cache.cache_path(work), work), "git")

    def test_relative_git_pointer_is_resolved_against_checkout(self):
        with tempfile.TemporaryDirectory() as base:
            work = os.path.join(base, "work")
            os.makedirs(os.path.join(base, "admin"))
            _put(work, "a.py", "def a():\n    return 1\n")
            _put(work, ".git", "gitdir: ../admin\n")
            self.assertEqual(cache._git_dir(work), os.path.join(base, "admin"))

    def test_broken_pointer_falls_back_to_user_cache(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as cdir:
            work = os.path.join(base, "work")
            _put(work, "a.py", "def a():\n    return 1\n")
            _put(work, ".git", "not a pointer at all\n")
            old = os.environ.get("TIER_CACHE_DIR")
            os.environ["TIER_CACHE_DIR"] = cdir
            try:
                self.assertEqual(cache._git_dir(work), "")
                path = cache.cache_path(work)
                self.assertTrue(path.startswith(os.path.abspath(cdir)))
            finally:
                if old is None:
                    os.environ.pop("TIER_CACHE_DIR", None)
                else:
                    os.environ["TIER_CACHE_DIR"] = old

    def test_read_only_gitdir_falls_back_to_user_cache(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as cdir:
            work = os.path.join(base, "work")
            admin = os.path.join(base, "admin")
            os.makedirs(admin)
            _put(work, "a.py", "def a():\n    return 1\n")
            _put(work, ".git", f"gitdir: {admin}\n")
            old = os.environ.get("TIER_CACHE_DIR")
            os.environ["TIER_CACHE_DIR"] = cdir
            try:
                with mock.patch.object(cache, "_writable_dir", return_value=False):
                    path = cache.cache_path(work)
                self.assertTrue(path.startswith(os.path.abspath(cdir)))
            finally:
                if old is None:
                    os.environ.pop("TIER_CACHE_DIR", None)
                else:
                    os.environ["TIER_CACHE_DIR"] = old

    def test_owner_manifest_restores_root_for_out_of_tree_map(self):
        with tempfile.TemporaryDirectory() as base:
            work = os.path.join(base, "work")
            admin = os.path.join(base, "main", ".git", "worktrees", "work")
            os.makedirs(work)
            os.makedirs(admin)
            _put(work, "a.py", '"""A."""\n\ndef a():\n    return 1\n')
            _put(work, ".git", f"gitdir: {admin}\n")
            path, doc, root = cache.ensure_doc(work)
            self.assertEqual(root, work)
            self.assertTrue(os.path.isfile(path))
            # A later resolve from the map path alone must find the checkout.
            self.assertEqual(cache.resolve_doc(path)[1], work)

    def test_real_worktree_shares_nothing_and_stays_clean(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        with tempfile.TemporaryDirectory() as base:
            main = os.path.join(base, "main")
            linked = os.path.join(base, "linked")
            os.makedirs(main)
            _put(main, "a.py", '"""Alpha."""\n\ndef helper(x):\n    return x + 1\n')
            _put(main, ".gitignore", "__pycache__/\n")
            for cmd in (["git", "init", "-q", main],
                        ["git", "-C", main, "add", "."],
                        ["git", "-C", main, "commit", "-qm", "init"]):
                subprocess.run(cmd, check=True, env=env, capture_output=True)
            added = subprocess.run(
                ["git", "-C", main, "worktree", "add", "-q", linked, "-b", "feature"],
                env=env, capture_output=True, text=True,
            )
            if added.returncode != 0:
                self.skipTest(f"git worktree unavailable: {added.stderr.strip()}")
            self.assertTrue(os.path.isfile(os.path.join(linked, ".git")),
                            "a linked worktree must have a .git pointer file")

            main_path, _doc, main_root = cache.ensure_doc(main)
            linked_path, linked_doc, linked_root = cache.ensure_doc(linked)
            self.assertEqual(main_root, main)
            self.assertEqual(linked_root, linked)
            self.assertNotEqual(main_path, linked_path,
                                "worktrees hold different branches, so each needs its own map")

            _put(linked, "a.py",
                 '"""Feature."""\n\ndef helper(x):\n    return x + 2\n\n'
                 'def brand_new():\n    return 9\n')
            _, linked_after, _ = cache.ensure_doc(linked)
            linked_names = {row["name"] for sec in linked_after.sections
                            for row in tier._symbol_rows(sec)}
            self.assertIn("brand_new", linked_names)

            _, main_after, _ = cache.ensure_doc(main)
            main_names = {row["name"] for sec in main_after.sections
                          for row in tier._symbol_rows(sec)}
            self.assertNotIn("brand_new", main_names,
                             "the main worktree must not pick up the feature branch")

            for tree in (main, linked):
                status = subprocess.run(["git", "-C", tree, "status", "--porcelain"],
                                        capture_output=True, text=True, check=True)
                self.assertNotIn("repo.tier", status.stdout)
                self.assertNotIn(".git/info", status.stdout)
            main_status = subprocess.run(["git", "-C", main, "status", "--porcelain"],
                                          capture_output=True, text=True, check=True)
            self.assertEqual(main_status.stdout.strip(), "",
                             "the main worktree must stay byte-clean")


class CallFilterTests(unittest.TestCase):
    """Rendering-time narrowing of `|calls` tables."""

    def _doc(self, td):
        _put(td, "a.py",
             '"""A."""\n\n\ndef leaf(x):\n    return x\n\n\n'
             'def mid(x):\n    return leaf(x)\n\n\ndef other(x):\n    return x\n')
        _put(td, "b.py",
             '"""B."""\nfrom a import mid\n\n\ndef top():\n    return mid(1)\n')
        return tier.from_repo(td)

    def test_filter_keeps_only_matching_callers(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self._doc(td)
            keep = context._calls_filter_for(doc, ["a.py#mid"], "auto")
            rendered = tier.render_section(doc.by_id()["a.py"], "raw", keep)
            self.assertIn("|calls[1]{caller,callee,module}", rendered)
            self.assertIn("mid,leaf,.", rendered)
            self.assertNotIn("top,mid,a.py", rendered)

    def test_filter_none_keeps_the_stored_table(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self._doc(td)
            body = tier.render_section(doc.by_id()["a.py"])
            self.assertIn("|calls[", body)
            self.assertEqual(body, tier.render_section(doc.by_id()["a.py"], "raw", None))

    def test_filter_all_mode_is_unfiltered(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self._doc(td)
            self.assertIsNone(context._calls_filter_for(doc, ["a.py#mid"], "all"))

    def test_filter_none_mode_drops_the_table(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self._doc(td)
            drop = context._calls_filter_for(doc, ["a.py#mid"], "none")
            rendered = tier.render_section(doc.by_id()["a.py"], "raw", drop)
            self.assertNotIn("|calls[", rendered)

    def test_empty_result_omits_the_table_entirely(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self._doc(td)
            keep = context._calls_filter_for(doc, ["a.py#other"], "auto")
            rendered = tier.render_section(doc.by_id()["a.py"], "raw", keep)
            self.assertNotIn("|calls[", rendered,
                             "a filtered-to-empty table must not emit a zero-count header")
            self.assertIn("|symbols[", rendered)

    def test_filtered_render_still_parses_and_lints(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self._doc(td)
            keep = context._calls_filter_for(doc, ["a.py#mid"], "auto")
            rendered = tier.render_section(doc.by_id()["a.py"], "raw", keep)
            again = tier.parse(rendered)
            self.assertEqual([m for l, m in tier.lint(again, 10**9) if l == "error"], [])
            self.assertEqual(tier.dumps(tier.parse(tier.dumps(again))), tier.dumps(again))

    def test_impact_still_reads_the_full_table(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self._doc(td)
            res = tier.impact(doc, "a.py#leaf", root=td)
            self.assertEqual(len(res["symbol_dependents"]), 1)
            self.assertIn("a.py#mid", res["symbol_dependents"][0])

    def test_context_uses_the_filter_and_respects_budget(self):
        with tempfile.TemporaryDirectory() as td:
            self._doc(td)
            for budget in (200, 400, 900):
                res = context.build_context(td, "mid leaf", budget_tokens=budget)
                self.assertLessEqual(res["tokens"], budget, f"budget={budget}")

    def test_context_include_calls_all_keeps_full_table(self):
        with tempfile.TemporaryDirectory() as td:
            self._doc(td)
            auto = context.build_context(td, "mid leaf", budget_tokens=4000)
            full = context.build_context(td, "mid leaf", budget_tokens=4000,
                                         include_calls="all")
            self.assertGreaterEqual(full["tokens"], auto["tokens"])


class CacheTests(unittest.TestCase):
    def test_tracked_repo_tier_wins(self):
        with tempfile.TemporaryDirectory() as td:
            _put(td, "a.py", "def a():\n    return 1\n")
            os.makedirs(os.path.join(td, ".git", "info"), exist_ok=True)
            tracked = _put(td, "repo.tier", "%tier 1.1.0\n%title x\n@a A\nbody\n")
            path, root, kind = cache.resolve_doc(td)
            self.assertEqual(path, tracked)
            self.assertEqual(kind, "tracked")
            self.assertEqual(root, td)

    def test_git_info_cache_used_inside_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            _put(td, "a.py", "def a():\n    return 1\n")
            os.makedirs(os.path.join(td, ".git", "info"), exist_ok=True)
            path, root, kind = cache.resolve_doc(td)
            self.assertEqual(path, os.path.join(td, ".git", "info", "tier", "repo.tier"))
            self.assertEqual(kind, "git")
            self.assertEqual(root, td)

    def test_git_cache_keeps_git_status_clean(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        with tempfile.TemporaryDirectory() as td:
            _put(td, "a.py", '"""A."""\n\ndef a():\n    return 1\n')
            _put(td, ".gitignore", "*.pyc\n")
            subprocess.run(["git", "init", "-q", td], check=True, env=env)
            subprocess.run(["git", "-C", td, "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", td, "commit", "-qm", "init"], check=True, env=env)
            path, doc, root = cache.ensure_doc(td)
            self.assertEqual(path, os.path.join(td, ".git", "info", "tier", "repo.tier"))
            self.assertTrue(os.path.isfile(path))
            status = subprocess.run(
                ["git", "-C", td, "status", "--porcelain"],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(status.stdout.strip(), "", "git status must stay clean")

    def test_cache_fallback_outside_git(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cache_dir:
            _put(td, "a.py", "def a():\n    return 1\n")
            old = os.environ.get("TIER_CACHE_DIR")
            os.environ["TIER_CACHE_DIR"] = cache_dir
            try:
                path, root, kind = cache.resolve_doc(td)
                self.assertEqual(kind, "cache")
                self.assertTrue(path.startswith(os.path.abspath(cache_dir)))
                self.assertEqual(root, td)
                p, doc, _r = cache.ensure_doc(td)
                self.assertTrue(os.path.isfile(p))
                self.assertTrue(doc.sections)
                # the owner manifest lets a cached map find its sources again
                self.assertEqual(_read(os.path.join(os.path.dirname(p), ".owner")).strip(), td)
                self.assertEqual(cache.resolve_doc(p)[1], td)
            finally:
                if old is None:
                    os.environ.pop("TIER_CACHE_DIR", None)
                else:
                    os.environ["TIER_CACHE_DIR"] = old

    def test_auto_create_then_auto_sync(self):
        with tempfile.TemporaryDirectory() as td:
            _put(td, "a.py", '"""A."""\n\ndef a():\n    return 1\n')
            os.makedirs(os.path.join(td, ".git", "info"), exist_ok=True)
            path, doc, _root = cache.ensure_doc(td)
            names = {row["name"] for sec in doc.sections for row in tier._symbol_rows(sec)}
            self.assertIn("a", names)
            _put(td, "b.py", '"""B."""\n\ndef b():\n    return 2\n')
            path2, doc2, _r2 = cache.ensure_doc(td)
            names2 = {row["name"] for sec in doc2.sections for row in tier._symbol_rows(sec)}
            self.assertIn("b", names2)
            self.assertIn("b.py", _read(path2))

    def test_is_cache_dir_filters_listings(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".git", "info", "tier"), exist_ok=True)
            self.assertTrue(cache.is_cache_dir(os.path.join(td, ".git", "info", "tier")))
            self.assertTrue(cache.is_cache_dir(os.path.join(td, ".git", "info", "tier", "x")))
            self.assertFalse(cache.is_cache_dir(td))

    def test_mcp_tier_context_accepts_directory(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            from tier.mcp import server as tier_mcp

            old_root, old_state = tier_mcp.ROOT, dict(tier_mcp._SYNC_STATE)
            tier_mcp.ROOT = os.path.realpath(td)
            tier_mcp._SYNC_STATE.clear()
            try:
                text = tier_mcp.call("tier_context", {"query": "caller helper",
                                                      "budget_tokens": 1500})
                self.assertIn("@a.py", text)
                self.assertIn("## Impact:", text)
                listed = tier_mcp.call("tier_list", {})
                self.assertNotIn(".git", listed)
            finally:
                tier_mcp.ROOT = old_root
                tier_mcp._SYNC_STATE.clear()
                tier_mcp._SYNC_STATE.update(old_state)

    def test_mcp_tools_include_context_first(self):
        from tier.mcp import server as tier_mcp

        names = [t["name"] for t in tier_mcp.TOOLS]
        self.assertEqual(len(names), 12)
        self.assertEqual(names[0], "tier_context")
        self.assertEqual(len(set(names)), 12)

    def test_cache_escapes_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                cache.resolve_doc(os.path.join(td, "nope"))
            _put(td, "x.txt", "hi")
            with self.assertRaises(ValueError):
                cache.resolve_doc(os.path.join(td, "x.txt"))


class CallGraphTests(unittest.TestCase):
    def test_python_cross_module_call_edge(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            doc = tier.from_repo(td)
            rows = {}
            for sec in doc.sections:
                rel = tier._repo_file_meta(sec).get("path", "")
                for nd in tier.nodes(sec):
                    if nd["t"] == "table" and nd["name"] == "calls":
                        rows[rel] = nd["rows"]
            self.assertIn(["caller", "helper", "."], rows["a.py"])
            self.assertIn(["top", "caller", "a.py"], rows["b.py"])

    def test_impact_reports_symbol_callers(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            doc = tier.from_repo(td)
            res = tier.impact(doc, "a.py#helper", root=td)
            self.assertEqual(res["symbol_target"], "a.py#helper")
            self.assertEqual(len(res["symbol_dependents"]), 1)
            self.assertIn("a.py#caller", res["symbol_dependents"][0])
            self.assertRegex(res["symbol_dependents"][0], r"\[\d+-\d+\]")
            text = tier.render_impact(res)
            self.assertIn("Symbol callers:", text)

    def test_impact_reports_symbol_calls(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            doc = tier.from_repo(td)
            res = tier.impact(doc, "b.py#top", root=td)
            self.assertIn("a.py#caller", res["call_dependencies"])

    def test_call_edges_present_for_ts_go_rust(self):
        import bench

        with tempfile.TemporaryDirectory() as td:
            bench.write_polyglot(td)
            doc = tier.from_repo(td)
            edges = {}
            for sec in doc.sections:
                rel = tier._repo_file_meta(sec).get("path", "")
                for nd in tier.nodes(sec):
                    if nd["t"] == "table" and nd["name"] == "calls":
                        edges[rel] = nd["rows"]
            self.assertTrue(any(r[1] == "formatMoney" for r in edges.get("web/api.ts", [])),
                            "TypeScript call edge missing")
            self.assertTrue(any(r[1] == "normalize0" for r in edges.get("services/rs_0.rs", [])),
                            "Rust call edge missing")
            self.assertTrue(any(r[1] == "Normalize0" for r in edges.get("services/go_0.go", [])),
                            "Go call edge missing")

    def test_calls_table_omitted_when_empty(self):
        with tempfile.TemporaryDirectory() as td:
            _put(td, "quiet.py", '"""Quiet."""\n\ndef lonely():\n    return 1\n')
            doc = tier.from_repo(td)
            names = [nd["name"] for sec in doc.sections for nd in tier.nodes(sec)
                     if nd["t"] == "table"]
            self.assertIn("symbols", names)
            self.assertNotIn("calls", names)

    def test_calls_table_round_trips_and_lints(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            doc = tier.from_repo(td)
            out = tier.dumps(doc)
            self.assertEqual(tier.dumps(tier.parse(out)), out)
            self.assertEqual([m for l, m in tier.lint(doc, 10**9) if l == "error"], [])

    def test_sync_preserves_and_refreshes_call_edges(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            path = _put(td, "repo.tier", tier.dumps(tier.from_repo(td)))
            _put(td, "b.py",
                 '"""Beta."""\nfrom a import caller, helper\n\n\ndef top():\n'
                 '    return caller(helper(2))\n')
            res = tier.sync_repo(path, root=td)
            self.assertIn("b.py", res["changed"])
            rows = []
            for sec in res["doc"].sections:
                if tier._repo_file_meta(sec).get("path") == "b.py":
                    for nd in tier.nodes(sec):
                        if nd["t"] == "table" and nd["name"] == "calls":
                            rows = nd["rows"]
            self.assertIn(["top", "helper", "a.py"], rows)
            self.assertEqual([m for l, m in tier.lint(res["doc"], 10**9) if l == "error"], [])

    def test_added_file_does_not_reorder_untouched_sections(self):
        with tempfile.TemporaryDirectory() as td:
            _two_module_repo(td)
            path = _put(td, "repo.tier", tier.dumps(tier.from_repo(td)))
            _put(td, "c.py", '"""C."""\n\ndef c():\n    return 3\n')
            res = tier.sync_repo(path, root=td)
            self.assertIn("c.py", res["added"])
            for sec in res["doc"].sections:
                body = sec.lines
                if any(l.startswith("|calls[") for l in body):
                    first_ref = next((i for i, l in enumerate(body) if l.startswith("> ")), len(body))
                    last_call = max(i for i, l in enumerate(body) if l.startswith("|calls["))
                    self.assertLess(last_call, first_ref, "calls must precede refs")


class TreeSitterTests(unittest.TestCase):
    def tearDown(self):
        repo._TS_STATE.clear()

    def test_absent_extra_falls_back_to_stdlib(self):
        repo._TS_STATE["pack"] = None
        self.assertFalse(repo._ts_available())
        purpose, symbols, imports = repo._extract_symbols("x.py", '"""D."""\n\ndef f():\n    pass\n')
        self.assertIn("f", [s[1] for s in symbols])
        self.assertTrue(purpose)

    def test_ts_only_extensions_hidden_without_extra(self):
        self.assertFalse(repo.REPO_TS_ONLY_EXTS & repo.REPO_SOURCE_EXTS)
        repo._TS_STATE["pack"] = None
        with tempfile.TemporaryDirectory() as td:
            _put(td, "a.py", "def a():\n    return 1\n")
            _put(td, "b.c", "int b(void) { return 1; }\n")
            files = [rel for rel, _f, _e in repo.repo_source_files(td)]
            self.assertEqual(files, ["a.py"])

    def test_ts_only_extensions_visible_with_extra(self):
        repo._TS_STATE["pack"] = None
        with tempfile.TemporaryDirectory() as td:
            _put(td, "a.py", "def a():\n    return 1\n")
            _put(td, "b.c", "int b(void) { return 1; }\n")
            with mock.patch.object(repo, "_ts_available", return_value=True):
                files = [rel for rel, _f, _e in repo.repo_source_files(td)]
            self.assertIn("a.py", files)
            self.assertIn("b.c", files)

    def test_mocked_pack_is_used_and_failure_falls_back(self):
        class Node:
            def __init__(self, type_, name="", start=0, end=1, children=None):
                self.type = type_
                self._name = name
                self.start_point = (start, 0)
                self.end_point = (end, 0)
                self.start_byte, self.end_byte = 0, len(name)
                self.children = children or []

            def child_by_field_name(self, field):
                return None

        class Tree:
            root_node = Node("module", children=[Node("function_definition", "f", 1, 3)])

        class Parser:
            def parse(self, text):
                return Tree()

        class Pack:
            __version__ = "0.0-test"

            @staticmethod
            def get_parser(lang):
                return Parser()

        repo._TS_STATE["pack"] = Pack
        self.assertTrue(repo._ts_available())
        purpose, symbols, _imports = repo._extract_symbols("x.py", "def f():\n    pass\n")
        self.assertEqual([s[1] for s in symbols], ["f"])

        class Broken:
            __version__ = "broken"

            @staticmethod
            def get_parser(lang):
                raise RuntimeError("no grammar")

        repo._TS_STATE["pack"] = Broken
        purpose, symbols, _imports = repo._extract_symbols(
            "x.py", '"""Doc."""\n\ndef g():\n    return 1\n')
        self.assertIn("g", [s[1] for s in symbols], "must fall back to the stdlib extractor")

    def test_doctor_reports_treesitter(self):
        from tier import doctor

        with tempfile.TemporaryDirectory() as td:
            _put(td, "a.py", "def a():\n    return 1\n")
            checks = doctor.run_doctor(td, home=td, env={}, platform="linux",
                                       find_executable=lambda n: None)
            names = [c.name for c in checks]
            self.assertIn("treesitter", names)


class InstallerSkillTests(unittest.TestCase):
    def test_skill_mentions_context_tool(self):
        text = repo and __import__("tier.installer", fromlist=["x"]).skill_markdown("/tmp")
        self.assertIn("tier_context", text)
        self.assertIn("tier context", text)


if __name__ == "__main__":
    unittest.main()
