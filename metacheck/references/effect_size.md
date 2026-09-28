# Rubric: effect sizes with t and F tests (`stat_effect_size`)

Rubric version 1. Needs: `modules/stat_effect_size.json`, `design_brief.json`.

## Purpose

Two questions per t or F test: (1) is an effect size reported for it, anywhere; (2) is the
reported effect size numerically coherent with the test statistic, **under the design the
study actually used**. The module answers (1) within one sentence and (2) under *any*
design assumption, because it cannot know the design. You supply the design facts; the
arithmetic stays in code (`mc_compute.R --op implied_es`).

## What the module gives you

One row per sentence containing a t or F test. Main columns:

- `test` (`t-test` / `F-test`), `test_text` (matched statistics; several joined by `;`),
  `es` (matched effect-size text, NA if none in the sentence), `text` (sentence), `text_id`.
- t-tests: `d_reported`, `d_reported_text`, `t_value`, `df`, implied values
  `d_implied_paired_dz`, `d_implied_paired_drm_r05`, `d_implied_indep_equal_n`,
  `d_implied_indep_unequal_min`, `d_implied_indep_unequal_max`, `d_implied_n`;
  `d_coherence` (`match_under_assumptions`, `no_match`, `indeterminate`),
  `d_coherence_assumption` (`paired_dz`, `independent_equal_n`,
  `independent_unequal_n_range`, `none`), `d_coherence_note`.
- F-tests: `f_reported`, `f_reported_text`, `df1`, `df2`, `eta_implied_partial`,
  `omega_implied_partial`, `eta_coherence`, `eta_coherence_assumption`
  (`partial_eta_squared`, `eta_squared`, `none`), `eta_coherence_note`.
- Sentences with several tests hold `;`-separated values in these columns, in order.

Light: red if no test has an effect size or any `no_match`; yellow if some tests lack an
effect size; green otherwise. Tolerance is ±.01.

## Known failure modes

- **"Match under assumptions" is permissive.** A d that matches the independent-groups
  formula is accepted even if the design was within-subjects (where it should match dz or
  be labelled otherwise). Most real errors pass. This is the main thing you add.
- The unequal-n branch accepts a *range*; a wide range matches almost anything.
- Same-sentence pairing: "missing" effect sizes are often in the next sentence, a table, or
  a parenthesis the regex did not parse; and an `r` or `beta` in the sentence may belong to
  a different analysis than the t-test next to it.
- "Partial" is detected as any label containing the letter p, so some labels are
  misclassified. Hedges' g is not parsed although the docs say it is; omega-squared handling
  is incomplete. Treat g and omega-squared rows as not checked by the module.
- Non-integer df (Welch), plain eta-squared in multi-factor designs, Cohen's f and dav/drm
  are `indeterminate` by design: not checkable, not wrong.
- Extraction errors in `t_value`/`df` from PDF damage.

## Procedure

Adjudicate rows where `es` is NA, or any coherence value is `no_match`, or the coherence is
`match_under_assumptions` but the assumption contradicts the design brief. Rows that match
under the assumption the brief confirms need no finding.

1. Context: `mc_context.R --run-dir $RUN --candidate-id <id>` (paragraph). Confirm the
   module's extraction against the sentence. Set `study_id`.
2. **Missing effect size (`es` NA).** Look in the paragraph, then
   `--search "<distinctive statistic, e.g. t\(58\) = 2.41|F\(2, 114\)>"` to find the same
   test elsewhere, then the design brief's note on tables. Is it a focal test or an
   auxiliary one (manipulation check, assumption test)? Unstandardised effects with CIs
   (mean difference and 95% CI, regression b with CI) count as an effect size.
3. **Coherence.** From the brief take `design_type` and N for that study/analysis. Then:
   - within: `mc_compute.R --op implied_es --stat t --value <t> --design within --df <df> --reported <d>`
   - between: `... --design between --n1 <a> --n2 <b> --reported <d>` (group sizes quoted
     from the paper; if only total N is known, pass `--df` and say equal n was assumed)
   - F: `mc_compute.R --op implied_es --stat F --value <F> --df1 <a> --df2 <b> --reported <eta2p>`
   Pass numbers exactly as quoted. The calculator reports match/no match at the tolerance.
4. If the design is `unknown` or `conflicting` in the brief, you cannot pick a formula:
   report the module's result as is and abstain on the design-specific question.
5. Explain mismatches where the text allows it: d labelled ambiguously (dz vs dav), effect
   size from a different model (covariate-adjusted) than the test, rounding of t, sign only.

## Classification vocabulary (closed)

| classification | verdict | severity |
|---|---|---|
| `es_missing_focal` - no effect size found for a hypothesis-relevant test; searches recorded | `confirmed_issue` | `medium` |
| `es_missing_auxiliary` - same for manipulation/assumption/control checks | `confirmed_issue` | `low` |
| `es_reported_elsewhere` - found in next sentence/table/other section (quote it) | `not_an_issue` | `none` |
| `es_incoherent` - calculator says no match under the design in the brief | `confirmed_issue` | `medium`; `high` if the effect is central and the discrepancy changes its conventional size label |
| `es_coherent_wrong_variant_label` - matches a variant other than the one labelled (e.g. matches dz, labelled "Cohen's d" in a within design, unlabelled) | `confirmed_issue` | `low` (advice: name the variant) |
| `es_coherent` - calculator match under the brief's design | `not_an_issue` | `none` |
| `not_checkable` - g, omega-squared, f, Welch df, eta-squared in factorial design, model-based effect | `not_applicable` | `none` |
| `extraction_error` | `not_applicable` | `none` |
| `design_unknown` | `insufficient_evidence` | `none` |

## When to abstain

`design_unknown` when the brief cannot say within vs between for this analysis, when group
sizes are needed and not reported (and equal-n vs unequal-n gives different answers), or
when the effect size might sit in a table the parse lost (then also coverage `partial`).

## Worked examples (invented illustrations)

**A. Confirmed.** Row: `t_value` 2.10, `df` 29, `d_reported` 0.78, `d_coherence`
`match_under_assumptions`, assumption `independent_equal_n`. Brief, study s2: design
`found` = within-subjects (quote: "All 30 participants completed both conditions").
Calculator (`--design within --df 29 --reported 0.78`): implied dz 0.38, no match. ->
`es_incoherent`, `medium`. Rationale cites the calculator output. Advice: "For a paired
t(29) = 2.10 the implied dz is 0.38 (metacheck calculator); the reported d = 0.78 would fit
an independent-groups formula. It is worth checking which d was computed and labelling
it (dz, dav or drm)."

**B. Not an issue.** `es` NA for "t(58) = 2.41, p = .019." Next sentence: "This
corresponds to a medium effect, d = 0.62, 95% CI [0.10, 1.14]." ->
`es_reported_elsewhere`, quote both.

**C. Not applicable.** "F(2.31, 131.7) = 4.02, p = .016, ηG2 = .04": generalised
eta-squared with corrected df. -> `not_checkable`.

**D. Abstention.** d reported for t(44); the Method says "participants were assigned to
one of two groups" but elsewhere "each participant rated both targets"; brief has design
`conflicting`. -> `design_unknown`; advice asks the authors to state the design and d
variant for this comparison.

## Advice phrasing

Quote the statistic, give the calculator's implied value with its assumption in words,
and ask which variant was computed. Never state that the effect size "is wrong": several
legitimate d variants exist and the paper may simply not name the one used.

## Recall sweep

Tests without effect sizes in tables are out of reach unless the table text is in the
parse; record as a limitation. For Hedges' g reported with a t-test in a between design,
you may check the corresponding d via the calculator only if the paper gives the
conversion inputs; otherwise `not_checkable`.
