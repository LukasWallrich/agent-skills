# Rubric: marginal significance (`marginal`) - pilot check

Rubric version 1. Needs: `modules/marginal.json`, `modules/all_p_values.json`. The design
brief is used only for alpha (step 3 below); if it does not exist yet, proceed and treat
alpha as unknown.

## Purpose

Find places where the authors interpret one of **their own** results with p above their
alpha level as if it were (nearly) an effect: "marginally significant", "a trend towards
significance", "approached significance". Treating such results as support inflates the
Type 1 error rate. The issue is the *interpretation*, not the p-value and not the word
"marginal".

## What the module gives you

`marginal` runs one regex over every sentence. Six stems, each followed within a few words
by `significan*`: `margin*` (up to 5 words between), `trend*` (up to 1), `almost`,
`approach*`, `border*`, `close to` (up to 2 each). Table columns:

| column | meaning |
|---|---|
| `text` | the full matching sentence |
| `text_id`, `paragraph_id`, `section_id`, `page_number` | location |
| `header`, `section_type` | section heading and its type (`intro`, `method`, `results`, `discussion`, ...) |
| `candidate_id`, `item_id` | added by the runner (`item_id` = `t<text_id>`) |

Light: **red if there is a single row**, else green. The module's documented validation:
63% of flagged statements were genuine, and 42% of genuine statements were missed. Both
numbers are why this rubric has two parts: filter the hits, and look where the regex is
blind.

**Second trigger.** `all_p_values` lists every extracted p-value with `text` (the match,
e.g. `p = .07`), `p_comp`, `p_value`, `text_id`, `section_type`. Rows with `p_comp` of `=`
and `0.05 < p_value < 0.10` are additional candidates: the sentence around such a p-value
is where marginal wording outside the six stems lives ("fell just short of", "a hint of").
Select them mechanically, for example:

    jq '[.table[] | select(.p_comp == "=" and .p_value > 0.05 and .p_value < 0.10)]' $RUN/modules/all_p_values.json

Skip second-trigger rows whose `text_id` is already a `marginal` candidate.

## Known failure modes of the module

False positives (expect about one in three regex hits):

1. **Negation.** "did not approach significance", "was not even marginally significant",
   "far from significant". The authors are saying the opposite.
2. **Other people's results.** A literature review describing what Smith (2015) found, or a
   metascience paper about how often results are called marginal.
3. **"Marginal means" and other statistical senses of "marginal".** "Estimated marginal
   means differed significantly", "marginal effects", "marginal R2", "marginal
   distribution". The regex allows five words between `margin*` and `significan*`, so these
   match often.
4. **"Trend analysis" and other senses of "trend".** "A linear trend was significant",
   "trend analysis showed a significant increase", time trends, polynomial contrasts. Here
   "trend" is the hypothesis tested, not a euphemism for p = .07.
5. **Talking about the terminology.** "We do not interpret marginally significant results",
   "results with .05 < p < .10 are sometimes described as marginally significant", a
   preregistered decision rule, a reviewer-response style disclaimer.
6. **"Significant" in the non-statistical sense**, or "borderline" as a clinical category
   ("borderline personality ... significantly").
7. The sentence matches but the p-value is below alpha ("marginally significant, p = .048"):
   odd wording, but not an inflated-alpha interpretation. See classification
   `significant_described_as_marginal`.

False negatives (the second trigger and the sweep exist for these): phrasings without
`significan*` ("a marginal effect", "trending", "a tendency for", "a hint of", "fell just
short of", "nearly reached the conventional threshold", "p = .06, suggesting that ...");
results in tables; marginal claims in the Abstract or Discussion that are separated from
the p-value; a non-.05 alpha.

## Procedure

For each candidate (regex rows first, then second-trigger rows):

1. Get the sentence in its paragraph:
   `mc_context.R --run-dir $RUN --candidate-id <id>` or
   `--text-id <n> --expand paragraph`.
2. **Whose result is it?** Look for a citation attached to the claim, reporting verbs with
   another subject ("they found"), the section type (`intro` makes others' results likely
   but not certain), and whether a test statistic from this study is attached. If the
   sentence reports the authors' own earlier study in a multi-study paper, it is their own
   result.
3. **Which result and which p?** Identify the statistic the wording refers to, in the same
   sentence or an adjacent one. Quote it. Compare p with alpha for that study from the
   design brief; if alpha is `unknown`, use .05 and say so in `rationale`; if the study
   states another alpha (e.g. .005 or .10), use that. With a stated alpha of .10, p = .07 is
   significant and "marginally" is at most `significant_described_as_marginal`.
4. **Is it negated or merely mentioned?** Read the whole clause. Check failure modes 1, 3,
   4, 5, 6 explicitly before classifying as an issue.
5. **Is the result interpreted as an effect?** Signs: directional language ("higher",
   "reduced"), "suggesting", "supports", "in line with H2", inclusion among confirmed
   hypotheses, or reuse in the Abstract/Discussion. Follow it once:
   `mc_context.R --run-dir $RUN --search "<distinctive DV or effect term>" --section-type discussion`
   (and `abstract`). If the downstream claim is stronger than the Results sentence, add it
   as a second evidence item and raise severity.
6. For **second-trigger** rows, the question is the same, but most will be fine: a plain
   "was not significant, p = .08" is `not_an_issue` / `nonsig_reported_neutrally`. Only
   step 5 language makes it an issue.
7. Classify, then write the finding.

## Classification vocabulary (closed)

| classification | verdict | severity | use when |
|---|---|---|---|
| `own_result_interpreted_as_effect` | `confirmed_issue` | `high` if the claim reaches the Abstract, Discussion or a hypothesis decision; else `medium` | own result, p > alpha, described with marginal wording **and** treated as evidence for an effect |
| `own_result_marginal_label_only` | `confirmed_issue` | `low` | own result, p > alpha, labelled "marginally significant" or similar, but explicitly not interpreted ("we do not interpret this further") or no interpretation visible |
| `significant_described_as_marginal` | `not_an_issue` | `none` | p is at or below the study's alpha; wording is odd but alpha is not inflated. Mention the wording in `advice`. |
| `negated` | `not_an_issue` | `none` | the sentence denies marginal significance |
| `others_result` | `not_an_issue` | `none` | describes a cited study's result, or marginal significance as a research topic |
| `different_sense_marginal` | `not_an_issue` | `none` | marginal means / effects / R2 / distribution |
| `different_sense_trend` | `not_an_issue` | `none` | trend analysis, linear/quadratic trend, time trend as the tested hypothesis |
| `terminology_or_policy` | `not_an_issue` | `none` | the authors describe or disavow the practice, or state a decision rule |
| `nonstatistical_significant` | `not_an_issue` | `none` | "significant" is not about a test |
| `nonsig_reported_neutrally` | `not_an_issue` | `none` | second-trigger row: p in (.05, .10) reported as non-significant without effect language |
| `cannot_link_to_result` | `insufficient_evidence` | `none` | see below |

One edge: `different_sense_trend` applies only when the trend test itself is significant or
the word names the analysis. "There was a trend (p = .08) for older adults to ..." is
`own_result_interpreted_as_effect`.

## When to abstain

Use `insufficient_evidence` / `cannot_link_to_result` when: you cannot tell whose result it
is; the sentence refers to a table or figure that the parse lost ("the marginal effects in
Table 3") and no p-value is recoverable; the sentence is truncated or garbled; or wording
suggests an issue but the statistic it refers to cannot be identified and the wording alone
is compatible with a false-positive class. Set `coverage.status: "partial"` and name the
missing piece in `limitations`. Do not resolve doubt in either direction.

## Writing the finding

- Regex candidates: `module: "marginal"`, `finding_id: "<candidate_id>:c1"`,
  `deterministic: {"source_id": "module:marginal", "value": "<matched phrase>"}`.
- Second-trigger candidates: `module: "marginal"`, `item_id: "t<text_id>"`,
  `finding_id: "<paper_id>:marginal:sweep:t<text_id>"`, `"sweep": true`,
  `deterministic: {"source_id": "module:all_p_values", "value": "<text>, e.g. p = .07"}`.
  To keep the file small you may omit `nonsig_reported_neutrally` findings for
  second-trigger rows, but then state in your group summary how many were read and cleared.
- Evidence: the flagged sentence, plus the statistic if it is in another sentence, plus the
  downstream claim if any. For `others_result`, quote the citation-bearing part.
- `study_id` from the design brief when you can tell.

## Worked examples (invented illustrations, not from real papers)

**A. Confirmed.** Candidate sentence (results, text_id 141): "The interaction was
marginally significant, F(1, 86) = 3.41, p = .068, suggesting that the intervention was
more effective for younger participants." Search of the discussion finds (text_id 203):
"As predicted, the intervention was particularly effective among younger participants."
Brief: study s1 alpha `found` = .05.

    "agent": {"verdict": "confirmed_issue", "classification": "own_result_interpreted_as_effect",
      "rationale": "Own result with p above the stated alpha of .05 is labelled marginally significant and interpreted directionally; the Discussion restates it as a predicted effect without the qualifier.",
      "evidence": [{"source_id": "paper", "text_id": 141, "location": "Results, Study 1", "quote": "marginally significant, F(1, 86) = 3.41, p = .068, suggesting that the intervention was more effective"},
                   {"source_id": "paper", "text_id": 203, "location": "Discussion", "quote": "the intervention was particularly effective among younger participants"}],
      "confidence": "high"},
    "coverage": {"status": "complete", "checked_source_ids": ["paper"], "limitations": []},
    "severity": "high",
    "advice": "The age interaction (p = .068) did not meet the alpha of .05 stated in the analysis plan, so it is worth describing it as non-significant in the Results and removing or qualifying the Discussion claim that the intervention worked better for younger participants.",
    "suggested_rewording": "The interaction was not statistically significant, F(1, 86) = 3.41, p = .068."

**B. Not an issue (marginal means).** "Estimated marginal means were significantly higher in
the training condition (M = 4.2) than in the control condition (M = 3.6), p = .002."
Verdict `not_an_issue`, classification `different_sense_marginal`, severity `none`,
confidence `high`, evidence = the quoted phrase "Estimated marginal means were
significantly higher", advice: "" (empty; nothing for the author to do).

**C. Not an issue (negation).** "The effect of order did not approach significance (p =
.41)." -> `negated`.

**D. Abstention.** "The marginal effects reported in Table 4 were significant for two of the
three predictors." The paragraph gives no statistics; `import_summary.json` shows the
table was not extracted. This could be marginal effects from a regression
(`different_sense_marginal`) or a euphemism. Verdict `insufficient_evidence`,
classification `cannot_link_to_result`, coverage `partial`, limitations: ["Table 4 not
available in the parsed paper"], advice: "Table 4 could not be read; if 'marginal' here
refers to p-values between .05 and .10, consider reporting these as non-significant."

**E. Second trigger, confirmed.** `all_p_values` row `p = .07` in: "Participants in the
warm condition tended to donate more, t(58) = 1.85, p = .07, a first hint that temperature
shapes generosity." No regex hit (no `significan*`). ->
`own_result_interpreted_as_effect`, severity `medium`, finding id
`<paper_id>:marginal:sweep:t<text_id>`.

## Advice phrasing

Address the specific result and say what to change. Pattern: "[Result] (p = [quoted]) is
above the alpha level of [alpha, and where it is stated or 'the conventional .05; no alpha
is stated']. It is worth describing it as non-significant and [removing / qualifying] the
interpretation in [location]." If the authors want to say more than "not significant",
point to what would license it: an equivalence test or Bayes factor for absence, a
preregistered higher alpha, or a replication. Do not lecture about Type 1 errors on every
finding; the report template carries the general explanation once. Do not compute anything
(for example a "corrected" p); quote only.

## Recall sweep for this check

After the candidates, run these searches (each once; cap reading at 30 hits in total, and
record the cap if reached):

    mc_context.R --run-dir $RUN --search "margin(al|ally)\b(?! (means?|effects?|distribution|R))|trend(ed|ing)? (toward|for|to)\b|tendency (for|to|toward)|hint of|just (short|missed|failed)|nearly (reached|significant)|borderline|approach(ed|ing) (the )?(conventional|statistical)|weak(ly)? significant|suggestive (evidence|of)"

If the search tool rejects the lookahead, drop it and filter by reading. Also check the
Abstract and the first paragraph of the Discussion for any result you confirmed above.
Each new hit that survives the procedure becomes a sweep finding (same format as
second-trigger rows). If the sweep finds nothing, write no finding: an empty sweep is not
evidence of absence, so report the queries, hit counts and any cap in your group summary
instead. Table contents that the parse lost are a standing limitation: say so.
