/**
 * Comment-collection endpoint for the html-comments overlay.
 *
 * ONE workbook, MANY documents: every document writes to its own sheet/tab,
 * named by the `project` field in each request.
 *
 * Contract (matches the gsheet-collect-endpoint skill, with fixes):
 *   POST  JSON body sent as Content-Type: text/plain;charset=utf-8
 *         {project, itemId, vote, note, voter, session}   -> {"ok":true, written:1}
 *         {records:[{...}, ...]}  or  [{...}, ...]        -> {"ok":true, written:N}
 *         A batch may span projects; records are grouped and written one range per tab.
 *         The overlay posts single records; resolve.py posts batches.
 *         Missing project or unparseable body -> {"ok":false, error} (no row written;
 *         the original FORRT endpoint silently appended a blank row to a 'default' tab).
 *         {action:'claim', project, url}                  -> {"ok":true, claimed:bool, url, claimedAt}
 *         Claiming reserves a slug in the _slugs registry. A tab only appears once
 *         someone comments, so the tab list alone cannot tell whether a slug is
 *         already spoken for by a deployed-but-uncommented page.
 *   GET   ?action=ping                     -> {"ok":true, service, ts}
 *         ?action=rows&project=<slug>      -> {"ok":true, rows:[...]} oldest first
 *         ?action=projects                 -> {"ok":true, projects:[tab names]} (excludes _slugs)
 *         ?action=slugs                    -> {"ok":true, slugs:[{slug, url, claimedAt}]}
 *
 * Tab names are sanitised identically on read and write (the original endpoint
 * sanitised only on write, so slugs with \ / ? * [ ] : wrote to one tab and read
 * another). Keep slugs to [a-z0-9-] anyway.
 */

var SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID';
var HEADERS = ['timestamp', 'project', 'itemId', 'vote', 'note', 'voter', 'session', 'userAgent'];
var SLUG_TAB = '_slugs';
var SLUG_HEADERS = ['slug', 'url', 'claimedAt'];

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

function rowFrom_(rec, now) {
  return [
    now,
    rec.project || '',
    rec.itemId || '',
    rec.vote || '',
    rec.note || '',
    rec.voter || '',
    rec.session || '',
    rec.userAgent || ''
  ];
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
    if (body && body.action === 'claim') return claimSlug_(body);

    // One record or many: {..}, [{..}, ..], or {records:[{..}, ..]}.
    var records = body && body.records ? body.records : body;
    if (!records) return json_({ ok: false, error: 'no records' });
    if (!(records instanceof Array)) records = [records];
    if (!records.length) return json_({ ok: false, error: 'no records' });
    for (var i = 0; i < records.length; i++) {
      if (!records[i] || !records[i].project) {
        return json_({ ok: false, error: 'project required on every record' });
      }
    }

    // Group by tab so a batch spanning projects still costs one write per tab.
    var now = new Date();
    var byTab = {}, order = [];
    for (var k = 0; k < records.length; k++) {
      var tab = tabName_(records[k].project);
      if (!byTab[tab]) { byTab[tab] = []; order.push(tab); }
      byTab[tab].push(rowFrom_(records[k], now));
    }

    var ss = getSpreadsheet_();
    var written = 0;
    for (var t = 0; t < order.length; t++) {
      var name = order[t], rows = byTab[name];
      var sh = ss.getSheetByName(name);
      if (!sh) {
        sh = ss.insertSheet(name);
        sh.appendRow(HEADERS);
        sh.setFrozenRows(1);
      }
      sh.getRange(sh.getLastRow() + 1, 1, rows.length, HEADERS.length).setValues(rows);
      written += rows.length;
    }
    return json_({ ok: true, written: written });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (e2) {}
  }
}

function slugSheet_(ss) {
  var sh = ss.getSheetByName(SLUG_TAB);
  if (!sh) {
    sh = ss.insertSheet(SLUG_TAB);
    sh.appendRow(SLUG_HEADERS);
    sh.setFrozenRows(1);
  }
  return sh;
}

function claimSlug_(body) {
  var slug = String(body.project || '').trim();
  if (!slug) return json_({ ok: false, error: 'project required' });
  var sh = slugSheet_(getSpreadsheet_());
  var data = sh.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) === slug) {
      // Already claimed: report the holder rather than overwriting it.
      return json_({
        ok: true, claimed: false, slug: slug,
        url: String(data[i][1] || ''),
        claimedAt: data[i][2] instanceof Date ? data[i][2].toISOString() : String(data[i][2] || '')
      });
    }
  }
  sh.appendRow([slug, String(body.url || ''), new Date()]);
  return json_({ ok: true, claimed: true, slug: slug, url: String(body.url || '') });
}

function doGet(e) {
  var action = (e && e.parameter && e.parameter.action) || 'ping';
  if (action === 'ping') return json_({ ok: true, service: 'html-comments', ts: new Date() });
  if (action === 'projects') {
    var sheets = getSpreadsheet_().getSheets();
    var names = [];
    for (var i = 0; i < sheets.length; i++) {
      if (sheets[i].getName() !== SLUG_TAB) names.push(sheets[i].getName());
    }
    return json_({ ok: true, projects: names });
  }
  if (action === 'slugs') {
    var sh0 = getSpreadsheet_().getSheetByName(SLUG_TAB);
    if (!sh0) return json_({ ok: true, slugs: [] });
    var d = sh0.getDataRange().getValues();
    var slugs = [];
    for (var m = 1; m < d.length; m++) {
      slugs.push({
        slug: String(d[m][0] || ''),
        url: String(d[m][1] || ''),
        claimedAt: d[m][2] instanceof Date ? d[m][2].toISOString() : String(d[m][2] || '')
      });
    }
    return json_({ ok: true, slugs: slugs });
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
