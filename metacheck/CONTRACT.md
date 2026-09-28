# Internal contract (for skill developers)

This file fixes the interfaces between scripts, rubrics and the agent. Source plan:
`metacheck/_stuff/agent_skill_review.html` (sections 6 and 7). The skill is thin
scaffolding around the `metacheck` R package: **never reimplement package logic here**;
call exported (or, if unavoidable, `metacheck:::`) functions.

## Invocation

All scripts are `Rscript scripts/<name>.R --flag value ...`, source `scripts/_common.R`,
print a single JSON object to stdout (human-readable progress goes to stderr), and exit
non-zero with `{"status":"error","message":...}` on failure.

metacheck is loaded by `mc_load()` in `_common.R`: installed package, or
`devtools::load_all()` from `$METACHECK_DEV_PATH` if set. For example, on a
machine with the `r-metacheck` micromamba environment and a source checkout:

    export MAMBA_ROOT_PREFIX=~/micromamba METACHECK_DEV_PATH=~/metacheck
    ~/.local/bin/micromamba run -n r-metacheck Rscript scripts/mc_import.R ...

The demo paper for tests is `metacheck::demofile("json")` / `demopaper()`
(paper_id `to_err_is_human`). `pwr` is optional.

## Run directory

Everything for one paper lives in one run directory (`--run-dir`):

    run_dir/
      manifest.json          provenance: run_id, paper_id, source file + sha256, metacheck
                             version/commit, R version, database dates, schema versions,
                             timestamps. Agent adds model_id + settings. Scripts append, never drop keys.
      paper.rds / paper.json the imported paper (rds is what scripts reload; json is for the agent)
      import_summary.json    sections, counts (sentences, refs, xrefs, urls, tables), parse warnings
      modules/<module>.json  one per module run (see below); modules/<module>.rds = raw module output
      run_status.json        per-module status table for the whole run
      sources/index.json     external artefacts: [{source_id, uri, path, retrieved_at, sha256, status, bytes}]
                             The first entry is mc_fetch.R's budget (source_id "_budget", status "budget");
                             readers must skip it. Fetch ops are not locked: run them sequentially per run dir.
      sources/<files>
      design_brief.json      written by the agent (schemas/design_brief.schema.json)
      findings/<module>.json written by the agent: array of findings (schemas/finding.schema.json)
      findings_validated.json  output of mc_validate.R
      report.html            output of mc_report.R

## Module result JSON (`modules/<module>.json`)

    { "module", "title", "section", "args",
      "status": "ok" | "failed" | "skipped_upstream_failed" | "skipped_missing_input",
      "error": null | "message", "upstream": ["repo_check"], "elapsed_s",
      "traffic_light", "summary_text", "report": [...original report strings...],
      "summary_table": [...], "table": [ {candidate_id, item_id, ...original columns} ] }

A failed API call / module is "could not check", never "nothing found": `status != "ok"`
must never be rendered or interpreted as a clean result. Original table columns and the
original traffic light are preserved untouched.

## IDs

- `item_id`: `t<text_id>` for rows with a text_id; `b<bib_id>` for reference rows (bib_id
  takes precedence in ref_* modules); otherwise `r<row number>`. Duplicates within a module
  get `.2`, `.3` suffixes in row order.
- `candidate_id`: `<paper_id>:<module>:<item_id>`
- `finding_id`: `<candidate_id>:c<n>` (n from 1). Recall-sweep findings that have no
  deterministic candidate use `<paper_id>:<module>:sweep:<item_id>`.
- `source_id`: `paper` for the paper itself; `module:<module>` for a module output;
  otherwise an id listed in `sources/index.json`.

## Finding (agent output) — see `schemas/finding.schema.json`

Fields: schema_version, finding_id, module, item_id, study_id, deterministic{source_id,value},
agent{verdict, classification, rationale, extracted?, evidence[{source_id,text_id?,location,quote}], confidence},
coverage{status, checked_source_ids, limitations, searches?}, severity, advice.

- verdict: confirmed_issue | not_an_issue | insufficient_evidence | not_checked | not_applicable
- confidence: high | medium | low
- coverage.status: complete | partial | not_performed
- severity: high | medium | low | none   (describes an identified issue; it is not a light)

`mc_validate.R` checks schema, that candidate/source ids exist, and that every quote is
found verbatim (whitespace-normalised) in the identified source/text_id. Absence claims
carry `coverage.searches` instead of quotes.

## Lights

Pilot = shadow mode: the report shows original module lights and adjudicated findings side
by side. `assets/light_mapping.json` (versioned) defines the deterministic mapping from
(verdicts, severity, coverage, module status) to an adjudicated light; it is displayed as
"shadow light" only. Incomplete checks can never map to green; confirmed issues stay
visible under partial coverage.

## Dependencies between modules

`code_check` needs `repo_check`; `ref_summary` needs `ref_accuracy`, `ref_pubpeer`,
`ref_replication`, `ref_retraction`. The runner orders modules and, when an upstream
failed, still records the downstream as `skipped_upstream_failed` (or runs it if the
package tolerates the missing input — record which).
