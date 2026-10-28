# Remote T3 server

Run `scripts/t3_rpc.mjs` on the target host. It requires Node with global `fetch` and `WebSocket` (Node 22+), and the installed T3 CLI. It creates a five-minute bearer session in memory and revokes it afterwards. Do not print CLI auth output or save bearer tokens.

Find the active CLI and loopback endpoint from the running service and its configuration; do not start another server or assume the default port. For the box, consult `openclaw_box/OPERATIONS.md`. At verification on 2026-10-28, the service was `t3code.service`, the loopback origin was `http://127.0.0.1:3773`, and its installed CLI was `~/.t3/runtime/versions/0.1.2/t3`. These paths can change after updates.

Inspect registered projects without reading transcripts:

```sh
node <skill-root>/scripts/t3_rpc.mjs --cli <installed-t3> --origin <loopback-origin> \
  --path /api/orchestration/shell
```

Match `projects[].workspaceRoot` to the intended canonical path. Reuse that project's ID. For a missing project, save a request with `type: "project.create"`, fresh `commandId` and `projectId` UUIDs, `title` and `workspaceRoot`, then POST it through the helper:

```sh
node <skill-root>/scripts/t3_rpc.mjs --cli <installed-t3> --origin <loopback-origin> \
  --path /api/projects/mutate --input /absolute/work/create-project.json
```

`--path` without `--input` reads with GET; adding `--input` sends POST. The installed nightly's `t3 project add/remove` commands attempt server ownership and fail while the service is running. Use the live server API instead. Test cleanup uses the same endpoint with `type: "project.delete"`, fresh `commandId`, and the test `projectId`; delete the test thread first through `orchestration.dispatchCommand` with `type: "thread.delete"`, fresh `commandId` and `threadId`.

Write a UTF-8 launch JSON file under the target project's `work/`. Treat this as the durable request receipt: reserve fresh UUIDs for `commandId`, `threadId`, and `initialMessage.messageId` before sending. Preserve the exact file after uncertainty; do not generate replacement IDs automatically. Use the requested model or established session model, with an explicit matching provider instance. Preserve the parent's runtime mode and task scope.

```json
{
  "commandId": "FRESH-UUID",
  "threadId": "FRESH-UUID",
  "projectId": "PROJECT-ID",
  "title": "Latest extraction update",
  "modelSelection": {"instanceId": "codex", "model": "gpt-6.1-sol"},
  "runtimeMode": "full-access",
  "interactionMode": "default",
  "workspaceStrategy": {"type": "root"},
  "initialMessage": {
    "messageId": "FRESH-UUID",
    "text": "Read the repository instructions and report progress, results and blockers for the latest extraction. This is a status request; do not start or restart extraction, change data or code, or incur paid API costs.",
    "attachments": []
  }
}
```

The sample mode assumes a full-access parent; substitute the actual parent mode. Model options, when needed, use an array of `{"id":"...","value":"..."}` objects matching the installed provider's catalog.

Launch using the server's protocol-2 service:

```sh
node <skill-root>/scripts/t3_rpc.mjs --cli <installed-t3> --origin <loopback-origin> \
  --method orchestration.launchThread --input /absolute/project/work/launch.json
```

The result includes `threadId`, `projection` and `resumed`. Inspect `projection.thread`, `projection.messages` and `projection.runs` to verify the model, access mode, exact user message and run state. A successful RPC with a preparing run confirms acceptance, not task completion.

Read the saved thread without sending another message:

```sh
node <skill-root>/scripts/t3_rpc.mjs --cli <installed-t3> --origin <loopback-origin> \
  --path /api/orchestration/threads/THREAD_ID
```

On timeout or disconnect, inspect that ID first. A matching stored thread/message confirms acceptance. If absent, retry only the identical saved launch request: protocol-2 `orchestration.launchThread` keeps command receipts. If the server reports incompatible state or schema, inspect its current contracts before further mutations.

The transport uses `x-t3-orchestration-protocol: 2` on HTTP reads, POST `/api/auth/websocket-ticket`, and `/ws?orchestrationProtocol=2&wsTicket=...`. WebSocket frames use Effect RPC JSON (`_tag: Request`, `id`, `tag`, `payload`, `headers`), not JSON-RPC 2.0. `/mcp` requires a separate MCP credential; an ordinary CLI bearer session cannot authenticate there. The native MCP tool name `t3_thread_launch` corresponds to service RPC `orchestration.launchThread`, not the retired v1 dispatch endpoint.

After changing transport code, verify a read against the live server and a no-tools echo launch in an isolated scratch project. Replay the identical saved request and check that only one user message exists. Delete only the test thread/project through the service afterwards. Do not rerun a substantive user task to test the transport.
