"""
Tests for agentdiff.cli — command-line entry points.

These tests call the command functions directly (not via subprocess) to avoid
PATH / install dependencies. A few tests use subprocess for the exit code contract.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests.helpers import delete_file, make_repo, stage_file, write_file


def _run_cmd(args, cwd):
    """Run agentdiff via python -m agentdiff in cwd. Returns (stdout, stderr, returncode)."""
    result = subprocess.run(
        [sys.executable, "-m", "agentdiff"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.stdout, result.stderr, result.returncode


class TestReviewClean(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"src/app.py": "x = 1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_clean_repo_exits_0(self):
        _, _, code = _run_cmd(["review"], cwd=self.repo)
        self.assertEqual(code, 0)

    def test_clean_repo_prints_clean(self):
        out, _, _ = _run_cmd(["review"], cwd=self.repo)
        self.assertIn("clean", out)


class TestReviewFindings(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"src/app.py": "x = 1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_added_dockerfile_exits_1(self):
        write_file(self.repo, "Dockerfile", "FROM python:3.11\n")
        stage_file(self.repo, "Dockerfile")
        _, _, code = _run_cmd(["review"], cwd=self.repo)
        self.assertEqual(code, 1)

    def test_output_contains_high_label(self):
        write_file(self.repo, "Dockerfile", "FROM python:3.11\n")
        stage_file(self.repo, "Dockerfile")
        out, _, _ = _run_cmd(["review"], cwd=self.repo)
        self.assertIn("HIGH", out)

    def test_json_output_is_valid(self):
        write_file(self.repo, "requirements.txt", "requests==2.31.0\n")
        stage_file(self.repo, "requirements.txt")
        out, _, code = _run_cmd(["review", "--json"], cwd=self.repo)
        data = json.loads(out)
        self.assertIn("findings", data)
        self.assertIn("clean", data)
        self.assertFalse(data["clean"])
        self.assertEqual(code, 1)

    def test_json_clean_output(self):
        out, _, code = _run_cmd(["review", "--json"], cwd=self.repo)
        data = json.loads(out)
        self.assertTrue(data["clean"])
        self.assertEqual(code, 0)

    def test_low_only_exits_0_without_strict(self):
        # A TODO comment alone should not cause exit 1 without --strict
        write_file(self.repo, "src/app.py", "x = 1\n# TODO: fix this\n")
        out, _, code = _run_cmd(["review"], cwd=self.repo)
        self.assertEqual(code, 0)

    def test_low_only_exits_1_with_strict(self):
        write_file(self.repo, "src/app.py", "x = 1\n# TODO: fix this\n")
        out, _, code = _run_cmd(["review", "--strict"], cwd=self.repo)
        self.assertEqual(code, 1)

    def test_error_not_a_repo_exits_2(self):
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="norepro_")
        try:
            _, _, code = _run_cmd(["review"], cwd=tmpdir)
            self.assertEqual(code, 2)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_bad_since_ref_exits_2(self):
        _, _, code = _run_cmd(["review", "--since", "totally-nonexistent-ref"], cwd=self.repo)
        self.assertEqual(code, 2)

    def test_report_file_written(self):
        write_file(self.repo, "Dockerfile", "FROM python:3.11\n")
        stage_file(self.repo, "Dockerfile")
        report_path = os.path.join(self.repo, "report.md")
        _run_cmd(["review", "--report", report_path], cwd=self.repo)
        self.assertTrue(os.path.isfile(report_path))
        with open(report_path) as f:
            content = f.read()
        self.assertIn("agentdiff report", content)
        self.assertIn("HIGH", content)


class TestScopeCommand(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"src/app.py": "x = 1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_scope_saves_file(self):
        _run_cmd(["scope", "src/**", "tests/**"], cwd=self.repo)
        scope_path = os.path.join(self.repo, ".agentdiff", "scope")
        self.assertTrue(os.path.isfile(scope_path))
        with open(scope_path) as f:
            lines = f.read().strip().splitlines()
        self.assertIn("src/**", lines)
        self.assertIn("tests/**", lines)

    def test_scope_exits_0(self):
        _, _, code = _run_cmd(["scope", "src/**"], cwd=self.repo)
        self.assertEqual(code, 0)

    def test_scope_then_review_flags_out_of_scope(self):
        _run_cmd(["scope", "src/**"], cwd=self.repo)
        write_file(self.repo, "infra/deploy.sh", "echo deploy\n")
        stage_file(self.repo, "infra/deploy.sh")
        out, _, _ = _run_cmd(["review"], cwd=self.repo)
        # deploy.sh is a deploy script (ci-release HIGH) + out of scope (MED)
        self.assertIn("MED", out)


class TestRulesCommand(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"f.py": "x=1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_rules_exits_0(self):
        _, _, code = _run_cmd(["rules"], cwd=self.repo)
        self.assertEqual(code, 0)

    def test_rules_lists_all_severities(self):
        out, _, _ = _run_cmd(["rules"], cwd=self.repo)
        self.assertIn("HIGH", out)
        self.assertIn("MED", out)
        self.assertIn("LOW", out)

    def test_rules_mentions_gitleaks(self):
        out, _, _ = _run_cmd(["rules"], cwd=self.repo)
        self.assertIn("gitleaks", out)


class TestVersionCommand(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"f.py": "x=1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_version_flag(self):
        out, _, code = _run_cmd(["--version"], cwd=self.repo)
        self.assertIn("0.1.0", out)


if __name__ == "__main__":
    unittest.main()
