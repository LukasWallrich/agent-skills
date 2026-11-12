---
name: pdf-evidence-crops
description: Crop source-PDF evidence (a table cell or a quoted passage) into page images with highlight boxes, for audit, coding-check or review pages. Use when showing where a value or quote sits in a PDF, or when evidence screenshots are cropped to the wrong place or lack highlights.
---

# PDF evidence crops

`pdf_evidence.py` turns evidence items into cropped page images plus highlight boxes, and reports
how confidently it located each one. It finds a table cell by intersecting the row label with the
column header (or the column's number in a correlation matrix), uses the text layer where the
value is in it, and falls back to Tesseract OCR for scans and for tables embedded as images. It
handles rotated pages and sideways tables, minus-sign variants, stars and footnote letters,
decimal commas, and `0.83` printed as `.83`. When it cannot tell which occurrence is meant, it
says so rather than drawing a box.

## Steps

1. **Write the items file**: a JSON list, one object per piece of evidence.

   | Field | Meaning |
   |---|---|
   | `id` | your key, echoed back |
   | `pdf`, `page` | source file and 1-based page (a neighbouring page is tried if the value is not there; `pages_tolerance`, default 1) |
   | `quote` | the value as printed (`−.35*`, `0.10 (0.06)`, `n = 80`, `F(2, 303) = 36.68`) or a passage of text |
   | `row`, `col` | the row label and the column header as printed; numbering (`2. Social policies`) is fine, and for a correlation matrix the column's number is matched against the header row |
   | `clip` | optional `[x0, y0, x1, y1]` in points of the page as displayed (PyMuPDF `page.rect`, after any `/Rotate`); omitted = from the table caption to two lines below the located row |
   | `rotate` | optional extra clockwise rotation (0/90/180/270); omitted = detected from the text direction |
   | `img` | output file name (default `<id>.jpg`); items sharing an `img` and `clip` share one crop |

   A numeric quote (digits plus at most a statistic letter) is located as a cell; anything with
   words is located as a passage.

2. **Run it** (Tesseract on the PATH enables OCR):

   ```sh
   uv run ~/.claude/skills/pdf-evidence-crops/pdf_evidence.py items.json \
     --out shots --preview preview --sheet preview/sheet.jpg --grid grid --results results.json
   ```

3. **Verify every crop.** Open the contact sheet (`--sheet`; each tile is labelled with its id
   and status) and look at each tile. The step is done when every `cell`, `row`, `column`,
   `value` and `text` tile shows its red box on the quoted value or passage, and every other
   status is either fixed (step 4) or listed for the reader as unhighlighted.

4. **Fix what failed**, using the `status` and `notes` in `results.json`, then rerun only those
   items:

   | Status | Meaning | Usual fix |
   |---|---|---|
   | `cell` | row and column both matched | — |
   | `row` / `column` | one axis matched, the other not | check the box; correct the other label to its printed wording |
   | `value` | the value occurs once on the page; neither axis confirmed | unreliable: a two-digit value often recurs in another cell or a note. Show the box only after checking it by eye |
   | `text` | passage matched (`notes` flags a fuzzy match) | — |
   | `ambiguous` | the value occurs several times and the labels do not decide | give the row/column label as printed, or a tighter `clip` |
   | `not_found` | value or passage not on the page (or the neighbours) | check `page`; quote the value exactly as printed; for scans, OCR may misread it |
| `outside_clip` | located, but outside the supplied `clip` | widen the clip, or drop it for the automatic one; a wrong match also lands here |
   | `error` | the item could not be processed | read `notes` (often a missing PDF) |
| `picked` / `picked_unread` / `manual` | placed from a visual pick (step 5) | check the preview |

   A note "minus sign not in the text layer" means the absolute value was matched: confirm the
   sign by eye, since extraction often drops minus signs.

5. **Pick the rest by eye.** For items still `ambiguous`, `not_found` or `outside_clip` (labels
   that repeat, headers that differ only in a prefix, unreadable print, values in figures), rerun
   them with `--grid grid/`. That writes each crop with a labelled 5% grid. View it, find the
   cell by reading the table, and add `"near": [x%, y%]` (the centre of the value) to the item.
   On rerun the box snaps to the word there that carries the quoted digits (status `picked`),
   using the text layer, page OCR, or a 600 dpi OCR window around the point. If OCR cannot read
   the value, the box goes on the numeric token under the point (`picked_unread`). As a last
   resort, `"box": [left%, top%, width%, height%]` is used as given (`manual`). Picks read off
   the grid are often a row out, so check each new preview before you count the item as done.

## Output

`results.json` holds, per item: `img`, `page` (where it was found), `rotate`, `clip`, `boxes`,
`status`, `source` (`text` or `ocr`) and `notes`. `boxes` are `[left, top, width, height]` in
percent of the written image: value boxes first, then the row label for table cells. Overlay them
on a relatively positioned wrapper:

```html
<span style="position:relative;display:inline-block">
  <img src="shots/x.jpg" style="display:block;max-width:100%">
  <span style="position:absolute;left:55.4%;top:27%;width:6.1%;height:9%;
               background:rgba(255,214,0,.35);outline:1px solid #d4a300"></span>
</span>
```
