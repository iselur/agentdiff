"""What happens when somebody presses ctrl-c.

Reviewing a large repository takes a moment, and a moment is long enough to
change your mind in.  Interrupting is an ordinary thing to do to a command that
is taking longer than you expected — it should not be answered with twenty
lines of interpreter internals ending in ``KeyboardInterrupt``, which reads as a
crash and sends people looking for the bug they just caused.

The exit code carries the other half of it.  This tool is meant to sit in front
of a commit, so `agentdiff review && git commit` must not commit because the
review was abandoned rather than passed.  130 is the shell's own way of spelling
"stopped by ctrl-c", which is exactly what happened.
"""

import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdiff import cli  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCtrlC(unittest.TestCase):

    def setUp(self):
        self.real = {name: getattr(cli, name)
                     for name in ("cmd_review", "cmd_rules")}

    def tearDown(self):
        for name, fn in self.real.items():
            setattr(cli, name, fn)

    def _interrupt(self, name):
        def boom(*args, **kwargs):
            raise KeyboardInterrupt
        setattr(cli, name, boom)

    def _code_for(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main(args)
            except SystemExit as exc:
                return exc.code, out.getvalue() + err.getvalue()
        return 0, out.getvalue() + err.getvalue()

    def test_it_does_not_report_a_clean_review(self):
        # The one that matters in front of a commit: an abandoned review is
        # not a passed one.
        self._interrupt("cmd_review")
        code, _ = self._code_for(["review"])
        self.assertEqual(code, 130)

    def test_it_does_not_print_a_traceback(self):
        self._interrupt("cmd_review")
        _, text = self._code_for(["review"])
        self.assertNotIn("Traceback", text)

    def test_the_other_commands_answer_the_same_way(self):
        self._interrupt("cmd_rules")
        code, _ = self._code_for(["rules"])
        self.assertEqual(code, 130)

    def test_the_real_command_line_agrees(self):
        # In process is where the assertion is precise; this is here to catch a
        # guard that exists in `main` but is bypassed by the module entry point.
        env = dict(os.environ, PYTHONPATH=_ROOT)
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from agentdiff import cli;"
             "cli.cmd_review = lambda *a, **k: (_ for _ in ()).throw("
             "KeyboardInterrupt());"
             "cli.main(['review'])"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=_ROOT)
        _, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 130, err.decode("utf-8", "replace"))
        self.assertNotIn(b"Traceback", err)


if __name__ == "__main__":
    unittest.main()
