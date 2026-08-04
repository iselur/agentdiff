"""A verdict has to survive the whole way out, not just the path list.

tests/test_hostile.py proves that a file git would quote — `café.py`, a name
with a space — is *reported* under its real name and that its diff is read.
That is where the path enters.  It is not where the path is used.

After it is read, the same string is matched against ignore globs, matched
against scope globs, put through the terminal-escaping in `_safe`, written into
a markdown report, and emitted as JSON.  Every one of those is a place where a
quoted or re-encoded name silently changes what the run *means*: a scope glob of
`*.py` does not match `"caf\303\251.py"`, so the file falls out of scope, and a
HIGH finding inside it stops being reported at all.

A review tool that misses a private key is worse than one that crashes, because
the crash gets noticed.  These tests put a real HIGH finding in a file with an
awkward name and follow it to every exit.

Exit codes are the contract: 0 clean, 1 findings, 2 usage or environment error.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.helpers import write_file
from tests.test_hostile import HostileRepoCase

# A PEM header is the least ambiguous HIGH there is: one line, one rule, no
# entropy threshold to argue with.  If this does not come back, nothing about
# the name survived.
SECRET = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n"

ODD = "café.py"
SPACED = "my file.py"

# What git prints for `café.py` when quoting is on.  Not a path — the point of
# core.quotePath=false — but exactly the string that would leak through if that
# setting were ever dropped.
QUOTED = '"caf\\303\\251.py"'


class OddNameCase(HostileRepoCase):
    """A HIGH finding sitting in a file whose name git wants to quote."""

    def high_findings(self, *argv):
        """Run the CLI as JSON and hand back (exit code, parsed, findings)."""
        code, out, err = self.run_cli("review", "--json", *argv)
        self.assertNoCrash(code, err)
        data = json.loads(out)
        return code, data, [f for f in data["findings"] if f["severity"] == "HIGH"]

    def _config(self, name, body):
        """Write .agentdiff/<name>.  Bytes, because the point is the encoding."""
        d = os.path.join(self.repo, ".agentdiff")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "wb") as fh:
            fh.write(body)
        return path


class TestTheFindingSurvives(OddNameCase):
    """The whole route: git → rules → exit code → stdout."""

    def test_a_secret_in_a_non_ascii_name_is_still_a_high_finding(self):
        write_file(self.repo, ODD, SECRET)
        code, data, high = self.high_findings()
        self.assertEqual(len(high), 1, data["findings"])
        self.assertEqual(high[0]["file"], ODD)
        self.assertEqual(code, 1, "a HIGH finding has to fail the run")

    def test_a_secret_in_a_name_with_a_space_is_still_a_high_finding(self):
        write_file(self.repo, SPACED, SECRET)
        _code, data, high = self.high_findings()
        self.assertEqual([f["file"] for f in high], [SPACED], data["findings"])

    def test_the_gate_is_triggered(self):
        # `gate_triggered` is the field a CI script branches on.  A HIGH that
        # reaches the findings list but not the gate is a merged secret.
        write_file(self.repo, ODD, SECRET)
        _code, data, _high = self.high_findings()
        self.assertTrue(data["gate_triggered"], data)
        self.assertFalse(data["clean"], data)
        self.assertEqual(data["counts"]["HIGH"], 1, data["counts"])

    def test_the_file_counts_as_reviewed_not_unread(self):
        # `reviewed` is how a script tells "nothing was wrong" from "nothing was
        # looked at".  An unreadable name would land in `unread` instead.
        write_file(self.repo, ODD, SECRET)
        _code, data, _high = self.high_findings()
        self.assertEqual(data["unread"], [], data["unread"])
        self.assertEqual(data["reviewed"], 1, data)

    def test_the_printed_review_names_the_file(self):
        # Not the JSON path: `_safe` escapes what could drive a terminal, and an
        # ordinary accented letter is not that.  A reader has to see the name
        # they can type back.
        write_file(self.repo, ODD, SECRET)
        code, out, err = self.run_cli("review")
        self.assertNoCrash(code, err)
        self.assertIn(ODD, out)
        self.assertNotIn("caf\\303\\251", out)

    def test_the_written_report_names_the_file(self):
        write_file(self.repo, ODD, SECRET)
        target = os.path.join(self.repo, "report.md")
        code, _out, err = self.run_cli("review", "--report", target)
        self.assertNoCrash(code, err)
        with open(target, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(ODD, text)


class TestTheGlobsSeeTheSamePath(OddNameCase):
    """Scope and ignore match the path by string.  It has to be the real one."""

    def test_a_scope_glob_covers_it(self):
        # The dangerous direction.  `*.py` does not match `"caf\303\251.py"`,
        # so a quoted name puts the file out of scope — and out-of-scope files
        # are the ones a reviewer skims past.
        write_file(self.repo, ODD, SECRET)
        _code, data, _high = self.high_findings("--scope", "*.py")
        creep = [f for f in data["findings"] if f["rule"] == "out-of-scope"]
        self.assertEqual(creep, [], creep)

    def test_the_high_finding_is_unaffected_by_being_in_scope(self):
        write_file(self.repo, ODD, SECRET)
        _code, data, high = self.high_findings("--scope", "*.py")
        self.assertEqual([f["file"] for f in high], [ODD], data["findings"])

    def test_ignoring_it_by_its_real_name_silences_it(self):
        # The other half of the same claim: if the path that reaches the globs
        # is the real one, then writing the real one has to work.
        write_file(self.repo, ODD, SECRET)
        self._config("ignore", (ODD + "\n").encode("utf-8"))
        _code, data, high = self.high_findings()
        self.assertEqual(high, [], data["findings"])
        # The run still exits 1, and should: writing the ignore file is itself
        # a change, and `ignore-config` flags it MED precisely so that silencing
        # the tool cannot be done quietly.  What has to be gone is the HIGH.
        self.assertEqual([f["rule"] for f in data["findings"]], ["ignore-config"],
                         data["findings"])

    def test_ignoring_it_by_gits_quoted_form_does_not(self):
        # And the quoted form is not a name anyone has.  If this ever silences
        # the finding, paths are being carried around in their display form.
        write_file(self.repo, ODD, SECRET)
        self._config("ignore", (QUOTED + "\n").encode("utf-8"))
        _code, data, high = self.high_findings()
        self.assertEqual([f["file"] for f in high], [ODD], data["findings"])


class TestTheOneOutputGitStillQuotes(OddNameCase):
    """`git diff --summary` is the only path-bearing output read line-by-line.

    Everywhere else agentdiff asks for `-z`, and NUL-separated output is never
    quoted, so `core.quotePath=false` changes nothing there.  `--summary` has no
    `-z` form: it is the one call where that setting is what stands between a
    real path and `"caf\\303\\251.py"`, and it is how the executable bit is
    detected.  A mis-parsed name there does not error — it reports the bit on a
    file that does not exist and stays quiet about the file that gained it.
    """

    files = {"app.py": "print('hello')\n", ODD: "x = 1\n", SPACED: "x = 1\n"}

    def med_files(self, rule="executable"):
        _code, data, _high = self.high_findings()
        return [f["file"] for f in data["findings"] if f["rule"] == rule]

    def test_the_executable_bit_on_a_non_ascii_name_is_reported_on_that_name(self):
        os.chmod(os.path.join(self.repo, ODD), 0o755)
        self.assertEqual(self.med_files(), [ODD], self.med_files())

    def test_the_executable_bit_on_a_name_with_a_space_is_too(self):
        os.chmod(os.path.join(self.repo, SPACED), 0o755)
        self.assertEqual(self.med_files(), [SPACED], self.med_files())

    def test_the_quoted_form_is_never_reported_as_a_file(self):
        # The failure this guards is not an exception.  It is a review naming a
        # path nobody can open, while the file that actually changed goes
        # unmentioned — which reads exactly like a clean run for that file.
        os.chmod(os.path.join(self.repo, ODD), 0o755)
        _code, data, _high = self.high_findings()
        for f in data["findings"]:
            self.assertNotIn("\\303", f["file"], data["findings"])


if __name__ == "__main__":
    unittest.main()
