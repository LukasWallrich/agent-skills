# Rubric: conflict-of-interest and funding statements (`coi_check`, `coi_check_oi`, `funding_check`, `funding_check_oi`)

Rubric version 1. Needs: the four module outputs (run all four; neither variant is a
superset of the other). No design brief needed.

## Purpose

The modules answer one question, "is there a statement?", and green means only that some
sentence matched. You (1) identify the actual statement among the candidates from **both**
generators, (2) extract structured content, and (3) distinguish three states the modules
collapse: a declaration of funding/conflicts, an explicit declaration of none, and no
statement in the checked content.

## What the modules give you

| module | method | table columns | `item_id` |
|---|---|---|---|
| `coi_check` | fuzzy matching plus positional heuristics tuned to test papers | `paper_id`, `text` (the assembled statement, may span sentences; **no `text_id`**) | `r<row>` |
| `coi_check_oi` | two keyword searches incl. headers (over-inclusive) | `text`, `text_id`, `header`, `section_type`, ... | `t<text_id>` |
| `funding_check` | ~35 regex patterns over a large synonym dictionary | `paper_id`, `text` (no `text_id`) | `r<row>` |
| `funding_check_oi` | sentences with a funding word and a study word; prefers funding/annex/acknowledgement sections | `text`, `text_id`, ... (only `paper_id`, `text` when empty) | `t<text_id>` |

All four lights: green if any row, red if none.

## Known failure modes

- COI full regex: fuzzy matching on short patterns gives false positives; statements
  without the keywords are missed ("the first author is employed by X", "Y holds shares
  in"); statement boundaries come from brittle positional rules.
- Funding full regex: typos in the grammar silently disable some patterns; the joiner
  class excludes periods, apostrophes and non-ASCII characters, so many funder names break
  the match; the "foundation" list matches any sentence containing "National" or "program".
- `_oi` variants: `funding_check_oi` matches e.g. "these results support the study
  hypothesis" when no funding section is detected; its section preference can drop a real
  statement located elsewhere (title-page footnote, author note).
- Saved comparisons show statements found **only** by the full regex and others only by
  `_oi`. Hence the union.
- Statements on title pages, in footnotes or in journal front matter are often lost in
  the parse; "no statement found" is therefore weak evidence.

## Procedure

1. **Union with provenance.** Pool the rows of both variants per topic. For full-regex
   rows, locate the `text_id`(s): `mc_context.R --run-dir $RUN --search "<distinctive 5-8 word phrase from text>"`.
   Deduplicate rows that point to the same sentence(s) and remember the generators:
   `["coi_check"]`, `["coi_check_oi"]` or both. Put that list into each finding's
   `deterministic.value`, e.g. `{"generators": ["coi_check", "coi_check_oi"], "matched":
   "<text>"}`. This provenance is what lets the evaluation measure what each generator
   contributes; do not omit it.
2. **Where findings go.** Write a finding for every candidate row in every module file
   (candidates must be accounted for). A duplicate gets the same verdict and
   classification as its twin, and its rationale names the twin's `candidate_id`.
3. **Pick the statement.** Read each pooled candidate in its paragraph/section
   (`--candidate-id` or `--text-id <n> --expand section`). Also open any section whose
   header suggests the topic (`--sections`: "Declaration of interests", "Disclosure",
   "Funding", "Acknowledg(e)ments", "Author note"). Determine the statement's boundaries:
   first to last sentence. Everything else is `not_the_statement`.
4. **Extract** (into `agent.extracted`; quote the spans as evidence):
   - Funding: `{"funders": [{"name": "...", "grant_ids": ["..."], "recipient": "..."}],
     "state": "funded | explicitly_unfunded | not_found"}`. Names and grant numbers exactly
     as written; do not expand abbreviations or "correct" names from memory; do not
     validate funders against registries (no tool for that here).
   - COI: `{"state": "none_declared | declared | boilerplate | not_found", "conflict_types":
     [...]}` with types from: `employment`, `consultancy_or_honoraria`,
     `equity_or_ownership`, `patent_or_royalties`, `industry_funding`,
     `editorial_or_reviewing_role`, `personal_or_family`, `other`. `boilerplate` = a generic
     template sentence that does not clearly state presence or absence (e.g. "Authors are
     required to disclose ...").
5. **Cross-check** (COI only; keep it factual). If the funding statement names a commercial
   funder, or an author affiliation in `paper.json` is a company, while the COI statement
   declares none: `possible_undeclared_interest`. Whether an entity is commercial must be
   evident from the paper's own words ("Inc.", "GmbH", "Pharmaceuticals", "funded by [the
   manufacturer of the product studied]"), not from your knowledge of the organisation.
6. **Nothing found.** If no candidate is a statement, search before concluding:
   - COI: `--search "conflicts? of interest|competing interests?|declaration of interest|disclos|financial interest|nothing to declare|employed by|consult(ant|ing) (for|to)|shares? in|honorari"`
   - Funding: `--search "fund(ed|ing|er)|financ|grant|award|fellowship|scholarship|supported by|support from|sponsor|no (specific|external) (funding|grant)"`
   Record them in `coverage.searches`. Result wording: "not found in checked content".

## Classification vocabulary (closed)

| classification | verdict | severity |
|---|---|---|
| `statement_funded` / `statement_explicitly_unfunded` | `not_an_issue` | `none` |
| `statement_coi_none_declared` / `statement_coi_declared` | `not_an_issue` | `none` (a declared conflict is good reporting, not a problem) |
| `statement_incomplete` - funding acknowledged without naming the funder, or grant mentioned without number where others have one; COI `boilerplate` | `confirmed_issue` | `low` |
| `possible_undeclared_interest` (step 5; quote both statements) | `confirmed_issue` | `medium`; always "worth checking" |
| `statement_not_found` (sweep finding; searches recorded) | `confirmed_issue` | `low`, coverage `partial` if title page/footnotes look lost |
| `not_the_statement` - candidate is a false positive or an acknowledgement of non-financial help | `not_applicable` | `none` |
| `duplicate_of_other_generator` - may be used instead of repeating the classification; rationale names the twin | same verdict as twin | same |
| `statement_unclear` | `insufficient_evidence` | `none` |

`statement_not_found` has no candidate: use `finding_id`
`<paper_id>:coi_check:sweep:statement` (or `funding_check`), `item_id` `"statement"`,
`"sweep": true`.

## When to abstain

`statement_unclear` when a candidate is cut off mid-statement by the parse, when a header
("Funding") exists with no text under it, or when an acknowledgement ("We thank the X
Foundation for their support") could be financial or not.

## Worked examples (invented illustrations)

- **Not an issue, structured.** Found by both generators: "This work was supported by the
  Dutch Research Council (NWO) under grant VI.Veni.201G.013 awarded to A.B." ->
  `statement_funded`; extraction: funder "Dutch Research Council (NWO)", grant
  "VI.Veni.201G.013", recipient "A.B."; generators both.
- **Not an issue, explicitly unfunded.** "This research received no specific grant from any
  funding agency." (found only by `funding_check`) -> `statement_explicitly_unfunded`. The
  original module light is the same green as for a funded study; your finding makes the
  distinction.
- **Not applicable.** `funding_check_oi` row: "These results support the hypothesis that
  the program improves retention." -> `not_the_statement`.
- **Confirmed, cautious.** COI: "The authors declare no competing interests." Funding:
  "The study was funded by NutriCo Inc., which also supplied the supplement." ->
  `possible_undeclared_interest`, `medium`. Advice: "The funding statement names NutriCo
  Inc. as funder and supplier of the product tested. Many journals ask for this to be
  listed under competing interests as well; worth checking the journal's policy."
- **Abstention.** Section header "Declaration of Competing Interest" is present in
  `--sections` with zero sentences. -> `statement_unclear`, limitation "section body
  missing from the parse".

## Advice phrasing

For missing statements: "No [funding/COI] statement was found in the parsed text. Title-page
footnotes are sometimes lost in conversion; if there is none, most journals require one,
including an explicit 'none'." Never imply concealment.
