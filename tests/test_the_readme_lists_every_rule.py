"""The rules table in the README, against the rules that actually run.

The README publishes a table — severity, rule name, what it catches — and it is
the only place a person decides whether agentdiff covers what they care about
before they install it.  Nothing tied it to the code.  A rule added to
`RULE_DOCS` and not to the table is a rule nobody discovers; a row left in the
table after the rule went away is a promise the tool no longer keeps.

There is a third list, and it is the one that matters: the rule names the
review code actually puts in its findings.  `RULE_DOCS` is prose, written by
hand, and it can drift from the emitting code just as easily as the README can
drift from it.  So the names are read straight out of the source with `ast` —
every `Finding(...)` built in rules.py — and compared both ways.  A rule that
fires under a name nothing documents fails here, and so does a documented rule
that nothing fires.

Only the severity and the name are compared.  The third column is a summary of
the docstring, not a copy of it, and holding those to each other would mean
either duplicating the prose or comparing nothing.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentdiff.rules import RULE_DOCS

README = os.path.join(_ROOT, "README.md")
RULES_SOURCE = os.path.join(_ROOT, "agentdiff", "rules.py")

# "| HIGH | secrets | PEM private key blocks, ... |"
_ROW = re.compile(r"^\|\s*(HIGH|MED|LOW)\s*\|\s*([a-z][a-z0-9-]*)\s*\|(.*)\|\s*$")


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def readme_rows(text):
    """The rules table, as (severity, name) in the order it is published."""
    return [(found.group(1), found.group(2))
            for found in (_ROW.match(line) for line in text.splitlines())
            if found]


def emitted_rule_names():
    """Every name rules.py hands to a Finding, read out of the source."""
    with open(RULES_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "Finding":
            continue
        # Finding(severity, file, line, reason, rule)
        if len(node.args) >= 5 and isinstance(node.args[4], ast.Constant):
            names.add(node.args[4].value)
        for keyword in node.keywords:
            if keyword.arg == "rule" and isinstance(keyword.value, ast.Constant):
                names.add(keyword.value.value)
    return names


class TestTheREADMEListsEveryRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = readme_rows(readme())

    def test_the_readme_still_publishes_a_rules_table(self):
        # Without this, deleting the table passes every comparison below by
        # making both sides empty.
        self.assertGreaterEqual(len(self.rows), 5,
                                "no rules table left in README.md")

    def test_the_table_is_the_rules_the_tool_documents(self):
        self.assertEqual(
            self.rows,
            [(sev, name) for sev, name, _doc in RULE_DOCS],
            "README.md's rules table and RULE_DOCS disagree — same rules, same "
            "severities, same order")

    def test_every_rule_that_fires_is_a_rule_that_is_documented(self):
        # RULE_DOCS is hand-written prose and can drift from the code it
        # describes; these are the names the review actually stamps on a
        # finding.
        documented = {name for _sev, name, _doc in RULE_DOCS}
        emitted = emitted_rule_names()
        self.assertGreaterEqual(len(emitted), 5,
                                "found no Finding(...) rule names in rules.py — "
                                "this test has stopped reading the source")
        self.assertEqual(sorted(emitted - documented), [],
                         "rules.py reports findings under a rule name nothing "
                         "documents")
        self.assertEqual(sorted(documented - emitted), [],
                         "RULE_DOCS documents a rule that rules.py never "
                         "reports")


if __name__ == "__main__":
    unittest.main()
