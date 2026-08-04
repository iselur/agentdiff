"""The dependency manifests agentdiff did not know about.

`_DEP_GLOBS` is the whole reach of the dependencies rule: a file it does not
match is never looked at, and a package added there is not flagged, not gated,
and not mentioned.  The list was written around npm, poetry and cargo, and it
missed the files a Python or Rust or Go project in 2026 actually changes:

  * `uv.lock`   — uv is the default Python resolver for a lot of projects now
  * `Cargo.lock` — `Cargo.toml` was covered; the lock beside it was not
  * `go.sum`    — `go.mod` was covered; the checksums beside it were not
  * `requirements.in` — pip-tools' *source* file, the one a human edits
  * `constraints.txt` — pins, which is what a dependency change looks like
  * `deno.lock`, `bun.lock`, `bun.lockb`

Every one of those is a file where adding a dependency is the ordinary way to
add a dependency.  A review that passes them silently is worse than no review,
because the gate said yes.

The last two tests are the other half: a glob wide enough to catch these must
not start flagging ordinary text files.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentdiff.rules import rule_dependencies
from tests.helpers import diff_with_added, make_change


class ManifestCase(unittest.TestCase):

    def findings_for(self, path, lines):
        fc = make_change(path=path, diff_text=diff_with_added(lines))
        return rule_dependencies(fc)

    def assertFlagged(self, path, lines, expect=None):
        findings = self.findings_for(path, lines)
        self.assertTrue(findings, f"{path} was not flagged at all")
        self.assertEqual(findings[0].severity, "HIGH", findings[0])
        self.assertEqual(findings[0].rule, "dependencies", findings[0])
        if expect:
            self.assertIn(expect, findings[0].reason, findings[0].reason)
        return findings


class TestTheLockFilesWeMissed(ManifestCase):
    """A lock file changing is a dependency changing.  Flag the file."""

    def test_uv_lock(self):
        self.assertFlagged("uv.lock",
                           ['name = "requests"', 'version = "2.31.0"'],
                           "lock file")

    def test_cargo_lock(self):
        self.assertFlagged("Cargo.lock",
                           ['name = "serde"', 'version = "1.0.203"'],
                           "lock file")

    def test_go_sum(self):
        self.assertFlagged("go.sum",
                           ["github.com/gin-gonic/gin v1.9.1 h1:abc="],
                           "lock file")

    def test_deno_lock(self):
        self.assertFlagged("deno.lock", ['  "npm:chalk@5": "sha512-x"'],
                           "lock file")

    def test_bun_lock(self):
        self.assertFlagged("bun.lock", ['"chalk": ["chalk@5.3.0"]'], "lock file")

    def test_a_lock_file_in_a_subdirectory_counts(self):
        # The globs are matched against the basename as well as the path, and
        # a workspace member's lock file is still a lock file.
        self.assertFlagged("crates/engine/Cargo.lock",
                           ['name = "tokio"'], "lock file")


class TestThePipToolsSourceFiles(ManifestCase):
    """`requirements.in` is where the dependency is actually typed."""

    def test_requirements_in_names_the_package(self):
        self.assertFlagged("requirements.in", ["requests==2.31.0"], "requests")

    def test_requirements_dev_in_names_the_package(self):
        self.assertFlagged("requirements-dev.in", ["pytest>=7.0"], "pytest")

    def test_constraints_txt_names_the_package(self):
        self.assertFlagged("constraints.txt", ["urllib3==2.2.1"], "urllib3")

    def test_a_requirements_directory_names_the_package(self):
        # `requirements/*.txt` was already globbed in, but the parser was given
        # the basename — `base.txt` — which matches nothing, so the file was
        # flagged without ever saying which package arrived.  The path is what
        # says what kind of file this is.
        self.assertFlagged("requirements/base.txt", ["django==5.0.6"], "django")


class TestTheGlobsAreStillNarrow(ManifestCase):
    """Wide enough to catch a manifest, not wide enough to catch prose."""

    def test_an_ordinary_text_file_is_not_a_manifest(self):
        self.assertEqual(self.findings_for("notes.txt", ["buy milk"]), [])

    def test_a_readme_is_not_a_manifest(self):
        self.assertEqual(self.findings_for("docs/install.txt",
                                           ["pip install requests"]), [])

    def test_a_source_file_named_like_a_lock_is_not_one(self):
        self.assertEqual(self.findings_for("src/lock.py", ["import threading"]), [])


if __name__ == "__main__":
    unittest.main()
