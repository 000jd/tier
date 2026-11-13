import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import tier
from tier import doctor, installer
from tier.ui import UI


def _ui():
    return UI(stream=io.StringIO(), use_rich=False, color=False)


def _put(root, name, text):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _which(name):
    if name == "tier-mcp":
        return None
    if name == "tier":
        return "/usr/bin/tier"
    if name in ("python3", "python"):
        return sys.executable
    return shutil.which(name)


def _snapshot(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            with open(full, "rb") as f:
                out[rel] = f.read()
    return out


class InstallerTests(unittest.TestCase):
    def test_client_paths_linux_macos_windows(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            linux = {s.id: s for s in installer.client_specs(root, home, {}, "linux")}
            self.assertEqual(
                linux["claude-desktop"].config_path,
                os.path.join(home, ".config", "Claude", "claude_desktop_config.json"),
            )
            self.assertEqual(linux["cursor"].config_path, os.path.join(root, ".cursor", "mcp.json"))
            cursor_global = linux.get("cursor-user") or linux["cursor-global"]
            self.assertEqual(cursor_global.config_path, os.path.join(home, ".cursor", "mcp.json"))
            # the pre-1.0.1 id still resolves through the alias table
            self.assertIn("cursor-user", installer.parse_clients("cursor-global", list(linux.values())))
            self.assertEqual(linux["vscode"].config_key, "servers")
            self.assertEqual(
                linux["windsurf"].config_path,
                os.path.join(home, ".codeium", "windsurf", "mcp_config.json"),
            )

            mac = {s.id: s for s in installer.client_specs(root, home, {}, "darwin")}
            self.assertEqual(
                mac["claude-desktop"].config_path,
                os.path.join(home, "Library", "Application Support", "Claude",
                             "claude_desktop_config.json"),
            )

            env = {"APPDATA": os.path.join(home, "AppData", "Roaming")}
            win = {s.id: s for s in installer.client_specs(root, home, env, "win32")}
            self.assertEqual(
                win["claude-desktop"].config_path,
                os.path.join(env["APPDATA"], "Claude", "claude_desktop_config.json"),
            )

    def test_detection_and_invalid_config(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, ".mcp.json", json.dumps({"mcpServers": {"other": {"command": "x"}}}))
            specs = {s.id: s for s in installer.client_specs(root, home, {}, "linux")}
            self.assertTrue(specs["claude-code"].detected)
            self.assertFalse(specs["claude-code"].configured)
            _put(root, ".mcp.json", json.dumps(
                {"mcpServers": {"tier": {"command": "x", "args": []}}}))
            specs = {s.id: s for s in installer.client_specs(root, home, {}, "linux")}
            self.assertTrue(specs["claude-code"].configured)
            _put(root, ".mcp.json", "{broken")
            specs = {s.id: s for s in installer.client_specs(root, home, {}, "linux")}
            self.assertTrue(specs["claude-code"].parse_error)

    def test_server_entry_absolute_and_vscode_type(self):
        root = tempfile.gettempdir()
        entry = installer.server_entry(root, "vscode", lambda name: "/opt/bin/tier-mcp")
        self.assertEqual(entry["command"], os.path.abspath("/opt/bin/tier-mcp"))
        self.assertEqual(entry["args"], [root])
        self.assertEqual(entry["type"], "stdio")
        fallback = installer.server_entry(root, "cursor", lambda name: None)
        self.assertEqual(fallback["command"], os.path.abspath(sys.executable))
        self.assertEqual(fallback["args"][:2], ["-m", "tier.mcp"])

    def test_install_merges_preserves_backs_up_and_summarizes(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(root, ".mcp.json", json.dumps({
                "mcpServers": {"other": {"command": "other"}},
                "keep": True,
            }))
            desktop = os.path.join(home, ".config", "Claude", "claude_desktop_config.json")
            _put(home, os.path.relpath(desktop, home), json.dumps({
                "mcpServers": {"desktop-other": {"command": "d"}},
            }))
            _put(root, ".vscode/mcp.json", json.dumps({"servers": {"vscode-other": {"command": "v"}}}))
            _put(home, ".codeium/windsurf/mcp_config.json", json.dumps({
                "mcpServers": {"wind-other": {"command": "w"}},
            }))
            _put(root, ".claude/settings.json", json.dumps({
                "hooks": {"PostToolUse": [{
                    "matcher": "Existing",
                    "hooks": [{"type": "command", "command": "other-cmd"}],
                }]},
                "keep": "yes",
            }))
            result = installer.install(
                root, clients=["claude-code", "claude-desktop", "vscode", "windsurf"],
                yes=True, home=home, env={}, platform="linux", ui=_ui(), find_executable=_which,
            )
            self.assertTrue(result["changes"])
            data = json.loads(_read(os.path.join(root, ".mcp.json")))
            self.assertIn("other", data["mcpServers"])
            self.assertTrue(data["keep"])
            self.assertIn("tier", data["mcpServers"])
            self.assertTrue(os.path.exists(os.path.join(root, ".mcp.json.bak")))
            self.assertEqual(json.loads(_read(os.path.join(root, ".mcp.json.bak")))["mcpServers"]["other"]["command"], "other")
            self.assertIn("desktop-other", json.loads(_read(desktop))["mcpServers"])
            self.assertIn("vscode-other", json.loads(_read(os.path.join(root, ".vscode/mcp.json")))["servers"])
            self.assertIn("wind-other", json.loads(_read(os.path.join(
                home, ".codeium/windsurf/mcp_config.json")))["mcpServers"])
            settings = json.loads(_read(os.path.join(root, ".claude/settings.json")))
            commands = json.dumps(settings)
            hook_cmds = tier._hook_commands(settings["hooks"]["PostToolUse"])
            self.assertIn("other-cmd", commands)
            self.assertTrue(any(installer._is_tier_hook(c) for c in hook_cmds))
            self.assertTrue(settings["keep"])
            self.assertIn(tier.TIER_BEGIN, _read(os.path.join(root, "CLAUDE.md")))
            self.assertIn(tier.TIER_BEGIN, _read(os.path.join(root, "AGENTS.md")))
            self.assertTrue(os.path.exists(os.path.join(root, "repo.tier")))
            self.assertGreater(result["repo"]["raw_tokens"], 0)

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            kwargs = dict(clients=["claude-code", "vscode"], yes=True, home=home,
                          env={}, platform="linux", ui=_ui(), find_executable=_which)
            installer.install(root, **kwargs)
            before = _snapshot(root), _snapshot(home)
            installer.install(root, **kwargs)
            after = _snapshot(root), _snapshot(home)
            self.assertEqual(before, after)
            settings = json.loads(_read(os.path.join(root, ".claude/settings.json")))
            hook_cmds = tier._hook_commands(settings["hooks"]["PostToolUse"])
            self.assertEqual(sum(1 for c in hook_cmds if installer._is_tier_hook(c)), 1)
            self.assertEqual(_read(os.path.join(root, "CLAUDE.md")).count(tier.TIER_BEGIN), 1)

    def test_dry_run_makes_no_mutations(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(root, ".mcp.json", json.dumps({"mcpServers": {"other": {"command": "x"}}}))
            _put(home, ".claude.json", json.dumps({"mcpServers": {}}))
            before = _snapshot(root), _snapshot(home)
            ui = _ui()
            installer.install(root, clients="all", yes=True, dry_run=True, home=home,
                              env={}, platform="linux", ui=ui, find_executable=_which)
            after = _snapshot(root), _snapshot(home)
            self.assertEqual(before, after)
            self.assertIn("Preview of config changes", ui.stream.getvalue())
            self.assertIn("+++", ui.stream.getvalue())
            self.assertFalse(os.path.exists(os.path.join(root, "repo.tier")))
            self.assertFalse(os.path.exists(os.path.join(root, ".mcp.json.bak")))

    def test_uninstall_removes_only_tier(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(root, ".mcp.json", json.dumps({"mcpServers": {"other": {"command": "x"}}}))
            _put(root, ".claude/settings.json", json.dumps({
                "hooks": {"PostToolUse": [
                    {"matcher": "Existing", "hooks": [{"type": "command", "command": "other-cmd"}]},
                ]},
            }))
            installer.install(root, clients=["claude-code"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which)
            installer.install(root, clients=["claude-code"], uninstall=True, yes=True,
                              home=home, env={}, platform="linux", ui=_ui())
            data = json.loads(_read(os.path.join(root, ".mcp.json")))
            self.assertIn("other", data["mcpServers"])
            self.assertNotIn("tier", data["mcpServers"])
            settings = json.loads(_read(os.path.join(root, ".claude/settings.json")))
            commands = json.dumps(settings)
            hook_cmds = tier._hook_commands(settings["hooks"]["PostToolUse"])
            self.assertIn("other-cmd", commands)
            self.assertFalse(any(installer._is_tier_hook(c) for c in hook_cmds))
            self.assertNotIn(tier.TIER_BEGIN, _read(os.path.join(root, "CLAUDE.md")))

    def test_no_repo_no_hook_no_instructions(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            installer.install(root, clients=["claude-code"], yes=True, repo=False, hook=False,
                              instructions="none", home=home, env={}, platform="linux",
                              ui=_ui(), find_executable=_which)
            self.assertTrue(os.path.exists(os.path.join(root, ".mcp.json")))
            self.assertFalse(os.path.exists(os.path.join(root, "repo.tier")))
            self.assertFalse(os.path.exists(os.path.join(root, ".claude/settings.json")))
            self.assertFalse(os.path.exists(os.path.join(root, "CLAUDE.md")))

    def test_unbalanced_markers_backup_and_preserve_user_content(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            user = (
                "# Project notes\n\n"
                "Keep this paragraph intact.\n\n"
                f"{tier.TIER_BEGIN}\n"
                "stale TIER block that never closes\n\n"
                "## Later user notes\n\n"
                "Do not lose this either.\n"
            )
            _put(root, "CLAUDE.md", user)
            before = _read(os.path.join(root, "CLAUDE.md"))
            result = installer.install(
                root, clients=["claude-code"], yes=True, home=home, env={}, platform="linux",
                ui=_ui(), find_executable=_which,
            )
            after = _read(os.path.join(root, "CLAUDE.md"))
            self.assertIn("Keep this paragraph intact.", after)
            self.assertIn("## Later user notes", after)
            self.assertIn("Do not lose this either.", after)
            self.assertIn("stale TIER block that never closes", after)
            self.assertEqual(after.count(tier.TIER_BEGIN), 2)
            self.assertTrue(os.path.exists(os.path.join(root, "CLAUDE.md.bak")))
            self.assertEqual(_read(os.path.join(root, "CLAUDE.md.bak")), before)
            self.assertTrue(any("unbalanced" in w for w in result["warnings"]))

            installer.install(root, clients=["claude-code"], uninstall=True, yes=True,
                              home=home, env={}, platform="linux", ui=_ui())
            uninstalled = _read(os.path.join(root, "CLAUDE.md"))
            # The user's stray BEGIN is their own content and must survive, but
            # TIER's complete block (and therefore its END marker) must be gone.
            self.assertEqual(uninstalled.count(tier.TIER_BEGIN), 1)
            self.assertNotIn(tier.TIER_END, uninstalled)
            self.assertNotIn("L0 repository index", uninstalled)
            self.assertIn("stale TIER block that never closes", uninstalled)
            self.assertIn("Keep this paragraph intact.", uninstalled)
            self.assertIn("## Later user notes", uninstalled)
            self.assertIn("Do not lose this either.", uninstalled)

    def test_orphan_end_marker_is_preserved(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            original = "Intro\n\n" + tier.TIER_END + "\n\nOutro\n"
            _put(root, "AGENTS.md", original)
            result = installer.install(
                root, clients=["claude-code"], yes=True, instructions="agents", repo=False,
                hook=False, home=home, env={}, platform="linux", ui=_ui(),
                find_executable=_which,
            )
            text = _read(os.path.join(root, "AGENTS.md"))
            self.assertTrue(text.startswith(original.rstrip("\n")))
            self.assertIn(tier.TIER_BEGIN, text)
            self.assertTrue(any("unbalanced" in w for w in result["warnings"]))
            installer.install(root, clients=["claude-code"], uninstall=True, yes=True,
                              home=home, env={}, platform="linux", ui=_ui())
            after = _read(os.path.join(root, "AGENTS.md"))
            self.assertNotIn(tier.TIER_BEGIN, after)
            self.assertIn(tier.TIER_END, after)
            self.assertIn("Intro", after)
            self.assertIn("Outro", after)

    def test_balanced_markers_replace_in_place(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            existing = (
                "Header\n\n"
                f"{tier.TIER_BEGIN}\nold block\n{tier.TIER_END}\n\n"
                "Footer\n"
            )
            _put(root, "CLAUDE.md", existing)
            result = installer.install(
                root, clients=["claude-code"], yes=True, home=home, env={}, platform="linux",
                ui=_ui(), find_executable=_which,
            )
            text = _read(os.path.join(root, "CLAUDE.md"))
            self.assertEqual(text.count(tier.TIER_BEGIN), 1)
            self.assertEqual(text.count(tier.TIER_END), 1)
            self.assertNotIn("old block", text)
            self.assertIn("Footer", text)
            self.assertFalse(result["warnings"])

    def test_setup_alias_cli(self):
        from tier import cli

        with tempfile.TemporaryDirectory() as root:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            out = io.StringIO()
            with __import__("contextlib").redirect_stdout(out):
                code = cli.main(["setup", root, "--clients", "claude-code", "--yes",
                                 "--no-repo", "--no-hook", "--instructions", "none", "--plain"])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(os.path.join(root, ".mcp.json")))

    def test_interactive_selection_and_yes_default(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            specs = installer.client_specs(root, home, {}, "linux")
            nums = {s.id: str(i) for i, s in enumerate(specs, 1)}
            choice = nums["claude-code"] + "," + nums["cursor"]
            answers = iter([root, choice, "y", "y", "both"])
            installer.install(root, interactive=True, home=home, env={}, platform="linux",
                              ask=lambda prompt: next(answers), ui=_ui(), find_executable=_which)
            self.assertTrue(os.path.exists(os.path.join(root, ".mcp.json")))
            self.assertTrue(os.path.exists(os.path.join(root, ".cursor/mcp.json")))
            self.assertFalse(os.path.exists(os.path.join(root, ".vscode/mcp.json")))
            self.assertTrue(os.path.exists(os.path.join(root, ".claude/settings.json")))

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            installer.install(root, yes=True, home=home, env={}, platform="linux",
                              ui=_ui(), find_executable=_which)
            self.assertTrue(os.path.exists(os.path.join(root, ".mcp.json")))
            self.assertFalse(os.path.exists(os.path.join(root, ".vscode/mcp.json")))


class MultiAgentTests(unittest.TestCase):
    """Every supported agent, schema, and merge/uninstall path."""

    def test_catalog_covers_all_documented_clients(self):
        specs = {s.id: s for s in installer.client_specs("/p", "/h", {}, "linux")}
        expected = {
            "claude-code", "claude-code-user", "claude-desktop",
            "command-code", "command-code-user",
            "opencode", "opencode-user", "codex", "codex-user",
            "gemini", "gemini-user", "cursor", "cursor-user", "vscode", "windsurf",
            "roo", "trae", "junie", "junie-user", "kiro", "warp", "zed",
            "continue", "skills", "skills-user",
        }
        self.assertEqual(expected, set(specs))

    def test_exact_paths_and_schemas(self):
        specs = {s.id: s for s in installer.client_specs(
            "/p", "/h", {"APPDATA": "/appdata"}, "linux")}
        cases = {
            "command-code": ("/p/.commandcode/mcp.json", "mcpServers"),
            "command-code-user": ("/h/.commandcode/mcp.json", "mcpServers"),
            "opencode": ("/p/opencode.json", "opencode"),
            "opencode-user": ("/h/.config/opencode/opencode.json", "opencode"),
            "codex": ("/p/.codex/config.toml", "codex-toml"),
            "codex-user": ("/h/.codex/config.toml", "codex-toml"),
            "gemini": ("/p/.gemini/settings.json", "mcpServers"),
            "gemini-user": ("/h/.gemini/settings.json", "mcpServers"),
            "cursor": ("/p/.cursor/mcp.json", "mcpServers"),
            "cursor-user": ("/h/.cursor/mcp.json", "mcpServers"),
            "vscode": ("/p/.vscode/mcp.json", "servers"),
            "windsurf": ("/h/.codeium/windsurf/mcp_config.json", "mcpServers"),
            "roo": ("/p/.roo/mcp.json", "mcpServers"),
            "trae": ("/p/.trae/mcp.json", "mcpServers"),
            "junie": ("/p/.junie/mcp/mcp.json", "mcpServers"),
            "junie-user": ("/h/.junie/mcp/mcp.json", "mcpServers"),
            "kiro": ("/p/.kiro/settings/mcp.json", "mcpServers"),
            "warp": ("/h/.warp/.mcp.json", "mcpServers"),
            "zed": ("/h/.config/zed/settings.json", "context_servers"),
            "continue": ("/p/.continue/mcpServers/tier.yaml", "continue-yaml"),
            "skills": ("/p/.agents/skills/tier/SKILL.md", "skill-md"),
            "skills-user": ("/h/.agents/skills/tier/SKILL.md", "skill-md"),
        }
        for client_id, (path, schema) in cases.items():
            self.assertEqual(specs[client_id].config_path, path, client_id)
            self.assertEqual(specs[client_id].schema, schema, client_id)

    def test_open_code_uses_mcp_key_and_command_list(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(root, "opencode.json", json.dumps({
                "$schema": "https://opencode.ai/schema.json",
                "model": "gpt-5",
                "mcp": {"other": {"type": "local", "command": ["o"], "enabled": True}},
            }))
            installer.install(root, clients=["opencode"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              instructions="none", repo=False, hook=False)
            data = json.loads(_read(os.path.join(root, "opencode.json")))
            self.assertEqual(data["$schema"], "https://opencode.ai/schema.json")
            self.assertEqual(data["model"], "gpt-5")
            self.assertIn("other", data["mcp"])
            tier_entry = data["mcp"]["tier"]
            self.assertEqual(tier_entry["type"], "local")
            self.assertTrue(tier_entry["enabled"])
            self.assertIsInstance(tier_entry["command"], list)
            self.assertEqual(tier_entry["command"][-1], os.path.abspath(root))

            installer.install(root, clients=["opencode"], uninstall=True, yes=True, home=home,
                              env={}, platform="linux", ui=_ui())
            after = json.loads(_read(os.path.join(root, "opencode.json")))
            self.assertNotIn("tier", after["mcp"])
            self.assertIn("other", after["mcp"])
            self.assertEqual(after["model"], "gpt-5")

    def test_codex_toml_preserves_other_tables_and_uninstalls_cleanly(self):
        original = (
            "[features]\n"
            "web_search = true\n"
            "\n"
            "[mcp_servers.other]\n"
            'command = "other"\n'
            "args = []\n"
        )
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(home, ".codex/config.toml", original)
            installer.install(root, clients=["codex-user"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              instructions="none", repo=False, hook=False)
            path = os.path.join(home, ".codex/config.toml")
            text = _read(path)
            self.assertIn("[features]", text)
            self.assertIn("web_search = true", text)
            self.assertIn("[mcp_servers.other]", text)
            self.assertIn("[mcp_servers.tier]", text)
            block = installer._parse_own_toml_block(text)
            expected = installer.command_argv(root, _which)
            self.assertEqual(block["command"], expected[0])
            self.assertEqual(block["args"], expected[1:])
            self.assertEqual(block["args"][-1], os.path.abspath(root))
            self.assertTrue(os.path.exists(path + ".bak"))
            self.assertEqual(_read(path + ".bak"), original)
            # idempotent
            installer.install(root, clients=["codex-user"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              instructions="none", repo=False, hook=False)
            self.assertEqual(_read(path).count("[mcp_servers.tier]"), 1)

            installer.install(root, clients=["codex-user"], uninstall=True, yes=True, home=home,
                              env={}, platform="linux", ui=_ui())
            self.assertEqual(_read(path), original)

    def test_zed_context_servers_shape(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(home, ".config/zed/settings.json", json.dumps({
                "theme": "One Dark",
                "context_servers": {"other": {"command": {"path": "x", "args": []}}},
            }))
            installer.install(root, clients=["zed"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              instructions="none", repo=False, hook=False)
            data = json.loads(_read(os.path.join(home, ".config/zed/settings.json")))
            self.assertEqual(data["theme"], "One Dark")
            self.assertIn("other", data["context_servers"])
            entry = data["context_servers"]["tier"]
            expected = installer.command_argv(root, _which)
            self.assertEqual(entry["command"]["path"], expected[0])
            self.assertEqual(entry["command"]["args"], expected[1:])
            self.assertEqual(entry["command"]["args"][-1], os.path.abspath(root))

            installer.install(root, clients=["zed"], uninstall=True, yes=True, home=home,
                              env={}, platform="linux", ui=_ui())
            after = json.loads(_read(os.path.join(home, ".config/zed/settings.json")))
            self.assertNotIn("tier", after["context_servers"])

    def test_continue_yaml_written_and_removed(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            installer.install(root, clients=["continue"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              instructions="none", repo=False, hook=False)
            path = os.path.join(root, ".continue/mcpServers/tier.yaml")
            text = _read(path)
            self.assertIn("mcpServers:", text)
            self.assertIn("name: tier", text)
            parsed = installer._parse_continue_yaml(text)
            expected = installer.command_argv(root, _which)
            self.assertEqual(parsed["command"], expected[0])
            self.assertEqual(parsed["args"], expected[1:])
            self.assertEqual(parsed["args"][-1], os.path.abspath(root))

            installer.install(root, clients=["continue"], uninstall=True, yes=True, home=home,
                              env={}, platform="linux", ui=_ui())
            self.assertFalse(os.path.exists(path))

    def test_skill_md_has_frontmatter_and_tool_docs(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            installer.install(root, clients=["skills"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              instructions="none", repo=False, hook=False)
            path = os.path.join(root, ".agents/skills/tier/SKILL.md")
            text = _read(path)
            self.assertTrue(text.startswith("---\nname: tier\n"))
            self.assertIn("description:", text)
            for tool in ("tier_index", "tier_find", "tier_get", "tier_slice",
                         "tier_impact", "tier_remember"):
                self.assertIn(tool, text)
            for cmd in ("tier index", "tier find", "tier slice", "tier impact"):
                self.assertIn(cmd, text)
            installer.install(root, clients=["skills"], uninstall=True, yes=True, home=home,
                              env={}, platform="linux", ui=_ui())
            self.assertFalse(os.path.exists(path))

    def test_gemini_writes_settings_and_gemini_md(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            installer.install(root, clients=["gemini"], yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              repo=False, hook=False, instructions="both")
            data = json.loads(_read(os.path.join(root, ".gemini/settings.json")))
            self.assertIn("tier", data["mcpServers"])
            gemini_md = _read(os.path.join(root, "GEMINI.md"))
            self.assertIn(tier.TIER_BEGIN, gemini_md)
            self.assertIn(tier.TIER_END, gemini_md)
            self.assertIn("tier index", gemini_md)

    def test_every_json_client_installs_and_uninstalls(self):
        json_clients = ["command-code", "command-code-user", "gemini", "gemini-user",
                        "cursor", "cursor-user", "roo", "trae", "junie", "junie-user",
                        "kiro", "warp", "vscode", "windsurf", "claude-desktop"]
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            specs = {s.id: s for s in installer.client_specs(
                root, home, {}, "linux", find_executable=_which)}
            installer.install(root, clients=json_clients, yes=True, home=home, env={},
                              platform="linux", ui=_ui(), find_executable=_which,
                              instructions="none", repo=False, hook=False)
            for client_id in json_clients:
                spec = specs[client_id]
                self.assertTrue(os.path.exists(spec.config_path), client_id)
                probe = installer.probe_entry(spec)
                self.assertTrue(probe["present"], client_id)
                self.assertEqual(probe["entry"]["args"][-1], os.path.abspath(root), client_id)
            checks = doctor.run_doctor(root, home=home, env={}, platform="linux",
                                       find_executable=_which)
            self.assertFalse([c for c in checks if c.status == "FAIL"],
                             [c.detail for c in checks if c.status == "FAIL"])

            installer.install(root, clients=json_clients, uninstall=True, yes=True, home=home,
                              env={}, platform="linux", ui=_ui())
            for client_id in json_clients:
                self.assertFalse(installer.probe_entry(specs[client_id])["present"], client_id)

    def test_custom_config_json_toml_yaml_and_skill(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            cases = [
                ("my/mcp.json", "mcpServers"),
                ("my/servers.json", "servers"),
                ("my/config.toml", "codex-toml"),
                ("my/tier.yaml", "continue-yaml"),
                ("my/SKILL.md", "skill-md"),
            ]
            for rel, schema in cases:
                installer.install(root, yes=True, home=home, env={}, platform="linux",
                                  ui=_ui(), find_executable=_which, instructions="none",
                                  repo=False, hook=False, custom_config=rel, schema=schema)
                path = os.path.join(root, rel)
                self.assertTrue(os.path.exists(path), rel)
                text = _read(path)
                if schema == "codex-toml":
                    self.assertIn("[mcp_servers.tier]", text)
                elif schema == "continue-yaml":
                    self.assertIn("mcpServers:", text)
                elif schema == "skill-md":
                    self.assertIn("name: tier", text)
                else:
                    data = json.loads(text)
                    self.assertIn("tier", data[schema])

    def test_custom_config_schema_inference_and_validation(self):
        self.assertEqual(installer.infer_schema("x/config.toml"), "codex-toml")
        self.assertEqual(installer.infer_schema("x/tier.yaml"), "continue-yaml")
        self.assertEqual(installer.infer_schema("x/opencode.json"), "opencode")
        self.assertEqual(installer.infer_schema("x/SKILL.md"), "skill-md")
        self.assertEqual(installer.infer_schema("x/mcp.json"), "mcpServers")
        with self.assertRaises(ValueError):
            installer.make_custom_spec("/tmp/x.json", "bogus-schema")

    def test_detected_selection_shortcut(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            os.makedirs(os.path.join(root, ".codex"))
            os.makedirs(os.path.join(home, ".commandcode"))
            specs = installer.client_specs(root, home, {}, "linux")
            detected = set(installer.detect_clients(specs))
            self.assertIn("codex", detected)
            self.assertIn("command-code-user", detected)

            answers = iter([root, "d", "n", "n", "none"])
            installer.install(root, interactive=True, home=home, env={}, platform="linux",
                              ask=lambda prompt: next(answers), ui=_ui(),
                              find_executable=_which)
            self.assertTrue(os.path.exists(os.path.join(root, ".codex/config.toml")))
            self.assertTrue(os.path.exists(os.path.join(home, ".commandcode/mcp.json")))

    def test_print_config_covers_every_schema(self):
        text = installer.render_snippets("/proj", lambda name: "/opt/bin/tier-mcp")
        for needle in ('"mcpServers"', '"servers"', '"mcp"', '"context_servers"',
                       "[mcp_servers.tier]", "mcpServers:", "name: tier", "claude mcp add",
                       "codex mcp add", "opencode mcp add", "gemini mcp add"):
            self.assertIn(needle, text, needle)
        self.assertIn("/opt/bin/tier-mcp", text)

    def test_cli_custom_config_and_snippet_commands(self):
        from tier import cli

        with tempfile.TemporaryDirectory() as root:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            out = io.StringIO()
            with __import__("contextlib").redirect_stdout(out):
                code = cli.main(["install", root, "--custom-config", "custom/mcp.json",
                                 "--schema", "servers", "--yes", "--no-repo",
                                 "--no-hook", "--instructions", "none", "--plain"])
            self.assertEqual(code, 0)
            custom = json.loads(_read(os.path.join(root, "custom/mcp.json")))
            self.assertIn("servers", custom)
            self.assertEqual(custom["servers"]["tier"]["type"], "stdio")

            out = io.StringIO()
            with __import__("contextlib").redirect_stdout(out):
                code = cli.main(["config-snippet", root])
            self.assertEqual(code, 0)
            self.assertIn("context_servers", out.getvalue())

            out = io.StringIO()
            with __import__("contextlib").redirect_stdout(out):
                code = cli.main(["clients", root])
            self.assertEqual(code, 0)
            self.assertIn("opencode", out.getvalue())


class DoctorTests(unittest.TestCase):
    def _healthy(self, root, home):
        _put(root, "demo.py", "def demo():\n    return 1\n")
        doc = tier.from_repo(root)
        _put(root, "repo.tier", tier.dumps(doc))
        entry = {"command": sys.executable, "args": ["-m", "tier.mcp", os.path.abspath(root)]}
        _put(root, ".mcp.json", json.dumps({"mcpServers": {"tier": entry, "other": {"command": "x"}}}))
        _put(home, ".claude/settings.json", json.dumps({
            "hooks": {"PostToolUse": [{"matcher": "X", "hooks": [
                {"type": "command", "command": "python -m tier hook --max-tokens 600"},
            ]}]},
        }))

    def test_doctor_healthy_and_json(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            self._healthy(root, home)
            checks = doctor.run_doctor(root, home=home, env={}, platform="linux",
                                       find_executable=_which)
            self.assertEqual(doctor.exit_code(checks), 0)
            self.assertFalse(any(c.status == "FAIL" for c in checks))
            payload = json.loads(doctor.render_json(checks))
            self.assertTrue(payload)
            ui = _ui()
            doctor.render(checks, ui)
            self.assertIn("PASS", ui.stream.getvalue())

    def test_doctor_cli(self):
        env = dict(os.environ, PYTHONPATH=SRC,
                   HOME=tempfile.mkdtemp(prefix="tier-doctor-home-"))
        with tempfile.TemporaryDirectory() as root:
            self._healthy(root, env["HOME"])
            proc = __import__("subprocess").run(
                [sys.executable, "-m", "tier", "doctor", root, "--plain"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("PASS", proc.stdout)

    def test_doctor_broken_config_and_stale_repo(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(root, "repo.tier", tier.dumps(tier.from_repo(root)))
            _put(root, ".mcp.json", "{broken")
            checks = doctor.run_doctor(root, home=home, env={}, platform="linux",
                                       find_executable=_which)
            self.assertEqual(doctor.exit_code(checks), 1)
            self.assertTrue(any(c.status == "FAIL" for c in checks))

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _put(root, "demo.py", "def demo():\n    return 1\n")
            _put(root, "repo.tier", tier.dumps(tier.from_repo(root)))
            _put(root, "demo.py", "def demo():\n    return 2\n")
            checks = doctor.run_doctor(root, home=home, env={}, platform="linux",
                                       find_executable=_which)
            self.assertTrue(any(c.name == "repo sync" and c.status == "WARN" for c in checks))
            self.assertEqual(doctor.exit_code(checks), 0)


class SDKAdapterTests(unittest.TestCase):
    def test_v2_and_v1_loader_fallbacks(self):
        from tier.mcp import sdk_server
        import types

        class V2:
            pass

        class V1:
            pass

        mcp = types.ModuleType("mcp")
        server = types.ModuleType("mcp.server")
        server.MCPServer = V2
        server.__path__ = []
        mcp.server = server
        with mock.patch.dict(sys.modules, {"mcp": mcp, "mcp.server": server}):
            cls, label = sdk_server._load_server_class()
        self.assertIs(cls, V2)
        self.assertEqual(label, "MCPServer")

        mcp1 = types.ModuleType("mcp")
        server1 = types.ModuleType("mcp.server")
        server1.__path__ = []
        fast = types.ModuleType("mcp.server.fastmcp")
        fast.FastMCP = V1
        mcp1.server = server1
        with mock.patch.dict(sys.modules, {
            "mcp": mcp1, "mcp.server": server1, "mcp.server.fastmcp": fast,
        }):
            cls, label = sdk_server._load_server_class()
        self.assertIs(cls, V1)
        self.assertEqual(label, "FastMCP")

    def test_missing_sdk_message(self):
        from tier.mcp import sdk_server

        stderr = io.StringIO()
        with mock.patch.object(sdk_server, "_load_server_class", return_value=(None, "")):
            with redirect_stderr(stderr):
                code = sdk_server.main(["--root", tempfile.gettempdir()])
        self.assertEqual(code, 1)
        self.assertIn('pip install "tier-ctx[mcp]"', stderr.getvalue())

    def test_all_eleven_tools_register_and_call(self):
        from tier.mcp import sdk_server

        class FakeServer:
            def __init__(self, name):
                self.name = name
                self.tools = {}
                self.run_kwargs = None

            def tool(self, **kwargs):
                def decorator(fn):
                    self.tools[kwargs.get("name", fn.__name__)] = fn
                    return fn
                return decorator

            def run(self, **kwargs):
                self.run_kwargs = kwargs

        with tempfile.TemporaryDirectory() as root:
            _put(root, "demo.py", '"""Demo."""\ndef demo():\n    return 1\n')
            _put(root, "repo.tier", tier.dumps(tier.from_repo(root)))
            srv = sdk_server.build_server(root, server_cls=FakeServer)
            self.assertEqual(set(srv.tools), {t["name"] for t in sdk_server.stdio.TOOLS})
            self.assertEqual(len(srv.tools), 11)
            self.assertIn("@demo.py", srv.tools["tier_index"]("repo.tier"))
            self.assertIn("repo.tier", srv.tools["tier_list"]())

        fake = FakeServer("tier")
        with mock.patch.object(sdk_server, "build_server", return_value=fake):
            code = sdk_server.main(["--root", tempfile.gettempdir(),
                                    "--transport", "streamable-http", "--port", "9123"])
        self.assertEqual(code, 0)
        self.assertEqual(fake.run_kwargs["transport"], "streamable-http")
        self.assertEqual(fake.run_kwargs["port"], 9123)
        stdio_fake = FakeServer("tier")
        with mock.patch.object(sdk_server, "build_server", return_value=stdio_fake):
            code = sdk_server.main(["--root", tempfile.gettempdir()])
        self.assertEqual(code, 0)
        self.assertEqual(stdio_fake.run_kwargs, {"transport": "stdio"})


if __name__ == "__main__":
    unittest.main()
