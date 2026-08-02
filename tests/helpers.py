"""
Test helpers: temporary git repo factory and FileChange constructor.
"""

import os
import shutil
import subprocess
import tempfile

from agentdiff.git import FileChange


def make_repo(files=None):
    """
    Create a temporary git repo, optionally with an initial committed set of files.

    Returns the repo root path. Call shutil.rmtree(root) in tearDown.
    files: dict of {relative_path: content_string}. If None, repo has no commits.
    """
    tmpdir = tempfile.mkdtemp(prefix="agentdiff_test_")

    def git(*args, **kwargs):
        result = subprocess.run(
            ["git"] + list(args),
            cwd=tmpdir,
            capture_output=True,
            text=True,
            **kwargs,
        )
        if result.returncode != 0 and kwargs.get("check", False):
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
        return result

    git("init")
    git("config", "user.email", "test@agentdiff.test")
    git("config", "user.name", "agentdiff test")

    if files:
        for path, content in files.items():
            full = os.path.join(tmpdir, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(content)
            git("add", path)
        git("commit", "-m", "initial commit", check=True)

    return tmpdir


def write_file(repo_root, path, content):
    """Write a file inside the repo."""
    full = os.path.join(repo_root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return full


def stage_file(repo_root, path):
    subprocess.run(["git", "add", path], cwd=repo_root, capture_output=True)


def delete_file(repo_root, path):
    subprocess.run(["git", "rm", path], cwd=repo_root, capture_output=True)


def make_change(
    status="M",
    path="src/app.py",
    diff_text="",
    is_binary=False,
    new_exec=False,
    old_path=None,
):
    """Build a FileChange with preset fields for rule unit tests."""
    fc = FileChange(status, path, old_path=old_path)
    fc.diff_text = diff_text
    fc.is_binary = is_binary
    fc.new_exec = new_exec
    return fc


def diff_with_added(lines, start=1):
    """Build a minimal unified diff string that adds the given lines."""
    header = (
        "--- a/test.py\n"
        "+++ b/test.py\n"
        f"@@ -1,0 +{start},{len(lines)} @@\n"
    )
    return header + "".join(f"+{line}\n" for line in lines)
