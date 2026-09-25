#!/usr/bin/env python3
"""Stress-test the throwaway comment layer in headless Chrome.

    bin/test-local-comments.py

Builds a page with the layer inlined, drives it through the ways a reviewer
actually breaks it, and prints one line per case. Exit code 1 if any case fails.

The cases that matter are the ones where a note silently fails to save: the
passage being dropped while the editor is open, a double-click on Save, a
selection that crosses two paragraphs, and a save from the keyboard.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INJECT = os.path.join(HERE, "bin", "local-comments.py")

def chrome_binary():
    candidates = [os.environ.get("HC_TEST_CHROME"),
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  shutil.which("google-chrome"), shutil.which("chromium")]
    candidates += [str(p) for p in sorted(
        Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"),
        reverse=True)]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    sys.exit("Chrome/Chromium not found; set HC_TEST_CHROME to its executable")


CHROME = chrome_binary()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Fixture</title>
</head><body>
<p id="a">Alpha one two three four five six seven eight nine ten eleven.</p>
<p id="b">Beta with &lt;angle&gt; brackets &amp; an ampersand and "quotes" in it.</p>
<p id="c">Gamma paragraph for cross-boundary selections.</p>
<p id="d">The word Sokolov appears here, and the word Sokolov appears again.</p>
<input id="field" value="text inside a form field">
</body></html>"""

# Each case is a JS body that returns a string: "" for a pass, or the reason it
# failed. `H` is the overlay's own handle, `run` a helper that drives one note
# through the UI the way a mouse would.
CASES = [
("a comment saves and reaches the log", """
  comment('a', 0, 9, 'the comment body');
  var log = L.log();
  return log.length === 1 ? "" : log.length + " rows written";
"""),
("the export carries the quote", """
  comment('a', 0, 9, 'the comment body');
  var md = L.markdown();
  return md.indexOf('Anchor: "Alpha one"') > -1 ? "" : md;
"""),
("the export carries the surrounding context", """
  comment('a', 6, 9, 'a three-letter quote');
  var md = L.markdown();
  if (md.indexOf('Context:') === -1) return "no context line";
  return md.indexOf('[[one]]') > -1 ? "" : md;
"""),
("a one-word quote that repeats is still placed", """
  comment('d', 44, 51, 'the second Sokolov');
  var md = L.markdown();
  if (md.indexOf('Anchor: "Sokolov"') === -1) return "quote not recorded";
  return md.indexOf('again') > -1 || md.indexOf('the word') > -1
    ? "" : "context does not disambiguate the two occurrences";
"""),
("a suggested edit records both sides", """
  suggest('a', 0, 9, 'Alpha two', 'because');
  var md = L.markdown();
  return md.indexOf('Replace with: "Alpha two"') > -1 ? "" : md;
"""),
("clicking into the composer keeps the passage", """
  select('a', 0, 9);
  mouseup();
  click('.hc-toolbar .hc-tb-btn');
  mouseup(q('.hc-composer textarea'));
  q('.hc-composer textarea').value = 'typed after clicking the box';
  click('.hc-composer .hc-btn-primary');
  return L.log().length === 1 ? "" : "the passage was dropped by the click";
"""),
("an empty comment is not stored", """
  select('a', 0, 9);
  mouseup();
  click('.hc-toolbar .hc-tb-btn');
  click('.hc-composer .hc-btn-primary');
  if (L.log().length) return "stored an empty comment";
  return q('.hc-composer') ? "" : "the composer closed on an empty submit";
"""),
("submitting twice stores one comment", """
  select('a', 0, 9);
  mouseup();
  click('.hc-toolbar .hc-tb-btn');
  q('.hc-composer textarea').value = 'once only';
  var btn = q('.hc-composer .hc-btn-primary');
  btn.click(); btn.click();
  return L.log().length === 1 ? "" : L.log().length + " rows written";
"""),
("cmd+enter submits", """
  select('a', 0, 9);
  mouseup();
  click('.hc-toolbar .hc-tb-btn');
  var ta = q('.hc-composer textarea');
  ta.value = 'saved from the keyboard';
  ta.dispatchEvent(new KeyboardEvent('keydown',
    {key: 'Enter', metaKey: true, bubbles: true}));
  return L.log().length === 1 ? "" : "the keyboard submit did nothing";
"""),
("escape closes without storing", """
  select('a', 0, 9);
  mouseup();
  click('.hc-toolbar .hc-tb-btn');
  q('.hc-composer textarea').value = 'abandoned';
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  if (L.log().length) return "stored an abandoned comment";
  return q('.hc-composer') ? "the composer stayed open" : "";
"""),
("a selection across two paragraphs anchors", """
  var r = document.createRange();
  r.setStart(q('#b').firstChild, 0);
  r.setEnd(q('#c').firstChild, 5);
  var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  mouseup();
  if (!q('.hc-toolbar')) return "no toolbar for a cross-paragraph selection";
  click('.hc-toolbar .hc-tb-btn');
  q('.hc-composer textarea').value = 'spans two paragraphs';
  click('.hc-composer .hc-btn-primary');
  return L.log().length === 1 ? "" : "cross-boundary selection lost";
"""),
("selecting inside the overlay offers nothing", """
  comment('a', 0, 9, 'a note to select from later');
  var thread = q('.hc-list .hc-thread');
  if (!thread) return "the thread did not render";
  var r = document.createRange(); r.selectNodeContents(thread);
  var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  mouseup(thread);
  return q('.hc-toolbar') ? "offered to comment on its own panel" : "";
"""),
("angle brackets and quotes survive into the export", """
  comment('b', 0, 40, 'has <tags> & "quotes"');
  var md = L.markdown();
  return md.indexOf('<tags>') > -1 && md.indexOf('&amp;') === -1
    ? "" : "the export mangled the text";
"""),
("the log persists in localStorage", """
  comment('a', 0, 9, 'should persist');
  var kept = JSON.parse(localStorage.getItem('hc-local-fixture') || '[]');
  return kept.length === 1 ? "" : "localStorage holds " + kept.length;
"""),
("three comments in a row all save", """
  comment('a', 0, 5, 'note 0');
  comment('a', 6, 9, 'note 1');
  comment('a', 10, 13, 'note 2');
  return L.log().length === 3 ? "" : L.log().length + " of 3 saved";
"""),
("a comment on already highlighted text saves", """
  comment('a', 0, 9, 'first');
  var mark = q('.hc-hl') || q('mark');
  if (!mark) return "no highlight rendered";
  var r = document.createRange(); r.selectNodeContents(mark);
  var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  mouseup(mark);
  if (!q('.hc-toolbar')) return "no offer on already highlighted text";
  click('.hc-toolbar .hc-tb-btn');
  q('.hc-composer textarea').value = 'second, on the same words';
  click('.hc-composer .hc-btn-primary');
  return L.log().length === 2 ? "" : L.log().length + " of 2 saved";
"""),
("the export button is on the page", """
  return q('.hc-export') ? "" : "no export button in local mode";
"""),
("the export reflects edits to comments and replies", """
  var ts = '2026-09-25T10:00:00Z';
  var rows = [
    {itemId:'root', vote:'comment', ts:ts, note:JSON.stringify({kind:'comment', text:'old comment', anchor:{quote:'Alpha'}})},
    {itemId:'reply', vote:'reply', ts:ts, note:JSON.stringify({parentId:'root', text:'old reply'})},
    {itemId:'edit-root', vote:'edit', ts:ts, note:JSON.stringify({parentId:'root', text:'new comment'})},
    {itemId:'edit-reply', vote:'edit', ts:ts, note:JSON.stringify({parentId:'reply', text:'new reply'})}
  ];
  localStorage.setItem('hc-local-fixture', JSON.stringify(rows));
  var md = L.markdown();
  return md.includes('new comment') && md.includes('Reply: new reply') &&
    !md.includes('old comment') && !md.includes('old reply') ? '' : md;
"""),
("the export omits deleted replies and threads", """
  var ts = '2026-09-25T10:00:00Z';
  var rows = [
    {itemId:'root', vote:'comment', ts:ts, note:JSON.stringify({kind:'comment', text:'keep', anchor:{quote:'Alpha'}})},
    {itemId:'reply', vote:'reply', ts:ts, note:JSON.stringify({parentId:'root', text:'remove this reply'})},
    {itemId:'delete-reply', vote:'delete', ts:ts, note:JSON.stringify({parentId:'reply'})}
  ];
  localStorage.setItem('hc-local-fixture', JSON.stringify(rows));
  if (L.markdown().includes('remove this reply')) return 'deleted reply remains';
  rows.push({itemId:'delete-root', vote:'delete', ts:ts, note:JSON.stringify({parentId:'root'})});
  localStorage.setItem('hc-local-fixture', JSON.stringify(rows));
  return L.markdown().includes('(no comments)') ? '' : 'deleted thread remains';
"""),
("nothing is posted to a network", """
  comment('a', 0, 9, 'stays here');
  return window.__hcFetched ? "the overlay called fetch" : "";
"""),
]

HARNESS = """
<script>
window.addEventListener("load", function () {
  var L = window.__hcLocal;
  function q(sel) { return document.querySelector(sel); }
  function click(sel) { var el = q(sel); if (el) el.click(); }
  function mouseup(target) {
    /* the overlay listens for pointerup, captured on the document */
    (target || document.body).dispatchEvent(
      new PointerEvent("pointerup", {bubbles: true}));
    flush();
  }
  function select(id, from, to) {
    var root = q('#' + id);
    var walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var r = document.createRange(), seen = 0, node, started = false;
    while ((node = walk.nextNode())) {
      var len = node.length;
      if (!started && seen + len > from) { r.setStart(node, from - seen); started = true; }
      if (started && seen + len >= to) {
        r.setEnd(node, to - seen);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        return;
      }
      seen += len;
    }
    throw new Error("offset " + to + " past the end of #" + id);
  }
  function comment(id, from, to, text) {
    select(id, from, to); mouseup();
    if (!q('.hc-toolbar')) throw new Error("no toolbar after selecting");
    click('.hc-toolbar .hc-tb-btn');
    q('.hc-composer textarea').value = text;
    click('.hc-composer .hc-btn-primary');
  }
  function suggest(id, from, to, replacement, text) {
    select(id, from, to); mouseup();
    click('.hc-toolbar .hc-tb-btn:last-child');
    var areas = document.querySelectorAll('.hc-composer textarea');
    areas[0].value = replacement;
    if (areas[1]) areas[1].value = text;
    click('.hc-composer .hc-btn-primary');
  }
  /* the overlay defers its selection read; drain that so a case reads straight */
  var queued = [];
  var realTimeout = window.setTimeout;
  window.setTimeout = function (fn, ms) {
    if (!ms || ms <= 20) { queued.push(fn); return 0; }
    return realTimeout.apply(window, arguments);
  };
  function flush() {
    for (var i = 0; i < 5 && queued.length; i++) {
      var batch = queued; queued = [];
      batch.forEach(function (f) { try { f(); } catch (e) {} });
    }
  }

  window.q = q; window.click = click; window.mouseup = mouseup;
  window.select = select; window.comment = comment; window.suggest = suggest;
  window.L = L;

  var results = [];
  CASES.forEach(function (c) {
    localStorage.removeItem('hc-local-fixture');
    if (window.__hcReset) window.__hcReset();
    var stale = document.querySelector('.hc-composer');
    if (stale) stale.remove();
    stale = document.querySelector('.hc-toolbar');
    if (stale) stale.remove();
    window.getSelection().removeAllRanges();
    flush();
    var why;
    try { why = c.body(); } catch (e) { why = "threw: " + e.message; }
    results.push({name: c.name, why: why || ""});
  });
  var out = document.createElement("pre");
  out.id = "results";
  out.textContent = JSON.stringify(results);
  document.body.appendChild(out);
});
</script>
"""


def main():
    tmp = tempfile.mkdtemp(prefix="hcl-test-")
    src = os.path.join(tmp, "fixture.html")
    open(src, "w", encoding="utf-8").write(PAGE)
    out = subprocess.run([sys.executable, INJECT, src, "-o",
                          os.path.join(tmp, "driven.html")],
                         capture_output=True, text=True)
    if out.returncode:
        sys.exit("could not inject the layer:\n" + out.stderr)
    driven = out.stdout.strip()

    cases = "var CASES = [" + ",".join(
        "{name: %s, body: function () {%s}}" % (json.dumps(n), b)
        for n, b in CASES) + "];"
    doc = open(driven, encoding="utf-8").read()
    doc = doc.replace("</body>", "<script>" + cases + "</script>" +
                      HARNESS.replace("</script>", "<\\/script>")
                             .replace("<\\/script>", "</script>") + "</body>")
    open(driven, "w", encoding="utf-8").write(doc)

    chrome_args = [CHROME, "--headless", "--disable-gpu", "--dump-dom",
                   "--virtual-time-budget=6000", "file://" + driven]
    if sys.platform.startswith("linux"):
        # The box does not enable unprivileged Chromium namespaces. This test
        # opens only its generated local fixture.
        chrome_args.insert(2, "--no-sandbox")
    proc = subprocess.run(
        chrome_args,
        capture_output=True, text=True)
    found = re.search(r'<pre id="results">(.*?)</pre>', proc.stdout, re.S)
    if not found:
        sys.exit("the harness produced no results (Chrome exit " +
                 str(proc.returncode) + "): " + proc.stderr[:500])
    import html as htmlmod
    results = json.loads(htmlmod.unescape(found.group(1)))

    bad = 0
    for r in results:
        if r["why"]:
            bad += 1
            print(f"  FAIL  {r['name']}: {r['why']}")
        else:
            print(f"  ok    {r['name']}")
    print(f"\n{len(results) - bad} of {len(results)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
