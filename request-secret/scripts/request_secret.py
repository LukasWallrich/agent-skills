#!/usr/bin/env python3
"""Private credential entry. Never return secret values to the calling agent."""
from __future__ import annotations

import argparse
import fcntl
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ENTRY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
MAX_FILE = 1024 * 1024
MAX_VALUE = 65536


class SafeError(Exception):
    """Only constant, non-secret messages may be exposed."""


class Cancelled(Exception):
    pass


def receipt(status, **metadata):
    print(json.dumps({"status": status, **metadata}), flush=True)


def target_path(raw):
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SafeError("Use an absolute destination path.")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise SafeError("The destination parent must be an existing directory.")
    return parent / path.name


def check_git(path):
    git_env = dict(os.environ, LC_ALL="C")
    def git(*args):
        return subprocess.run(["git", "-C", str(path.parent), *args],
                              env=git_env, capture_output=True, timeout=10)
    try:
        root = git("rev-parse", "--show-toplevel")
        if root.returncode:
            if root.returncode != 128 or b"not a git repository" not in root.stderr:
                raise SafeError("Could not establish Git status for the destination.")
            return
        root_path = Path(os.fsdecode(root.stdout).strip()).resolve()
        relative = path.relative_to(root_path).as_posix()
        tracked = subprocess.run(["git", "-C", str(root_path), "--literal-pathspecs",
                                  "ls-files", "--error-unmatch", "--", relative],
                                 env=git_env, capture_output=True, timeout=10)
        if tracked.returncode == 0:
            raise SafeError("Destination is tracked by Git. Choose an untracked private file.")
        ignored = git("check-ignore", "--no-index", "--quiet", "--", str(path))
        if tracked.returncode != 1 or ignored.returncode != 0:
            raise SafeError("Destination must be Git-ignored, or outside the repository.")
        git_dir = git("rev-parse", "--absolute-git-dir")
        if git_dir.returncode:
            raise SafeError("Could not locate Git's private metadata directory.")
        return Path(os.fsdecode(git_dir.stdout).strip())
    except FileNotFoundError:
        raise SafeError("Git is required to check that the destination is private.") from None


def read_private(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise SafeError("Cannot read destination; symlinks are refused.") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise SafeError("Destination must be a regular file owned by you, with no hard links.")
        data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise SafeError("Credential file exceeds the size limit.")
        try:
            return data.decode("utf-8")
        except UnicodeError:
            raise SafeError("Credential file must be UTF-8 text.") from None


def validate_values(names, values):
    if not isinstance(values, dict) or set(values) != set(names):
        raise SafeError("Supply exactly the requested variable names.")
    for value in values.values():
        if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_VALUE
                or any(c in value for c in "\x00\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")):
            raise SafeError("Values must be non-empty, single-line text within the size limit.")


def updated_text(original, values):
    lines = []
    written = set()
    for line in (original or "").splitlines(keepends=True):
        entry = ENTRY.match(line.rstrip("\r\n"))
        if entry and entry[1] in values:
            name = entry[1]
            if name not in written:
                lines.append(f"{name}={shlex.quote(values[name])}\n")
                written.add(name)
        else:
            lines.append(line)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for name, value in values.items():
        if name not in written:
            lines.append(f"{name}={shlex.quote(value)}\n")
    return "".join(lines)


def save(path, original, values):
    temporary_dir = check_git(path) or path.parent
    validate_values(list(values), values)
    content = updated_text(original, values)
    if len(content.encode("utf-8")) > MAX_FILE:
        raise SafeError("Updated credential file exceeds the size limit.")
    if temporary_dir.stat().st_dev != path.parent.stat().st_dev:
        raise SafeError("Atomic save requires Git metadata and destination on the same filesystem; choose a private file outside Git.")
    # Keep the stable lock inode so concurrent helpers share one lock.
    lock_path = path.parent / ("." + path.name + ".request-secret.lock")
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError:
        raise SafeError("Cannot create the private write lock.") from None
    with os.fdopen(fd, "r+b") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise SafeError("Write lock must be a regular file owned by you.")
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if read_private(path) != original:
            raise SafeError("Destination changed during entry. Nothing saved; request again.")
        temporary = None
        try:
            # In repositories, plaintext staging stays in .git, outside Git's index.
            fd, temporary = tempfile.mkstemp(prefix=".request-secret-", dir=temporary_dir)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


APPLESCRIPT = '''on run argv
  set keyName to item 1 of argv
  set destination to item 2 of argv
  set waitSeconds to (item 3 of argv) as integer
  set deadline to (current date) + waitSeconds
  set secretValue to ""
  set revealValue to false
  with timeout of (waitSeconds + 1) seconds
    repeat
      set remainingSeconds to (deadline - (current date)) as integer
      if remainingSeconds < 1 then error "Cancelled" number -128
      set promptText to "Enter " & keyName & return & return & "Save to: " & destination & return & "This value stays outside the chat."
      if revealValue then
        set answer to display dialog promptText with title "Private credential entry" default answer secretValue without hidden answer buttons {"Cancel", "Hide", "Save"} default button "Save" cancel button "Cancel" giving up after remainingSeconds
      else
        set answer to display dialog promptText with title "Private credential entry" default answer secretValue with hidden answer buttons {"Cancel", "Show", "Save"} default button "Save" cancel button "Cancel" giving up after remainingSeconds
      end if
      if gave up of answer then error "Cancelled" number -128
      set secretValue to text returned of answer
      if button returned of answer is "Save" then return secretValue
      set revealValue to not revealValue
    end repeat
  end timeout
end run
'''


def native_values(names, path, timeout):
    if len(names) != 1:
        raise SafeError("Native entry accepts one value; use the browser form for multiple values.")
    deadline = time.monotonic() + timeout
    values = {}
    for name in names:
        remaining = max(1, int(deadline - time.monotonic()))
        if time.monotonic() >= deadline:
            raise Cancelled()
        try:
            result = subprocess.run(["/usr/bin/osascript", "-", name, str(path), str(remaining)],
                                    input=APPLESCRIPT, capture_output=True, text=True,
                                    timeout=remaining + 2)
        except subprocess.TimeoutExpired:
            raise Cancelled() from None
        if result.returncode:
            if "(-128)" in result.stderr or "(-1712)" in result.stderr:
                raise Cancelled()
            raise SafeError("Native dialog unavailable; try --ui browser.")
        values[name] = result.stdout.removesuffix("\n")
    validate_values(names, values)
    return values


def form_html(names, path, nonce):
    fields = "".join(f'<label for="{name}">{name}</label><input id="{name}" '
                     f'name="{name}" type="password" autocomplete="off" required '
                     'spellcheck="false" autocapitalize="none">' for name in names)
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Private credential entry</title><style nonce="{nonce}">
:root{{color-scheme:light dark;font:17px system-ui}}body{{max-width:560px;margin:8vh auto;padding:24px}}
h1{{font-size:28px}}p{{line-height:1.5}}code{{overflow-wrap:anywhere}}label{{display:block;margin:24px 0 8px}}
input{{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #888;border-radius:6px}}
button{{padding:12px 20px;font:inherit;cursor:pointer;margin:24px 12px 0 0;border-radius:6px}}
#message{{min-height:1.5em}}.muted{{opacity:.7}}</style>
<h1>Private credential entry</h1><p>Save directly to <code>{html.escape(str(path))}</code>.</p>
<p class="muted">Values go to the local helper, outside the agent's conversation. The file is plaintext, readable by your user account.</p>
<form autocomplete="off">{fields}<button type="button" id="reveal" aria-controls="{' '.join(names)}" aria-pressed="false">Show values</button>
<button type="submit">Save credentials</button>
<button type="button" id="cancel">Cancel</button></form><p id="message" role="status"></p>
<script nonce="{nonce}">
const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);
const form=document.querySelector('form'),message=document.querySelector('#message'),reveal=document.querySelector('#reveal');
function setVisibility(visible){{
  form.querySelectorAll('input').forEach(input=>input.type=visible?'text':'password');
  reveal.textContent=visible?'Hide values':'Show values';
  reveal.setAttribute('aria-pressed',String(visible));
}}
reveal.addEventListener('click',()=>setVisibility(reveal.getAttribute('aria-pressed')!=='true'));
async function submit(action,values){{
  form.querySelectorAll('input').forEach(input=>input.value='');
  setVisibility(false);
  form.querySelectorAll('button').forEach(button=>button.disabled=true);
  try{{const response=await fetch('/'+action,{{method:'POST',headers:{{'Content-Type':'application/json','X-Request-Token':token}},body:JSON.stringify({{values}})}});
    const result=await response.json();values=null;message.textContent=result.message;
    if(response.ok){{
      form.remove();
      if(action==='save'){{
        message.textContent='Saved. Closing this tab…';
        setTimeout(()=>message.textContent='Saved. You can close this tab.',250);
        try{{window.close();}}catch{{message.textContent='Saved. You can close this tab.';}}
      }}
    }}else{{form.querySelectorAll('button').forEach(button=>button.disabled=false);}}
  }}catch{{values=null;message.textContent='Request unavailable or expired. Check the agent receipt before retrying.';}}
}}
form.addEventListener('submit',event=>{{event.preventDefault();const values={{}};
  form.querySelectorAll('input').forEach(input=>values[input.name]=input.value);submit('save',values);}});
document.querySelector('#cancel').addEventListener('click',()=>submit('cancel',{{}}));
</script></html>'''


class FormServer(ThreadingHTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        pass  # Never emit a traceback containing request/body data.


def tailnet_address():
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                                text=True, timeout=10)
        address = ipaddress.IPv4Address(result.stdout.strip())
        if result.returncode or address not in ipaddress.IPv4Network("100.64.0.0/10"):
            raise ValueError()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise SafeError("Tailnet entry requires a running Tailscale connection with an IPv4 address.") from None
    return str(address)


class FormRequest:
    def __init__(self, names, path, original, timeout, bind_address="127.0.0.1"):
        self.names, self.path, self.original = names, path, original
        self.token, self.nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(18)
        self.deadline = time.monotonic() + timeout
        self.done, self.mutex = threading.Event(), threading.Lock()
        self.status = "waiting"
        self.error = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def respond(self, code, data, content_type="application/json"):
                payload = data.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; "
                                 f"script-src 'nonce-{owner.nonce}'; style-src 'nonce-{owner.nonce}'; "
                                 "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(payload)

            def valid_host(self):
                return self.headers.get("Host") == owner.host

            def do_GET(self):
                if not self.valid_host():
                    self.respond(403, '{"message":"Invalid host."}')
                elif self.path != "/":
                    self.respond(404, '{"message":"Not found."}')
                elif owner.done.is_set() or time.monotonic() >= owner.deadline:
                    self.respond(410, '{"message":"Request expired or completed."}')
                else:
                    self.respond(200, form_html(owner.names, owner.path, owner.nonce), "text/html")

            def do_POST(self):
                if (not self.valid_host() or self.headers.get("Origin") != owner.origin
                        or not secrets.compare_digest(self.headers.get("X-Request-Token", ""), owner.token)):
                    self.respond(403, '{"message":"Request not authorized."}')
                    return
                if self.path not in ("/save", "/cancel"):
                    self.respond(404, '{"message":"Not found."}')
                    return
                with owner.mutex:
                    if owner.done.is_set() or time.monotonic() >= owner.deadline:
                        self.respond(410, '{"message":"Request expired or completed."}')
                        return
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if (not 0 < length <= MAX_FILE or self.headers.get("Transfer-Encoding")
                                or self.headers.get("Content-Type") != "application/json"):
                            raise SafeError("Invalid submission.")
                        payload = json.loads(self.rfile.read(length))
                        if not isinstance(payload, dict):
                            raise SafeError("Invalid submission.")
                        if self.path == "/cancel":
                            owner.status = "cancelled"
                        else:
                            values = payload.get("values")
                            validate_values(owner.names, values)
                            try:
                                save(owner.path, owner.original, values)
                            except SafeError as error:
                                owner.status, owner.error = "error", str(error)
                                owner.done.set()
                                self.respond(409, '{"message":"Destination unavailable or changed. Nothing saved; see the agent receipt."}')
                                return
                            owner.status = "saved"
                        owner.done.set()
                        self.respond(200, json.dumps({"message": "Saved. You can close this page."
                                                      if owner.status == "saved" else "Cancelled. Nothing saved."}))
                    except (ValueError, UnicodeError, SafeError, OSError):
                        self.respond(400, '{"message":"Could not save. Check the destination and supply non-empty single-line values."}')

        self.server = FormServer((bind_address, 0), Handler)
        self.host = f"{bind_address}:{self.server.server_port}"
        self.origin = "http://" + self.host
        self.url = self.origin + "/#" + self.token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self.url

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def assignment_value(raw):
    # A # inside a quoted value or mid-token is literal; only an unquoted,
    # whitespace-separated # starts a comment. shlex's comments=True differs.
    quote = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None and (index == 0 or raw[index - 1].isspace()):
            raw = raw[:index]
            break
        index += 1
    try:
        parts = shlex.split(raw, comments=False, posix=True)
    except ValueError:
        raise SafeError("Unsupported env-file quoting.") from None
    if len(parts) > 1:
        raise SafeError("Env values containing spaces must be quoted.")
    return parts[0] if parts else ""


def env_values(text):
    values = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        entry = ENTRY.match(line)
        if not entry:
            raise SafeError("Unsupported env-file syntax; only single-line assignments are supported.")
        values[entry[1]] = assignment_value(entry[2])
    return values


def run_command(path, command, redact):
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SafeError("Supply a command after --.")
    values = env_values(read_private(path))
    if not values:
        raise SafeError("No credentials are available in the destination.")
    env = dict(os.environ)
    env.update(values)
    if not redact:
        result = subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode
    patterns = sorted({v.encode() for v in values.values() if v}, key=len, reverse=True)
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pending = b""
    with process.stdout as output:
        while True:
            chunk = output.read1(8192)
            pending += chunk
            while pending:
                match = next((p for p in patterns if pending.startswith(p)), None)
                if match:
                    sys.stdout.buffer.write(b"[REDACTED]")
                    pending = pending[len(match):]
                elif chunk and any(p.startswith(pending) for p in patterns):
                    break
                else:
                    sys.stdout.buffer.write(pending[:1])
                    pending = pending[1:]
            sys.stdout.buffer.flush()
            if not chunk:
                break
    return process.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    for action in ("request", "list", "run"):
        sub = subs.add_parser(action)
        sub.add_argument("--file", required=True)
        if action == "request":
            sub.add_argument("names", nargs="+", help="Variable names to collect and save together (up to 16)")
            sub.add_argument("--ui", choices=("auto", "native", "browser"), default="auto")
            sub.add_argument("--no-open", action="store_true")
            sub.add_argument("--tailnet", action="store_true",
                             help="Serve the browser form on this machine's Tailscale IPv4 address")
            sub.add_argument("--timeout", type=int, default=600)
        elif action == "run":
            sub.add_argument("--redact-output", action="store_true")
            sub.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        path = target_path(args.file)
        if args.action == "list":
            text = read_private(path)
            names = sorted({m[1] for line in (text or "").splitlines() if (m := ENTRY.match(line))})
            receipt("present" if text is not None else "missing", file=str(path), names=names)
            return 0
        if args.action == "run":
            code = run_command(path, args.command, args.redact_output)
            receipt("exited", exit_code=code)
            return code if 0 <= code <= 255 else 1
        if (not all(NAME.fullmatch(name) for name in args.names)
                or any(len(name) > 128 for name in args.names)
                or len(set(args.names)) != len(args.names) or len(args.names) > 16):
            raise SafeError("Supply up to 16 distinct valid environment-variable names.")
        if not 1 <= args.timeout <= 3600:
            raise SafeError("Timeout must be between 1 and 3600 seconds.")
        if args.tailnet and args.ui == "native":
            raise SafeError("Tailnet entry uses the browser form; omit --ui native.")
        if args.ui == "native" and len(args.names) != 1:
            raise SafeError("Native entry accepts one value; use the browser form for multiple values.")
        check_git(path)
        original = read_private(path)
        env_values(original)  # Reject unsupported multiline/shell syntax before entry.
        ui = ("browser" if args.tailnet else
              args.ui if args.ui != "auto" else
              "native" if sys.platform == "darwin" and len(args.names) == 1 else "browser")
        if ui == "native":
            if sys.platform != "darwin":
                raise SafeError("Native input requires macOS. Use --ui browser.")
            receipt("waiting", file=str(path), names=args.names, ui=ui)
            values = native_values(args.names, path, args.timeout)
            save(path, original, values)
        else:
            address = tailnet_address() if args.tailnet else "127.0.0.1"
            request = FormRequest(args.names, path, original, args.timeout, bind_address=address)
            try:
                url = request.start()
                receipt("waiting", file=str(path), names=args.names, ui=ui, url=url,
                        timeout_seconds=args.timeout)
                if not args.no_open and not args.tailnet:
                    try:
                        webbrowser.open(url)
                    except Exception:
                        pass
                if not request.done.wait(args.timeout):
                    with request.mutex:
                        if request.status != "saved":
                            request.status = "expired"
                if request.status != "saved":
                    receipt(request.status, file=str(path), **({"message": request.error} if request.error else {}))
                    return 2 if request.status == "error" else 1
            finally:
                request.close()
        receipt("saved", file=str(path), names=args.names)
        return 0
    except Cancelled:
        receipt("cancelled", message="Nothing saved.")
        return 1
    except SafeError as error:
        receipt("error", message=str(error))
        return 2
    except KeyboardInterrupt:
        receipt("cancelled", message="Entry interrupted; check the receipt before retrying.")
        return 1
    except Exception:
        receipt("error", message="Private entry failed. Check destination permissions and local UI support.")
        return 3


if __name__ == "__main__":
    sys.exit(main())
