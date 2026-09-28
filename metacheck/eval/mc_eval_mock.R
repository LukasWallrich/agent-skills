#!/usr/bin/env Rscript
# Expand a compact verdict sheet into schema-conformant findings/<module>.json files in a run set.
# For harness tests and for importing verdicts recorded elsewhere; it is not an agent.
#   mc_eval_mock.R --verdicts <csv> --runs <run-set-dir> [--module marginal]
# csv columns: paper_id, text_id, verdict, quote (verbatim from that sentence; may be empty), sweep (TRUE/FALSE), rationale
here <- dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])))
source(file.path(here, "..", "scripts", "_common.R"))
source(file.path(here, "_eval_common.R"))

mc_main({
  args <- mc_require(mc_args(list(module = "marginal")), c("verdicts", "runs"))
  v <- utils::read.csv(args$verdicts, stringsAsFactors = FALSE, comment.char = "")
  dirs <- ev_run_dirs(args$runs)
  if (length(miss <- setdiff(unique(v$paper_id), names(dirs)))) mc_fail("No run dir for: ", paste(miss, collapse = ", "))
  n <- vapply(unique(v$paper_id), function(id) {
    mod <- mc_read_json(file.path(dirs[[id]], "modules", paste0(args$module, ".json")))
    cand <- stats::setNames(vapply(mod$table, `[[`, "", "candidate_id"), vapply(mod$table, function(r) as.character(r$text_id), ""))
    rows <- v[v$paper_id == id, ]
    fs <- lapply(seq_len(nrow(rows)), function(i) {
      r <- rows[i, ]; item <- paste0("t", r$text_id); sweep <- isTRUE(as.logical(r$sweep))
      if (!sweep && is.na(cand[as.character(r$text_id)])) mc_fail(id, " text_id ", r$text_id, " is not a candidate; set sweep = TRUE")
      abstain <- r$verdict %in% c("insufficient_evidence", "not_checked")
      list(schema_version = "1",
        finding_id = if (sweep) paste(id, args$module, "sweep", item, sep = ":") else paste0(cand[[as.character(r$text_id)]], ":c1"),
        module = args$module, item_id = item, study_id = NULL, sweep = sweep,
        deterministic = list(source_id = if (!sweep) paste0("module:", args$module), value = if (!sweep) "regex match"),
        agent = list(verdict = r$verdict, classification = NULL, rationale = r$rationale,
          evidence = if (nzchar(r$quote)) list(list(source_id = "paper", text_id = r$text_id, location = item, quote = r$quote)) else list(),
          confidence = if (abstain) "low" else "high"),
        coverage = list(status = if (abstain) "partial" else "complete", checked_source_ids = list("paper"),
          limitations = if (abstain) list("mock: evidence not available") else list()),
        severity = if (r$verdict == "confirmed_issue") "medium" else "none", advice = "")
    })
    mc_write_json(fs, file.path(dirs[[id]], "findings", paste0(args$module, ".json")))
    length(fs)
  }, 1L)
  mc_out(list(status = "ok", run_set = normalizePath(args$runs), findings_written = as.list(n)))
})
