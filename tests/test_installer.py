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
            self.assertEqual(linux["cursor-global"].config_path, os.path.join(home, ".cursor", "mcp.json"))
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
            answers = iter([root, "1,4", "y", "y", "both"])
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
