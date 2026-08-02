"""
agentdiff.git — git plumbing via subprocess.

All functions operate on an absolute repo_root path.
Nothing here modifies the repo.
"""

import os
import subprocess


class GitError(Exception):
    """Raised when git is unavailable, the directory is not a repo, or a ref is bad."""


def _git(args, cwd):
    """Run git with the given args in cwd. Returns CompletedProcess. Never raises on non-zero."""
    try:
        return subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise GitError("git not found in PATH")


def find_repo_root(start=None):
    """Return the absolute git repo root path, or raise GitError."""
    r = _git(["rev-parse", "--show-toplevel"], cwd=start or os.getcwd())
    if r.returncode != 0:
        raise GitError("not a git repository (or git not available)")
    return r.stdout.strip()


def _has_commits(repo_root):
    return _git(["rev-parse", "HEAD"], cwd=repo_root).returncode == 0


def _ref_exists(repo_root, ref):
    return _git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_root).returncode == 0


class FileChange:
    """One path that differs from the comparison ref."""

    __slots__ = ("status", "path", "old_path", "diff_text", "is_binary", "new_exec")

    def __init__(self, status, path, old_path=None):
        self.status = status     # A added, M modified, D deleted, R renamed, U untracked
        self.path = path
        self.old_path = old_path
        self.diff_text = ""      # unified diff or "+lines" for untracked files
        self.is_binary = False
        self.new_exec = False    # executable bit was added


def get_changes(repo_root, since_ref="HEAD", staged_only=False):
    """
    Return list[FileChange] for everything that differs from since_ref.

    Default mode: compares HEAD against working tree (staged + unstaged + untracked).
    staged_only=True: only staged changes (index vs since_ref); useful as a pre-commit hook.
    Raises GitError on bad repo or unknown ref.
    """
    if not _has_commits(repo_root):
        return _new_repo_changes(repo_root)

    if not _ref_exists(repo_root, since_ref):
        raise GitError(f"unknown ref: {since_ref!r}")

    cache = ["--cached"] if staged_only else []

    # 1. Tracked file changes
    r = _git(["diff"] + cache + ["-M", "--name-status", since_ref], cwd=repo_root)
    if r.returncode != 0:
        raise GitError(f"git diff failed: {r.stderr.strip()}")

    changes = []
    seen = set()

    for raw in r.stdout.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        code = parts[0][0]
        if code == "R" and len(parts) >= 3:
            fc = FileChange("R", parts[2], old_path=parts[1])
        else:
            fc = FileChange(code, parts[1])
        changes.append(fc)
        seen.add(fc.path)

    # 2. Untracked files (working-tree mode only)
    if not staged_only:
        r2 = _git(["ls-files", "--others", "--exclude-standard"], cwd=repo_root)
        if r2.returncode == 0:
            for line in r2.stdout.splitlines():
                p = line.strip()
                if p and p not in seen:
                    changes.append(FileChange("U", p))
                    seen.add(p)

    # 3. Executable-bit additions from diff summary
    r_sum = _git(["diff"] + cache + ["--summary", since_ref], cwd=repo_root)
    exec_set = _parse_exec_added(r_sum.stdout if r_sum.returncode == 0 else "")

    # 4. Fill diff_text, is_binary, new_exec
    for fc in changes:
        _fill_diff(fc, repo_root, since_ref, cache)
        if fc.path in exec_set:
            fc.new_exec = True
        # Untracked executables: check filesystem directly
        if fc.status == "U" and not fc.is_binary:
            full = os.path.join(repo_root, fc.path)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                fc.new_exec = True

    return changes


def _new_repo_changes(repo_root):
    """
    For a brand-new repo with no commits, return staged and untracked files.

    git diff HEAD fails with no commits, so we query the index and the working
    tree separately and union the results.
    """
    changes = []
    seen = set()

    # Staged files (git add-ed but not yet committed)
    r = _git(["ls-files", "--cached"], cwd=repo_root)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            p = line.strip()
            if not p:
                continue
            fc = FileChange("A", p)
            _read_as_added(fc, os.path.join(repo_root, p))
            changes.append(fc)
            seen.add(p)

    # Untracked files (not staged, not ignored)
    r2 = _git(["ls-files", "--others", "--exclude-standard"], cwd=repo_root)
    if r2.returncode == 0:
        for line in r2.stdout.splitlines():
            p = line.strip()
            if p and p not in seen:
                fc = FileChange("U", p)
                _read_as_added(fc, os.path.join(repo_root, p))
                changes.append(fc)
                seen.add(p)

    return changes


def _fill_diff(fc, repo_root, since_ref, cache):
    if fc.status == "U":
        _read_as_added(fc, os.path.join(repo_root, fc.path))
        return
    r = _git(["diff"] + cache + [since_ref, "--", fc.path], cwd=repo_root)
    if r.returncode == 0:
        fc.diff_text = r.stdout
        if "Binary files " in fc.diff_text:
            fc.is_binary = True
            fc.diff_text = ""


def _read_as_added(fc, full_path):
    """Read a file from disk and format each line as +line (for untracked/new files)."""
    try:
        with open(full_path, "rb") as f:
            raw = f.read(8192)
        if b"\x00" in raw:
            fc.is_binary = True
            return
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            fc.diff_text = "".join(f"+{line}\n" for line in fh.read().splitlines())
    except OSError:
        pass


def _parse_exec_added(summary_text):
    """Return set of file paths that gained the executable bit in a diff --summary."""
    paths = set()
    for line in summary_text.splitlines():
        s = line.strip()
        # "create mode 100755 path/to/file"
        if s.startswith("create mode 100755 "):
            paths.add(s[len("create mode 100755 "):])
        # "mode change 100644 => 100755 path/to/file"
        elif "mode change" in s and "=> 100755" in s:
            # Take the last whitespace-separated token as the path
            parts = s.split()
            if parts:
                paths.add(parts[-1])
    return paths
