# Rubric: non-significant results read as "no effect" (`stat_p_nonsig`)

Rubric version 1. Needs: `modules/stat_p_nonsig.json`, `design_brief.json`.

## Purpose

p > alpha means the test did not detect an effect, not that there is none. The issue to
find: the authors conclude absence ("no difference", "did not affect", "groups were the
same", "X is unrelated to Y") from a non-significant test alone, without an equivalence
test, Bayes factor, or interval-based argument. The module's own report says it "does not
yet analyze automatically whether sentences ... are correct". This rubric is that analysis.

## What the module gives you

Every extracted p-value that is *not* (`p_value <= 0.05` with comparator `<`, `=`, `<=`).
Columns: `text` (the p-value match, e.g. `p = .21`), `p_comp`, `p_value`, `significance`
(always `nonsignificant`), `expanded` (the full sentence), `text_id`, `paragraph_id`,
`section_id`, `header`, `section_type`. Light: yellow if any row, green if none.

## Known failure modes

- Alpha is fixed at .05. A study with alpha .005 has "significant" results the module does
  not list and you should treat as non-significant; a study with alpha .10 has listed rows
  that are significant. Use the design brief per study.
- Rows include `p > .05`, `p = n.s.` (`p_value` NA), `p < .10`, and threshold statements
  ("we considered p < .10 ..."). `p < .10` is not necessarily non-significant.
- Nearly every empirical paper is yellow; most rows are fine. Expect to clear the majority.
- A paper with no extractable p-values is green, which means "nothing extracted", not
  "nothing wrong". Tables are usually not covered.
- The claim often lives elsewhere: the Results sentence is neutral, the Abstract or
  Discussion says "no effect". The module never looks there.
- Several p-values in one sentence produce several rows with the same `text_id`
  (`item_id` suffixes `.2`, `.3`). Adjudicate each, but you may reuse the evidence.

## Procedure

1. Fetch the paragraph: `mc_context.R --run-dir $RUN --candidate-id <id>`.
2. Identify the test and the effect (which variables, which study). Set `study_id`.
   Check alpha for that study in the brief. If the row is significant under the study's
   stated alpha, or is a threshold/criterion statement, or reports another paper's result:
   `not_applicable`.
3. Read how the result is worded **in the sentence and paragraph**. Absence wording: "no
   effect/difference/relationship", "did not differ/affect/influence", "were the
   same/equal/comparable/similar", "unaffected", "independent of", "rule out". Neutral
   wording: "not significant(ly)", "no significant difference", "we did not find
   evidence", "failed to reject".
   "No significant difference" is neutral: it restates the test.
4. Follow the result downstream once:
   `mc_context.R --run-dir $RUN --search "<effect or variable term>" --section-type discussion`
   and the same with `abstract`. An absence claim there counts, and is more severe.
5. If absence is claimed, look for support, in the paragraph and paper-wide:
   `--search "equivalen|TOST|smallest effect size of interest|SESOI|Bayes factor|BF ?(01|10)|\bBF\b|region of practical equivalence|ROPE|non-?inferiority"`.
   Support counts only if it concerns the same effect. Record these searches in
   `coverage.searches` when the verdict rests on not finding support.
6. Special cases: manipulation/randomisation/attrition checks and assumption tests
   ("groups did not differ in age, p = .45") are the same logical error but low stakes:
   classify as `absence_claim_auxiliary`. Non-significant interaction followed by "the
   effect was the same in both groups": an absence claim about the interaction.

## Classification vocabulary (closed)

| classification | verdict | severity |
|---|---|---|
| `absence_claim_unsupported` - absence of a focal effect asserted; no equivalence/Bayes support found | `confirmed_issue` | `high` if in Abstract/Discussion/conclusion or a hypothesis decision; else `medium` |
| `absence_claim_auxiliary` - same, for a baseline, manipulation, assumption or control check | `confirmed_issue` | `low` |
| `absence_claim_supported` - absence claimed and backed by an equivalence test, Bayes factor or interval argument for this effect | `not_an_issue` | `none` |
| `neutral_wording` - reported as not significant / no evidence, nothing stronger downstream | `not_an_issue` | `none` |
| `not_a_nonsig_result` - threshold statement, significant under the study's alpha, other study's result, p-value of a test where non-significance is not interpreted (e.g. listed in passing) | `not_applicable` | `none` |
| `context_unavailable` | `insufficient_evidence` | `none` |

## When to abstain

`context_unavailable` when you cannot determine which effect the p-value belongs to
(garbled sentence, p-value orphaned from a table), or the sentence says "see Table 2" and
the claim cannot be located. Also abstain when the wording is genuinely in between
("showed little effect", "was negligible") and no interval or effect size lets you judge;
say which reading would make it an issue.

## Worked examples (invented illustrations)

**A. Confirmed.** Row `p = .34`, `expanded`: "There was no effect of feedback type on
persistence, t(118) = 0.96, p = .34." Discussion search finds: "Because feedback type does
not influence persistence, practitioners can choose either format." Support search: 0 hits
for equivalence/Bayes terms in the whole paper. -> `absence_claim_unsupported`, `high`.
Evidence: both quotes. `coverage.searches`: `[{"query": "equivalen|TOST|...|ROPE", "scope":
"whole paper", "n_hits": 0}]`. Advice: "The test of feedback type (p = .34) does not show
that there is no effect; the study may not have been sensitive enough to detect one. It is
worth rewording the Results and the practical recommendation in the Discussion, or adding
an equivalence test against the smallest effect you consider meaningful."
`suggested_rewording`: "We did not find a significant effect of feedback type on
persistence, t(118) = 0.96, p = .34."

**B. Not an issue.** "The difference between conditions was not statistically significant,
F(1, 72) = 1.20, p = .28, BF01 = 4.6, indicating moderate evidence for the null." ->
`absence_claim_supported` (quote the BF part). Do not judge whether 4.6 is "enough"; note
only that support is reported.

**C. Not applicable.** "Effects with p < .10 are reported as exploratory." ->
`not_a_nonsig_result`.

**D. Abstention.** `expanded`: "p = .12 .45 .08 Condition Age Gender" (flattened table).
-> `context_unavailable`, coverage `partial`, limitation "p-value comes from a table that
was flattened by the parser; the corresponding claim could not be identified".

## Advice phrasing

Name the effect, quote the p, say where the absence claim is, and offer both routes:
reword ("we did not find evidence for ...") or support the claim (equivalence test with a
justified smallest effect size of interest, or a Bayes factor). Do not run or sketch that
test yourself. Surface only the problematic cases in your summary; cleared rows stay in
the findings file.

## Recall sweep

Absence claims without a nearby p-value: search the Abstract and Discussion once with
`--search "\bno (effect|difference|relationship|association|impact|influence)\b|did not (differ|affect|influence)|unrelated to|independent of"`
and, for each hit about the authors' own result, trace it back to the test (then handle it
as above, as a sweep finding with `item_id` `t<text_id>` of the claim). Cap: 20 hits.
