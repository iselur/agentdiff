# Example: reviewing an agent session in under a minute

This example creates a small repo, simulates an agent that touched files
outside its scope and added a dependency, then runs agentdiff to triage the
changes.

Copy-paste the commands in order. Everything runs locally; nothing touches
the network.

## Setup

```bash
# Create a throwaway repo
mkdir /tmp/agentdiff_example && cd /tmp/agentdiff_example
git init && git config user.email "you@example.com" && git config user.name "You"

# Commit a baseline
mkdir src tests
echo 'def process(x): return x' > src/main.py
echo 'from src.main import process' > tests/test_main.py
git add . && git commit -m "initial"
```

## Tell agentdiff what the agent was scoped to

```bash
agentdiff scope "src/**" "tests/**"
```

This writes `.agentdiff/scope` so subsequent `review` runs know what was
authorized.

## Simulate the agent

The agent was asked to improve `src/main.py` but it also added a `Dockerfile`
and a `requirements.txt`:

```bash
echo 'def process(x):
    # TODO: add input validation
    return x
' > src/main.py

echo "FROM python:3.11-slim" > Dockerfile
echo "requests==2.31.0" > requirements.txt
```

(Files are intentionally left untracked/unstaged to show that agentdiff
inspects the full working tree, not just staged changes.)

## Run the review

```bash
agentdiff review
```

Expected output:

```
HIGH (2)
  HIGH   Dockerfile  CI/release file modified: Dockerfile
  HIGH   requirements.txt:1  dependency added/changed: requests

MED (2)
  MED    Dockerfile  changed outside declared scope (src/**, tests/**)
  MED    requirements.txt  changed outside declared scope (src/**, tests/**)

LOW (1)
  LOW    src/main.py:2  TODO/FIXME added

5 finding(s): 2 HIGH, 2 MED, 1 LOW — review before merge
```

Exit code will be 1 (findings at gating severity).

## Try the machine-readable path

```bash
agentdiff review --json | python3 -c "
import json, sys
data = json.load(sys.stdin)
highs = [f for f in data['findings'] if f['severity'] == 'HIGH']
print(f\"{len(highs)} HIGH finding(s):\")
for f in highs:
    print(f\"  {f['file']}: {f['reason']}\")
"
```

## Suppress a known-good finding

Create `.agentdiff/ignore` to skip the Dockerfile (perhaps you authorized it
in a second pass):

```bash
echo "Dockerfile" > .agentdiff/ignore
agentdiff review
```

Now only the `requirements.txt` dependency and the TODO comment appear.

## Write a PR evidence document

```bash
agentdiff review --report review.md
cat review.md
```

`review.md` is a markdown summary ready to paste into a PR description or
GitHub issue comment.

## See every rule

```bash
agentdiff rules
```

This lists each rule, its severity, what it flags, and which specialist tool
covers the same domain more deeply.

## Clean up

```bash
cd /tmp && rm -rf agentdiff_example
```
