#!/usr/bin/env node
// Protocol-2 transport for T3 servers whose native MCP tools are unavailable.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const args = process.argv.slice(2);
const option = name => {
  const index = args.indexOf(name);
  return index < 0 ? undefined : args[index + 1];
};
if (args.includes('--help')) {
  console.log('node t3_rpc.mjs --cli /path/to/t3 [--origin http://127.0.0.1:3773] (--path /api/... [--input post.json] | --method orchestration.launchThread --input request.json)');
  process.exit(0);
}
const cli = option('--cli');
if (!option('--origin')) throw new Error('Provide the running server loopback --origin.');
const origin = new URL(option('--origin'));
const path = option('--path');
const method = option('--method');
const input = option('--input');
if (!cli || Boolean(path) === Boolean(method) || (method && !input)) {
  throw new Error('Provide --cli and either --path or both --method and --input.');
}
if (!['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname) || origin.username || origin.password) {
  throw new Error('Run this helper on the target host against its loopback server.');
}
const payload = input ? JSON.parse(readFileSync(input, 'utf8')) : undefined;
const auth = parameters => {
  try {
    return execFileSync(cli, ['auth', 'session', ...parameters, ...(option('--home') ? ['--base-dir', option('--home')] : [])], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 25000,
    });
  } catch {
    throw new Error('T3 auth CLI failed; captured output withheld because it may contain credentials.');
  }
};
let session;
let ws;
try {
  session = JSON.parse(auth(['issue', '--ttl', '5m', '--label', 't3-launch', '--json']));
  const headers = {
    Authorization: `Bearer ${session.token}`,
    'Content-Type': 'application/json',
    'x-t3-orchestration-protocol': '2',
  };
  const request = async (route, post = false, body) => {
    const response = await fetch(new URL(route, origin), {
      headers, method: post ? 'POST' : 'GET', body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new Error(`T3 HTTP ${response.status}; no automatic retry.`);
    return response.json();
  };
  if (path) {
    console.log(JSON.stringify(await request(path, Boolean(input), payload)));
  } else {
    const ticket = await request('/api/auth/websocket-ticket', true);
    const url = new URL('/ws', origin);
    url.protocol = origin.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('orchestrationProtocol', '2');
    url.searchParams.set('wsTicket', ticket.ticket);
    ws = new WebSocket(url);
    const result = await new Promise((resolve, reject) => {
      const fail = message => { clearTimeout(timer); reject(new Error(message)); };
      const timer = setTimeout(() => fail('T3 RPC timed out; inspect the saved thread ID before retrying.'), 30000);
      ws.onerror = () => fail('T3 WebSocket failed; inspect the saved thread ID before retrying.');
      ws.onclose = () => fail('T3 WebSocket closed before a result; inspect the saved thread ID.');
      ws.onopen = () => ws.send(JSON.stringify({ _tag: 'Request', id: '1', tag: method, payload, headers: [] }));
      ws.onmessage = event => {
        try {
          const message = JSON.parse(event.data);
          if (message._tag === 'Ping') { ws.send(JSON.stringify({ _tag: 'Pong' })); return; }
          if (message._tag === 'Exit' && message.requestId === '1') {
            clearTimeout(timer);
            resolve(message.exit);
          }
          if (message._tag === 'Defect') fail('T3 RPC reported a defect; inspect the saved thread ID.');
        } catch { fail('Invalid T3 RPC response.'); }
      };
    });
    if (result._tag !== 'Success') throw new Error(`T3 RPC failed: ${JSON.stringify(result.cause)}`);
    console.log(JSON.stringify(result.value));
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  ws?.close();
  if (session) {
    try { auth(['revoke', session.sessionId]); }
    catch { console.error('Session revocation failed; it expires within five minutes.'); process.exitCode = 1; }
  }
}
