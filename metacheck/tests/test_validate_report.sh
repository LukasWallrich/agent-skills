#!/usr/bin/env bash
# Tests mc_validate.R and mc_report.R on a fixture run dir built from the demo paper.
#   export MAMBA_ROOT_PREFIX=~/micromamba METACHECK_DEV_PATH=~/metacheck
#   RSCRIPT="$HOME/.local/bin/micromamba run -n r-metacheck Rscript" bash tests/test_validate_report.sh
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
R=${RSCRIPT:-Rscript}
run="$(mktemp -d)/run"
fails=0
check() { if eval "$2"; then echo "ok   - $1"; else echo "FAIL - $1"; fails=$((fails + 1)); fi; }
json() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))" "$1" "$2"; }
mod() { json "$run/findings_validated.json" "[m for m in d['modules'] if m['module']=='$1'][0]$2"; }

$R "$here/tests/make_fixture.R" "$run" >/dev/null 2>&1
check "fixture built" "[ -f $run/modules/marginal.json ]"

# 1. deterministic only: no findings at all
$R "$here/scripts/mc_validate.R" --run-dir "$run" >/dev/null; check "no findings: validate exits 0" "[ $? -eq 0 ]"
check "no findings: candidates unadjudicated" "[ \"\$(mod marginal \"['unadjudicated'].__len__()\")\" = 2 ]"
check "no findings: shadow keeps original red, not green" "[ \"\$(mod marginal \"['shadow_light']\")\" = red ]"
check "failed module: shadow light is fail" "[ \"\$(mod ref_retraction \"['shadow_light']\")\" = fail ]"
$R "$here/scripts/mc_report.R" --run-dir "$run" >/dev/null; check "no findings: report exits 0" "[ $? -eq 0 ]"
check "no findings: report says deterministic only" "grep -q 'deterministic module output only' $run/report.html"
check "failed module listed as could not check" "grep -q 'could not be checked' $run/report.html"

# 2. valid findings
mkdir -p "$run/findings"; cp "$here/tests/fixtures/marginal_valid.json" "$run/findings/marginal.json"
$R "$here/scripts/mc_validate.R" --run-dir "$run" --module marginal >/dev/null; check "valid: validate exits 0" "[ $? -eq 0 ]"
check "valid: 0 invalid" "[ \"\$(json $run/findings_validated.json \"d['n_invalid']\")\" = 0 ]"
check "valid: medium confirmed issue -> yellow" "[ \"\$(mod marginal \"['shadow_light']\")\" = yellow ]"
check "valid: original light preserved" "[ \"\$(mod marginal \"['original_light']\")\" = red ]"
check "valid: --module keeps other modules" "[ \"\$(mod ref_retraction \"['shadow_light']\")\" = fail ]"
check "note disclaims interpretation" "json $run/findings_validated.json \"d['note']\" | grep -q 'does NOT check that the evidence supports'"
check "manifest keeps keys, gains mapping version" "[ \"\$(json $run/manifest.json \"d['model_id']+d['light_mapping_version']\")\" = fixture-model1 ]"
$R "$here/scripts/mc_report.R" --run-dir "$run" --out "$run/out/r.html" >/dev/null; check "valid: report exits 0" "[ $? -eq 0 ]"
check "report lists confirmed issue first" "grep -q 'class=\"priority\"' $run/out/r.html && grep -q 'to_err_is_human:marginal:t16:c1' $run/out/r.html"
check "report collapses not_an_issue" "grep -q '1 flagged item(s) judged not an issue' $run/out/r.html"
check "report has no unrendered R chunk or callout" "! grep -q -e '{r}' -e ':::' $run/out/r.html"
check "report has provenance" "grep -q 'fixture-model' $run/out/r.html && grep -q 'Light mapping' $run/out/r.html"
check "report is self-contained" "! grep -q -e '<script' -e '<link ' $run/out/r.html"

# 3. only the clean finding + all clear -> green; drop it -> cannot be green
python3 - "$run" <<'P'
import json, sys
p = sys.argv[1] + "/findings/marginal.json"; d = json.load(open(p))
for f in d: f["agent"]["verdict"] = "not_an_issue"; f["severity"] = "none"
json.dump(d, open(p, "w"))
P
$R "$here/scripts/mc_validate.R" --run-dir "$run" >/dev/null
check "all not_an_issue, complete -> green" "[ \"\$(mod marginal \"['shadow_light']\")\" = green ]"
python3 - "$run" <<'P'
import json, sys
p = sys.argv[1] + "/findings/marginal.json"; d = json.load(open(p))
d[0]["coverage"]["status"] = "partial"; d[0]["coverage"]["limitations"] = ["budget reached"]
json.dump(d, open(p, "w"))
P
$R "$here/scripts/mc_validate.R" --run-dir "$run" >/dev/null
check "partial coverage -> not green" "[ \"\$(mod marginal \"['shadow_light']\")\" != green ]"

# 4. invalid findings: fabricated quote, bad enum, unknown candidate and source
cp "$here/tests/fixtures/marginal_invalid.json" "$run/findings/marginal.json"
$R "$here/scripts/mc_validate.R" --run-dir "$run" >"$run/v.json"; check "invalid: validate exits non-zero" "[ $? -ne 0 ]"
check "invalid: fabricated quote caught" "grep -q 'quote not found verbatim in text_id 16' $run/v.json"
check "invalid: bad verdict caught" "grep -q 'agent.verdict' $run/v.json"
check "invalid: unknown candidate caught" "grep -q \"marginal:t99' is not in modules\" $run/v.json"
check "invalid: unknown source caught" "grep -q \"source_id 'retraction_watch'\" $run/v.json"
check "invalid: candidates fall back to unadjudicated" "[ \"\$(mod marginal \"['unadjudicated'].__len__()\")\" = 2 ]"
$R "$here/scripts/mc_report.R" --run-dir "$run" >/dev/null; check "invalid: report still renders" "[ $? -eq 0 ]"
check "invalid findings excluded and listed in limits" "grep -q 'failed validation and is excluded' $run/report.html && ! grep -q 'Fabricated quote' $run/report.html"

# 5. external source quotes + sweep ids + absence claim
mkdir -p "$run/sources"; printf 'Line one.\nWe  planned “N = 100” participants.\n' > "$run/sources/prereg.txt"
echo '[{"source_id":"prereg1","uri":"https://osf.io/x","path":"sources/prereg.txt","retrieved_at":"2026-01-01T00:00:00Z","sha256":"abc","status":"ok","bytes":40}]' > "$run/sources/index.json"
python3 - "$run" "$here" <<'P'
import json, sys
run, here = sys.argv[1:3]
f = json.load(open(here + "/tests/fixtures/marginal_valid.json"))
s = json.loads(json.dumps(f[0]))
s.update(finding_id="to_err_is_human:marginal:sweep:t17", item_id="t17", sweep=True)
s["deterministic"] = {"source_id": None, "value": None}
s["agent"]["evidence"] = [{"source_id": "prereg1", "location": "line 2", "quote": "We planned \"N = 100\" participants."}]
s["coverage"]["checked_source_ids"] = ["paper", "prereg1"]
a = json.loads(json.dumps(f[0])); a["finding_id"] = "to_err_is_human:marginal:t16:c2"
a["agent"]["evidence"] = []; a["coverage"]["searches"] = [{"query": "alpha", "scope": "paper full text", "n_hits": 0}]
json.dump(f + [s, a], open(run + "/findings/marginal.json", "w"))
P
$R "$here/scripts/mc_validate.R" --run-dir "$run" >"$run/v.json"; check "external quote, sweep id and absence claim validate" "[ $? -eq 0 ]"
$R "$here/scripts/mc_report.R" --run-dir "$run" >/dev/null; check "report renders sweep + searches" "[ $? -eq 0 ] && grep -q 'recall sweep' $run/report.html && grep -q 'searched <code>alpha</code>' $run/report.html"

# 6. schemas and mapping are valid JSON
for f in schemas/finding.schema.json schemas/design_brief.schema.json assets/light_mapping.json; do
  check "$f parses" "python3 -c 'import json; json.load(open(\"$here/$f\"))'"
done

echo; [ $fails -eq 0 ] && echo "all passed (run dir: $run)" || { echo "$fails failed (run dir: $run)"; exit 1; }
