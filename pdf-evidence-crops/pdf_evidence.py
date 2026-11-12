# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24", "pillow>=10"]
# ///
"""Crop PDF evidence (a table cell or a quoted passage) and locate highlight boxes.

Input: a JSON list of items, each
  {"id": str, "pdf": path, "page": 1-based int, "quote": str,
   "row": str?, "col": str?, "clip": [x0,y0,x1,y1]? (points of the displayed page, after /Rotate),
   "rotate": 0|90|180|270? (clockwise, applied after page rotation; omitted = auto),
   "img": output file name?, "pages_tolerance": int? (default 1),
   "near": [x%, y%]?, "box": [l%, t%, w%, h%]?, "choose": int? (1-based candidate)}
Output: JSON list, one result per item:
  {"id", "img", "page", "rotate", "clip", "boxes": [[left%, top%, width%, height%], ...],
   "status", "source", "notes", "candidates"?, "review"}
status: cell | row | column | value | text | picked | picked_unread | manual | unverified |
        ambiguous | not_found | outside_clip | error.
boxes are percentages of the written image (value boxes first, then the row label).
review is false only for confident statuses (cell, row, column, text) without warnings.
"""
import argparse, io, json, os, re, shutil, subprocess, sys, tempfile
from difflib import SequenceMatcher
import pymupdf as fitz
from PIL import Image, ImageDraw

DPI = 125
MINUS = "−–—‐‑‒﹣－"
NUM_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?")
STAT_PREFIX = re.compile(r"^\s*(?:[nNrRpbBβMF]|SD|df)\s*[=<>]\s*")


# ---------- normalisation ----------
def norm_text(s):
    s = (s or "").translate({ord(c): "-" for c in MINUS})
    s = s.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("­", "")
    return re.sub(r"\s+", " ", s).strip()


NUM_TOKEN = re.compile(r"^[(\[]*([-+]?(?:\d{1,3}(?:,\d{3})+(?![\d,])|\d+)?(?:[.,]\d+)?)(.*)$")


def num_key(tok):
    """Canonical number string for a token ('-0.35**' -> '-.35', '0,14c' -> '.14'), or None.

    Trailing stars, daggers, footnote letters and brackets are ignored; a comma followed by
    exactly three digits is a thousands separator (unless the integer part is 0), any other
    comma a decimal comma."""
    t = norm_text(tok)
    m = NUM_TOKEN.match(t)
    if not m or not re.search(r"\d", m.group(1)):
        return None
    num, rest = m.group(1), m.group(2)
    if re.search(r"\d", rest) or re.search(r"[A-Za-z]{3,}", rest):
        return None
    if re.fullmatch(r"[-+]?\d+,\d{1,2}|[-+]?\d+,\d{4,}|[-+]?0,\d+", num):
        num = num.replace(",", ".")
    sign = "-" if num.startswith("-") else ""
    t = num.lstrip("+-").replace(",", "")
    if "." in t:
        a, b = t.split(".", 1)
        t = f"{a.lstrip('0')}.{b}"
    else:
        t = t.lstrip("0") or "0"
    return sign + t


def is_numeric_quote(q):
    q = STAT_PREFIX.sub("", norm_text(q))
    words = re.findall(r"[A-Za-z]{2,}", q)
    return bool(re.search(r"\d", q)) and not words


def first_number(q):
    """The reported value: the number after the last '=' if there is one, else the first."""
    q = norm_text(q)
    q = q.rsplit("=", 1)[1] if "=" in q else STAT_PREFIX.sub("", q)
    for tok in q.split():
        k = num_key(tok)
        if k:
            return k
    m = NUM_RE.search(q.replace(" ", ""))
    return num_key(m.group(0)) if m else None


def label_variants(label):
    """Strings to look for when matching a row/column label, the full label first. Variants
    without a parenthetical qualifier ('(Time 2)') are weaker: see variant_weight."""
    lab = norm_text(label)
    lab = re.sub(r"^(title|footnote|note)\s*:\s*", "", lab, flags=re.I)
    out = []
    parts = [p.strip() for p in re.split(r"\s*[/;:]\s*", lab) if p.strip()]
    for p in [lab] + parts[::-1]:
        p2 = re.sub(r"\([^)]*\)", "", p).strip()
        p3 = re.sub(r"^\(?\d+[a-z]?[.)]?\s+", "", p2).strip()
        for v in (p, p2, p3):
            if v and v not in out:
                out.append(v)
    return out


def enum_number(label):
    m = re.match(r"^\s*\(?(\d+[a-z]?)[.)]?\s", norm_text(label) + " ")
    return m.group(1) if m else None


def variant_weight(label, v):
    """A variant that drops a parenthetical qualifier of the label matches less strongly, so
    'Contact (Time 2)' prefers its own row over 'Contact (Time 1)'."""
    return 0.9 if re.search(r"\([^)]*[A-Za-z0-9][^)]*\)", norm_text(label)) and "(" not in v else 1.0


def simple(s):
    # keep letters of any script: Greek headers (alpha, beta) must stay distinct
    return re.sub(r"[^\w.+-]+", " ", norm_text(s).lower()).replace("_", " ").strip()


# ---------- geometry ----------
def rot_point(x, y, r, W, H):
    if r == 90:
        return H - y, x
    if r == 180:
        return W - x, H - y
    if r == 270:
        return y, W - x
    return x, y


def make_transform(page, zoom, r):
    """Unrotated PDF points -> pixel coords of the upright (display + r) page image."""
    M = page.rotation_matrix * fitz.Matrix(zoom, zoom)
    disp = page.rect * fitz.Matrix(zoom, zoom)  # page.rect is already the displayed size
    W, H = disp.width, disp.height

    def T(rect):
        pts = [fitz.Point(rect[0], rect[1]) * M, fitz.Point(rect[2], rect[1]) * M,
               fitz.Point(rect[0], rect[3]) * M, fitz.Point(rect[2], rect[3]) * M]
        pts = [rot_point(p.x, p.y, r, W, H) for p in pts]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return [min(xs), min(ys), max(xs), max(ys)]

    def vec(dx, dy):
        p = fitz.Point(dx, dy) * page.rotation_matrix - fitz.Point(0, 0) * page.rotation_matrix
        x, y = p.x, p.y
        for _ in range(r // 90):
            x, y = -y, x
        return x, y

    return T, vec


def clip_transform(page, zoom, r):
    """Clip rectangles are in displayed page coordinates (PyMuPDF's page.rect, as a viewer
    shows the page, i.e. after /Rotate): scale, then apply the extra rotation r."""
    W, H = page.rect.width * zoom, page.rect.height * zoom

    def C(rect):
        pts = [rot_point(x * zoom, y * zoom, r, W, H) for x, y in
               ((rect[0], rect[1]), (rect[2], rect[1]), (rect[0], rect[3]), (rect[2], rect[3]))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return [min(xs), min(ys), max(xs), max(ys)]
    return C


_RENDERS = {}


def render_upright(page, zoom, r, clip=None):
    """The page (or a clip of it, in displayed points) as an upright image; whole-page renders
    at the crop resolution are cached, since many items share a page."""
    key = (page.parent.name, page.number, zoom, r)
    if clip is None and key in _RENDERS:
        return _RENDERS[key]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, clip=clip)
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    im = im.rotate(-r, expand=True) if r else im
    if clip is None and zoom <= 200 / 72:
        if len(_RENDERS) >= 4:
            _RENDERS.pop(next(iter(_RENDERS)))
        _RENDERS[key] = im
    return im


def upright_to_display(page, zoom, r, box):
    """Inverse of clip_transform: a box in upright pixels at zoom -> displayed page points."""
    W, H = page.rect.width * zoom, page.rect.height * zoom
    Wr, Hr = (H, W) if r in (90, 270) else (W, H)
    pts = [rot_point(x, y, (360 - r) % 360, Wr, Hr) for x, y in
           ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))]
    xs, ys = [p[0] / zoom for p in pts], [p[1] / zoom for p in pts]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys)) & page.rect


# ---------- words ----------
def text_words(page):
    """Words (unrotated coords) with their line's reading direction."""
    dirs = {}
    for b in page.get_text("dict")["blocks"]:
        for li, l in enumerate(b.get("lines", [])):
            dirs[(b["number"], li)] = l["dir"]
    out = []
    for x0, y0, x1, y1, w, bno, lno, _ in page.get_text("words"):
        out.append({"r": [x0, y0, x1, y1], "t": w, "dir": dirs.get((bno, lno), (1, 0))})
    return out


def auto_rotation(words, vec):
    """Extra clockwise rotation that makes the dominant text direction left-to-right."""
    score = {0: 0, 90: 0, 180: 0, 270: 0}
    for w in words:
        dx, dy = vec(*w["dir"])
        n = len(w["t"])
        if abs(dx) >= abs(dy):
            score[0 if dx > 0 else 180] += n
        else:
            score[90 if dy < 0 else 270] += n  # text reading upwards needs a clockwise turn
    return max(score, key=score.get) if sum(score.values()) else 0


_OCR = {}


def ocr_words(page, r, zoom_out, dpi=300):
    """Tesseract words in upright pixel coords at zoom_out (via a dpi render), cached per page."""
    if not shutil.which("tesseract"):
        return []
    key = (page.parent.name, page.number, r, zoom_out, dpi)
    if key not in _OCR:
        _OCR[key] = _ocr(page, r, zoom_out, dpi)
    return [dict(w) for w in _OCR[key]]


def tesseract(im, min_conf=30):
    """Words in image pixels from two Tesseract passes: psm 11 finds isolated table cells
    best; psm 6 words that do not overlap them are added."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.png")
        im.save(p)
        passes = []
        for psm in ("11", "6"):
            tsv = subprocess.run(["tesseract", p, "-", "--psm", psm, "tsv"], capture_output=True, text=True).stdout
            got = []
            for line in tsv.splitlines()[1:]:
                f = line.split("\t")
                if len(f) < 12 or not f[11].strip():
                    continue
                try:
                    conf = float(f[10])
                except ValueError:
                    continue
                if conf < min_conf:
                    continue
                L, Tp, Wd, Ht = map(int, f[6:10])
                got.append({"u": [L, Tp, L + Wd, Tp + Ht], "t": f[11], "conf": conf})
            passes.append(got)
    base = passes[0]
    for w in passes[1]:
        if not any(overlap(w["u"], b["u"]) > 0.5 for b in base):
            base.append(w)
    return base


def _ocr(page, r, zoom_out, dpi):
    z = dpi / 72
    s = zoom_out / z
    words = tesseract(render_upright(page, z, r))
    for w in words:
        w["u"] = [v * s for v in w["u"]]
    return words


def overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))
    return ix * iy / area


def merge_minus(words):
    """Join a free-standing minus sign to the number that follows it on the same line."""
    words = sorted(words, key=lambda w: (round(w["u"][1] / 3), w["u"][0]))
    out, skip = [], False
    for i, w in enumerate(words):
        if skip:
            skip = False
            continue
        t = norm_text(w["t"])
        if t == "-" and i + 1 < len(words):
            n = words[i + 1]
            h = w["u"][3] - w["u"][1]
            if abs(cy(n["u"]) - cy(w["u"])) < 0.6 * h and 0 <= n["u"][0] - w["u"][2] < 0.8 * h:
                out.append({**n, "t": "-" + n["t"], "u": [w["u"][0], min(w["u"][1], n["u"][1]), n["u"][2], max(w["u"][3], n["u"][3])]})
                skip = True
                continue
        out.append(w)
    return out


def cy(u):
    return (u[1] + u[3]) / 2


def lines_of(words):
    """Cluster words into visual lines: similar vertical centre and no horizontal overlap."""
    ws = sorted(words, key=lambda w: cy(w["u"]))
    lines = []
    for w in ws:
        h = w["u"][3] - w["u"][1]
        home = None
        for L in reversed(lines[-6:]):
            if abs(cy(w["u"]) - L["cy"]) < 0.45 * max(h, L["h"]) and not any(
                    min(w["u"][2], x["u"][2]) - max(w["u"][0], x["u"][0]) > 0.5 for x in L["w"]):
                home = L
                break
        if home:
            home["w"].append(w)
            home["cy"] = sum(cy(x["u"]) for x in home["w"]) / len(home["w"])
            home["h"] = max(home["h"], h)
        else:
            lines.append({"w": [w], "cy": cy(w["u"]), "h": h})
    lines.sort(key=lambda L: L["cy"])
    for L in lines:
        L["w"].sort(key=lambda w: w["u"][0])
        L["y0"] = min(w["u"][1] for w in L["w"])
        L["y1"] = max(w["u"][3] for w in L["w"])
    return lines


def union(us):
    return [min(u[0] for u in us), min(u[1] for u in us), max(u[2] for u in us), max(u[3] for u in us)]


# ---------- matching ----------
def best_seq(words, target, min_ratio, max_start=None):
    """Best run of consecutive words (in one line) whose text matches target.

    max_start limits where the run may begin (row labels lead their line)."""
    tgt = simple(target)
    if not tgt:
        return None
    n = len(tgt.split())
    best = None
    for k in range(max(1, n - 2), n + 3):
        for i in range(0, (max(0, len(words) - k) if max_start is None else min(max_start, max(0, len(words) - k))) + 1):
            run = words[i:i + k]
            if not run:
                continue
            txt = simple(" ".join(w["t"] for w in run))
            ratio = SequenceMatcher(None, tgt, txt).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, run)
    return best if best and best[0] >= min_ratio else None


def find_row(lines, label):
    """Row label: best leading-text match; a wrapped label may join the next line.

    For a grouped label ('Within level: Intergroup contact', 'School / Girls') the last part is
    the row, taken from the first few lines below a match of the group part."""
    if not label:
        return None
    parts = [p.strip() for p in re.split(r"\s*[/;:]\s*", norm_text(label)) if p.strip()]
    if len(parts) > 1:
        best = None
        for g in _row_cands(lines, parts[0]):
            for r in _row_cands(lines, parts[-1]):
                if 0 < r[1] - g[1] <= 12:
                    sc = g[0] + r[0] - 0.01 * (r[1] - g[1])
                    if best is None or sc > best[0]:
                        best = (sc, r)
        if best:
            return _row_result(lines, best[1])
    c = _row_cands(lines, label)
    return _row_result(lines, max(c, key=lambda x: x[0])) if c else None


def find_rows(lines, label):
    """All row matches within 0.02 of the best (a label repeated per study or panel)."""
    best = find_row(lines, label)
    if not best or re.search(r"[/;:]", norm_text(label or "")):
        return [best] if best else []
    c = _row_cands(lines, label)
    top = max(x[0] for x in c)
    return [_row_result(lines, x) for x in sorted(c, key=lambda x: x[1]) if x[0] >= top - 0.02]


def _row_cands(lines, label, min_ratio=0.72):
    """(ratio, line index, words) for every line whose leading text matches the label."""
    out = {}
    for v in label_variants(label):
        for i, L in enumerate(lines):
            lead = []
            for w in L["w"]:
                opened = sum(x["t"].count("(") - x["t"].count(")") for x in lead) > 0
                if num_key(w["t"]) and lead and not opened:  # '(Time 2)' is part of the label
                    break
                lead.append(w)
            # a group label may share the line, separated by a wide gap: each segment can lead
            segs, cur = [], []
            for w in lead:
                if cur and w["u"][0] - cur[-1]["u"][2] > 1.5 * L["h"]:
                    segs.append(cur); cur = []
                cur.append(w)
            if cur:
                segs.append(cur)
            cands = segs if len(segs) > 1 else [lead]
            if i + 1 < len(lines):
                nxt = [w for w in lines[i + 1]["w"] if not num_key(w["t"])]
                if nxt and nxt[0]["u"][0] >= lead[0]["u"][0] - 2:
                    cands.append(lead + nxt[: len(v.split())])
            for c in cands:
                m = best_seq(c, v, 0.0, max_start=2)  # allow an enumeration token or two first
                if not m or not any(w in L["w"] for w in m[1]):
                    continue  # a match lying wholly on the next line belongs to that line
                ratio = m[0] * (1 if len(simple(v)) > 3 else 0.9) * variant_weight(label, v)
                # table rows carry values: prefer label lines followed by numbers
                if not any(num_key(w["t"]) for w in L["w"][len(lead):]):
                    ratio -= 0.12
                en = enum_number(label)
                if en and lead and norm_text(lead[0]["t"]).strip("().") == en:
                    ratio += 0.05
                if ratio >= min_ratio and (i not in out or ratio > out[i][0]):
                    out[i] = (ratio, i, m[1])
    return list(out.values())


def _row_result(lines, cand):
    ratio, i, run = cand
    ys = [lines[i]]
    if i + 1 < len(lines) and any(w in lines[i + 1]["w"] for w in run):
        ys.append(lines[i + 1])
    return {"line": i, "lines": [i] if len(ys) == 1 else [i, i + 1], "box": union([w["u"] for w in run]), "y0": min(L["y0"] for L in ys),
            "y1": max(L["y1"] for L in ys), "ratio": round(ratio, 2)}


def find_col(lines, label, row):
    """Column header: fuzzy text above the row, or the column's enumeration number.

    A grouped header ('Negro / Civil Rights Attitude / Males', 'Low group: SD') is resolved level by level: at
    each level, of the best-matching headers, the one nearest the span of the level above wins."""
    if not label:
        return None
    above = [L for L in lines if row is None or L["y1"] <= row["y0"] + 1]
    parts = [p.strip() for p in re.split(r"\s*[/;:]\s*", norm_text(label)) if p.strip()]
    if len(parts) > 1:
        g = _col_cands(above, parts[0], None)
        if g:
            gb = max(g, key=lambda c: c[0])[1]
            for part in parts[1:]:
                subs = [c for c in _col_cands(above, part, row) if c[1][1] >= gb[1] - 1]
                if not subs:
                    break
                top = max(c[0] for c in subs)  # 'Attitude' must not lose to a nearer 'Activity'
                subs = [c for c in subs if c[0] >= (top - 0.05 if top < 0.99 else 0.99)]
                def gap(c, gb=gb):
                    x = (c[1][0] + c[1][2]) / 2
                    return max(0, gb[0] - x, x - gb[2])
                gb = min(subs, key=lambda c: (gap(c), -c[0]))[1]
            else:
                return {"box": gb, "cx": (gb[0] + gb[2]) / 2}
    c = _col_cands(above, label, row)
    if not c:
        return None
    top = max(x[0] for x in c)
    # an exact header wins outright; otherwise, of the near-best, the one nearest the body
    bx = max([x for x in c if x[0] >= (top - 0.05 if top < 0.99 else 0.99)], key=lambda x: x[1][3])[1]
    return {"box": bx, "cx": (bx[0] + bx[2]) / 2}


def _col_cands(above, label, row):
    """(ratio, box) header matches near the row; an enumeration number counts as 0.9.

    Header text may stack over two or three lines; words of the following lines that overlap
    a run horizontally are joined to it."""
    xmin = row["box"][2] if row else -1
    above = above[-25:]  # the table's own header, not running heads or body text
    out = []
    for v in label_variants(label):
        n = len(simple(v).split())
        wt = variant_weight(label, v)
        if not simple(v):
            continue  # punctuation only: no evidence
        for k, L in enumerate(above):
            ws = [w for w in L["w"] if w["u"][0] > xmin]
            if len(simple(v)) <= 2:  # short headers (N, M, SD, r, β): exact words only
                out += [(wt, w["u"]) for w in ws if simple(w["t"]) == simple(v)]
                continue
            for i in range(len(ws)):
                for j in range(i + 1, min(len(ws), i + n + 2) + 1):
                    run = ws[i:j]
                    for depth in range(0, 3):
                        stack = list(run)
                        for L2 in above[k + 1:k + 1 + depth]:
                            span = union([w["u"] for w in stack])
                            stack += [w for w in L2["w"] if min(w["u"][2], span[2]) - max(w["u"][0], span[0]) > 0]
                        txt = simple(" ".join(w["t"] for w in stack))
                        r = SequenceMatcher(None, simple(v), txt).ratio()
                        if r >= 0.75:
                            out.append((r * wt, union([w["u"] for w in stack])))
    num = enum_number(label)
    if num:
        # a correlation matrix numbers its columns: a header line of mostly bare integers
        for L in reversed(above):
            ints = [w for w in L["w"] if re.fullmatch(r"\(?\d+[.)]?", norm_text(w["t"]))]
            if len(ints) >= 3 and len(ints) >= 0.6 * len(L["w"]):
                hit = [w for w in ints if re.sub(r"\D", "", w["t"]) == num and w["u"][0] > xmin]
                if hit:
                    out.append((1.01, hit[0]["u"]))  # beats a fuzzy text match
                    break
    return out


def digits(k):
    return re.sub(r"\D", "", k or "")


def locate_value(lines, item, ocr=False):
    """Locate a numeric quote as a table cell. Results flagged `verify` matched the digits but
    not the exact printed value (sign or punctuation); process() checks them by OCR."""
    words = [w for L in lines for w in L["w"]]
    line_of = {id(w): i for i, L in enumerate(lines) for w in L["w"]}
    key = first_number(item["quote"])
    if not key:
        return None
    hits = [w for w in words if num_key(w["t"]) == key]
    notes, loose, verify = [], False, False
    if not hits and key.startswith("-"):
        hits = [w for w in words if num_key(w["t"]) == key[1:]]
        if hits:
            verify = True
            notes.append("minus sign not in the text layer; matched the absolute value")
    if not hits and ocr:
        # OCR often drops the decimal point or the minus: match the digits, but only where
        # the row or column confirms the cell
        hits = [w for w in words if digits(num_key(w["t"])) == digits(key)]
        if hits:
            loose = verify = True
            notes.append("OCR digits match without the exact punctuation")
    if not re.search(r"[=<>]", item["quote"]):
        # a bare value is never the threshold of a significance note ('p < .01')
        prev = {id(b): a for L in lines for a, b in zip(L["w"], L["w"][1:])}
        hits = [w for w in hits if not (id(w) in prev and re.search(r"[<>=]$|^p$", norm_text(prev[id(w)]["t"])))]

    def out(status, boxes, row, extra=(), cands=None):
        r = {"status": status, "boxes": boxes, "row": row, "notes": notes + list(extra), "verify": verify}
        if cands:
            r["cands"] = cands
        return r

    def text_in(box):
        return " ".join(x["t"] for x in words if box[0] - 1 <= (x["u"][0] + x["u"][2]) / 2 <= box[2] + 1
                        and box[1] - 1 <= cy(x["u"]) <= box[3] + 1)

    rows = find_rows(lines, item.get("row"))
    row = rows[0] if rows else None
    if len(rows) > 1:
        holding = [r for r in rows if any(line_of.get(id(w)) in r["lines"] for w in hits)]
        if len(holding) == 1:
            row = holding[0]
            notes.append("row label repeats on the page; took the occurrence that holds the value")
        elif len(holding) > 1:
            cands = [w["u"] for w in hits if any(line_of.get(id(w)) in r["lines"] for r in holding)]
            return out("ambiguous", [], None, ["row label repeats and each occurrence holds the value"], cands)
    col = find_col(lines, item.get("col"), row)
    if loose and not row:
        return out("not_found", [], row, ["loose OCR match without a matched row"])
    if not hits:
        return out("not_found", [], row, [f"value {key} not on page"])

    label_x = row["box"][2] if row else None

    def foreign_row(w):
        """w's visual line carries another row label: a word starting in the label column
        (allowing for indented sub-rows)."""
        i = line_of.get(id(w))
        if row is None or i is None or i in row["lines"]:
            return False
        h = row["y1"] - row["y0"]
        # the label's own wrapped continuation ('women (n = 7)'): the line starts with a word of
        # the requested label that the matched label lacks
        part = re.split(r"\s*[/;:]\s*", norm_text(item.get("row") or ""))[-1]
        missing = set(simple(part).split()) - set(simple(text_in(row["box"])).split())
        first = [x for x in lines[i]["w"] if simple(x["t"])]
        if i == row["lines"][-1] + 1 and first and simple(first[0]["t"]) in missing:
            return False
        return any(not num_key(x["t"]) and x["u"][0] < row["box"][0] + 3 * h and simple(x["t"]) for x in lines[i]["w"])

    # other headers beside the matched one, on the header's lowest line: a value lying under one
    # of them and not under the matched header belongs to that column. (Distance to the header
    # centres is no guide: 'M (SD)' cells are wider than their header, and right-aligned
    # numbers sit off-centre.)
    others = []
    if col:
        hl = [L for L in lines if col["box"][1] - 1 <= L["cy"] <= col["box"][3] + 1]
        if hl:
            gap = 0.5 * hl[-1]["h"]  # words closer than this to the header continue it ('(Step 2)')
            others = [x["u"] for x in hl[-1]["w"] if simple(x["t"])
                      and (x["u"][0] > col["box"][2] + gap or x["u"][2] < col["box"][0] - gap)
                      and (label_x is None or x["u"][0] > label_x)]

    def owns_col(w):
        under = lambda b: min(w["u"][2], b[2]) - max(w["u"][0], b[0]) > 0
        return under(col["box"]) or not any(under(o) for o in others)

    def paren_numbers(text):
        return set(re.findall(r"\d+", " ".join(re.findall(r"\(([^)]*)\)", norm_text(text)))))

    def printed_near(box):  # the matched label and the words continuing it on its line(s)
        g = box[3] - box[1]
        return " ".join(x["t"] for x in words if box[0] - g <= (x["u"][0] + x["u"][2]) / 2 <= box[2] + 2 * g
                        and box[1] - 1 <= cy(x["u"]) <= box[3] + 1)
    for what, lab, m in (("row", item.get("row"), row), ("column", item.get("col"), col)):
        if m and lab:
            # a printed qualifier that differs ('(Time 1)' for '(Time 2)'): a qualifier the
            # coder added and the table does not print is no conflict
            printed = paren_numbers(printed_near(m["box"]))
            if printed and paren_numbers(lab) - printed:
                notes.append(f"{what} label's printed qualifier differs from the requested one")

    in_row = []
    if row:
        # values on the row's own visual line(s); the y-range test is only a fallback, since
        # text-layer boxes of adjacent rows can overlap
        in_row = [w for w in hits if line_of.get(id(w)) in row["lines"] and w["u"][0] >= row["box"][0]]
        if not in_row:
            pad = 0.2 * (row["y1"] - row["y0"])
            in_row = [w for w in hits if row["y0"] - pad <= cy(w["u"]) <= row["y1"] + pad
                      and w["u"][0] >= row["box"][0] and not foreign_row(w)]

    def near_col(ws):
        return min(ws, key=lambda w: abs((w["u"][0] + w["u"][2]) / 2 - col["cx"]))
    if in_row and col:
        w = near_col(in_row)
        colw = max(col["box"][2] - col["box"][0], 2.5 * (w["u"][3] - w["u"][1]))
        if abs((w["u"][0] + w["u"][2]) / 2 - col["cx"]) <= 1.2 * colw and owns_col(w):
            return out("cell", [w["u"]], row)
        return out("ambiguous", [], row, ["the value in this row is not under the matched column"],
                   [x["u"] for x in in_row])
    if in_row:
        if len(in_row) == 1:
            return out("row", [in_row[0]["u"]], row)
        return out("ambiguous", [], row, ["value occurs more than once in the row; no column match"],
                   [x["u"] for x in in_row])
    if col:
        colw = max(8.0, col["box"][2] - col["box"][0])
        in_col = [w for w in hits if abs((w["u"][0] + w["u"][2]) / 2 - col["cx"]) < 1.5 * colw
                  and w["u"][1] >= col["box"][1] and owns_col(w)]
        if row:
            # the value sits under the column within a line of the labelled row (labels and
            # values are often set on slightly different baselines), not on another row's line
            h = row["y1"] - row["y0"]
            near = [w for w in in_col if row["y0"] - h <= cy(w["u"]) <= row["y1"] + h and not foreign_row(w)]
            if len(near) == 1 and not loose:
                return out("cell", [near[0]["u"]], row)
        elif len(in_col) == 1:
            return out("column", [in_col[0]["u"]], None, ["row label not matched"])
    if row and row["ratio"] >= 0.9:
        # the labelled row exists but does not hold the value: another cell is no evidence
        return out("not_found", [], row, ["value not in the matched row"])
    if len(hits) == 1 and not loose:
        return out("value", [hits[0]["u"]], row, ["matched the only occurrence of the value; row/column not confirmed"])
    return out("ambiguous", [], row, [f"value occurs {len(hits)} times; row/column did not decide"],
               [w["u"] for w in hits])


NEGATIONS = {"not", "no", "non", "never", "nor", "neither", "without", "cannot", "nt"}


def content_key(text):
    """Numbers and negations of a passage: a near match must not differ in these."""
    toks = simple(text.replace("n't", " nt").replace("n’t", " nt")).split()
    return sorted(k for k in (num_key(t.rstrip(".")) for t in toks) if k), sorted(t for t in toks if t in NEGATIONS)


def locate_text(lines, quote, row_label=None):
    """Passage match over word windows of flexible length. Windows that differ from the quote
    in a number or a negation do not count as matches. With a row label, the occurrence on or
    nearest below that row wins (scale anchors often repeat row after row); without one, a
    passage found more than once is ambiguous."""
    words = [w for L in lines for w in L["w"]]
    line_of = {id(w): i for i, L in enumerate(lines) for w in L["w"]}
    tq = " ".join(simple(quote).split())
    n = len(tq.split())
    if not n:
        return None
    qkey = content_key(quote)
    flat = [simple(w["t"]) for w in words]
    cands, misses = [], []
    for i in range(len(words)):
        for k in range(max(1, n - 2), n + 3):
            seg = " ".join(flat[i:i + k])
            r = SequenceMatcher(None, tq, seg).ratio()
            if r >= 0.75:
                if content_key(" ".join(w["t"] for w in words[i:i + k])) == qkey:
                    cands.append((r, i, k))
                else:
                    misses.append((r, i, k))

    def boxes(c):
        by_line = {}
        for w in words[c[1]:c[1] + c[2]]:
            by_line.setdefault(line_of[id(w)], []).append(w["u"])
        return [union(v) for v in by_line.values()]
    near_miss = max(misses) if misses else None
    if not cands:
        if near_miss:
            return {"status": "unverified", "boxes": boxes(near_miss), "row": None,
                    "notes": [f"closest passage ({near_miss[0]:.2f}) differs from the quote in a number or negation"]}
        return {"status": "not_found", "boxes": [], "row": None, "notes": ["quote not found"]}
    top = max(c[0] for c in cands)
    good = sorted([c for c in cands if c[0] >= top - 0.03], key=lambda c: (-c[0], abs(c[2] - n)))
    occ = []  # distinct occurrences: overlapping windows are one
    for c in good:
        if not any(c[1] < o[1] + o[2] and o[1] < c[1] + c[2] for o in occ):
            occ.append(c)
    row = find_row(lines, row_label) if row_label else None
    notes = []
    if row and len(occ) > 1:
        def dist(c):
            d = line_of[id(words[c[1]])] - row["line"]
            return (0 if d >= 0 else 1, abs(d), -c[0])
        best = min(occ, key=dist)
    elif len(occ) > 1:
        return {"status": "ambiguous", "boxes": [], "row": None, "cands": [boxes(c) for c in occ],
                "notes": [f"the passage occurs {len(occ)} times; give `row` or `choose`"]}
    else:
        best = occ[0]
    # a truncated window must not evade a better-matching number/negation mismatch over the same words
    over = [m for m in misses if m[0] > best[0] and best[1] < m[1] + m[2] and m[1] < best[1] + best[2]]
    if over:
        m = max(over)
        return {"status": "unverified", "boxes": boxes(m), "row": None,
                "notes": [f"the closest passage ({m[0]:.2f}) differs from the quote in a number or negation"]}
    if best[0] < 1:
        notes.append(f"close, not exact, match ({best[0]:.2f})")
    return {"status": "text", "boxes": boxes(best), "row": None, "notes": notes}


# ---------- per item ----------
CONFIDENT = ("cell", "row", "column", "text")
_DOCS = {}


def open_pdf(path):
    if path not in _DOCS:
        _DOCS[path] = fitz.open(path)
    return _DOCS[path]


def locate_on_page(page, item, zoom, numeric, first):
    """(result, lines, rotation, source) for one page: the text layer first, then OCR when the
    text layer is empty or cannot place the evidence (scans, tables embedded as images)."""
    raw = text_words(page)
    sparse = sum(len(w["t"]) for w in raw) <= 200  # a scan, perhaps with a stamped header

    def run(ws, ocr, r):
        ls = lines_of(ws)
        rr = locate_value(ls, item, ocr=ocr) if numeric else locate_text(ls, item.get("quote", ""), item.get("row"))
        return rr or {"status": "not_found", "boxes": [], "row": None, "notes": ["empty quote"]}, ls

    def best_of(ws, ocr, r):
        # a supplied clip marks the intended table: search it first, since row labels often
        # repeat in another table on the same page
        if item.get("clip") and first:
            c = clip_transform(page, zoom, r)(item["clip"])
            sub = [w for w in ws if c[0] - 4 <= (w["u"][0] + w["u"][2]) / 2 <= c[2] + 4 and c[1] - 4 <= cy(w["u"]) <= c[3] + 4]
            if sub:
                rr, ls = run(sub, ocr, r)
                if rr["status"] not in ("not_found", "ambiguous"):
                    return rr, ls
        return run(ws, ocr, r)

    res = lines = None
    r = 0
    if raw:
        _, vec = make_transform(page, zoom, 0)
        r = item["rotate"] if item.get("rotate") is not None else auto_rotation(raw, vec)
        T, _ = make_transform(page, zoom, r)
        res, lines = best_of(merge_minus([{"u": T(w["r"]), "t": w["t"]} for w in raw]), False, r)
        if res["status"] not in ("not_found", "ambiguous"):
            return res, lines, r, "text"
    if not shutil.which("tesseract"):
        if res is None:
            return {"status": "not_found", "boxes": [], "row": None, "notes": ["no text layer and no Tesseract"]}, [], 0, "ocr"
        return res, lines, r, "text"
    if sparse:
        # orientation from OCR confidence, since a scan has no text direction to read
        rots = [item["rotate"]] if item.get("rotate") is not None else [0, 90, 270]
        best = None
        for r_ in rots:
            ws = ocr_words(page, r_, zoom)
            score = sum(w["conf"] for w in ws if len(w["t"]) > 2)
            if best is None or score > best[0]:
                best = (score, r_, ws)
            if score > 4000 and r_ == 0:
                break
        _, ro, ws = best
        ores, olines = best_of(merge_minus(ws), True, ro)
        if res is None or ores["status"] not in ("not_found", "ambiguous"):
            return ores, olines, ro, "ocr"
        return {**res, "notes": res["notes"] + ["OCR: " + "; ".join(ores["notes"])]}, lines, r, "text"
    # the table may be an embedded image inside an otherwise text-based page
    for dpi in (300, 450):  # small print in embedded images often needs the finer pass
        ores, olines = best_of(merge_minus(ocr_words(page, r, zoom, dpi)), True, r)
        if ores["status"] not in ("not_found", "ambiguous"):
            return ores, olines, r, "ocr"
    return {**res, "notes": res["notes"] + ["OCR: " + "; ".join(ores["notes"])]}, lines, r, "text"


def verify_value(page, r, zoom, u, key):
    """OCR a 600 dpi window around a boxed token (with room for a minus sign on its left) and
    report whether it reads exactly as the quoted value."""
    h = u[3] - u[1]
    read = []
    for k in (1, 2):  # Tesseract often misreads a lone token; more context can help
        words = local_ocr(page, r, zoom, (u[0] + u[2]) / 2 - 0.5 * h, cy(u), k * ((u[2] - u[0]) / 2 + 2 * h), k * 1.5 * h)
        on = [w for w in words if overlap(w["u"], u) > 0.3]
        if any(num_key(w["t"]) == key for w in on):
            return True, [w["t"] for w in on]
        read = read or [w["t"] for w in on]
    return False, read


def process(item, outdir, preview, grid=None, written=None):
    doc = open_pdf(item["pdf"])
    zoom = DPI / 72
    tol = int(item.get("pages_tolerance", 1))
    p0 = int(item["page"])
    order = [p0] + [p for d in range(1, tol + 1) for p in (p0 + d, p0 - d)]
    order = [p for p in order if 1 <= p <= len(doc)]
    numeric = is_numeric_quote(item.get("quote", ""))
    tried = []
    for pno in order:
        page = doc[pno - 1]
        res, lines, r, source = locate_on_page(page, item, zoom, numeric, pno == p0)
        tried.append((pno, res, r, page, source, lines))
        # on the stated page any located match counts; a neighbouring page needs the row or
        # column to confirm it (a bare '19' elsewhere is no evidence)
        if res["status"] in (CONFIDENT if pno != p0 else CONFIDENT + ("value", "ambiguous")):
            break  # an ambiguous stated page is not replaced by a lone match on a neighbour
    pick = [t for t in tried if t[1]["status"] in (CONFIDENT if t[0] != p0 else CONFIDENT + ("value",))]
    pno, res, r, page, source, lines = pick[0] if pick else tried[0]
    im = render_upright(page, zoom, r)
    W, H = im.size
    if item.get("clip") and pno == p0:
        crop = clip_transform(page, zoom, r)(item["clip"])
    else:
        crop = auto_clip(lines, res, W)
    crop = [max(0, crop[0]), max(0, crop[1]), min(W, crop[2]), min(H, crop[3])]
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    pad = 2 * zoom
    cands = res.get("cands") or []
    if item.get("choose"):
        k = int(item["choose"])
        if 1 <= k <= len(cands):
            c = cands[k - 1]
            res = {"status": "picked", "boxes": c if isinstance(c[0], list) else [c], "row": None,
                   "verify": res.get("verify"), "notes": res["notes"] + [f"candidate {k} of {len(cands)} chosen by hand"]}
        else:
            res = {**res, "notes": res["notes"] + [f"choose={k} but there are {len(cands)} candidates"]}
    elif item.get("near") or item.get("box"):
        res = visual_pick(item, res, lines, page, r, zoom, crop, numeric)
    if res.get("verify") and res["boxes"] and res["status"] in CONFIDENT + ("value", "picked"):
        key = first_number(item["quote"])
        ok, read = verify_value(page, r, zoom, res["boxes"][0], key) if shutil.which("tesseract") else (False, [])
        if ok:
            res = {**res, "notes": res["notes"] + ["600 dpi OCR confirms the printed value"]}
        else:
            res = {**res, "status": "unverified", "notes": res["notes"] + [
                f"600 dpi OCR does not confirm {key} (read {' '.join(read) or 'nothing'}); check the sign and decimals by eye"]}

    def pct(u):
        return [round((u[0] - pad - crop[0]) / cw * 100, 2), round((u[1] - pad - crop[1]) / ch * 100, 2),
                round((u[2] - u[0] + 2 * pad) / cw * 100, 2), round((u[3] - u[1] + 2 * pad) / ch * 100, 2)]

    def inside(u):  # the whole box, not just its centre, must be on the crop
        return crop[0] - 1 <= u[0] and u[2] <= crop[2] + 1 and crop[1] - 1 <= u[1] and u[3] <= crop[3] + 1
    boxes = [u for u in res["boxes"] if inside(u)]
    notes = list(res["notes"])
    status = res["status"]
    if len(boxes) < len(res["boxes"]):
        notes.append("part of the located evidence lies outside the clip")
        status = "outside_clip"
    if grid and status not in GOOD:
        grid_image(im.crop(tuple(int(v) for v in crop))).save(os.path.join(grid, f"{item['id']}.png"))
    if res.get("row") and boxes and status in ("cell", "row", "unverified") and inside(res["row"]["box"]):
        boxes = boxes + [res["row"]["box"]]
    out = im.crop(tuple(int(v) for v in crop))
    img_name = item.get("img") or f"{item['id']}.jpg"
    if written is not None:
        ident = [os.path.abspath(item["pdf"]), pno, r, [int(v) for v in crop], DPI]
        stem, ext = os.path.splitext(img_name)
        name, n = img_name, 1
        while written.setdefault(name, {"ident": ident, "ids": []})["ident"] != ident:
            name = f"{stem}-{item['id']}{ext}" if n == 1 else f"{stem}-{item['id']}-{n}{ext}"
            n += 1
        if name != img_name:
            notes.append(f"img {img_name} already holds a different crop; written as {name}")
            img_name = name
        written[img_name]["ids"] = sorted(set(written[img_name]["ids"]) | {item["id"]})
    out.save(os.path.join(outdir, img_name), quality=80)
    cand_boxes = [[c] if not isinstance(c[0], list) else c for c in cands] if status == "ambiguous" else []
    if preview:
        pv = out.copy()
        d = ImageDraw.Draw(pv)
        for u in boxes:
            d.rectangle([u[0] - crop[0] - pad, u[1] - crop[1] - pad, u[2] - crop[0] + pad, u[3] - crop[1] + pad], outline=(220, 0, 0), width=2)
        for k, c in enumerate(cand_boxes, 1):  # numbered candidates, for `choose`
            for u in c:
                d.rectangle([u[0] - crop[0] - pad, u[1] - crop[1] - pad, u[2] - crop[0] + pad, u[3] - crop[1] + pad], outline=(255, 140, 0), width=2)
            d.text((c[0][0] - crop[0] - pad, c[0][1] - crop[1] - pad - 11), str(k), fill=(255, 100, 0))
        pv.save(os.path.join(preview, f"{item['id']}.png"))
    if pno != p0:
        notes.append(f"found on page {pno}, not {p0}")
    result = {"id": item["id"], "img": img_name, "page": pno, "rotate": r,
              "clip": item.get("clip") if (item.get("clip") and pno == p0) else None,  # displayed points, as supplied
              "boxes": [pct(u) for u in boxes] if status != "not_found" else [],
              "status": status if (boxes or status in ("not_found", "ambiguous", "outside_clip")) else "outside_clip",
              "source": source, "notes": notes}
    if cand_boxes:
        result["candidates"] = [[pct(u) for u in c] for c in cand_boxes]
    result["review"] = not (result["status"] in CONFIDENT and not notes)
    return result


def auto_clip(lines, res, W):
    """Table region around the located boxes: caption/headers above, a few lines below."""
    if not lines:
        return [0, 0, W, W]
    xs0 = min(w["u"][0] for L in lines for w in L["w"])
    xs1 = max(w["u"][2] for L in lines for w in L["w"])
    us = list(res["boxes"]) + ([res["row"]["box"]] if res.get("row") else [])
    if not us:
        return [xs0 - 6, lines[0]["y0"] - 6, xs1 + 6, lines[-1]["y1"] + 6]
    top, bot = min(u[1] for u in us), max(u[3] for u in us)
    idx = [i for i, L in enumerate(lines) if L["y1"] >= top - 1 and L["y0"] <= bot + 1]
    i0, i1 = min(idx), max(idx)
    for j in range(i0, -1, -1):  # walk up to the table caption, at most 25 lines
        if re.match(r"^(table|tab\.)\s*\w", " ".join(w["t"] for w in lines[j]["w"]).lower()) or i0 - j > 25:
            i0 = j
            break
        i0 = j
    i1 = min(len(lines) - 1, i1 + 2)
    return [xs0 - 6, lines[i0]["y0"] - 6, xs1 + 6, lines[i1]["y1"] + 6]


GOOD = ("cell", "row", "column", "text", "value", "picked", "picked_unread", "manual")


def visual_pick(item, res, lines, page, r, zoom, crop, numeric):
    """Fallback after a visual reading of the crop. `near` = [x%, y%] of the crop image marks
    the cell; the box snaps to the nearest word there carrying the quoted value (text layer,
    then page OCR, then a 600 dpi OCR window). A word with the quoted digits but another sign
    or decimal point is `unverified`; if OCR cannot read the value, the box goes on the token
    under the point (`picked_unread`). `box` = [left%, top%, width%, height%] is used as given
    when nothing is found."""
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    if item.get("near"):
        px, py = crop[0] + item["near"][0] / 100 * cw, crop[1] + item["near"][1] / 100 * ch
        rx, ry = 0.08 * cw, 0.04 * ch
        def close(w):
            return abs((w["u"][0] + w["u"][2]) / 2 - px) <= rx and abs(cy(w["u"]) - py) <= ry
        def d2(w):
            return ((w["u"][0] + w["u"][2]) / 2 - px) ** 2 + (cy(w["u"]) - py) ** 2
        key = first_number(item["quote"]) if numeric else None
        exact = lambda w: key is not None and num_key(w["t"]) == key
        same_digits = lambda w: key is not None and digits(num_key(w["t"])).lstrip("0") == digits(key).lstrip("0")
        pools = [lambda: [w for L in lines for w in L["w"]],  # cheapest first
                 lambda: merge_minus(ocr_words(page, r, zoom)),
                 lambda: local_ocr(page, r, zoom, px, py, rx, ry),
                 lambda: local_ocr(page, r, zoom, px, py, 2 * rx, 2 * ry)]  # more context can help
        near_digits, local = [], None
        for get in pools:
            ws = [w for w in get() if close(w)]
            if get is pools[2]:
                local = ws
            if any(exact(w) for w in ws):
                return {"status": "picked", "boxes": [min(filter(exact, ws), key=d2)["u"]], "row": None,
                        "notes": ["placed from a visual pick, snapped to the matching word"]}
            near_digits += [w for w in ws if same_digits(w)]
        if near_digits:
            w = min(near_digits, key=d2)
            return {"status": "unverified", "boxes": [w["u"]], "row": None,
                    "notes": [f"visual pick: the word there reads '{w['t']}', not {key}; check the sign and decimals by eye"]}
        # the point must lie on the token itself: a neighbouring cell is no fallback
        tokens = [w for w in (local or []) if re.search(r"\d" if numeric else r"\S", w["t"]) and
                  w["u"][0] - 0.01 * cw <= px <= w["u"][2] + 0.01 * cw and w["u"][1] - 0.01 * ch <= py <= w["u"][3] + 0.01 * ch]
        if tokens:
            w = min(tokens, key=d2)
            return {"status": "picked_unread", "boxes": [w["u"]], "row": None,
                    "notes": [f"OCR could not read the value (read '{w['t']}'); box on the token at the visual pick"]}
    if item.get("box"):
        l, t, wd, ht = item["box"]
        u = [crop[0] + l / 100 * cw, crop[1] + t / 100 * ch, crop[0] + (l + wd) / 100 * cw, crop[1] + (t + ht) / 100 * ch]
        return {"status": "manual", "boxes": [u], "row": None, "notes": ["box given by hand from a visual reading"]}
    return {**res, "notes": res["notes"] + ["visual pick: nothing found near the point; give `box`"]}


def local_ocr(page, r, zoom, px, py, rx, ry, dpi=600):
    """Tesseract on a small window around (px, py) (upright pixels at zoom), rendered alone at
    high resolution; words in upright pixel coords at zoom. Reads values the page-scale OCR
    misses (small print, figure labels, a dropped minus)."""
    if not shutil.which("tesseract"):
        return []
    clip = upright_to_display(page, zoom, r, [px - rx, py - ry, px + rx, py + ry])
    if clip.is_empty:
        return []
    win = render_upright(page, dpi / 72, r, clip=clip)
    x0, y0 = clip_transform(page, zoom, r)(list(clip))[:2]  # window origin in upright pixels
    f = dpi / 72 / zoom
    out = []
    for w in tesseract(win, min_conf=-1):
        u = w["u"]
        out.append({"u": [x0 + u[0] / f, y0 + u[1] / f, x0 + u[2] / f, y0 + u[3] / f], "t": w["t"]})
    return merge_minus(out)


def grid_image(im):
    """The crop with a labelled 5% grid, so a reader can give `near` coordinates."""
    g = im.convert("RGB").copy()
    d = ImageDraw.Draw(g)
    W, H = g.size
    for k in range(5, 100, 5):
        x, y = W * k / 100, H * k / 100
        col = (0, 110, 255) if k % 10 == 0 else (150, 200, 255)
        d.line([(x, 0), (x, H)], fill=col, width=1)
        d.line([(0, y), (W, y)], fill=col, width=1)
        if k % 10 == 0:
            d.text((x + 2, 2), str(k), fill=(0, 70, 200))
            d.text((2, y + 1), str(k), fill=(0, 70, 200))
    return g


def contact_sheet(res, preview, path, width=640, max_h=520):
    """Stack the previews (scaled to one width) under id/status labels; splits into numbered
    pages. A tall crop is cut to a window around its boxes rather than shrunk."""
    tiles = []
    for r in res:
        p = os.path.join(preview, f"{r['id']}.png")
        if not os.path.exists(p):
            continue
        im = Image.open(p)
        im = im.resize((width, max(1, int(im.height * width / im.width))))
        if im.height > max_h:
            bs = r.get("boxes") or [u for c in r.get("candidates", []) for u in c]
            mid = (sum(b[1] + b[3] / 2 for b in bs) / len(bs) / 100 * im.height) if bs else im.height / 2
            top = int(min(max(0, mid - max_h / 2), im.height - max_h))
            im = im.crop((0, top, width, top + max_h))
        lab = Image.new("RGB", (width, 22), (30, 30, 30))
        ImageDraw.Draw(lab).text((6, 4), f"{r['id']}  {r['status']}  {'; '.join(r.get('notes', []))[:90]}", fill=(255, 255, 255))
        tiles.append((lab, im))
    pages, cur, h = [], [], 0
    for lab, im in tiles:
        if cur and h + lab.height + im.height > 2400:
            pages.append(cur); cur, h = [], 0
        cur.append((lab, im)); h += lab.height + im.height + 8
    if cur:
        pages.append(cur)
    base, ext = os.path.splitext(path)
    for k, pg in enumerate(pages, 1):
        H = sum(l.height + i.height + 8 for l, i in pg)
        sheet = Image.new("RGB", (width, H), (255, 255, 255))
        y = 0
        for lab, im in pg:
            sheet.paste(lab, (0, y)); y += lab.height
            sheet.paste(im, (0, y)); y += im.height + 8
        sheet.save(f"{base}-{k}{ext}" if len(pages) > 1 else path, quality=75)


def main():
    global DPI
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("items", help="JSON file: list of items")
    ap.add_argument("--out", required=True, help="directory for the cropped images")
    ap.add_argument("--preview", help="directory for preview PNGs with the boxes drawn")
    ap.add_argument("--grid", help="directory for gridded crops of unresolved items, for a visual pick (see SKILL.md)")
    ap.add_argument("--sheet", help="contact sheet (JPEG) of the previews that need review (review: true)")
    ap.add_argument("--sheet-all", action="store_true", help="put every preview on the contact sheet")
    ap.add_argument("--results", default="-", help="where to write the results JSON (default stdout)")
    ap.add_argument("--dpi", type=int, default=125)
    a = ap.parse_args()
    DPI = a.dpi
    os.makedirs(a.out, exist_ok=True)
    for d in (a.preview, a.grid):
        if d:
            os.makedirs(d, exist_ok=True)
    items = json.load(open(a.items))
    # which crop each image file holds, kept across runs so a rerun of some items cannot
    # overwrite an image other items' boxes refer to
    ledger = os.path.join(a.out, "crops.json")
    written = json.load(open(ledger)) if os.path.exists(ledger) else {}
    ids = {it.get("id") for it in items}
    written = {k: v for k, v in written.items() if not set(v["ids"]) <= ids}  # rewritten in full now
    res = []
    for it in items:
        try:
            res.append(process(it, a.out, a.preview, a.grid, written))
        except Exception as e:  # keep going; report the failure per item
            res.append({"id": it.get("id"), "img": it.get("img"), "status": "error", "notes": [repr(e)], "boxes": [], "review": True})
    json.dump(written, open(ledger, "w"), indent=1)
    if a.sheet and a.preview:
        contact_sheet([r for r in res if a.sheet_all or r["review"]], a.preview, a.sheet)
    js = json.dumps(res, ensure_ascii=False, indent=1)
    (sys.stdout.write(js + "\n") if a.results == "-" else open(a.results, "w").write(js))
    from collections import Counter
    print(dict(Counter(r["status"] for r in res)), f"review: {sum(r['review'] for r in res)}", file=sys.stderr)


if __name__ == "__main__":
    main()
