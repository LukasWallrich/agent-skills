#!/usr/bin/env node
// Exercise transport races without writing to a live comment log.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../assets/html-comments.js'), 'utf8');
function fn(name) {
  const start = source.indexOf('  function ' + name + '(');
  assert(start >= 0, name);
  return source.slice(start, source.indexOf('\n  }', start) + 4);
}
function setup() {
  let box = [];
  const c = {
    LOCAL: false, posting: {}, ENDPOINT: 'https://example.test/exec', PROJECT: 'fixture',
    ROWS_READ_TRIES: 3, ROWS_RETRY_MS: 1, FLUSH_SPACING: 1,
    Date, Promise, console: {warn() {}}, setTimeout, encodeURIComponent,
    readOutbox: () => box, writeOutbox: v => { box = v; },
    genId: (() => { let n = 0; return () => 'request-' + ++n; })()
  };
  vm.createContext(c);
  for (const name of ['composeRows', 'postRecord', 'flushOutbox', 'fetchRows', 'reconcileOutbox']) vm.runInContext(fn(name), c);
  return c;
}
(async () => {
  const c = setup();
  const deletion = {itemId: 'delete-1', vote: 'delete', ts: '2026-09-14T09:00:00Z', note: '{"parentId":"root"}'};
  let finish;
  let posts = 0;
  c.rawPost = () => { posts++; return new Promise(r => { finish = r; }); };
  const pending = c.postRecord(deletion);
  assert.equal(c.readOutbox().length, 1, 'deletion must survive a reload before POST completes');
  assert.equal(c.composeRows([]).length, 1, 'stale reads must preserve the deletion');
  await c.flushOutbox();
  assert.equal(posts, 1, 'refresh must not resend an in-flight write');
  finish({ok: true}); await pending;
  assert.equal(c.composeRows([]).length, 1, 'POST acknowledgement must not expose stale state');
  c.rawPost = async () => { posts++; return {ok: true}; };
  await c.flushOutbox();
  assert.equal(posts, 1, 'acknowledged writes must not be resent before read-back');
  assert.equal(c.readOutbox().length, 1, 'flush must retain writes until read-back');
  c.reconcileOutbox([deletion]);
  assert.equal(c.readOutbox().length, 0, 'read-back must retire the pending write');
  assert.equal(c.composeRows([deletion]).length, 1);
  console.log('ok: deletion survives pending POST, stale reads, and flush; read-back clears it');

  c.rawPost = async () => { throw new Error('offline'); };
  await c.postRecord(deletion);
  assert.equal(c.readOutbox().length, 1, 'failed writes remain durable');
  await c.postRecord(deletion);
  assert.equal(c.readOutbox().length, 1, 'retry must not duplicate the queued record');
  console.log('ok: failed writes remain queued without duplicate entries');

  const reads = [];
  c.fetchJSON = async (url, opts) => { reads.push({url, opts}); return {ok:true, rows:[]}; };
  await c.fetchRows(); await c.fetchRows();
  assert.notEqual(reads[0].url, reads[1].url, 'each read needs a fresh redirect URL');
  assert.equal(reads[0].opts.cache, 'no-store');
  assert.equal(new URL(reads[0].url).searchParams.get('project'), 'fixture');
  console.log('ok: row reads bypass cached responses and cached redirects');
})().catch(e => { console.error(e); process.exitCode = 1; });
