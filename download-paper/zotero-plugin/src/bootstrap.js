/* Registers the Find PDF endpoint on Zotero's local HTTP server. See handler.js. */

var endpointPath;

function install() {}

async function startup({ version, rootURI }) {
  await Zotero.initializationPromise;
  const scope = {};
  Services.scriptloader.loadSubScript(rootURI + "handler.js", scope);
  endpointPath = scope.FindPdfApi.PATH;
  Zotero.Server.Endpoints[endpointPath] = scope.FindPdfApi.createEndpoint(Zotero, { version });
  Zotero.debug(`Find PDF API ${version}: listening at ${endpointPath}`);
}

function shutdown() {
  if (endpointPath && Zotero.Server?.Endpoints) {
    delete Zotero.Server.Endpoints[endpointPath];
  }
}

function uninstall() {}
