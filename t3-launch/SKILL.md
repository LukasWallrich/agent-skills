---
name: t3-launch
description: Add an existing local folder as a T3 Code project and launch a new prompt with a chosen model, using the running T3 server's local API instead of computer use. Use when the user asks to add projects or start chats in T3 Code.
---

# T3 Code project and prompt launch

Use `scripts/t3_launch.py` to control the running local desktop app or headless server. It uses the installed CLI for a five-minute bearer session, revokes it afterwards, and sends supported orchestration commands over loopback HTTP. It does not modify the T3 database directly or need an additional server, browser, or package installation.

On macOS, the default CLI comes from `/Applications/T3 Code (Alpha).app`; `--app` overrides that bundle. On Linux, the helper finds `t3` on PATH or the running T3 service executable through `/proc`. Pass `--cli /absolute/path/to/t3` to select another installed CLI. `--home` overrides the T3 data directory. The desktop app or server must already be running. The endpoint comes from `<home>/userdata/server-runtime.json`; do not assume port 3773. Remote environments are outside this helper's scope.

Resolve `<skill-root>` to the directory containing this `SKILL.md`, following symlinks if needed. The helper needs only Python 3 and an installed T3 app or CLI. It controls the T3 server on the same machine: box-side paths refer to projects on the box, not on the Mac.

## Add or reuse a project

Locate the intended folder and read applicable instructions. Project identity is its canonical absolute path. Existing active projects are reused without changing their settings.

```sh
python3 <skill-root>/scripts/t3_launch.py add '/absolute/project/path'
```

Use `--project-title` for a requested new-project title. `inspect` returns active project IDs, titles and paths without printing chat contents or credentials.

## Launch a new prompt

Write the intended task to a UTF-8 prompt file under the current workspace's `work/`. Include context needed by the receiving agent, such as sources and a request to pull repository updates first, when requested. Preserve the user's scope; starting a task does not authorize publishing, contacting people, or unrelated changes.

Choose the model from the user's request or established preference; do not silently route a Codex request through OpenRouter. `--instance codex --model gpt-6.1-sol` selects GPT-6.1 Sol through the Codex subscription. Other configured provider instances can be selected explicitly; model IDs and reasoning options must match that instance. Omit `--effort` unless requested or established by context.

```sh
python3 <skill-root>/scripts/t3_launch.py launch '/absolute/project/path' \
  --title 'Compare deduplication methods' \
  --prompt-file '/absolute/work/prompt.txt' \
  --receipt '/absolute/work/t3-launch-receipt.json' \
  --instance codex --model gpt-6.1-sol --effort high
```

`launch` adds the project if needed, creates the thread, and starts its first turn. It uses the current checkout and records the current Git branch; it does not switch branches or pull updates itself. Permission mode defaults to `approval-required`. Pass `--runtime-mode full-access` only when the user's authorization or established session context supports that mode.

Keep the receipt: it reserves IDs before mutations and binds them to the project, title, model, mode and prompt hash. Re-running the identical command with the same receipt verifies the existing message without sending it again. Use a fresh receipt only for an intentionally new chat. Do not automatically use a fresh receipt after errors. If an outcome is uncertain, inspect the recorded thread before proceeding; the helper stops instead of resending.

Output verifies the project, stored model and user message, counts user messages in the new chat’s latest turn, and reports turn/session state. `starting` means the prompt was accepted and the provider is starting; it is not task completion. Report that accurately. The helper does not focus the chat window. Use computer use only if UI navigation is also needed or the installed API has changed and cannot be adapted from source.

For a later status check without sending anything:

```sh
python3 <skill-root>/scripts/t3_launch.py inspect --thread-id THREAD_ID
```

This reports model, latest turn, session status and the user-message count for that turn. It does not expose the chat’s message text.

## Compatibility and verification

T3's API is internal and can change. On schema errors, inspect the installed app version and the corresponding `packages/contracts/src/{environmentHttp,orchestration}.ts` files in an available T3 source checkout. HTTP requires separate `thread.create` and `thread.turn.start` commands; the UI's bootstrap payload alone does not create a thread through HTTP. Model options are an array of `{id, value}` objects.

Project CLI commands also exist (`t3 project add PATH`, `t3 app PATH`), but there is currently no bundled thread-launch CLI. On macOS the helper calls the installed app's CLI through `ELECTRON_RUN_AS_NODE=1`; on Linux it calls the installed service executable. Both avoid downloads. Never print captured auth command output or retain bearer tokens in files.

After a helper change, use an isolated scratch project and a no-tools echo prompt. Confirm the assistant response and verify that replay with the same receipt leaves only one user message. Remove only the scratch project/thread through orchestration commands after completion. Do not rerun the user's substantive task merely to test transport.
