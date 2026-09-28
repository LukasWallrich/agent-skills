# Rubric: statcheck triage (`stat_check`)

Rubric version 1. Needs: `modules/stat_check.json`, `design_brief.json`.

## Purpose

statcheck recomputes p from a reported test statistic and degrees of freedom and flags
inconsistencies. Its recomputation is reliable; its assumptions (two-tailed, no correction,
plain t/F) are not always true of the paper. Per the module's documentation, 43% of its
flags were false positives. You decide, for each flagged row, whether a documented feature
of the analysis explains the discrepancy, and you state the consequence: does the
significance decision change?

You never recompute a p-value yourself. All numbers come from the module table or from
`mc_compute.R`.

## What the module gives you

One row per APA-formatted t or F test (chi2, r, z are extracted by statcheck but discarded
by the module). Columns: `test_type` (`t`/`F`), `df1`, `df2`, `test_comp`, `test_value`,
`p_comp`, `reported_p`, `computed_p`, `raw` (the matched string), `error` (reported and
recomputed p inconsistent), `decision_error` (inconsistent **and** on opposite sides of
.05), `one_tailed_in_txt` (statcheck saw "one-tailed/one-sided/directional" somewhere),
`apa_factor`, `text` (sentence), `text_id`, `section_type`, `header`. Light: red if any
`error`; `decision_error` is counted in `summary_table` but does not change the light.
Only rows with `error == true` need adjudication; the others need no finding.

## Known failure modes

- One-tailed tests: recomputed two-tailed p is double the reported one.
- Multiplicity corrections (Bonferroni, Holm, FDR): the reported p is adjusted, the
  recomputed one is not.
- Welch, Greenhouse-Geisser or Huynh-Feldt corrections where df were rounded or the
  uncorrected df are reported with the corrected p (or the reverse).
- Extraction errors: a minus sign or decimal lost in the PDF, "F(1, 23) = 4.5" read from
  "F(1, 123)", statistic and p from two different tests in one sentence.
- `p = .000` and `p < .03`-style reporting are flagged as errors by design; those are
  handled by `stat_p_exact.md` (classify here as `reporting_format`).
- Sentence-level: a statement "all tests are one-sided" in the Method is invisible to it.
- Tests in tables and non-APA formats are never seen (see recall sweep).

## Procedure

For each row with `error == true`:

1. `mc_context.R --run-dir $RUN --candidate-id <id>` (paragraph). Confirm that `raw`
   matches what the sentence says: same statistic, df and p, belonging to one test. If the
   extraction is wrong, stop: `extraction_error`.
2. Look up the study in the design brief: `sidedness`, `multiplicity_corrections`, alpha.
   If the brief says `unknown`, search once:
   `--search "one-(tailed|sided)|directional|Bonferroni|Holm|FDR|false discovery|corrected for multiple|Welch|Greenhouse|Huynh|sphericity"`.
3. Test each candidate explanation with the calculator, never by eye:
   - one-tailed: `mc_compute.R --op p_from_stat --stat t --value <t> --df <df> --tails 1`
     (or `--op statcheck --text "<raw>" --one-tailed`). Explained if the result is
     consistent with the reported p at the reported precision **and** the paper states
     one-sided testing for this analysis.
   - correction: a Bonferroni-adjusted p equals the raw p times the number of tests. Do not
     multiply yourself; if the paper states the number of comparisons and the adjustment,
     classify on the documented correction plus the direction of the discrepancy (reported
     p larger than computed), and say the match was not numerically verified.
   - Welch/GG: non-integer df or an explicit statement; reported p smaller/larger in the
     expected direction. Same caveat.
   - typo: try the obvious single-character alternatives through
     `mc_compute.R --op p_from_stat` (e.g. df 23 vs 123 if N in the brief makes 123
     plausible). A typo is still an issue for the author to fix.
4. Consequence. Use `decision_error` from the table, relative to the **study's alpha**: if
   alpha is not .05, compare the quoted reported p and the tool-computed p with that alpha
   (a comparison, not arithmetic) and say so.
5. An explanation needs evidence in the paper (quote it). "It might be one-tailed" with no
   statement anywhere is not an explanation: it is `unexplained_inconsistency` with the
   note that a one-tailed test would reconcile it, if the calculator confirms that.

## Classification vocabulary (closed)

| classification | verdict | severity |
|---|---|---|
| `unexplained_inconsistency` | `confirmed_issue` | `high` if decision flips, else `medium` |
| `likely_typo` (a single-character change reconciles it; calculator output cited) | `confirmed_issue` | `high` if decision flips as reported, else `low` |
| `explained_one_tailed` (stated in paper, calculator-consistent) | `not_an_issue` | `none` |
| `explained_correction` (documented multiplicity or df correction, direction consistent) | `not_an_issue` | `none` (advice: report that the p is adjusted, in the sentence) |
| `reporting_format` (`p = .000`, `p < .03` style) | `not_an_issue` here | `none` (defer to `stat_p_exact`) |
| `extraction_error` (module misread the text) | `not_applicable` | `none` |
| `cannot_determine` | `insufficient_evidence` | `none` |

## When to abstain

`cannot_determine` when the sentence is damaged, when two tests share the sentence and you
cannot tell which p belongs to which statistic, or when a correction is plausible from
context (many post-hoc comparisons) but not documented. Say what the author could add to
settle it.

## Worked examples (invented illustrations)

**A. Confirmed, decision flips.** `raw`: "t(28) = 1.70, p = .04", `computed_p` 0.1002,
`decision_error` true. Brief: sidedness `found` two-sided ("All tests were two-tailed").
Calculator with `--tails 1` returns 0.0501: not consistent with .04 either. ->
`unexplained_inconsistency`, `high`. Advice: "For t(28) = 1.70 the recomputed two-tailed p
is about .10 (metacheck/statcheck), not .04, which would change the conclusion at alpha
.05. It is worth checking the statistic, the df and the p against the analysis output."
(The ".10" is the module's `computed_p`, cited with `deterministic.source_id:
"module:stat_check"`.)

**B. Not an issue.** `raw`: "t(45) = 1.80, p = .039", `computed_p` 0.0786. Method sentence
(quoted): "Given the directional hypothesis, we used one-sided tests." Calculator
`--tails 1` returns 0.0393. -> `explained_one_tailed`. Advice: "Consider writing 'one-sided'
next to this test so that readers and automated checks do not read the p as two-sided."

**C. Not applicable.** `raw` "F(1, 23) = 9.10, p < .001" but the sentence reads
"F(1, 123) = 9.10" -> `extraction_error`; note the misread in `rationale`.

**D. Abstention.** Six pairwise comparisons in a paragraph, all reported p larger than
computed, no mention of any correction in the paper (search: 0 hits). ->
`cannot_determine`, confidence `low`, advice: "The reported p-values are all larger than
the recomputed ones, which would fit an unreported multiplicity correction; it is worth
stating which correction, if any, was applied."

## Advice phrasing

Give the recomputed value with its source ("recomputed by statcheck"), state whether the
decision changes at the study's alpha, and what to check. Never call it an "error" in the
author's analysis: statcheck detects inconsistency in the report, and the cause may be a
typo in any of three numbers.

## Recall sweep

See `recall_sweep.md` (statistics in tables and non-APA formats): extract the numbers
verbatim and run `mc_compute.R --op statcheck --text "<APA-formatted string>"` or
`--op p_from_stat` (which also handles `r`, `chi2`, `z`). Those findings use
`module: "stat_check"` with sweep ids.
