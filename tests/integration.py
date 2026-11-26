"""Exercise standard logging and optional collection through real processes."""
import json
import os
from pathlib import Path
import shutil
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest

EXAMPLE = str(Path(sys.argv.pop(1)).resolve())
RUN = str(Path(sys.argv.pop(1)).resolve())
ROOT = Path(__file__).resolve().parent.parent
MESSAGES = {
    "err": "error: error 17\n",
    "warn": "warning: warning careful\n",
    "info": "info: info 23\n",
    "debug": "debug: debug abcd\n",
    "scope-network": "info(network): connected 42\n",
    "scope-quiet": "warning(quiet): visible\n",
}
STDOUT = "stdout stays stdout\n"
STDERR = "stderr stays stderr\n"
DEFAULT_LOG = (
    "error: error 17\nwarning: warning careful\ninfo: info 23\n"
    "debug: debug abcd\ninfo(network): connected 42\nwarning(quiet): visible\n"
)


class Collection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fd-log test '")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.logs = self.directory / "logs with spaces ' ; $"
        self.env = dict(os.environ, NO_COLOR="1", CLICOLOR_FORCE="0")
        self.env.pop("ZIG_LOG_FD", None)
        self.env.pop("FD_LOG_DIR", None)
        self.env.update(FD_LOG_RUN=RUN, FD_LOG_EXAMPLE=EXAMPLE,
                        TEST_LOG_DIR=str(self.logs),
                        PATH=os.pathsep.join([str(Path(RUN).parent),
                                              str(ROOT / "scripts"), self.env["PATH"]]))

    def run_command(self, argv, **kwargs):
        return subprocess.run(argv, env=self.env, capture_output=True, text=True,
                              timeout=30, **kwargs)

    def assert_logs(self, expected=MESSAGES):
        self.assertEqual({p.stem for p in self.logs.glob("*.log")}, set(expected))
        for name, message in expected.items():
            self.assertEqual((self.logs / f"{name}.log").read_text(), message)

    def test_standard_logging_without_collector(self):
        for value in (None, "", "garbage", "-1", "1", "999999"):
            with self.subTest(marker=value):
                if value is None:
                    self.env.pop("ZIG_LOG_FD", None)
                else:
                    self.env["ZIG_LOG_FD"] = value
                result = self.run_command([EXAMPLE], check=True)
                self.assertEqual(result.stdout, STDOUT)
                self.assertEqual(result.stderr, STDERR + DEFAULT_LOG)

    def test_unclaimed_and_invalid_descriptors_are_not_used(self):
        for marker in (None, "3"):
            with self.subTest(marker=marker):
                if marker:
                    self.env["ZIG_LOG_FD"] = marker
                result = self.run_command(["bash", "-c",
                    'exec "$FD_LOG_EXAMPLE" 3>/dev/null 4>/dev/null 5>/dev/null 6>/dev/null'], check=True)
                self.assertEqual(result.stderr, STDERR + DEFAULT_LOG)

    def test_launch_from_each_shell(self):
        commands = {
            "bash": ["bash", "--noprofile", "--norc", "-c",
                     'exec "$FD_LOG_RUN" "$TEST_LOG_DIR" -- "$FD_LOG_EXAMPLE" --long'],
            "fish": ["fish", "--no-config", "-c",
                     '$FD_LOG_RUN "$TEST_LOG_DIR" -- "$FD_LOG_EXAMPLE" --long'],
            "nu": ["nu", "--no-config-file", "-c",
                   '^$env.FD_LOG_RUN $env.TEST_LOG_DIR -- $env.FD_LOG_EXAMPLE --long'],
        }
        configs = ROOT / "examples/shell"
        commands.update({
            "bash-config": ["bash", "--noprofile", "--norc", "-c",
                f'source {json.dumps(str(configs / "config.sh"))}; '
                'log-files "$FD_LOG_EXAMPLE" --long'],
            "zsh-config": ["zsh", "-f", "-c",
                f'source {json.dumps(str(configs / "config.sh"))}; '
                'log-files "$FD_LOG_EXAMPLE" --long'],
            "fish-config": ["fish", "--no-config", "-c",
                f'source {json.dumps(str(configs / "config.fish"))}; '
                'log-files "$FD_LOG_EXAMPLE" --long'],
            "nu-config": ["nu", "--no-config-file", "-c",
                f'source {json.dumps(str(configs / "config.nu"))}; '
                'log files $env.FD_LOG_EXAMPLE --long'],
        })
        for shell, argv in commands.items():
            with self.subTest(shell=shell):
                self.assertIsNotNone(shutil.which(argv[0]), f"missing test dependency: {argv[0]}")
                self.logs = (self.directory / "logs" if shell.endswith("-config")
                             else Path(self.env["TEST_LOG_DIR"]))
                shutil.rmtree(self.logs, ignore_errors=True)
                result = self.run_command(argv, cwd=self.directory, check=True)
                self.assertEqual(result.stdout, STDOUT)
                self.assertEqual(result.stderr, STDERR)
                expected = dict(MESSAGES)
                expected["info"] += "info: " + "x" * 8192 + "\ninfo: after long message\n"
                self.assert_logs(expected)
        # Verify FD_LOG_DIR overrides the default ./logs in all shell configs.
        override = self.directory / "custom logs ' dir"
        self.env["FD_LOG_DIR"] = str(override)
        for shell in ("bash-config", "zsh-config", "fish-config", "nu-config"):
            with self.subTest(fd_log_dir=shell):
                self.logs = override
                shutil.rmtree(self.logs, ignore_errors=True)
                result = self.run_command(commands[shell], cwd=self.directory, check=True)
                self.assertEqual(result.stdout, STDOUT)
                self.assertEqual(result.stderr, STDERR)
                expected = dict(MESSAGES)
                expected["info"] += "info: " + "x" * 8192 + "\ninfo: after long message\n"
                self.assert_logs(expected)

    def test_append_and_concurrent_records(self):
        for _ in range(2):
            self.run_command([RUN, str(self.logs), "--", EXAMPLE], check=True)
        self.assert_logs({key: value * 2 for key, value in MESSAGES.items()})
        self.run_command([RUN, str(self.logs), "--", EXAMPLE, "--concurrent"], check=True)
        lines = (self.logs / "info.log").read_text().splitlines()
        self.assertEqual(lines[:3], MESSAGES["info"].splitlines() * 3)
        expected = {f"info: worker={worker} sequence={sequence} " + "y" * 2048
                    for worker in range(4) for sequence in range(50)}
        self.assertEqual(len(lines[3:]), len(expected))
        self.assertEqual(set(lines[3:]), expected)

    def test_discovery_scope_names_and_multiline_messages(self):
        result = self.run_command([RUN, str(self.logs), "--", EXAMPLE, "--scopes"], check=True)
        self.assertEqual(result.stdout, STDOUT)
        self.assertEqual(result.stderr, STDERR)
        expected = dict(MESSAGES)
        expected.update({
            "scope-network": MESSAGES["scope-network"] + "error(network): disconnected\n",
            "scope-database": "info(database): transaction\ncommitted\n",
            "scope-err": "warning(err): a scope, not a level\n",
            "scope-%2E%2E%2Fname%20with%20%27quotes%27%20%24%3B%0A":
                "debug(../name with 'quotes' $;\n): safe filename\n",
            "scope-%E6%97%A5%E6%9C%AC%E8%AA%9E": "info(日本語): unicode scope\n",
            "scope-worker": "info(worker): working\n",
        })
        self.assert_logs(expected)

    def test_broken_collector_falls_back_to_standard_logging(self):
        read, write = os.pipe()
        os.close(read)
        self.addCleanup(os.close, write)
        self.env["ZIG_LOG_FD"] = str(write)
        result = self.run_command([EXAMPLE, "--long"], pass_fds=(write,), check=True)
        self.assertEqual(result.stdout, STDOUT)
        self.assertEqual(result.stderr, STDERR + DEFAULT_LOG +
                         "info: " + "x" * 8192 + "\ninfo: after long message\n")

    def test_launcher_preserves_stdio_arguments_and_status(self):
        args = ["a b", "'quoted'", "$(touch should-not-exist)", "", "--flag"]
        command = [sys.executable, "-c",
                   "import json,sys; print(json.dumps(sys.argv[1:])); "
                   "print(sys.stdin.read(),end=''); print('direct stderr',file=sys.stderr); sys.exit(37)", *args]
        commands = {"collector": [RUN, str(self.logs), "--", *command]}
        configs = ROOT / "examples/shell"
        for shell, flags, filename, call in (
            ("bash", ["--noprofile", "--norc"], "config.sh", "log-files"),
            ("zsh", ["-f"], "config.sh", "log-files"),
            ("fish", ["--no-config"], "config.fish", "log-files"),
            ("nu", ["--no-config-file"], "config.nu", "log files"),
        ):
            quoted = (" ".join(map(json.dumps, command)) if shell == "nu"
                      else shlex.join(command))
            commands[shell] = [shell, *flags, "-c",
                f'source {json.dumps(str(configs / filename))}; {call} {quoted}']
        for name, argv in commands.items():
            with self.subTest(launcher=name):
                result = self.run_command(argv, cwd=self.directory, input="inherited stdin\n")
                self.assertEqual(result.returncode, 37, result.stderr)
                first, rest = result.stdout.split("\n", 1)
                self.assertEqual(json.loads(first), args)
                self.assertEqual(rest, "inherited stdin\n")
                self.assertEqual(result.stderr, "direct stderr\n")

    def test_caller_opened_extra_descriptors_are_inherited(self):
        output = self.directory / "extra fd"
        code = "import os; os.write(7,b'extra descriptor\\n'); print('normal stdout')"
        result = self.run_command([
            "bash", "--noprofile", "--norc", "-c",
            'exec "$1" "$2" -- "$3" -c "$4" 7>"$5"',
            "fd-test", RUN, str(self.logs), sys.executable, code, str(output),
        ], check=True)
        self.assertEqual(output.read_text(), "extra descriptor\n")
        self.assertEqual(result.stdout, "normal stdout\n")
        self.assertEqual(result.stderr, "")

    def test_fragmented_frames_and_hook_arguments(self):
        hook = self.directory / "hook with ' quotes"
        hook.write_text(f"#!{sys.executable}\nimport json,sys\n"
                        "from pathlib import Path\n"
                        "with (Path(sys.argv[1]) / 'discovered').open('a') as f: "
                        "f.write(json.dumps(sys.argv[1:])+'\\n')\n")
        hook.chmod(0o755)
        frames = b"".join(struct.pack("<BIQ", 2, len(scope), len(message)) + scope + message
                          for scope, message in [(b"network", b"one\nembedded\x00byte\n"),
                                                 (b"", b"two\n"),
                                                 (b"network", b"three\n")])
        program = ("import os; fd=int(os.environ['ZIG_LOG_FD']); "
                   f"data={frames!r}; "
                   "[os.write(fd,data[i:i+3]) for i in range(0,len(data),3)]")
        self.run_command([RUN, "--on-stream", str(hook), str(self.logs), "--",
                          sys.executable, "-c", program], check=True)
        self.assertEqual((self.logs / "scope-network.log").read_bytes(), b"one\nembedded\x00byte\nthree\n")
        self.assertEqual((self.logs / "info.log").read_text(), "two\n")
        self.assertEqual([json.loads(line) for line in (self.logs / "discovered").read_text().splitlines()],
                         [[str(self.logs), "scope-network"], [str(self.logs), "info"]])

    def test_truncated_transport_reports_failure(self):
        for frame in (b"\x02", struct.pack("<BIQ", 2, 7, 100) + b"networkshort"):
            with self.subTest(frame=frame):
                result = self.run_command([RUN, str(self.logs), "--", sys.executable, "-c",
                    f"import os; os.write(int(os.environ['ZIG_LOG_FD']),{frame!r})"])
                self.assertEqual(result.returncode, 1)
                self.assertIn("fd-log-run:", result.stderr)

    def test_hook_stdio_isolated_and_many_scopes_evict_fds(self):
        hook = self.directory / "noisy-hook"
        hook.write_text(f"#!{sys.executable}\nimport sys\n"
                        "assert sys.stdin.read() == ''\n"
                        "print('hook stdout must be ignored')\n")
        hook.chmod(0o755)
        program = (
            "import os, struct, sys; fd=int(os.environ['ZIG_LOG_FD'])\n"
            "for round in range(2):\n"
            "    for i in range(80):\n"
            "        s=f's{i}'.encode(); m=f'{round}:{i}\\n'.encode()\n"
            "        os.write(fd, struct.pack('<BIQ', 2, len(s), len(m)) + s + m)\n"
            "print(sys.stdin.read(), end='')\n"
        )
        result = self.run_command([RUN, "--on-stream", str(hook), str(self.logs), "--",
                                   sys.executable, "-c", program],
                                  input="app stdin preserved\n", check=True)
        self.assertEqual(result.stdout, "app stdin preserved\n")
        self.assertEqual(result.stderr, "")
        for i in range(80):
            self.assertEqual((self.logs / f"scope-s{i}.log").read_text(), f"0:{i}\n1:{i}\n")

    def test_sigterm_forwards_and_drains_shutdown_logs(self):
        import signal
        ready = self.directory / "ready"
        program = (
            "import os, signal, struct, time\n"
            "fd = int(os.environ['ZIG_LOG_FD'])\n"
            "def on_term(_sig, _frame):\n"
            "    msg = b'graceful shutdown\\n'\n"
            "    os.write(fd, struct.pack('<BIQ', 2, 0, len(msg)) + msg)\n"
            "    raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM, on_term)\n"
            f"open({str(ready)!r}, 'w').close()\n"
            "time.sleep(30)\n"
        )
        proc = subprocess.Popen([RUN, str(self.logs), "--", sys.executable, "-c", program],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = __import__("time").monotonic() + 10
            while not ready.exists():
                self.assertLess(__import__("time").monotonic(), deadline)
                __import__("time").sleep(0.02)
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual((self.logs / "info.log").read_text(), "graceful shutdown\n")


if __name__ == "__main__":
    unittest.main()
