"""`--json` prints one shape, whether the review ran or not.

The README's offer is that the exit code gates and the object carries the
detail, so a script can do both.  That only holds if the object is the same
object either way, and it was not: a run that finished printed seven keys, a
run that could not start printed five.  The two missing ones were `reviewed`
and `unread` -- which is to say the two a script reads to find out what the
review did *not* cover.  A CI step doing

    data = json.loads(out)
    if data["reviewed"] == 0: ...

raised a KeyError on the day git failed, and only on that day: green through
every test, every dry run, every normal afternoon.  The error path is the one
path a script's error handling is written against, so it is the worst one to
be a different shape.

So the document has one builder now, and this file says so from the outside --
by running the command both ways and comparing the keys it printed.  A key
added to the good path and forgotten on the bad one fails here rather than in
somebody's pipeline.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Case(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentdiff_json_shape_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "app")
        os.makedirs(self.repo)
        self._git("init", "-q", ".")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        self._write("a.py", "value = 1\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "initial")
        self._write("a.py", "value = 2\n")

    def _git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.repo,
                              capture_output=True, text=True, timeout=60)

    def _write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_agentdiff(self, *args):
        env = dict(os.environ, PYTHONPATH=_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "agentdiff"] + list(args),
            capture_output=True, text=True, encoding="utf-8",
            cwd=self.tmp, env=env, timeout=60)

    def finished(self):
        """A review that ran: the document as it is meant to look."""
        proc = self.run_agentdiff("review", "--json", "--project", self.repo)
        return json.loads(proc.stdout), proc

    def could_not_start(self):
        """A review that never got going.  A directory that is not there is the
        plainest way in, and it is also the mistake people actually make."""
        proc = self.run_agentdiff("review", "--json",
                                  "--project", os.path.join(self.tmp, "nope"))
        return json.loads(proc.stdout), proc


class TestTheShapeIsTheSameEitherWay(Case):

    def test_the_failure_carries_every_key_the_success_carries(self):
        good, _ = self.finished()
        bad, _ = self.could_not_start()
        self.assertEqual(
            sorted(set(good) - set(bad)), [],
            "`--json` drops these keys when the review cannot run, so a script "
            "that reads them breaks on exactly the runs it was written for")

    def test_the_only_extra_key_on_failure_is_the_reason(self):
        # Extra keys are as much of a shape change as missing ones -- a script
        # that switches on the document has to be told which one it has, and
        # `error` is that flag.  Anything else appearing here is drift.
        good, _ = self.finished()
        bad, _ = self.could_not_start()
        self.assertEqual(sorted(set(bad) - set(good)), ["error"])

    def test_the_reason_says_what_was_wrong(self):
        bad, _ = self.could_not_start()
        self.assertIn("nope", bad["error"])


class TestAFailedRunIsNotACleanOne(Case):

    def test_it_does_not_claim_to_be_clean(self):
        # The trap this key exists to avoid.  Nothing was flagged, because
        # nothing was looked at, and `clean` is the field a merge gates on.
        bad, _ = self.could_not_start()
        self.assertFalse(bad["clean"])

    def test_it_counts_nothing_rather_than_guessing(self):
        bad, _ = self.could_not_start()
        self.assertEqual(bad["files_changed"], 0)
        self.assertEqual(bad["reviewed"], 0)
        self.assertEqual(bad["findings"], [])
        self.assertEqual(bad["unread"], [])
        self.assertFalse(bad["gate_triggered"])

    def test_the_exit_code_is_the_usage_error_one(self):
        _, proc = self.could_not_start()
        self.assertEqual(proc.returncode, 2)

    def test_nothing_goes_to_stderr_when_json_was_asked_for(self):
        # The point of `--json` is one machine-readable thing in one place.  A
        # message on stderr as well would be the same failure said twice, in
        # two formats, and a wrapper that logs stderr would report it twice.
        _, proc = self.could_not_start()
        self.assertEqual(proc.stderr, "")


class TestWithoutJsonItSaysWhoFailed(Case):

    def test_the_message_begins_with_the_command_name(self):
        # Four of the five commands in this family name themselves when they
        # fail.  They are five commands out of one install, so a line that
        # begins `error:` does not say which of them is talking.
        proc = self.run_agentdiff("review",
                                  "--project", os.path.join(self.tmp, "nope"))
        self.assertTrue(proc.stderr.startswith("agentdiff: "),
                        "stderr was {!r}".format(proc.stderr))

    def test_it_goes_to_stderr_and_stdout_stays_empty(self):
        proc = self.run_agentdiff("review",
                                  "--project", os.path.join(self.tmp, "nope"))
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.returncode, 2)

    def test_the_other_commands_say_it_the_same_way(self):
        # `scope` has no `--json` at all, which is exactly why it is worth
        # checking: the name in front is not a property of the JSON path.
        proc = self.run_agentdiff("scope", "src/**",
                                  "--project", os.path.join(self.tmp, "nope"))
        self.assertTrue(proc.stderr.startswith("agentdiff: "),
                        "stderr was {!r}".format(proc.stderr))


if __name__ == "__main__":
    unittest.main()
