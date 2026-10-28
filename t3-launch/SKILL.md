---
name: t3-launch
description: Start a new top-level T3 Code conversation in a project, locally or on the box. Prefer native T3 tools; use the remote protocol-2 fallback when the target server is outside the connected environment. Not for delegated subagents.
---

# Launch a T3 thread

## Use native tools in the connected environment

Call `t3_environment_read` to identify the server, then `t3_project_list` to find the project by its canonical workspace path. Native tools operate on that environment; a paired box in the desktop sidebar does not make the Mac's tools target the box. The current `t3_thread_launch` schema has no remote-environment selector. If future tools expose one, prefer it to the fallback.

Use `orchestrator_capabilities` for the live provider/model catalog. Start an ordinary top-level conversation with `t3_thread_launch`, passing the project's ID, title and task as `message`. Choose the requested model or inherit the established model; do not silently route Codex through OpenRouter. Preserve the parent's runtime mode and the user's task restrictions. A status request should ask for inspection and an update without starting another extraction or other substantive job.

Set `workspaceStrategy` explicitly: `{"type":"root"}` for the project's existing root, `existing_worktree` for a specified checkout, or `worktree` with an explicit base ref for a new checkout. Omitted project/workspace settings may target the caller's project or its root instead of the intended location. Use the native project creation tool when the project is missing, or the remote project mutation endpoint described below.

Retain the returned thread ID. `preparing`, `queued` or `starting` means accepted, not completed. Verify with `t3_thread_read` / `t3_thread_wait` when needed. Launch has no native idempotency key: after an error or lost response, inspect `t3_thread_list` before another launch. Link as `[title](t3-thread://v1/THREAD_ID)`.

## Launch on another server

For a box request, use the `box-access` skill to establish SSH and identify the running service. Run the fallback on the box, using box-side project paths. Read [references/remote.md](references/remote.md) for the protocol-2 transport and saved launch request.

The old Python helper used v1 dispatch endpoints and `server-runtime.json`; it is retired. Native tools replace it for the connected environment. The small `scripts/t3_rpc.mjs` fallback handles loopback authentication and protocol-2 WebSocket RPC for environments without native tool access; it does not launch a server or modify a database.
