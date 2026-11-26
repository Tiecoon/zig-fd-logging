"""Exercise the Python handler and real Zig collector in subprocesses."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

COLLECTOR = str(Path(sys.argv.pop(1)).resolve())
ROOT = Path(__file__).resolve().parent.parent

LOGGING = """
import logging
import sys
from zig_fd_logging import Handler
handler = HANDLER()
handler.terminator = '<end>\\n'
handler.setFormatter(logging.Formatter('%(levelname)s|%(name)s|%(message)s'))
handler.addFilter(lambda record: record.getMessage() != 'filtered')
logging.basicConfig(handlers=[handler], level=1)
logging.debug('debug %s', 'value')
logging.info('unicode 日本語')
logging.warning('filtered')
logging.getLogger('network').warning('warning')
try:
    raise ValueError('exception detail')
except ValueError:
    logging.exception('failed %d', 42)
logging.getLogger('quiet').setLevel(logging.ERROR)
logging.getLogger('quiet').warning('hidden')
print('stdout unchanged')
print('stderr unchanged', file=sys.stderr)
"""


class PythonClient(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="python fd-log '")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.logs = self.directory / "logs with spaces"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "python"),
                        PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("ZIG_LOG_FD", None)

    def run_python(self, code, collected=False, **kwargs):
        command = [sys.executable, "-c", textwrap.dedent(code)]
        if collected:
            command = [COLLECTOR, str(self.logs), "--", *command]
        return subprocess.run(command, env=self.env, capture_output=True,
                              text=True, timeout=30, **kwargs)

    def read_logs(self):
        return {p.name: p.read_text() for p in self.logs.glob("*.log")}

    def test_standard_streamhandler_equivalence(self):
        expected = self.run_python(LOGGING.replace("HANDLER()", "logging.StreamHandler()"),
                                   check=True)
        for marker in (None, "", "garbage", "-1", "1", "999999", "3.0", "３"):
            with self.subTest(marker=marker):
                if marker is None:
                    self.env.pop("ZIG_LOG_FD", None)
                else:
                    self.env["ZIG_LOG_FD"] = marker
                actual = self.run_python(LOGGING.replace("HANDLER()", "Handler()"), check=True)
                self.assertEqual(actual.stdout, expected.stdout)
                self.assertEqual(actual.stderr, expected.stderr)
        self.assertIn('ValueError: exception detail<end>', expected.stderr)
        self.assertNotIn('filtered', expected.stderr)
        self.assertNotIn('hidden', expected.stderr)

    def test_invalid_descriptor_types_and_flags(self):
        code = """
            import logging, os
            from zig_fd_logging import Handler
            kind = os.environ['FD_KIND']
            read, write = os.pipe()
            file = open(os.devnull, 'wb')
            fd = file.fileno() if kind == 'file' else read if kind == 'read' else write
            if kind == 'nonblocking':
                os.set_blocking(fd, False)
            if kind != 'unclaimed':
                os.environ['ZIG_LOG_FD'] = str(fd)
            logging.basicConfig(handlers=[Handler()], level=logging.INFO)
            logging.info('ordinary')
        """
        for kind in ('file', 'read', 'nonblocking', 'unclaimed'):
            with self.subTest(kind=kind):
                self.env['FD_KIND'] = kind
                result = self.run_python(code, check=True)
                self.assertEqual(result.stderr, 'INFO:root:ordinary\n')
                self.assertEqual(result.stdout, '')

    def test_collected_formatter_exception_and_filters(self):
        result = self.run_python(LOGGING.replace("HANDLER()", "Handler()"), True, check=True)
        self.assertEqual(result.stdout, 'stdout unchanged\n')
        self.assertEqual(result.stderr, 'stderr unchanged\n')
        logs = self.read_logs()
        self.assertEqual(set(logs), {'debug.log', 'info.log', 'scope-network.log', 'err.log'})
        self.assertEqual(logs['debug.log'], 'DEBUG|root|debug value<end>\n')
        self.assertEqual(logs['info.log'], 'INFO|root|unicode 日本語<end>\n')
        self.assertEqual(logs['scope-network.log'], 'WARNING|network|warning<end>\n')
        self.assertTrue(logs['err.log'].startswith('ERROR|root|failed 42\nTraceback'))
        self.assertTrue(logs['err.log'].endswith('ValueError: exception detail<end>\n'))

    def test_severity_boundaries_and_scope_encoding(self):
        result = self.run_python("""
            import logging
            from zig_fd_logging import Handler
            logging.basicConfig(handlers=[Handler()], level=1, format='%(message)s')
            for level in (1, 9, 10, 19, 20, 29, 30, 39, 40, 50, 60):
                logging.log(level, 'level %d', level)
            logging.info('surrogate \\udcff')
            for name in ('network', 'err', 'default', '../日本語'):
                logger = logging.getLogger(name)
                logger.info('first')
                logger.error('second')
        """, True, check=True)
        self.assertEqual(result.stderr, '')
        self.assertEqual(self.read_logs(), {
            'debug.log': ''.join(f'level {n}\n' for n in (1, 9, 10, 19)),
            'info.log': 'level 20\nlevel 29\nsurrogate \\udcff\n',
            'warn.log': 'level 30\nlevel 39\n',
            'err.log': 'level 40\nlevel 50\nlevel 60\n',
            'scope-network.log': 'first\nsecond\n',
            'scope-err.log': 'first\nsecond\n',
            'scope-default.log': 'first\nsecond\n',
            'scope-%2E%2E%2F%E6%97%A5%E6%9C%AC%E8%AA%9E.log': 'first\nsecond\n',
        })

    def test_long_concurrent_records_across_handler_instances(self):
        result = self.run_python("""
            import logging, threading
            from zig_fd_logging import Handler
            def worker(number):
                logger = logging.getLogger('worker%d' % number)
                logger.setLevel(logging.INFO)
                logger.addHandler(Handler())
                for sequence in range(25):
                    logger.info('%d:%d:%s', number, sequence, '日' * 8192)
            threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        """, True, check=True)
        self.assertEqual(result.stderr, '')
        logs = self.read_logs()
        self.assertEqual(len(logs), 6)
        for number in range(6):
            self.assertEqual(logs[f'scope-worker{number}.log'], ''.join(
                f'{number}:{sequence}:' + '日' * 8192 + '\n' for sequence in range(25)))

    def test_preserved_stdio_status_and_borrowed_fd(self):
        result = self.run_python("""
            import logging, os, subprocess, sys
            from zig_fd_logging import Handler
            fd = int(os.environ['ZIG_LOG_FD'])
            handler = Handler()
            assert not os.get_inheritable(fd)
            logging.basicConfig(handlers=[handler], level=logging.INFO)
            logging.info('before close')
            handler.close()
            os.fstat(fd)
            subprocess.run([sys.executable, '-c',
                "import logging; from zig_fd_logging import Handler; "
                "logging.basicConfig(handlers=[Handler()]); logging.warning('child fallback')"],
                close_fds=False, check=True)
            print(sys.stdin.read(), end='')
            print('direct stderr', file=sys.stderr)
            sys.exit(37)
        """, True, input='inherited stdin\n')
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual(result.stdout, 'inherited stdin\n')
        self.assertEqual(result.stderr, 'WARNING:root:child fallback\ndirect stderr\n')
        self.assertEqual(self.read_logs(), {'info.log': 'INFO:root:before close\n'})

    def test_collection_with_closed_standard_descriptors(self):
        result = subprocess.run([
            'bash', '-c', 'exec "$@" 0<&- 1>&- 2>&-', 'closed-stdio',
            COLLECTOR, str(self.logs), '--', sys.executable, '-c',
            "import logging; from zig_fd_logging import Handler; "
            "logging.basicConfig(handlers=[Handler()], level=logging.INFO); "
            "logging.info('collected without standard streams')",
        ], env=self.env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_logs(), {
            'info.log': 'INFO:root:collected without standard streams\n',
        })

    def test_broken_pipe_retries_and_stays_on_standard_stream(self):
        read, write = os.pipe()
        os.close(read)
        self.addCleanup(os.close, write)
        self.env['ZIG_LOG_FD'] = str(write)
        result = self.run_python(LOGGING.replace("HANDLER()", "Handler()"),
                                 pass_fds=(write,), check=True)
        expected = self.run_python(LOGGING.replace("HANDLER()", "logging.StreamHandler()"),
                                   pass_fds=(write,), check=True)
        self.assertEqual(result.stdout, expected.stdout)
        self.assertEqual(result.stderr, expected.stderr)

    def test_real_collector_failure_falls_back(self):
        self.logs.mkdir()
        (self.logs / 'info.log').mkdir()  # A real collector output-open failure.
        result = self.run_python("""
            import logging, os
            from zig_fd_logging import Handler
            logging.basicConfig(handlers=[Handler()], level=logging.INFO)
            logging.info('triggers collector failure')
            fd = int(os.environ['ZIG_LOG_FD'])
            import select
            poll = select.poll()
            poll.register(fd, select.POLLERR)
            assert poll.poll(5000), 'collector did not disconnect'
            logging.warning('fallback after collector failure')
            logging.error('still on stderr')
        """, True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('WARNING:root:fallback after collector failure\n', result.stderr)
        self.assertIn('ERROR:root:still on stderr\n', result.stderr)
        self.assertIn('fd-log-run:', result.stderr)

    def test_broken_pipe_disables_all_handlers_and_fork_falls_back(self):
        read, write = os.pipe()
        os.close(read)
        self.addCleanup(os.close, write)
        self.env['ZIG_LOG_FD'] = str(write)
        result = self.run_python("""
            import logging
            from zig_fd_logging import Handler
            h1, h2 = Handler(), Handler()
            l1, l2 = logging.getLogger('one'), logging.getLogger('two')
            l1.setLevel(logging.INFO); l2.setLevel(logging.INFO)
            l1.addHandler(h1); l2.addHandler(h2)
            l1.info('first')
            assert h1._fd is None
            l2.info('second')
            assert h2._fd is None
        """, pass_fds=(write,), check=True)
        self.assertEqual(result.stderr, 'first\nsecond\n')

        if hasattr(os, 'fork'):
            forked = self.run_python("""
                import logging, os
                from zig_fd_logging import Handler
                logging.basicConfig(handlers=[Handler()], level=logging.INFO, format='%(message)s')
                logging.info('parent before')
                pid = os.fork()
                if pid == 0:
                    logging.info('child fallback')
                    os._exit(0)
                _, status = os.waitpid(pid, 0)
                assert os.waitstatus_to_exitcode(status) == 0
                logging.info('parent after')
            """, True, check=True)
            self.assertEqual(forked.stderr, 'child fallback\n')
            self.assertEqual(self.read_logs(), {'info.log': 'parent before\nparent after\n'})


if __name__ == '__main__':
    unittest.main()
