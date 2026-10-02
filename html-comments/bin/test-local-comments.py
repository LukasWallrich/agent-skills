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
<p id="e">Some <em>emphasised words</em> in a
   line that wraps in the source.</p>
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
("edit mode is off by default and leaves no editable text when switched off", """
  if (q('#a').isContentEditable) return "page editable before Edit mode";
  click('.hc-edit-toggle');
  if (!q('#a').isContentEditable) return "Edit mode did not make #a editable";
  if (q('#hc-root').isContentEditable) return "the overlay UI became editable";
  click('.hc-edit-toggle');
  if (document.querySelectorAll('[contenteditable]').length) return "contenteditable left behind";
  comment('a', 0, 9, 'normal comment after Edit mode');
  return L.log().length === 1 ? "" : "commenting broke after Edit mode";
"""),
("edit mode: replacing a word stores one suggestion", """
  edit('a', function (b) { overtype(b, 'three', '3'); });
  var log = L.log();
  if (log.length !== 1) return log.length + " rows written";
  var n = JSON.parse(log[0].note);
  if (log[0].vote !== 'suggestion' || n.kind !== 'suggestion' || n.via !== 'edit') return "wrong record type";
  if (n.anchor.quote !== 'three' || n.replacement !== '3') return JSON.stringify([n.anchor.quote, n.replacement]);
  if (!n.anchor.prefix || !n.anchor.suffix) return "no context recorded";
  return tracked() === 'del:three|ins:3' ? "" : tracked();
"""),
("edit mode: deleting a phrase", """
  edit('a', function (b) { overtype(b, 'four five ', ''); });
  var n = JSON.parse(L.log()[0].note);
  if (n.anchor.quote !== 'four five' || n.replacement !== '') return JSON.stringify([n.anchor.quote, n.replacement]);
  return tracked() === 'del:four five|ins:' ? "" : tracked();
"""),
("edit mode: inserting text anchors on the word before it", """
  edit('a', function (b) { insertAfter(b, 'two', ' and a half'); });
  var n = JSON.parse(L.log()[0].note);
  if (n.anchor.quote !== 'two' || n.replacement !== 'two and a half') return JSON.stringify([n.anchor.quote, n.replacement]);
  if (q('mark.hc-del')) return "the anchor word is struck through";
  return tracked() === 'ins: and a half' ? "" : tracked();
"""),
("edit mode: inserting at the start of a block anchors on the word after it", """
  edit('c', function (b) {
    var t = b.firstChild, r = document.createRange();
    r.setStart(t, 0); r.collapse(true);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    if (!document.execCommand('insertText', false, 'New ')) throw new Error('execCommand refused');
  });
  var n = JSON.parse(L.log()[0].note);
  if (n.anchor.quote !== 'Gamma' || n.replacement !== 'New Gamma') return JSON.stringify([n.anchor.quote, n.replacement]);
  if (q('mark.hc-del')) return "the anchor word is struck through";
  if (tracked() !== 'ins:New ') return tracked();
  if (q('#c').firstChild !== q('#c .hc-ins')) return "insertion not placed before the word";
  click('.hc-edit-toggle');
  var rows = L.log();
  window.__hcReset();
  window.__hcInjectRows(rows);
  return tracked() === 'ins:New ' && q('#c').firstChild === q('#c .hc-ins') ? "" : "after reload: " + tracked();
"""),
("edit mode: two edits in one paragraph, then a third later", """
  edit('a', function (b) { overtype(b, 'one', 'uno'); overtype(b, 'nine', 'nueve'); });
  if (L.log().length !== 2) return L.log().length + " rows after the first edit";
  var first = L.log().map(function (r) { return r.itemId; }).join();
  edit('a', function (b) { overtype(b, 'Alpha', 'Omega'); });
  var log = L.log();
  if (log.length !== 3) return log.length + " rows after the second edit";
  if (log.slice(0, 2).map(function (r) { return r.itemId; }).join() !== first) return "earlier suggestions were replaced";
  return tracked() === 'del:Alpha|ins:Omega|del:one|ins:uno|del:nine|ins:nueve' ? "" : tracked();
"""),
("edit mode: re-editing a change updates it instead of adding one", """
  edit('a', function (b) { overtype(b, 'three', '3'); });
  edit('a', function (b) { overtype(b, '3', 'THREE'); });
  var log = L.log();
  if (log.length !== 2 || log[1].vote !== 'edit') return log.map(function (r) { return r.vote; }).join();
  if (tracked() !== 'del:three|ins:THREE') return tracked();
  var md = L.markdown();
  return md.indexOf('Replace with: "THREE"') > -1 && md.indexOf('"3"') === -1 ? "" : md;
"""),
("edit mode: typing the original back withdraws the suggestion", """
  edit('a', function (b) { overtype(b, 'three', '3'); });
  edit('a', function (b) { overtype(b, '3', 'three'); });
  if (q('.hc-ins') || q('mark.hc-del')) return "tracked change still shown: " + tracked();
  return L.markdown().indexOf('(no comments)') > -1 ? "" : L.markdown();
"""),
("edit mode: focus and blur without typing posts nothing", """
  edit('a', function () {});
  return L.log().length ? L.log().length + " rows written" : "";
"""),
("edit mode: escape abandons the edit", """
  click('.hc-edit-toggle');
  var b = q('#a'); focusBlock(b);
  overtype(b, 'three', '3');
  b.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  if (__hcState().editing) blurBlock(b);
  if (L.log().length) return "stored an abandoned edit";
  return q('#a').textContent.indexOf('three') > -1 ? "" : "the typed text stayed on the page";
"""),
("edit mode: inline formatting survives an edit", """
  edit('e', function (b) { overtype(b, 'emphasised', 'stressed'); });
  var n = JSON.parse(L.log()[0].note);
  if (n.anchor.quote !== 'emphasised' || n.replacement !== 'stressed') return JSON.stringify([n.anchor.quote, n.replacement]);
  var em = q('#e em');
  if (!em) return "the <em> was lost";
  return em.querySelector('mark.hc-del') && em.querySelector('.hc-ins') ? "" : q('#e').innerHTML;
"""),
("edit mode: reload shows the same tracked changes", """
  edit('a', function (b) { overtype(b, 'three', '3'); overtype(b, 'seven eight ', ''); insertAfter(b, 'eleven', ' more'); });
  var before = tracked();
  if (before.split('|').length < 3) return "edits not rendered: " + before;
  click('.hc-edit-toggle');
  var rows = L.log();
  window.__hcReset();
  if (q('.hc-ins')) return "reset left tracked changes";
  window.__hcInjectRows(rows);
  return tracked() === before ? "" : before + " vs " + tracked();
"""),
("edit mode: a re-render arriving mid-edit does not lose the typing", """
  edit('a', function (b) {
    overtype(b, 'three', '3');
    window.__hcInjectRows(L.log());  /* what a background refresh does */
    overtype(b, 'ten', '10');
  });
  return tracked() === 'del:three|ins:3|del:ten|ins:10' ? "" : tracked();
"""),
("edit mode: the export carries the suggestions", """
  edit('a', function (b) { overtype(b, 'three', '3'); insertAfter(b, 'six', ' and a half'); });
  var md = L.markdown();
  return md.indexOf('Anchor: "three"') > -1 && md.indexOf('Replace with: "3"') > -1 &&
    md.indexOf('Replace with: "six and a half"') > -1 && md.indexOf('Context:') > -1 ? "" : md;
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

  /* Edit mode: focus a block, change its text the way typing would, blur. */
  function focusBlock(b) {
    b.focus();
    if (!window.__hcState().editing) b.dispatchEvent(new FocusEvent('focusin', {bubbles: true}));
  }
  function blurBlock(b) {
    b.blur();
    if (window.__hcState().editing) b.dispatchEvent(new FocusEvent('focusout', {bubbles: true}));
  }
  function editableNodes(b) {
    var out = [], w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) {
      var p = n.parentNode, ok = true;
      for (; p && p !== b; p = p.parentNode) if (p.getAttribute('contenteditable') === 'false') ok = false;
      if (ok) out.push(n);
    }
    return out;
  }
  function selectText(b, word, collapseToEnd) {
    var nodes = editableNodes(b);
    for (var i = 0; i < nodes.length; i++) {
      var k = nodes[i].nodeValue.indexOf(word);
      if (k < 0) continue;
      var r = document.createRange();
      r.setStart(nodes[i], k); r.setEnd(nodes[i], k + word.length);
      if (collapseToEnd) r.collapse(false);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      return nodes[i];
    }
    throw new Error('"' + word + '" not found in editable text');
  }
  function overtype(b, word, text) {
    selectText(b, word, false);
    var ok = text ? document.execCommand('insertText', false, text)
                  : document.execCommand('delete');
    if (!ok) throw new Error('execCommand refused');
  }
  function insertAfter(b, word, text) {
    selectText(b, word, true);
    if (!document.execCommand('insertText', false, text)) throw new Error('execCommand refused');
  }
  function edit(id, fn) {
    if (!window.__hcState().editMode) click('.hc-edit-toggle');
    if (!window.__hcState().editMode) throw new Error('Edit mode did not switch on');
    var b = q('#' + id);
    focusBlock(b);
    if (!window.__hcState().editing) throw new Error('focusing #' + id + ' did not start an edit');
    fn(b);
    blurBlock(b);
    if (window.__hcState().editing) throw new Error('blur did not commit');
  }
  /* The tracked changes on the page, in document order. */
  function tracked() {
    return Array.prototype.map.call(document.querySelectorAll('mark.hc-del, .hc-ins'), function (n) {
      return (n.classList.contains('hc-ins') ? 'ins:' : 'del:') + n.textContent;
    }).join('|');
  }
  window.focusBlock = focusBlock; window.blurBlock = blurBlock; window.edit = edit;
  window.overtype = overtype; window.insertAfter = insertAfter; window.tracked = tracked;

  window.q = q; window.click = click; window.mouseup = mouseup;
  window.select = select; window.comment = comment; window.suggest = suggest;
  window.L = L;

  var results = [];
  CASES.forEach(function (c) {
    if (window.__hcState().editMode) click('.hc-edit-toggle');
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
