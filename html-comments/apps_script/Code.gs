/**
 * Comment-collection endpoint for the html-comments overlay.
 *
 * ONE workbook, MANY documents: every document writes to its own sheet/tab,
 * named by the `project` field in each request.
 *
 * Contract (matches the gsheet-collect-endpoint skill, with fixes):
 *   POST  JSON body sent as Content-Type: text/plain;charset=utf-8
 *         {project, itemId, vote, note, voter, session} -> {"ok":true}
 *         Missing project or unparseable body -> {"ok":false, error} (no row written;
 *         the original FORRT endpoint silently appended a blank row to a 'default' tab).
 *   GET   ?action=ping                     -> {"ok":true, service, ts}
 *         ?action=rows&project=<slug>      -> {"ok":true, rows:[...]} oldest first
 *         ?action=projects                 -> {"ok":true, projects:[tab names]}
 *
 * Tab names are sanitised identically on read and write (the original endpoint
 * sanitised only on write, so slugs with \ / ? * [ ] : wrote to one tab and read
 * another). Keep slugs to [a-z0-9-] anyway.
 */

var SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID';
var HEADERS = ['timestamp', 'project', 'itemId', 'vote', 'note', 'voter', 'session', 'userAgent'];

function getSpreadsheet_() {
  return SpreadsheetApp.openById(SPREADSHEET_ID);
}

function tabName_(project) {
  return String(project).substring(0, 90).replace(/[\\\/\?\*\[\]:]/g, '_');
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
    var body;
    try {
      body = JSON.parse((e && e.postData && e.postData.contents) || '');
    } catch (err) {
      return json_({ ok: false, error: 'body must be JSON (sent as text/plain)' });
    }
    if (!body || !body.project) return json_({ ok: false, error: 'project required' });
    var ss = getSpreadsheet_();
    var name = tabName_(body.project);
    var sh = ss.getSheetByName(name);
    if (!sh) {
      sh = ss.insertSheet(name);
      sh.appendRow(HEADERS);
      sh.setFrozenRows(1);
    }
    sh.appendRow([
      new Date(),
      body.project || '',
      body.itemId || '',
      body.vote || '',
      body.note || '',
      body.voter || '',
      body.session || '',
      body.userAgent || ''
    ]);
    return json_({ ok: true });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (e2) {}
  }
}

function doGet(e) {
  var action = (e && e.parameter && e.parameter.action) || 'ping';
  if (action === 'ping') return json_({ ok: true, service: 'html-comments', ts: new Date() });
  if (action === 'projects') {
    var sheets = getSpreadsheet_().getSheets();
    var names = [];
    for (var i = 0; i < sheets.length; i++) names.push(sheets[i].getName());
    return json_({ ok: true, projects: names });
  }
  if (action === 'rows') {
    var project = e.parameter.project;
    if (!project) return json_({ ok: false, error: 'project required' });
    var sh = getSpreadsheet_().getSheetByName(tabName_(project));
    if (!sh) return json_({ ok: true, project: project, rows: [] });
    var data = sh.getDataRange().getValues();
    var out = [];
    for (var j = 1; j < data.length; j++) {
      out.push({
        ts: data[j][0] instanceof Date ? data[j][0].toISOString() : String(data[j][0]),
        itemId: String(data[j][2] || ''),
        vote: String(data[j][3] || ''),
        note: String(data[j][4] || ''),
        voter: String(data[j][5] || ''),
        session: String(data[j][6] || '')
      });
    }
    return json_({ ok: true, project: project, rows: out });
  }
  return json_({ ok: false, error: 'unknown action' });
}
