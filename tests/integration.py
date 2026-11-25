"""Exercise std.log through a real executable and inherited OS descriptors."""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

EXAMPLE = str(Path(sys.argv.pop(1)).resolve())
ROOT = Path(__file__).resolve().parent.parent
RUN = str(ROOT / "scripts/fd-log-run")
MESSAGES = {
    "err": "error: error 17\n",
    "warn": "warning: warning careful\nwarning(quiet): visible\n",
    "info": "info: info 23\ninfo(network): connected 42\n",
    "debug": "debug: debug abcd\n",
}
STDOUT = "stdout stays stdout\n"
STDERR = "stderr stays stderr\n"
DEFAULT_LOG = (
    "error: error 17\nwarning: warning careful\ninfo: info 23\n"
    "debug: debug abcd\ninfo(network): connected 42\nwarning(quiet): visible\n"
)


class Routing(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fd-log test '")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.logs = self.directory / "logs with spaces ' ; $"
        self.env = dict(os.environ, NO_COLOR="1", CLICOLOR_FORCE="0")
        self.env.pop("ZIG_LOG_FDS", None)

    def run_command(self, argv, **kwargs):
        return subprocess.run(argv, env=self.env, capture_output=True, text=True,
                              timeout=30, **kwargs)

    def assert_logs(self, expected=MESSAGES):
        for level, message in expected.items():
            self.assertEqual((self.logs / f"{level}.log").read_text(), message)

    def test_default_stderr(self):
        result = self.run_command([EXAMPLE], check=True)
        self.assertEqual(result.stdout, STDOUT)
        self.assertEqual(result.stderr, STDERR + DEFAULT_LOG)

    def test_launch_from_each_shell(self):
        commands = {
            "bash": ["bash", "--noprofile", "--norc", "-c",
                     'exec "$FD_LOG_RUN" "$FD_LOG_DIR" -- "$FD_LOG_EXAMPLE" --long'],
            "fish": ["fish", "--no-config", "-c",
                     '$FD_LOG_RUN "$FD_LOG_DIR" -- "$FD_LOG_EXAMPLE" --long'],
            "nu": ["nu", "--no-config-file", "-c",
                   '^$env.FD_LOG_RUN $env.FD_LOG_DIR -- $env.FD_LOG_EXAMPLE --long'],
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
        self.env.update(FD_LOG_RUN=RUN, FD_LOG_EXAMPLE=EXAMPLE,
                        FD_LOG_DIR=str(self.logs),
                        PATH=str(ROOT / "scripts") + os.pathsep + self.env["PATH"])
        for shell, argv in commands.items():
            with self.subTest(shell=shell):
                self.assertIsNotNone(shutil.which(argv[0]), f"missing test dependency: {argv[0]}")
                self.logs = (self.directory / "logs" if shell.endswith("-config")
                             else Path(self.env["FD_LOG_DIR"]))
                shutil.rmtree(self.logs, ignore_errors=True)
                result = self.run_command(argv, cwd=self.directory, check=True)
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
        self.assertEqual(lines[:6], MESSAGES["info"].splitlines() * 3)
        expected = {f"info: worker={worker} sequence={sequence} " + "y" * 2048
                    for worker in range(4) for sequence in range(50)}
        self.assertEqual(len(lines[6:]), len(expected))
        self.assertEqual(set(lines[6:]), expected)

    def test_partial_routes(self):
        self.logs.mkdir()
        self.env.update(FD_LOG_EXAMPLE=EXAMPLE, FD_LOG_DIR=str(self.logs))
        result = self.run_command([
            "bash", "-c", 'exec "$FD_LOG_EXAMPLE" --partial '
            '4>>"$FD_LOG_DIR/warn.log" 6>>"$FD_LOG_DIR/debug.log"'
        ], check=True)
        self.assertEqual(result.stdout, STDOUT)
        self.assertEqual(result.stderr, STDERR + MESSAGES["err"] + MESSAGES["info"])
        self.assert_logs({key: MESSAGES[key] for key in ("warn", "debug")})

    def test_unclaimed_descriptors_are_not_used(self):
        self.env["FD_LOG_EXAMPLE"] = EXAMPLE
        result = self.run_command([
            "bash", "-c", 'exec "$FD_LOG_EXAMPLE" 3>/dev/null 4>/dev/null '
            '5>/dev/null 6>/dev/null'
        ], check=True)
        self.assertEqual(result.stderr, STDERR + DEFAULT_LOG)

    def test_bad_descriptors_rejected(self):
        self.env.update(ZIG_LOG_FDS="1", FD_LOG_EXAMPLE=EXAMPLE)
        for redirections in (
            "3>/dev/null 4>/dev/null 5>/dev/null 6>&-",
            "3>/dev/null 4>/dev/null 5>/dev/null 6</dev/null",
        ):
            with self.subTest(redirections=redirections):
                result = self.run_command(["bash", "-c",
                    'exec "$FD_LOG_EXAMPLE" ' + redirections])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("InvalidDescriptor", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_launcher_preserves_arguments_and_status(self):
        args = ["a b", "'quoted'", "$(touch should-not-exist)", "", "--flag"]
        command = [sys.executable, "-c",
                   "import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(37)", *args]
        commands = {"launcher": [RUN, str(self.logs), "--", *command]}
        configs = ROOT / "examples/shell"
        for shell, filename, call in (
            ("bash", "config.sh", "log-files"),
            ("zsh", "config.sh", "log-files"),
            ("fish", "config.fish", "log-files"),
            ("nu", "config.nu", "log files"),
        ):
            # Nu uses literal JSON-style strings; Bash, Zsh, and Fish use shell quoting.
            quoted = (" ".join(map(json.dumps, command)) if shell == "nu"
                      else shlex.join(command))
            commands[shell] = [shell, "-c",
                f'source {json.dumps(str(configs / filename))}; {call} {quoted}']
        self.env["PATH"] = str(ROOT / "scripts") + os.pathsep + self.env["PATH"]
        for name, argv in commands.items():
            with self.subTest(launcher=name):
                result = self.run_command(argv, cwd=self.directory)
                self.assertEqual(result.returncode, 37, result.stderr)
                self.assertEqual(json.loads(result.stdout), args)


if __name__ == "__main__":
    unittest.main()
