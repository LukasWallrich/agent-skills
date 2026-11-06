const test = require("node:test");
const assert = require("node:assert");
const { PATH, METHODS, TAG, createEndpoint } = require("../src/handler.js");

const KEY = "ABCD2345";
const GOOD = "a-good-key";

function makeItem(over = {}) {
  return {
    tags: [],
    saves: 0,
    deleted: false,
    hasTag(t) { return this.tags.includes(t); },
    addTag(t) { this.tags.push(t); },
    async saveTx() { this.saves++; },
    async loadAllData() {},
    ...over,
  };
}

function makeZotero({ item = makeItem(), search, eligible = true, enabled = true } = {}) {
  const attachment = makeItem({
    key: "ATTA2345", attachmentContentType: "application/pdf", erased: 0,
    async eraseTx() { this.erased++; },
  });
  class Root {
    requireServerIDOnWrite = true;
    _validateServerID(r) {
      const id = r.headers.get("Zotero-Server-ID");
      if (!id) return [428, "text/plain", "not provided"];
      return id === "SERVER" ? null : [412, "text/plain", "mismatch"];
    }
    async _authenticateWriteRequest(r) {
      const fromUrl = r.searchParams.get("key");
      return (r.headers.get("Zotero-API-Key") || fromUrl) === GOOD ? null : [401, "text/plain", "no key"];
    }
  }
  const zotero = {
    attachment,
    calls: { resolvers: [], added: [], delays: [] },
    Libraries: { userLibraryID: 1 },
    Items: { getByLibraryAndKeyAsync: async (lib, key) => (lib === 1 && key === KEY ? item : null) },
    Prefs: { get: (name) => (name === "httpServer.localAPI.enabled" ? enabled : undefined) },
    Server: { LocalAPI: { Root } },
    Promise: { delay: (ms) => (ms > 1000 ? new Promise(() => {}) : new Promise((resolve) => setTimeout(resolve, ms))) },
    logError() {},
    Attachments: {
      canFindFileForItem: () => eligible,
      getFileResolvers(i, methods) {
        zotero.calls.resolvers.push(methods);
        return ["RESOLVER"];
      },
      addFileFromURLs(i, resolvers, options) {
        zotero.calls.added.push(resolvers);
        options.onAccessMethodStart("doi");
        return search ? search(attachment) : Promise.resolve(attachment);
      },
    },
  };
  return zotero;
}

function request(data = { itemKey: KEY }, headers = {}) {
  return {
    method: "POST",
    data,
    headers: { "Zotero-Server-ID": "SERVER", "Zotero-API-Key": GOOD, ...headers },
  };
}

async function post(zotero, req = request(), options = {}) {
  const Endpoint = createEndpoint(zotero, { version: "9.9.9", ...options });
  const [status, type, body] = await new Endpoint().init(req);
  return { status, type, body: type === "application/json" ? JSON.parse(body) : body };
}

test("the server's single-argument convention and JSON-only POST are declared", () => {
  const Endpoint = createEndpoint(makeZotero());
  const endpoint = new Endpoint();
  assert.equal(endpoint.init.length, 1);
  assert.deepEqual(endpoint.supportedMethods, ["POST"]);
  assert.deepEqual(endpoint.supportedDataTypes, ["application/json"]);
  assert.equal(endpoint.allowRequestsFromUnsafeWebContent, undefined);
  assert.equal(PATH, "/find-pdf-api/v1/find");
});

test("every lookup uses all four of Zotero's methods", async () => {
  const zotero = makeZotero();
  await post(zotero);
  assert.deepEqual(METHODS, ["doi", "url", "oa", "custom"]);
  assert.deepEqual(zotero.calls.resolvers, [["doi", "url", "oa", "custom"]]);
  assert.deepEqual(zotero.calls.added, [["RESOLVER"]]);
});

test("callers cannot supply methods, resolvers or batches", async () => {
  for (const data of [
    { itemKey: KEY, methods: ["custom"] },
    { itemKey: KEY, resolvers: [] },
    { itemKeys: [KEY] },
    [KEY],
    { itemKey: [KEY] },
    { itemKey: "short" },
    null,
  ]) {
    const zotero = makeZotero();
    const { status, body } = await post(zotero, request(data));
    assert.equal(status, 400, JSON.stringify(data));
    assert.equal(body.state, "bad-request");
    assert.equal(zotero.calls.added.length, 0);
  }
});

test("a request with an Origin header is refused before anything else", async () => {
  const zotero = makeZotero();
  const { status, body } = await post(zotero, request({ itemKey: KEY }, { origin: "https://example.org" }));
  assert.equal(status, 403);
  assert.equal(body.state, "forbidden");
  assert.equal(zotero.calls.added.length, 0);
});

test("a missing or wrong key is refused with Zotero's own response", async () => {
  for (const headers of [{ "Zotero-API-Key": "wrong" }, { "Zotero-API-Key": undefined }]) {
    const zotero = makeZotero();
    const { status } = await post(zotero, request({ itemKey: KEY }, headers));
    assert.equal(status, 401);
    assert.equal(zotero.calls.added.length, 0);
  }
});

test("a key in the URL is not accepted", async () => {
  const zotero = makeZotero();
  const req = request({ itemKey: KEY }, { "Zotero-API-Key": undefined });
  req.searchParams = new URLSearchParams(`key=${GOOD}`);
  assert.equal((await post(zotero, req)).status, 401);
});

test("a missing or wrong server ID is refused", async () => {
  assert.equal((await post(makeZotero(), request({ itemKey: KEY }, { "Zotero-Server-ID": undefined }))).status, 428);
  assert.equal((await post(makeZotero(), request({ itemKey: KEY }, { "Zotero-Server-ID": "OTHER" }))).status, 412);
});

test("the local API switch is respected", async () => {
  const { status, body } = await post(makeZotero({ enabled: false }));
  assert.equal(status, 403);
  assert.equal(body.state, "local-api-off");
});

test("a Zotero without the expected internals answers unsupported", async () => {
  const zotero = makeZotero();
  delete zotero.Server.LocalAPI.Root.prototype._authenticateWriteRequest;
  const { status, body } = await post(zotero);
  assert.equal(status, 501);
  assert.equal(body.state, "unsupported");
});

test("found: tags the attachment and the item, and reports the new tag", async () => {
  const item = makeItem();
  const zotero = makeZotero({ item });
  const { status, body } = await post(zotero);
  assert.equal(status, 200);
  assert.deepEqual(body, {
    state: "found", attachmentKey: "ATTA2345", contentType: "application/pdf",
    itemTagged: true, methodsTried: ["doi"], version: "9.9.9",
  });
  assert.deepEqual(item.tags, [TAG]);
  assert.deepEqual(zotero.attachment.tags, [TAG]);
});

test("found: an item already tagged is not saved again", async () => {
  const item = makeItem({ tags: [TAG] });
  const { body } = await post(makeZotero({ item }));
  assert.equal(body.itemTagged, false);
  assert.equal(item.saves, 0);
});

test("a failed tag removes the attachment, so no untagged file stays", async () => {
  let zotero;
  const item = makeItem({ async saveTx() { throw new Error("locked"); } });
  zotero = makeZotero({ item });
  const { status, body } = await post(zotero);
  assert.equal(status, 500);
  assert.equal(body.state, "error");
  assert.equal(zotero.attachment.erased, 1);
});

test("none: leaves the item untagged", async () => {
  const item = makeItem();
  const { body } = await post(makeZotero({ item, search: async () => false }));
  assert.equal(body.state, "none");
  assert.deepEqual(body.methodsTried, ["doi"]);
  assert.deepEqual(item.tags, []);
});

test("an unknown key, or an item with a file already, is reported without a search", async () => {
  const unknown = makeZotero();
  assert.equal((await post(unknown, request({ itemKey: "ZZZZ2345" }))).body.state, "item-not-found");
  assert.equal(unknown.calls.added.length, 0);
  const full = makeZotero({ eligible: false });
  assert.equal((await post(full)).body.state, "not-eligible");
  assert.equal(full.calls.added.length, 0);
  const trashed = makeZotero({ item: makeItem({ deleted: true }) });
  assert.equal((await post(trashed)).body.state, "not-eligible");
});

test("a search failure answers error and releases the lock", async () => {
  let fail = true;
  const zotero = makeZotero({ search: async (a) => { if (fail) throw new Error("boom"); return a; } });
  const Endpoint = createEndpoint(zotero);
  const [status, , body] = await new Endpoint().init(request());
  assert.equal(status, 500);
  assert.equal(JSON.parse(body).state, "error");
  fail = false;
  const [again] = await new Endpoint().init(request());
  assert.equal(again, 200);
});

test("a second call waits its turn: busy while a search runs, free afterwards", async () => {
  let release;
  let hold = true;
  const zotero = makeZotero({
    search: (a) => (hold ? new Promise((resolve) => { release = () => resolve(a); }) : Promise.resolve(a)),
  });
  const Endpoint = createEndpoint(zotero);
  const first = new Endpoint().init(request());
  await new Promise((resolve) => setImmediate(resolve));
  const [status, , body] = await new Endpoint().init(request());
  assert.equal(status, 409);
  assert.equal(JSON.parse(body).state, "busy");
  hold = false;
  release();
  assert.equal(JSON.parse((await first)[2]).state, "found");
  assert.equal((await new Endpoint().init(request()))[0], 200);
});

test("timeout: replies, stays busy, then erases the late file and frees the lock", async () => {
  let release;
  const zotero = makeZotero({ search: (a) => new Promise((resolve) => { release = () => resolve(a); }) });
  const Endpoint = createEndpoint(zotero, { deadlineMs: 5 });
  const [, , body] = await new Endpoint().init(request());
  assert.equal(JSON.parse(body).state, "timeout");
  assert.equal((await new Endpoint().init(request()))[0], 409);
  release();
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(zotero.attachment.erased, 1);
  assert.equal(zotero.attachment.tags.length, 0);
  assert.equal(JSON.parse((await new Endpoint().init(request({ itemKey: "ZZZZ2345" })))[2]).state, "item-not-found");
});

test("the lock stays held through tagging and failure cleanup", async () => {
  for (const fails of [false, true]) {
    let releaseTag;
    let releaseCleanup;
    const item = makeItem({
      saveTx: () => new Promise((resolve, reject) => {
        releaseTag = () => fails ? reject(new Error("locked")) : resolve();
      }),
    });
    const zotero = makeZotero({ item });
    zotero.attachment.eraseTx = () => new Promise((resolve) => { releaseCleanup = resolve; });
    const Endpoint = createEndpoint(zotero);
    const first = new Endpoint().init(request());
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal((await new Endpoint().init(request()))[0], 409);
    releaseTag();
    if (fails) {
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal((await new Endpoint().init(request()))[0], 409);
      releaseCleanup();
    }
    assert.equal((await first)[0], fails ? 500 : 200);
    assert.equal(JSON.parse((await new Endpoint().init(request({ itemKey: "ZZZZ2345" })))[2]).state, "item-not-found");
  }
});

test("a failed late cleanup is logged and releases the lock", async () => {
  let release;
  const errors = [];
  const zotero = makeZotero({ search: (a) => new Promise((resolve) => { release = () => resolve(a); }) });
  zotero.logError = (error) => errors.push(error.message);
  zotero.attachment.eraseTx = async () => { throw new Error("cleanup failed"); };
  const Endpoint = createEndpoint(zotero, { deadlineMs: 5 });
  assert.equal(JSON.parse((await new Endpoint().init(request()))[2]).state, "timeout");
  release();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(errors, ["cleanup failed"]);
  assert.equal(JSON.parse((await new Endpoint().init(request({ itemKey: "ZZZZ2345" })))[2]).state, "item-not-found");
});
