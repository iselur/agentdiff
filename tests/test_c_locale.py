"""What this tool does on a machine whose locale says ASCII.

A container with no locale set is the ordinary case, not the exotic one: it is
what CI runs on, what a Dockerfile without `ENV LANG` gives you, and what cron
hands a hook.  Python takes the locale at its word there — stdout encodes as
ASCII, and `text=True` decodes subprocess output as ASCII too.

So two different things break, and both raise rather than degrade.  Printing an
em dash — one of ours, in output that has nothing to do with the repo — dies
halfway through with a traceback and half a screen.  And reading `git`, whose
output is UTF-8 whatever the locale claims, dies on the first repo containing a
file named in anything but English.

Everything here runs the real command in a real subprocess with that
environment, because the codec is chosen when the process starts and cannot be
faked from inside one.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_repo, stage_file, write_file


def _ascii_env():
    """The environment of a container nobody gave a locale to."""
    env = dict(os.environ)
    env.update(LC_ALL="C", LANG="C", LANGUAGE="C",
               PYTHONCOERCECLOCALE="0",   # or Python quietly upgrades C to C.UTF-8
               PYTHONUTF8="0")            # or UTF-8 mode overrides the locale
    env.pop("PYTHONIOENCODING", None)
    return env


def _run(args, cwd):
    result = subprocess.run(
        [sys.executable, "-m", "agentdiff"] + args,
        cwd=cwd, capture_output=True, text=True, env=_ascii_env())
    return result.stdout, result.stderr, result.returncode


class TestAnAsciiTerminal(unittest.TestCase):

    def setUp(self):
        self.repo = make_repo({"src/app.py": "x = 1\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def assert_no_traceback(self, err, code):
        self.assertNotIn("Traceback", err, err)
        self.assertIn(code, (0, 1), err)

    def test_a_clean_repo_reviews_without_a_traceback(self):
        # Nothing about this repo is unusual.  The em dash is ours.
        _, err, code = _run(["review"], cwd=self.repo)
        self.assert_no_traceback(err, code)

    def test_a_repo_with_findings_reviews_without_a_traceback(self):
        write_file(self.repo, "requirements.txt", "requests==2.31.0\n")
        stage_file(self.repo, "requirements.txt")
        _, err, code = _run(["review"], cwd=self.repo)
        self.assert_no_traceback(err, code)

    def test_the_json_stays_json(self):
        write_file(self.repo, "requirements.txt", "requests==2.31.0\n")
        stage_file(self.repo, "requirements.txt")
        out, err, _ = _run(["review", "--json"], cwd=self.repo)
        self.assertNotIn("Traceback", err, err)
        json.loads(out)                 # raises if we mangled it

    def test_a_file_named_in_japanese_does_not_stop_the_review(self):
        # git speaks UTF-8 whatever the locale says; believing the locale here
        # means one file in a repo takes the whole tool down.
        write_file(self.repo, "設定/requirements.txt", "requests==2.31.0\n")
        stage_file(self.repo, "設定/requirements.txt")
        _, err, code = _run(["review"], cwd=self.repo)
        self.assert_no_traceback(err, code)

    def test_a_japanese_path_is_still_spelled_right_in_the_report(self):
        # Not crashing is the floor.  A reviewer has to be able to read which
        # file it is and paste it back into a command, so the name has to
        # arrive whole rather than as a row of question marks.
        write_file(self.repo, "設定/requirements.txt", "requests==2.31.0\n")
        stage_file(self.repo, "設定/requirements.txt")
        out, _, _ = _run(["review"], cwd=self.repo)
        self.assertIn("設定", out, out)

    def test_a_diff_containing_a_dash_does_not_stop_the_review(self):
        write_file(self.repo, "notes.py", "# a comment — with an em dash\n")
        stage_file(self.repo, "notes.py")
        _, err, code = _run(["review"], cwd=self.repo)
        self.assert_no_traceback(err, code)

    def test_a_japanese_path_survives_into_the_json(self):
        write_file(self.repo, "設定/requirements.txt", "requests==2.31.0\n")
        stage_file(self.repo, "設定/requirements.txt")
        out, err, _ = _run(["review", "--json"], cwd=self.repo)
        self.assertNotIn("Traceback", err, err)
        data = json.loads(out)
        paths = " ".join(f.get("file", "") for f in data.get("findings", []))
        self.assertIn("設定", paths, out)


if __name__ == "__main__":
    unittest.main()
