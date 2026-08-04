"""The review the README quotes, produced by the code that prints it.

The README shows a full run and the same run again as `--json`:

    HIGH (2)
      HIGH   Dockerfile  CI/release file modified: Dockerfile
      HIGH   requirements.txt:1  dependency added/changed: requests

    5 finding(s): 2 HIGH, 2 MED, 1 LOW — review before merge

All of it is generated — the severity column is padded so the paths line up,
`file:line` drops the line when there isn't one, the sections are counted, the
summary counts them again and picks its closing clause from whether anything
gates.  None of it was checked.  This is the output a pre-commit hook prints
to somebody at the moment they are trying to commit, and the README is where
they learn to read it.

The findings are taken from the README's own `--json` block, which carries all
five fields, and handed to the printer; its whole output is then compared to
the README's human block, character for character.  Nothing about the layout
comes from this file — not the padding, not the separators, not the wording —
so a change to any of them fails here.

The `--json` block is checked the other way round: the findings in it are the
input, so those match trivially and prove nothing, but every other field is
computed by the code from the findings and the changes — how many files were
looked at, whether the run was clean, whether the gate tripped, the counts per
severity.  Those are what that assertion is about.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentdiff.cli import _print_review, _print_review_json
from agentdiff.rules import Finding
from tests.helpers import make_change

README = os.path.join(_ROOT, "README.md")

# "  HIGH   requirements.txt:1  dependency added/changed: requests"
_FINDING_LINE = re.compile(r"^  (HIGH|MED|LOW) +(\S+?)(?::(\d+))?  (.*)$")


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def blocks(text):
    return re.findall(r"```[a-z]*\n(.*?)```", text, re.S)


def human_block(text):
    """The block that opens with a real `agentdiff review` invocation."""
    for block in blocks(text):
        lines = block.splitlines()
        if lines and lines[0].startswith("$ agentdiff review"):
            return "\n".join(lines[1:]).strip("\n") + "\n"
    return None


def json_block(text):
    for block in blocks(text):
        if block.lstrip().startswith("{") and '"findings"' in block:
            return json.loads(block)
    return None


def unread_block(text):
    """The block quoting what a run says when a file could not be read."""
    for block in blocks(text):
        if "could not be read" in block and not block.startswith("$"):
            return block.strip("\n") + "\n"
    return None


def unread_json_block(text):
    """The `--json` fields the README documents for that same run."""
    for block in blocks(text):
        if block.lstrip().startswith("{") and '"unread": [{' in block:
            return json.loads(block)
    return None


class TestTheREADMEShowsWhatItPrints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = readme()
        cls.human = human_block(cls.text)
        cls.data = json_block(cls.text)

    def findings(self):
        return [Finding(f["severity"], f["file"], f["line"], f["reason"], f["rule"])
                for f in self.data["findings"]]

    def changes(self):
        paths = []
        for f in self.data["findings"]:
            if f["file"] not in paths:
                paths.append(f["file"])
        return [make_change("M", p, "") for p in paths]

    def test_the_readme_still_quotes_a_review(self):
        # Without this the assertions below compare None to None, or run over
        # an empty finding list, which is what deleting the examples looks like.
        self.assertIsNotNone(self.human, "no quoted review left in README.md")
        self.assertIsNotNone(self.data, "no --json example left in README.md")
        self.assertGreaterEqual(len(self.data["findings"]), 5,
                                "the README's example lost its findings")

    def test_the_quoted_review_is_the_one_the_printer_produces(self):
        buf = io.StringIO()
        _print_review(self.findings(), self.changes(), strict=False, out=buf)
        self.assertEqual(buf.getvalue().strip("\n") + "\n", self.human,
                         "README.md shows a review agentdiff no longer prints")

    def test_the_two_blocks_describe_the_same_run(self):
        # The human block is the one a person reads and the JSON block is the
        # one CI reads; they are two renderings of one run, and a README where
        # they disagree has taught somebody the wrong thing about their gate.
        shown = []
        for line in self.human.splitlines():
            found = _FINDING_LINE.match(line)
            if found:
                sev, path, lineno, reason = found.groups()
                shown.append((sev, path, int(lineno or 0), reason))
        self.assertEqual(
            shown,
            [(f["severity"], f["file"], f["line"], f["reason"])
             for f in self.data["findings"]],
            "README.md's quoted review and its --json block disagree")

    def test_the_json_example_is_what_the_code_computes(self):
        buf = io.StringIO()
        stdout, sys.stdout = sys.stdout, buf
        try:
            _print_review_json(self.findings(), self.changes(), strict=False)
        finally:
            sys.stdout = stdout
        produced = json.loads(buf.getvalue())
        # The findings went in, so they come back; the fields around them —
        # what was reviewed, whether it was clean, whether the gate tripped,
        # the per-severity counts — are the code's own arithmetic.
        self.assertEqual(produced, self.data,
                         "README.md shows --json output agentdiff no longer emits")

    def unread_run(self):
        """The two-file run the README quotes: one read, one that could not be."""
        bad = make_change("M", "bad.py", "")
        bad.unread = "[Errno 13] Permission denied: '/repo/bad.py'"
        return [make_change("M", "src/ok.py", ""), bad]

    def test_the_unread_notice_is_the_one_the_code_prints(self):
        quoted = unread_block(self.text)
        self.assertIsNotNone(quoted, "no unread-file example left in README.md")
        buf = io.StringIO()
        _print_review([], self.unread_run(), strict=False, out=buf)
        self.assertEqual(buf.getvalue().strip("\n") + "\n", quoted,
                         "README.md shows an unread-file notice agentdiff no "
                         "longer prints")

    def test_the_unread_json_fields_are_the_ones_the_code_emits(self):
        # The main --json example has an empty `unread`, so the shape of an
        # entry in it — the one field a CI script reads when the gate trips on
        # a file nobody could look at — went undocumented and unchecked.  The
        # README quotes the fields that matter for that run; this compares each
        # of them, so the block is a subset and not a second full payload.
        shown = unread_json_block(self.text)
        self.assertIsNotNone(shown, "no unread --json example left in README.md")
        buf = io.StringIO()
        stdout, sys.stdout = sys.stdout, buf
        try:
            _print_review_json([], self.unread_run(), strict=False)
        finally:
            sys.stdout = stdout
        produced = json.loads(buf.getvalue())
        for key, value in shown.items():
            self.assertIn(key, produced,
                          "README.md documents a --json field agentdiff no "
                          "longer emits: " + key)
            self.assertEqual(produced[key], value,
                             "README.md shows a different --json " + key)


if __name__ == "__main__":
    unittest.main()
