# Rubric: power analyses and sample-size justification (`power`)

Rubric version 1. Needs: `modules/power.json`, `design_brief.json`,
`references/power_schema.json`.

## Purpose

A power analysis is interpretable only if it reports: type (a priori / sensitivity /
post hoc), statistical test, sample size, alpha, power, effect size, effect-size metric and
software. The module finds candidate paragraphs. In the package, an optional LLM then fills
the eight fields from **that paragraph alone**, so an alpha stated once in the analysis
plan counts as missing for every power paragraph (false reds; the docs acknowledge this).
In this skill that LLM step is off and you do the extraction with whole-paper context.

## What the module gives you

Paragraphs containing `power`/`powered`/`powers`, plus at least one power-related phrase
(power analysis, effect size, sample size, G*Power, a priori, sensitivity, to detect, %,
...), plus a number that is not a year. Documented precision of this filter: 90.6%.
Columns: `text` (the **paragraph**), `text_id` (first sentence of it), `paragraph_id`,
`section_id`, `header`, `section_type`, `power_type` (regex guess: `apriori`,
`sensitivity`, `compromise`, `posthoc`, `unknown`), `complete` (NA without the LLM),
`power_id`. Light without LLM: yellow if any candidate, `na` if none.

## Known failure modes

- False positives: statistical power discussed as a limitation, power of a cited study,
  "powerful", electrical/social power with a percentage nearby.
- One paragraph may hold several analyses (one per study or hypothesis), or one analysis
  may be spread over two paragraphs.
- Misses every justification that avoids the word "power": simulation-based, Bayesian
  (BFDA), precision/accuracy-in-parameter-estimation, resource constraints, heuristics,
  "we preregistered N = 200". See recall sweep.
- `power_type` is a keyword guess: "post hoc" matches "post hoc comparisons".

## Procedure

For each candidate paragraph:

1. Read it (`mc_context.R --run-dir $RUN --candidate-id <id>`; `--expand section` if the
   analysis continues). Decide whether it reports a power analysis for this paper's
   study. If not: `not_a_power_analysis`.
2. Extract the fields of `power_schema.json` for each analysis in the paragraph. Use the
   schema's enums (`power_type`, `statistical_test`, `effect_size_metric`, `software`);
   null when the information is not given. Put the extracted object in
   `agent.extracted`; that keeps the extraction auditable. (`deterministic.value` is
   for what the module said: its `power_type` guess.)
3. For each null field, look outside the paragraph before calling it missing: the design
   brief first (alpha, sidedness, N), then
   `--search "alpha|significance level|two-(tailed|sided)|G\*?Power|pwr|simr|Superpower"`
   restricted to the method section (`--section-type method`). A field found elsewhere
   counts as reported; quote it. It is only **missing** if neither place has it; record the
   searches.
4. Effect-size justification: is there any reason given for the effect size (prior study
   or meta-analysis, smallest effect size of interest, pilot, benchmark)? "A medium effect
   (d = 0.5)" with no reason, or a pilot-study estimate used uncritically, is worth a note.
5. Recompute when test, effect size, alpha and power (or N) are all available and the test
   is one the calculator supports:
   `mc_compute.R --op power --test t_two --es 0.5 --power 0.80 --alpha 0.05 --reported-n 64`
   (tests: `t_one`, `t_paired`, `t_two`, `r`, `anova` with `--k`; `--n` is per group, pairs
   for `t_paired`, total for `r`). If `pwr` is not installed the op says so: record
   "recomputation not performed" as a limitation. Differences of a few participants are
   software rounding; flag only when the calculator's required N clearly exceeds the
   reported one. Do not convert between effect-size metrics by hand (`--op convert` covers
   d and r only).
6. Compare the planned N with `n_analysed` in the brief. Analysed N below the required N
   (after exclusions) is worth noting, as a quoted-number comparison.
7. Post hoc / observed power: flag as such; it is almost never informative. Check first
   that it is not a mislabelled sensitivity analysis (N fixed, solves for effect size).

## Classification vocabulary (closed)

One finding per analysis; use the most severe applicable class and list the others in the
rationale.

| classification | verdict | severity |
|---|---|---|
| `incomplete_power_analysis` - required fields absent in paragraph **and** checked content (list them) | `confirmed_issue` | `medium`; `low` if only `software` is missing |
| `recomputation_mismatch` - calculator's required N clearly above reported N (output cited) | `confirmed_issue` | `medium` |
| `underpowered_after_exclusions` - analysed N below the stated required N | `confirmed_issue` | `medium` |
| `effect_size_unjustified` - complete otherwise, no rationale for the effect size | `confirmed_issue` | `low` |
| `observed_power_reported` | `confirmed_issue` | `low` |
| `complete_power_analysis` - all fields present (in paragraph or elsewhere), justified | `not_an_issue` | `none` |
| `non_power_justification` (sweep) - simulation, Bayesian, precision, resource or heuristic justification; describe its type | `not_an_issue` | `none` |
| `no_sample_size_justification_found` (sweep) - none in checked content for a study | `confirmed_issue` | `low` |
| `not_a_power_analysis` | `not_applicable` | `none` |
| `cannot_extract` | `insufficient_evidence` | `none` |

## When to abstain

`cannot_extract` when the paragraph refers to a supplement or preregistration for the
details ("see the preregistration for the full power analysis") and you have not read that
source (if `prereg_check` retrieved it, read it first); or when multiple studies share one
paragraph and fields cannot be assigned.

## Worked examples (invented illustrations)

**A. Not an issue (the module's classic false red).** Paragraph: "An a priori power
analysis in G*Power for an independent-samples t-test indicated that 64 participants per
group are needed to detect d = 0.50 with 80% power." No alpha. Analysis-plan sentence
elsewhere (quoted): "We used an alpha level of .05 for all confirmatory tests." Effect size
justified in the previous sentence by a cited meta-analysis. Calculator: required n per
group 64. -> `complete_power_analysis`; advice: "Consider repeating the alpha level in the
power-analysis sentence so it stands alone."

**B. Confirmed.** "Our sample of 120 gave us sufficient power (> .80) to detect effects."
No test, effect size, metric, alpha or software in paragraph; method-section searches: 0
relevant hits. -> `incomplete_power_analysis`, `medium`, `coverage.searches` recorded.
Advice lists the missing elements and offers the template sentence from metacheck's
guidance (type, test, software, effect size and metric, alpha, power, resulting N).

**C. Not applicable.** "The limited statistical power of earlier studies (median N = 24)
may explain the mixed findings." -> `not_a_power_analysis`.

**D. Abstention.** "Sample size was determined by the power analysis in our
preregistration (https://osf.io/xxxxx)." Registration fetch failed (status in
`sources/index.json`). -> `cannot_extract`, coverage `partial`, limitation names the
inaccessible source.

## Advice phrasing

List exactly which elements are missing and where the others were found. For sensitivity
analyses ask for N, alpha, power and the resulting detectable effect. Keep "worth adding";
authors often have the information and left it out.

## Recall sweep

If the module table is empty **or** some study in the brief has no power candidate, search
the method sections once:
`--search "sample size|number of participants|stopping rule|recruit|simulat|Bayes factor design|precision|accuracy in parameter|resource|as many as possible|rule of thumb|saturation" --section-type method`.
Classify any sample-size justification found as `non_power_justification`. If a study has
no justification in checked content, write a sweep finding
`no_sample_size_justification_found` with `coverage.searches`, phrased as "not found in
checked content" (supplements and preregistrations you did not read are limitations).
