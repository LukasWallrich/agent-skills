# Rubric: exact p-values (`stat_p_exact`)

Rubric version 1. Needs: `modules/stat_p_exact.json`. No design brief needed.

## Purpose

Best practice (APA 7) is to report exact p-values (`p = .032`), with `p < .001` for very
small ones, and never `p = .000`. The module applies that policy mechanically. Your job is
narrow: remove rows that are not reports of the authors' own test results. The feedback is
uniform, so do not spend effort beyond that.

## What the module gives you

All extracted p-values with two flags. Columns: `text` (match), `p_comp`, `p_value`,
`expanded` (sentence), `imprecise`, `zero`, `text_id`, `section_type`, `header`.

- `imprecise` is true when: comparator is `<` and value > .001; or comparator is anything
  other than `=` or `<` (so `>`, `<=`, `≤`, `≈`...); or the value is not numeric (`p = n.s.`).
- `zero` is true for `p = 0`, `p = .000` etc.
- The module already drops star notes such as `* p < .05`.
- Light: red if any flagged row, green if p-values exist and none is flagged, `na` if none.

Adjudicate only rows with `imprecise` or `zero` true.

**Policy you must not "correct":** `p ≤ .001` is flagged as imprecise on purpose (the
package keeps a regression test for it). Do not clear such rows because `≤ .001` "is
basically `< .001`". Likewise `p > .05` for a non-significant result is imprecise.

## Known failure modes

Per the module docs: 78% of flags were correct; 136 of 405 imprecise values were missed.
False positives are mostly (a) alpha or threshold statements ("significance was set at p <
.05", "we used p < .005 as criterion"), (b) figure/table notes the star filter missed
("†p < .10", "a: p < .05", "bold values indicate p < .05"), (c) results quoted from other
studies, (d) generic statements in the Introduction or a tutorial-style paper. Misses are
mostly tables and "ps < .05" (see `recall_sweep.md`).

## Procedure

1. Read `expanded`. It is usually enough. Fetch the paragraph
   (`mc_context.R --candidate-id <id>`) only if the sentence is ambiguous.
2. Ask: is this p-value the reported outcome of a test the authors ran? If yes, the flag
   stands. Do not evaluate whether the imprecision "matters".
3. Many rows with identical `text` in the same paragraph can share one rationale, but each
   candidate gets its own finding.

## Classification vocabulary (closed)

| classification | verdict | severity |
|---|---|---|
| `imprecise_own_result` | `confirmed_issue` | `low` |
| `zero_p_own_result` | `confirmed_issue` | `low` |
| `threshold_statement` (alpha, decision rule, criterion) | `not_an_issue` | `none` |
| `table_or_figure_note` | `not_an_issue` | `none` |
| `others_result` | `not_an_issue` | `none` |
| `summary_with_exact_values_elsewhere` ("all ps < .05" where you verified that the exact values are reported, e.g. in a table you can read; if you cannot verify, use `imprecise_own_result` or abstain) | `not_an_issue` | `none` |
| `unclear_origin` | `insufficient_evidence` | `none` |

Severity is `low` throughout: this is a reporting-quality point. If an imprecise p hides a statcheck inconsistency, that belongs to `stat_check`.

## When to abstain

`unclear_origin` when a flattened table or caption makes it impossible to tell a note from
a result, or when "all ps < .05" points to a table the parse lost (limitation: table not
available).

## Worked examples (invented illustrations)

- **Confirmed.** "Accuracy was higher after training, t(40) = 2.31, p < .05." ->
  `imprecise_own_result`. Advice: "Consider reporting the exact value for t(40) = 2.31
  (three decimals), e.g. from your analysis output." Do not supply the exact p yourself in
  the advice, even via the calculator: the author's output is the authority, and a
  recomputed value belongs to `stat_check`.
- **Not an issue.** "Following Benjamin et al. (2018), we set the threshold for
  significance at p < .005." -> `threshold_statement`.
- **Not an issue.** "Note. Coefficients in bold are significant at p < .01." ->
  `table_or_figure_note`.
- **Abstention.** "Condition 3.21 p < .05 Age 0.44 p > .10" -> `unclear_origin`, coverage
  `partial`, limitation "flattened table; cannot tell note from result".

## Advice phrasing

One sentence per finding, naming the test. In the group summary, aggregate: "12 of 40
p-values are reported as inequalities (list in report); exact values help meta-analysts
and error checking." No rewording needed.
