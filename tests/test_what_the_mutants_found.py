"""Lines the suite ran but did not actually pin down.

A mutation sweep changes one operator or one constant at a time and reruns the
whole suite.  Every mutant that survives is a line the tests execute without
depending on: change it, and nothing complains.  These are the survivors from
the sweep over `agentdiff/rules.py`, each turned into the test that kills it.

  * `in_dep_section = False` in the Cargo.toml and Pipfile parsers.  Flip it to
    `True` and every line before the first section header in a hunk becomes a
    dependency — so a bumped `version` in `[package]` gets reported as a
    package named `version`.  Nothing failed, because no test ever handed those
    parsers a hunk that starts mid-file, which is what git actually produces.

  * `strict=False` in `gating_findings`.  Flip it and LOW findings gate by
    default, which turns a TODO comment into a failed build.  Every existing
    test passed `strict` explicitly.

  * the `+++` / `---` skip in `_walk_diff_lines`.  See the note on that test.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentdiff.rules import (
    Finding,
    _walk_diff_lines,
    gating_findings,
    rule_dependencies,
)
from tests.helpers import diff_with_added, make_change


def hunk(path_lines, start=12):
    """A hunk that begins mid-file, with no section header inside it.

    This is the normal shape.  git puts the enclosing section on the `@@` line
    as context — `@@ -12,3 +12,4 @@ [package]` — and `_walk_diff_lines` skips
    `@@` lines, so the parser never sees it.
    """
    header = f"--- a/f\n+++ b/f\n@@ -{start},0 +{start},{len(path_lines)} @@ [package]\n"
    return header + "".join(f"+{line}\n" for line in path_lines)


class TestASectionlessHunkNamesNoPackages(unittest.TestCase):
    """Outside a dependency section, a `key = value` line is not a dependency."""

    def reasons(self, path, diff_text):
        return [f.reason for f in rule_dependencies(
            make_change(path=path, diff_text=diff_text))]

    def test_a_cargo_version_bump_is_not_a_package_named_version(self):
        reasons = self.reasons("Cargo.toml", hunk(['version = "0.2.0"']))
        # The file still gates — a manifest changed and that is worth a look.
        # What it must not do is invent a dependency out of a metadata key.
        self.assertEqual(reasons, ["dependency file modified: Cargo.toml"], reasons)

    def test_a_cargo_edition_bump_is_not_a_package_named_edition(self):
        reasons = self.reasons("Cargo.toml", hunk(['edition = "2024"']))
        self.assertNotIn("dependency added/changed: edition", reasons)

    def test_a_pipfile_source_key_is_not_a_package(self):
        reasons = self.reasons("Pipfile", hunk(['name = "pypi"']))
        self.assertNotIn("dependency added/changed: name", reasons)

    def test_a_section_header_inside_the_hunk_still_opens_the_section(self):
        # The other direction: when the header *is* in the hunk, the packages
        # under it are named.  Without this the test above could be satisfied
        # by a parser that never names anything.
        reasons = self.reasons("Cargo.toml",
                               hunk(["[dependencies]", 'serde = "1.0.203"']))
        self.assertIn("dependency added/changed: serde", reasons)


class TestOnlyAddedLinesInsideTheSectionCount(unittest.TestCase):
    """Both halves of `if not is_added or not in_dep_section: continue`.

    Loosen either half and the rule starts naming packages nobody touched:
    every context line inside `[dependencies]` becomes a new dependency, so an
    unrelated one-word edit to Cargo.toml reports the whole dependency list as
    added.  A rule that cries wolf on ordinary edits is a rule people turn off.
    """

    def names(self, path, diff_text):
        return [f.reason for f in rule_dependencies(
            make_change(path=path, diff_text=diff_text))
            if f.reason.startswith("dependency added/changed: ")]

    CARGO = (
        "--- a/Cargo.toml\n+++ b/Cargo.toml\n@@ -1,4 +1,5 @@\n"
        " [dependencies]\n"
        ' serde = "1.0.203"\n'          # context: was already there
        '+tokio = "1.38.0"\n'           # added: the only new dependency
        " \n"
        " [package]\n"
    )

    def test_a_context_line_in_the_dependency_section_is_not_an_addition(self):
        self.assertEqual(self.names("Cargo.toml", self.CARGO),
                         ["dependency added/changed: tokio"])

    def test_an_added_line_outside_the_dependency_section_is_not_a_dependency(self):
        diff = (
            "--- a/Cargo.toml\n+++ b/Cargo.toml\n@@ -1,3 +1,4 @@\n"
            " [package]\n"
            '+description = "a thing"\n'
            " [dependencies]\n"
        )
        self.assertEqual(self.names("Cargo.toml", diff), [])

    def test_the_same_holds_for_pipfile(self):
        diff = (
            "--- a/Pipfile\n+++ b/Pipfile\n@@ -1,4 +1,5 @@\n"
            " [packages]\n"
            ' requests = "*"\n'
            '+httpx = "*"\n'
        )
        self.assertEqual(self.names("Pipfile", diff),
                         ["dependency added/changed: httpx"])


class TestLowFindingsDoNotGateByDefault(unittest.TestCase):
    """`--strict` is opt-in, and the default has to be the safe-to-adopt one."""

    LOW = Finding("LOW", "a.py", 1, "TODO/FIXME added", "test-quality")
    MED = Finding("MED", "b.py", 1, "executable bit added", "executable")

    def test_a_low_finding_alone_does_not_gate(self):
        # Called with no `strict` argument at all — that is the point.  A tool
        # that fails the build over a TODO on its first run gets uninstalled.
        self.assertEqual(gating_findings([self.LOW]), [])

    def test_a_med_finding_gates_without_strict(self):
        self.assertEqual(gating_findings([self.MED, self.LOW]), [self.MED])

    def test_strict_pulls_the_low_finding_in(self):
        self.assertEqual(gating_findings([self.LOW], strict=True), [self.LOW])


class TestADeletedManifestIsNotAModifiedOne(unittest.TestCase):
    """`status in ("M", "A", "R", "U")` — the set exists to leave "D" out.

    Deleting requirements.txt is already a MED `file deleted`.  Reporting it a
    second time as HIGH `dependency file modified` would be both wrong and the
    loudest thing in the report, which is how a real HIGH gets scrolled past.
    """

    def test_a_deleted_requirements_file_reports_no_dependency_change(self):
        fc = make_change(status="D", path="requirements.txt", diff_text="")
        self.assertEqual(rule_dependencies(fc), [])

    def test_a_modified_one_still_does(self):
        fc = make_change(status="M", path="requirements.txt",
                         diff_text=diff_with_added(["requests==2.31.0"]))
        self.assertTrue(rule_dependencies(fc))


class TestTheDeletionThreshold(unittest.TestCase):
    """Fifty lines removed is a refactor.  Fifty-one is the documented line."""

    def removed(self, n):
        from agentdiff.rules import rule_deletion
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,%d +1,0 @@\n" % n
        diff += "".join(f"-line {i}\n" for i in range(n))
        return rule_deletion(make_change(path="app.py", diff_text=diff))

    def test_exactly_the_threshold_is_not_flagged(self):
        from agentdiff.rules import _DELETION_THRESHOLD
        self.assertEqual(self.removed(_DELETION_THRESHOLD), [])

    def test_one_more_than_the_threshold_is(self):
        from agentdiff.rules import _DELETION_THRESHOLD
        findings = self.removed(_DELETION_THRESHOLD + 1)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0].severity, "MED")
        # The count is in the message: "51 lines removed" is actionable in a
        # way that "large deletion" is not.
        self.assertIn(str(_DELETION_THRESHOLD + 1), findings[0].reason)


class TestTheLargeFileThresholds(unittest.TestCase):
    """The "possible generated content" thresholds, at their edges.

    Both are documented numbers — the README says >1000 lines and >2000
    characters — so both boundaries are part of the contract, not an internal
    detail.  A hand-written thousand-line file that gets called generated is
    the kind of wrong that makes people stop reading LOW findings.
    """

    def reasons(self, lines):
        from agentdiff.rules import rule_test_quality
        fc = make_change(status="A", path="data/big.py",
                         diff_text=diff_with_added(lines))
        return [f.reason for f in rule_test_quality(fc)]

    def test_exactly_the_line_threshold_is_not_generated_content(self):
        from agentdiff.rules import _LARGE_FILE_THRESHOLD
        self.assertEqual(self.reasons(["x = 1"] * _LARGE_FILE_THRESHOLD), [])

    def test_one_line_more_is(self):
        from agentdiff.rules import _LARGE_FILE_THRESHOLD
        reasons = self.reasons(["x = 1"] * (_LARGE_FILE_THRESHOLD + 1))
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("large file added", reasons[0])

    def test_exactly_the_long_line_threshold_is_fine(self):
        from agentdiff.rules import _LARGE_LINE_THRESHOLD
        self.assertEqual(self.reasons(["x" * _LARGE_LINE_THRESHOLD]), [])

    def test_one_character_more_is_minified_looking(self):
        from agentdiff.rules import _LARGE_LINE_THRESHOLD
        reasons = self.reasons(["x" * (_LARGE_LINE_THRESHOLD + 1)])
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("very long lines", reasons[0])


class TestTheFileMarkersAreNotContent(unittest.TestCase):
    """`--- a/x` and `+++ b/x` are the diff's frame, not lines of the file.

    The walker matches them by prefix, so an added line whose content begins
    with `++` would be dropped too.  That is unreachable for the two formats
    that use this walker — Cargo.toml and Pipfile, where no key starts with a
    plus — and narrowing the match would mean guessing whether `--- foo` is a
    header or a removed `-- foo` comment, which unified diff genuinely does not
    say.  Pinned as-is, deliberately, rather than left to drift.
    """

    def test_neither_marker_is_yielded(self):
        walked = list(_walk_diff_lines(diff_with_added(['serde = "1.0"'])))
        self.assertEqual([text for _added, _lineno, text in walked],
                         ['serde = "1.0"'], walked)

    def test_the_added_line_keeps_its_real_line_number(self):
        walked = list(_walk_diff_lines(diff_with_added(["a = 1", "b = 2"], start=41)))
        self.assertEqual([(added, lineno) for added, lineno, _t in walked],
                         [(True, 41), (True, 42)], walked)


if __name__ == "__main__":
    unittest.main()
