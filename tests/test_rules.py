"""
Tests for agentdiff.rules — rule engine logic.

All tests work with in-memory FileChange objects; no git required.
"""

import unittest

from agentdiff.rules import (
    Finding,
    gating_findings,
    rule_ci_release,
    rule_deletion,
    rule_dependencies,
    rule_executable_binary,
    rule_out_of_scope,
    rule_secrets,
    rule_test_quality,
    run_rules,
)
from tests.helpers import diff_with_added, make_change


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def findings_by_rule(findings, rule_name):
    return [f for f in findings if f.rule == rule_name]


# ---------------------------------------------------------------------------
# Secrets rule
# ---------------------------------------------------------------------------

class TestRuleSecrets(unittest.TestCase):

    def test_pem_private_key_flagged(self):
        diff = diff_with_added(["-----BEGIN RSA PRIVATE KEY-----"])
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "HIGH")
        self.assertIn("private key", findings[0].reason)

    def test_openssh_private_key_flagged(self):
        diff = diff_with_added(["-----BEGIN OPENSSH PRIVATE KEY-----"])
        fc = make_change(diff_text=diff)
        self.assertTrue(rule_secrets(fc))

    def test_aws_access_key_flagged(self):
        # AKIA + exactly 16 uppercase/digit chars = 20-char key
        diff2 = diff_with_added(["key = 'AKIAXYZ1234567890ABC'"])
        fc2 = make_change(diff_text=diff2)
        findings = rule_secrets(fc2)
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, "HIGH")

    def test_high_entropy_secret_flagged(self):
        # 41 distinct chars — well above the 40-char threshold, high entropy
        token = "aB3dE6gH9jKlMnOpQrStUvWxYz0123456789ABCDE"  # 42 chars
        diff = diff_with_added([f'secret = "{token}"'])
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertTrue(findings, "Should flag high-entropy secret assignment")
        self.assertEqual(findings[0].severity, "HIGH")

    def test_high_entropy_api_key_flagged(self):
        # 42 chars with high character diversity
        token = "xK9mP2nQ7rS4tU1vW8yZ0aB3cD6eF5gH7jKlMnOp"  # 42 chars
        diff = diff_with_added([f'api_key = "{token}"'])
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertTrue(findings)

    def test_no_flag_on_low_entropy_password(self):
        # Low-entropy value: repeated characters
        diff = diff_with_added(['password = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'])
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertFalse(findings, "Low-entropy value should not be flagged")

    def test_no_flag_on_short_value(self):
        # Value is only 20 chars — too short for the 40-char threshold
        diff = diff_with_added(['secret = "abcde12345fghij67890"'])
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertFalse(findings)

    def test_no_flag_on_url(self):
        # A URL in a 'token' field should not be flagged (contains '://')
        diff = diff_with_added(['token = "https://example.com/api/v1/resource/endpoint/callback"'])
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertFalse(findings, "URLs should not be flagged as secrets")

    def test_removed_lines_not_flagged(self):
        # The rule must only check added lines (+), not removed ones (-)
        diff = "--- a/cfg.py\n+++ b/cfg.py\n@@ -1,1 +1,0 @@\n-secret='AKIAXYZ1234567890AB'\n"
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertFalse(findings, "Removed lines should not trigger secret rule")

    def test_context_lines_not_flagged(self):
        token = "xK9mP2nQ7rS4tU1vW8yZ0aB3cD6eF5gH7jKlM"
        # Context line (no leading +/-) should not be flagged
        diff = f"--- a/cfg.py\n+++ b/cfg.py\n@@ -1,1 +1,1 @@\n secret='{token}'\n"
        fc = make_change(diff_text=diff)
        findings = rule_secrets(fc)
        self.assertFalse(findings)


# ---------------------------------------------------------------------------
# CI / release rule
# ---------------------------------------------------------------------------

class TestRuleCiRelease(unittest.TestCase):

    def test_github_workflow_flagged(self):
        fc = make_change(path=".github/workflows/ci.yml")
        self.assertTrue(rule_ci_release(fc))

    def test_dockerfile_flagged(self):
        fc = make_change(path="Dockerfile")
        self.assertTrue(rule_ci_release(fc))

    def test_dockerfile_variant_flagged(self):
        fc = make_change(path="Dockerfile.prod")
        self.assertTrue(rule_ci_release(fc))

    def test_terraform_flagged(self):
        fc = make_change(path="infra/main.tf")
        self.assertTrue(rule_ci_release(fc))

    def test_jenkinsfile_flagged(self):
        fc = make_change(path="Jenkinsfile")
        self.assertTrue(rule_ci_release(fc))

    def test_makefile_release_target_flagged(self):
        diff = diff_with_added(["release:", "\tpython setup.py sdist upload"])
        fc = make_change(path="Makefile", diff_text=diff)
        findings = rule_ci_release(fc)
        self.assertTrue(findings, "Makefile release target should be flagged")
        self.assertEqual(findings[0].severity, "HIGH")

    def test_makefile_no_release_target_not_flagged(self):
        diff = diff_with_added(["test:", "\tpytest tests/"])
        fc = make_change(path="Makefile", diff_text=diff)
        findings = rule_ci_release(fc)
        self.assertFalse(findings, "Makefile without release targets should not be flagged")

    def test_regular_py_file_not_flagged(self):
        fc = make_change(path="src/app.py")
        self.assertFalse(rule_ci_release(fc))

    def test_deleted_ci_file_not_flagged(self):
        # Deletions are covered by the deletion rule, not ci-release
        fc = make_change(status="D", path=".github/workflows/ci.yml")
        self.assertFalse(rule_ci_release(fc))


# ---------------------------------------------------------------------------
# Dependencies rule
# ---------------------------------------------------------------------------

class TestRuleDependencies(unittest.TestCase):

    def test_requirements_txt_dep_flagged(self):
        diff = diff_with_added(["requests==2.31.0"])
        fc = make_change(path="requirements.txt", diff_text=diff)
        findings = rule_dependencies(fc)
        self.assertTrue(findings)
        self.assertIn("requests", findings[0].reason)

    def test_requirements_dev_txt_flagged(self):
        diff = diff_with_added(["pytest>=7.0"])
        fc = make_change(path="requirements-dev.txt", diff_text=diff)
        self.assertTrue(rule_dependencies(fc))

    def test_package_json_dep_flagged(self):
        diff = diff_with_added(['    "lodash": "^4.17.21",'])
        fc = make_change(path="package.json", diff_text=diff)
        findings = rule_dependencies(fc)
        self.assertTrue(findings)
        self.assertIn("lodash", findings[0].reason)

    def test_gomod_dep_flagged(self):
        diff = diff_with_added(["github.com/gin-gonic/gin v1.9.1"])
        fc = make_change(path="go.mod", diff_text=diff)
        findings = rule_dependencies(fc)
        self.assertTrue(findings)
        self.assertIn("gin", findings[0].reason)

    def test_lock_file_flagged_without_parsing(self):
        diff = diff_with_added(['  "resolved": "https://registry.npmjs.org/lodash"'])
        fc = make_change(path="package-lock.json", diff_text=diff)
        findings = rule_dependencies(fc)
        self.assertTrue(findings)
        self.assertIn("lock file", findings[0].reason)

    def test_non_dep_file_not_flagged(self):
        diff = diff_with_added(["import os"])
        fc = make_change(path="src/app.py", diff_text=diff)
        self.assertFalse(rule_dependencies(fc))

    def test_requirements_comment_line_not_flagged(self):
        diff = diff_with_added(["# this is a comment"])
        fc = make_change(path="requirements.txt", diff_text=diff)
        findings = rule_dependencies(fc)
        # May produce a "dependency file modified" finding, but not a spurious package name
        for f in findings:
            self.assertNotIn("#", f.reason)


# ---------------------------------------------------------------------------
# Out-of-scope rule
# ---------------------------------------------------------------------------

class TestRuleOutOfScope(unittest.TestCase):

    def test_file_outside_scope_flagged(self):
        fc = make_change(path="scripts/deploy.sh")
        findings = rule_out_of_scope(fc, scope_globs=["src/**", "src/*.py"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "MED")

    def test_file_inside_scope_not_flagged(self):
        fc = make_change(path="src/app.py")
        findings = rule_out_of_scope(fc, scope_globs=["src/*.py"])
        self.assertFalse(findings)

    def test_no_scope_means_no_finding(self):
        fc = make_change(path="anything.py")
        findings = rule_out_of_scope(fc, scope_globs=[])
        self.assertFalse(findings)

    def test_basename_glob_matches(self):
        # "*.py" should match "src/deep/module.py"
        fc = make_change(path="src/deep/module.py")
        findings = rule_out_of_scope(fc, scope_globs=["*.py"])
        self.assertFalse(findings, "Basename glob should match file in subdirectory")


# ---------------------------------------------------------------------------
# Deletion rule
# ---------------------------------------------------------------------------

class TestRuleDeletion(unittest.TestCase):

    def test_deleted_file_flagged(self):
        fc = make_change(status="D", path="src/old.py")
        findings = rule_deletion(fc)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "MED")
        self.assertEqual(findings[0].reason, "file deleted")

    def test_large_removal_flagged(self):
        removed = [f"- line {i}" for i in range(60)]
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1,60 +1,0 @@\n" + "\n".join(removed)
        fc = make_change(diff_text=diff)
        findings = rule_deletion(fc)
        self.assertEqual(len(findings), 1)
        self.assertIn("60 lines removed", findings[0].reason)

    def test_small_removal_not_flagged(self):
        removed = [f"- line {i}" for i in range(10)]
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1,10 +1,0 @@\n" + "\n".join(removed)
        fc = make_change(diff_text=diff)
        self.assertFalse(rule_deletion(fc))


# ---------------------------------------------------------------------------
# Executable / binary rule
# ---------------------------------------------------------------------------

class TestRuleExecutableBinary(unittest.TestCase):

    def test_executable_bit_added_flagged(self):
        fc = make_change(status="A", path="run.sh", new_exec=True)
        findings = rule_executable_binary(fc)
        self.assertEqual(len(findings), 1)
        self.assertIn("executable", findings[0].reason)

    def test_binary_file_added_flagged(self):
        fc = make_change(status="A", path="assets/lib.so", is_binary=True)
        findings = rule_executable_binary(fc)
        self.assertEqual(len(findings), 1)
        self.assertIn("binary", findings[0].reason)

    def test_no_exec_no_binary_not_flagged(self):
        fc = make_change(status="M", path="src/app.py")
        self.assertFalse(rule_executable_binary(fc))

    def test_modified_binary_not_flagged(self):
        # Binary that was already in the repo being modified is not a new binary
        fc = make_change(status="M", path="assets/icon.png", is_binary=True)
        self.assertFalse(rule_executable_binary(fc))


# ---------------------------------------------------------------------------
# Test quality rule
# ---------------------------------------------------------------------------

class TestRuleTestQuality(unittest.TestCase):

    def test_todo_added_flagged(self):
        diff = diff_with_added(["# TODO: fix this properly"])
        fc = make_change(diff_text=diff)
        findings = rule_test_quality(fc)
        self.assertTrue(any("TODO" in f.reason for f in findings))
        self.assertTrue(all(f.severity == "LOW" for f in findings))

    def test_fixme_added_flagged(self):
        diff = diff_with_added(["# FIXME: broken"])
        fc = make_change(diff_text=diff)
        findings = rule_test_quality(fc)
        self.assertTrue(findings)

    def test_test_file_deleted_flagged(self):
        fc = make_change(status="D", path="tests/test_app.py")
        findings = rule_test_quality(fc)
        self.assertEqual(len(findings), 1)
        self.assertIn("test file deleted", findings[0].reason)

    def test_non_test_file_deleted_not_flagged(self):
        fc = make_change(status="D", path="src/app.py")
        self.assertFalse(rule_test_quality(fc))

    def test_assertion_removed_from_test_flagged(self):
        diff = (
            "--- a/tests/test_app.py\n"
            "+++ b/tests/test_app.py\n"
            "@@ -5,7 +5,6 @@\n"
            " def test_foo():\n"
            "-    self.assertEqual(result, expected)\n"
            "     pass\n"
        )
        fc = make_change(path="tests/test_app.py", diff_text=diff)
        findings = rule_test_quality(fc)
        self.assertTrue(any("assertions removed" in f.reason for f in findings))

    def test_large_added_file_flagged(self):
        lines = [f"line {i}" for i in range(1100)]
        diff = diff_with_added(lines)
        fc = make_change(status="A", path="generated.py", diff_text=diff)
        findings = rule_test_quality(fc)
        self.assertTrue(any("large file" in f.reason for f in findings))

    def test_normal_added_file_not_flagged(self):
        diff = diff_with_added(["def hello(): pass", "    return 'world'"])
        fc = make_change(status="A", diff_text=diff)
        self.assertFalse(rule_test_quality(fc))


# ---------------------------------------------------------------------------
# Gating logic
# ---------------------------------------------------------------------------

class TestGating(unittest.TestCase):

    def _low_finding(self):
        return Finding("LOW", "src/app.py", 1, "TODO added", "test-quality")

    def _high_finding(self):
        return Finding("HIGH", "src/cfg.py", 5, "private key added", "secrets")

    def test_low_only_does_not_gate_by_default(self):
        gating = gating_findings([self._low_finding()], strict=False)
        self.assertFalse(gating)

    def test_low_gates_under_strict(self):
        gating = gating_findings([self._low_finding()], strict=True)
        self.assertTrue(gating)

    def test_high_always_gates(self):
        gating = gating_findings([self._high_finding()], strict=False)
        self.assertTrue(gating)

    def test_run_rules_sorted_high_first(self):
        changes = [
            make_change(
                status="A",
                path="tests/test_foo.py",
                diff_text=diff_with_added(["# TODO"]),
            ),
            make_change(
                status="A",
                path="Dockerfile",
            ),
        ]
        findings = run_rules(changes)
        if len(findings) >= 2:
            sev_order = {"HIGH": 0, "MED": 1, "LOW": 2}
            for i in range(len(findings) - 1):
                self.assertLessEqual(
                    sev_order.get(findings[i].severity, 9),
                    sev_order.get(findings[i + 1].severity, 9),
                    "Findings should be sorted most severe first",
                )

    def test_ignore_patterns_exclude_file(self):
        diff = diff_with_added(["-----BEGIN RSA PRIVATE KEY-----"])
        changes = [make_change(path="tests/fixtures/fake_key.pem", diff_text=diff)]
        findings = run_rules(changes, ignore_patterns=["*.pem"])
        self.assertFalse(findings, "Ignored file should produce no findings")


if __name__ == "__main__":
    unittest.main()
