"""Real multiplexer regression tests; the collector alone is a fixture.

Run: python3 tests/panes.py (tmux and Rex tests skip if the CLI is absent).
All sessions, sockets, logs, and the fixture collector are disposable.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
PANES = str(ROOT / "scripts/fd-log-panes")
STREAMS = ["err", "scope-err", "scope-three", "scope-net%20work%27%2F%25",
           "scope-five", "scope-six", "scope-%E2%98%83"]
ARGUMENT = "spaces ' quotes ; $HOME"


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="fd-log panes '")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.logs = self.directory / "logs ' ; $"
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        collector = self.bin / "fd-log-run"
        collector.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
hook = None
if args[0] == "--on-stream":
    hook, args = args[1], args[2:]
directory = pathlib.Path(args[0])
assert args[1] == "--"
directory.mkdir(parents=True, exist_ok=True)
for stream in json.loads(os.environ.get("TEST_STREAMS", "[]")):
    with (directory / (stream + ".log")).open("a") as output:
        output.write("record " + stream + "\\n")
    if hook:
        assert pathlib.Path(hook).is_absolute()
        subprocess.run([hook, str(directory), stream], check=True)
os.execvp(args[2], args[2:])
''')
        collector.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{ROOT / 'scripts'}:{os.environ['PATH']}")
        for name in ("REX_SESSION", "REX_BLOCK", "TMUX", "TMUX_PANE"):
            self.env.pop(name, None)

    def run_command(self, command, **kwargs):
        return subprocess.run(command, env=self.env, text=True, capture_output=True,
                              timeout=60, **kwargs)

    def checked(self, command):
        result = self.run_command(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def launch(self, backend, streams=STREAMS):
        self.env["TEST_STREAMS"] = json.dumps(streams)
        result = self.run_command([PANES, backend, str(self.logs), "--", sys.executable,
                                   "-c", "import sys; print(sys.argv[1]); sys.exit(7)", ARGUMENT])
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, ARGUMENT + "\n")
        self.assertEqual(result.stderr, "")
        for stream in streams:
            self.assertIn("record " + stream, (self.logs / (stream + ".log")).read_text())
        self.assertFalse((self.logs / "warn.log").exists())

    def state(self, backend):
        paths = [p for p in self.logs.glob(f".fd-log-{backend}-*") if p.suffix != ".lock"]
        self.assertEqual(len(paths), 1)
        return {row[0]: row[1:3] for line in paths[0].read_text().splitlines()
                if (row := line.split("\t"))}

    def check_isolation(self, backend):
        original = self.state(backend)
        saved = {p: p.read_text() for p in self.logs.glob(f".fd-log-{backend}-*")
                 if p.suffix != ".lock"}
        variable = "TMUX_PANE" if backend == "tmux" else "REX_BLOCK"
        caller = self.env[variable]
        self.env[variable] = self.unrelated
        self.launch(backend, ["err"])
        paths = [p for p in self.logs.glob(f".fd-log-{backend}-*") if p.suffix != ".lock"]
        self.assertEqual(len(paths), 2)
        for path, value in saved.items():
            self.assertEqual(path.read_text(), value)
        self.env[variable] = caller
        self.logs = self.directory / "another directory '"
        self.launch(backend, ["err"])
        self.assertNotEqual(self.state(backend)["err"][0], original["err"][0])


class Shells(Fixture):
    def test_configs_preserve_arguments_and_exit_status(self):
        configs = ROOT / "examples/shell"
        for shell, flags, config, invoke in [
            ("bash", ["--noprofile", "--norc"], "config.sh", 'log-files "$TEST_PYTHON" -c \'import sys; print(sys.argv[1]); sys.exit(7)\' "$TEST_ARGUMENT"'),
            ("zsh", ["-f"], "config.sh", 'log-files "$TEST_PYTHON" -c \'import sys; print(sys.argv[1]); sys.exit(7)\' "$TEST_ARGUMENT"'),
            ("fish", ["--no-config"], "config.fish", 'log-files "$TEST_PYTHON" -c \'import sys; print(sys.argv[1]); sys.exit(7)\' "$TEST_ARGUMENT"'),
            ("nu", ["--no-config-file"], "config.nu", 'log files $env.TEST_PYTHON -c \'import sys; print(sys.argv[1]); sys.exit(7)\' $env.TEST_ARGUMENT; exit $env.LAST_EXIT_CODE'),
        ]:
            with self.subTest(shell=shell):
                if not shutil.which(shell):
                    self.skipTest(f"{shell} is not installed")
                self.env.update(TEST_PYTHON=sys.executable, TEST_ARGUMENT=ARGUMENT)
                code = f'source "{configs / config}"; {invoke}'
                result = self.run_command([shell, *flags, "-c", code], cwd=self.directory)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertEqual(result.stdout, ARGUMENT + "\n")

    def test_missing_backend_context_and_invalid_stream(self):
        for backend in ("tmux", "rex"):
            if shutil.which(backend):
                result = self.run_command([PANES, backend, str(self.logs), "--", "true"])
                self.assertEqual(result.returncode, 2, result.stderr)
        for stream in ("../escape", "scope-../escape", "scope-%ff", "scope-a.b", "other"):
            result = self.run_command([str(ROOT / "scripts/fd-log-view"), str(self.logs), stream])
            self.assertEqual(result.returncode, 2, result.stderr)


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class Tmux(Fixture):
    def tmux(self, *args):
        return self.checked(["tmux", "-S", str(self.socket), *args])

    def setUp(self):
        super().setUp()
        self.socket = self.directory / "tmux.sock"
        caller = self.tmux("-f", "/dev/null", "new-session", "-d", "-x", "200", "-y", "120",
                           "-P", "-F", "#{pane_id}", "sleep", "1200")
        self.addCleanup(lambda: self.run_command(["tmux", "-S", str(self.socket), "kill-server"]))
        self.env["TMUX"] = self.tmux("display-message", "-p", "-t", caller, "#{socket_path},#{pid},0")
        self.env["TMUX_PANE"] = caller
        self.unrelated = self.tmux("split-window", "-d", "-v", "-t", caller,
                                   "-P", "-F", "#{pane_id}", "sleep", "1200")
        self.tmux("select-pane", "-t", self.unrelated)
        self.unrelated_geometry = self.geometry(self.unrelated)

    def geometry(self, pane):
        return self.tmux("display-message", "-p", "-t", pane,
                         "#{pane_left},#{pane_top},#{pane_width},#{pane_height}")

    def assert_layout(self, state):
        self.assertEqual(self.geometry(self.unrelated), self.unrelated_geometry)
        self.assertEqual(self.tmux("display-message", "-p", "#{pane_id}"), self.unrelated)
        self.assertEqual(int(self.tmux("list-panes", "-F", "#{pane_id}").count("%")), len(state) + 2)
        caller = list(map(int, self.geometry(self.env["TMUX_PANE"]).split(",")))
        heights = []
        for pane, pid in state.values():
            x, y, width, height = map(int, self.geometry(pane).split(","))
            self.assertGreater(x, caller[0])
            self.assertLess(y, caller[1] + caller[3])
            self.assertLessEqual(abs(width - caller[2]), 1)
            self.assertEqual(self.tmux("display-message", "-p", "-t", pane, "#{pane_pid}"), pid)
            heights.append(height)
        self.assertLessEqual(max(heights) - min(heights), 1)

    def test_dynamic_reuse_closed_and_dead_viewer(self):
        self.launch("tmux")
        first = self.state("tmux")
        self.assertEqual(set(first), set(STREAMS))
        self.assert_layout(first)
        for stream, (pane, _) in first.items():
            self.assertIn("record " + stream, self.tmux("capture-pane", "-p", "-t", pane))
        self.launch("tmux")
        self.assertEqual(self.state("tmux"), first)
        self.tmux("kill-pane", "-t", first[STREAMS[2]][0])
        self.launch("tmux")
        second = self.state("tmux")
        self.assertNotEqual(second.pop(STREAMS[2]), first[STREAMS[2]])
        self.assertEqual(second, {k: v for k, v in first.items() if k != STREAMS[2]})
        self.assert_layout(self.state("tmux"))
        # A retained pane whose tail has exited is not a surviving viewer.
        pane = second[STREAMS[0]][0]
        self.tmux("set-option", "-p", "-t", pane, "remain-on-exit", "on")
        pid = int(self.tmux("display-message", "-p", "-t", pane, "#{pane_pid}"))
        os.kill(pid, signal.SIGTERM)
        self.launch("tmux")
        self.assertNotEqual(self.state("tmux")[STREAMS[0]][0], pane)
        self.assert_layout(self.state("tmux"))
        self.check_isolation("tmux")


@unittest.skipUnless(shutil.which("rex"), "Rex is not installed")
class Rex(Fixture):
    def rex(self, *args):
        return self.checked(["rex", *args])

    def setUp(self):
        super().setUp()
        def block(label):
            return {"block": {"flavor": "com.superlogical.terminal.shell", "label": label,
                              "options": {"shell": "none", "login": False, "command": ["sleep", "1200"]}}}
        created = json.loads(self.rex("api", "call", "session.create", json.dumps({
            "label": "fd-log disposable test", "initial_windows": [{"layout": {"split": {
                "direction": "vertical", "ratio": 0.75,
                "before": block("caller"), "after": block("unrelated"),
            }}}],
        })))
        self.session = created["session_id"]
        self.addCleanup(lambda: self.run_command(["rex", "kill", self.session]))
        self.env["REX_SESSION"] = self.session
        self.env["REX_BLOCK"], self.unrelated = created["initial_windows"][0]["block_ids"]
        self.rex("focus", self.unrelated)

    def view(self):
        return json.loads(self.rex("session", "inspect", self.session, "--json"))

    def assert_layout(self, state):
        view = self.view()
        window = view["windows"][0]
        self.assertEqual(window["focused_block_id"], self.unrelated)
        rects = {}
        def walk(node):
            if "split" in node:
                walk(node["split"]["before"])
                walk(node["split"]["after"])
            else:
                rects[node["block_id"]] = node["rect"]
        walk(window["layers"][0]["layout"])
        self.assertEqual(len(rects), len(state) + 2)
        self.assertEqual(rects[self.unrelated], [0, 0.75, 1, 0.25])
        self.assertEqual(rects[self.env["REX_BLOCK"]], [0, 0, 0.5, 0.75])
        for pane, pid in state.values():
            x, y, width, height = rects[pane]
            self.assertEqual((x, width), (0.5, 0.5))
            self.assertAlmostEqual(height, 0.75 / len(state))
            process = json.loads(self.rex("block", "call", "-b", pane, "process"))
            self.assertIsNone(process["last_exit"])
            self.assertEqual(process["child"]["pid"], int(pid))
            self.assertEqual(process["child"]["argv0"], "tail")

    def test_dynamic_reuse_closed_and_dead_viewer(self):
        self.launch("rex")
        first = self.state("rex")
        self.assertEqual(set(first), set(STREAMS))
        self.assert_layout(first)
        for stream, (pane, _) in first.items():
            self.assertIn("record " + stream, self.rex("capture", "-b", pane, "--trim"))
        self.launch("rex")
        self.assertEqual(self.state("rex"), first)
        self.rex("block", "close", first[STREAMS[2]][0])
        self.launch("rex")
        second = self.state("rex")
        self.assertNotEqual(second.pop(STREAMS[2]), first[STREAMS[2]])
        self.assertEqual(second, {k: v for k, v in first.items() if k != STREAMS[2]})
        self.assert_layout(self.state("rex"))
        pane, pid = second[STREAMS[0]]
        os.kill(int(pid), signal.SIGTERM)
        self.launch("rex")
        self.assertNotEqual(self.state("rex")[STREAMS[0]][0], pane)
        self.assert_layout(self.state("rex"))
        self.check_isolation("rex")


if __name__ == "__main__":
    unittest.main(verbosity=2)
