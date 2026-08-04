"""A changed file agentdiff could not read was reviewed as if it were empty.

An untracked file with an AWS key in it, and one permission bit between the
two answers:

    $ chmod 000 bad.py && agentdiff review
    clean: 1 file(s) changed, nothing flagged
    $ echo $?
    0

    $ chmod 644 bad.py && agentdiff review
    HIGH   bad.py:1  AWS access key ID pattern added
    $ echo $?
    1

`clean` is this tool's verdict word and exit 0 is what `agentdiff review &&
git commit` acts on, so a file that could not be opened did not merely go
unreported — it was counted into `1 file(s) changed` and then cleared.  This
is the same hole as the empty diff (see `test_nothing_reviewed.py`), one file
at a time instead of all of them, and harder to notice because the count on
screen looks right.

Only untracked and newly added files reach this path.  A *tracked* file that
cannot be read fails in `git diff` first, and agentdiff already stops with
exit 2 and git's own message — loud, and not this bug.

The unreadable file is now named, subtracted from the reviewed count, and the
run exits 1: not because anything was flagged, but because something was not
looked at, and this tool's exit 0 is a claim that it was.  The README's own
reasoning about interrupted runs applies unchanged — a review that "found
nothing *and* cleared nothing" must not read as a pass.

A binary file is deliberately not one of these.  agentdiff opens it, sees the
NUL, and has a rule about binaries being added; it knows what the file is.
These are the files it does not know anything about.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# A real-looking key that the secrets rule fires on.  It is AWS's own
# documentation example, which is why it is safe to commit to a test.
KEY_LINE = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'


class Case(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ad-unread-")
        self.addCleanup(self._cleanup)
        self.git("init", "-q", ".")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("a.py", "import os\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

    def _cleanup(self):
        # A 000 file cannot be removed by rmtree until its mode is put back.
        for dirpath, _, names in os.walk(self.repo):
            for name in names:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.repo, ignore_errors=True)

    def git(self, *argv):
        return subprocess.run(["git", *argv], cwd=self.repo,
                              capture_output=True, text=True, check=False)

    def write(self, name, text, mode=0o644):
        path = os.path.join(self.repo, name)
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, mode)
        return path

    def review(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "agentdiff", "review", *argv],
            cwd=self.repo, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))


class TestAChangedFileThatCouldNotBeRead(Case):

    def setUp(self):
        super().setUp()
        self.write("bad.py", KEY_LINE, mode=0o000)

    def test_it_is_not_called_clean(self):
        p = self.review()
        self.assertNotIn("clean", p.stdout.lower(),
                         "cleared a file it never opened:\n" + p.stdout)

    def test_it_names_the_file(self):
        # The count alone is not actionable.  The fix is a chmod on one path,
        # or a line in .agentdiff/ignore, and both need the name.
        p = self.review()
        self.assertIn("bad.py", p.stdout + p.stderr, p.stdout + p.stderr)

    def test_it_says_the_file_could_not_be_read(self):
        p = self.review()
        self.assertIn("could not be read", (p.stdout + p.stderr).lower(),
                      p.stdout + p.stderr)

    def test_the_exit_code_is_not_zero(self):
        # `agentdiff review && git commit` is the whole reason the exit code
        # exists.  Nothing was flagged here, but nothing was cleared either.
        p = self.review()
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_reviewed_count_does_not_include_it(self):
        p = self.review("--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["files_changed"], 1, data)
        self.assertEqual(data["reviewed"], 0, data)

    def test_the_json_does_not_claim_clean(self):
        # The field a CI script reads to decide whether to merge.
        p = self.review("--json")
        data = json.loads(p.stdout)
        self.assertFalse(data["clean"], data)

    def test_the_json_names_it_too(self):
        p = self.review("--json")
        data = json.loads(p.stdout)
        self.assertEqual([e["file"] for e in data["unread"]], ["bad.py"], data)

    def test_a_readable_sibling_is_still_reviewed(self):
        # The unreadable file must not cost the run the findings it did get.
        self.write("also.py", KEY_LINE)
        p = self.review()
        self.assertIn("also.py", p.stdout, p.stdout)
        self.assertIn("HIGH", p.stdout, p.stdout)

    def test_the_reviewed_count_is_the_readable_ones(self):
        self.write("also.py", "x = 1\n")
        p = self.review("--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["files_changed"], 2, data)
        self.assertEqual(data["reviewed"], 1, data)

    def test_the_markdown_report_says_so(self):
        # A report is read later, by somebody who was not at the terminal.
        path = os.path.join(self.repo, "r.md")
        self.review("--report", path)
        with open(path) as fh:
            body = fh.read()
        self.assertIn("bad.py", body, body)
        self.assertIn("could not be read", body.lower(), body)

    def test_the_same_file_readable_is_flagged(self):
        # The reproduction, both halves in one test: one permission bit is the
        # only difference between HIGH and a green commit.
        locked = self.review()
        os.chmod(os.path.join(self.repo, "bad.py"), 0o644)
        opened = self.review()
        self.assertIn("HIGH", opened.stdout, opened.stdout)
        self.assertNotEqual(locked.returncode, 0,
                            "the locked run passed while the same file "
                            "readable is HIGH:\n" + locked.stdout)


class TestAChangedFileThatIsNotAFile(Case):
    """The FIFO guard, tested where it can actually be reached.

    Not through the CLI: git does not report a FIFO as a change at all — it is
    absent from `git status --porcelain`, and a tracked file replaced by one
    stops `git diff` with `unsupported file type`, which agentdiff already
    surfaces as exit 2.  So the guard in `_read_as_added` is defence in depth.
    It is still pinned here, because the way it used to fail was to return
    quietly and let the file count as reviewed.
    """

    def test_a_non_regular_file_records_why_it_was_not_read(self):
        from agentdiff.git import FileChange, _read_as_added

        path = os.path.join(self.repo, "pipe")
        os.mkfifo(path)
        fc = FileChange("U", "pipe")
        _read_as_added(fc, path)
        self.assertTrue(fc.unread,
                        "skipped a file it cannot open and said nothing")
        self.assertEqual(fc.diff_text, "")

    def test_a_missing_file_records_why_it_was_not_read(self):
        # The path git named is gone by the time we go to read it — a rebase,
        # a build script, another agent working in the same tree.
        from agentdiff.git import FileChange, _read_as_added

        fc = FileChange("U", "gone.py")
        _read_as_added(fc, os.path.join(self.repo, "gone.py"))
        self.assertTrue(fc.unread, "read nothing and reported nothing")


class TestAnOrdinaryReviewIsUnaffected(Case):

    def test_a_clean_diff_is_still_clean(self):
        self.write("b.py", "x = 1\n")
        p = self.review()
        self.assertIn("clean", p.stdout.lower(), p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_nothing_is_said_about_unread_files(self):
        self.write("b.py", "x = 1\n")
        p = self.review()
        self.assertNotIn("could not be read", (p.stdout + p.stderr).lower(),
                         p.stdout)

    def test_the_reviewed_count_equals_the_changed_count(self):
        self.write("b.py", "x = 1\n")
        self.write("c.py", "y = 2\n")
        p = self.review("--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["reviewed"], data["files_changed"], data)
        self.assertEqual(data["unread"], [], data)

    def test_a_real_finding_is_unchanged(self):
        self.write("b.py", KEY_LINE)
        p = self.review()
        self.assertIn("HIGH", p.stdout, p.stdout)
        self.assertEqual(p.returncode, 1, p.stdout)

    def test_a_binary_file_is_not_called_unread(self):
        # It opened, and agentdiff knows what it is: there is a rule about
        # binaries being added.  Not knowing what a file is, is the thing this
        # note is for.
        with open(os.path.join(self.repo, "blob.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02binary\x00")
        p = self.review("--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["unread"], [], data)


if __name__ == "__main__":
    unittest.main()
