"""
agentdiff.cli — command-line interface.

Commands:
  agentdiff review   analyse working-tree changes
  agentdiff scope    persist intended scope globs
  agentdiff rules    print every rule and what it flags

Exit codes: 0 = clean, 1 = findings at gating severity, 2 = usage/error.
"""

import argparse
import json
import os
import sys

from . import __version__
from .git import find_repo_root, get_changes, GitError
from .rules import RULE_DOCS, SEVERITY, gating_findings, run_rules


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _agentdiff_dir(repo_root):
    return os.path.join(repo_root, ".agentdiff")


def _load_file_lines(path):
    """Read a config file and return non-blank, non-comment lines."""
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def _load_scope(repo_root):
    return _load_file_lines(os.path.join(_agentdiff_dir(repo_root), "scope"))


def _load_ignore(repo_root):
    return _load_file_lines(os.path.join(_agentdiff_dir(repo_root), "ignore"))


# ---------------------------------------------------------------------------
# Output: text
# ---------------------------------------------------------------------------

_SEV_PAD = {"HIGH": "HIGH ", "MED": "MED  ", "LOW": "LOW  "}


def _fmt_finding(f):
    loc = f"{f.file}:{f.line}" if f.line else f.file
    return f"  {_SEV_PAD.get(f.severity, f.severity)}  {loc}  {f.reason}"


def _print_review(findings, changes, strict, out=None):
    """Print human-readable review output. Returns the appropriate exit code."""
    if out is None:
        out = sys.stdout

    n_files = len(set(c.path for c in changes))
    gating = gating_findings(findings, strict=strict)

    if not findings:
        print(f"clean: {n_files} file(s) changed, nothing flagged", file=out)
        return 0

    # Group by severity and print most severe first
    by_sev = {s: [f for f in findings if f.severity == s] for s in SEVERITY}
    for sev in SEVERITY:
        group = by_sev[sev]
        if group:
            print(f"\n{sev} ({len(group)})", file=out)
            for f in group:
                print(_fmt_finding(f), file=out)

    counts = {s: len(by_sev[s]) for s in SEVERITY if by_sev[s]}
    count_str = ", ".join(f"{v} {k}" for k, v in counts.items())
    total = len(findings)

    if gating:
        print(f"\n{total} finding(s): {count_str} — review before merge", file=out)
        return 1
    else:
        print(f"\n{total} finding(s): {count_str} — LOW only, pass --strict to gate on LOW", file=out)
        return 0


# ---------------------------------------------------------------------------
# Output: JSON
# ---------------------------------------------------------------------------

def _print_review_json(findings, changes, strict):
    gating = gating_findings(findings, strict=strict)
    data = {
        "findings": [
            {
                "severity": f.severity,
                "file": f.file,
                "line": f.line,
                "reason": f.reason,
                "rule": f.rule,
            }
            for f in findings
        ],
        "files_changed": len(set(c.path for c in changes)),
        "clean": len(findings) == 0,
        "gate_triggered": len(gating) > 0,
        "counts": {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY},
    }
    print(json.dumps(data, indent=2))
    return 1 if gating else 0


# ---------------------------------------------------------------------------
# Output: markdown report
# ---------------------------------------------------------------------------

def _write_report(findings, changes, since_ref, report_path):
    n_files = len(set(c.path for c in changes))
    by_sev = {s: [f for f in findings if f.severity == s] for s in SEVERITY}

    lines = [
        "# agentdiff report",
        "",
        f"**ref:** `{since_ref}`  ",
        f"**files changed:** {n_files}  ",
        f"**total findings:** {len(findings)}",
        "",
    ]

    for sev in SEVERITY:
        group = by_sev[sev]
        if not group:
            continue
        lines += [f"## {sev} ({len(group)})", ""]
        for f in group:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- **{loc}** — {f.reason}")
        lines.append("")

    if not findings:
        lines += ["_Nothing flagged._", ""]

    with open(report_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _json_error(message):
    """Print a JSON error document to stdout and return 2."""
    print(json.dumps({
        "error": message,
        "findings": [],
        "files_changed": 0,
        "clean": False,
        "gate_triggered": False,
        "counts": {"HIGH": 0, "MED": 0, "LOW": 0},
    }))
    return 2


def cmd_review(args):
    """agentdiff review — analyse working-tree changes."""
    use_json = getattr(args, "json", False)
    try:
        repo_root = find_repo_root()
    except GitError as e:
        if use_json:
            return _json_error(str(e))
        print(f"error: {e}", file=sys.stderr)
        return 2

    since_ref = args.since or "HEAD"
    scope_globs = list(args.scope) if args.scope else _load_scope(repo_root)
    ignore_patterns = _load_ignore(repo_root)

    try:
        changes = get_changes(
            repo_root,
            since_ref=since_ref,
            staged_only=getattr(args, "staged_only", False),
        )
    except GitError as e:
        if use_json:
            return _json_error(str(e))
        print(f"error: {e}", file=sys.stderr)
        return 2

    findings = run_rules(changes, scope_globs=scope_globs, ignore_patterns=ignore_patterns)

    if getattr(args, "report", None):
        _write_report(findings, changes, since_ref, args.report)

    if use_json:
        return _print_review_json(findings, changes, args.strict)
    return _print_review(findings, changes, args.strict)


def cmd_scope(args):
    """agentdiff scope GLOB... — persist intended scope to .agentdiff/scope."""
    try:
        repo_root = find_repo_root()
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    d = _agentdiff_dir(repo_root)
    os.makedirs(d, exist_ok=True)
    scope_path = os.path.join(d, "scope")
    with open(scope_path, "w") as f:
        for g in args.globs:
            f.write(g + "\n")
    print(f"scope saved: {', '.join(args.globs)}")
    print(f"  stored in {scope_path}")
    return 0


def cmd_rules(args):
    """agentdiff rules — print every rule and what it flags."""
    print("Rules run by 'agentdiff review':\n")
    for sev, name, doc in RULE_DOCS:
        print(f"  {sev:<4}  {name}")
        # Wrap doc text at ~76 chars
        words = doc.split()
        line = "        "
        for word in words:
            if line.strip() and len(line) + len(word) + 1 > 80:
                print(line)
                line = "        " + word
            else:
                line += (" " if line.strip() else "") + word
        if line.strip():
            print(line)
        print()
    print("Note: LOW findings only affect the exit code under --strict.")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser():
    p = argparse.ArgumentParser(
        prog="agentdiff",
        description="See what the agent actually changed — before you merge.",
    )
    p.add_argument("--version", action="version", version=f"agentdiff {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # review
    rev = sub.add_parser("review", help="analyse working-tree changes against HEAD (or --since REF)")
    rev.add_argument(
        "--since", metavar="GIT_REF",
        help="compare against this ref instead of HEAD",
    )
    rev.add_argument(
        "--scope", metavar="GLOB", action="append",
        help="intended scope glob (repeatable); overrides .agentdiff/scope",
    )
    rev.add_argument("--json", action="store_true", help="machine-readable JSON output")
    rev.add_argument("--report", metavar="FILE", help="write markdown evidence document to FILE")
    rev.add_argument(
        "--strict", action="store_true",
        help="LOW findings also trigger exit 1",
    )
    rev.add_argument(
        "--staged-only", "--pre-commit",
        action="store_true", dest="staged_only",
        help="only staged changes (for use as a pre-commit hook)",
    )

    # scope
    sc = sub.add_parser("scope", help="persist intended scope globs to .agentdiff/scope")
    sc.add_argument("globs", nargs="+", metavar="GLOB")

    # rules
    sub.add_parser("rules", help="print every rule and what it flags")

    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "review":
        sys.exit(cmd_review(args))
    elif args.command == "scope":
        sys.exit(cmd_scope(args))
    elif args.command == "rules":
        sys.exit(cmd_rules(args))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
