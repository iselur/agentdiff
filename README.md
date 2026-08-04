# agentdiff

See what the agent actually changed — before you merge.

An AI coding agent just edited your repo. Before you accept the diff you want
to know, without reading every line: did it touch anything outside what you
asked for? Did it add a dependency, modify a CI pipeline, or leave a
high-entropy string in a config file?

agentdiff answers those questions in under a second, deterministically, with
no LLM call and nothing leaving your machine.

Part of a small family of zero-dependency tools for working with coding agents
— see [stillworks](https://github.com/iselur/stillworks) for behavior-lock /
characterization testing.

---

## 30-second quickstart

```bash
pip install 'stillworks[all]'   # all five agent tools, including this one
pip install agentdiff-cli       # or just this one (the command is `agentdiff`)

# Inside any git repo, after an agent session:
agentdiff review

# Tell it what the agent was supposed to touch:
agentdiff scope "src/**" "tests/**"
agentdiff review          # now flags files outside that scope

# See exactly what every rule flags (and which tools go deeper):
agentdiff rules

# Machine-readable output for CI:
agentdiff review --json
```

## Real example output

A repo where an agent was scoped to `src/*` and `tests/*` but also
created a `Dockerfile`, a `requirements.txt`, and added a `TODO` comment:

```
$ agentdiff review --scope "src/*" --scope "tests/*"

HIGH (2)
  HIGH   Dockerfile  CI/release file modified: Dockerfile
  HIGH   requirements.txt:1  dependency added/changed: requests

MED (2)
  MED    Dockerfile  changed outside declared scope (src/*, tests/*)
  MED    requirements.txt  changed outside declared scope (src/*, tests/*)

LOW (1)
  LOW    src/main.py:1  TODO/FIXME added

5 finding(s): 2 HIGH, 2 MED, 1 LOW — review before merge
```

Exit code is 1 (findings at HIGH or MED). With `--json` — one finding per line
here for width; the real output is indented:

```json
{
  "findings": [
    {"severity": "HIGH", "file": "Dockerfile",       "line": 0, "reason": "CI/release file modified: Dockerfile", "rule": "ci-release"},
    {"severity": "HIGH", "file": "requirements.txt", "line": 1, "reason": "dependency added/changed: requests",   "rule": "dependencies"},
    {"severity": "MED",  "file": "Dockerfile",       "line": 0, "reason": "changed outside declared scope (src/*, tests/*)", "rule": "out-of-scope"},
    {"severity": "MED",  "file": "requirements.txt", "line": 0, "reason": "changed outside declared scope (src/*, tests/*)", "rule": "out-of-scope"},
    {"severity": "LOW",  "file": "src/main.py",      "line": 1, "reason": "TODO/FIXME added", "rule": "test-quality"}
  ],
  "files_changed": 3,
  "clean": false,
  "gate_triggered": true,
  "counts": {"HIGH": 2, "MED": 2, "LOW": 1}
}
```

---

## CLI reference

```
agentdiff review [--project DIR] [--since GIT_REF] [--scope GLOB]... [--json]
                 [--report FILE] [--strict] [--staged-only | --pre-commit]

agentdiff scope GLOB...     # save the intended scope to .agentdiff/scope
agentdiff rules             # print every rule and what it flags
```

**`--project DIR`** Review that repository instead of the current directory,
before or after the subcommand. CI checks out into one directory and runs from
another, and an agent driving several checkouts shouldn't have to `cd` for each
one. The same flag the rest of the family uses; a path that isn't there is an
error naming it.

**`--since GIT_REF`** Compare the working tree against that ref instead of
HEAD. Useful when the agent was handed a feature branch and you want to see
only what changed since that branch was cut.

**`--scope GLOB`** Repeatable. Overrides `.agentdiff/scope` for this run.
Files not matching any glob produce MED findings.

**`--strict`** LOW findings also set exit code 1.

**`--staged-only` / `--pre-commit`** Only inspect staged (git-indexed) files.
Clean pre-commit hook integration:
```
# .git/hooks/pre-commit
agentdiff review --staged-only || exit 1
```

**`--report FILE`** Write a markdown evidence document to FILE, suitable for
pasting into a PR description or issue comment.

**Exit codes:** 0 = nothing flagged at gating severity. 1 = one or more
findings at HIGH or MED (or LOW under --strict). 2 = usage error (not a git
repo, unknown ref).

---

## Rules

Run `agentdiff rules` to see all rules and which specialist tools cover
each domain more deeply.

| Severity | Rule | What it catches |
|----------|------|-----------------|
| HIGH | secrets | PEM private key blocks, AWS access key ID patterns, high-entropy tokens assigned to names containing key/token/secret/password |
| HIGH | ci-release | `.github/workflows/`, Dockerfiles, `*.tf`, `.circleci/`, Jenkinsfile, Makefile release targets, deploy scripts (shell/Python/Ruby/PowerShell — not YAML/JSON data files) |
| HIGH | dependencies | Added or version-changed packages in requirements.txt, pyproject.toml, package.json, go.mod, Cargo.toml (section-aware), Gemfile, Pipfile. Lock files flagged as modified: package-lock.json, Gemfile.lock, poetry.lock, yarn.lock, pnpm-lock.yaml, Pipfile.lock, composer.lock |
| MED | ignore-config | `.agentdiff/ignore` added or modified. The ignore file is never suppressed by its own patterns — an agent silencing its reviewer is always flagged |
| MED | out-of-scope | Files changed outside the declared scope (only when scope is set) |
| MED | deletion | File deleted or more than 50 lines removed from a file |
| MED | executable | Executable bit added, or a new binary file added |
| LOW | test-quality | Test files deleted or renamed out of the test tree, assertions removed, TODO/FIXME added, large (>1000 line) or minified-looking files added |

LOW findings only affect the exit code under `--strict`.

---

## Config files

Both live in `.agentdiff/` at the repo root.

**`.agentdiff/scope`** — one glob per line, `#` comments. Written by
`agentdiff scope`. Lists the paths the agent was authorized to touch.

**`.agentdiff/ignore`** — one glob per line, `#` comments. Files matching
these globs are skipped by all rules. Useful for vendored code, test fixtures,
or generated files you don't want reviewed.

Neither file is required. Without `.agentdiff/scope`, the out-of-scope rule
does not run.

---

## Why not just ask your AI to review the diff?

Because the answer changes every time, you cannot automate it, and the model
can be wrong in ways that are hard to catch. agentdiff produces the same output
for the same diff, every time, with no API key. It composes cleanly with
`grep`, `jq`, and CI pipelines. And because it is small enough to read in an
afternoon, you can decide whether to trust it.

---

## Prior art (and what's different)

agentdiff is not the only tool covering this space. Here is an honest map:

**Secrets detection**

- [gitleaks (gitleaks/gitleaks)](https://github.com/gitleaks/gitleaks) — 18k+
  stars. Scans git history, staged files, and diffs using 150+ regex rules plus
  entropy analysis. Runs as a pre-commit hook or in CI. Supports custom TOML
  rule files and allowlists. agentdiff's secret rule is a fast first pass; for
  comprehensive scanning, run gitleaks alongside.
- [TruffleHog (trufflesecurity/trufflehog)](https://github.com/trufflesecurity/trufflehog) —
  scans 800+ credential types with live provider verification (it actually tests
  whether an AWS key is still active). Covers git history, diffs, S3, Slack.
  agentdiff will miss secrets that TruffleHog catches.
- [detect-secrets (Yelp/detect-secrets)](https://github.com/Yelp/detect-secrets) —
  baseline-driven scanner with 27 detectors, regex + entropy + keyword. Scans
  staged files. agentdiff is not a replacement.

**Dependency review**

- [GitHub dependency-review action](https://github.com/actions/dependency-review-action) —
  scans lock-file diffs in pull requests, reports CVE severity, blocks merges.
  Requires an open GitHub pull request. agentdiff runs on the raw working tree
  before a PR exists, but provides no vulnerability data.
- [socket.dev](https://socket.dev) — supply-chain security: detects
  typosquatting, dependency confusion, compromised maintainers. agentdiff only
  reports the package name.
- [e18e/action-dependency-diff](https://github.com/e18e/action-dependency-diff) —
  trust levels, install size, duplicates, module replacements. PR-native.

**CI/CD change detection**

- [Elastic cicd-abuse-detector](https://github.com/elastic/cicd-abuse-detector) —
  uses regex plus Claude LLM to detect suspicious CI changes. LLM-based, not
  deterministic.

**Review frameworks**

- [Danger (danger.systems)](https://danger.systems) — scripting framework that
  runs a Dangerfile in CI. Every agentdiff rule could be a Dangerfile, but
  Danger provides no opinionated rules and requires a PR on GitHub/GitLab.
- [reviewdog (reviewdog/reviewdog)](https://github.com/reviewdog/reviewdog) —
  routes linter output to PR annotations. Infrastructure, not rules.
- [Semgrep (semgrep/semgrep)](https://semgrep.dev) — full SAST with diff-aware
  mode, not zero-config.

**Agent-specific tools (name collisions)**

Three repos already occupy the "agentdiff" name: sunilmallya/agentdiff (Claude
Code audit trail, LLM-based), agentdiff-ai/agentdiff (TypeScript agent
behavioral escalation detector, LLM-based), codeprakhar25/agentdiff (git-native
AI code attribution with ed25519 signing). None of these are this tool; none are
pip-installable as of this writing.

**What agentdiff does differently**

The one thing no surveyed tool does: the `scope` subcommand persists what the
agent was authorized to touch and flags deviations as MED findings. Existing
tools can detect what changed; none track whether it was authorized to change.
agentdiff also operates on the raw working tree (staged + unstaged + untracked)
before a commit or PR exists, with zero configuration and zero dependencies.

---

## Honest limits (v0.1)

- **The secrets rule is a narrow first pass.** It covers PEM private key
  blocks, AWS access key ID patterns, and high-entropy token assignments.
  It does not use entropy analysis over whole files, does not scan git history,
  and will miss many credential types that gitleaks or TruffleHog catch. The
  tradeoff is near-zero false positives; do not rely on this rule alone for
  secrets hygiene.

- **Scope globs use Python fnmatch, not gitignore semantics.** A glob like
  `src/**` matches files with a literal `src/` prefix in fnmatch but may
  not behave identically to gitignore patterns in all cases. Test your globs
  with `agentdiff review --scope YOUR_GLOB` on a known set of files.

- **Binary files are flagged but not inspected.** A new binary whose content
  looks benign (a PNG icon) gets the same MED finding as a compiled payload.
  Use `.agentdiff/ignore` to suppress expected binary additions.

- **No diff of diffs.** agentdiff compares the working tree against a ref. If
  the agent made a large refactor that deletes 200 lines and adds 200 different
  ones, it flags the deletion (>50 lines removed) but has no opinion about
  whether the replacement is equivalent.

- **The dependency rule reads added lines only.** It does not resolve package
  metadata, check for vulnerabilities, or detect transitive changes via lock
  files. All lock files are flagged as "lock file modified" without line-level
  parsing (too noisy to parse per-package). Use dependency-review-action for
  CVE context. For per-package change detection in lock files, use socket.dev
  or the GitHub dependency review action.

- **Cargo.toml target-specific dependency sections are best-effort.** The
  Cargo.toml parser is section-aware and correctly ignores `[package]`,
  `[profile.*]`, and `[features]` keys. Standard sections (`[dependencies]`,
  `[dev-dependencies]`, `[build-dependencies]`, `[workspace.dependencies]`) are
  well-covered. Complex `[target.'cfg(...)'.dependencies]` expressions may have
  edge cases in multi-hunk diffs where a section header is not visible as a
  context line.

- **`.agentdiff/ignore` bypass is detected, not prevented.** When an agent
  overwrites `.agentdiff/ignore` with `**`, the tool emits a MED finding for
  the ignore config change and exits 1 — it does not exit 0. However, other
  files in the same changeset that match `**` are still suppressed by the
  updated ignore list. The MED finding is the signal to investigate manually.

- **No Windows path support.** The tool assumes POSIX paths throughout.
  Contributions welcome.

---

## Installation

```bash
pip install agentdiff-cli
```

(The PyPI name is `agentdiff-cli` because `agent-diff` was already taken. The
command, the module, and the repo are all just `agentdiff`.)

Or run it straight from a checkout, no install needed — it is stdlib only:

```bash
git clone https://github.com/iselur/agentdiff
cd agentdiff && python3 -m agentdiff --help
```

Requires Python 3.9+, git in PATH. Zero third-party dependencies.

## Part of a small family

Five tools for working with coding agents, same house style: zero
dependencies, MIT, no API key, nothing leaves your machine. None of them
call a model — that is the point, since the thing being checked already is
one.

- [stillworks](https://github.com/iselur/stillworks) — record what your code does now, catch when it changes later
- [agentdiff](https://github.com/iselur/agentdiff) — see what the agent actually changed, before you merge  ← you are here
- [agentlog](https://github.com/iselur/agentlog) — what did your coding agent actually do today?
- [agentwatch](https://github.com/iselur/agentwatch) — tail what your agent is doing, right now
- [unedit](https://github.com/iselur/unedit) — a safety net for letting an agent loose on your files

One install gets all five, and `stillworks tools` says which ones you have:

```sh
pip install 'stillworks[all]'
stillworks tools
```

## License

MIT. Copyright (c) 2026 stillworks contributors.
