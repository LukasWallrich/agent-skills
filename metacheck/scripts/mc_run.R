#!/usr/bin/env Rscript
# Run metacheck modules on an imported paper, in dependency order, one result file per module.
#   mc_run.R --run-dir <dir> --modules a,b,c|default [--args-json '{"power":{...}}'] [--force]
source(file.path(dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])), "_common.R"))

# upstream modules (CONTRACT.md). hard = downstream is skipped when an upstream failed;
# otherwise the module tolerates missing upstream tables and runs on what is available
DEPS <- list(
  code_check = c("repo_check"),
  ref_summary = c("ref_accuracy", "ref_pubpeer", "ref_replication", "ref_retraction")
)
HARD_DEPS <- "code_check"
NETWORK <- c("ref_pubpeer", "ref_accuracy", "repo_check", "code_check", "prereg_check", "causal_claims")
FETCH_FAIL <- "could not connect|could not resolve|download failed|cannot open URL|status was|timed? ?out|HTTP [45][0-9]{2}|failed to perform"

# requested modules plus missing upstreams, upstreams first, otherwise in requested order
resolve_order <- function(modules) {
  out <- character(0)
  for (m in modules) out <- union(out, c(DEPS[[m]], m))
  list(order = out, auto_added = setdiff(out, modules))
}

# drop the nested paper / previous outputs; the chain is rebuilt from the per-module files
strip_output <- function(mo) {
  mo$paper <- NULL
  mo$prev_outputs <- NULL
  mo
}

# what module_run() expects as piped input: the last output carrying paper + earlier outputs
chain_input <- function(paper, outputs) {
  if (!length(outputs)) return(paper)
  n <- length(outputs)
  carrier <- outputs[[n]]
  carrier$paper <- paper
  carrier$prev_outputs <- outputs[-n]
  carrier$summary_table <- data.frame(paper_id = paper$paper_id) # keep each summary_table module-specific
  class(carrier) <- "metacheck_module_output"
  carrier
}

# cheap signs that a module swallowed a fetch error
soft_warnings <- function(module, mo) {
  w <- character(0)
  tbl <- mo$table
  if (is.data.frame(tbl) && "repo_error" %in% names(tbl)) {
    err <- unique(tbl[!is.na(tbl$repo_error), intersect(c("repo_url", "repo_error"), names(tbl)), drop = FALSE])
    if (nrow(err)) w <- c(w, paste0("Repository could not be checked: ", do.call(paste, c(unname(as.list(err)), sep = " - "))))
  }
  if (module == "ref_accuracy" && is.data.frame(tbl) && "no_match" %in% names(tbl) && nrow(tbl) && all(tbl$no_match %in% TRUE))
    w <- c(w, "No reference could be matched; this may be a lookup failure rather than inaccurate references")
  w
}

result_json <- function(module, info, args, status, error = NULL, upstream = character(0), elapsed = NA,
                        mo = list(), warnings = character(0), extra = list()) {
  tbl <- mo$table
  if (is.data.frame(tbl) && nrow(tbl)) {
    tbl <- cbind(mc_candidate_ids(tbl, extra$paper_id, module)[, c("candidate_id", "item_id")], as.data.frame(tbl))
  } else tbl <- list()
  c(list(
    schema_version = "1", module = module, title = mo$title %||% info$title %||% module,
    section = mo$section %||% NA, args = if (length(args)) args else setNames(list(), character(0)),
    status = status, error = error, upstream = I(upstream), elapsed_s = round(elapsed, 2),
    traffic_light = if (status == "ok") mo$traffic_light else NA,
    summary_text = if (status == "ok" && length(mo$summary_text)) paste(mo$summary_text, collapse = "\n") else NA,
    report = I(as.character(unlist(mo$report %||% character(0)))),
    summary_table = if (is.data.frame(mo$summary_table)) mo$summary_table else list(),
    table = tbl, warnings = I(unique(warnings))
  ), extra[setdiff(names(extra), "paper_id")])
}

mc_main({
  args <- mc_args()
  mc_require(args, c("run_dir", "modules"))
  if (!is.character(args$modules)) mc_fail("--modules needs a comma-separated list or 'default'")
  mc_load()
  run_dir <- normalizePath(args$run_dir, mustWork = FALSE)
  paper <- mc_paper(run_dir)
  mod_dir <- file.path(run_dir, "modules")
  dir.create(mod_dir, showWarnings = FALSE)
  force <- isTRUE(args$force)

  requested <- trimws(strsplit(args$modules, ",")[[1]])
  if (identical(requested, "default")) requested <- eval(formals(report)$modules)
  requested <- unique(requested[nzchar(requested)])
  mod_args <- if (is.character(args$args_json)) jsonlite::fromJSON(args$args_json, simplifyVector = TRUE) else list()
  if (length(mod_args) && (!is.list(mod_args) || is.null(names(mod_args)))) mc_fail("--args-json must be an object keyed by module name")
  plan <- resolve_order(requested)
  unknown <- setdiff(names(mod_args), plan$order)
  if (length(unknown)) mc_fail("--args-json has arguments for modules that are not run: ", paste(unknown, collapse = ", "))

  is_online <- tryCatch(online(), error = function(e) FALSE)
  outputs <- list() # stripped ok outputs, in run order
  status <- list()

  for (m in plan$order) {
    mc_log("[", match(m, plan$order), "/", length(plan$order), "] ", m)
    a <- as.list(mod_args[[m]])
    ups <- DEPS[[m]] %||% character(0)
    ups_failed <- ups[!ups %in% names(outputs)]
    json_path <- file.path(mod_dir, paste0(m, ".json"))
    rds_path <- file.path(mod_dir, paste0(m, ".rds"))
    extra <- list(paper_id = paper$paper_id, requested = m %in% requested, reused = FALSE, run_at = mc_now())
    if (length(ups)) extra$upstream_failed <- I(ups_failed)
    # module_find is internal in installed metacheck; load_all() exposes it in development.
    info <- tryCatch(module_info(getFromNamespace("module_find", "metacheck")(m)), error = function(e) NULL)
    res <- NULL

    # reuse an earlier ok result (same args) unless --force or an upstream was re-run
    if (!force && file.exists(json_path) && file.exists(rds_path)) {
      old <- mc_read_json(json_path)
      same_args <- identical(jsonlite::toJSON(old$args, auto_unbox = TRUE), jsonlite::toJSON(if (length(a)) a else setNames(list(), character(0)), auto_unbox = TRUE))
      ups_reused <- all(vapply(ups, \(u) isTRUE(status[[u]]$reused) || !u %in% names(status), TRUE))
      if (identical(old$status, "ok") && same_args && ups_reused) {
        outputs[[m]] <- readRDS(rds_path)
        status[[m]] <- list(module = m, status = "ok", reused = TRUE, requested = m %in% requested, elapsed_s = old$elapsed_s,
                            traffic_light = old$traffic_light, n_rows = length(old$table), error = NULL, n_warnings = length(old$warnings))
        mc_log("  reused")
        next
      }
    }

    if (is.null(info)) {
      res <- result_json(m, NULL, a, "failed", paste0("Unknown module: ", m), ups, 0, extra = extra)
    } else if (m %in% HARD_DEPS && length(ups_failed)) {
      res <- result_json(m, info, a, "skipped_upstream_failed", paste0("Upstream module(s) did not complete: ", paste(ups_failed, collapse = ", ")), ups, 0, extra = extra)
    } else if (length(ups) && length(ups_failed) == length(ups)) {
      res <- result_json(m, info, a, "skipped_upstream_failed", "All upstream modules failed", ups, 0, extra = extra)
    } else {
      warns <- character(0)
      if (length(ups_failed)) warns <- paste0("Ran without failed upstream module(s): ", paste(ups_failed, collapse = ", "), "; their checks are missing from this output, not clean")
      if (m %in% NETWORK && !is_online) warns <- c(warns, "No internet connection detected when this module ran")
      t0 <- proc.time()[["elapsed"]]
      sink(stderr(), type = "output") # modules may print; stdout is reserved for the JSON result
      mo <- tryCatch(
        withCallingHandlers(
          do.call(module_run, c(list(paper = chain_input(paper, outputs), module = m), a)),
          warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") }
        ),
        error = function(e) e
      )
      sink(type = "output")
      elapsed <- proc.time()[["elapsed"]] - t0
      if (inherits(mo, "error")) {
        res <- result_json(m, info, a, "failed", conditionMessage(mo), ups, elapsed, warnings = warns, extra = extra)
      } else if (mo$traffic_light %in% c("fail", "error")) {
        # the module reports it could not do its check; keep its output but never as ok
        res <- result_json(m, info, a, "failed", mo$summary_text %||% "Module reported an error", ups, elapsed, mo, warns, extra)
        res$traffic_light <- mo$traffic_light
      } else if (m %in% NETWORK && !is_online && !mo$traffic_light %in% "na") {
        res <- result_json(m, info, a, "failed", "No internet connection: the module ran but its online lookups cannot have succeeded", ups, elapsed, mo, warns, extra)
      } else if (m %in% NETWORK && NROW(mo$table) == 0 && any(grepl(FETCH_FAIL, warns, ignore.case = TRUE))) {
        # fetch errors downgraded to warnings and nothing retrieved: "could not check", not "nothing found"
        res <- result_json(m, info, a, "failed", paste0("Online lookups failed: ", paste(grep(FETCH_FAIL, warns, ignore.case = TRUE, value = TRUE), collapse = "; ")), ups, elapsed, mo, warns, extra)
      } else {
        mo <- strip_output(mo)
        saveRDS(mo, rds_path)
        outputs[[m]] <- mo
        res <- result_json(m, info, a, "ok", NULL, ups, elapsed, mo, c(warns, soft_warnings(m, mo)), extra)
      }
    }
    if (res$status != "ok" && file.exists(rds_path)) file.remove(rds_path) # never leave a stale ok output behind
    mc_write_json(res, json_path)
    status[[m]] <- list(module = m, status = res$status, reused = FALSE, requested = m %in% requested, elapsed_s = res$elapsed_s,
                        traffic_light = res$traffic_light, n_rows = NROW(res$table), error = res$error, n_warnings = length(res$warnings))
    mc_log("  ", res$status, if (!is.null(res$error)) paste0(": ", res$error))
  }

  # keep entries from earlier invocations for modules not touched now
  old_path <- file.path(run_dir, "run_status.json")
  if (file.exists(old_path)) {
    for (o in mc_read_json(old_path)$modules) {
      if (!o$module %in% names(status) && file.exists(file.path(mod_dir, paste0(o$module, ".json")))) status[[o$module]] <- c(o, list(earlier_run = TRUE))
    }
  }
  st <- vapply(status, \(s) s$status, "")
  run_status <- list(
    status = "ok", paper_id = paper$paper_id, run_at = mc_now(), online = is_online,
    requested = I(requested), order = I(plan$order), auto_added = I(plan$auto_added),
    counts = as.list(table(factor(st, c("ok", "failed", "skipped_upstream_failed", "skipped_missing_input")))),
    modules = unname(status)
  )
  mc_write_json(run_status, file.path(run_dir, "run_status.json"))
  mc_manifest_update(run_dir, list(last_module_run_at = run_status$run_at))
  mc_out(run_status)
})
