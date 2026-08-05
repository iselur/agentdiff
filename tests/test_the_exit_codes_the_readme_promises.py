"""The exit codes the README promises, from real runs of the real command.

    **Exit codes:** 0 = nothing flagged at gating severity. 1 = one or more
    findings at HIGH or MED (or LOW under --strict), **or a changed file that
    could not be read**. 2 = usage error (not a git repo, unknown ref). 130 =
    stopped by ctrl-c. 141 = the reader hung up.

That paragraph is the whole contract with CI.  Nobody reads agentdiff's
stdout in a pipeline — `agentdiff review && git commit` reads one number, and
if the number is wrong the mistake is silent in the direction that matters:
a gate that exits 0 on a finding lets the change through and says nothing.

130 and 141 have their own tests (test_interrupt, test_broken_pipe) because
producing them means sending signals.  What was missing was everything else:
no test walked the README's own sentence, and no test ran the command for
each ordinary case and checked the number against what the README says that
case is worth.  So a documented code could quietly stop being reachable, or
the code could grow a new one nobody wrote down, and both look like nothing.

The codes are also read out of cli.py with `ast` — every constant `return`
and every `sys.exit(N)` — and compared with the README both ways.  Every
integer cli.py returns is an exit code today; if that stops being true the
comparison fails here rather than silently widening, and the new return
belongs in `_NOT_AN_EXIT_CODE` with its reason.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

README = os.path.join(_ROOT, "README.md")
CLI_SOURCE = os.path.join(_ROOT, "agentdiff", "cli.py")
SHELL_SOURCE = os.path.join(_ROOT, "agentdiff", "shell.py")

# "0 = nothing flagged at gating severity. 1 = one or more findings ..."
# The paragraph is wrapped, so the space after `=` is sometimes a newline.
_DOCUMENTED = re.compile(r"(?<![\w.])(\d{1,3}) =\s")

# Constant ints cli.py returns that are not exit codes, each with its reason.
_NOT_AN_EXIT_CODE: dict = {}

# A key that trips the secrets rule.  Not a real one — the pattern is what
# matters, and the rule looks at the shape of the line.
FAKE_KEY = 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def documented_codes(text):
    """The exit codes the README's own sentence lists."""
    start = text.find("**Exit codes:**")
    if start < 0:
        return set()
    end = text.find("\n\n", start)
    return {int(code) for code in _DOCUMENTED.findall(text[start:end])}


def shell_codes():
    """The codes `shell.py` chooses on its own -- the two it gives names to.

    Everything else that module returns is a number this command picked and
    handed back out, and those are counted in cli.py where they were picked.
    These two are picked nowhere else.  They are also the two the README
    documents that no longer appear in cli.py at all, so a reader that stops
    at cli.py sees them vanish and calls that agreement.
    """
    with open(SHELL_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    named = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)
                and not isinstance(node.value.value, bool)):
            named[node.targets[0].id] = node.value.value
    returned = {node.value.id for node in ast.walk(tree)
                if isinstance(node, ast.Return)
                and isinstance(node.value, ast.Name)}
    return {named[name] for name in returned & set(named)}


def source_codes():
    """Every constant exit code cli.py can produce."""
    with open(CLI_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    codes = set()
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Return):
            value = node.value
        elif (isinstance(node, ast.Call)
              and getattr(node.func, "attr", None) == "exit"):
            value = node.args[0] if node.args else None
        if (isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and not isinstance(value.value, bool)):
            codes.add(value.value)
    return (codes | shell_codes()) - set(_NOT_AN_EXIT_CODE)


class Repo(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ad-exitcode-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
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
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(name) else None
        with open(path, "w") as handle:
            handle.write(text)

    def review(self, *argv, cwd=None):
        return subprocess.run(
            [sys.executable, "-m", "agentdiff", "review", *argv],
            cwd=cwd or self.repo, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=_ROOT))


class TestTheExitCodesTheREADMEPromises(Repo):

    def test_the_readme_still_promises_exit_codes(self):
        # Without this the two comparisons below pass on an empty set, which
        # is what deleting the paragraph looks like.
        codes = documented_codes(readme())
        self.assertGreaterEqual(len(codes), 4,
                                "no exit-code paragraph left in README.md")

    def test_the_documented_codes_are_the_ones_the_code_can_return(self):
        self.assertEqual(
            sorted(documented_codes(readme())), sorted(source_codes()),
            "README.md's exit codes and the ones agentdiff/cli.py returns "
            "disagree")

    def test_nothing_flagged_is_zero(self):
        self.write("a.py", "import os\nimport sys\n")
        proc = self.review("--since", "HEAD")
        self.assertEqual(proc.returncode, 0,
                         "a change with nothing to flag did not exit 0:\n"
                         + proc.stdout + proc.stderr)

    def test_a_high_finding_is_one(self):
        self.write("a.py", "import os\n" + FAKE_KEY)
        proc = self.review("--since", "HEAD")
        self.assertIn("HIGH", proc.stdout, proc.stdout)
        self.assertEqual(proc.returncode, 1,
                         "a HIGH finding did not gate:\n" + proc.stdout)

    def test_a_low_finding_gates_only_under_strict(self):
        # The README says LOW counts "under --strict", so both halves of that
        # sentence get run: without the flag it must not gate.
        self.write("a.py", "import os\n# TODO: remove this\n")
        relaxed = self.review("--since", "HEAD")
        self.assertIn("LOW", relaxed.stdout, relaxed.stdout)
        self.assertEqual(relaxed.returncode, 0,
                         "a LOW finding gated without --strict:\n"
                         + relaxed.stdout)
        strict = self.review("--since", "HEAD", "--strict")
        self.assertEqual(strict.returncode, 1,
                         "a LOW finding did not gate under --strict:\n"
                         + strict.stdout)

    def test_not_a_git_repo_is_two(self):
        outside = tempfile.mkdtemp(prefix="ad-notrepo-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        proc = self.review(cwd=outside)
        self.assertEqual(proc.returncode, 2,
                         "running outside a repo did not exit 2:\n"
                         + proc.stdout + proc.stderr)

    def test_an_unknown_ref_is_two(self):
        proc = self.review("--since", "no-such-ref-anywhere")
        self.assertEqual(proc.returncode, 2,
                         "an unknown ref did not exit 2:\n"
                         + proc.stdout + proc.stderr)

    def test_a_file_that_could_not_be_read_is_one(self):
        # The README singles this out: the run found nothing, but it also
        # cleared nothing, so it must not read as a pass.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root — chmod does not deny us anything")
        self.write("new.py", "x = 1\n")
        os.chmod(os.path.join(self.repo, "new.py"), 0)
        self.addCleanup(os.chmod, os.path.join(self.repo, "new.py"), 0o644)
        proc = self.review("--since", "HEAD")
        self.assertIn("could not be read", proc.stdout, proc.stdout)
        self.assertEqual(proc.returncode, 1,
                         "a changed file nobody could read did not gate:\n"
                         + proc.stdout)


if __name__ == "__main__":
    unittest.main()
