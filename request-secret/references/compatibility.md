# Compatibility and verification

`request-secret` uses a Python helper rather than a provider-specific input tool.
It works when the agent has shell execution and access to the destination machine.
No API requests or additional packages are required. macOS and Linux are supported;
Windows needs WSL because the helper uses POSIX file permissions and locks.

## Installation

Keep this repository as the source of truth and symlink its `request-secret`
directory into the relevant discovery directories:

```sh
ln -s /absolute/path/to/agent-skills/request-secret ~/.codex/skills/request-secret
ln -s /absolute/path/to/agent-skills/request-secret ~/.claude/skills/request-secret
```

The installed Codex on Lukas's machine discovers `~/.codex/skills`. Current
[Codex documentation](https://developers.openai.com/codex/skills) also documents
`~/.agents/skills` for user skills; use the location supported by your installed
version. Claude documents `~/.claude/skills` in its
[skills guide](https://code.claude.com/docs/en/skills). T3 delegates skill discovery
to each provider. Existing sessions may need to restart to discover the new name.

## Source comparison

[cuentadesanti/ask-secret](https://github.com/cuentadesanti/ask-secret) is an existing
MIT-licensed skill that requests values in a masked macOS dialog and writes them
to an env file. This helper is independently implemented around that same private
entry pattern. It adds a loopback form for Linux/remote hosts, atomic batch saves,
concurrent-edit detection, refusal of tracked/unignored Git targets, and suppressed
child output by default. It does not depend on the upstream project.

The OpenAI-specific
[openai-platform-api-key skill](https://github.com/openai/plugins/blob/main/plugins/openai-developers/skills/openai-platform-api-key/SKILL.md)
uses a Platform connector and encrypted delivery. It depends on connector
capabilities and is specific to OpenAI API key creation, so it does not provide a
general entry UI across T3's Codex and Claude providers.

The inspected local T3 source checkout's normal user-input flow returns the answer
to the provider. Its Codex MCP elicitation flow handles app approvals and declines
URL elicitation without an explicit opening flow. Neither inspected path provides
a generic secret-to-file channel. This implementation uses the provider shell,
so changes to those internal interfaces do not affect it.

## File handling

- All requested values are collected before one atomic replacement with mode
  `0600`. Comments and unrelated single-line assignments are preserved. Duplicate
  entries for a replaced variable are consolidated. New values are shell-quoted;
  the helper's loader treats them literally and performs no expansion or execution.
- Multiline values, empty values, invalid variable names and unsupported existing
  env-file syntax are refused. This is an env-file writer, not a general file or
  JSON credential-store editor.
- The destination's parent must exist. The file must be owned by the user and must
  not be a symlink or hard-linked. Parent aliases are canonicalized. To update an
  intentionally symlinked credential file, pass its resolved target path.
- Git targets must already be ignored and untracked. The helper does not edit
  `.gitignore`. Temporary plaintext is staged in Git's metadata directory when
  saving in a repository, and removed after success or handled failure. That
  directory must share the destination filesystem for atomic replacement.
- A persistent, empty `.<filename>.request-secret.lock` coordinates helper writers.
  It contains no values. A conflicting edit after entry began aborts the save.
  External writers that ignore this lock can still race the final check/rename.
- A hard process kill or machine crash may leave a mode-`0600` staging file.
  This is ordinary filesystem persistence, not encrypted storage. The helper
  does not protect against another process running as the same user.

## Browser handling

The listener binds only to `127.0.0.1` on a random port. A per-request 256-bit token
is delivered in the URL fragment, used in a request header and removed from the
visible URL on load. The server checks Host and Origin, disables request logging,
uses `no-store` and `no-referrer`, and serves no third-party code. It accepts one
successful submission or cancellation and expires at the configured deadline.
The token may appear in the agent receipt; it authorizes that temporary submission
but does not grant access to saved values. It is not a saved API credential.

Both entry UIs provide Show/Hide controls to verify the value before saving. Native
Show/Hide redraws the dialog with the same value, entirely inside the captured
osascript process; it does not pass the value through another tool call or argv.
Browser Show/Hide switches the input type locally and preserves its value.
Revealing a value does not change the receipt or file handling.

After the helper confirms a successful save, the page calls `window.close()`.
It never closes on a failed or unconfirmed save. If the browser blocks script
closing, the cleared form is replaced by a saved confirmation. Browsers restrict
which windows scripts may close; see [Window.close](https://developer.mozilla.org/en-US/docs/Web/API/Window/close).

Use a regular browser for real entry. T3 preview automation has tool/action history;
inspecting form values or typing a real credential through an agent tool defeats
the entry boundary. Browser extensions, devtools, OS clipboard managers and local
debugging can also observe user input. Prefer the native dialog on a local Mac.

Over SSH, the form and destination remain on the agent host. Forward the listener
to the same loopback port on the user's machine through an existing authorized SSH
connection. Do not publish the form or use a public preview proxy.

## Verification

Run the synthetic tests without any real credentials:

```sh
python3 -m unittest discover -s request-secret/scripts -p 'test_*.py' -v
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py request-secret
```

Tests exercise the real HTTP listener and subprocess CLI with dummy values. They
cover receipts/output, file permissions, preservation, quoting, cancellation,
expiry, replay, cross-origin/token rejection, tracked destinations (including
nested paths), concurrent edits, and child-output suppression/redaction. Native
dialog behavior uses mocked subprocess replies; compile the embedded AppleScript
on macOS before delivery. Native user interaction requires a human smoke test.
