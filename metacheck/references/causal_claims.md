# Rubric: causal claims vs design (`causal_claims`)

Rubric version 1. Needs: `modules/causal_claims.json`, `design_brief.json`.

## Purpose

Causal language ("X increases Y", "the effect of X on Y", "leads to", "reduces") is
warranted for a variable that was **randomly assigned in this study** (or identified by a
credible quasi-experimental design the authors argue for). The issue to find: a causal
claim about a variable that was measured, not manipulated, or a claim much stronger than
the design supports. Hedged or explicitly theoretical causal language can be proportionate.

## What the module gives you

Two separate things:

1. **Table**: every Abstract sentence run through a remote ML classifier (SocioCausaNet).
   Columns: `sentence`, `causal` (logical), `cause`, `effect` (extracted spans). There is
   **no `text_id`**: rows have `item_id` `r<row>`. The title is classified too but appears
   only in `report`. Adjudicate rows with `causal == true`.
2. **Randomisation sentences**: a regex for "randomly assigned / randomized / random
   allocation ..." with a long exclusion list (random effects, random order, random
   sample...). These sentences appear only in the `report` strings, not in the table.

Light: yellow if causal language and no randomisation sentence anywhere; green otherwise.

## Known failure modes

- **Green covers two different situations**: no causal language, or causal language plus
  *any* randomisation sentence *anywhere*, including random assignment in a cited trial,
  randomised stimulus order that slipped past the exclusions, or randomisation of a
  different variable than the one the claim is about.
- The classifier is a HuggingFace Space called per sentence with a 10 s timeout; a cold
  start fails the whole module. If `status != "ok"`, the module finding is `not_checked`,
  and you run the sweep below as the primary check, saying that the classifier pass was
  unavailable.
- Only title and abstract are screened. Causal claims in the Discussion and in headline
  conclusions are never seen.
- Classifier false positives: statements of aims ("we test whether X affects Y"), methods
  descriptions, descriptions of prior literature. False negatives: implicit causal verbs
  ("boosts", "shapes", "protects against"), mediation language ("through", "via").

## Procedure

1. From the design brief, list per study: `design_type` and `randomised_variables` (with
   their evidence). If these are `unknown`, try once to settle it:
   `mc_context.R --run-dir $RUN --search "random" --section-type method` and read the hits;
   remember the distinction between random **assignment** to levels of a variable,
   random **sampling**, and random **order**. Update the brief if you learn something.
2. For each `causal == true` row: locate the sentence to get a `text_id`
   (`--search "<distinctive 4-6 word phrase>" --section-type abstract`). Identify the cause
   and effect variables yourself; the `cause`/`effect` spans are hints.
3. Decide what kind of sentence it is: a finding of this paper, an aim or hypothesis,
   background, or an implication. Only findings and implications are claims.
4. Match the cause variable to the brief. Randomised in the study the claim is about? If
   the paper has several studies and only some are experimental, the claim must be tied to
   those.
5. Gauge strength: unhedged ("X causes/increases/reduces Y", "the effect of X") vs hedged
   ("may contribute", "is consistent with a causal role", "predicts" used statistically).
   "Predicts", "is associated with", "is linked to" are not causal claims. "Effect" in the
   name of a statistical term ("main effect", "indirect effect") needs reading: "the
   indirect effect of X on Y via M" in cross-sectional data is a causal claim about
   mediation.
6. Follow into the Discussion once: `--search "<cause term>" --section-type discussion`.
   Conclusions and practical recommendations ("interventions should target X") presuppose
   causality. These become sweep findings (see below) or extra evidence.
7. For experimental studies, check JARS randomisation details as a separate, low-severity
   reporting point: method of sequence generation, concealment, who implemented it
   (`--search "random" --section-type method`). One finding per study at most.

## Classification vocabulary (closed)

| classification | verdict | severity |
|---|---|---|
| `causal_claim_nonrandomised` - unhedged causal finding/implication about a variable not randomised in this study, no quasi-experimental identification argued | `confirmed_issue` | `high` in title/abstract/conclusion; `medium` elsewhere |
| `causal_claim_overreach` - design gives some leverage (longitudinal, natural experiment, randomised but claim generalises to a different variable or mediator) and wording exceeds it | `confirmed_issue` | `medium` |
| `randomisation_details_missing` - experimental study, JARS details not found in checked content | `confirmed_issue` | `low` |
| `causal_claim_supported` - claim concerns a variable randomised in this study | `not_an_issue` | `none` |
| `hedged_proportionate` - causal idea expressed with proportionate hedging or as a limitation-aware interpretation | `not_an_issue` | `none` |
| `not_a_causal_finding` - aim, hypothesis, background, associational wording, classifier false positive | `not_applicable` | `none` |
| `design_unclear` | `insufficient_evidence` | `none` |

## When to abstain

`design_unclear` when the brief has `design_type` or `randomised_variables` `unknown` or
`conflicting` for the relevant study and the Method search does not settle it, or when you
cannot tell which study an abstract claim summarises. Say what the authors could state
(e.g. "whether participants were randomly assigned to the feedback conditions").

## Worked examples (invented illustrations)

**A. Confirmed.** Abstract: "Social media use reduces adolescents' sleep quality." Brief:
s1 cross-sectional survey, `randomised_variables` `found` = [] (quote: "Participants
completed an online questionnaire"). Module light was green because the Introduction says
"in a randomized trial, Lee et al. ...". -> `causal_claim_nonrandomised`, `high`. Evidence:
abstract sentence, the design sentence. Advice: "Social media use was measured rather than
assigned, so it is worth rewording to an associational statement or explaining what
supports a causal reading." `suggested_rewording`: "Higher social media use was associated
with lower sleep quality among adolescents."

**B. Not an issue.** Abstract: "Participants who received the self-affirmation exercise
reported lower stress than controls." Brief: `randomised_variables` = ["self-affirmation
vs control"], quoted. -> `causal_claim_supported`.

**C. Not applicable.** "We examined whether perceived autonomy affects turnover
intentions." (aim) -> `not_a_causal_finding`.

**D. Abstention.** "Training improved accuracy in both studies." Study 1 is randomised;
for Study 2 the Method says only "participants completed the training or a control task".
-> `design_unclear`, coverage `complete` (the text was read; the information is absent),
advice asks the authors to state how participants were allocated in Study 2.

## Advice phrasing

Name the variable, say how it was treated in the design (quote), and offer a rewording.
Avoid "the authors wrongly claim": explicit causal reasoning with stated assumptions is
legitimate in non-experimental work; the point is that the basis should be visible.

## Recall sweep

Title: read `paper.json` info/title (or `import_summary.json`) and adjudicate it as an
extra candidate (`finding_id` `<paper_id>:causal_claims:sweep:title`, evidence location
"Title"; if the title has no `text_id`, give the search that locates it, or quote the
Abstract sentence making the same claim). Discussion: search once for
`"\b(causes?|caused|leads? to|led to|results? in|increases?|reduces?|improves?|impairs?|the (effect|impact|influence) of)\b" --section-type discussion`,
cap 25 hits, and adjudicate only sentences stating the paper's own conclusions.
