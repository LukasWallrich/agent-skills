# Shared helpers for eval/mc_eval_*.R. Source after scripts/_common.R.

EXTRACTION_LABELS <- c("candidate", "not_candidate")
CONTEXTUAL_LABELS <- c("confirmed_issue", "not_an_issue", "ambiguous", "unavailable_evidence")
LABEL_COLS <- c("paper_id", "module", "text_id", "text", "fixture", "synthetic", "extraction_label",
                "contextual_label", "ambiguous", "category", "rationale", "labeller")

ev_norm <- function(x) trimws(gsub("\\s+", " ", x))

ev_rscript <- function() file.path(R.home("bin"), "Rscript")

# read + check a label file; optionally keep one module / one split (split is by paper)
ev_labels <- function(path, module = NULL, split = NULL, splits_file = NULL) {
  if (!file.exists(path)) mc_fail("Label file not found: ", path)
  lab <- utils::read.csv(path, stringsAsFactors = FALSE, comment.char = "", encoding = "UTF-8", check.names = FALSE)
  miss <- setdiff(LABEL_COLS, names(lab))
  if (length(miss)) mc_fail("Label file lacks column(s): ", paste(miss, collapse = ", "))
  lab$synthetic <- as.logical(lab$synthetic)
  lab$ambiguous <- as.logical(lab$ambiguous)
  lab$text_id <- as.integer(lab$text_id)
  bad <- c(
    if (anyNA(lab$synthetic) || anyNA(lab$ambiguous)) "synthetic/ambiguous must be TRUE or FALSE",
    if (!all(lab$extraction_label %in% EXTRACTION_LABELS)) paste("extraction_label must be one of:", paste(EXTRACTION_LABELS, collapse = ", ")),
    if (!all(lab$contextual_label %in% CONTEXTUAL_LABELS)) paste("contextual_label must be one of:", paste(CONTEXTUAL_LABELS, collapse = ", ")),
    if (anyNA(lab$text_id)) "text_id must be an integer",
    if (anyDuplicated(lab[, c("paper_id", "module", "text_id")])) "duplicate (paper_id, module, text_id) rows",
    if (any(!nzchar(lab$labeller) | !nzchar(lab$rationale))) "labeller and rationale are required"
  )
  if (length(bad)) mc_fail("Invalid label file ", path, ": ", paste(bad, collapse = "; "))
  if (!is.null(module)) lab <- lab[lab$module == module, , drop = FALSE]
  if (!is.null(split)) {
    sp <- ev_splits(splits_file)
    if (!split %in% c("dev", "heldout")) mc_fail("--split must be dev or heldout")
    unassigned <- setdiff(unique(lab$paper_id), c(sp$dev, sp$heldout))
    if (length(unassigned)) mc_fail("Paper(s) not assigned to a split in ", splits_file, ": ", paste(unassigned, collapse = ", "))
    lab <- lab[lab$paper_id %in% sp[[split]], , drop = FALSE]
  }
  if (!nrow(lab)) mc_fail("No label rows left after filtering (module/split)")
  lab
}

ev_splits <- function(path) {
  if (is.null(path) || !file.exists(path)) mc_fail("Splits file not found: ", path)
  sp <- mc_read_json(path, simplify = TRUE)
  sp <- list(dev = as.character(unlist(sp$dev)), heldout = as.character(unlist(sp$heldout)))
  both <- intersect(sp$dev, sp$heldout)
  if (length(both)) mc_fail("Paper(s) in both dev and heldout: ", paste(both, collapse = ", "))
  sp
}

# run dirs (dirs holding a manifest.json) in a run set, named by paper_id
ev_run_dirs <- function(set_dir) {
  if (!dir.exists(set_dir)) mc_fail("Run set not found: ", set_dir)
  dirs <- if (file.exists(file.path(set_dir, "manifest.json"))) set_dir else list.dirs(set_dir, recursive = FALSE)
  dirs <- dirs[file.exists(file.path(dirs, "manifest.json"))]
  ids <- vapply(dirs, function(d) mc_read_json(file.path(d, "manifest.json"))$paper_id %or% NA_character_, "")
  stats::setNames(dirs, ids)
}

`%or%` <- function(a, b) if (is.null(a) || !length(a)) b else a

ev_div <- function(a, b) if (b == 0) NA_real_ else a / b

# ---- agreement (base R) ----
ev_cohen <- function(a, b) {
  po <- mean(a == b)
  pe <- sum(vapply(union(a, b), function(k) mean(a == k) * mean(b == k), 1))
  if (pe == 1) 1 else (po - pe) / (1 - pe)
}

# m: units x runs character matrix
ev_fleiss <- function(m) {
  cats <- unique(c(m)); n <- ncol(m)
  cnt <- matrix(vapply(cats, function(k) rowSums(m == k), numeric(nrow(m))), nrow = nrow(m))
  pe <- sum((colSums(cnt) / (nrow(m) * n))^2)
  if (pe == 1) 1 else (mean((rowSums(cnt^2) - n) / (n * (n - 1))) - pe) / (1 - pe)
}

ev_agreement <- function(m) {
  if (ncol(m) < 2 || !nrow(m)) return(NULL)
  pairs <- utils::combn(ncol(m), 2, simplify = FALSE)
  list(
    n_units = nrow(m),
    pairwise_agreement = mean(vapply(pairs, function(p) mean(m[, p[1]] == m[, p[2]]), 1)),
    pairwise_cohen_kappa = mean(vapply(pairs, function(p) ev_cohen(m[, p[1]], m[, p[2]]), 1)),
    fleiss_kappa = ev_fleiss(m)
  )
}
