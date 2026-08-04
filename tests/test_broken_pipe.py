"""`agentdiff review | head` is a normal thing to do, and it used to be a crash.

A review of a large change is long, so it gets piped: `| head -30` to skim the
top findings, `| less` and quit with `q`, `| grep -q HIGH` that stops as soon
as it has an answer.  All three close the read end while we are still writing.
The next write fails with EPIPE, Python raises `BrokenPipeError`, and unhandled
the interpreter prints

    Exception ignored in: <_io.TextIOWrapper name='<stdout>' ...>
    BrokenPipeError: [Errno 32] Broken pipe

over the output and exits 120 — or, when the error escapes `main()` rather than
the shutdown flush, a full traceback and exit **1**, which is this tool's code
for *the gate triggered*.  That is the worst possible spelling of it: `agentdiff
review | head` on a clean tree came back looking exactly like a review that had
found something.

141 is 128 + SIGPIPE, the shell's own spelling of "the reader hung up", the
same way 130 spells ctrl-c.  A review that got cut off found nothing and
cleared nothing, so it must answer neither 0 nor 1.

The read end is closed before the command writes a byte, so none of this
depends on how much output there is or on the size of the pipe buffer.
"""

import os
import shutil
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tests.helpers import make_repo, write_file  # noqa: E402


def _env():
    return dict(os.environ, PYTHONPATH=_ROOT)


def run_with_no_reader(args):
    """Run the CLI with a stdout pipe whose read end is already closed."""
    read_fd, write_fd = os.pipe()
    os.close(read_fd)                       # the reader went away
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentdiff"] + list(args),
        stdout=write_fd, stderr=subprocess.PIPE, cwd=_ROOT, env=_env())
    os.close(write_fd)
    _, err = proc.communicate(timeout=180)
    return proc.returncode, err.decode("utf-8", "replace")


def run_normally(args):
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentdiff"] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=_ROOT, env=_env())
    out, err = proc.communicate(timeout=180)
    return (proc.returncode,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


class TestTheReaderHungUp(unittest.TestCase):

    def setUp(self):
        base = {"src/mod{}.py".format(i): "VALUE = {}\n".format(i) * 30
                for i in range(60)}
        self.repo = make_repo(base)
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        for i in range(0, 60, 2):
            write_file(self.repo, "src/mod{}.py".format(i),
                       "VALUE = {}\n".format(i + 1000) * 30)
        # One finding, so `review` has a verdict to be confused with 141.
        write_file(self.repo, "src/creds.py",
                   'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')

    def commands(self):
        p = ["--project", self.repo]
        return [
            p + ["review"],
            p + ["review", "--json"],
            ["rules"],
            ["--version"],
            ["--help"],
        ]

    def test_nothing_is_printed_about_a_broken_pipe(self):
        for args in self.commands():
            with self.subTest(args=args[-2:]):
                _, err = run_with_no_reader(args)
                self.assertNotIn("BrokenPipeError", err, err)
                self.assertNotIn("Exception ignored", err, err)

    def test_it_is_not_a_traceback(self):
        for args in self.commands():
            with self.subTest(args=args[-2:]):
                _, err = run_with_no_reader(args)
                self.assertNotIn("Traceback", err, err)

    def test_a_cut_off_review_is_not_a_verdict(self):
        # 1 means the gate triggered.  A review nobody read triggered nothing.
        for args in self.commands():
            with self.subTest(args=args[-2:]):
                code, err = run_with_no_reader(args)
                self.assertEqual(code, 141,
                                 "{} -> {}\n{}".format(args[-2:], code, err))
                self.assertNotIn(code, (0, 1))

    def test_help_and_version_are_covered_too(self):
        # argparse prints these and exits before any command body runs.
        for args in (["--version"], ["--help"]):
            with self.subTest(args=args):
                code, err = run_with_no_reader(args)
                self.assertEqual(code, 141, err)
                self.assertEqual(err, "", err)

    def test_the_gate_still_closes_when_anyone_is_reading(self):
        # The regression guard: the real verdict must survive all of the above.
        code, out, err = run_normally(["--project", self.repo, "review"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("creds.py", out, out)

    def test_the_rules_still_print(self):
        code, out, err = run_normally(["rules"])
        self.assertEqual(code, 0, err)
        self.assertTrue(out.strip(), "no rules printed")


if __name__ == "__main__":
    unittest.main()
