#!/usr/bin/env Rscript
# Build one run set (one run dir per labelled paper) from a label file, and run the module.
#   mc_eval_build.R --labels <file> --out <run-set-dir> [--module marginal] [--split dev|heldout]
#                   [--splits eval/splits.json] [--direct] [--force]
# fixture column: "inline" (paper built from the label rows via test_paper(); text_id = row order),
# "demo" (metacheck demo paper), or a paper file path (absolute, or relative to eval/fixtures/).
# Uses scripts/mc_import.R + mc_run.R when present; --direct (or their absence) does the same in R.
here <- dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])))
source(file.path(here, "..", "scripts", "_common.R"))
source(file.path(here, "_eval_common.R"))

script <- function(name) file.path(here, "..", "scripts", name)

# call a sibling script; TRUE if it exited 0
call_script <- function(name, args) {
  out <- suppressWarnings(system2(ev_rscript(), c(shQuote(script(name)), args), stdout = TRUE, stderr = FALSE))
  ok <- is.null(attr(out, "status"))
  if (!ok) mc_log("  ", name, " failed: ", paste(out, collapse = " "))
  ok
}

# inline fixture -> paper JSON on disk, so it goes through the same import path as real papers
inline_paper <- function(rows, paper_id, dir) {
  rows <- rows[order(rows$text_id), ]
  if (!identical(rows$text_id, seq_len(nrow(rows)))) mc_fail("Inline fixture ", paper_id, ": text_id must be 1..n without gaps")
  p <- test_paper(rows$text)
  p$paper_id <- paper_id
  p$info$title <- paste("SYNTHETIC fixture", paper_id)
  p$info$file_hash <- paper_id
  paper_write(p, paper_id, dir)
}

import_direct <- function(src, run_dir) {
  paper <- read(src)
  if (!.is_paper(paper)) mc_fail("Could not read ", src)
  saveRDS(paper, file.path(run_dir, "paper.rds"))
  paper_write(paper, "paper", run_dir)
  mc_manifest_update(run_dir, list(
    run_id = paste0(paper$paper_id, "_", format(Sys.time(), "%Y%m%dT%H%M%SZ", tz = "UTC")), paper_id = paper$paper_id,
    source = list(path = src, sha256 = mc_sha256(src)),
    metacheck = list(version = as.character(utils::packageVersion("metacheck"))),
    r_version = R.version.string, created_at = mc_now(), built_by = "eval/mc_eval_build.R --direct"))
}

# CONTRACT module result, written directly
run_direct <- function(run_dir, module) {
  paper <- mc_paper(run_dir)
  t0 <- Sys.time()
  mo <- tryCatch(module_run(paper, module), error = function(e) e)
  failed <- inherits(mo, "error")
  tab <- if (failed) NULL else as.data.frame(mo$table)
  if (NROW(tab)) tab <- cbind(mc_candidate_ids(tab, paper$paper_id, module)[, c("candidate_id", "item_id")], tab)
  res <- list(module = module, title = if (!failed) mo$title, section = if (!failed) mo$section, args = list(),
    status = if (failed) "failed" else "ok", error = if (failed) conditionMessage(mo), upstream = list(),
    elapsed_s = round(as.numeric(difftime(Sys.time(), t0, units = "secs")), 2),
    traffic_light = if (!failed) mo$traffic_light, summary_text = if (!failed) mo$summary_text,
    report = if (!failed) as.list(mo$report) else list(),
    summary_table = if (!failed) as.data.frame(mo$summary_table) else list(), table = tab %or% list())
  mc_write_json(res, file.path(run_dir, "modules", paste0(module, ".json")))
  if (!failed) { mo$paper <- NULL; mo$prev_outputs <- NULL; saveRDS(mo, file.path(run_dir, "modules", paste0(module, ".rds"))) }
  mc_write_json(list(modules = list(list(module = module, status = res$status, error = res$error))), file.path(run_dir, "run_status.json"))
}

mc_main({
  args <- mc_args(list(module = "marginal", splits = file.path(here, "splits.json")))
  mc_require(args, c("labels", "out"))
  lab <- ev_labels(args$labels, args$module, if (is.character(args$split)) args$split, args$splits)
  mc_load()
  direct <- isTRUE(args$direct)
  use_import <- !direct && file.exists(script("mc_import.R"))
  use_run <- !direct && file.exists(script("mc_run.R"))
  dir.create(args$out, recursive = TRUE, showWarnings = FALSE)
  out <- normalizePath(args$out)
  tmp <- tempfile("ev_fix"); dir.create(tmp)

  built <- lapply(unique(lab$paper_id), function(id) {
    rows <- lab[lab$paper_id == id, ]
    fixture <- unique(rows$fixture)
    if (length(fixture) != 1) mc_fail("Paper ", id, " has more than one fixture value")
    run_dir <- file.path(out, id)
    if (dir.exists(run_dir) && !isTRUE(args$force)) mc_fail("Run dir exists (use --force): ", run_dir)
    unlink(run_dir, recursive = TRUE)
    dir.create(file.path(run_dir, "modules"), recursive = TRUE)
    dir.create(file.path(run_dir, "findings"))
    dir.create(file.path(run_dir, "sources"))
    mc_log("Building ", id, " (", fixture, ")")
    src <- switch(fixture, inline = inline_paper(rows, id, tmp), demo = demofile("json"),
      if (file.exists(fixture)) fixture else file.path(here, "fixtures", fixture))
    if (!file.exists(src)) mc_fail("Fixture file not found for ", id, ": ", src)

    how <- c(import = "direct", run = "direct")
    if (use_import && call_script("mc_import.R", c("--file", shQuote(src), "--run-dir", shQuote(run_dir)))) how["import"] <- "mc_import.R"
    if (how["import"] == "direct") import_direct(src, run_dir)
    pid <- mc_paper(run_dir)$paper_id
    if (pid != id) mc_fail("Fixture for ", id, " imports as paper_id ", pid, "; label paper_id must match")
    mod_file <- file.path(run_dir, "modules", paste0(args$module, ".json"))
    if (use_run && call_script("mc_run.R", c("--run-dir", shQuote(run_dir), "--modules", args$module)) &&
        file.exists(mod_file)) how["run"] <- "mc_run.R"
    if (how["run"] == "direct") run_direct(run_dir, args$module)

    mod <- mc_read_json(mod_file)
    mc_manifest_update(run_dir, list(eval = list(labels = normalizePath(args$labels), labels_sha256 = mc_sha256(args$labels),
      fixture = fixture, synthetic = any(rows$synthetic), import_via = how[["import"]], run_via = how[["run"]])))
    list(paper_id = id, run_dir = run_dir, fixture = fixture, import_via = how[["import"]], run_via = how[["run"]],
         module_status = mod$status, candidate_ids = I(vapply(mod$table, function(r) r$candidate_id, "")))
  })
  mc_out(list(status = "ok", module = args$module, run_set = out, n_papers = length(built), papers = built,
              next_step = "agent writes <run_dir>/findings/<module>.json per paper; then mc_eval_score.R"))
})
