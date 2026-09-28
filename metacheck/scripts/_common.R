# Shared helpers for the mc_*.R scripts. Source with:
#   source(file.path(dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])), "_common.R"))

# parse "--flag value" / "--flag" (TRUE) pairs; dashes in names become underscores
mc_args <- function(defaults = list(), args = commandArgs(trailingOnly = TRUE)) {
  out <- defaults
  i <- 1
  while (i <= length(args)) {
    if (!startsWith(args[i], "--")) mc_fail("Unexpected argument: ", args[i])
    key <- gsub("-", "_", substring(args[i], 3))
    if (i == length(args) || startsWith(args[i + 1], "--")) {
      out[[key]] <- TRUE
      i <- i + 1
    } else {
      out[[key]] <- args[i + 1]
      i <- i + 2
    }
  }
  out
}

mc_require <- function(args, keys) {
  missing <- setdiff(keys, names(args))
  if (length(missing)) mc_fail("Missing required argument(s): --", paste(gsub("_", "-", missing), collapse = ", --"))
  invisible(args)
}

# load metacheck: dev checkout if METACHECK_DEV_PATH is set, else the installed package
mc_load <- function() {
  dev <- Sys.getenv("METACHECK_DEV_PATH", "")
  if (nzchar(dev)) {
    suppressMessages(devtools::load_all(path.expand(dev), quiet = TRUE))
  } else if (requireNamespace("metacheck", quietly = TRUE)) {
    suppressMessages(library(metacheck))
  } else {
    mc_fail("metacheck is not installed and METACHECK_DEV_PATH is not set")
  }
  invisible(TRUE)
}

mc_log <- function(...) message(...)

mc_now <- function() format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")

mc_write_json <- function(x, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  jsonlite::write_json(x, path, auto_unbox = TRUE, pretty = TRUE, null = "null", na = "null", dataframe = "rows", digits = NA)
  invisible(path)
}

mc_read_json <- function(path, simplify = FALSE) {
  jsonlite::read_json(path, simplifyVector = simplify)
}

# print the script's single JSON result to stdout
mc_out <- function(x) {
  cat(jsonlite::toJSON(x, auto_unbox = TRUE, pretty = TRUE, null = "null", na = "null", dataframe = "rows", digits = NA), "\n")
}

mc_fail <- function(...) {
  mc_out(list(status = "error", message = paste0(...)))
  quit(status = 1, save = "no")
}

# run the script body, turning any R error into the JSON error contract
mc_main <- function(expr) {
  tryCatch(expr, error = function(e) mc_fail(conditionMessage(e)))
}

mc_sha256 <- function(path) digest::digest(path, algo = "sha256", file = TRUE)

# read, update and write back manifest.json without dropping existing keys
mc_manifest_update <- function(run_dir, updates) {
  path <- file.path(run_dir, "manifest.json")
  man <- if (file.exists(path)) mc_read_json(path) else list()
  man <- utils::modifyList(man, updates)
  mc_write_json(man, path)
  invisible(man)
}

mc_paper <- function(run_dir) {
  path <- file.path(run_dir, "paper.rds")
  if (!file.exists(path)) mc_fail("No paper.rds in ", run_dir, "; run mc_import.R first")
  readRDS(path)
}

# item_id / candidate_id per CONTRACT.md
mc_candidate_ids <- function(table, paper_id, module) {
  n <- nrow(table)
  if (is.null(n) || n == 0) return(character(0))
  item <- paste0("r", seq_len(n))
  if ("text_id" %in% names(table)) {
    ok <- !is.na(table$text_id)
    item[ok] <- paste0("t", table$text_id[ok])
  }
  if (startsWith(module, "ref_") && "bib_id" %in% names(table)) {
    ok <- !is.na(table$bib_id)
    item[ok] <- paste0("b", table$bib_id[ok])
  }
  dup <- stats::ave(seq_len(n), item, FUN = seq_along)
  item <- ifelse(dup > 1, paste0(item, ".", dup), item)
  data.frame(item_id = item, candidate_id = paste(paper_id, module, item, sep = ":"))
}
