#!/usr/bin/env bash
# End-to-end test of the evaluation harness on the demo paper + SYNTHETIC fixtures.
# Builds a run set, fills three copies with mock findings (good, good, worse) and scores them.
#   MAMBA_ROOT_PREFIX=~/micromamba METACHECK_DEV_PATH=~/metacheck ~/.local/bin/micromamba run -n r-metacheck bash tests/test_eval.sh
set -euo pipefail
cd "$(dirname "$0")/.."
T=${EVAL_TMP:-$(mktemp -d)}; [ -n "${EVAL_TMP:-}" ] || trap 'rm -rf "$T"' EXIT
L=eval/labels/marginal_dev.csv
fail() { echo "FAIL: $*" >&2; exit 1; }
# q <json> <R expression on x>: must evaluate to TRUE
q() { Rscript -e 'x <- jsonlite::read_json(commandArgs(TRUE)[1]); ok <- eval(parse(text = commandArgs(TRUE)[2])); if (!isTRUE(ok)) quit(status = 1)' "$1" "$2" || fail "$2"; echo "ok: $2"; }

echo "== build (mc_import.R / mc_run.R if present)"
Rscript eval/mc_eval_build.R --labels $L --split dev --out "$T/good1" > "$T/build.json"
q "$T/build.json" 'x$n_papers == 5 && all(sapply(x$papers, `[[`, "module_status") == "ok")'
q "$T/build.json" '"to_err_is_human:marginal:t16" %in% unlist(x$papers[[1]]$candidate_ids)'
echo "== build --direct gives the same candidates"
Rscript eval/mc_eval_build.R --labels $L --split dev --out "$T/direct" --direct > "$T/build_direct.json"
Rscript -e 'a <- lapply(commandArgs(TRUE), jsonlite::read_json); f <- function(x) sort(unlist(lapply(x$papers, `[[`, "candidate_ids"))); stopifnot(identical(f(a[[1]]), f(a[[2]])))' "$T/build.json" "$T/build_direct.json" || fail "direct candidates differ"
cp -r "$T/good1" "$T/good2"; cp -r "$T/good1" "$T/worse"

echo "== mock findings"
Rscript eval/mc_eval_mock.R --verdicts tests/fixtures/eval/mock_good.csv --runs "$T/good1" > /dev/null
Rscript eval/mc_eval_mock.R --verdicts tests/fixtures/eval/mock_good.csv --runs "$T/good2" > /dev/null
Rscript eval/mc_eval_mock.R --verdicts tests/fixtures/eval/mock_worse.csv --runs "$T/worse" > /dev/null

echo "== score: good run"
Rscript eval/mc_eval_score.R --labels $L --split dev --runs "$T/good1" --out "$T/res_good" > "$T/good.json" 2> /dev/null
q "$T/good.json" 'r <- x$runs[[1]]; r$data$n_confirmed_issue == 8 && r$data$n_ambiguous == 1 && r$data$n_unavailable_evidence == 1'
q "$T/good.json" 'r <- x$runs[[1]]$extraction; r$fn == 3 && r$fp == 2'                 # 3 missed phrasings; marginal means + trend
q "$T/good.json" 'r <- x$runs[[1]]$module_contextual; r$tp == 5 && r$recall == 5/8'
q "$T/good.json" 'r <- x$runs[[1]]$final; r$precision == 1 && r$recall == 1 && r$recall_loss < 0 && r$abstention_rate == 0 && r$coverage_rate == 1'
q "$T/good.json" 'x$runs[[1]]$sweep$n_true_issue == 3 && x$runs[[1]]$quotes$n_invalid_quotes == 0 && x$runs[[1]]$unavailable_evidence$n_abstained == 1'
q "$T/good.json" 'x$overall != "PASS" && !x$eligible_for_acceptance'                     # too few papers, one run, synthetic, dev
[ -s "$T/res_good/eval_marginal_dev.md" ] || fail "no markdown summary"

echo "== score: three runs (good, good, worse) -> worst-case criteria + agreement"
Rscript eval/mc_eval_score.R --labels $L --split dev --runs "$T/good1,$T/good2,$T/worse" --out "$T/res_all" > "$T/all.json" 2> /dev/null
q "$T/all.json" 'w <- x$runs[[3]]; w$final$precision < 0.85 && w$final$recall < 1 && w$final$abstention_rate > 0 && w$quotes$n_invalid_quotes == 1'
q "$T/all.json" 'x$runs[[3]]$unavailable_evidence$n_confirmed_issue == 1 && x$runs[[3]]$sweep$n_false == 1'
q "$T/all.json" 'a <- x$agreement; a$n_runs == 3 && a$pairwise_agreement < 1 && a$fleiss_kappa < 0.8 && length(a$disagreements) > 0'
q "$T/all.json" 'r <- setNames(sapply(x$criteria, `[[`, "result"), sapply(x$criteria, `[[`, "id")); all(r[c("final_precision", "invalid_quotes", "run_kappa", "overclaims_on_unavailable")] == "FAIL")'
q "$T/all.json" 'x$overall == "FAIL"'

echo "== two identical runs agree perfectly; agreement criteria need 3 runs"
Rscript eval/mc_eval_score.R --labels $L --split dev --runs "$T/good1,$T/good2" --out "$T/res_2" > "$T/two.json" 2> /dev/null
q "$T/two.json" 'x$agreement$pairwise_agreement == 1 && x$agreement$fleiss_kappa == 1'
q "$T/two.json" 'r <- setNames(sapply(x$criteria, `[[`, "result"), sapply(x$criteria, `[[`, "id")); r[["run_kappa"]] == "NOT_EVALUATED"'

echo "== held-out split is kept apart"
Rscript eval/mc_eval_build.R --labels eval/labels/marginal_heldout.csv --split heldout --out "$T/held" > "$T/held.json"
q "$T/held.json" 'x$n_papers == 1 && x$papers[[1]]$paper_id == "synth_05"'
if Rscript eval/mc_eval_score.R --labels $L --split heldout --runs "$T/good1" --out "$T/res_x" > "$T/err.json" 2> /dev/null; then fail "dev labels scored as heldout"; fi
q "$T/err.json" 'x$status == "error"'
echo "ALL EVAL TESTS PASSED"
