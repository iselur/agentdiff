"""
Tests for agentdiff.git — git plumbing.

Each test creates a fresh temporary repo and tears it down.
"""

import os
import shutil
import stat
import subprocess
import unittest

from agentdiff.git import FileChange, GitError, find_repo_root, get_changes
from tests.helpers import delete_file, make_repo, stage_file, write_file


class TestFindRepoRoot(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"README.md": "hello\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_finds_root_from_repo_dir(self):
        root = find_repo_root(self.repo)
        self.assertEqual(root, self.repo)

    def test_finds_root_from_subdir(self):
        subdir = os.path.join(self.repo, "src")
        os.makedirs(subdir)
        root = find_repo_root(subdir)
        self.assertEqual(root, self.repo)

    def test_raises_outside_repo(self):
        tmpdir = "/tmp"
        # /tmp is very unlikely to be inside a git repo
        try:
            root = find_repo_root(tmpdir)
            # If /tmp happens to be inside a git repo on this machine, skip
            self.skipTest("/tmp is inside a git repo on this machine")
        except GitError:
            pass  # expected


class TestGetChangesBasic(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"src/app.py": "x = 1\n", "README.md": "hi\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_clean_repo_returns_no_changes(self):
        changes = get_changes(self.repo)
        self.assertEqual(changes, [])

    def test_modified_file_detected(self):
        write_file(self.repo, "src/app.py", "x = 2\n")
        changes = get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn("src/app.py", paths)
        m = next(c for c in changes if c.path == "src/app.py")
        self.assertEqual(m.status, "M")

    def test_added_file_detected(self):
        write_file(self.repo, "src/new.py", "def hello(): pass\n")
        stage_file(self.repo, "src/new.py")
        changes = get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn("src/new.py", paths)
        a = next(c for c in changes if c.path == "src/new.py")
        self.assertEqual(a.status, "A")

    def test_deleted_file_detected(self):
        delete_file(self.repo, "README.md")
        changes = get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn("README.md", paths)
        d = next(c for c in changes if c.path == "README.md")
        self.assertEqual(d.status, "D")

    def test_untracked_file_detected(self):
        write_file(self.repo, "scratch.txt", "notes\n")
        changes = get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn("scratch.txt", paths)
        u = next(c for c in changes if c.path == "scratch.txt")
        self.assertEqual(u.status, "U")

    def test_diff_text_populated_for_modified(self):
        write_file(self.repo, "src/app.py", "x = 999\n")
        changes = get_changes(self.repo)
        m = next(c for c in changes if c.path == "src/app.py")
        self.assertIn("+x = 999", m.diff_text)

    def test_diff_text_populated_for_untracked(self):
        write_file(self.repo, "notes.txt", "hello world\n")
        changes = get_changes(self.repo)
        u = next(c for c in changes if c.path == "notes.txt")
        self.assertIn("+hello world", u.diff_text)


class TestGetChangesStagedOnly(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"src/app.py": "x = 1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_staged_only_includes_staged(self):
        write_file(self.repo, "new.py", "y = 2\n")
        stage_file(self.repo, "new.py")
        changes = get_changes(self.repo, staged_only=True)
        paths = [c.path for c in changes]
        self.assertIn("new.py", paths)

    def test_staged_only_excludes_untracked(self):
        write_file(self.repo, "unstaged.py", "z = 3\n")
        # Do NOT stage it
        changes = get_changes(self.repo, staged_only=True)
        paths = [c.path for c in changes]
        self.assertNotIn("unstaged.py", paths)


class TestGetChangesBadRef(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"f.py": "x=1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_bad_ref_raises_git_error(self):
        with self.assertRaises(GitError):
            get_changes(self.repo, since_ref="nonexistent-ref-abc123")


class TestGetChangesNewRepo(unittest.TestCase):
    """Repo with no commits at all."""

    def setUp(self):
        self.repo = make_repo()  # no files, no commits

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_new_repo_staged_files_returned(self):
        write_file(self.repo, "first.py", "print('hi')\n")
        stage_file(self.repo, "first.py")
        changes = get_changes(self.repo)
        paths = [c.path for c in changes]
        self.assertIn("first.py", paths)


if __name__ == "__main__":
    unittest.main()
