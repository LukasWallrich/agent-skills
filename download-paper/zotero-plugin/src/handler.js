/*
 * POST /find-pdf-api/v1/find  {"itemKey": "ABCD2345"}
 *
 * Runs Zotero's Find Available PDF for one item of the personal library and reports
 * what happened. Callers choose only the item: the lookup methods are fixed here, and
 * "custom" uses whatever the user has configured in Zotero.
 *
 * Zotero is injected so that the tests can run this file without Zotero.
 */
var FindPdfApi = (function () {
  const PATH = "/find-pdf-api/v1/find";
  const METHODS = Object.freeze(["doi", "url", "oa", "custom"]);
  const TAG = "agent-added";
  // The caller allows longer than this, so it always hears the outcome.
  const DEADLINE_MS = 240000;
  const ITEM_KEY = /^[A-Z0-9]{8}$/;

  function json(status, body) {
    return [status, "application/json", JSON.stringify(body)];
  }

  function missingInternals(Zotero) {
    const root = Zotero.Server?.LocalAPI?.Root?.prototype;
    return !(
      typeof root?._authenticateWriteRequest === "function"
      && typeof root?._validateServerID === "function"
      && typeof Zotero.Attachments?.getFileResolvers === "function"
      && typeof Zotero.Attachments?.addFileFromURLs === "function"
      && typeof Zotero.Attachments?.canFindFileForItem === "function"
    );
  }

  /** Zotero's own write check: server ID, then a key from /api/local/authorize. */
  async function refusal(Zotero, request) {
    const check = new Zotero.Server.LocalAPI.Root();
    const auth = {
      method: request.method,
      headers: { get: (name) => request.headers[name] ?? null },
      searchParams: { get: () => null }, // a key belongs in a header, not a URL
    };
    return check._validateServerID(auth) || (await check._authenticateWriteRequest(auth));
  }

  async function tagFound(item, attachment) {
    attachment.addTag(TAG);
    await attachment.saveTx();
    const itemTagged = !item.hasTag(TAG);
    if (itemTagged) {
      item.addTag(TAG);
      await item.saveTx();
    }
    return itemTagged;
  }

  function createEndpoint(Zotero, { version = "", deadlineMs = DEADLINE_MS } = {}) {
    let busy = false;

    async function lookup(itemKey) {
      const item = await Zotero.Items.getByLibraryAndKeyAsync(Zotero.Libraries.userLibraryID, itemKey);
      if (!item) return json(200, { state: "item-not-found", version });
      await item.loadAllData();
      if (item.deleted || !Zotero.Attachments.canFindFileForItem(item)) {
        return json(200, { state: "not-eligible", version });
      }

      const tried = [];
      const resolvers = Zotero.Attachments.getFileResolvers(item, [...METHODS]);
      let timedOut = false;
      const search = Zotero.Attachments.addFileFromURLs(item, resolvers, {
        onAccessMethodStart: (method) => tried.push(method),
      }).then(async (attachment) => {
        // Nobody is left to check a file that arrives after the caller was told to give up.
        if (timedOut && attachment) await attachment.eraseTx();
        return attachment;
      });
      const deadline = Zotero.Promise.delay(deadlineMs).then(() => {
        timedOut = true;
        return "timeout";
      });

      const finished = search.catch((error) => {
        // The request handler logs ordinary failures; late failures have no caller left.
        if (timedOut) Zotero.logError(error);
      });
      const attachment = await Promise.race([search, deadline]);
      if (attachment === "timeout") {
        // Only a timeout hands lock ownership to the background search. Otherwise the
        // request handler keeps it through tagging and any failure cleanup.
        return {
          reply: json(200, { state: "timeout", methodsTried: tried, version }),
          finished: finished.then(() => { busy = false; }),
        };
      }
      await finished;
      if (!attachment) return json(200, { state: "none", methodsTried: tried, version });

      let itemTagged;
      try {
        itemTagged = await tagFound(item, attachment);
      }
      catch (e) {
        await attachment.eraseTx();
        throw e;
      }
      return json(200, {
        state: "found",
        attachmentKey: attachment.key,
        contentType: attachment.attachmentContentType,
        itemTagged,
        methodsTried: tried,
        version,
      });
    }

    async function handle(request) {
      if (request.headers.origin) return json(403, { state: "forbidden", version });
      if (missingInternals(Zotero)) return json(501, { state: "unsupported", version });
      if (!Zotero.Prefs.get("httpServer.localAPI.enabled")) {
        return json(403, { state: "local-api-off", version });
      }
      const refused = await refusal(Zotero, request);
      if (refused) return refused;

      const body = request.data;
      if (!body || typeof body !== "object" || Array.isArray(body)
          || Object.keys(body).length !== 1
          || typeof body.itemKey !== "string" || !ITEM_KEY.test(body.itemKey)) {
        return json(400, { state: "bad-request", version });
      }
      if (busy) return json(409, { state: "busy", version });

      busy = true;
      let holdsLock = true;
      try {
        const result = await lookup(body.itemKey);
        if (Array.isArray(result)) return result;
        holdsLock = false; // the running search releases it
        return result.reply;
      }
      catch (e) {
        Zotero.logError(e);
        return json(500, { state: "error", message: String(e?.message || e).slice(0, 200), version });
      }
      finally {
        if (holdsLock) busy = false;
      }
    }

    return class FindPdfEndpoint {
      supportedMethods = ["POST"];
      supportedDataTypes = ["application/json"];

      // One declared parameter selects the server's single-argument calling convention.
      init(request) {
        return handle(request);
      }
    };
  }

  return { PATH, METHODS, TAG, createEndpoint };
})();

if (typeof module !== "undefined") module.exports = FindPdfApi;
