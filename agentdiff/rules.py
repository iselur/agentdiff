"""
agentdiff.rules — deterministic detection rules.

Each rule_* function takes a FileChange (and optionally scope_globs) and
returns list[Finding]. run_rules() applies them all.

Design constraint: when in doubt, do not flag. False positives are fatal for
a tool that people must trust before they trust the agent.
"""

import collections
import fnmatch
import math
import os
import re


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

SEVERITY = ("HIGH", "MED", "LOW")

Finding = collections.namedtuple("Finding", ["severity", "file", "line", "reason", "rule"])


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------

def _added_lines(diff_text):
    """
    Yield (new_lineno, text) for every added line in a unified diff.

    Also handles the '+lines' pseudo-diff format used for untracked files,
    where every line starts with '+' and there are no @@ headers.
    """
    new_lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            if m:
                new_lineno = int(m.group(1)) - 1
        elif raw.startswith("+++"):
            continue
        elif raw.startswith("+"):
            new_lineno += 1
            yield new_lineno, raw[1:]
        elif not raw.startswith("-"):
            new_lineno += 1


def _removed_line_count(diff_text):
    """Count removed lines (starting with '-', not '---') in a unified diff."""
    return sum(
        1 for r in diff_text.splitlines()
        if r.startswith("-") and not r.startswith("---")
    )


def _matches_any(path, globs):
    """True if path matches any of the given fnmatch globs (also tries basename)."""
    basename = os.path.basename(path)
    for g in globs:
        if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(basename, g):
            return True
    return False


# ---------------------------------------------------------------------------
# Rule: secrets (HIGH)
# ---------------------------------------------------------------------------

# PEM private key header — extremely specific, near-zero false positives.
_PEM_RE = re.compile(r"-----BEGIN\s+(?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")

# AWS access key ID — fixed 4-letter prefix plus 16 uppercase alphanumerics.
_AWS_RE = re.compile(r"\b(AKIA|ASIA|AROA|AIDA|AIPA|ANPA|ANVA)[0-9A-Z]{16}\b")

# Assignment of a 40+ character token to a variable with a sensitive name.
# Group 1 = the name keyword; group 2 = the value.
_SENSITIVE_ASSIGN_RE = re.compile(
    r"(?i)(api_key|api_secret|access_key|private_key|secret_key|"
    r"auth_token|access_token|client_secret|password|passwd|secret|token)"
    r"\s*[=:]\s*['\"]?([A-Za-z0-9+/=_\-]{40,})['\"]?"
)


def _entropy(s):
    """Shannon entropy in bits per character."""
    if not s:
        return 0.0
    counts = collections.Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def rule_secrets(change):
    """HIGH: private key blocks, AWS access key IDs, high-entropy token assignments."""
    findings = []
    for lineno, text in _added_lines(change.diff_text):
        if _PEM_RE.search(text):
            findings.append(Finding(
                "HIGH", change.path, lineno, "private key block added", "secrets"
            ))
            continue

        m = _AWS_RE.search(text)
        if m:
            findings.append(Finding(
                "HIGH", change.path, lineno, "AWS access key ID pattern added", "secrets"
            ))
            continue

        m = _SENSITIVE_ASSIGN_RE.search(text)
        if m:
            key_name = m.group(1)
            token = m.group(2)
            # Skip URLs (base64 can contain '/' but '://' is a dead giveaway)
            if "://" not in token and _entropy(token) >= 4.0:
                findings.append(Finding(
                    "HIGH", change.path, lineno,
                    f"high-entropy token assigned to {key_name!r}",
                    "secrets",
                ))
    return findings


# ---------------------------------------------------------------------------
# Rule: CI / release surface (HIGH)
# ---------------------------------------------------------------------------

_CI_GLOBS = [
    ".github/workflows/*",
    ".github/workflows/**",
    "Dockerfile",
    "Dockerfile.*",
    "*.tf",
    ".circleci/*",
    ".circleci/**",
    "Jenkinsfile",
]

_DEPLOY_SCRIPT_RE = re.compile(r"(?i)(deploy|release|publish|ship)")

# A Makefile target that looks like a release step: "release:" or "deploy:"
_MAKE_TARGET_RE = re.compile(r"^(release|deploy|publish|ship)\s*[:?!]", re.IGNORECASE)


def rule_ci_release(change):
    """HIGH: CI/CD files, Dockerfiles, Terraform configs, deploy scripts changed."""
    if change.status == "D":
        return []  # deletions are handled by the deletion rule

    findings = []
    path = change.path

    if _matches_any(path, _CI_GLOBS):
        findings.append(Finding("HIGH", path, 0, f"CI/release file modified: {path}", "ci-release"))
        return findings

    # Makefile: flag if a release/deploy target was added
    basename = os.path.basename(path)
    if basename in ("Makefile", "GNUmakefile", "makefile"):
        for lineno, text in _added_lines(change.diff_text):
            if _MAKE_TARGET_RE.match(text):
                findings.append(Finding(
                    "HIGH", path, lineno,
                    f"Makefile release/deploy target: {text.strip()[:60]}",
                    "ci-release",
                ))
        return findings

    # Deploy/release scripts: filename contains a keyword and has a runnable extension
    if _DEPLOY_SCRIPT_RE.search(basename):
        ext = os.path.splitext(basename)[1]
        if ext in (".sh", ".bash", ".py", ".rb", ".ps1", ".yml", ".yaml") or not ext:
            findings.append(Finding(
                "HIGH", path, 0, f"deploy/release script modified: {path}", "ci-release"
            ))

    return findings


# ---------------------------------------------------------------------------
# Rule: dependencies (HIGH)
# ---------------------------------------------------------------------------

_DEP_GLOBS = [
    "requirements*.txt",
    "requirements/*.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "Gemfile.lock",
]

# requirements.txt: "requests>=2.0" or "flask==2.0.0[extra]"
_REQ_RE = re.compile(r"^([A-Za-z0-9_\-\.][A-Za-z0-9_\-\.]*)[\s>=!<@#\[;]")
_REQ_BARE_RE = re.compile(r"^([A-Za-z0-9_\-\.]{2,})\s*$")

# pyproject.toml: '"requests>=2.28"' or poetry-style 'requests = "^2.28"'
_PYTOML_QUOTED_RE = re.compile(r'"([A-Za-z0-9_\-\.]+)\s*[>=!<~^@]')
_PYTOML_POETRY_RE = re.compile(r'^([A-Za-z0-9_\-\.]+)\s*=\s*"[~^>=]')

# package.json: '"lodash": "^4.17"'
_NPM_RE = re.compile(r'"([A-Za-z0-9_\-\./]+)"\s*:\s*"[~^>=]?\d')

# go.mod: 'github.com/foo/bar v1.0'
_GOMOD_RE = re.compile(r"^([A-Za-z0-9_\-\./]+)\s+v\d")

# Cargo.toml: 'serde = "1.0"'
_CARGO_RE = re.compile(r"^([A-Za-z0-9_\-]+)\s*=\s*")

# Gemfile: "gem 'rails'"
_GEMFILE_RE = re.compile(r"gem\s+['\"]([A-Za-z0-9_\-]+)['\"]")


def _dep_name_from_line(text, basename):
    """Extract a package name from an added dependency file line, or return None."""
    if "requirements" in basename or (basename.endswith(".txt") and "require" in basename):
        line = text.split("#")[0].strip()
        if not line or line.startswith("-") or line.startswith("#"):
            return None
        m = _REQ_RE.match(line)
        if m:
            return m.group(1)
        m = _REQ_BARE_RE.match(line)
        if m:
            return m.group(1)

    elif basename == "pyproject.toml":
        m = _PYTOML_QUOTED_RE.search(text)
        if m:
            return m.group(1)
        m = _PYTOML_POETRY_RE.match(text.strip())
        if m:
            return m.group(1)

    elif basename == "package.json":
        m = _NPM_RE.search(text)
        if m:
            return m.group(1)

    elif basename == "go.mod":
        m = _GOMOD_RE.match(text.strip())
        if m:
            return m.group(1)

    elif basename == "Cargo.toml":
        s = text.strip()
        if s.startswith("[") or "=" not in s:
            return None
        m = _CARGO_RE.match(s)
        if m:
            name = m.group(1)
            # Skip section headers and obvious non-package keys
            if name not in ("version", "name", "edition", "authors", "description"):
                return name

    elif basename in ("Gemfile", "Gemfile.lock"):
        m = _GEMFILE_RE.search(text)
        if m:
            return m.group(1)

    return None


def rule_dependencies(change):
    """HIGH: dependency added or version-changed in a known manifest file."""
    if not _matches_any(change.path, _DEP_GLOBS):
        return []

    basename = os.path.basename(change.path)

    # Lock files: too noisy to parse, just flag the file changed
    if basename in ("package-lock.json", "Gemfile.lock"):
        return [Finding("HIGH", change.path, 0, f"lock file modified: {basename}", "dependencies")]

    findings = []
    seen = set()
    for lineno, text in _added_lines(change.diff_text):
        name = _dep_name_from_line(text, basename)
        if name and name not in seen:
            seen.add(name)
            findings.append(Finding(
                "HIGH", change.path, lineno,
                f"dependency added/changed: {name}",
                "dependencies",
            ))

    # If the file changed but we parsed no specific packages (e.g. a version number bump
    # on a line we couldn't match), still flag the file itself.
    if not findings and change.status in ("M", "A", "R", "U"):
        findings.append(Finding(
            "HIGH", change.path, 0,
            f"dependency file modified: {basename}",
            "dependencies",
        ))

    return findings


# ---------------------------------------------------------------------------
# Rule: out of scope (MED)
# ---------------------------------------------------------------------------

def rule_out_of_scope(change, scope_globs):
    """MED: file changed outside the declared scope."""
    if not scope_globs:
        return []
    if _matches_any(change.path, scope_globs):
        return []
    return [Finding(
        "MED", change.path, 0,
        f"changed outside declared scope ({', '.join(scope_globs)})",
        "out-of-scope",
    )]


# ---------------------------------------------------------------------------
# Rule: deletion (MED)
# ---------------------------------------------------------------------------

_DELETION_THRESHOLD = 50


def rule_deletion(change):
    """MED: file deleted or more than 50 lines removed."""
    if change.status == "D":
        return [Finding("MED", change.path, 0, "file deleted", "deletion")]

    if change.diff_text:
        n = _removed_line_count(change.diff_text)
        if n > _DELETION_THRESHOLD:
            return [Finding("MED", change.path, 0, f"{n} lines removed", "deletion")]

    return []


# ---------------------------------------------------------------------------
# Rule: executable / binary (MED)
# ---------------------------------------------------------------------------

def rule_executable_binary(change):
    """MED: executable bit added, or new binary file added."""
    findings = []
    if change.new_exec:
        findings.append(Finding("MED", change.path, 0, "executable bit added", "executable"))
    if change.is_binary and change.status in ("A", "U"):
        findings.append(Finding("MED", change.path, 0, "binary file added", "executable"))
    return findings


# ---------------------------------------------------------------------------
# Rule: test quality (LOW)
# ---------------------------------------------------------------------------

_TEST_PATH_RE = re.compile(
    r"(?i)(^|[\\/])tests?[\\/]|test_[^/]+\.py$|[^/]+_test\.py$"
    r"|[^/]+\.test\.[jt]sx?$|[^/]+\.spec\.[jt]sx?$"
)
_ASSERT_RE = re.compile(
    r"\b(assert\b|assertEqual|assertTrue|assertFalse|assertRaises|assertIn"
    r"|assertIsNone|expect\s*\(|it\s*\(|describe\s*\()"
)
_TODO_RE = re.compile(r"\b(TODO|FIXME)\b")
_LARGE_LINE_THRESHOLD = 2000
_LARGE_FILE_THRESHOLD = 1000


def _is_test_file(path):
    return bool(_TEST_PATH_RE.search(path))


def rule_test_quality(change):
    """LOW: test files deleted, assertions removed, TODO/FIXME added, large generated file."""
    findings = []

    # Test file deleted
    if change.status == "D" and _is_test_file(change.path):
        return [Finding("LOW", change.path, 0, "test file deleted", "test-quality")]

    if not change.diff_text:
        return findings

    # Assertions removed in a test file
    if _is_test_file(change.path):
        for raw in change.diff_text.splitlines():
            if raw.startswith("-") and not raw.startswith("---") and _ASSERT_RE.search(raw):
                findings.append(Finding(
                    "LOW", change.path, 0, "assertions removed from test file", "test-quality"
                ))
                break  # one finding per file

    # TODO/FIXME added (report only the first occurrence)
    for lineno, text in _added_lines(change.diff_text):
        if _TODO_RE.search(text):
            findings.append(Finding("LOW", change.path, lineno, "TODO/FIXME added", "test-quality"))
            break

    # Large or generated-looking file added
    if change.status in ("A", "U"):
        added = list(_added_lines(change.diff_text))
        if len(added) > _LARGE_FILE_THRESHOLD:
            findings.append(Finding(
                "LOW", change.path, 0,
                f"large file added ({len(added)} lines — possible generated content)",
                "test-quality",
            ))
        elif any(len(text) > _LARGE_LINE_THRESHOLD for _, text in added):
            findings.append(Finding(
                "LOW", change.path, 0,
                "file with very long lines added (possible minified/generated content)",
                "test-quality",
            ))

    return findings


# ---------------------------------------------------------------------------
# Rule metadata (used by 'agentdiff rules')
# ---------------------------------------------------------------------------

RULE_DOCS = [
    (
        "HIGH", "secrets",
        "Detects added private key blocks (PEM), AWS access key ID patterns, "
        "and high-entropy tokens assigned to names containing key/token/secret/password. "
        "Tight patterns only — for deeper coverage, see gitleaks (gitleaks/gitleaks) "
        "and TruffleHog (trufflesecurity/trufflehog).",
    ),
    (
        "HIGH", "ci-release",
        "Flags any change to CI/CD pipeline files, Dockerfiles, Terraform configs, "
        "deploy scripts, and Makefile release/deploy targets. "
        "(.github/workflows/**, Dockerfile*, *.tf, .circleci/**, Jenkinsfile, "
        "Makefile release targets, deploy/release scripts)",
    ),
    (
        "HIGH", "dependencies",
        "Reports added or version-changed entries in dependency manifests "
        "(requirements*.txt, pyproject.toml, package.json, package-lock.json, "
        "go.mod, Cargo.toml, Gemfile). "
        "Reports package name only — no vulnerability data. "
        "For CVE context: github.com/actions/dependency-review-action. "
        "For supply-chain analysis: socket.dev.",
    ),
    (
        "MED", "out-of-scope",
        "When a scope is declared (--scope GLOB or .agentdiff/scope), "
        "flags files changed outside it. "
        "No scope declared means this rule does not run.",
    ),
    (
        "MED", "deletion",
        f"Flags deleted files and changes that remove more than "
        f"{_DELETION_THRESHOLD} lines from a single file.",
    ),
    (
        "MED", "executable",
        "Flags files that gain the executable bit and new binary files added to the repo.",
    ),
    (
        "LOW", "test-quality",
        f"Flags deleted test files, removed assertions in test files, added TODO/FIXME "
        f"markers, and large added files (>{_LARGE_FILE_THRESHOLD} lines or a single "
        f"line >{_LARGE_LINE_THRESHOLD} chars). "
        f"LOW findings only affect the exit code under --strict.",
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def gating_findings(findings, strict=False):
    """Return the subset of findings that should influence the exit code."""
    gate = set(SEVERITY) if strict else {"HIGH", "MED"}
    return [f for f in findings if f.severity in gate]


def _is_ignored(path, patterns):
    if not patterns:
        return False
    basename = os.path.basename(path)
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(basename, p) for p in patterns)


def run_rules(changes, scope_globs=None, ignore_patterns=None):
    """
    Run all rules against every FileChange. Return list[Finding], most severe first.

    scope_globs:     glob patterns for the intended scope (empty list = no scope check).
    ignore_patterns: glob patterns from .agentdiff/ignore.
    """
    scope_globs = scope_globs or []
    ignore_patterns = ignore_patterns or []

    all_findings = []
    for change in changes:
        if _is_ignored(change.path, ignore_patterns):
            continue
        all_findings.extend(rule_secrets(change))
        all_findings.extend(rule_ci_release(change))
        all_findings.extend(rule_dependencies(change))
        all_findings.extend(rule_out_of_scope(change, scope_globs))
        all_findings.extend(rule_deletion(change))
        all_findings.extend(rule_executable_binary(change))
        all_findings.extend(rule_test_quality(change))

    sev_order = {s: i for i, s in enumerate(SEVERITY)}
    all_findings.sort(key=lambda f: (sev_order.get(f.severity, 99), f.file, f.line))
    return all_findings
