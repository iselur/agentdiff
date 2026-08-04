"""The second mutation sweep over `agentdiff/rules.py` — what it found.

The first sweep ran over the diff reader and the gating; this one ran over the
rules themselves, and almost every survivor was a line about *which side of a
diff a line is on*. That is the one question this tool exists to answer. A
review that reports the secret you deleted, or the dependency you removed, is
not a stricter review — it is a review nobody reads twice.

What is pinned here:

  * **Removed lines are not added lines.** `_walk_diff_lines` yields removed
    lines with `is_added=False` and no line number, and the dependency parsers
    trust that. Flip the flag and `- serde = "1.0"` reads as a new dependency,
    at line -1.

  * **Line numbers count the file that came out, not the diff.** Only added and
    context lines advance the counter; removed lines do not exist in the new
    file and cannot have a position in it. Count the wrong ones and every
    finding points at a line the reader has to go and not find.

  * **A blank line in a diff is a blank line.** It reaches the same branch as a
    context line, and that branch indexes into it.

  * **The entropy threshold includes the value it names.** A 48-character token
    of 16 distinct characters has entropy of exactly 4.0 bits per character.
    `>= 4.0` and `> 4.0` differ on precisely the hand-written-looking token that
    a real key is most likely to be mistaken for.

  * **`-r base.txt` is not a package called `-r`.** requirements.txt include
    lines, and any line that names no package at all, produce no finding — and
    the same package listed twice produces one, not two.

  * **A Gemfile is read.** It is the one manifest whose parser hangs off an
    equality against a single name, which is the easiest branch in the file to
    make unreachable.

  * **"Assertions removed" means removed.** The check is a removed line, that is
    not the `---` file header, that contains an assertion. Loosen it and adding
    a test — or deleting an import from one — is reported as deleting its
    assertions, which is the accusation this rule exists to make carefully.

  * **An ignore pattern may name just the file.** `secrets.env` in
    `.agentdiff/ignore` has to match `config/secrets.env`, because that is how
    everyone writes one.

Two survivors are equivalent mutants and are left alive; the reason is at the
bottom of this file.
"""

from __future__ import annotations

import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentdiff.rules import (
    _is_ignored,
    _walk_diff_lines,
    rule_dependencies,
    rule_secrets,
    rule_test_quality,
    run_rules,
)

from tests.helpers import make_change


def diff(path, *lines, start=1):
    """A unified diff of `lines`, each already carrying its +/-/space prefix."""
    added = sum(1 for line in lines if line.startswith("+"))
    kept = sum(1 for line in lines if not line.startswith(("+", "-")))
    header = "--- a/{p}\n+++ b/{p}\n@@ -{s},{o} +{s},{n} @@\n".format(
        p=path, s=start, o=kept + sum(1 for line in lines if line.startswith("-")),
        n=kept + added)
    return header + "".join(line + "\n" for line in lines)


def reasons(findings):
    return [f.reason for f in findings]


class TestWhichSideOfTheDiffALineIsOn(unittest.TestCase):

    def test_a_dependency_that_was_removed_is_not_reported(self):
        change = make_change("M", "Cargo.toml", diff(
            "Cargo.toml", "[dependencies]", '-serde = "1.0"', '+anyhow = "1.0"'))
        self.assertEqual(reasons(rule_dependencies(change)),
                         ["dependency added/changed: anyhow"],
                         "a dependency that was deleted was reported as added")

    def test_a_removed_dependency_alone_is_only_a_file_change(self):
        # Not silence — the file did change — but not a new package either.
        change = make_change("M", "Cargo.toml", diff(
            "Cargo.toml", "[dependencies]", '-serde = "1.0"'))
        self.assertEqual(reasons(rule_dependencies(change)),
                         ["dependency file modified: Cargo.toml"])

    def test_the_line_number_counts_the_file_not_the_diff(self):
        # Two context lines above the addition, so the new file has it third.
        change = make_change("M", "requirements.txt", diff(
            "requirements.txt", " flask==2.0", " django==3.0", "+requests==2.31"))
        findings = rule_dependencies(change)
        self.assertEqual([(f.line, f.reason) for f in findings],
                         [(3, "dependency added/changed: requests")],
                         "the reported line number does not count context lines")

    def test_a_blank_line_in_a_diff_is_not_a_crash(self):
        change = make_change("M", "Cargo.toml", "\n".join([
            "--- a/Cargo.toml", "+++ b/Cargo.toml", "@@ -1,3 +1,4 @@",
            " [dependencies]", "", '+serde = "1.0"']))
        self.assertEqual(reasons(rule_dependencies(change)),
                         ["dependency added/changed: serde"])

    def test_a_bare_line_keeps_its_first_character(self):
        # The pseudo-diff form has no prefix to strip; taking one anyway turns
        # every section header into something that is not a section header.
        self.assertEqual(list(_walk_diff_lines("[dependencies]\n")),
                         [(False, 1, "[dependencies]")],
                         "a line with no diff prefix lost its first character")


class TestTheEntropyThreshold(unittest.TestCase):

    # 16 distinct characters, each three times: exactly log2(16) = 4.0 bits.
    EXACTLY_FOUR_BITS = "0123456789abcdef" * 3

    def test_a_token_exactly_at_the_threshold_is_reported(self):
        change = make_change("M", "app/config.py", diff(
            "app/config.py", '+api_key = "%s"' % self.EXACTLY_FOUR_BITS))
        self.assertEqual(reasons(rule_secrets(change)),
                         ["high-entropy token assigned to 'api_key'"],
                         "a token sitting exactly on the threshold was let through")

    def test_a_long_but_predictable_token_is_not_reported(self):
        change = make_change("M", "app/config.py", diff(
            "app/config.py", '+api_key = "%s"' % ("ab" * 30)))
        self.assertEqual(rule_secrets(change), [],
                         "a token with almost no entropy was called a secret")


class TestWhatCountsAsADependencyLine(unittest.TestCase):

    def test_an_include_line_is_not_a_dependency(self):
        change = make_change("M", "requirements.txt", diff(
            "requirements.txt", "+-r base.txt", "+requests==2.31"))
        found = reasons(rule_dependencies(change))
        self.assertEqual(found, ["dependency added/changed: requests"],
                         "an include line was parsed as a package")
        self.assertNotIn("dependency added/changed: None", found)

    def test_a_gem_is_a_dependency(self):
        change = make_change("M", "Gemfile", diff(
            "Gemfile", "+gem 'rails', '~> 7.0'"))
        self.assertEqual(reasons(rule_dependencies(change)),
                         ["dependency added/changed: rails"],
                         "a Gemfile was matched but not read")

    def test_the_same_dependency_twice_is_reported_once(self):
        change = make_change("M", "requirements.txt", diff(
            "requirements.txt", "+requests==2.31", "+urllib3==2.0",
            "+requests==2.31"))
        self.assertEqual(reasons(rule_dependencies(change)),
                         ["dependency added/changed: requests",
                          "dependency added/changed: urllib3"],
                         "a package listed twice was reported twice")


class TestWhatCountsAsAnAssertionRemoved(unittest.TestCase):

    REMOVED = "assertions removed from test file"

    def test_an_assertion_added_is_not_an_assertion_removed(self):
        change = make_change("M", "tests/test_auth.py", diff(
            "tests/test_auth.py", "+    self.assertEqual(token, expected)"))
        self.assertNotIn(self.REMOVED, reasons(rule_test_quality(change)),
                         "adding an assertion was reported as removing one")

    def test_removing_a_line_that_is_not_an_assertion_is_not_flagged(self):
        change = make_change("M", "tests/test_auth.py", diff(
            "tests/test_auth.py", "-import os", "+import pathlib"))
        self.assertNotIn(self.REMOVED, reasons(rule_test_quality(change)),
                         "an unused import removed from a test file was "
                         "reported as its assertions going")

    def test_an_assertion_actually_removed_is_still_flagged(self):
        change = make_change("M", "tests/test_auth.py", diff(
            "tests/test_auth.py", "-    self.assertEqual(token, expected)"))
        self.assertIn(self.REMOVED, reasons(rule_test_quality(change)))


class TestIgnorePatternsThatNameAFile(unittest.TestCase):

    def test_a_pattern_with_no_directory_matches_the_file_anywhere(self):
        self.assertTrue(_is_ignored("config/secrets.env", ["secrets.env"]),
                        "an ignore pattern naming just the file matched nothing")

    def test_a_pattern_with_a_directory_still_matches_the_whole_path(self):
        self.assertTrue(_is_ignored("build/out.min.js", ["build/*"]))
        self.assertFalse(_is_ignored("src/out.min.js", ["build/*"]))

    def test_that_pattern_actually_silences_the_finding(self):
        change = make_change("M", "config/secrets.env", diff(
            "config/secrets.env",
            "+api_key = \"%s\"" % ("0123456789abcdef" * 3)))
        self.assertTrue(run_rules([change]), "nothing to silence in the first place")
        self.assertEqual(run_rules([change], ignore_patterns=["secrets.env"]), [],
                         "an ignored file was still reviewed")


# Equivalent mutants, left alive on purpose:
#
#   `if not stripped or stripped.startswith("#")` in both `_cargo_dep_findings`
#   and `_pipfile_dep_findings`.  Flipped to `and`, the guard never fires — no
#   line is both empty and a comment — so every line it used to skip falls
#   through to the rest of the loop instead.  Both survive it: a blank line
#   holds no "=" and stops at the check for one, and a comment line begins with
#   "#", which neither dependency-name pattern will match.  The guard is an
#   early exit, not the thing that makes comments invisible.
#
# Leaving them means the sweep log keeps two survivors it will always keep.
# That is the honest state: they are unreachable, not untested, and the tests
# above pin what these two parsers report either way.


if __name__ == "__main__":
    unittest.main()
