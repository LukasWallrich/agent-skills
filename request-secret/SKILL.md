---
name: request-secret
description: Request one or several passwords, API keys or tokens through a private native dialog, local browser form or tailnet form and save them together to an env file without putting values in chat or tool output. Use when a task needs missing credentials, including Codex or Claude sessions in T3 Code.
---

# Request a secret

Use `scripts/request_secret.py` (Python 3.10+, standard library only). The agent
requests the credential in chat; the user enters it in a separate private UI.
The helper receives and writes the value and returns only a receipt.

Use the folder containing this file as `<skill-root>`; executing through an
installed skill-directory symlink works without a separate resolution call.
The helper has no default destination: `--file` is required. Choose the variable
name and destination for the consuming service or project, honoring any explicit
path and existing credential conventions. For Lukas's shared credentials, prefer
the existing canonical credentials file; `$CODEX_HOME/.env` is a symlink, so
resolve that file's destination without reading values. For project-only secrets,
use its Git-ignored `.env.local` or a service/project-specific private config file
outside Git. A generic request with no consumer can use a separate demonstration
file outside Git; do not make that the default for real credentials.

Use an absolute path with an existing parent directory. The helper refuses tracked
or non-ignored repository files, file symlinks, and files owned by another user.

Say which credential is needed, why, and where it will be saved, then launch:

```sh
python3 <skill-root>/scripts/request_secret.py request \
  --file '/absolute/path/to/credentials.env' SERVICE_API_KEY
```

For several credentials going to the same file, request them in one invocation:

```sh
python3 <skill-root>/scripts/request_secret.py request \
  --file '/absolute/path/to/credentials.env' SERVICE_API_KEY SERVICE_API_SECRET
```

Supply up to 16 distinct variable names. Multiple values always use one browser
form, including on macOS, and save together atomically. Native macOS dialogs are
only for a single value; `--ui native` with several names is refused.
For credentials with different destinations, use separate requests.

Launch directly with the ordinary shell tool after reading this skill. Routine
entry does not need tool discovery, helper-source inspection, or a separate path
inventory. If a private parent directory needs creating or the credential-file
symlink needs resolving, combine that preparation and helper launch in one shell
invocation. Use a short initial yield (about one second) so the dialog opens
promptly and the request remains active; then wait on that same process for its
receipt. Do not re-read this skill within the same conversation unless it changed.

For one value on a local Mac, this opens a native dialog with masked input and a
Show/Hide button to check correctness. Show/Hide preserves the entered value and the request's
original timeout. Elsewhere it opens a
private browser form bound to `127.0.0.1`, and prints its temporary URL; requests
for several values also use this form on macOS. Use
`--ui browser` to choose the form explicitly; `--no-open` leaves opening the URL
to the user. Remote tailnet entry is described below. Existing
entries with those names are replaced; other entries are preserved. Cancel or
timeout saves nothing. The default timeout is ten minutes. The browser form has
one Show/Hide button for all values; revealing changes only local display, not transcript output.
After a confirmed browser save, the page tries to close its tab. If browser policy
blocks closing, it displays a saved confirmation and invites the user to close it.

Keep the yielding shell process alive while the user enters the value. Poll using
the runtime's process/session tool; do not end the turn with the request pending.
Report only `saved`, variable names and the destination. On failure, act on the
safe error; cancellation is not a reason to relaunch automatically.

## Keep values out of the conversation

- Never ask for a secret in a chat reply, `request_user_input`, `AskUserQuestion`,
  or an MCP form response. Masked display alone does not prevent persistence.
- Never pass values in tool arguments, shell commands, prompts, screenshots,
  or attachments. Do not inspect, automate, record, snapshot, or read the private
  form/dialog while real credentials are being entered. Let the user submit it.
- Do not read the env file with agent tools, print it, or include it in context.
  Use `list` for names and presence only:

  ```sh
  python3 <skill-root>/scripts/request_secret.py list --file '/absolute/path/to/credentials.env'
  ```

- To use the file, load it inside the consuming process. This helper provides a
  literal env loader; it never sources or executes file contents:

  ```sh
  python3 <skill-root>/scripts/request_secret.py run \
    --file '/absolute/path/to/credentials.env' -- python3 app.py
  ```

  Child stdout/stderr are suppressed by default; the receipt contains its exit
  code. Add `--redact-output` before `--` only when output is needed and the command
  is understood: it removes exact secret values across output chunk boundaries,
  but cannot protect encoded, transformed or partial disclosures. Avoid verbose
  HTTP/auth logging and passing credentials as command-line arguments.

## T3 Code and remote hosts

This works through the ordinary shell tool in both Codex and Claude; no T3 fork,
MCP registration, provider API key, or paid service is needed. T3's normal question
cards are not a private credential channel. On macOS, native entry stays outside
T3's browser tooling and history. For real browser entry, give the user the link
to open directly instead of automating the form through T3's preview tools.

The file is on the **agent's machine**. A native dialog requires that machine's
desktop session. On a remote machine reachable through the user's tailnet,
prefer a direct link:

```sh
python3 <skill-root>/scripts/request_secret.py request \
  --file '/absolute/path/to/credentials.env' --tailnet SERVICE_API_KEY SERVICE_API_SECRET
```

`--tailnet` runs `tailscale ip -4`, binds only to that Tailscale IPv4 address on
a random port, and prints an HTTP URL with the request token in its fragment.
It selects browser entry even on macOS and does not open a browser on the remote
host. Calls without `--tailnet` stay local and do not need Tailscale. The listener
closes after save, cancellation or timeout.

Verify the returned URL's origin responds (for example with `curl --noproxy '*'`
without the fragment), then give the complete clickable URL to the user to open
directly. Never submit a real value as part of verification. Keep the request
process alive for the receipt. Do not require SSH forwarding or the built-in T3
browser for this tailnet path; a blank T3 preview does not prove the form is broken.

Tailnet HTTP relies on Tailscale's encrypted transport and access rules. Do not
bind to `0.0.0.0`, enable Funnel, publish the form, or use a public preview/tunnel.
If no shared tailnet is available, use `--ui browser --no-open` with an authorized
SSH forward to the same loopback port on the user's machine; matching ports are
required by Host/Origin checks.

The saved file is plaintext with mode `0600`. This prevents accidental transcript
entry through this workflow; it does not deny an agent or another process running
as the user access to the file. A stronger boundary needs a broker/keychain and
restricted execution. If a value was already pasted into chat, do not claim this
removes it: avoid repeating it and recommend rotation.

See [compatibility and verification](references/compatibility.md) for the source
comparison, installation locations, tests, and exact limits.
