"""`agentdiff rules` fits an eighty-column window, and nothing checked it.

The page is the one place this tool prints prose rather than rows, and its
wrapping was ten hand-rolled lines under a comment naming a width the code did
not use.  Three tests ran the command — for its exit code, for the severities,
for the word `gitleaks` — and every one of them passes with each description
laid out as a single 520-character line running off the side of the terminal.

What the page actually promises is two things.  No line runs past the window.
And nothing a reader might retype — `--strict`, `package-lock.json` — is split
across two of them, which `textwrap` does by default: hyphens are break points
unless you say otherwise.

Two of the three arguments that say otherwise cannot be proved by today's text,
and the tests that would prove them are not written as if they could be.  The
longest word in any rule is shorter than the room a line has, so whether long
words are broken is not a question this text asks; and no rule holds a run of
space, so collapsing runs of space changes nothing here.  Both are asserted as
what they are — the conditions under which the arguments are unobservable — so
that adding a rule that breaks either one fails here rather than silently
making a dead argument load-bearing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentdiff.rules import RULE_DOCS
from agentdiff.terminal import display_width

#: The width the page is laid out to, spelled out here rather than imported
#: from the command: a test that reads the number out of the code under test
#: agrees with whatever number is there.
WINDOW = 80

#: The command indents a description this far under the rule's name.
INDENT = 8


class RulesPage(unittest.TestCase):
    def setUp(self) -> None:
        # Somewhere that is not a repository, because `rules` describes the
        # tool rather than any checkout and should not need one.
        self.anywhere = tempfile.mkdtemp(prefix="ad-rules-")
        self.addCleanup(__import__("shutil").rmtree, self.anywhere,
                        ignore_errors=True)
        done = subprocess.run(
            [sys.executable, "-m", "agentdiff", "rules"],
            cwd=self.anywhere, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.page = done.stdout
        self.lines = self.page.splitlines()

    def rules(self):
        """The page as (name row, description lines), one pair per rule.

        Blank lines separate the blocks, and a rule's block opens with its
        name row — indented two columns, where the page's opening sentence
        and its closing note start at nought and a description line at eight.
        A rule is found by that opening row, so a block is still found whole
        when the description under it is indented wrongly or not at all.
        """
        blocks, block = [], []
        for line in self.lines:
            if line.strip():
                block.append(line)
            elif block:
                blocks.append(block)
                block = []
        if block:
            blocks.append(block)
        named = [b for b in blocks
                 if b[0].startswith("  ") and not b[0].startswith(" " * INDENT)]
        self.assertEqual(len(named), len(RULE_DOCS),
                         "the page no longer has one block per rule:\n"
                         + self.page)
        return [(b[0], b[1:]) for b in named]


class TestItFitsTheWindow(RulesPage):
    def test_no_line_runs_past_the_window(self):
        too_wide = [ln for ln in self.lines if display_width(ln) > WINDOW]
        self.assertEqual(too_wide, [],
                         "{} line(s) run past {} columns".format(
                             len(too_wide), WINDOW))

    def test_the_page_is_wrapped_rather_than_one_line_per_rule(self):
        # The failure this replaces did not look like a crash: every rule
        # printed, on one enormous line each.
        wrapped = [ln for ln in self.lines
                   if ln.startswith(" " * INDENT) and ln.strip()]
        self.assertGreater(len(wrapped), len(RULE_DOCS),
                           "no description was wrapped at all:\n" + self.page)

    def test_a_line_is_short_only_because_the_next_word_would_not_fit(self):
        # The other half of fitting the window is using it.  Asking instead
        # for one wide line somewhere on the page is not the same question
        # and does not answer this one: laid out to a twentieth of the
        # window, every line is a stub except the one holding a
        # forty-four-character URL, which is wider than half the window on
        # its own and says the page is full when nothing else on it is.
        #
        # This is the promise itself rather than a symptom of it.  A line
        # ends early only when the first word of the next line could not
        # have been added to it -- which is what wrapping to a width means,
        # and which one long word cannot make true of every line around it.
        #
        # Lengths are counted the way the wrap counts them, in characters,
        # since that is the arithmetic being checked.
        for name_row, body in self.rules():
            for line, following in zip(body, body[1:]):
                nxt = following.split()[0]
                self.assertGreater(
                    len(line) + 1 + len(nxt), WINDOW,
                    "{}: {!r} would have fitted on the line before it, so "
                    "the page is laid out narrower than the window\n{}"
                    .format(name_row.strip(), nxt, "\n".join(body)))


class TestADescriptionSitsUnderItsName(RulesPage):
    """Every line of a description, including the first one.

    The indent is what makes the page two columns rather than one: the rule
    names down the left, the prose set in from them, so a reader skimming for
    a name is reading a column and not a paragraph.  A first line that starts
    at the margin puts a sentence where the next name should be, and the page
    still fits the window and still wraps — so nothing above notices.
    """

    def test_every_line_of_a_description_is_set_in_from_the_name(self):
        for name_row, body in self.rules():
            self.assertTrue(body, "{}: no description at all"
                            .format(name_row.strip()))
            for line in body:
                self.assertEqual(
                    len(line) - len(line.lstrip(" ")), INDENT,
                    "{}: {!r} is not set in {} columns like the rest of the "
                    "description\n{}".format(name_row.strip(), line, INDENT,
                                             "\n".join(body)))

    def test_a_name_row_is_never_mistaken_for_prose(self):
        # The fixture finds a rule by its two-column indent, so the two
        # indents have to differ for any of the above to mean anything.
        self.assertNotEqual(INDENT, 2)
        for name_row, _ in self.rules():
            self.assertEqual(len(name_row) - len(name_row.lstrip(" ")), 2,
                             repr(name_row))


class TestNothingAReaderWouldTypeIsBroken(RulesPage):
    def test_a_flag_or_a_filename_survives_the_wrap_whole(self):
        # `textwrap` breaks on hyphens unless told not to, and this text is
        # full of them: `--strict`, `package-lock.json`, `--scope`.  A reader
        # who copies one off a line that ends mid-word copies a wrong flag.
        hyphenated = sorted({word.strip(".,;:()") for _, _, doc in RULE_DOCS
                             for word in doc.split() if "-" in word})
        self.assertTrue(hyphenated, "the text stopped naming any flags")
        for word in hyphenated:
            self.assertIn(word, self.page,
                          "{!r} was broken across two lines".format(word))

    def test_no_line_ends_on_a_hyphen(self):
        ending = [ln for ln in self.lines if ln.rstrip().endswith("-")]
        self.assertEqual(ending, [],
                         "a line ends mid-word:\n" + "\n".join(ending))

    def test_every_word_of_every_rule_reaches_the_page(self):
        # Wrapping is meant to move words between lines and lose none of them.
        for _, name, doc in RULE_DOCS:
            for word in doc.split():
                self.assertIn(word, self.page,
                              "{}: {!r} did not survive".format(name, word))


class TestWhatThisTextCannotProve(unittest.TestCase):
    """The conditions that make two of the wrap's arguments unobservable.

    Neither `break_long_words=False` nor collapsing runs of space changes a
    single line of today's page.  Rather than write a test that passes either
    way and reads as if it were covering them, the conditions are asserted
    directly: if a rule is ever added that violates one, this fails here and
    says which argument just became load-bearing.
    """

    def test_no_word_is_wider_than_the_room_a_line_has(self):
        room = WINDOW - INDENT
        longest = max((word for _, _, doc in RULE_DOCS
                       for word in doc.split()), key=len)
        self.assertLessEqual(
            len(longest), room,
            "{!r} is wider than the {} columns a wrapped line has, so "
            "break_long_words is now observable and wants a test"
            .format(longest, room))

    def test_no_rule_holds_a_run_of_space(self):
        for _, name, doc in RULE_DOCS:
            self.assertNotIn("  ", doc,
                             "{}: two spaces in the text, so collapsing runs "
                             "of space is now observable and wants a test"
                             .format(name))
            self.assertNotIn("\n", doc, name)


if __name__ == "__main__":
    unittest.main()
