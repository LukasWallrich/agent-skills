# Evaluation harness (pilot: `marginal`)

Companion evaluator for the agent adjudication layer. It does not change `metacheck::validate()`.
Source: plan section 6 ("Evaluation deliverable"), guardrail "Evaluate before trusting", order-of-work steps 2 and 4.

Why not `validate()`: it builds one `test_paper()` per ground-truth row from bare text and full-joins the
module table to the ground truth by `paper_id` + exact `text`. So it (a) cannot take a complete paper, its
sections, neighbouring sentences or saved external evidence; (b) has no candidate/finding ids; (c) breaks on any
whitespace difference in `text`; (d) can only say whether a module *extracted* a sentence, not whether an
*interpretation* of it is right; (e) knows nothing of abstentions, coverage, sweeps or repeated runs.
This harness keeps `validate()`'s idea (papers from text via `test_paper()`) for inline fixtures and adds the rest.

## Two label layers

One row per `(paper_id, module, text_id)`; files are `eval/labels/<module>_<split>.csv`.

| column | meaning |
|---|---|
| `paper_id`, `module`, `text_id`, `text` | the unit. `text` is the sentence verbatim; the scorer checks it against the imported paper and re-keys by text if a re-parse shifted `text_id` (reported under "problems") |
| `fixture` | `inline` (paper is built from this file's rows with `test_paper()`; `text_id` must be 1..n), `demo` (metacheck demo paper), or a paper file (absolute, or relative to `eval/fixtures/`) |
| `synthetic` | TRUE for invented sentences |
| `extraction_label` | **Layer 1.** `candidate` / `not_candidate`: should a candidate generator surface this sentence for adjudication? For `marginal`: the sentence uses near-significance language, or describes a p > alpha result as an effect - whoever's result it is and whether or not the wording is justified. Include candidates the current regex misses. |
| `contextual_label` | **Layer 2.** `confirmed_issue` / `not_an_issue` (same words as the agent verdicts) / `ambiguous` (informed labellers could reasonably disagree) / `unavailable_evidence` (cannot be decided from the fixture and its saved sources; the expected verdict is `insufficient_evidence`) |
| `ambiguous` | TRUE if the labeller is unsure or two labellers disagreed, whatever the label. Flagged or `ambiguous`-labelled rows are reported separately and excluded from precision/recall |
| `category` | free text error class (`negation`, `other_authors`, `marginal_means`, `trend_analysis`, `terminology`, `missed_phrasing`, ...); used for the per-category breakdown |
| `rationale`, `labeller` | required |

For full-paper fixtures label every sentence the generator flags, every sentence a careful reader would flag
(read the whole paper: missed candidates are the point), and a few true negatives. Sentences without a row are
treated as unlabelled, not as negatives; unlabelled candidates are counted (`n_unlabelled_candidates`) and should be 0.
Label before looking at agent output, ideally two labellers, disagreements resolved or flagged `ambiguous`.

## Split by paper

`eval/splits.json` assigns each `paper_id` to `dev` or `heldout`; both scripts refuse unassigned papers.
Rubrics, prompts, the recall sweep and candidate regexes are tuned on `dev` only. Held-out is scored rarely and
deliberately, and a paper never moves from held-out to dev after its results were seen. Do not read held-out
findings to "understand failures" and then edit the rubric; add new dev papers instead.

## Workflow / CLI

    export MAMBA_ROOT_PREFIX=~/micromamba METACHECK_DEV_PATH=~/metacheck
    R="$HOME/.local/bin/micromamba run -n r-metacheck Rscript"
    # 1. one run set per agent run (a directory with one CONTRACT run dir per paper)
    $R eval/mc_eval_build.R --labels eval/labels/marginal_dev.csv --split dev --out eval/runs/dev_run1   # [--module m] [--direct] [--force]
    # 2. the agent adjudicates each run dir and writes findings/marginal.json (fresh context per run set)
    # 3. score; >= 2 run sets add repeated-run agreement, the agreement criteria need 3
    $R eval/mc_eval_score.R --labels eval/labels/marginal_dev.csv --split dev \
       --runs eval/runs/dev_run1,eval/runs/dev_run2,eval/runs/dev_run3    # [--out eval/results] [--criteria f] [--no-validate] [--strict]

`mc_eval_build.R` uses `scripts/mc_import.R` and `scripts/mc_run.R` when they exist (inline fixtures are written
as paper JSON first, so they take the same import path) and otherwise, or with `--direct`, writes the same
run-dir shape itself using `mc_candidate_ids()`. The manifest records which (`eval.import_via`, `eval.run_via`).
`mc_eval_score.R` writes `eval_<module>_<split>.json` and `.md` to `--out` and prints the JSON. It calls
`scripts/mc_validate.R` when `findings_validated.json` is missing or older than the findings; findings that fail
validation count as unadjudicated, as in the report. `mc_eval_mock.R --verdicts <csv> --runs <set>` expands a
hand-written verdict sheet into schema-conformant findings (tests, or importing verdicts recorded elsewhere).
`eval/runs/` and `eval/results/` are outputs; do not commit held-out results next to rubric changes.

## Metrics

Unit = sentence (`paper_id`, `text_id`). A unit's verdict is its strongest finding
(`confirmed_issue` > `insufficient_evidence` > `not_checked` > `not_an_issue` > `not_applicable`; none = `none`).
A paper whose module status is not `ok` is "could not check": excluded and listed, never scored as clean.

- `extraction`: generator vs layer 1 - candidate precision and recall.
- `module_contextual`: generator alone vs layer 2 - the precision the agent must beat, and the recall ceiling without a sweep.
- `final`: `confirmed_issue` = positive, vs layer 2 over decidable rows. Sweep findings count, so final recall can exceed candidate recall.
  `recall_loss` = `module_contextual.recall` - `final.recall` (negative = the sweep gained more than adjudication lost).
  `abstention_rate` = candidates with `insufficient_evidence`, `not_checked` or no valid finding, over candidates with a decidable label.
  `coverage_rate` = candidates with a valid verdict other than `not_checked`; `coverage_complete_rate` = with `coverage.status == complete`.
- `sweep`: sweep findings, true and false. `ambiguous` and `unavailable_evidence`: verdict counts, reported separately;
  a `confirmed_issue` on an `unavailable_evidence` row is an overclaim.
- `quotes.n_invalid_quotes`: from `findings_validated.json`. Quote existence does not validate the interpretation; layer 2 does that.
- `agreement` (>= 2 run sets, over candidate and sweep units): pairwise agreement, mean pairwise Cohen's kappa, Fleiss' kappa (base R), also for the binary issue/other split, plus the list of disagreements.
- `by_category`: counts per error class.

All rates are micro-averaged over sentences; with few papers, check that one paper does not drive the result.

## Pilot acceptance criteria - PROPOSED, to be confirmed by maintainers

Written on 2026-09-19, before any agent result existed; machine-readable in `eval/criteria.json` (versioned).
Changing a threshold after held-out results exist needs a new version and a stated reason. Over repeated runs
the worst run counts. A criterion that cannot be computed is NOT_EVALUATED, which is not a pass.

| criterion | proposed threshold |
|---|---|
| final precision | >= .85 (module's published PPV: .63) |
| recall loss (candidate recall - final recall, among true issues) | <= 5 percentage points |
| abstention rate | <= 15% |
| coverage rate | >= 95% |
| invalid quotes | 0 |
| `confirmed_issue` where evidence is unavailable | 0 |
| repeated-run verdict agreement, 3 runs | pairwise >= .90 and Fleiss' kappa >= .80 |
| held-out sample | >= 20 papers and >= 30 true issues, none synthetic |

The pilot stays in **shadow mode** (original lights shown, agent findings alongside, shadow light only) until all
criteria pass on the **held-out** split with real papers; the scorer reports `eligible_for_acceptance: false` for
anything else. Broadening the generator (e.g. a p in (.05, .10) trigger) is judged on the same numbers:
extraction recall up, final precision and abstention within bounds, cost recorded.

Existing module accuracy figures (e.g. marginal: 38 TP / 22 FP / 27 missed in 51 papers) describe the original
extraction task - "did the regex find statements of this kind". They are a reference point for candidate recall and
for `module_contextual.precision`, not a baseline for interpretive judgments in general (whose result, negation,
justified wording, severity). Those need the contextual labels here.

## Status of the label data

- `labels/marginal_dev.csv`: the demo paper (4 rows; `text_id` 16 is the true issue, `text_id` 3 is the abstract
  describing the demo's contents: a candidate, not an issue) plus 21 **SYNTHETIC** sentences in four inline papers
  (`synth_01`-`04`) covering negation, other authors' results, marginal means, trend analysis, terminology,
  unlisted phrasings ("fell just short of", "a hint of"), p = .07 described as an effect, one ambiguous and one
  unavailable-evidence case. `labels/marginal_heldout.csv`: one synthetic paper, only so that the split logic is testable.
- Synthetic fixtures test the harness. They are written to be clear-cut, say nothing about real-world precision or
  recall, and results that include them never count towards acceptance. All seed labels are by `claude-seed (unreviewed)`.
- **Real labelled papers are still needed**, split into dev and held-out. The 51-paper / 87-statement sample cited
  in `metacheck/inst/modules/marginal.R` is not in the metacheck repository (searched `_stuff/`, `data-raw/`,
  `tests/`, git history of the module): there is no labelled marginal file. Nearby material, none of it labelled:
  `metacheck/_stuff/validations/text.csv` (sentences for the effect-size validation; 4 contain "marginal"),
  `metacheck/_stuff/psychsci errors.csv` (per-paper module traffic lights, errors and timings over the psychsci set; module output only), and
  `metacheck/data-raw/demo/ground_truth.json` (the demo paper's source). Ask the module author for the original
  coding sheet; if it has sentence text and paper ids it can seed layer 1 directly, and layer 2 after re-reading in context.

## Test

    ~/.local/bin/micromamba run -n r-metacheck bash tests/test_eval.sh

Builds the dev run set (via the scripts and `--direct`), fills three copies from
`tests/fixtures/eval/mock_good.csv` (twice) and `mock_worse.csv` (misses a negation, blames other authors' results,
drops a true issue, abstains, overclaims, one wrong sweep hit, one invalid quote), and checks the metrics,
agreement, PASS/FAIL output and the split guard.

## Generalising to other modules

Add `labels/<module>_<split>.csv`, papers to `splits.json`, and a `modules.<module>` block in `criteria.json`; pass
`--module`. Units are sentences, so modules whose rows have no `text_id` (reference modules: `b<bib_id>`) need the
unit key extended to `item_id`; modules needing saved external evidence need full-paper fixtures whose run dirs
are built once with `sources/` and then copied per agent run, rather than rebuilt.
