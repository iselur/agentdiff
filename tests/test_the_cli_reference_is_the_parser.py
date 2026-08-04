"""The CLI reference in README.md, against the parser it describes.

    agentdiff review [--project DIR] [--since GIT_REF] [--scope GLOB]... [--json]
                     [--report FILE] [--strict] [--staged-only | --pre-commit]

    agentdiff scope GLOB...     # save the intended scope to .agentdiff/scope
    agentdiff rules             # print every rule and what it flags

The usage block and the parser had not drifted — that is the honest result,
and unlike unedit's command table there was no missing flag to find.  What was
missing was smaller and only visible once the two were listed side by side:
every flag in the block has a `**`--flag`**` paragraph under it explaining what
it does, except `--json`, which is discussed twice further down the file and
skipped in the one place a reader goes to look flags up.

So there are three bindings here, not one:

  * the commands in the block are the subcommands the parser builds
  * the flags in the block are the flags those subcommands take, both ways
  * every flag the block names is explained beneath it

The third is the one that failed.  It is also the one that stays true only by
somebody remembering, which is what makes it worth a test rather than a note.

`--project` is left off the `scope` and `rules` lines on purpose — its own
paragraph says "before or after the subcommand", covering all three — so it is
excluded by name, and then checked to really be on all three.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentdiff.cli import _build_parser  # noqa: E402

README = os.path.join(_ROOT, "README.md")

_FLAG = re.compile(r"(--[a-z][a-z-]*)")
# "agentdiff review [--project DIR] ..." and its wrapped continuation line.
_USAGE = re.compile(r"^agentdiff ([a-z]+)\b(.*)$")
# "**`--staged-only` / `--pre-commit`** Only inspect staged files."
_EXPLAINED = re.compile(r"^\*\*(`--[^*]+`)\*\*")

# Flags the block leaves off some lines on purpose, with the reason.
_EVERYWHERE = {
    "--project": "its paragraph says it works before or after the subcommand",
    "--help": "argparse adds it to every parser ever built",
}


def readme() -> str:
    with open(README, encoding="utf-8") as handle:
        return handle.read()


def reference(text):
    """The CLI reference section, from its heading to the next one."""
    start = text.find("## CLI reference")
    if start < 0:
        return ""
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


def block_commands(text):
    """{command: {flags}} as the usage block publishes it.

    A wrapped line continues the command above it — `review`'s flags do not
    stop being `review`'s because the line ran out.
    """
    section = reference(text)
    start = section.find("```")
    end = section.find("```", start + 3)
    commands, current = {}, None
    for line in section[start + 3:end].splitlines():
        found = _USAGE.match(line.strip())
        if found and not line.startswith(" "):
            current = found.group(1)
            commands.setdefault(current, set())
            commands[current].update(_FLAG.findall(found.group(2)))
        elif current and line.startswith(" "):
            commands[current].update(_FLAG.findall(line))
    return commands


def explained_flags(text):
    """Every flag with a paragraph of its own in the CLI reference."""
    flags = set()
    for line in reference(text).splitlines():
        found = _EXPLAINED.match(line)
        if found:
            flags.update(_FLAG.findall(found.group(1)))
    return flags


def parser_commands(keep_universal=False):
    """{command: [option, ...]} the parser accepts.

    An option is its set of spellings, not one string, so a block naming any
    one of them has named the option.
    """
    parser = _build_parser()
    commands = {}
    for action in parser._actions:
        choices = getattr(action, "choices", None) or {}
        if not choices or not hasattr(next(iter(choices.values())), "_actions"):
            continue
        for name, sub in choices.items():
            options = [frozenset(arg.option_strings) for arg in sub._actions
                       if arg.option_strings]
            if not keep_universal:
                options = [option for option in options
                           if not option & set(_EVERYWHERE)]
            commands[name] = options
    return commands


class TestTheCLIReferenceIsTheParser(unittest.TestCase):

    def setUp(self):
        self.text = readme()
        self.block = block_commands(self.text)
        self.parser = parser_commands()

    def test_the_readme_still_has_a_cli_reference(self):
        # Every comparison below is vacuous against an empty block.
        self.assertGreaterEqual(len(self.block), 3,
                                "no usage block found under ## CLI reference")

    def test_the_parser_still_has_subcommands(self):
        self.assertGreaterEqual(len(self.parser), 3,
                                "the parser introspection found nothing — the "
                                "comparisons below would pass against anything")

    def test_the_block_lists_the_commands_that_exist(self):
        self.assertEqual(sorted(self.block), sorted(self.parser))

    def test_every_flag_in_the_block_is_one_the_parser_takes(self):
        for command, flags in sorted(self.block.items()):
            options = self.parser.get(command, [])
            known = set().union(*options) if options else set()
            extra = flags - known - set(_EVERYWHERE)
            self.assertFalse(
                extra,
                "README.md offers `agentdiff {} {}` and the parser rejects it"
                .format(command, " ".join(sorted(extra))))

    def test_every_flag_the_parser_takes_is_one_the_block_lists(self):
        undocumented = []
        for command, options in sorted(self.parser.items()):
            listed = self.block.get(command, set())
            for option in options:
                if not option & listed:
                    undocumented.append("agentdiff {} {}"
                                        .format(command, sorted(option)[0]))
        self.assertFalse(
            undocumented,
            "these work and the CLI reference does not list them:\n  "
            + "\n  ".join(undocumented))

    def test_every_flag_in_the_block_is_explained_under_it(self):
        # The block says a flag exists; the paragraphs say what it does.  A
        # flag in the first and not the second is one a reader has to go and
        # find somewhere else in the file, which is what the reference is for.
        explained = explained_flags(self.text)
        self.assertGreaterEqual(len(explained), 4,
                                "no flag paragraphs found — the check below "
                                "would pass against an empty reference")
        named = set().union(*self.block.values()) if self.block else set()
        missing = sorted(named - explained)
        self.assertFalse(
            missing,
            "the CLI reference names {} in the usage block and never explains "
            "{}".format(", ".join(missing),
                        "them" if len(missing) > 1 else "it"))

    def test_the_universal_flag_really_is_universal(self):
        # It is left off two of the three lines because one paragraph covers
        # all three.  That is only true while all three still take it.
        for command, options in sorted(parser_commands(keep_universal=True).items()):
            spellings = set().union(*options) if options else set()
            self.assertIn(
                "--project", spellings,
                "`agentdiff {} --project` is not accepted, but its paragraph "
                "says the flag works on any subcommand".format(command))

    def test_json_does_not_change_the_exit_code(self):
        # The paragraph written for --json claims this, and a claim that
        # arrives with no test is the thing this whole file exists about.
        import json
        import subprocess
        import tempfile

        repo = tempfile.mkdtemp(prefix="ad-json-exit-")
        run = lambda *cmd: subprocess.run(cmd, cwd=repo, capture_output=True,
                                          text=True)
        run("git", "init", "-q", ".")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        with open(os.path.join(repo, "a.py"), "w") as handle:
            handle.write("x = 1\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")
        with open(os.path.join(repo, "a.py"), "a") as handle:
            handle.write('AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n')

        def review(*argv):
            return subprocess.run(
                [sys.executable, "-m", "agentdiff", "review", *argv],
                cwd=repo, capture_output=True, text=True,
                env=dict(os.environ, PYTHONPATH=_ROOT))

        plain, structured = review(), review("--json")
        self.assertEqual(plain.returncode, structured.returncode,
                         "--json changed the exit code, and its paragraph says "
                         "a script can gate on the code either way")
        self.assertEqual(plain.returncode, 1,
                         "the fixture stopped producing a gating finding, so "
                         "the comparison above is 0 == 0")
        json.loads(structured.stdout)  # and it really is one object

    def test_the_universal_flag_is_still_explained(self):
        self.assertIn("--project", explained_flags(self.text),
                      "the CLI reference no longer explains --project, and two "
                      "of its three usage lines leave it out because {}"
                      .format(_EVERYWHERE["--project"]))


if __name__ == "__main__":
    unittest.main()
