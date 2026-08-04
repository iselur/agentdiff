"""You should be able to review a repository you are not standing in.

`agentdiff review` found its repository by asking git about the current
directory and nothing else, so the only way to review a project was to `cd`
into it first.  That is fine at a prompt and awkward everywhere else: CI checks
out into one directory and runs from another, a pre-commit wrapper may run from
the repo it is guarding or from wherever the editor launched it, and an agent
driving several checkouts has to shell out through `cd` for every one.

The rest of the family already had an answer to this.  `stillworks --project
DIR` works, and works both before and after the subcommand, so `--project` is
the word this family already uses for "operate on that directory instead".
These tests say agentdiff uses it too, spelled the same way and accepted in the
same two places.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestReviewingARepoYouAreNotStandingIn(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentdiff_project_")
        self.repo = os.path.join(self.tmp, "app")
        self.elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(self.repo)
        os.makedirs(self.elsewhere)
        self._git("init", "-q", ".")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        self._write("a.py", "value = 1\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "initial")
        self._write("a.py", "value = 2\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.repo,
                              capture_output=True, text=True, timeout=60)

    def _write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_agentdiff(self, *args, **kwargs):
        """Run from ``elsewhere`` — the whole point is that cwd is not the repo."""
        env = dict(os.environ, PYTHONPATH=_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "agentdiff"] + list(args),
            capture_output=True, text=True, encoding="utf-8",
            cwd=kwargs.pop("cwd", self.elsewhere), env=env, timeout=60)

    def test_review_finds_the_repo_it_was_pointed_at(self):
        # `--scope src/**` puts the one changed file out of scope, so the
        # review has to name it.  A clean review would only say "1 file(s)
        # changed", which is also what a review of the wrong repo could say.
        result = self.run_agentdiff("review", "--project", self.repo,
                                    "--scope", "src/**")
        self.assertNotIn("not a git repository",
                         (result.stdout + result.stderr).lower(),
                         result.stdout + result.stderr)
        self.assertIn("a.py", result.stdout + result.stderr,
                      result.stdout + result.stderr)

    def test_it_works_before_the_subcommand_too(self):
        # `agentdiff --project DIR review` reads more naturally to some people,
        # and stillworks accepts both, so a hand that learned one should not
        # have to learn where the flag goes per tool.
        result = self.run_agentdiff("--project", self.repo, "review",
                                    "--scope", "src/**")
        self.assertIn("a.py", result.stdout + result.stderr,
                      result.stdout + result.stderr)

    def test_the_json_output_is_about_that_repo(self):
        result = self.run_agentdiff("review", "--project", self.repo,
                                    "--scope", "src/**", "--json")
        self.assertIn("a.py", result.stdout, result.stdout + result.stderr)
        self.assertIn('"files_changed": 1', result.stdout, result.stdout)

    def test_scope_writes_into_the_repo_it_was_pointed_at(self):
        # Not just reading: `scope` persists a file, and it has to land in the
        # named project rather than next to wherever the shell happened to be.
        result = self.run_agentdiff("scope", "src/**", "--project", self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(self.repo, ".agentdiff", "scope")),
            result.stdout + result.stderr)
        self.assertFalse(
            os.path.exists(os.path.join(self.elsewhere, ".agentdiff")),
            "it wrote next to the shell instead of into the project")

    def test_a_project_that_is_not_a_repo_is_a_plain_error(self):
        result = self.run_agentdiff("review", "--project", self.elsewhere)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr, result.stderr)

    def test_a_project_that_does_not_exist_says_so(self):
        missing = os.path.join(self.tmp, "no-such-dir")
        result = self.run_agentdiff("review", "--project", missing)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Traceback", result.stderr, result.stderr)
        self.assertIn("no-such-dir", result.stdout + result.stderr,
                      result.stdout + result.stderr)

    def test_standing_in_the_repo_still_works_with_no_flag(self):
        # The other half: this must not become a tool that requires the flag.
        result = self.run_agentdiff("review", "--scope", "src/**",
                                    cwd=self.repo)
        self.assertIn("a.py", result.stdout + result.stderr,
                      result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
