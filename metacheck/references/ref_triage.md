# Rubric: reference triage (`ref_accuracy`, `ref_consistency`) and synthesis (`ref_summary`)

Rubric version 1. Needs: `modules/ref_accuracy.json`, `modules/ref_consistency.json`,
`modules/ref_summary.json`; for the synthesis, the findings from `citation_context.md`.

## Purpose

Both checking modules are noisy by their own account: `ref_accuracy` "has a high false
positive rate"; `ref_consistency` is "under development and should not be relied on" yet
turns red on a single unlinked item. You separate parser artefacts from errors the author
should fix, and then write the short ranked list that `ref_summary` currently lacks.

## Part 1: `ref_accuracy`

**Module output.** Needs the `bib_match` table (import with `--crossref-lookup`); otherwise
the module reports an error and the check is "could not check". One row per reference
(`bib_id`): parsed fields `doi.orig`, `title.orig`, `year.orig`, `authors.orig`; CrossRef
fields `doi.match`, `title.match`, `year.match`, `authors.match`; `text` (the raw reference
string); flags `doi_mismatch`, `year_mismatch`, `title_mismatch`, `author_mismatch`,
`no_match`. Light yellow if any flag. Adjudicate only rows with at least one flag true.

**Known failure modes.**
- Title: PDF artefacts (ligatures, hyphenation at line breaks, lost diacritics, subtitle
  cut at a colon, title and journal swapped by the reference parser).
- Year: online-first vs print year; CrossRef `issued` vs what the style guide wants.
- Authors: the check tests whether each CrossRef surname occurs as a **substring** of the
  parsed author string, one direction only ("Li" matches "Oliver"; extra or missing authors
  in the manuscript pass; particles, hyphens, transliteration and consortium names fail).
- **Wrong match.** References without DOI are matched by CrossRef title search (score >=
  50). A wrong match is then "confirmed" as a mismatch on every field, and its DOI flows
  into the retraction, replication and PubPeer joins. Check this first.
- `no_match` is expected for books, chapters, theses, reports, software, datasets,
  preprints on some servers, and non-English sources.

**Procedure.** `mc_context.R --run-dir $RUN --bib-id <n>` shows the parsed reference, the
raw string and the CrossRef match side by side. Compare using the **raw string `text`** as
the authority for what the authors wrote; the parsed fields are an intermediate product.
1. Same work? Compare first author, year (within a year or two), title gist, journal. If
   not: `wrong_match`; add to your group summary that downstream database hits for this
   `bib_id` are unreliable.
2. For each flag, find the differing characters. Artefact of extraction, a trivial variant
   (capitalisation, subtitle, "&" vs "and", abbreviated journal), an online-first year, or
   a difference a reader would call an error (wrong year by more than one, misspelt or
   missing first author, wrong DOI resolving to another paper, substantially different
   title)?
3. `doi_mismatch` where the raw string's DOI differs from CrossRef's: confirm what the
   manuscript's DOI points to with `mc_fetch.R --run-dir $RUN --op doi --doi <doi.orig>`.
   Never decide from memory what a DOI resolves to.

| classification | verdict | severity |
|---|---|---|
| `genuine_error` (say which field) | `confirmed_issue` | `medium` for wrong/dead DOI or wrong first author; `low` otherwise |
| `parse_artefact` | `not_an_issue` | `none` |
| `trivial_variant` | `not_an_issue` | `none` |
| `online_first_year` | `not_an_issue` | `none` |
| `wrong_match` | `not_applicable` | `none` |
| `no_match_expected` (book, thesis, report, software...) | `not_applicable` | `none` |
| `no_match_unexpected` - journal article with no CrossRef record found; could be a garbled or fabricated reference | `confirmed_issue` | `low`, advice "worth verifying that this reference exists as cited"; never say "fabricated" |
| `cannot_compare` | `insufficient_evidence` | `none` |

Abstain (`cannot_compare`) when the raw string is too damaged to read, or when two
plausible CrossRef works exist (e.g. article and its erratum, preprint and journal version).

## Part 2: `ref_consistency`

**Module output.** A full join of the bibliography and the importer's in-text citation
links, keeping the unmatched. Columns: `bib_id`, `reference` (NA when a citation has no
reference), `contents` (the in-text citation string, NA when a reference is never cited),
`text` (citing sentence). Rows with `bib_id` get `item_id` `b<bib_id>`; the others `r<row>`.
Light red if any row. All the intelligence sits in the PDF importer's citation linking,
which fails on narrative citations ("as Smith and Jones (2019) showed"), a/b suffixes, year
typos, "et al." variants, numeric ranges ("[3-7]"), and citations in tables and footnotes.

**Procedure.** First read `import_summary.json`: if citation linking broadly failed (few or
no `bibr` xrefs), do not adjudicate row by row; write nothing and report the whole check as
not performed. Otherwise:
- Reference never cited (`contents` NA): search for it.
  `mc_context.R --run-dir $RUN --search "<Surname>.{0,60}<year>"` (try the second author,
  and the year +/- one written out explicitly as alternatives, e.g. `(2018|2019|2020)`);
  numeric styles: `--search "\[[^\]]*\b<n>\b[^\]]*\]"` and remember ranges cover numbers
  that are not printed. Found -> `linked_by_search`. Found with a different year or
  spelling -> `citation_reference_discrepancy`.
- Citation without reference (`reference` NA): read `contents` and `text`. Is it a
  citation at all (or a year in parentheses, a statistic, a table cross-reference)? If it
  is, look for the reference by surname in the bibliography:
  `--search "<Surname>" --section-type references`.

| classification | verdict | severity |
|---|---|---|
| `reference_not_cited` - searches find no citation | `confirmed_issue` | `low` |
| `citation_without_reference` - a real citation, no matching entry found | `confirmed_issue` | `medium` |
| `citation_reference_discrepancy` - year/spelling/suffix differs between text and list (quote both) | `confirmed_issue` | `low` |
| `linked_by_search` - importer missed the link | `not_an_issue` | `none` |
| `not_a_citation` | `not_applicable` | `none` |
| `cannot_verify` (numeric ranges, reference list garbled) | `insufficient_evidence` | `none` |

Absence verdicts (`reference_not_cited`, `citation_without_reference`) need
`coverage.searches`. Tables and footnotes lost by the parse are a standing limitation.

## Part 3: `ref_summary` synthesis

The module merges the four reference tables by `bib_id` (`accuracy_mismatch`, `pubpeer`,
`replication_type`, `retractionwatch`) and shows the first rows. After all reference
findings are written, produce the list a reader wants: **references needing attention,
ranked, one line of reason each.** Write it as findings in `findings/ref_summary.json`, one
per reference that has at least one `confirmed_issue` in another reference module:

- `finding_id`: `<paper_id>:ref_summary:b<bib_id>:c1` if the `ref_summary` table has that
  row (it lists all references), verdict `confirmed_issue`, classification
  `needs_attention`, severity = the highest severity among that reference's findings.
- `rationale`: the one-liner, e.g. "Retracted; cited as support for H1 in the
  Introduction", "Failed replication (FLoRA); central to the effect-size assumption",
  "DOI resolves to a different article". Mention cleared flags only when it prevents
  needless work: "year mismatch is online-first; ignore".
- `evidence`: reuse the strongest quote from the underlying finding. `advice`: the action.
- Order matters to the reader but JSON arrays are rendered in order: sort by severity, then
  by whether the citation is load-bearing.

Do not introduce any new fact here; every statement must trace to an existing finding.

## Worked examples (invented illustrations)

- **Not an issue.** `title_mismatch`: parsed "The in uence of af liation on coping",
  CrossRef "The influence of affiliation on coping". -> `parse_artefact` (lost "fl"/"fi"
  ligatures). Quote the raw string from `module:ref_accuracy`.
- **Not an issue.** `year_mismatch` 2019 vs 2020, same DOI, same title. ->
  `online_first_year`.
- **Confirmed.** Raw string DOI 10.1037/abc0000123; `mc_fetch --op doi` returns a record
  with a different title and authors. -> `genuine_error` (DOI), `medium`. Advice: "The DOI
  given for Garcia (2017) resolves to a different article according to CrossRef; worth
  correcting."
- **Not applicable.** No-DOI reference to a 1994 book chapter matched by title search to a
  2011 journal article by other authors. -> `wrong_match`.
- **Abstention.** Reference 17 "never cited" in a numeric-style paper whose text contains
  "[12-19]". -> `cannot_verify`, limitation "numeric range citations cannot be resolved".
