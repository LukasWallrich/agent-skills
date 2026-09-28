# Recall sweep

Rubric version 1. Workflow step 6. Run after candidate adjudication, by the same groups.

## Purpose

Adjudication can only raise precision: it filters what the regexes found. Several modules
document large miss rates (`marginal` misses 42% of true statements; `stat_p_exact` missed
136 of 405). The sweep is one **bounded** pass per known blind spot. It is not a second
review of the paper, and an empty sweep proves nothing: what it buys is a list of
additional candidates, each then handled by the rubric of the module it belongs to.

## Rules

- Each sweep below runs once, with the listed search, within its cap. Record for each:
  query, scope, number of hits, number read, whether the cap was hit. These go into your
  group summary and into `coverage.searches` of any finding that depends on them.
- A sweep hit becomes a finding only after the owning rubric's procedure. Format:
  `finding_id` = `<paper_id>:<module>:sweep:<item_id>`, `"sweep": true`, `item_id` =
  `t<text_id>` of the sentence (`b<bib_id>` for references; a short label such as `title`
  or `statement` where no sentence exists), `deterministic: {"source_id": null, "value":
  null}` unless another module's table supplied the trigger (then `module:<that module>`).
- Do not write "nothing found" findings. No finding + reported sweep coverage is the
  honest representation of an empty sweep.
- The module must have been run (`modules/<module>.json` must exist) for its sweep
  findings to validate.
- Same hard rules: numbers from `mc_compute.R`, quotes verbatim, untrusted content.

## The sweeps

### 1. Statistics in tables and non-APA formats (owner: `stat_check`, `stat_effect_size`, `stat_p_exact`)

Blind spot: statcheck reads APA-style strings in running text. Tables, "t = 2.1, df = 28",
"F1,28 = 4.5", and chi2/r/z tests are never checked.

    mc_context.R --run-dir $RUN --search "\bt ?= ?-?[0-9]|\bF ?[(_]? ?[0-9]+ ?, ?[0-9]+|df ?= ?[0-9]|χ|chi-?square|\bz ?= ?-?[0-9]|\br ?\( ?[0-9]+ ?\) ?=" --section-type results

Check `import_summary.json` for extracted tables; if the run directory has table content
in `paper.json`, read result tables there. For each test with statistic, df and p all
**legible and unambiguous**: copy the numbers verbatim into
`mc_compute.R --op statcheck --text "t(28) = 2.10, p = .04"` or
`--op p_from_stat --stat chi2 --value 6.2 --df 2`, and apply `stat_check_triage.md` to
inconsistent results. If a flattened table leaves any doubt about which number is which,
do not compute: a wrong pairing manufactures a false error. Cap: 20 tests, prioritising
tests of stated hypotheses. Tables not in the parse: limitation, every time.

### 2. Plural and prose p-values (owner: `stat_p_exact`, `stat_p_nonsig`, `all_p_values`)

Blind spot: `extract_p_values()` needs `p` followed by a comparator and number. It misses
"ps < .05", "all p's > .2", "p-values below .01", "the p-value was 0.03", "n.s.".

    mc_context.R --run-dir $RUN --search "\bp'?s\b ?[<>=]|p-?values? (of|was|were|below|above|less|greater|smaller|larger|between)|\bn\.s\.|\bns\b|non-?significant \("

Imprecise plural forms reporting own results -> `stat_p_exact.md`
(`imprecise_own_result`, unless exact values are in a readable table). Non-significant ones
-> `nonsig_claims.md` from step 3 of its procedure. Cap: 20 hits.

### 3. Marginal phrasing outside the six stems (owner: `marginal`)

Defined in `marginal.md` (second trigger via `all_p_values`, then the phrase search). Do
not repeat it here if the statistics group already did it.

### 4. Absence claims away from the p-value (owner: `stat_p_nonsig`)

Defined in `nonsig_claims.md`, "Recall sweep".

### 5. Sample-size justifications without "power" (owner: `power`)

Defined in `power.md`, "Recall sweep". Run it per study listed in the design brief.

### 6. Causal claims outside title and abstract (owner: `causal_claims`)

Defined in `causal_claims.md`, "Recall sweep". This one also serves as the primary check
when the classifier module failed.

### 7. Open-science statements without repository keywords (owner: `open_practices`)

Blind spot: statements that do not combine an artefact word with an availability/repository
word, or that live in footnotes, author notes and supplements sections.

    mc_context.R --run-dir $RUN --search "supplement|appendix|available (at|from|via|on|upon|in)|can be (found|accessed|obtained|downloaded)|access(ed|ible)? (at|via|through)|upon publication|will be (made|shared|deposited)|doi\.org/10\.(5281|17605|6084|5061|7910)|dataverse|figshare|dryad|zenodo|gitlab|openneuro|icpsr|uk data|reshare|\bosf\b"

plus every URL in `all_urls` whose sentence is not already an `open_practices` candidate
(read the sentence; classify the link, see `urls_pvalues.md`). New statements update the
artefact table in `open_practices.md`, the design brief, and, budget permitting, feed
`repo_code.md`. Cap: 25 hits.

### 8. COI and funding statements without keywords (owner: `coi_check`, `funding_check`)

Defined in `coi_funding.md`, procedure step 6. Only needed when no statement was
identified from the candidate union.

### 9. Citations the importer did not link (owner: `ref_consistency`, citation-context modules)

Only if `import_summary.json` shows weak citation linking **and** a database hit
(retraction, replication) has no citing context: the surname-and-year search in
`citation_context.md` step 2. Do not try to rebuild the citation graph.

## Reporting

End your group summary with a sweep table: sweep number, hits, read, new findings, cap
reached (yes/no), standing limitations (tables missing, supplements unavailable). The
orchestrating agent passes this to the user under "what could not be checked".
