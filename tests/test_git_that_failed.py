"""When git cannot answer, agentdiff must not answer for it.

`agentdiff review` is a gate.  Exit 0 means "I looked, and nothing needs your
attention"; that is what a pre-commit hook or a CI step acts on.

Most of the git calls behind that were written as

    r = _git([...])
    if r.returncode == 0:
        ...use r.stdout...

with no else.  A git that failed produced an empty list, and an empty list is
indistinguishable from a clean tree.  In a repository with no commits yet and a
damaged `.git/index`, `agentdiff review` printed

    clean: 0 file(s) changed, nothing flagged

and exited 0, with a file staged the whole time.  The same damage in a
repository that *does* have commits was reported properly, as an error, exit 2 —
so the tool was already of two minds about it.

An unreadable index is not exotic: an interrupted `git add`, a full disk, a
crashed agent mid-write.  And the shape generalises past the index — any of
these calls failing for any reason turned into "nothing to review".

Nothing found is a finding.  Nothing readable is not.
"""

import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentdiff import git as gitmod
from agentdiff.git import GitError, get_changes
from tests.helpers import make_repo, stage_file, write_file

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _damage_the_index(repo_root):
    """Leave a `.git/index` git will refuse to read."""
    with open(os.path.join(repo_root, ".git", "index"), "wb") as f:
        f.write(b"GARBAGE")


def _review(repo_root, *args):
    """Run the real command, the way a hook would.  Returns (rc, output)."""
    p = subprocess.run(
        [sys.executable, "-m", "agentdiff", "review"] + list(args),
        cwd=repo_root, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": _ROOT},
    )
    return p.returncode, p.stdout + p.stderr


class _Repo(unittest.TestCase):

    def repo(self, files=None):
        root = make_repo(files)
        self.addCleanup(shutil.rmtree, root, True)
        return root


class TestADamagedIndexIsNotACleanTree(_Repo):

    def test_a_new_repo_does_not_report_clean(self):
        root = self.repo()
        write_file(root, "src/a.py", "print(1)\n")
        stage_file(root, "src/a.py")
        _damage_the_index(root)
        rc, out = _review(root)
        self.assertNotIn("clean:", out,
                         "said the tree was clean without being able to read it")
        self.assertNotEqual(rc, 0, "a gate passed on a repository it could not read")

    def test_it_says_what_went_wrong(self):
        root = self.repo()
        write_file(root, "src/a.py", "print(1)\n")
        stage_file(root, "src/a.py")
        _damage_the_index(root)
        rc, out = _review(root)
        self.assertIn("error", out.lower())
        self.assertIn("index", out.lower(),
                      "did not name the thing git could not read")

    def test_the_exit_code_matches_the_repo_that_has_commits(self):
        # The same damage, the same answer, whether or not there is a HEAD.
        new = self.repo()
        write_file(new, "src/a.py", "print(1)\n")
        stage_file(new, "src/a.py")
        _damage_the_index(new)

        old = self.repo({"src/a.py": "print(1)\n"})
        write_file(old, "src/a.py", "print(2)\n")
        _damage_the_index(old)

        self.assertEqual(_review(new)[0], _review(old)[0])

    def test_staged_only_does_not_report_clean_either(self):
        # The pre-commit path, which is the one that actually blocks a commit.
        root = self.repo()
        write_file(root, "src/a.py", "print(1)\n")
        stage_file(root, "src/a.py")
        _damage_the_index(root)
        rc, out = _review(root, "--staged")
        self.assertNotIn("clean:", out)
        self.assertNotEqual(rc, 0)

    def test_the_library_raises_rather_than_returning_nothing(self):
        root = self.repo()
        write_file(root, "src/a.py", "print(1)\n")
        stage_file(root, "src/a.py")
        _damage_the_index(root)
        with self.assertRaises(GitError):
            get_changes(root, since_ref="HEAD")


class TestEveryListingFailureIsReported(_Repo):
    """Not just the index: any of these calls failing means we cannot tell."""

    def setUp(self):
        self._real = gitmod._git
        self.addCleanup(setattr, gitmod, "_git", self._real)

    def _fail_when(self, predicate):
        real = self._real

        class _Failed:
            returncode = 128
            stdout = ""
            stderr = "fatal: pretend git broke"

        def fake(args, cwd, timeout=gitmod.DEFAULT_TIMEOUT):
            if predicate(args):
                return _Failed()
            return real(args, cwd, timeout)
        gitmod._git = fake

    def test_the_untracked_listing(self):
        root = self.repo({"src/a.py": "print(1)\n"})
        write_file(root, "src/new.py", "print(2)\n")
        self._fail_when(lambda a: a[:2] == ["ls-files", "--others"])
        with self.assertRaises(GitError):
            get_changes(root, since_ref="HEAD")

    def test_the_staged_listing_in_a_new_repo(self):
        root = self.repo()
        write_file(root, "src/a.py", "print(1)\n")
        stage_file(root, "src/a.py")
        self._fail_when(lambda a: a[:2] == ["ls-files", "--cached"])
        with self.assertRaises(GitError):
            get_changes(root, since_ref="HEAD")

    def test_the_summary_that_finds_new_executables(self):
        # A failure here silently drops the "became executable" flag, which is
        # one of the things review exists to catch.
        root = self.repo({"src/a.py": "print(1)\n"})
        write_file(root, "src/a.py", "print(2)\n")
        self._fail_when(lambda a: "--summary" in a)
        with self.assertRaises(GitError):
            get_changes(root, since_ref="HEAD")

    def test_the_per_file_diff(self):
        # A failure here left the file listed with an empty diff: reviewed as
        # having changed nothing.
        root = self.repo({"src/a.py": "print(1)\n"})
        write_file(root, "src/a.py", "print(2)\n")
        self._fail_when(lambda a: a[0] == "diff" and "--" in a)
        with self.assertRaises(GitError):
            get_changes(root, since_ref="HEAD")


class TestAHealthyRepoIsUnaffected(_Repo):
    """The regression guard: none of the above may cost a working repo."""

    def test_a_new_repo_still_lists_its_staged_files(self):
        root = self.repo()
        write_file(root, "src/a.py", "print(1)\n")
        stage_file(root, "src/a.py")
        changes = get_changes(root, since_ref="HEAD")
        self.assertEqual([c.path for c in changes], ["src/a.py"])

    def test_a_new_repo_still_lists_untracked_files(self):
        root = self.repo()
        write_file(root, "src/loose.py", "print(1)\n")
        changes = get_changes(root, since_ref="HEAD")
        self.assertIn("src/loose.py", [c.path for c in changes])

    def test_staged_only_still_excludes_untracked(self):
        root = self.repo()
        write_file(root, "src/a.py", "print(1)\n")
        stage_file(root, "src/a.py")
        write_file(root, "src/loose.py", "print(2)\n")
        changes = get_changes(root, since_ref="HEAD", staged_only=True)
        self.assertEqual([c.path for c in changes], ["src/a.py"])

    def test_a_committed_repo_still_reviews_clean(self):
        root = self.repo({"src/a.py": "print(1)\n"})
        write_file(root, "src/a.py", "print(2)\n")
        rc, out = _review(root)
        self.assertEqual(rc, 0)
        self.assertIn("clean", out)

    def test_a_repo_with_nothing_to_say_still_says_it(self):
        root = self.repo({"src/a.py": "print(1)\n"})
        rc, out = _review(root)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
