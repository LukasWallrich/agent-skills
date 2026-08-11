/* html-comments.js
 * Self-contained, dependency-free comment/suggestion overlay for static HTML
 * pages (e.g. Quarto reports on static hosts). Single IIFE, no build step.
 *
 * Backend: a Google Apps Script web app.
 *   WRITE: POST (text/plain) {project,itemId,vote,note,voter,session} -> {ok:true}
 *   READ : GET  ?action=rows&project=<p> -> {ok:true,rows:[...]}  (append-only log)
 *
 * All application data is stored in the `note` field as a JSON string; `vote`
 * is used as the record type (comment|suggestion|reply|resolve|reopen|delete).
 */
(function () {
  'use strict';

  var HC_VERSION = '2026-08-11.2';

  // Loading the overlay twice (e.g. a page that both inlines the script and
  // loads it from the asset host) would produce two sidebars, two toolbars and
  // duplicated posts. First one wins.
  if (window.__hcVersion) {
    console.warn('[html-comments] already loaded (version ' + window.__hcVersion +
      '); ignoring this duplicate inclusion.');
    return;
  }

  /* ------------------------------------------------------------------ *
   * 0. Config capture (currentScript is only valid at top-level exec)  *
   * ------------------------------------------------------------------ */
  // Config comes from the script tag's data-* attributes, or from a
  // window.HC_CONFIG object when the loader injects the script dynamically
  // (which is how pages avoid having a frozen copy inlined into them).
  var SCRIPT = document.currentScript;
  var CFG = (SCRIPT && SCRIPT.dataset && SCRIPT.dataset.project)
    ? SCRIPT.dataset
    : (window.HC_CONFIG || (SCRIPT ? SCRIPT.dataset : {}));
  var ENDPOINT = (CFG.endpoint || '').trim();
  var PROJECT = (CFG.project || '').trim();
  var DEBUG_ROWS = CFG.debugRows || ''; // optional inline test rows (JSON)

  var VIA_DATASET = !!(SCRIPT && SCRIPT.dataset && SCRIPT.dataset.project);

  // A misconfigured overlay used to fail silently in the console, which reads to
  // the author exactly like "the comment system is broken". Say what is wrong,
  // on the page, naming the config path actually in use.
  function configError(msg) {
    console.error('[html-comments] ' + msg);
    function paint() {
      var b = document.createElement('div');
      b.className = 'hc-config-error';
      b.textContent = 'html-comments: ' + msg;
      // Inline the essentials: the stylesheet may not be loaded either.
      b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
        'background:#b91c1c;color:#fff;padding:8px 14px;font:13px/1.4 system-ui,sans-serif;' +
        'text-align:center';
      if (document.body) document.body.appendChild(b);
    }
    if (document.body) paint();
    else document.addEventListener('DOMContentLoaded', paint);
  }

  var WHERE = VIA_DATASET
    ? 'the data-endpoint/data-project attributes on the script tag'
    : 'window.HC_CONFIG';

  if (!ENDPOINT || !PROJECT) {
    configError('missing ' + (!ENDPOINT && !PROJECT ? 'endpoint and project' :
      (!ENDPOINT ? 'endpoint' : 'project')) + ' in ' + WHERE + '; overlay disabled.');
    return;
  }
  if (PROJECT === 'UNIQUE-PROJECT-SLUG') {
    configError('replace the UNIQUE-PROJECT-SLUG placeholder with a real project slug in ' +
      WHERE + '; overlay disabled.');
    return;
  }
  if (!/^https?:\/\//.test(ENDPOINT)) {
    configError('endpoint must be a full http(s) URL (got "' + ENDPOINT + '") in ' +
      WHERE + '; overlay disabled.');
    return;
  }

  var LS = {
    name: 'hc-name',
    // Per-browser, not per-project: it is a reading preference, not document state.
    push: 'hc-push',
    outbox: 'hc-outbox-' + PROJECT,
    cache: 'hc-cache-' + PROJECT,
    showSug: 'hc-showsug-' + PROJECT,
    drafts: 'hc-drafts-' + PROJECT
  };

  /* ------------------------------------------------------------------ *
   * 1. Small utilities                                                 *
   * ------------------------------------------------------------------ */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function el(tag, props, kids) {
    var n = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        if (k === 'class') n.className = props[k];
        else if (k === 'text') n.textContent = props[k];
        else if (k === 'html') n.innerHTML = props[k]; // only used with pre-escaped strings
        else if (k.slice(0, 2) === 'on' && typeof props[k] === 'function') n.addEventListener(k.slice(2), props[k]);
        else if (k === 'dataset') Object.keys(props[k]).forEach(function (d) { n.dataset[d] = props[k][d]; });
        else if (props[k] != null) n.setAttribute(k, props[k]);
      });
    }
    (kids || []).forEach(function (c) {
      if (c == null) return;
      n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return n;
  }

  function genId() {
    return 'hc-' + Date.now().toString(36) + '-' +
      Math.random().toString(36).slice(2, 6);
  }

  // Storage access itself can throw (blocked cookies, some file:// setups,
  // private modes). Every read/write goes through these so a browser that
  // refuses storage costs the reviewer durability, not the whole overlay.
  function safeGet(store, key) {
    try {
      var s = window[store];
      return s ? s.getItem(key) : null;
    } catch (e) { return null; }
  }
  function safeSet(store, key, value) {
    try {
      var s = window[store];
      if (!s) return false;
      s.setItem(key, value);
      return true;
    } catch (e) {
      if (e && /quota/i.test(String(e.name || '') + String(e.message || ''))) {
        console.warn('[html-comments] storage full; ' + key + ' not saved:', e);
        showStorageNote('Local storage is full — offline copies may not be saved.');
      }
      return false;
    }
  }
  function safeRemove(store, key) {
    try {
      var s = window[store];
      if (s) s.removeItem(key);
    } catch (e) {}
  }

  function nowSession() {
    var s = safeGet('sessionStorage', 'hc-session');
    if (!s) { s = genId(); safeSet('sessionStorage', 'hc-session', s); }
    return s;
  }
  var SESSION = nowSession();

  function relTime(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return '';
    var d = Math.round((Date.now() - t) / 1000);
    if (d < 45) return 'just now';
    if (d < 90) return 'a minute ago';
    var m = Math.round(d / 60);
    if (m < 45) return m + ' min ago';
    var h = Math.round(m / 60);
    if (h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
    var days = Math.round(h / 24);
    if (days < 30) return days + (days === 1 ? ' day ago' : ' days ago');
    var mo = Math.round(days / 30);
    if (mo < 12) return mo + (mo === 1 ? ' month ago' : ' months ago');
    return Math.round(mo / 12) + ' yr ago';
  }

  function truncate(s, n) {
    s = String(s || '').replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function getName() { return safeGet('localStorage', LS.name) || ''; }
  function setName(v) {
    if (v) safeSet('localStorage', LS.name, v);
    else safeRemove('localStorage', LS.name);
  }

  // Unsubmitted reply drafts, keyed by thread id, so a re-render (or a stray
  // Resolve click, or closing the tab) never silently eats typed text.
  function readDrafts() {
    try { return JSON.parse(safeGet('localStorage', LS.drafts) || '{}'); }
    catch (e) { return {}; }
  }
  function getDraft(threadId) { return readDrafts()[threadId] || ''; }
  function setDraft(threadId, text) {
    var d = readDrafts();
    if (text) d[threadId] = text; else delete d[threadId];
    safeSet('localStorage', LS.drafts, JSON.stringify(d));
  }

  /* ------------------------------------------------------------------ *
   * 2. Networking + outbox (fire-and-forget with retry)               *
   * ------------------------------------------------------------------ */
  // Offline durability rests on two localStorage stores:
  //   outbox — records written here but not yet accepted by the server
  //   cache  — the last successful server read
  // The rendered log is always cache + outbox, so a reviewer working with no
  // network (on a plane, say) still sees the document's existing comments and
  // everything they add, across reloads, until the queue drains.
  function readOutbox() {
    try { return JSON.parse(safeGet('localStorage', LS.outbox) || '[]'); }
    catch (e) { return []; }
  }
  function writeOutbox(arr) {
    safeSet('localStorage', LS.outbox, JSON.stringify(arr));
    renderPending();
  }

  function readCache() {
    try { return JSON.parse(safeGet('localStorage', LS.cache) || '[]'); }
    catch (e) { return []; }
  }
  function writeCache(rows) {
    safeSet('localStorage', LS.cache, JSON.stringify(rows || []));
  }

  // The visible log: server rows plus anything still queued. itemIds are unique
  // per record, so deduping on them also absorbs a replayed post whose response
  // was lost (the server has the row; our outbox copy is the same record).
  function composeRows(serverRows) {
    var seen = {}, out = [];
    (serverRows || []).concat(readOutbox()).forEach(function (row) {
      if (!row || !row.itemId || seen[row.itemId]) return;
      seen[row.itemId] = true;
      out.push(row);
    });
    return out.sort(function (a, b) { return Date.parse(a.ts) - Date.parse(b.ts); });
  }

  var NET_TIMEOUT = 20000;   // ms; a hung request must not wedge refresh forever
  var FLUSH_SPACING = 1000;  // ms between queued posts (the endpoint rate-limits bursts)
  var KEEPALIVE_CAP = 60000; // chars; keepalive fetches are capped near 64KB

  // fetch + JSON parse under one abort timeout. The parse has to be inside the
  // timed window: a server that sends headers and then stalls on the body would
  // otherwise leave r.json() hanging forever, and with it the refreshing flag.
  function fetchJSON(url, opts) {
    var ctl = null;
    try { ctl = new AbortController(); } catch (e) {}
    if (ctl) opts.signal = ctl.signal;
    var timer = setTimeout(function () {
      if (ctl) try { ctl.abort(); } catch (e) {}
    }, NET_TIMEOUT);
    function clear(v) { clearTimeout(timer); return v; }
    function rethrow(e) { clearTimeout(timer); throw e; }
    return fetch(url, opts)
      .then(function (r) { return r.json(); })
      .then(clear, rethrow);
  }

  function rawPost(payload) {
    var body = JSON.stringify(payload);
    var opts = {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: body
    };
    // keepalive lets a post survive the page being closed, but browsers cap such
    // bodies near 64KB and throw outright above it — a long suggestion would
    // then fail before it ever left.
    if (body.length < KEEPALIVE_CAP) opts.keepalive = true;
    return fetchJSON(ENDPOINT, opts);
  }

  // Post a record; on network failure, queue it in the outbox for retry.
  function postRecord(payload) {
    return rawPost(payload).then(function (res) {
      if (!res || res.ok !== true) throw new Error('server rejected');
      return res;
    }).catch(function (err) {
      var box = readOutbox();
      box.push(payload);
      writeOutbox(box);
      console.warn('[html-comments] POST failed, queued in outbox:', err);
      return { ok: false, queued: true };
    });
  }

  // Drop the given itemIds from the outbox, re-reading it first. A record queued
  // by postRecord while a flush was in flight must survive: writing back a list
  // computed from the pre-flush snapshot would silently delete it.
  function dropFromOutbox(sentIds) {
    if (!sentIds.length) return;
    var keep = {};
    sentIds.forEach(function (id) { keep[id] = true; });
    writeOutbox(readOutbox().filter(function (p) {
      return !(p && p.itemId && keep[p.itemId]);
    }));
  }

  function flushOutbox() {
    var box = readOutbox();
    if (!box.length) return Promise.resolve(0);
    var sent = [];
    return box.reduce(function (chain, payload, i) {
      return chain.then(function () {
        // Space the replay out: the endpoint rate-limits bursts, and a queue
        // fired all at once comes back rejected and re-queues itself.
        return i === 0 ? null : new Promise(function (r) { setTimeout(r, FLUSH_SPACING); });
      }).then(function () {
        return rawPost(payload).then(function (res) {
          if (res && res.ok === true && payload.itemId) sent.push(payload.itemId);
        }).catch(function () {});
      });
    }, Promise.resolve()).then(function () {
      dropFromOutbox(sent);
      return sent.length;
    });
  }

  function fetchRows() {
    var url = ENDPOINT + (ENDPOINT.indexOf('?') >= 0 ? '&' : '?') +
      'action=rows&project=' + encodeURIComponent(PROJECT);
    return fetchJSON(url, { method: 'GET' });
  }

  /* ------------------------------------------------------------------ *
   * 3. Text index: concatenate a container's text nodes with a map     *
   *    back to (node, offset). Skips script/style and overlay UI so    *
   *    matching always runs against clean, original page text.         *
   * ------------------------------------------------------------------ */
  function isSkippable(node) {
    var p = node.parentNode;
    while (p && p.nodeType === 1) {
      var tag = p.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') return true;
      if (p.classList && (p.classList.contains('hc-ins') || p.classList.contains('hc-ui'))) return true;
      if (p.id === 'hc-root') return true;
      p = p.parentNode;
    }
    return false;
  }

  // Block-level elements whose boundaries get a '\n' in the index. Without one,
  // `<td>A</td><td>B</td>` reads as "AB" and a selection crossing the boundary
  // yields a quote that exists nowhere in the document. This list is
  // deliberately separate from BLOCK_TAGS (which picks the anchor's block
  // element and must not include generic containers).
  var SEP_TAGS = /^(P|LI|TD|TH|DD|DT|BLOCKQUOTE|FIGCAPTION|CAPTION|PRE|H1|H2|H3|H4|H5|H6|DIV|SECTION|ARTICLE)$/;

  function sepBlockOf(node) {
    var e = node.parentNode;
    while (e && e.nodeType === 1) {
      if (SEP_TAGS.test(e.tagName)) return e;
      e = e.parentNode;
    }
    return null;
  }

  function buildIndex(container) {
    var walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (isSkippable(n)) return NodeFilter.FILTER_REJECT;
        return n.nodeValue.length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var segs = [], text = '', node, prevBlock = null, first = true;
    while ((node = walker.nextNode())) {
      var blk = sepBlockOf(node);
      if (!first && blk !== prevBlock) text += '\n';
      first = false;
      prevBlock = blk;
      segs.push({ node: node, start: text.length, end: text.length + node.nodeValue.length });
      text += node.nodeValue;
    }
    return { text: text, segs: segs };
  }

  // Map a character offset in the concatenated text to (node, offset).
  function locate(index, charPos) {
    var segs = index.segs;
    for (var i = 0; i < segs.length; i++) {
      if (charPos >= segs[i].start && charPos <= segs[i].end) {
        return { node: segs[i].node, offset: charPos - segs[i].start };
      }
    }
    return null;
  }

  /* ------------------------------------------------------------------ *
   * 4. Anchoring: create anchors from a selection, re-find them later  *
   * ------------------------------------------------------------------ */
  // Context stored with every anchor. This is not only used to disambiguate
  // re-attachment in the browser: an agent applying suggestions back to the
  // source needs enough context to place a one-word change ("a" -> "the")
  // unambiguously, so store generously.
  var CTX = 120;       // chars of prefix/suffix
  var BLOCK_CAP = 700; // chars of the surrounding block element's text

  // Real text blocks. Generic containers are deliberately absent: a selection
  // spanning two paragraphs would otherwise capture a whole section as its
  // "block". They are used only as a fallback when nothing better exists.
  var BLOCK_TAGS = /^(P|LI|TD|TH|DD|DT|BLOCKQUOTE|FIGCAPTION|CAPTION|PRE|H1|H2|H3|H4|H5|H6)$/;
  var BLOCK_FALLBACK_TAGS = /^(DIV|SECTION|ARTICLE|BODY)$/;

  function nearestBlockEl(node) {
    var e = node.nodeType === 1 ? node : node.parentNode;
    var fallback = null;
    while (e && e.nodeType === 1) {
      if (BLOCK_TAGS.test(e.tagName)) return e;
      if (!fallback && BLOCK_FALLBACK_TAGS.test(e.tagName)) fallback = e;
      e = e.parentNode;
    }
    return fallback;
  }

  function collapse(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }

  // The block's text, windowed around the quote rather than truncated at the
  // head. A quote past char 700 used to be absent from its own block field,
  // which silently breaks source-matching downstream.
  function blockWindowText(blockEl, range) {
    if (!blockEl) return '';
    // Index text, not textContent: an agent matching this block against the
    // source file must not be handed a suggestion's replacement text spliced
    // into the middle of the document's own words.
    var idx = buildIndex(blockEl);
    var raw = idx.text;
    if (raw.length <= BLOCK_CAP) return collapse(raw);
    var s = boundaryPos(idx, range.startContainer, range.startOffset, true);
    var e = boundaryPos(idx, range.endContainer, range.endOffset, false);
    if (s == null || e == null || e < s) { s = 0; e = 0; }
    var qlen = e - s, from, to;
    if (qlen >= BLOCK_CAP) {
      // Quote longer than the cap: no window can hold it. Start at the quote.
      from = s;
      to = Math.min(raw.length, s + BLOCK_CAP);
    } else {
      var pad = Math.floor((BLOCK_CAP - qlen) / 2);
      from = Math.max(0, s - pad);
      to = Math.min(raw.length, from + BLOCK_CAP);
      from = Math.max(0, to - BLOCK_CAP);
    }
    var out = collapse(raw.slice(from, to));
    if (from > 0) out = '…' + out;
    if (to < raw.length) out = out + '…';
    return out;
  }

  // The heading chain in scope at a block: nearest preceding h1, then the
  // nearest h2 after it, and so on down to the block's own level.
  function headingChain(blockEl) {
    var chain = [];
    if (!blockEl || !blockEl.compareDocumentPosition) return chain;
    var hs = document.querySelectorAll('h1,h2,h3,h4,h5,h6');
    var before = [];
    for (var i = 0; i < hs.length; i++) {
      var h = hs[i];
      if (h.closest && h.closest('#hc-root')) continue;
      if (h === blockEl ||
        (blockEl.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_PRECEDING)) before.push(h);
    }
    var level = 7;
    for (var j = before.length - 1; j >= 0; j--) {
      var lv = parseInt(before[j].tagName.charAt(1), 10);
      if (lv < level) {
        chain.push(truncate(buildIndex(before[j]).text, 200));
        level = lv;
        if (lv === 1) break;
      }
    }
    return chain.reverse();
  }

  // Return the nearest id-bearing ancestor as an element, not just its id:
  // ids repeat in generated HTML, and getElementById would hand back the first
  // element with that id — usually the wrong one.
  function nearestIdEl(node) {
    var e = node.nodeType === 1 ? node : node.parentNode;
    while (e && e.nodeType === 1) {
      if (e.id && e.id !== 'hc-root') return { el: e, id: e.id };
      e = e.parentNode;
    }
    return { el: null, id: '' };
  }

  function containerFor(anchor) {
    if (anchor.containerId) {
      var c = document.getElementById(anchor.containerId);
      if (c) return c;
    }
    return document.body;
  }

  // A shown suggestion puts its replacement text on the page in a .hc-ins span.
  // That text is overlay, not document, so the text index ignores it — and a
  // selection touching it has no position in the index at all. Snap such
  // boundaries onto the original text the suggestion replaces, which is what a
  // comment on a suggested edit should anchor to anyway.
  // The range covering the original text that this .hc-ins replaces. Those
  // marks always sit immediately before it in the DOM (see applyHighlights).
  function insHostRange(insEl) {
    var id = insEl.getAttribute('data-hc-id');
    var entry = id && registry[id];
    var marks = entry && entry.marks;
    var r = document.createRange();
    try {
      if (marks && marks.length) {
        r.setStartBefore(marks[0]);
        r.setEndAfter(marks[marks.length - 1]);
      } else {
        r.selectNode(insEl);
      }
    } catch (e) { return null; }
    return r;
  }

  // Grow a selection so that every suggestion it touches contributes the
  // original text being replaced instead of the inserted replacement. A
  // selection lying wholly inside a replacement then anchors to the text that
  // replacement is proposed for, rather than to nothing.
  function normalizeRangeForAnchor(range) {
    var r;
    try { r = range.cloneRange(); } catch (e) { return range; }
    Array.prototype.forEach.call(document.querySelectorAll('.hc-ins'), function (ins) {
      var hits = false;
      try { hits = range.intersectsNode(ins); } catch (e) {}
      if (!hits) return;
      var hr = insHostRange(ins);
      if (!hr) return;
      try {
        if (r.compareBoundaryPoints(Range.START_TO_START, hr) > 0) r.setStart(hr.startContainer, hr.startOffset);
        if (r.compareBoundaryPoints(Range.END_TO_END, hr) < 0) r.setEnd(hr.endContainer, hr.endOffset);
      } catch (e) {}
    });
    return r;
  }

  // Build an anchor object from the current selection's Range.
  function anchorFromRange(range) {
    range = normalizeRangeForAnchor(range);
    var idNode = range.commonAncestorContainer;
    var found = nearestIdEl(idNode);
    var containerId = found.id;
    var container = found.el || document.body;
    var index = buildIndex(container);
    // Locate the selection's position to derive the quote and prefix/suffix
    // from container text. The quote must come from the index, never from
    // range.toString(): a selection may include a suggestion's inserted
    // replacement text, which no later search of the page can ever find.
    var occ = findOccurrenceOffsets(index, range);
    var bodyIndex = null, bodyOcc = null;
    function ensureBodyIndex() {
      if (!bodyIndex) {
        bodyIndex = container === document.body ? index : buildIndex(document.body);
        bodyOcc = container === document.body ? occ : findOccurrenceOffsets(bodyIndex, range);
      }
      return bodyIndex;
    }
    if (!occ && container !== document.body) {
      // Mapping against the container failed (a boundary outside it, say). The
      // whole-document index is a better answer than range.toString(), which
      // can carry overlay-injected replacement text.
      ensureBodyIndex();
      if (bodyOcc) { container = document.body; containerId = ''; index = bodyIndex; occ = bodyOcc; }
    }
    var quote = occ ? index.text.slice(occ.start, occ.end) : range.toString();
    var prefix = '', suffix = '', nth = null, total = null;
    if (occ && quote) {
      prefix = index.text.slice(Math.max(0, occ.start - CTX), occ.start);
      suffix = index.text.slice(occ.end, occ.end + CTX);
      var all = allOccurrences(index.text, quote);
      total = all.length;
      nth = all.indexOf(occ.start);
      if (nth < 0) nth = null;
    }
    var blockEl = nearestBlockEl(idNode);
    // Where in the document this sits, as a fraction. Survives re-generation of
    // the page better than any id, and lets a reader order orphaned anchors.
    var docPos = null;
    ensureBodyIndex();
    if (bodyOcc && bodyIndex.text.length) {
      docPos = Math.round((bodyOcc.start / bodyIndex.text.length) * 10000) / 10000;
    }
    return {
      quote: quote, prefix: prefix, suffix: suffix, containerId: containerId,
      // Which occurrence of `quote` this is within the container, and the whole
      // sentence/paragraph it sits in — both are for placing short, repeated
      // quotes ("a", "the") in a source file where prefix/suffix alone may not
      // survive markdown/rendering differences.
      nth: nth, total: total,
      block: blockWindowText(blockEl, range),
      blockTag: blockEl ? blockEl.tagName.toLowerCase() : '',
      headings: headingChain(blockEl),
      docPos: docPos
    };
  }

  // Map one range boundary to a char offset in the container index. A boundary
  // that is not itself an indexed text node — it sits on an element (selection
  // ending at a tag boundary) or inside skipped overlay text — is snapped
  // outward to the nearest indexed position rather than abandoned.
  function boundaryPos(index, node, offset, isStart) {
    var segs = index.segs, i;
    for (i = 0; i < segs.length; i++) {
      if (segs[i].node === node) {
        return segs[i].start + Math.min(offset, segs[i].node.nodeValue.length);
      }
    }
    var probe = document.createRange();
    try { probe.setStart(node, offset); probe.collapse(true); }
    catch (e) { return null; }
    try {
      if (isStart) {
        for (i = 0; i < segs.length; i++) {
          if (probe.comparePoint(segs[i].node, 0) >= 0) return segs[i].start;
        }
        return index.text.length;
      }
      for (i = segs.length - 1; i >= 0; i--) {
        if (probe.comparePoint(segs[i].node, segs[i].node.nodeValue.length) <= 0) return segs[i].end;
      }
      return 0;
    } catch (e) { return null; }
  }

  // Given the live range, find its char offsets within the container index.
  function findOccurrenceOffsets(index, range) {
    var startPos = boundaryPos(index, range.startContainer, range.startOffset, true);
    var endPos = boundaryPos(index, range.endContainer, range.endOffset, false);
    if (startPos == null || endPos == null || endPos < startPos) return null;
    return { start: startPos, end: endPos };
  }

  function allOccurrences(text, quote) {
    var out = [], from = 0, idx;
    if (!quote) return out;
    while ((idx = text.indexOf(quote, from)) !== -1) {
      out.push(idx);
      from = idx + 1;
    }
    return out;
  }

  function tailMatchLen(a, b) {
    // How many trailing chars of a equal the trailing chars of b.
    var n = 0, i = a.length - 1, j = b.length - 1;
    while (i >= 0 && j >= 0 && a[i] === b[j]) { n++; i--; j--; }
    return n;
  }
  function headMatchLen(a, b) {
    var n = 0, i = 0;
    while (i < a.length && i < b.length && a[i] === b[i]) { n++; i++; }
    return n;
  }

  // Locate the span between an anchor's recorded prefix and suffix. Used only
  // when the quote itself cannot be found. A prefix that occurs more than once
  // with a plausible suffix after it is treated as unplaceable rather than
  // guessed at — a highlight in the wrong paragraph is worse than none.
  function offsetsFromContext(index, anchor) {
    var pre = anchor.prefix || '', suf = anchor.suffix || '';
    // A suffix is what bounds the end; without one there is nothing to stop the
    // span running to the end of the container. Short contexts are allowed as
    // long as the pair turns out to be unique (checked below) — a selection at
    // the very start of a paragraph legitimately has almost no prefix.
    if (!suf || pre.length + suf.length < 16) return null;
    var cap = Math.max(40, (anchor.quote || '').length * 2 + 40);
    var starts = [], from = 0, p;
    if (!pre) starts.push(0);
    else while ((p = index.text.indexOf(pre, from)) !== -1) {
      starts.push(p + pre.length);
      from = p + 1;
    }
    var best = null;
    for (var i = 0; i < starts.length; i++) {
      var s = starts[i], q = index.text.indexOf(suf, s);
      if (q === -1 || q - s > cap) continue;
      if (best) return null; // ambiguous
      best = { start: s, end: q };
    }
    return best;
  }

  // Re-attach an anchor: return a live Range or null (orphaned).
  function rangeFromAnchor(anchor) {
    if (!anchor || !anchor.quote) return null;
    var container = containerFor(anchor);
    var index = buildIndex(container);
    var occ = allOccurrences(index.text, anchor.quote);
    if (!occ.length && container !== document.body) {
      // Fall back to full document body.
      container = document.body;
      index = buildIndex(container);
      occ = allOccurrences(index.text, anchor.quote);
    }
    if (!occ.length) {
      // Anchors written before the quote was taken from the text index can hold
      // a quote with a suggestion's replacement text spliced into it, which no
      // search of the page will ever match. Their prefix/suffix came from clean
      // text, so place them by context instead of orphaning them forever.
      var ctx = offsetsFromContext(index, anchor);
      if (!ctx) return null;
      var ca = locate(index, ctx.start), cb = locate(index, ctx.end);
      if (!ca || !cb) return null;
      var crange = document.createRange();
      try {
        crange.setStart(ca.node, ca.offset);
        crange.setEnd(cb.node, cb.offset);
      } catch (e) { return null; }
      return crange;
    }

    var best = occ[0], bestScore = -1;
    for (var i = 0; i < occ.length; i++) {
      var s = occ[i], e = s + anchor.quote.length;
      var pre = index.text.slice(Math.max(0, s - CTX), s);
      var suf = index.text.slice(e, e + CTX);
      var score = tailMatchLen(pre, anchor.prefix || '') + headMatchLen(suf, anchor.suffix || '');
      if (anchor.nth === i) score += 0.5; // tiebreak only: same-position occurrence
      if (score > bestScore) { bestScore = score; best = s; }
    }
    var a = locate(index, best);
    var b = locate(index, best + anchor.quote.length);
    if (!a || !b) return null;
    var range = document.createRange();
    try {
      range.setStart(a.node, a.offset);
      range.setEnd(b.node, b.offset);
    } catch (e) { return null; }
    return range;
  }

  /* ------------------------------------------------------------------ *
   * 5. Highlight registry: wrap ranges in <mark>, cleanly reversible   *
   * ------------------------------------------------------------------ */
  // registry: hcId -> { marks:[mark...], ins: span|null }
  var registry = {};

  function splitAndWrap(textNode, start, end, cls, hcId) {
    // Wrap textNode[start:end] in a <mark>, splitting as needed.
    var node = textNode;
    if (start > 0) node = node.splitText(start);
    // node now begins at original `start`; its length is (origLen - start).
    if (end - start < node.nodeValue.length) node.splitText(end - start);
    var mark = document.createElement('mark');
    mark.className = cls;
    mark.setAttribute('data-hc-id', hcId);
    node.parentNode.replaceChild(mark, node);
    mark.appendChild(node);
    return mark;
  }

  // Wrap all text-node segments intersected by `range` (per node, never across
  // element boundaries). Returns the array of mark elements in document order.
  function wrapRange(range, cls, hcId) {
    // Collect intersecting text nodes first (mutation during walk is unsafe).
    var root = range.commonAncestorContainer;
    if (root.nodeType === 3) root = root.parentNode;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (isSkippable(n)) return NodeFilter.FILTER_REJECT;
        if (!n.nodeValue.length) return NodeFilter.FILTER_REJECT;
        return range.intersectsNode(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var nodes = [], n;
    while ((n = walker.nextNode())) nodes.push(n);

    var marks = [];
    nodes.forEach(function (tn) {
      var s = 0, e = tn.nodeValue.length;
      if (tn === range.startContainer) s = range.startOffset;
      if (tn === range.endContainer) e = range.endOffset;
      if (e <= s) return;
      marks.push(splitAndWrap(tn, s, e, cls, hcId));
    });
    return marks;
  }

  function unwrapMark(mark) {
    var parent = mark.parentNode;
    if (!parent) return;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
  }

  // Remove every highlight/insertion and restore original text nodes.
  function unwrapAll() {
    Object.keys(registry).forEach(function (id) {
      var r = registry[id];
      if (r.ins && r.ins.parentNode) r.ins.parentNode.removeChild(r.ins);
      (r.marks || []).forEach(unwrapMark);
    });
    registry = {};
    // Merge adjacent text nodes so subsequent indexing sees clean text.
    if (document.body.normalize) document.body.normalize();
  }

  /* ------------------------------------------------------------------ *
   * 6. Record model / reducer: rows -> threads                         *
   * ------------------------------------------------------------------ */
  var ROWS = [];      // raw event log (oldest first)
  var THREADS = [];   // derived, ordered

  function parseNote(row) {
    try { return JSON.parse(row.note || '{}'); } catch (e) { return {}; }
  }

  function buildThreads(rows) {
    var roots = {}, statusEvents = {}, deleted = {}, replies = {};
    rows.forEach(function (row) {
      var note = parseNote(row);
      var rec = {
        itemId: row.itemId, vote: row.vote, ts: note.cts || row.ts,
        voter: row.voter || '', session: row.session || '', note: note
      };
      switch (row.vote) {
        case 'comment':
        case 'suggestion':
          roots[row.itemId] = rec;
          break;
        case 'reply':
          var pid = note.parentId;
          (replies[pid] = replies[pid] || []).push(rec);
          break;
        case 'resolve':
        case 'reopen':
          // Plain row order decides: the last status row wins. Comparing
          // timestamps mixed client-authored cts with server ts — two different
          // clocks — so a resolve written offline and uploaded late could beat a
          // reopen made after it. bin/resolve.py reduces the same way.
          statusEvents[note.parentId] = rec;
          break;
        case 'delete':
          // target may be a thread root or a reply itemId
          deleted[note.parentId] = true;
          break;
      }
    });

    var threads = [];
    Object.keys(roots).forEach(function (id) {
      if (deleted[id]) return; // whole thread deleted
      var root = roots[id];
      var reps = (replies[id] || [])
        .filter(function (r) { return !deleted[r.itemId]; })
        .sort(function (a, b) { return Date.parse(a.ts) - Date.parse(b.ts); });
      var status = statusEvents[id];
      threads.push({
        id: id,
        root: root,
        replies: reps,
        resolved: status ? status.vote === 'resolve' : false,
        anchor: root.note.anchor || null,
        kind: root.note.kind || (root.vote === 'suggestion' ? 'suggestion' : 'comment')
      });
    });
    return threads;
  }

  /* ------------------------------------------------------------------ *
   * 7. Rendering: highlights + suggestions + sidebar                   *
   * ------------------------------------------------------------------ */
  var showSuggestions = safeGet('localStorage', LS.showSug);
  showSuggestions = showSuggestions == null ? null : showSuggestions === '1';
  var activeFilter = 'all'; // all | open | resolved
  var UI = {}; // cached DOM refs

  function applyHighlights() {
    unwrapAll();
    // Assign document order + orphan status by attempting re-attachment.
    THREADS.forEach(function (th) {
      th._range = null; th._orphan = true; th._docPos = Infinity;
      if (th.resolved) return;      // resolved: no page highlight
      if (!th.anchor) return;
      var range = rangeFromAnchor(th.anchor);
      if (!range) return;           // orphaned
      th._orphan = false;
      var isSug = th.kind === 'suggestion' && showSuggestionsEffective();
      var repl = th.root.note.replacement;
      var marks = wrapRange(range, isSug ? 'hc-highlight hc-del' : 'hc-highlight', th.id);
      if (!marks.length) { th._orphan = true; return; }
      var entry = { marks: marks, ins: null };
      if (isSug) {
        var ins = document.createElement('span');
        ins.className = 'hc-ins';
        ins.setAttribute('data-hc-id', th.id);
        ins.textContent = repl || '';
        var last = marks[marks.length - 1];
        last.parentNode.insertBefore(ins, last.nextSibling);
        entry.ins = ins;
      }
      registry[th.id] = entry;
      // click on highlight -> open sidebar to thread
      marks.forEach(function (m) {
        m.addEventListener('click', function (ev) {
          ev.stopPropagation();
          openSidebar();
          focusThread(th.id, false);
        });
      });
    });
    assignDocPositions();
  }

  // Sidebar ordering follows the page. querySelectorAll returns document order,
  // so one pass over the marks just placed gives every thread its position —
  // the previous code re-walked the whole document once per thread.
  function assignDocPositions() {
    var marks = document.querySelectorAll('mark.hc-highlight');
    var pos = 0, seen = {};
    for (var i = 0; i < marks.length; i++) {
      var id = marks[i].getAttribute('data-hc-id');
      if (!id || seen[id]) continue;
      seen[id] = ++pos;
    }
    THREADS.forEach(function (th) {
      th._docPos = seen[th.id] || Infinity;
    });
  }

  function showSuggestionsEffective() {
    if (showSuggestions != null) return showSuggestions;
    // default ON when any suggestion exists
    return THREADS.some(function (t) { return t.kind === 'suggestion'; });
  }

  function sortedThreads() {
    return THREADS.slice().sort(function (a, b) {
      var ao = a._orphan ? 1 : 0, bo = b._orphan ? 1 : 0;
      if (ao !== bo) return ao - bo;
      if (a._docPos !== b._docPos) return a._docPos - b._docPos;
      return Date.parse(a.root.ts) - Date.parse(b.root.ts);
    });
  }

  function openThreadCount() {
    return THREADS.filter(function (t) { return !t.resolved; }).length;
  }

  /* ------------------------------------------------------------------ *
   * 8. Sidebar UI                                                      *
   * ------------------------------------------------------------------ */
  function buildShell() {
    var root = el('div', { id: 'hc-root', class: 'hc-ui' });

    // Toggle button (bottom-right)
    UI.toggleBtn = el('button', {
      class: 'hc-toggle', title: 'Comments',
      onclick: function () { toggleSidebar(); }
    }, ['💬', el('span', { class: 'hc-count' })]);

    // Sidebar panel
    UI.panel = el('aside', { class: 'hc-panel', 'aria-hidden': 'true' });

    // Push vs overlay. Pushing is the default — an overlaid panel hides the
    // right-hand 340px of the document, which is where the text being commented
    // on often is. Below the CSS cutoff the push does not apply either way.
    UI.pushBtn = el('button', {
      class: 'hc-icon-btn hc-push-btn', text: '⇥',
      onclick: function () { setPushMode(!pushMode); }
    });

    UI.pending = el('button', {
      class: 'hc-pending', style: 'display:none',
      title: 'Not yet uploaded — click to retry now',
      onclick: function () { refresh(); }
    });

    var header = el('div', { class: 'hc-head' }, [
      el('div', { class: 'hc-title', text: 'Comments' }),
      el('div', { class: 'hc-head-btns' }, [
        UI.pending,
        UI.pushBtn,
        el('button', { class: 'hc-icon-btn', title: 'Refresh', text: '↻',
          onclick: function () { refresh(); } }),
        el('button', { class: 'hc-icon-btn', title: 'Close', text: '×',
          onclick: function () { closeSidebar(); } })
      ])
    ]);

    UI.sugToggle = el('label', { class: 'hc-sug-toggle' }, [
      el('input', { type: 'checkbox', onchange: function (e) {
        showSuggestions = e.target.checked;
        safeSet('localStorage', LS.showSug, showSuggestions ? '1' : '0');
        renderAll();
      } }),
      document.createTextNode(' Show suggestions')
    ]);

    UI.tabs = el('div', { class: 'hc-tabs' }, ['all', 'open', 'resolved'].map(function (f) {
      return el('button', {
        class: 'hc-tab' + (f === activeFilter ? ' hc-active' : ''),
        dataset: { f: f }, text: f.charAt(0).toUpperCase() + f.slice(1),
        onclick: function () { activeFilter = f; renderSidebar(); }
      });
    }));

    // A name is required for every post; keep that state explicit.
    UI.whoInput = el('input', { class: 'hc-who-input', type: 'text',
      placeholder: 'Your name (required)', value: getName(), required: 'required',
      'aria-required': 'true', autocomplete: 'name' });
    UI.whoInput.addEventListener('input', function () {
      setName(UI.whoInput.value.trim());
      UI.whoInput.classList.remove('hc-invalid');
      renderWho();
      renderSidebar();
    });
    UI.who = el('div', { class: 'hc-who' }, [
      el('span', { class: 'hc-who-lbl', text: 'You:' }), UI.whoInput
    ]);

    UI.list = el('div', { class: 'hc-list' });

    // One-line notice for storage failures (quota). Sits under the header so a
    // reviewer whose offline copy could not be saved is told, not left guessing.
    UI.storageNote = el('div', { class: 'hc-storage-note', style: 'display:none' });

    UI.panel.appendChild(header);
    UI.panel.appendChild(UI.storageNote);
    UI.panel.appendChild(UI.who);
    UI.panel.appendChild(el('div', { class: 'hc-controls' }, [UI.sugToggle, UI.tabs]));
    UI.panel.appendChild(UI.list);

    root.appendChild(UI.toggleBtn);
    root.appendChild(UI.panel);
    document.body.appendChild(root);

    renderWho();
    renderPending();
    syncPushMode();
    if (pendingStorageNote) showStorageNote(pendingStorageNote);

    // Banner container (top)
    UI.banner = el('div', { class: 'hc-banner', style: 'display:none' });
    root.appendChild(UI.banner);
  }

  // Push mode: the panel resizes the page rather than covering it. Stored
  // per browser; the media query in the stylesheet suppresses the push on
  // viewports too narrow for two columns.
  var pushMode = safeGet('localStorage', LS.push);
  pushMode = pushMode == null ? true : pushMode === '1';

  function setPushMode(v) {
    pushMode = !!v;
    safeSet('localStorage', LS.push, pushMode ? '1' : '0');
    syncPushMode();
  }
  function syncPushMode() {
    document.documentElement.classList.toggle('hc-push', pushMode);
    if (!UI.pushBtn) return;
    UI.pushBtn.classList.toggle('hc-active', pushMode);
    UI.pushBtn.title = pushMode
      ? 'Panel pushes page aside — click to overlay the page instead'
      : 'Panel overlays page — click to push the page aside instead';
  }

  var pendingStorageNote = null;
  function showStorageNote(msg) {
    pendingStorageNote = msg;
    if (typeof UI === 'undefined' || !UI || !UI.storageNote) return;
    UI.storageNote.textContent = msg;
    UI.storageNote.style.display = '';
  }

  // Queued-but-unsent count. Silence here would look exactly like a successful
  // upload, which is the one thing an offline reviewer must not have to guess at.
  function renderPending() {
    if (!UI.pending) return;
    var n = readOutbox().length;
    UI.pending.style.display = n ? '' : 'none';
    UI.pending.textContent = n === 1 ? '1 pending' : n + ' pending';
    if (UI.toggleBtn) UI.toggleBtn.classList.toggle('hc-pending-dot', n > 0);
  }

  function renderWho() {
    if (!UI.who) return;
    var named = !!getName();
    UI.who.classList.toggle('hc-who-anon', !named);
    UI.who.firstChild.textContent = named ? 'You:' : 'Name required —';
  }

  function syncViewportHeight() {
    var vv = window.visualViewport;
    var h = vv ? vv.height : window.innerHeight;
    if (h) document.documentElement.style.setProperty('--hc-vv-height', h + 'px');
    document.documentElement.style.setProperty('--hc-vv-offset-top', (vv ? vv.offsetTop : 0) + 'px');
    if (lastFocusedInput) keepFocusedInputVisible(lastFocusedInput);
  }

  var lastFocusedInput = null;
  function keepFocusedInputVisible(target) {
    if (!target || !target.matches || !target.matches('input, textarea')) return;
    setTimeout(function () {
      try { target.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' }); } catch (e) {}
    }, 80);
  }

  // The offline banner stays up for as long as there is no network, so unlike a
  // transient warning it would otherwise sit on top of the panel header for the
  // whole session. Publish its height and let the panel start below it.
  function syncBannerHeight() {
    var h = (UI.banner && UI.banner.style.display !== 'none') ? UI.banner.offsetHeight : 0;
    document.documentElement.style.setProperty('--hc-banner-h', h + 'px');
  }
  function showBanner(msg) {
    UI.banner.textContent = msg;
    UI.banner.style.display = 'block';
    syncBannerHeight();
  }
  function hideBanner() {
    UI.banner.style.display = 'none';
    syncBannerHeight();
  }

  function toggleSidebar() {
    if (UI.panel.classList.contains('hc-open')) closeSidebar(); else openSidebar();
  }
  function openSidebar() {
    UI.panel.classList.add('hc-open');
    UI.panel.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('hc-panel-open');
  }
  function closeSidebar() {
    UI.panel.classList.remove('hc-open');
    UI.panel.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('hc-panel-open');
  }

  function renderCount() {
    var c = openThreadCount();
    UI.toggleBtn.querySelector('.hc-count').textContent = c ? String(c) : '';
    UI.toggleBtn.classList.toggle('hc-has', c > 0);
  }

  function threadMatchesFilter(th) {
    if (activeFilter === 'open') return !th.resolved;
    if (activeFilter === 'resolved') return th.resolved;
    return true;
  }

  function renderSidebar() {
    // sync tab active state + suggestion toggle
    Array.prototype.forEach.call(UI.tabs.children, function (b) {
      b.classList.toggle('hc-active', b.dataset.f === activeFilter);
    });
    UI.sugToggle.querySelector('input').checked = showSuggestionsEffective();

    UI.list.textContent = '';
    var threads = sortedThreads().filter(threadMatchesFilter);

    if (!threads.length) {
      UI.list.appendChild(el('div', { class: 'hc-empty' }, [
        el('p', { text: activeFilter === 'all'
          ? 'No comments yet.' : 'No ' + activeFilter + ' threads.' }),
        el('p', { class: 'hc-empty-hint',
          text: 'Select any text in the page to add a comment or suggestion.' })
      ]));
    } else {
      threads.forEach(function (th) { UI.list.appendChild(renderThread(th)); });
    }
    renderCount();
  }

  function renderThread(th) {
    var box = el('div', { class: 'hc-thread' + (th.resolved ? ' hc-resolved' : '') +
      (th._orphan ? ' hc-orphan' : ''), dataset: { thread: th.id } });

    var root = th.root, note = root.note;
    // header
    box.appendChild(el('div', { class: 'hc-thread-head' }, [
      el('span', { class: 'hc-author', text: root.voter || 'Anonymous' }),
      el('span', { class: 'hc-time', text: relTime(root.ts) }),
      th.kind === 'suggestion' ? el('span', { class: 'hc-badge', text: 'suggestion' }) : null,
      th.resolved ? el('span', { class: 'hc-badge hc-badge-done', text: 'resolved' }) : null,
      (th._orphan && !th.resolved) ? el('span', { class: 'hc-badge hc-badge-warn', text: 'original text not found' }) : null
    ]));

    // suggestion old -> new
    if (th.kind === 'suggestion') {
      var oldT = (note.anchor && note.anchor.quote) || '';
      var newT = note.replacement || '';
      box.appendChild(el('div', { class: 'hc-sugdiff' }, [
        el('span', { class: 'hc-old', text: truncate(oldT, 80) }),
        el('span', { class: 'hc-arrow', text: ' → ' }),
        newT === '' ? el('span', { class: 'hc-new hc-del-note', text: '(deleted)' })
                    : el('span', { class: 'hc-new', text: truncate(newT, 80) })
      ]));
    }

    if (note.text) box.appendChild(el('div', { class: 'hc-body', text: note.text }));

    // quote snippet
    var q = note.anchor && note.anchor.quote;
    if (q) {
      box.appendChild(el('div', { class: 'hc-quote' + ((th._orphan && !th.resolved) ? ' hc-quote-orphan' : ''),
        text: '“' + truncate(q, 120) + '”' }));
    }

    // replies
    th.replies.forEach(function (rp) {
      var rbox = el('div', { class: 'hc-reply' }, [
        el('div', { class: 'hc-thread-head' }, [
          el('span', { class: 'hc-author', text: rp.voter || 'Anonymous' }),
          el('span', { class: 'hc-time', text: relTime(rp.ts) })
        ]),
        el('div', { class: 'hc-body', text: rp.note.text || '' })
      ]);
      if (isOwn(rp)) rbox.appendChild(delBtn(th.id, rp.itemId));
      box.appendChild(rbox);
    });

    // reply input
    var rInput = el('input', { class: 'hc-reply-input', type: 'text', placeholder: 'Reply…' });
    rInput.value = getDraft(th.id);

    function sendReply() {
      var text = rInput.value.trim();
      if (!text) return;
      // Replies used to post silently as Anonymous whenever no name was set;
      // send the reviewer to the name field instead.
      if (!getName()) {
        openSidebar();
        UI.whoInput.classList.add('hc-invalid');
        UI.whoInput.focus();
        return;
      }
      rInput.value = '';
      setDraft(th.id, '');
      submitReply(th.id, text);
    }

    rInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') sendReply();
    });
    rInput.addEventListener('input', function () {
      setDraft(th.id, rInput.value.trim());
      syncReplyBtn();
    });

    // Submit sits next to the reply box and appears as soon as there is text,
    // so an unsent reply is never one stray Resolve/Delete click from gone.
    var replyBtn = el('button', { class: 'hc-btn hc-btn-sm hc-btn-primary',
      text: 'Submit', onclick: sendReply });
    function syncReplyBtn() {
      replyBtn.style.display = rInput.value.trim() ? '' : 'none';
      box.classList.toggle('hc-has-draft', !!rInput.value.trim());
    }
    syncReplyBtn();
    box.appendChild(el('div', { class: 'hc-reply-row' }, [rInput, replyBtn]));

    var actions = el('div', { class: 'hc-actions' }, [
      el('button', { class: 'hc-btn hc-btn-sm',
        text: th.resolved ? 'Reopen' : 'Resolve',
        onclick: function () { submitStatus(th.id, th.resolved ? 'reopen' : 'resolve'); } })
    ]);
    if (isOwn(root)) actions.appendChild(delBtn(th.id, th.id));
    box.appendChild(actions);

    // click thread -> scroll + flash highlight
    box.addEventListener('click', function (e) {
      if (e.target.closest('button') || e.target.closest('input')) return;
      focusThread(th.id, true);
    });

    return box;
  }

  function delBtn(threadId, targetId) {
    return el('button', { class: 'hc-btn hc-btn-sm hc-btn-danger', text: 'Delete',
      onclick: function () {
        if (!confirm('Delete this ' + (threadId === targetId ? 'comment' : 'reply') + '?')) return;
        submitDelete(threadId, targetId);
      } });
  }

  function isOwn(rec) {
    var me = getName();
    return me && rec.voter && rec.voter.toLowerCase() === me.toLowerCase();
  }

  function focusThread(threadId, flash) {
    var entry = registry[threadId];
    if (entry && entry.marks && entry.marks.length) {
      entry.marks[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (flash) {
        entry.marks.forEach(function (m) { m.classList.add('hc-flash'); });
        setTimeout(function () {
          entry.marks.forEach(function (m) { m.classList.remove('hc-flash'); });
        }, 1200);
      }
    }
    var node = UI.list.querySelector('[data-thread="' + cssEscape(threadId) + '"]');
    if (node) {
      node.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      node.classList.add('hc-thread-flash');
      setTimeout(function () { node.classList.remove('hc-thread-flash'); }, 1200);
    }
  }

  function cssEscape(s) { return String(s).replace(/["\\]/g, '\\$&'); }

  /* ------------------------------------------------------------------ *
   * 9. Submitting records (optimistic)                                 *
   * ------------------------------------------------------------------ */
  // Show the record immediately and send it. The local row and the payload are
  // the same object plus `project`, so a row that lands in the outbox re-renders
  // identically (same ts, same itemId) after a reload with no network.
  function record(itemId, vote, note, voter) {
    var ts = new Date().toISOString();
    // The server stamps its own timestamp on arrival, which for a queued record
    // is whenever the network came back. Keep the authoring time in the note so
    // a flight's worth of comments doesn't collapse to the moment of landing.
    note.cts = ts;
    var row = {
      ts: ts, itemId: itemId, vote: vote,
      note: JSON.stringify(note), voter: voter || getName() || '', session: SESSION
    };
    ROWS.push(row);
    var payload = {
      project: PROJECT, ts: row.ts, itemId: row.itemId, vote: row.vote,
      note: row.note, voter: row.voter, session: row.session
    };
    postRecord(payload);
  }

  function submitComment(anchor, text, kind, replacement, voter) {
    var itemId = genId();
    var note = { v: 2, text: text || '', kind: kind, anchor: anchor };
    if (kind === 'suggestion') note.replacement = replacement || '';
    setName(voter);
    if (UI.whoInput) { UI.whoInput.value = voter; renderWho(); }
    record(itemId, kind, note, voter);
    renderAll();
  }

  function submitReply(threadId, text) {
    var itemId = genId();
    var note = { v: 2, text: text, parentId: threadId };
    record(itemId, 'reply', note);
    renderAll();
  }

  function submitStatus(threadId, vote) {
    var itemId = genId();
    var note = { v: 2, parentId: threadId };
    record(itemId, vote, note);
    renderAll();
  }

  function submitDelete(threadId, targetId) {
    var itemId = genId();
    var note = { v: 2, parentId: targetId };
    record(itemId, 'delete', note);
    renderAll();
  }

  /* ------------------------------------------------------------------ *
   * 10. Floating toolbar + composer popover                            *
   * ------------------------------------------------------------------ */
  // Captured at selection time: the anchor (not a live Range) plus the rect the
  // toolbar/composer position from. A live Range is destroyed by the
  // body.normalize() every re-render performs, so a background refresh landing
  // between selecting text and pressing 💬 used to corrupt the quote.
  var savedSel = null; // { anchor, quote, rect }

  function clearToolbar() {
    if (UI.toolbar) { UI.toolbar.remove(); UI.toolbar = null; }
  }
  function clearComposer() {
    if (UI.composer) { UI.composer.remove(); UI.composer = null; }
  }

  function selectionInsideUI(sel) {
    if (!sel.rangeCount) return true;
    var node = sel.getRangeAt(0).commonAncestorContainer;
    var e = node.nodeType === 1 ? node : node.parentNode;
    return !!(e && e.closest && e.closest('.hc-ui'));
  }

  function onMouseUp(e) {
    if (e.target.closest && e.target.closest('.hc-ui')) return;
    setTimeout(function () {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.toString().trim() || selectionInsideUI(sel)) {
        clearToolbar();
        return;
      }
      var range = sel.getRangeAt(0);
      var anchor;
      try { anchor = anchorFromRange(range); } catch (err) {
        console.warn('[html-comments] could not anchor selection:', err);
        clearToolbar();
        return;
      }
      savedSel = { anchor: anchor, quote: anchor.quote, rect: range.getBoundingClientRect() };
      showToolbar(range);
    }, 10);
  }

  function showToolbar(range) {
    clearToolbar();
    var rect = range.getBoundingClientRect();
    UI.toolbar = el('div', { class: 'hc-ui hc-toolbar' }, [
      el('button', { class: 'hc-tb-btn', text: '💬 Comment',
        onclick: function () { openComposer('comment'); } }),
      el('button', { class: 'hc-tb-btn', text: '✏️ Suggest',
        onclick: function () { openComposer('suggestion'); } })
    ]);
    document.body.appendChild(UI.toolbar);
    var below = window.scrollY + rect.bottom + 8;
    var above = window.scrollY + rect.top - UI.toolbar.offsetHeight - 8;
    var viewportBottom = window.scrollY + window.innerHeight;
    // Prefer the natural reading order (below the selection), falling back
    // above only when the toolbar would otherwise leave the viewport.
    var mobile = window.matchMedia && window.matchMedia('(max-width: 899px)').matches;
    var top, left;
    if (mobile) {
      var belowViewport = rect.bottom + 8;
      var aboveViewport = rect.top - UI.toolbar.offsetHeight - 8;
      top = belowViewport + UI.toolbar.offsetHeight <= window.innerHeight - 8
        ? belowViewport : Math.max(4, aboveViewport);
      top = Math.min(top, window.innerHeight - UI.toolbar.offsetHeight - 4);
      left = Math.max(4, Math.min(rect.left,
        document.documentElement.clientWidth - UI.toolbar.offsetWidth - 4));
    } else {
      top = below + UI.toolbar.offsetHeight <= viewportBottom - 8
        ? below : Math.max(window.scrollY + 4, above);
      left = window.scrollX + rect.left;
      left = Math.min(left, window.scrollX + document.documentElement.clientWidth - UI.toolbar.offsetWidth - 8);
      left = Math.max(4, left);
    }
    UI.toolbar.style.top = top + 'px';
    UI.toolbar.style.left = left + 'px';
  }

  function openComposer(kind) {
    if (!savedSel || !savedSel.anchor) return;
    clearToolbar();
    clearComposer();
    var anchor = savedSel.anchor;
    if (!anchor.quote) {
      // Nothing of the document itself is selected (only overlay text, with no
      // original behind it). Anchoring here would orphan the thread on sight.
      showBanner('Select some of the document text to comment on it.');
      setTimeout(hideBanner, 4000);
      return;
    }
    var rect = savedSel.rect;

    var replArea = null, textArea;
    var frag = [];

    if (kind === 'suggestion') {
      replArea = el('textarea', { class: 'hc-ta', rows: '3',
        placeholder: 'Replacement text (empty = suggest deletion)' });
      replArea.value = anchor.quote;
      frag.push(el('label', { class: 'hc-lbl', text: 'Replacement text' }));
      frag.push(replArea);
      textArea = el('textarea', { class: 'hc-ta', rows: '2', placeholder: 'Optional comment…' });
      frag.push(el('label', { class: 'hc-lbl', text: 'Comment (optional)' }));
      frag.push(textArea);
    } else {
      textArea = el('textarea', { class: 'hc-ta', rows: '3', placeholder: 'Your comment…' });
      frag.push(textArea);
    }

    var nameInput = el('input', { class: 'hc-name', type: 'text',
      placeholder: 'Your name (required)', required: 'required',
      'aria-required': 'true', autocomplete: 'name' });
    nameInput.value = getName();
    frag.push(nameInput);

    function doSubmit() {
      var voter = nameInput.value.trim();
      if (!voter) { nameInput.focus(); nameInput.classList.add('hc-invalid'); return; }
      var text = textArea.value.trim();
      var repl = replArea ? replArea.value : '';
      if (kind === 'comment' && !text) { textArea.focus(); return; }
      submitComment(anchor, text, kind, repl, voter);
      clearComposer();
      window.getSelection().removeAllRanges();
    }
    var submit = el('button', { class: 'hc-btn hc-btn-primary', text: 'Submit',
      onclick: doSubmit });
    var cancel = el('button', { class: 'hc-btn', text: 'Cancel',
      onclick: function () { clearComposer(); } });

    function syncSubmitState() {
      var disabled = !nameInput.value.trim();
      submit.disabled = disabled;
      submit.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    }
    nameInput.addEventListener('input', function () {
      var name = nameInput.value.trim();
      setName(name);
      if (UI.whoInput) { UI.whoInput.value = name; renderWho(); }
      nameInput.classList.remove('hc-invalid');
      syncSubmitState();
    });
    syncSubmitState();

    // Cmd/Ctrl+Enter submits from any field in the composer.
    [replArea, textArea, nameInput].forEach(function (f) {
      if (!f) return;
      f.addEventListener('keydown', function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doSubmit(); }
      });
    });

    frag.push(el('div', { class: 'hc-composer-btns' }, [cancel, submit]));

    UI.composer = el('div', { class: 'hc-ui hc-composer' }, [
      el('div', { class: 'hc-composer-title',
        text: kind === 'suggestion' ? 'Suggest an edit' : 'Add a comment' })
    ].concat(frag));
    document.body.appendChild(UI.composer);

    var mobile = window.matchMedia && window.matchMedia('(max-width: 899px)').matches;
    var top, left;
    if (mobile) {
      // CSS makes the composer fixed on small screens. Center it after layout
      // so it cannot be hidden below the selection or browser chrome.
      top = Math.max(8, Math.min((window.innerHeight - UI.composer.offsetHeight) / 2,
        window.innerHeight - UI.composer.offsetHeight - 8));
      left = Math.max(8, (document.documentElement.clientWidth - UI.composer.offsetWidth) / 2);
    } else {
      top = window.scrollY + rect.bottom + 8;
      left = window.scrollX + rect.left;
      left = Math.min(left, window.scrollX + document.documentElement.clientWidth - UI.composer.offsetWidth - 8);
      left = Math.max(4, left);
    }
    UI.composer.style.top = top + 'px';
    UI.composer.style.left = left + 'px';
    if (replArea) {
      // Word-style: the original text is pre-selected, so typing replaces it
      // outright, while arrow keys / a click drop you into editing it in place.
      replArea.focus();
      replArea.setSelectionRange(0, replArea.value.length);
    } else {
      textArea.focus();
    }
  }

  // Dismiss transient UI on outside click / escape.
  document.addEventListener('mousedown', function (e) {
    if (e.target.closest && e.target.closest('.hc-ui')) return;
    if (UI.composer && !UI.composer.contains(e.target)) { /* keep until action */ }
    if (UI.toolbar && !UI.toolbar.contains(e.target)) clearToolbar();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { clearToolbar(); clearComposer(); }
  });

  /* ------------------------------------------------------------------ *
   * 11. Orchestration                                                  *
   * ------------------------------------------------------------------ */
  function renderAll(rebuildThreads) {
    if (rebuildThreads !== false) THREADS = buildThreads(ROWS);
    applyHighlights();
    renderSidebar();
  }

  var refreshing = false;

  function refresh() {
    if (refreshing) return Promise.resolve();
    refreshing = true;
    hideBanner();
    // Drain the queue before reading, so a flushed record comes back in the
    // same GET rather than living on as an outbox entry until the next refresh.
    var done = function () {
      refreshing = false;
      renderPending();
    };
    return flushOutbox().then(fetchRows).then(function (res) {
      if (res && res.ok === true) {
        var rows = res.rows || [];
        // Server truth closes the outbox. A successful POST is often answered
        // with a 302 to a one-time URL that then 404s, so the response cannot be
        // parsed even though the row was written; the record stays queued and
        // gets posted again, duplicating it. If the server has the itemId, the
        // record landed — drop it.
        reconcileOutbox(rows);
        writeCache(rows);
        ROWS = composeRows(rows);
        renderAll(true);
      } else if (res && res.ok === false && /unknown action/i.test(res.error || '')) {
        showBanner('Comments backend needs updating (rows action not deployed)');
        renderAll(true); // still render anything we have locally
      } else {
        showBanner('Comments backend returned an unexpected response.');
      }
    }).catch(function (err) {
      console.warn('[html-comments] rows GET failed:', err);
      // Fall back to the last server read plus the queue, so the reviewer keeps
      // working with a full picture either way.
      try {
        ROWS = composeRows(readCache());
        renderAll(true);
      } catch (e) { console.warn('[html-comments] render after failed refresh:', e); }
      // A TypeError is what fetch throws when the request never reached a
      // server. A timeout (AbortError) or unparseable body is a reachable-but-
      // broken backend, which is a different thing to tell the reviewer.
      var offline = (navigator && navigator.onLine === false) ||
        (err && err.name === 'TypeError');
      if (offline) {
        showBanner(readOutbox().length
          ? 'Offline — showing the last synced comments plus yours; queued edits upload when you reconnect.'
          : 'Offline — showing the last synced comments. Anything you add is saved locally and uploads when you reconnect.');
      } else {
        showBanner(readOutbox().length
          ? 'Comments server unreachable or returned an invalid response — showing the last synced comments plus yours; queued edits upload when it responds again.'
          : 'Comments server unreachable or returned an invalid response — showing the last synced comments.');
      }
    }).then(done, function (e) {
      // Even a throw inside the handlers above must not leave refresh wedged.
      console.warn('[html-comments] refresh failed:', e);
      done();
    });
  }

  function reconcileOutbox(rows) {
    var box = readOutbox();
    if (!box.length) return;
    var have = {};
    (rows || []).forEach(function (r) { if (r && r.itemId) have[r.itemId] = true; });
    var keep = box.filter(function (p) { return !(p && p.itemId && have[p.itemId]); });
    if (keep.length !== box.length) writeOutbox(keep);
  }

  // Debug hook: inspect internal state (harmless; aids testing).
  window.__hcVersion = HC_VERSION;
  window.__hcState = function () {
    return { version: HC_VERSION, rows: ROWS.length, threads: THREADS.length,
      savedSelection: !!savedSel, composer: !!UI.composer };
  };

  // Debug hook: what anchor would the current selection produce?
  window.__hcAnchorFromSelection = function () {
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount) return null;
    return anchorFromRange(sel.getRangeAt(0));
  };
  // Debug hook: can a stored anchor still be found on the page?
  window.__hcCanReattach = function (anchor) { return !!rangeFromAnchor(anchor); };

  // Debug hook: feed fake rows through the exact same render path.
  window.__hcInjectRows = function (rows) {
    ROWS = (rows || []).slice();
    renderAll(true);
    openSidebar();
    return THREADS;
  };

  function init() {
    buildShell();
    syncViewportHeight();
    window.addEventListener('resize', syncViewportHeight, { passive: true });
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', syncViewportHeight, { passive: true });
      window.visualViewport.addEventListener('scroll', syncViewportHeight, { passive: true });
    }
    UI.panel.addEventListener('focusin', function (e) {
      lastFocusedInput = e.target;
      keepFocusedInputVisible(e.target);
    });
    document.addEventListener('pointerup', onMouseUp, true);

    // Retry the queue whenever the network plausibly came back. Without this the
    // only trigger is a page load or the Refresh button, so a reviewer who works
    // offline and never reopens the page leaves comments stranded locally.
    window.addEventListener('online', function () { refresh(); });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && readOutbox().length) refresh();
    });

    if (DEBUG_ROWS) {
      try { window.__hcInjectRows(JSON.parse(DEBUG_ROWS)); } catch (e) {}
      return;
    }
    // Paint from local state first: with no network this is the whole session,
    // and with a network it just avoids an empty sidebar during the round-trip.
    ROWS = composeRows(readCache());
    renderAll(true);
    refresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
