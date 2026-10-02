"""Synthetic credentials only. Never use the user's live credential file."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import request_secret as secret

SCRIPT = Path(secret.__file__).resolve()
DUMMY = "dummy-credential-'quoted'-$()=`literal`-with spaces-ß"


class PrivateEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "credentials.env"

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=15)

    def form(self, timeout=30):
        form = secret.FormRequest(["TEST_KEY"], self.path, secret.read_private(self.path), timeout)
        form.start()
        self.addCleanup(form.close)
        return form

    def post(self, form, values=None, route="save", **headers):
        defaults = {"Origin": form.origin, "X-Request-Token": form.token,
                    "Content-Type": "application/json"}
        defaults.update(headers)
        body = json.dumps({"values": {"TEST_KEY": DUMMY} if values is None else values}).encode()
        request = urllib.request.Request(form.origin + "/" + route, data=body, headers=defaults)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, response.read().decode()

    def test_http_save_is_private_and_replay_rejected(self):
        form = self.form()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code, body = self.post(form)
        self.assertEqual(code, 200)
        self.assertTrue(form.done.wait(1))
        self.assertEqual(secret.env_values(self.path.read_text()), {"TEST_KEY": DUMMY})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertNotIn(DUMMY, body + output.getvalue())
        self.assertEqual(self.post(form)[0], 410)

    def test_form_has_no_token_or_value_in_html(self):
        form = self.form()
        with urllib.request.urlopen(form.origin) as response:
            body = response.read().decode()
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn('type="password"', body)
        self.assertNotIn(form.token, body)
        self.assertNotIn(DUMMY, body)
        self.assertEqual(form.server.server_address[0], "127.0.0.1")

    def test_cross_origin_wrong_token_and_host_rejected(self):
        form = self.form()
        for headers in ({"Origin": "https://evil.example"}, {"X-Request-Token": "wrong"},
                        {"Origin": "null"}, {"Host": "evil.example"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(form, **headers)[0], 403)
        self.assertFalse(self.path.exists())

    def test_invalid_values_are_not_echoed_or_written(self):
        form = self.form()
        for values in ({"TEST_KEY": ""}, {"TEST_KEY": DUMMY + "\n"}, {"TEST_KEY": "\x00"},
                       {"TEST_KEY": 42}, {"OTHER_KEY": DUMMY}, {"TEST_KEY": "x" * 65537}):
            with self.subTest(kind=type(next(iter(values.values()))).__name__):
                code, body = self.post(form, values)
                self.assertEqual(code, 400)
                self.assertNotIn(DUMMY, body)
        self.assertFalse(self.path.exists())
        self.assertFalse(form.done.is_set())

    def test_cancel_saves_nothing(self):
        self.path.write_text("EXISTING=keep\n")
        form = self.form()
        self.assertEqual(self.post(form, {}, route="cancel")[0], 200)
        self.assertEqual(form.status, "cancelled")
        self.assertEqual(self.path.read_text(), "EXISTING=keep\n")

    def test_expired_form_cannot_save(self):
        form = self.form(timeout=-1)
        self.assertEqual(self.post(form)[0], 410)
        self.assertFalse(self.path.exists())

    def test_changed_destination_reports_conflict_without_overwrite(self):
        form = self.form()
        self.path.write_text("OTHER=edited\n")
        code, body = self.post(form)
        self.assertEqual(code, 409)
        self.assertEqual(form.status, "error")
        self.assertNotIn(DUMMY, body)
        self.assertEqual(self.path.read_text(), "OTHER=edited\n")

    def test_preserve_comments_other_values_and_quotes(self):
        original = "# hello\nKEEP='two words'\nexport TEST_KEY=old\nTEST_KEY=duplicate\nTAIL=last"
        self.path.write_text(original)
        secret.save(self.path, original, {"TEST_KEY": DUMMY, "NEW": " literal $HOME # \\ "})
        text = self.path.read_text()
        self.assertIn("# hello\nKEEP='two words'\n", text)
        self.assertEqual(text.count("TEST_KEY="), 1)
        self.assertEqual(secret.env_values(text), {"KEEP": "two words", "TEST_KEY": DUMMY,
                                                "TAIL": "last", "NEW": " literal $HOME # \\ "})
        self.assertFalse(list(self.root.glob(".request-secret-*")))

    def test_literal_hash_and_shell_syntax_are_not_interpreted(self):
        text = "A=abc#def\nB='a # b' # comment\nC= # empty\nD='$(touch NEVER_CREATED)'\n"
        self.assertEqual(secret.env_values(text), {"A": "abc#def", "B": "a # b", "C": "",
                                                 "D": "$(touch NEVER_CREATED)"})
        for value in ("line\u2028break", "line\x85break"):
            with self.assertRaises(secret.SafeError):
                secret.validate_values(["KEY"], {"KEY": value})

    def test_symlink_hardlink_and_fifo_refused(self):
        real = self.root / "real"
        real.write_text("OTHER=keep\n")
        self.path.symlink_to(real)
        with self.assertRaises(secret.SafeError):
            secret.read_private(self.path)
        self.path.unlink()
        os.link(real, self.path)
        with self.assertRaises(secret.SafeError):
            secret.read_private(self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(secret.SafeError):
            secret.read_private(self.path)

    def test_git_unignored_and_nested_tracked_targets_refused(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        with self.assertRaises(secret.SafeError):
            secret.check_git(self.path)
        nested = self.root / "nested"
        nested.mkdir()
        path = nested / ".env.local"
        (self.root / ".gitignore").write_text(".env.local\n")
        secret.save(path, None, {"TEST_KEY": DUMMY})
        self.assertEqual(secret.env_values(path.read_text())["TEST_KEY"], DUMMY)
        subprocess.run(["git", "-C", str(self.root), "add", "-f", "nested/.env.local"], check=True)
        with self.assertRaises(secret.SafeError):
            secret.check_git(path)
        self.assertFalse(list((self.root / ".git").glob(".request-secret-*")))

    def test_list_returns_only_names(self):
        secret.save(self.path, None, {"TEST_KEY": DUMMY})
        result = self.cli("list", "--file", str(self.path))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["names"], ["TEST_KEY"])
        self.assertNotIn(DUMMY, result.stdout + result.stderr)

    def test_runner_suppresses_both_streams_and_preserves_exit_code(self):
        secret.save(self.path, None, {"TEST_KEY": DUMMY})
        command = "import os,sys;print(os.environ['TEST_KEY']);print(os.environ['TEST_KEY'],file=sys.stderr);sys.exit(7)"
        result = self.cli("run", "--file", str(self.path), "--", sys.executable, "-c", command)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(json.loads(result.stdout), {"status": "exited", "exit_code": 7})
        self.assertEqual(result.stderr, "")
        self.assertNotIn(DUMMY, result.stdout)

    def test_runner_redacts_split_writes_on_both_streams(self):
        secret.save(self.path, None, {"TEST_KEY": DUMMY})
        command = ("import os,time;v=os.environ['TEST_KEY'].encode();os.write(1,b'normal\\n'+v[:10]);"
                   "time.sleep(.03);os.write(1,v[10:]+b'\\n');os.write(2,v+b'\\n')")
        result = self.cli("run", "--file", str(self.path), "--redact-output", "--",
                          sys.executable, "-c", command)
        self.assertEqual(result.returncode, 0)
        self.assertIn("normal\n[REDACTED]\n[REDACTED]\n", result.stdout)
        self.assertNotIn(DUMMY, result.stdout + result.stderr)

    def test_cli_browser_receipts_never_contain_values(self):
        process = subprocess.Popen([sys.executable, str(SCRIPT), "request", "--file", str(self.path),
                                    "--ui", "browser", "--no-open", "--timeout", "5", "TEST_KEY"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            waiting_line = process.stdout.readline()
            waiting = json.loads(waiting_line)
            origin, token = waiting["url"].split("/#")
            body = json.dumps({"values": {"TEST_KEY": DUMMY}}).encode()
            request = urllib.request.Request(origin + "/save", data=body,
                        headers={"Origin": origin, "X-Request-Token": token, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()
            output, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(json.loads(output)["status"], "saved")
            self.assertNotIn(DUMMY, waiting_line + output + error)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_cli_timeout_leaves_no_file(self):
        result = self.cli("request", "--file", str(self.path), "--ui", "browser",
                          "--no-open", "--timeout", "1", "TEST_KEY")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])["status"], "expired")
        self.assertFalse(self.path.exists())

    def test_native_values_are_captured_with_whitespace_preserved(self):
        reply = subprocess.CompletedProcess([], 0, "  " + DUMMY + "  \n", "")
        with patch.object(secret.subprocess, "run", return_value=reply) as run:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                values = secret.native_values(["TEST_KEY"], self.path, 10)
        self.assertEqual(values, {"TEST_KEY": "  " + DUMMY + "  "})
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertNotIn(DUMMY, str(run.call_args.args))

    def test_native_cancel_and_errors_never_forward_subprocess_output(self):
        for stderr, exception in (("User cancelled. (-128)", secret.Cancelled),
                                  (DUMMY, secret.SafeError)):
            with patch.object(secret.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, DUMMY, stderr)):
                with self.assertRaises(exception) as raised:
                    secret.native_values(["TEST_KEY"], self.path, 10)
                self.assertNotIn(DUMMY, str(raised.exception))

    def test_multiline_existing_file_and_bad_names_fail_before_ui(self):
        self.path.write_text("TEST_KEY='line one\nline two'\n")
        result = self.cli("request", "--file", str(self.path), "TEST_KEY")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("waiting", result.stdout)
        self.path.unlink()
        result = self.cli("request", "--file", str(self.path), "bad-name")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("waiting", result.stdout)


if __name__ == "__main__":
    unittest.main()
