#!/usr/bin/env bash
# Smoke test for mc_import.R, mc_run.R and mc_context.R on the demo paper.
#   tests/test_core.sh            core modules (works offline; ref_pubpeer may then be "failed")
#   MC_TEST_DEFAULT=1 tests/test_core.sh   also runs --modules default (slow, needs network)
# Rscript is taken from $MC_RSCRIPT, else micromamba env r-metacheck if present, else PATH.
set -u
cd "$(dirname "$0")/.."

if [ -z "${MC_RSCRIPT:-}" ]; then
  if [ -x "$HOME/.local/bin/micromamba" ]; then
    export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
    [ -d "$HOME/metacheck" ] && export METACHECK_DEV_PATH="${METACHECK_DEV_PATH:-$HOME/metacheck}"
    MC_RSCRIPT="$HOME/.local/bin/micromamba run -n r-metacheck Rscript"
  else
    MC_RSCRIPT="Rscript"
  fi
fi

RUN=$(mktemp -d)
OUT="$RUN/_out.json"
trap 'rm -rf "$RUN"' EXIT
pass=0; fail=0

ok() { pass=$((pass + 1)); echo "ok   - $1"; }
no() { fail=$((fail + 1)); echo "FAIL - $1"; [ -f "$OUT" ] && head -c 600 "$OUT" && echo; }
# mc <script> args... : run a script, stdout to $OUT
mc() { local s=$1; shift; $MC_RSCRIPT "scripts/$s.R" "$@" > "$OUT" 2> "$RUN/_err.txt"; }
# check <label> <jq-ish R expression on `x` (parsed $OUT) returning TRUE>
check() {
  if $MC_RSCRIPT -e 'a <- commandArgs(TRUE); x <- jsonlite::read_json(a[1]); run <- a[2]; rj <- function(f) jsonlite::read_json(file.path(run, f)); if (!isTRUE(eval(parse(text = a[3])))) quit(status = 1)' "$OUT" "$RUN" "$2" > /dev/null 2>&1
  then ok "$1"; else no "$1"; fi
}

# ---- import ----
mc mc_import --demo --run-dir "$RUN" && ok "import exits 0" || no "import exits 0"
check "import summary" 'x$status == "ok" && x$paper_id == "to_err_is_human" && x$counts$sentences == 37 && x$counts$refs == 5 && length(x$sections) == 14'
check "import parse warnings are an array" 'is.list(x$parse_warnings)'
check "import files" 'all(file.exists(file.path(run, c("paper.rds", "paper.json", "import_summary.json", "manifest.json"))))'
check "manifest" 'm <- rj("manifest.json"); nchar(m$source$sha256) == 64 && m$paper_id == "to_err_is_human" && !is.null(m$metacheck$version) && m$schema_versions$finding == "1" && !is.null(m$databases$retractionwatch)'
mc mc_import --file /nonexistent.pdf --run-dir "$RUN" && no "import of missing file exits non-zero" || ok "import of missing file exits non-zero"
check "import error JSON" 'x$status == "error"'

# ---- run ----
mc mc_run --run-dir "$RUN" --modules marginal,stat_p_exact,all_p_values,ref_summary,ref_retraction && ok "run exits 0" || no "run exits 0"
check "upstreams auto-added and ordered before ref_summary" 'o <- unlist(x$order); setequal(unlist(x$auto_added), c("ref_accuracy", "ref_pubpeer", "ref_replication")) && match("ref_retraction", o) < match("ref_summary", o) && match("ref_pubpeer", o) < match("ref_summary", o)'
check "offline modules ok" 's <- setNames(sapply(x$modules, `[[`, "status"), sapply(x$modules, `[[`, "module")); all(s[c("marginal", "stat_p_exact", "all_p_values", "ref_retraction", "ref_replication", "ref_summary")] == "ok")'
check "ref_pubpeer is ok or failed with an error, never silently empty" 'p <- rj("modules/ref_pubpeer.json"); (p$status == "ok" && is.null(p$error)) || (p$status == "failed" && nzchar(p$error) && is.null(p$traffic_light))'
check "module json shape" 'm <- rj("modules/stat_p_exact.json"); all(c("module", "title", "section", "args", "status", "error", "upstream", "elapsed_s", "traffic_light", "summary_text", "report", "summary_table", "table", "warnings") %in% names(m)) && m$traffic_light == "red"'
check "candidate ids" 'm <- rj("modules/stat_p_exact.json"); identical(names(m$table[[1]])[1:2], c("candidate_id", "item_id")) && m$table[[2]]$candidate_id == "to_err_is_human:stat_p_exact:t16"'
check "ref modules use bib ids" 'rj("modules/ref_retraction.json")$table[[1]]$candidate_id == "to_err_is_human:ref_retraction:b2"'
check "chain works: ref_summary sees upstream tables" 'r <- rj("modules/ref_summary.json")$table; any(sapply(r, \(z) !is.null(z$retractionwatch))) && any(sapply(r, \(z) !is.null(z$replication_type)))'
check "rds is stripped" 'o <- readRDS(file.path(run, "modules/ref_summary.rds")); is.null(o$paper) && is.null(o$prev_outputs) && is.data.frame(o$table)'
check "run_status.json written" 'length(rj("run_status.json")$modules) == 8'

mc mc_run --run-dir "$RUN" --modules ref_summary,marginal,nonexistent_module --args-json '{"marginal":{"bogus":1}}' && ok "run with failures still exits 0" || no "run with failures still exits 0"
check "ok results are reused (chain rebuilt from rds)" 's <- Filter(\(m) m$module == "ref_retraction", x$modules)[[1]]; isTRUE(s$reused) && s$status == "ok"'
check "module error becomes failed, run continues" 's <- setNames(sapply(x$modules, `[[`, "status"), sapply(x$modules, `[[`, "module")); s[["marginal"]] == "failed" && s[["nonexistent_module"]] == "failed" && s[["ref_summary"]] == "ok"'
check "failed module json has error, no light, no rds" 'm <- rj("modules/marginal.json"); grepl("bogus", m$error) && is.null(m$traffic_light) && !file.exists(file.path(run, "modules/marginal.rds"))'
check "earlier modules kept in run_status" 'any(sapply(x$modules, \(m) m$module == "stat_p_exact" && isTRUE(m$earlier_run)))'
mc mc_run --run-dir "$RUN" --modules marginal --force > /dev/null; check "force re-run repairs marginal" 'rj("modules/marginal.json")$status == "ok"'

# ---- context ----
mc mc_context --run-dir "$RUN" --text-id 16,17
check "text-id" 'r <- x$results[[1]]; length(x$results) == 2 && r$text_id == 16 && r$header == "Procedure" && r$section_type == "method" && grepl("marginally", r$text) && all(sapply(r$context, \(s) !is.null(s$text_id)))'
mc mc_context --run-dir "$RUN" --text-id 16 --expand sentence --plus 1 --minus 1
check "text-id plus/minus" 'identical(sapply(x$results[[1]]$context, `[[`, "text_id"), 15:17)'
mc mc_context --run-dir "$RUN" --bib-id 2
check "bib-id with citing context" 'r <- x$results[[1]]; r$n_citing == 1 && r$citing[[1]]$text_id == 4 && grepl("Gino", r$reference_text) && length(r$citing[[1]]$context) >= 1'
mc mc_context --run-dir "$RUN" --bib-id 4
check "uncited reference carries a note" 'x$results[[1]]$n_citing == 0 && nzchar(x$results[[1]]$note)'
mc mc_context --run-dir "$RUN" --candidate-id to_err_is_human:ref_retraction:b2
check "candidate-id (ref)" 'x$module == "ref_retraction" && x$bib$bib_id == 2 && x$bib$citing[[1]]$text_id == 4'
mc mc_context --run-dir "$RUN" --candidate-id to_err_is_human:stat_p_exact:t16
check "candidate-id (text)" 'x$row$item_id == "t16" && x$text$text_id == 16'
mc mc_context --run-dir "$RUN" --search "p [<=>]" --section-type method
check "search" 'x$n == 3 && identical(sapply(x$results, `[[`, "text_id"), 15:17)'
mc mc_context --run-dir "$RUN" --sections
check "sections" 'length(x$results) == 14 && x$results[[3]]$header == "Method" && x$results[[3]]$text_id_min == 8'
mc mc_context --run-dir "$RUN" --section-id 3
check "section-id" 'r <- x$results[[1]]; r$header == "Procedure" && r$n_sentences == length(r$sentences) && !is.null(r$sentences[[1]]$text_id)'
mc mc_context --run-dir "$RUN" --candidate-id to_err_is_human:marginal:t999 && no "unknown candidate exits non-zero" || ok "unknown candidate exits non-zero"
check "context error JSON" 'x$status == "error"'

# ---- optional: full default set ----
if [ -n "${MC_TEST_DEFAULT:-}" ]; then
  mc mc_run --run-dir "$RUN" --modules default --force && ok "default run exits 0" || no "default run exits 0"
  check "default: 16 modules, each ok or explicitly failed/skipped" 'length(x$modules) >= 16 && all(sapply(x$modules, \(m) m$status == "ok" || nzchar(m$error)))'
  $MC_RSCRIPT -e 'x <- jsonlite::read_json(commandArgs(TRUE)[1]); for (m in x$modules) cat(sprintf("       %-18s %-24s %s\n", m$module, m$status, if (is.null(m$error)) "" else m$error))' "$OUT"
fi

echo "---- $pass passed, $fail failed"
[ "$fail" -eq 0 ]
