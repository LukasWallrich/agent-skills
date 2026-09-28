#!/usr/bin/env bash
# Smoke tests for mc_compute.R and mc_fetch.R. Network tests are skipped when offline.
#   export MAMBA_ROOT_PREFIX=~/micromamba METACHECK_DEV_PATH=~/metacheck
#   bash tests/test_compute_fetch.sh
set -u
cd "$(dirname "$0")/.."
if [ -n "${MC_RSCRIPT:-}" ]; then RS="$MC_RSCRIPT"
elif [ -x "$HOME/.local/bin/micromamba" ]; then RS="$HOME/.local/bin/micromamba run -n r-metacheck Rscript"
else RS="Rscript"; fi
pass=0; fail=0
RUN=$(mktemp -d)
trap 'rm -rf "$RUN"' EXIT

# check <label> <R expression on parsed JSON `j`> ; reads JSON from $OUT
check() {
  if printf '%s' "$OUT" | $RS -e 'j <- jsonlite::fromJSON(file("stdin"), simplifyVector = FALSE); ok <- isTRUE('"$2"'); quit(status = if (ok) 0 else 1)' >/dev/null 2>&1
  then pass=$((pass+1)); echo "ok   - $1"
  else fail=$((fail+1)); echo "FAIL - $1"; printf '%s\n' "$OUT" | head -30; fi
}
compute() { OUT=$($RS scripts/mc_compute.R "$@" 2>/dev/null); }
fetch() { OUT=$($RS scripts/mc_fetch.R --run-dir "$RUN" "$@" 2>/dev/null); }

# ---- compute ----
compute --op list;                                             check "list ops" 'all(c("p_from_stat","statcheck","implied_es","power") %in% names(j$ops))'
compute --op p_from_stat --stat t --value 2.20 --df 28;        check "t(28)=2.20 two-tailed p=.036" 'abs(j$result$p - 0.03623) < 1e-4'
compute --op p_from_stat --stat t --value -2.20 --df 28 --tails 1; check "one-tailed p is half" 'abs(j$result$p - 0.01811) < 1e-4'
compute --op p_from_stat --stat F --value 4.5 --df1 1 --df2 38; check "F(1,38)=4.5 p" 'abs(j$result$p - pf(4.5, 1, 38, lower.tail = FALSE)) < 1e-12'
compute --op p_from_stat --stat r --value 0.3 --n 50;          check "r=.3 n=50 p=.034" 'abs(j$result$p - 0.0343) < 1e-3'
compute --op p_from_stat --stat chi2 --value 3.84 --df 1;      check "chi2(1)=3.84 p=.05" 'abs(j$result$p - 0.05) < 1e-3'
compute --op p_from_stat --stat z --value 1.96;                check "z=1.96 p=.05" 'abs(j$result$p - 0.05) < 1e-3'
compute --op statcheck --text "t(28) = 2.20, p = .03";         check "statcheck flags .03 vs .036" 'j$result$n == 1 && isTRUE(j$result$rows[[1]]$error) && !isTRUE(j$result$rows[[1]]$decision_error)'
compute --op statcheck --text "no stats here";                 check "statcheck no extraction is not a pass" 'j$result$n == 0 && grepl("not a pass", j$result$note)'
compute --op implied_es --stat F --value 4.5 --df1 1 --df2 38 --reported .11; check "eta2p = 4.5/(4.5+38), matches .11" 'abs(j$result$eta2_partial - 4.5/42.5) < 1e-12 && isTRUE(j$result$comparison$any_match)'
compute --op implied_es --stat F --value 4.5 --df1 1 --df2 38 --reported .15; check "eta2p .15 does not match" '!isTRUE(j$result$comparison$any_match)'
compute --op implied_es --stat t --value 2.2 --design within --df 28; check "dz = t/sqrt(n)" 'abs(j$result$dz - 2.2/sqrt(29)) < 1e-12'
compute --op implied_es --stat t --value 2.2 --design between --n1 10 --n2 20 --reported 0.85; check "ds unequal n" 'abs(j$result$ds - 2.2*sqrt(1/10+1/20)) < 1e-12 && isTRUE(j$result$comparison$any_match)'
compute --op implied_es --stat t --value 2.2 --design between --df 28; check "ds equal n" 'abs(j$result$ds_equal_n - 4.4/sqrt(30)) < 1e-12'
compute --op implied_es --stat t --value 2.2 --df 28;          check "design must be stated" 'j$status == "error"'
compute --op power --test t_two --es 0.5 --power 0.8 --reported-n 50; check "two-sample d=.5 -> 64/group; 50 too few" 'j$result$n_ceiling == 64 && !j$result$reported_n$meets_required && nzchar(j$engine)'
compute --op power --test t_paired --es 0.5 --n 34;            check "paired dz=.5 n=34 power ~.80" 'abs(j$result$power - 0.80) < 0.01'
compute --op power --test anova --es 0.25 --k 3 --power 0.8;   check "anova f=.25 k=3 -> 53/group" 'j$result$n_ceiling == 53'
compute --op power --test r --es 0.3 --power 0.8;              check "r=.3 -> n ~ 84-85, engine stated" 'j$result$n_ceiling %in% 84:85 && nzchar(j$engine)'
compute --json '{"op":"convert","from":"d","value":0.5}';      check "--json input, d->r" 'abs(j$result$r - 0.5/sqrt(4.25)) < 1e-12'
compute --op nope;                                             check "unknown op -> error JSON" 'j$status == "error"'

# ---- fetch ----
fetch --op list;                                               check "fetch list shows budget" 'j$budget$max_requests == 60 && j$budget$requests_used == 0'
fetch --op file --url "file:///etc/passwd";                    check "non-http scheme refused + recorded as failed" 'j$status == "failed" && grepl("could not check", j$interpretation)'
fetch --op file --url "https://nonexistent.invalid/data.csv";  check "bogus URL recorded as failed" 'j$status == "failed" && nzchar(j$error) && j$source_id == "file_nonexistent_invalid_data_csv"'
fetch --op file --url "https://nonexistent.invalid/data.csv";  check "retry after failure gets a .2 id" 'j$source_id == "file_nonexistent_invalid_data_csv.2"'
OUT=$(cat "$RUN/sources/index.json");                          check "index keeps both failures + budget" 'length(j) == 4 && j[[1]]$source_id == "_budget" && j[[1]]$requests_used == 3 && all(sapply(j[-1], \(e) e$status) == "failed")'

if curl -sfI --max-time 10 https://osf.io >/dev/null 2>&1; then
  fetch --op link_check --urls "https://osf.io,https://nonexistent.invalid/x"; check "link_check: one ok, one could-not-check" 'j$status == "partial" && j$summary$n_ok == 1 && j$summary$n_could_not_check == 1'
  fetch --op doi --doi "10.1177/0956797614520714";             check "doi metadata" 'j$status %in% c("ok","partial") && nzchar(j$summary$title) && nchar(j$sha256) == 64 && file.exists(file.path("'"$RUN"'", j$path))'
  fetch --op doi --doi "10.1177/0956797614520714";             check "repeat fetch is cached" 'isTRUE(j$cached)'
  fetch --op doi --doi "10.9999/not.a.real.doi.xyz";           check "bogus DOI -> failed" 'j$status == "failed"'
  fetch --op pubpeer --doi "10.1177/0146167211398138";         check "pubpeer" 'j$status == "ok" && j$summary$total_comments >= 0'
  fetch --op repo_list --url "https://osf.io/6nt4v";           check "OSF listing" 'j$status == "ok" && j$summary$n_files >= 1 && j$source_id == "repo_list_osf_6nt4v"'
  fetch --op repo_list --url "https://github.com/scienceverse/metacheck"; check "GitHub listing" 'j$status == "ok" && j$summary$n_files > 20'
  fetch --op repo_list --url "https://github.com/scienceverse/no-such-repo-xyz"; check "missing repo -> failed" 'j$status == "failed"'
  fetch --op readme --url "https://github.com/scienceverse/metacheck"; check "GitHub README" 'j$status == "ok" && j$source_id == "readme_github_scienceverse_metacheck" && j$bytes > 100'
  fetch --op file --url "https://osf.io/download/75qgk/";      check "OSF file download + sha256" 'j$status == "ok" && j$bytes == 185 && nchar(j$sha256) == 64'
  fetch --op file --url "https://raw.githubusercontent.com/scienceverse/metacheck/main/DESCRIPTION" --max-bytes 100 --force; check "per-file cap -> budget_exceeded, no file kept" 'j$status == "budget_exceeded" && is.null(j$path)'
  fetch --op prereg --url "https://osf.io/bdvxs";              check "OSF registration content (or recorded failure)" 'j$status == "ok" && j$summary$n_responses > 0 || j$status == "failed" && nzchar(j$error)'
  fetch --op link_check --urls "https://example.com" --max-requests 1; check "request budget -> budget_exceeded" 'j$status == "budget_exceeded"'
  OUT=$(cat "$RUN/sources/index.json");                        check "index: unique ids, nothing dropped" '!anyDuplicated(sapply(j, \(e) e$source_id)) && length(j) >= 14'
else
  echo "skip - network tests (offline)"
fi

echo "passed: $pass  failed: $fail"
[ "$fail" -eq 0 ]
