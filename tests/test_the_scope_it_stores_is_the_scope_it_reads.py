"""A scope that saves and then reviews as no scope at all.

    $ agentdiff scope '#urgent/**'
    scope saved: #urgent/**
      stored in /repo/.agentdiff/scope
    $ agentdiff review
    clean: 2 file(s) changed, nothing flagged

Both commands worked.  The file is on disk with the glob in it.  And the scope
rule never ran, because the reader drops lines beginning with `#` as comments,
so `agentdiff review` saw no scope declared — and a review with no scope
declared is green on every file in the repository, for as long as nobody
notices.

That is the same failure a newline in a glob was already guarded against: a
scope silently wider than the one asked for.  It happened anyway because the
format was written down twice, once in the reader and once as the writer's list
of refusals, and the second list was shorter than the first.

The fix is not a longer list.  `scope.write` writes what it is about to write,
reads it back through the parser `review` will use, and refuses anything that
does not come back unchanged — so the two can no longer disagree.  These tests
pin that promise:

    read(root) returns exactly what write(root, globs) was given, in order.
"""

import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdiff import scope
from tests.helpers import make_repo, write_file


class Case(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"a.py": "x = 1\n"})
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    def run_cli(self, *args):
        result = subprocess.run(
            [sys.executable, "-m", "agentdiff"] + list(args),
            cwd=self.repo, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))),
            timeout=60)
        return result.returncode, result.stdout, result.stderr

    def stored(self):
        with open(scope.path(self.repo), encoding="utf-8") as fh:
            return fh.read()


class TestTheRoundTrip(Case):
    """What went in comes back out, in order, or it was refused."""

    def test_ordinary_globs_come_back_exactly(self):
        globs = ["src/**", "tests/**", "docs/*.md", "a b/c.py", "café/**"]
        scope.write(self.repo, globs)
        self.assertEqual(scope.read(self.repo), globs)

    def test_the_order_is_the_order_they_were_given_in(self):
        # The rule prints the scope back to the person as a list, and a list
        # that reorders itself between saving and printing reads as a different
        # scope from the one they typed.
        scope.write(self.repo, ["z/**", "a/**", "m/**"])
        self.assertEqual(scope.read(self.repo), ["z/**", "a/**", "m/**"])

    def test_a_glob_that_would_not_come_back_is_refused(self):
        for glob in ("#urgent/**", " src/**", "src/** ", "", "   ",
                     "src/**\n**", "src/**\r**", "  # spaced comment"):
            with self.subTest(glob=glob):
                with self.assertRaises(scope.ScopeError):
                    scope.write(self.repo, [glob])

    def test_nothing_is_written_when_one_glob_is_refused(self):
        # All-or-nothing, because the alternative is a scope half replaced: the
        # first two globs of the new one and none of the old one, which is a
        # scope nobody asked for at all.
        scope.write(self.repo, ["src/**"])
        with self.assertRaises(scope.ScopeError):
            scope.write(self.repo, ["lib/**", "#nope"])
        self.assertEqual(scope.read(self.repo), ["src/**"])

    def test_a_byte_that_is_not_utf8_comes_back_as_the_byte_it_was(self):
        # Reading with `errors="replace"` answers U+FFFD here — a glob nobody
        # wrote, and the promise broken in the way that is hardest to notice:
        # the file is on disk, `read` returns something, and the something is
        # not what is in the file.  Keeping the byte is also what makes the
        # glob still match on the machine that produced it, where the paths
        # come out of the filesystem carrying that same byte.
        os.makedirs(os.path.dirname(scope.path(self.repo)), exist_ok=True)
        with open(scope.path(self.repo), "wb") as fh:
            fh.write(b"caf\xe9/**\n")
        got = scope.read(self.repo)
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0].encode("utf-8", "surrogateescape"),
                         b"caf\xe9/**")

    def test_a_missing_file_is_no_scope_rather_than_an_error(self):
        self.assertEqual(scope.read(self.repo), [])
        self.assertEqual(scope.read_ignore(self.repo), [])

    def test_what_write_hands_back_is_what_read_will_give_back(self):
        # The promise stated as an equality, because on one kind of machine the
        # two are not the same string.  With no locale set, argparse hands the
        # command a run of surrogates rather than the word somebody typed:
        # `café/**` arrives as `caf\udcc3\udca9/**`.  Both spellings are the
        # same bytes in the file and the same bytes on the terminal, so nothing
        # the command prints can tell them apart — but a caller comparing what
        # it saved against what it reads back would find them unequal, and this
        # is the module that promised it would not.
        typed_where_the_locale_said_nothing = "caf\udcc3\udca9/**"
        stored = scope.write(self.repo, [typed_where_the_locale_said_nothing])
        self.assertEqual(stored, scope.read(self.repo))
        self.assertEqual(stored, ["café/**"])


class TestTheCommandRefusesWhatItCannotStore(Case):

    def test_a_glob_starting_with_a_hash_is_not_saved(self):
        code, out, err = self.run_cli("scope", "#urgent/**")
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("scope saved", out, out)
        self.assertFalse(os.path.exists(scope.path(self.repo)),
                         "refused, and wrote the file anyway")

    def test_it_says_which_character_and_why(self):
        # The person has to be able to fix it without reading this source, and
        # the fix is "take the # off" — so the message names the character and
        # says what it would have cost them.
        code, out, err = self.run_cli("scope", "#urgent/**")
        self.assertIn("#", err, err)
        self.assertIn("comment", err.lower(), err)

    def test_an_empty_glob_says_it_is_empty(self):
        # `agentdiff scope "$PATTERN"` with the variable unset, which is how an
        # empty glob gets typed in real life.  "cannot be stored as written"
        # describes nothing the person can see on their screen.
        code, out, err = self.run_cli("scope", "")
        self.assertEqual(code, 2, out + err)
        self.assertIn("empty", err.lower(), err)

    def test_a_padded_glob_says_it_is_the_space(self):
        # The one character that does not show up in the terminal is the one
        # they have to remove, so the message has to name it in words.
        code, out, err = self.run_cli("scope", " src/**")
        self.assertIn("space", err.lower(), err)

    def test_a_glob_padded_with_a_space_is_not_silently_trimmed(self):
        # Saying `scope saved:  src/**` and storing `src/**` is a small lie,
        # and it is the same lie in a smaller size: the confirmation line does
        # not describe the file.
        code, out, err = self.run_cli("scope", " src/**")
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("scope saved", out, out)


class TestTheScopeThatSavedIsTheScopeThatReviews(Case):
    """End to end, which is the only place the old bug was visible."""

    def test_a_saved_scope_is_obeyed_by_the_next_review(self):
        code, out, err = self.run_cli("scope", "src/**")
        self.assertEqual(code, 0, out + err)
        write_file(self.repo, "a.py", "x = 2\n")
        code, out, err = self.run_cli("review")
        self.assertIn("outside declared scope", out.lower(), out + err)

    def test_a_scope_the_command_refused_is_not_a_review_that_says_clean(self):
        # The whole shape of the bug: `scope` reported success, `review`
        # reported clean, and between them the scope had disappeared.  Refusing
        # at the first step is what keeps the second one honest.
        code, out, err = self.run_cli("scope", "#src/**")
        self.assertEqual(code, 2, out + err)
        write_file(self.repo, "a.py", "x = 2\n")
        code, out, err = self.run_cli("review")
        self.assertNotIn("outside declared scope", out.lower(), out + err)


class TestAFileSomebodyEditedByHand(Case):
    """`ignore` is kept by hand and `scope` can be, so reading is forgiving."""

    def _write_raw(self, name, text):
        os.makedirs(os.path.dirname(scope.path(self.repo, name)), exist_ok=True)
        with open(scope.path(self.repo, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_comments_and_blank_lines_are_not_globs(self):
        self._write_raw("scope", "# what this covers\n\nsrc/**\n\n")
        self.assertEqual(scope.read(self.repo), ["src/**"])

    def test_an_indented_comment_is_still_a_comment(self):
        # It looks like a comment in every editor there is.  Treating it as a
        # glob makes it match nothing, which quietly narrows the scope to the
        # globs that are left.
        self._write_raw("scope", "  # indented\nsrc/**\n")
        self.assertEqual(scope.read(self.repo), ["src/**"])

    def test_ignore_is_read_by_the_same_rules(self):
        self._write_raw("ignore", "# generated\n\n  vendor/**  \n")
        self.assertEqual(scope.read_ignore(self.repo), ["vendor/**"])

    def test_a_directory_where_a_file_should_be_is_not_a_crash(self):
        os.makedirs(scope.path(self.repo, "scope"))
        self.assertEqual(scope.read(self.repo), [])

    def test_a_pipe_where_a_file_should_be_does_not_hang_the_review(self):
        # Only a regular file is opened.  A directory raises, and the same
        # `except OSError` that answers a missing file answers that too, which
        # makes the check that it *is* a file look like a line worth deleting —
        # until the thing in the way is a named pipe.  `open` on one waits for
        # a writer that is never coming, and a review that hangs forever is
        # worse than one that crashes: nothing is printed, and there is nothing
        # to read afterwards to find out why.  This runs the command in a
        # subprocess because a hang in this process cannot be interrupted; the
        # assertion that matters is that it returns at all.
        if not hasattr(os, "mkfifo"):
            self.skipTest("no named pipes on this platform")
        os.makedirs(os.path.dirname(scope.path(self.repo)), exist_ok=True)
        os.mkfifo(scope.path(self.repo, "ignore"))
        code, out, err = self.run_cli("review")
        self.assertNotIn("Traceback", err, err)


class TestAMachineWithNoLocale(Case):
    """A container without `ENV LANG` writes ASCII unless told otherwise."""

    def _ascii_env(self):
        env = dict(os.environ,
                   PYTHONPATH=os.path.dirname(
                       os.path.dirname(os.path.abspath(__file__))),
                   LC_ALL="C", LANG="C", LANGUAGE="C",
                   PYTHONCOERCECLOCALE="0", PYTHONUTF8="0")
        env.pop("PYTHONIOENCODING", None)
        return env

    def test_a_glob_naming_a_non_ascii_directory_saves_and_reads_back(self):
        # The file is read as UTF-8 whatever the locale says, so it has to be
        # written as UTF-8 too.  Left to the locale this raised
        # UnicodeEncodeError from inside a command that had already checked its
        # arguments and printed nothing.
        result = subprocess.run(
            [sys.executable, "-m", "agentdiff", "scope", "café/**"],
            cwd=self.repo, capture_output=True, env=self._ascii_env(),
            timeout=60)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)
        self.assertNotIn(b"Traceback", result.stderr, result.stderr)
        self.assertEqual(scope.read(self.repo), ["café/**"])

    def test_the_confirmation_is_readable_on_a_machine_with_no_locale(self):
        # The confirmation line is the only thing the person sees, and on this
        # machine stdout is an ASCII stream.  What keeps `café/**` off it as
        # `caf??/**`, or as a UnicodeEncodeError raised after the file was
        # already written, is `shell`'s reconfiguration of the output streams —
        # so this pins that the two modules still agree, not which string
        # `cmd_scope` chose to print.  Those are the same bytes here; see the
        # note in `cli.py` for why it prints the stored one anyway.
        result = subprocess.run(
            [sys.executable, "-m", "agentdiff", "scope", "café/**"],
            cwd=self.repo, capture_output=True, env=self._ascii_env(),
            timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("café/**", result.stdout.decode("utf-8"),
                      result.stdout)


if __name__ == "__main__":
    unittest.main()
