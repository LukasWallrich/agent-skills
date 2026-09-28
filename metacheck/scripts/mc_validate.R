#!/usr/bin/env Rscript
# Validate agent findings against the schema, the module candidates and the quoted sources.
#   Rscript scripts/mc_validate.R --run-dir <dir> [--module <m>]
# Writes <run-dir>/findings_validated.json, prints a summary; exit status 2 if any finding is invalid.

mc_skill_dir <- dirname(dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))))
source(file.path(mc_skill_dir, "scripts", "_common.R"))
source(file.path(mc_skill_dir, "scripts", "_validate.R"))

mc_main({
  args <- mc_require(mc_args(), "run_dir")
  run_dir <- normalizePath(args$run_dir, mustWork = TRUE)
  res <- mc_validate_run(run_dir, if (is.character(args$module)) args$module)$out
  if (is.character(args$module) && file.exists(file.path(run_dir, "findings_validated.json"))) {
    # single-module re-validation: keep the other modules' earlier results
    old <- mc_read_json(file.path(run_dir, "findings_validated.json"))
    res$modules <- c(Filter(function(s) !identical(s$module, args$module), old$modules), res$modules)
    res$findings <- c(Filter(function(f) !identical(f$module, args$module), old$findings), res$findings)
    res$n_findings <- length(res$findings)
    res$n_invalid <- sum(!vapply(res$findings, function(f) isTRUE(f$valid), logical(1)))
    res$n_unadjudicated <- sum(vapply(res$modules, function(s) length(s$unadjudicated), 1L))
    if (res$n_invalid > 0 || any(vapply(res$modules, function(s) !is.null(s$file_error), logical(1)))) res$status <- "invalid"
  }
  mc_write_json(res, file.path(run_dir, "findings_validated.json"))
  mc_manifest_update(run_dir, list(
    schema_versions = list(finding = res$schema_version), light_mapping_version = res$mapping_version,
    validated_at = res$validated_at))
  invalid <- Filter(function(f) !isTRUE(f$valid), res$findings)
  mc_out(list(status = res$status, output = file.path(run_dir, "findings_validated.json"),
              n_findings = res$n_findings, n_invalid = res$n_invalid, n_unadjudicated = res$n_unadjudicated,
              invalid = invalid, modules = res$modules, note = res$note))
  if (res$status != "ok") quit(status = 2, save = "no")
})
