"""A review named a file that does not exist.

`_safe` says what a review line is for:

    Every line of a review exists to say which file to go and look at.

It then deleted the characters it would not print, which is safe and silent.
A directory whose name contains a newline — legal on every Unix, and git hands
it over raw because agentdiff reads `--name-status -z` — came out fused:

    HIGH   depsHIGH   forged.py   x/requirements.txt:1  dependency added/changed

`deps` and what followed it were two path components; on screen they are one
word. There is no `depsHIGH` on disk. So the gate says a dependency file
changed and review it before merge, and the file it names cannot be found, with
nothing on the line saying anything was dropped. Copy the path, `git show` it,
get nothing, and the reasonable conclusion is that agentdiff is confused.

Deleting was the right instinct — a raw newline there would let a filename
write its own `HIGH` row, and a bidi override would make a line name a
different file from the one that changed. But there is a form that is neither
raw nor lossy, and git already uses it: quote the path and escape what cannot
be shown. `git status` prints exactly that. So does this now, which makes the
displayed path the same string git shows and a reader can act on.

Ordinary paths, including non-ASCII ones, must come through untouched — `café/`
is a perfectly printable directory and quoting it would be noise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentdiff.terminal import quoted as _safe

from tests.helpers import make_repo


class TestOrdinaryPathsAreUntouched(unittest.TestCase):
    """The common case must not acquire quotes it does not need."""

    def test_a_plain_path(self):
        self.assertEqual(_safe("src/auth/session.py"), "src/auth/session.py")

    def test_a_path_with_spaces(self):
        self.assertEqual(_safe("my docs/a b.txt"), "my docs/a b.txt")

    def test_a_non_ascii_path(self):
        # Printable, so nothing to escape and nothing to quote.
        self.assertEqual(_safe("café/naïve.py"), "café/naïve.py")

    def test_a_japanese_path(self):
        self.assertEqual(_safe("設定/ファイル.py"), "設定/ファイル.py")


class TestAPathThatCannotBePrintedIsShownAsGitShowsIt(unittest.TestCase):

    def test_the_components_are_not_fused(self):
        got = _safe("deps\nHIGH   forged.py   x/requirements.txt")
        self.assertNotIn("depsHIGH", got,
                         "two path components silently became one word: " + got)

    def test_the_newline_is_escaped_not_dropped(self):
        got = _safe("deps\nHIGH/requirements.txt")
        self.assertIn("\\n", got, got)
        self.assertNotIn("\n", got, "still a real newline: " + repr(got))

    def test_it_is_quoted_the_way_git_quotes_it(self):
        got = _safe("deps\nHIGH/requirements.txt")
        self.assertEqual(got, '"deps\\nHIGH/requirements.txt"')

    def test_a_tab_and_a_return_too(self):
        self.assertEqual(_safe("a\tb"), '"a\\tb"')
        self.assertEqual(_safe("a\rb"), '"a\\rb"')

    def test_anything_else_becomes_a_hex_escape(self):
        self.assertEqual(_safe("a\x01b"), '"a\\x01b"')

    def test_a_real_backslash_is_escaped_once_it_is_quoted(self):
        # Otherwise a file literally named `a\nb` and one named `a<newline>b`
        # print identically, and the escaping means nothing.
        with_newline = _safe("a\nb")
        with_backslash = _safe("a\\nb")
        self.assertNotEqual(with_newline, with_backslash)
        self.assertEqual(with_backslash, '"a\\\\nb"')

    def test_a_quote_in_the_name_is_escaped(self):
        self.assertEqual(_safe('a"b\nc'), '"a\\"b\\nc"')

    def test_a_backslash_alone_is_quoted_too(self):
        # git quotes this one as well, and for the same reason: once escaping
        # exists, a name containing a backslash has to be told apart from the
        # escape it looks like.  Verified against `git status --porcelain`,
        # which prints `"a\\b"` for a file named `a\b`.
        self.assertEqual(_safe("a\\b"), '"a\\\\b"')


class TestTheTerminalIsStillSafe(unittest.TestCase):
    """The reason `_safe` exists does not change."""

    def test_an_escape_sequence_cannot_reach_the_terminal(self):
        got = _safe("a\x1b[2Jb")
        self.assertNotIn("\x1b", got, repr(got))

    def test_a_bidi_override_cannot_reach_the_terminal(self):
        got = _safe("gpif.txt")          # U+202E between `g` and `pif`
        self.assertNotIn("‮", got, repr(got))

    def test_it_still_fits_on_one_row(self):
        got = _safe("a\nb\nc\nd")
        self.assertEqual(len(got.splitlines()), 1, repr(got))


class TestEndToEnd(unittest.TestCase):
    """Through the real gate, on a real repo, with a real filename."""

    def setUp(self):
        self.repo = make_repo({"src/ok.txt": "base\n"})
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.dirname = "deps\nHIGH   forged.py   secrets exfiltrated"
        d = os.path.join(self.repo, self.dirname)
        os.makedirs(d)
        with open(os.path.join(d, "requirements.txt"), "w") as fh:
            fh.write("requests==2.0\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo,
                       capture_output=True, text=True)

    def _review(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "agentdiff", "review", *extra],
            cwd=self.repo, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))

    def test_the_finding_is_one_row(self):
        p = self._review()
        rows = [l for l in p.stdout.splitlines() if l.startswith("  HIGH")]
        self.assertEqual(len(rows), 1, p.stdout)

    def test_the_row_does_not_name_a_file_that_is_not_there(self):
        p = self._review()
        self.assertNotIn("depsHIGH", p.stdout, p.stdout)
        self.assertIn("\\n", p.stdout, p.stdout)

    def test_the_json_view_keeps_the_path_that_is_really_on_disk(self):
        # Consumed by another program, which wants to open the file.
        p = self._review("--json")
        data = json.loads(p.stdout)
        paths = [f["file"] for f in data["findings"]]
        self.assertIn(self.dirname + "/requirements.txt", paths, paths)

    def test_the_gate_still_gates(self):
        p = self._review()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
