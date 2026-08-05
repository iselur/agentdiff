"""What agentdiff does with a repo that is not the one in the README.

agentdiff reads a working tree and reports on it.  Two things can go wrong
that matter more than a crash: it can report on a path that is not the path
git meant, and it can persist a scope that is wider than the one asked for.
Both make a review that passes mean nothing.

Exit codes are the contract: 0 clean, 1 findings, 2 usage or environment error.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentdiff.cli import main
from agentdiff import git as _git

from tests.helpers import make_repo, write_file


class HostileRepoCase(unittest.TestCase):
    files = {"app.py": "print('hello')\n"}

    def setUp(self) -> None:
        self.repo = make_repo(self.files)
        self.origin = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self.origin)
        for dirpath, dirnames, _ in os.walk(self.repo):
            for d in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, d), 0o700)
                except OSError:
                    pass
        shutil.rmtree(self.repo, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def assertNoCrash(self, code, err):
        self.assertIn(code, (0, 1, 2), "exit {}: {}".format(code, err))
        self.assertNotIn("Traceback", err)

    def skip_as_root(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores the permission bits this test relies on")


class TestReportPath(HostileRepoCase):
    """--report is a path somebody typed."""

    def test_report_into_a_missing_directory_is_an_error_not_a_crash(self):
        write_file(self.repo, "app.py", "print('changed')\n")
        target = os.path.join(self.repo, "no", "such", "dir", "report.md")
        code, _, err = self.run_cli("review", "--report", target)
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2)
        self.assertIn("report", err.lower())

    def test_report_onto_a_directory_is_an_error_not_a_crash(self):
        write_file(self.repo, "app.py", "print('changed')\n")
        target = os.path.join(self.repo, "adir")
        os.makedirs(target)
        code, _, err = self.run_cli("review", "--report", target)
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2)

    def test_a_writable_report_still_works(self):
        write_file(self.repo, "app.py", "print('changed')\n")
        target = os.path.join(self.repo, "report.md")
        code, _, err = self.run_cli("review", "--report", target)
        self.assertNoCrash(code, err)
        self.assertTrue(os.path.isfile(target))


class TestOddFileTypes(HostileRepoCase):
    """An untracked FIFO is still an untracked file as far as git is concerned."""

    def test_an_untracked_fifo_does_not_hang_review(self):
        try:
            os.mkfifo(os.path.join(self.repo, "pipe"))
        except (AttributeError, OSError) as exc:
            self.skipTest("no FIFO support here: {}".format(exc))
        code, _, err = self.run_cli("review")
        self.assertNoCrash(code, err)

    def test_git_calls_carry_a_timeout(self):
        import inspect
        params = inspect.signature(_git._git).parameters
        self.assertIn("timeout", params,
                      "a hanging git must not become a hanging agentdiff")

    def test_a_file_that_vanishes_mid_review_is_not_fatal(self):
        write_file(self.repo, "gone.py", "x = 1\n")
        real_open = open

        def vanishing(path, *a, **kw):
            if isinstance(path, str) and path.endswith("gone.py") and os.path.exists(path):
                os.unlink(path)
            return real_open(path, *a, **kw)

        import builtins
        builtins.open = vanishing
        try:
            code, _, err = self.run_cli("review")
        finally:
            builtins.open = real_open
        self.assertNoCrash(code, err)


class TestScopeIsPersisted(HostileRepoCase):
    """`scope` writes a file that later runs obey.  It must say what was meant."""

    def scope_lines(self):
        path = os.path.join(self.repo, ".agentdiff", "scope")
        with open(path, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if line.strip()]

    def test_a_newline_in_a_glob_does_not_become_two_globs(self):
        code, _, err = self.run_cli("scope", "src/**\n**")
        self.assertNoCrash(code, err)
        if code == 0:
            self.assertEqual(len(self.scope_lines()), 1,
                             "one glob was given; one glob must be stored")

    def test_an_empty_glob_is_a_usage_error(self):
        code, out, err = self.run_cli("scope", "")
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2, "an empty scope is not a scope: {}".format(out))
        self.assertNotIn("scope saved", out)

    def test_a_blank_glob_among_real_ones_is_a_usage_error(self):
        code, out, err = self.run_cli("scope", "src/**", "   ")
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2)

    def test_an_unwritable_agentdiff_dir_is_an_error_not_a_crash(self):
        self.skip_as_root()
        d = os.path.join(self.repo, ".agentdiff")
        os.makedirs(d)
        os.chmod(d, 0o500)
        try:
            code, _, err = self.run_cli("scope", "src/**")
            self.assertNoCrash(code, err)
            self.assertEqual(code, 2)
        finally:
            os.chmod(d, 0o700)

    def test_a_scope_that_is_a_directory_is_an_error_not_a_crash(self):
        os.makedirs(os.path.join(self.repo, ".agentdiff", "scope"))
        code, _, err = self.run_cli("scope", "src/**")
        self.assertNoCrash(code, err)
        self.assertEqual(code, 2)

    def test_a_normal_scope_still_saves(self):
        code, out, err = self.run_cli("scope", "src/**", "tests/**")
        self.assertNoCrash(code, err)
        self.assertEqual(code, 0)
        self.assertEqual(self.scope_lines(), ["src/**", "tests/**"])


class TestUnreadableConfig(HostileRepoCase):
    """.agentdiff/scope is a file on disk, and files on disk go wrong."""

    def _config(self, name, data: bytes) -> str:
        d = os.path.join(self.repo, ".agentdiff")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_an_unreadable_scope_file_is_not_fatal(self):
        self.skip_as_root()
        path = self._config("scope", b"src/**\n")
        os.chmod(path, 0o000)
        code, _, err = self.run_cli("review")
        self.assertNoCrash(code, err)

    def test_a_scope_file_that_is_not_utf8_is_not_fatal(self):
        self._config("scope", b"src/\xff\xfe**\n")
        code, _, err = self.run_cli("review")
        self.assertNoCrash(code, err)

    def test_an_ignore_file_that_is_a_directory_is_not_fatal(self):
        os.makedirs(os.path.join(self.repo, ".agentdiff", "ignore"))
        code, _, err = self.run_cli("review")
        self.assertNoCrash(code, err)


class TestPathsAreRealPaths(HostileRepoCase):
    """git quotes unusual filenames.  A quoted name is not a path."""

    def test_a_filename_with_a_space_is_reported_unquoted(self):
        write_file(self.repo, "my file.py", "import os\nos.system('x')\n")
        changes = _git.get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn("my file.py", paths, "got: {}".format(paths))

    def test_a_filename_with_a_non_ascii_character_is_reported_unquoted(self):
        write_file(self.repo, "café.py", "x = 1\n")
        changes = _git.get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn("café.py", paths, "got: {}".format(paths))

    def test_a_filename_with_a_leading_space_keeps_it(self):
        try:
            write_file(self.repo, " lead.py", "x = 1\n")
        except OSError as exc:
            self.skipTest("filesystem refuses that name: {}".format(exc))
        changes = _git.get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn(" lead.py", paths, "got: {}".format(paths))

    def test_the_diff_of_an_oddly_named_file_is_actually_read(self):
        write_file(self.repo, "my file.py", "x = 1\n")
        changes = {c.path: c for c in _git.get_changes(self.repo)}
        self.assertIn("my file.py", changes)
        self.assertIn("x = 1", changes["my file.py"].diff_text)


class TestRepoWithNoCommits(unittest.TestCase):
    """A repo before its first commit is a normal thing to point this at."""

    def setUp(self) -> None:
        self.repo = make_repo(None)
        self.origin = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self.origin)
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_an_unknown_since_ref_is_still_an_error(self):
        write_file(self.repo, "new.py", "x = 1\n")
        with self.assertRaises(_git.GitError):
            _git.get_changes(self.repo, since_ref="definitely-not-a-ref")

    def test_staged_only_does_not_include_untracked_files(self):
        write_file(self.repo, "untracked.py", "x = 1\n")
        changes = _git.get_changes(self.repo, staged_only=True)
        self.assertEqual([c.path for c in changes], [],
                         "--staged-only must mean staged only, first commit or not")

    def test_staged_files_are_still_found(self):
        write_file(self.repo, "staged.py", "x = 1\n")
        subprocess.run(["git", "add", "staged.py"], cwd=self.repo,
                       capture_output=True, text=True)
        changes = _git.get_changes(self.repo, staged_only=True)
        self.assertEqual([c.path for c in changes], ["staged.py"])

    def test_head_is_accepted_before_the_first_commit(self):
        write_file(self.repo, "new.py", "x = 1\n")
        changes = _git.get_changes(self.repo, since_ref="HEAD")
        self.assertEqual([c.path for c in changes], ["new.py"])


class TestOutputCannotDriveTheTerminal(HostileRepoCase):
    """A path in a review is text somebody else chose to put in the tree.

    agentdiff prints the path of every file it flags, and the whole point of
    that line is to tell you which file to go and look at.  An escape sequence
    in the path clears the screen or retitles the window as it is printed, and
    a right-to-left override makes it name a different file from the one that
    changed — which is the one failure this tool cannot afford, because a
    review is read to decide whether to merge.
    """

    # Assembled from chr() so this file stays printable.
    ESC, BEL, RLO = chr(27), chr(7), chr(0x202E)
    NASTY = (
        ESC + "[2J" + ESC + "[H",       # clear the screen
        ESC + "]0;pwned" + BEL,         # retitle the window
        ESC + "[31m",                   # colour everything after this
        RLO,                            # right-to-left override
        chr(127),                       # delete
    )

    def assertPrintable(self, out, nasty, what):
        for char in out:
            if char in "\n\t":
                continue                # the layout's own whitespace
            self.assertFalse(
                ord(char) < 32 or ord(char) == 127,
                "control character {!r} reached the terminal from {!r} via {}"
                .format(char, nasty, what))
        self.assertNotIn(self.RLO, out, what)

    def test_a_flagged_path_cannot_carry_an_escape_to_the_terminal(self):
        # The rule matches on the basename, so the payload goes in the
        # directory: an agent that can add a file can add the directory too.
        for nasty in self.NASTY:
            name = "dir" + nasty + "x"
            try:
                write_file(self.repo, name + "/Dockerfile", "FROM python:3.11\n")
            except (OSError, ValueError) as exc:
                self.skipTest("filesystem refuses this name: {}".format(exc))
            code, out, err = self.run_cli("review")
            self.assertNoCrash(code, err)
            self.assertEqual(code, 1, "the Dockerfile should still be flagged")
            self.assertPrintable(out, nasty, "review")

    def test_the_path_is_still_recognisable_once_stripped(self):
        # Stripping must not eat the path, or the line is safe and useless at
        # the same time.
        write_file(self.repo, "keep" + self.ESC + "[2Jme/Dockerfile",
                   "FROM python:3.11\n")
        code, out, err = self.run_cli("review")
        self.assertNoCrash(code, err)
        self.assertIn("me/Dockerfile", out)
        self.assertIn("keep", out)

    def test_json_keeps_the_bytes_because_it_is_not_a_terminal(self):
        # --json is consumed by another program, which wants the path that is
        # really on disk; JSON's own escaping makes it safe to print.
        write_file(self.repo, "dir" + self.ESC + "[2J/Dockerfile",
                   "FROM python:3.11\n")
        code, out, err = self.run_cli("review", "--json")
        self.assertNoCrash(code, err)
        json.loads(out)                         # still valid JSON
        self.assertIn("\\u001b", out)


if __name__ == "__main__":
    unittest.main()
