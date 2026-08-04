"""`clean` was the word for a review that looked at nothing.

    $ agentdiff review --since HEAD
    clean: 0 file(s) changed, nothing flagged
    $ echo $?
    0

`clean` is this tool's verdict word — it means the changes were examined and
none of them were flagged.  With no changes there is nothing to examine, and
the two situations printed the same first word and the same exit code.  The
JSON was blunter about it: `"clean": true`, which is the field a CI script
reads to decide whether to merge.

The two ways this actually happens are both mundane:

  * `agentdiff review --staged-only` in a pre-commit hook, run before `git add`
  * `agentdiff review --since origin/main` in CI, on a checkout where
    `origin/main` is not the ref the author had in mind — a shallow clone, a
    stale fetch, a typo that still resolves

Both are green, silently, forever, and the reason they are green is that the
review never happened.

The exit code stays 0 here, unlike stillworks' equivalent — see the README.
An empty diff is a true and ordinary state of a repository, and a hook that
starts exiting 2 on it is worse than the problem.  What changes is that the
tool stops calling it clean, says what it actually did and which ref it did
it against, and gives `--json` a `reviewed` count so a script can tell the
difference the word was hiding.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


class Case(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ad-vacuous-")
        self.addCleanup(__import__("shutil").rmtree, self.repo, ignore_errors=True)
        self.git("init", "-q", ".")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("a.py", "import os\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

    def git(self, *argv):
        return subprocess.run(["git", *argv], cwd=self.repo,
                              capture_output=True, text=True, check=False)

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as fh:
            fh.write(text)

    def review(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "agentdiff", "review", *argv],
            cwd=self.repo, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))


class TestNothingWasReviewed(Case):

    def test_it_is_not_called_clean(self):
        p = self.review("--since", "HEAD")
        self.assertNotIn("clean", p.stdout.lower(),
                         "reviewed nothing and called it clean:\n" + p.stdout)

    def test_it_says_nothing_was_reviewed(self):
        p = self.review("--since", "HEAD")
        self.assertIn("no changes", p.stdout.lower(), p.stdout)

    def test_it_names_the_ref_it_looked_against(self):
        # The whole failure is that the ref was not the one the author meant,
        # so the ref is the one fact that has to be on screen.
        self.write("b.py", "x = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "work")
        p = self.review("--since", "HEAD")
        self.assertIn("HEAD", p.stdout, p.stdout)

    def test_the_staged_only_case_says_so_too(self):
        # Different flag, same hole: a pre-commit hook that runs before git add.
        self.write("a.py", "import os\nx = 1\n")
        p = self.review("--staged-only")
        self.assertNotIn("clean", p.stdout.lower(), p.stdout)
        self.assertIn("staged", p.stdout.lower(),
                      "did not say which of the two empty diffs this was:\n"
                      + p.stdout)

    def test_the_json_view_carries_a_reviewed_count(self):
        p = self.review("--since", "HEAD", "--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["reviewed"], 0, data)

    def test_the_report_does_not_call_it_nothing_flagged(self):
        path = os.path.join(self.repo, "r.md")
        self.review("--since", "HEAD", "--report", path)
        with open(path) as fh:
            body = fh.read()
        self.assertNotIn("_Nothing flagged._", body, body)

    def test_the_exit_code_is_still_zero(self):
        # Deliberate, and documented: an empty diff is an ordinary state of a
        # repository, and every pre-commit hook in the world runs this.
        p = self.review("--since", "HEAD")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class TestARealReviewIsUnaffected(Case):

    def setUp(self):
        super().setUp()
        self.write("a.py", "import os\nx = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "work")

    def test_a_reviewed_change_with_no_findings_is_still_clean(self):
        p = self.review("--since", "HEAD~1")
        self.assertIn("clean", p.stdout.lower(), p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_json_view_counts_the_files_it_reviewed(self):
        p = self.review("--since", "HEAD~1", "--json")
        data = json.loads(p.stdout)
        self.assertEqual(data["reviewed"], data["files_changed"])
        self.assertEqual(data["reviewed"], 1, data)
        self.assertTrue(data["clean"])

    def test_a_gating_finding_still_exits_one(self):
        # Something the rules actually flag: a dependency change.
        self.write("requirements.txt", "requests==2.0\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "dep")
        p = self.review("--since", "HEAD~1")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_the_report_still_says_nothing_flagged_for_a_real_review(self):
        path = os.path.join(self.repo, "r.md")
        self.review("--since", "HEAD~1", "--report", path)
        with open(path) as fh:
            body = fh.read()
        self.assertIn("_Nothing flagged._", body, body)


if __name__ == "__main__":
    unittest.main()
