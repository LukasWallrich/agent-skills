# Rubric: extraction tables (`all_urls`, `all_p_values`)

Rubric version 1. Needs: `modules/all_urls.json`, `modules/all_p_values.json`.

Both modules are extractors with light `info` (or `na` when empty). Neither flags
anything; they are the shared base tables for other checks. Your work here is small and
mostly serves those checks: clean the URL list and say what each link is; note which test
each p-value belongs to where a downstream rubric needs it. Do not spend effort producing
a finding for every row.

## Part 1: `all_urls`

**Module output.** One row per regex match. Columns: `text` (the matched URL-like token),
`text_id`, `paragraph_id`, `section_id`, `header`, `section_type`. `item_id` `t<text_id>`
(`.2`, `.3` for several URLs in one sentence).

**Known failure modes.** The regex is permissive and the scheme is optional, so it matches
junk tokens ("e.g", "i.e", "data.csv", "et.al", "p.12", version numbers, email domains). PDF
line breaks split URLs ("https://osf.io/ab" + "12c/") or glue the next word on
("https://osf.io/ab12c/The"). Trailing punctuation and brackets are common. URLs in
footnotes and title pages are often missing from the parse.

**Procedure.**
1. Read the table. Discard junk by inspection (no fetch needed).
2. Repair: for a truncated or glued URL, read the sentence and the next one
   (`mc_context.R --run-dir $RUN --text-id <n> --expand paragraph`). A repair must be
   visible in the text (the rest of the URL is on the next line). Do not guess identifiers,
   and do not "fix" a URL from your memory of where a project lives. Record the repaired
   form in `advice` and quote both fragments.
3. Check that links resolve (within the request budget; one call for all):
   `mc_fetch.R --run-dir $RUN --op link_check --urls <u1>,<u2>,...`
   The result lists HTTP status per URL; 401/403/429 and network errors are reported as
   "could not check", not as dead. Many publishers and DOIs block automated requests: a
   failure there is `link_not_checked`.
4. Classify what each real link **is**, from its sentence: `authors_repository` (data,
   code, materials), `preregistration`, `supplement`, `cited_tool_or_software`,
   `cited_dataset_third_party`, `reference_doi_or_publisher`, `institutional_or_ethics`,
   `other`. Put the class at the start of `agent.rationale`. This is what `repo_code.md`
   and `prereg_compare.md` need (own repository vs cited tool) and feeds the design brief.
5. Write findings only for: dead links, repaired links, and links whose class is needed
   downstream (`authors_repository`, `preregistration`, `supplement`). Junk tokens and
   reference DOIs need no finding; give their counts in your summary.

| classification | verdict | severity |
|---|---|---|
| `link_dead` - link_check returned 404/410 or DNS failure for the URL as printed (after trimming punctuation) | `confirmed_issue` | `medium` for authors' repository/preregistration/supplement; `low` otherwise |
| `link_broken_in_text` - URL is split or glued in the parsed text; repaired form resolves | `confirmed_issue` | `low`, confidence at most `medium` (may be a PDF artefact; advice: "worth checking the link is clickable and complete in the manuscript") |
| `link_ok` | `not_an_issue` | `none` |
| `not_a_url` | `not_applicable` | `none` |
| `link_not_checked` (blocked, rate-limited, budget) | `not_checked` | `none` |
| `link_unclear` - cannot tell where the URL ends | `insufficient_evidence` | `none` |

Evidence for `link_dead`: quote the URL from the paper, and cite the link-check source
(`source_id` from `sources/index.json`, quote the status line) so the retrieval date is on
record. View-only OSF links and anonymised review links are fine; do not flag them, but
note that anonymised links will need replacing on publication.

**Examples (invented illustrations).**
- `text` "e.g" -> no finding (junk; counted).
- `text` "https://osf.io/ab12c/files" with sentence "...are available at
  https://osf.io/ab12c/files." and link_check 200 -> `link_ok`, rationale starts
  "authors_repository: ...".
- `text` "https://github.com/jdoe/study-" and the next sentence begins "materials contains
  all scripts": repaired `https://github.com/jdoe/study-materials` resolves (200), printed
  form 404 -> `link_broken_in_text`.
- `text` "www.example-lab.org/data"; link_check returns 403 -> `link_not_checked`, coverage
  `partial`, limitation "server refused automated request".
- `text` "https://doi.org/10.1234/abcd.5678Additional" where it is unclear whether
  "Additional" belongs to the DOI suffix and neither form resolves -> `link_unclear`.

## Part 2: `all_p_values`

**Module output.** One row per p-value from `extract_p_values()`, the single shared
extractor behind `stat_p_exact` and `stat_p_nonsig`: `text` (match, e.g. `p < .001`),
`p_comp` (comparator; many Unicode forms are handled), `p_value` (numeric; NA for `n.s.`
etc.; scientific notation handled), `text_id`, `header`, `section_type`. It matches upper-
and lower-case `p`. It deliberately ignores prose such as "the p-value was 0.03" (too many
false positives from threshold discussions) and misses plural forms ("ps < .05") and most
table content.

**Your uses of it.**
1. **Second trigger for `marginal.md`**: rows with `p_comp` `=` and `p_value` strictly
   between .05 and .10.
2. **Sanity signal for the parse**: an empirical paper with Results sections and zero or
   very few p-values probably has its statistics in tables or in a format the regex does
   not see. Say so in the statistics group's coverage limits; every p-based module
   (`stat_p_exact`, `stat_p_nonsig`, `stat_check`, `marginal` trigger) is then weakly
   covered, and their green or `na` lights mean little.
3. **Labelling (on demand, not exhaustive).** When a downstream rubric needs to know which
   test or hypothesis a p-value belongs to (several p-values in one sentence, a p-value
   orphaned in a list), work it out from the sentence and record it in that rubric's
   finding (`rationale`, `study_id`). Do not produce a labelled copy of the whole table
   unless the user asks for it.
4. **Recall sweep** for plural and prose p-values: `recall_sweep.md`, sweep 2.

Findings with `module: "all_p_values"` are rarely needed. The one case: an extraction
error that misleads other modules (e.g. `p = .5` extracted from "p = .50 cm" or from a
variable named p). Then write `extraction_error` (`not_applicable`, severity `none`) for
that candidate, quote the sentence, and treat the corresponding rows in `stat_p_exact` /
`stat_p_nonsig` as `not_applicable` too. If you cannot tell whether the token is a p-value
(`unclear_token`), abstain with `insufficient_evidence`.
