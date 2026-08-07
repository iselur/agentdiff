"""The markdown report says what changed, not what a filename told it to say.

`--report FILE` writes the document somebody keeps as evidence of a review.
Almost every string in it came out of the tree the agent changed: the paths,
and the pieces of paths that rules quote back inside a reason.  Markdown reads
some of those characters as instructions.

So a file called ``notes_*draft*.py`` is written into the report as
``notes_*draft*.py`` and rendered as *notes_draft.py* -- the path the reviewer
copies out of the evidence is not a path on disk, and the copy that is on disk
is not mentioned anywhere.  A file whose name contains ``[text](target)``
renders as a link: a filename putting a clickable destination into the record.

None of this happens in the terminal, and that is why it was there.  Both views
were written from one line of code, `terminal.quoted` made that line safe for
a screen, and only one of the two views is a screen.

The tests below are in three parts:

* what the two markdown answers do on their own;
* the location rule -- ``file:line``, or ``file`` when the finding is about the
  whole file -- which the two views used to spell separately;
* the structure: the report cannot go back to calling the terminal's answer,
  and no view can go back to building a location itself.  Those two are the
  reason this is a module rather than a fix, and a change that undoes either
  of them is the bug coming back.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentdiff.markdown import as_a_path, as_prose  # noqa: E402
from agentdiff.rules import Finding, where_to_look  # noqa: E402
from agentdiff.terminal import quoted  # noqa: E402

from tests.helpers import write_file  # noqa: E402
from tests.test_hostile import HostileRepoCase  # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLI = os.path.join(_HERE, "agentdiff", "cli.py")

# A name carrying one of everything markdown acts on.  It is a legal filename
# on any Unix box, which is the whole difficulty: nothing rejects it.
LOUD = "notes_*draft*_[go](elsewhere)_`x`.py"


def _rendered(text):
    """What a markdown reader shows for `text`, as far as escaping goes.

    A backslash in front of ASCII punctuation is dropped and the punctuation is
    printed.  That is the whole of the CommonMark rule and it is all these
    tests need: written out here rather than asserted against a literal,
    because a literal full of backslashes is a thing nobody can read and
    nobody will correct when it is wrong.
    """
    out = []
    skip = False
    for i, char in enumerate(text):
        if skip:
            skip = False
            continue
        if char == "\\" and i + 1 < len(text) and not text[i + 1].isalnum():
            skip = True
            out.append(text[i + 1])
        else:
            out.append(char)
    return "".join(out)


def _inside_the_fence(row):
    """The code span at the front of a report row, with its fence removed.

    Matched by counting backticks rather than by a lazy `.+?`: a name that
    itself holds a backtick closes such a pattern early, and the test then
    asserts about half a path.
    """
    match = re.match(r"^- (`+)(.+?)\1(?: |$)", row)
    if not match:
        return None
    return match.group(2).strip()


def _cli_tree():
    with open(_CLI, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("{} has no function named {}".format(_CLI, name))


def _calls_to(node, names):
    """The names in `names` called anywhere inside `node`."""
    found = []
    for child in ast.walk(node):
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                and child.func.id in names):
            found.append(child.func.id)
    return found


class TestAPathInAReport(unittest.TestCase):
    """`as_a_path` -- a name the reader has to go and find."""

    def test_a_loud_name_comes_out_exactly_as_it_is(self):
        # The whole point.  Inside a code span markdown reads nothing as
        # syntax, so the name renders as the name and can be copied.  LOUD
        # holds a backtick as well, so the fence around it is two.
        self.assertEqual(as_a_path(LOUD), "``" + LOUD + "``")
        self.assertEqual(as_a_path("notes_*draft*.py"), "`notes_*draft*.py`")

    def test_an_ordinary_name_is_not_dressed_up(self):
        self.assertEqual(as_a_path("src/app.py"), "`src/app.py`")

    def test_a_name_holding_a_backtick_gets_a_longer_fence(self):
        # One backtick inside a one-backtick span closes it early: the rest of
        # the path spills into the sentence and the fence count goes odd for
        # everything after it on the line.
        self.assertEqual(as_a_path("a`b.py"), "``a`b.py``")

    def test_a_run_of_backticks_gets_a_fence_longer_than_the_run(self):
        self.assertEqual(as_a_path("a```b.py"), "````a```b.py````")

    def test_a_name_that_begins_or_ends_with_one_is_padded(self):
        # Markdown drops one leading and one trailing space from a code span,
        # so the padding is invisible when rendered -- and without it the
        # reader is shown one backtick fewer than the file has.
        self.assertEqual(as_a_path("`a.py"), "`` `a.py ``")
        self.assertEqual(as_a_path("a.py`"), "`` a.py` ``")

    def test_a_control_character_never_reaches_the_document(self):
        # A newline in a path ends the list item.  The rest of the name becomes
        # a paragraph of the report, indistinguishable from something the tool
        # wrote itself.  `quoted` is the screen's answer to this, and it is the
        # right answer here too, so this asks for it rather than repeating it.
        shown = as_a_path("a\nb.py")
        self.assertNotIn("\n", shown)
        self.assertIn("\\n", shown)

    def test_it_is_one_line_for_every_control_character_there_is(self):
        for bad in ("\n", "\r", "\x1b[31m", "\t", "\x00", " "):
            with self.subTest(repr(bad)):
                self.assertEqual(len(as_a_path("a" + bad + "b.py").splitlines()),
                                 1)


class TestAReasonInAReport(unittest.TestCase):
    """`as_prose` -- a sentence the reader has to read."""

    def test_an_ordinary_sentence_is_left_alone(self):
        # Escaping everything would be safe and unreadable.  A reason is prose
        # and has to still look like prose.
        self.assertEqual(as_prose("lock file modified: uv.lock"),
                         "lock file modified: uv.lock")

    def test_the_characters_markdown_acts_on_are_defused(self):
        for char in "`*_[]<>&~|":
            with self.subTest(char):
                self.assertEqual(as_prose("a" + char + "b"),
                                 "a\\" + char + "b")

    def test_what_it_renders_as_is_what_the_screen_would_have_shown(self):
        # The property, rather than a literal: escaping is supposed to be
        # invisible, so a reader of the document and a reader of the terminal
        # see the same sentence.  A backslash is the case that says it -- it
        # goes in front of every other character here, so escaping it after
        # them would put a backslash in front of the ones it just added, and
        # the reader would see them.
        for reason in ("a\\*b", "lock file modified: uv.lock",
                       "TODO added in " + LOUD, 'a "quoted" word',
                       "removed 5 * 3 lines"):
            with self.subTest(reason):
                self.assertEqual(_rendered(as_prose(reason)), quoted(reason))

    def test_a_reason_quoting_a_filename_cannot_end_the_line(self):
        # Rules put pieces of a path inside a reason -- "lock file modified:
        # <basename>" -- so the reason is as attacker-chosen as the path.
        loud = as_prose("TODO added in " + LOUD)
        self.assertNotIn("\n", loud)
        for char in "*[]`":
            self.assertNotIn(char, loud.replace("\\" + char, ""))

    def test_a_sentence_is_not_put_in_a_box(self):
        # A code span would be safe and wrong: a reason wraps, a code span
        # does not, and a long reason in one runs off the side of the page.
        self.assertNotIn("`", as_prose("something happened"))


class TestWhereAFindingPoints(unittest.TestCase):
    """The location rule, which both views used to spell for themselves."""

    def test_a_line_number_is_named_the_way_an_editor_takes_it(self):
        f = Finding("LOW", "src/app.py", 12, "TODO added", "test-quality")
        self.assertEqual(where_to_look(f), "src/app.py:12")

    def test_line_zero_means_the_file_rather_than_the_top_of_it(self):
        # Half the rules write 0 for "this is about the file": a lock file
        # changed, a file was deleted, an executable bit appeared.  `app.py:0`
        # would send an editor to line 1 as if that were where to look.
        f = Finding("MED", "app.py", 0, "file deleted", "deletion")
        self.assertEqual(where_to_look(f), "app.py")

    def test_the_two_views_send_the_reader_to_the_same_place(self):
        # The failure the shared rule exists to stop is the two copies
        # drifting, and drift shows up as the report and the screen naming
        # different places for one finding.
        from agentdiff.cli import _fmt_finding
        for line in (0, 7):
            f = Finding("HIGH", "a/b.py", line, "why", "rule")
            with self.subTest(line=line):
                self.assertIn(where_to_look(f), _fmt_finding(f))


class TestTheReportOfARepoWithALoudName(HostileRepoCase):
    """End to end: the name on disk is the name in the document."""

    def _report(self, *argv):
        target = os.path.join(self.repo, "report.md")
        code, _out, err = self.run_cli("review", "--report", target, *argv)
        self.assertNoCrash(code, err)
        with open(target, encoding="utf-8") as fh:
            return fh.read()

    def test_the_path_in_the_document_is_the_path_on_disk(self):
        write_file(self.repo, LOUD, "x = 1\n# TODO: later\n")
        report = self._report("--strict")
        rows = [ln for ln in report.splitlines()
                if ln.startswith("- ") and LOUD in ln]
        self.assertEqual(len(rows), 1, report)
        # Not just "the name appears somewhere in the file": it has to be
        # inside the code span, which is the only place markdown leaves it
        # alone, and it has to carry the line number with it.
        self.assertEqual(_inside_the_fence(rows[0]), LOUD + ":2", rows[0])

    def test_nothing_in_the_row_is_read_as_markdown(self):
        write_file(self.repo, LOUD, "x = 1\n# TODO: later\n")
        report = self._report("--strict")
        row = [ln for ln in report.splitlines() if LOUD in ln][0]
        # Everything outside the code span is the list marker, the dash the
        # tool wrote, and the reason -- and the reason is escaped.  So no live
        # markdown character is left anywhere outside the fence.
        outside = re.sub(r"^- (`+).+?\1", "", row)
        for char in "*[]<>`":
            self.assertNotIn(char, outside.replace("\\" + char, ""),
                             "{!r} is live in {!r}".format(char, row))

    def test_a_reason_that_repeats_the_name_is_defused_too(self):
        # A reason is not always fixed English.  `rule_ci_release` writes
        # "CI/release file modified: <path>", so the name an agent chose is
        # inside the sentence as well as at the front of the row -- and the
        # sentence is prose, so it is escaped rather than fenced.  Without
        # this the second half of the row was still live markdown while the
        # first half was safe, which is the harder half of the bug to see.
        where = ".github/workflows/" + LOUD
        write_file(self.repo, where, "x = 1\n")
        report = self._report("--strict")
        row = [ln for ln in report.splitlines()
               if ln.startswith("- ") and "CI/release" in ln][0]
        said = row.split(" — ", 1)[1]
        # Two things, and the first alone is not enough: text nobody escaped
        # also renders back to itself, so "it reads correctly" cannot tell a
        # defused sentence from a live one.  What separates them is whether a
        # character markdown acts on is left standing.
        self.assertEqual(_rendered(said),
                         "CI/release file modified: " + where, row)
        for char in "*[]<>`":
            self.assertNotIn(char, said.replace("\\" + char, ""),
                             "{!r} is live in the reason {!r}".format(char, said))

    def test_a_file_that_could_not_be_read_is_named_the_same_way(self):
        # The second list in the report, written by different code, from the
        # same kind of string.  It was the second copy of the same mistake.
        self.skip_as_root()
        write_file(self.repo, LOUD, "x = 1\n")
        target = os.path.join(self.repo, LOUD)
        os.chmod(target, 0o000)
        try:
            report = self._report()
        finally:
            # Restored here rather than in a cleanup: cleanups run after
            # tearDown, which has already removed the repo by then.
            os.chmod(target, 0o644)
        rows = [ln for ln in report.splitlines()
                if ln.startswith("- ") and LOUD in ln]
        self.assertEqual(len(rows), 1, report)
        self.assertEqual(_inside_the_fence(rows[0]), LOUD, rows[0])


class TestItCannotComeBack(unittest.TestCase):
    """Read off the code, because both mistakes are silent when they return.

    A report that quotes for a terminal still writes a file and still exits 0.
    A view that builds its own location still prints something plausible.
    Neither has a failing case a person would notice, so the check is that the
    call is not there rather than that the output is right.
    """

    def test_the_report_never_asks_the_terminal_how_to_write_something(self):
        report = _function(_cli_tree(), "_write_report")
        self.assertEqual(
            _calls_to(report, {"quoted"}), [],
            "the markdown report is calling `quoted`, which is the screen's "
            "answer: it escapes what a terminal obeys and nothing markdown "
            "does")

    def test_the_report_asks_the_markdown_module_instead(self):
        # The other half.  A report that called neither would pass the test
        # above by printing raw paths, which is where this started.
        report = _function(_cli_tree(), "_write_report")
        self.assertTrue(_calls_to(report, {"as_a_path"}), "no path is fenced")
        self.assertTrue(_calls_to(report, {"as_prose"}), "no reason is escaped")

    def test_only_the_machine_readable_view_touches_a_line_number(self):
        # Every human view goes through `where_to_look`.  `--json` is the one
        # place a line number is a number rather than part of a sentence, and
        # the consumer there wants the field, not the formula.
        tree = _cli_tree()
        allowed = "_review_document"
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name == allowed:
                continue
            reads = [child for child in ast.walk(node)
                     if isinstance(child, ast.Attribute) and child.attr == "line"]
            self.assertEqual(
                reads, [],
                "{}() reads a finding's line number itself; the rule for "
                "turning one into a place lives in rules.where_to_look"
                .format(node.name))


if __name__ == "__main__":
    unittest.main()
