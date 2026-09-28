#!/usr/bin/env Rscript
# Score deterministic candidates and agent verdicts against the two label layers.
#   mc_eval_score.R --labels <file> --runs <set1,set2,...> [--split dev|heldout] [--module marginal]
#                   [--criteria eval/criteria.json] [--splits eval/splits.json] [--out eval/results]
#                   [--no-validate] [--strict]
# A run set is a directory of run dirs (one per paper, as built by mc_eval_build.R) or a single run dir.
# Each run set is one agent run over the same papers; >= 2 sets give repeated-run agreement.
# --strict: exit status 3 unless every criterion passes. Does not load metacheck.
here <- dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])))
source(file.path(here, "..", "scripts", "_common.R"))
source(file.path(here, "_eval_common.R"))

VERDICT_PRIORITY <- c("confirmed_issue", "insufficient_evidence", "not_checked", "not_an_issue", "not_applicable")
ABSTAIN <- c("insufficient_evidence", "not_checked", "none")

item_text_id <- function(item_id) suppressWarnings(as.integer(sub("^t([0-9]+)(\\.[0-9]+)?$", "\\1", item_id)))

# findings_validated.json for this run dir, (re)made with mc_validate.R when missing or stale
validated <- function(run_dir, module, allow_run) {
  vf <- file.path(run_dir, "findings_validated.json")
  ff <- file.path(run_dir, "findings", paste0(module, ".json"))
  stale <- !file.exists(vf) || file.mtime(vf) < file.mtime(ff)
  vs <- file.path(here, "..", "scripts", "mc_validate.R")
  if (stale && allow_run && file.exists(vs)) {
    suppressWarnings(system2(ev_rscript(), c(shQuote(vs), "--run-dir", shQuote(run_dir)), stdout = FALSE, stderr = FALSE))
    stale <- !file.exists(vf) || file.mtime(vf) < file.mtime(ff)
  }
  if (stale) return(NULL)
  Filter(function(f) identical(f$module, module), mc_read_json(vf)$findings)
}

# one row per (paper, text unit): labels, is_candidate, unit verdict
score_paper <- function(run_dir, lab, module, allow_validate) {
  id <- lab$paper_id[1]
  mod_file <- file.path(run_dir, "modules", paste0(module, ".json"))
  mod <- if (file.exists(mod_file)) mc_read_json(mod_file) else list(status = "missing")
  if (!identical(mod$status, "ok")) return(list(could_not_check = sprintf("%s: module status %s", id, mod$status)))

  # labels are keyed by text_id; the text must match the imported paper (re-key by text if the parse shifted)
  text <- readRDS(file.path(run_dir, "paper.rds"))$text
  problems <- character(0)
  for (i in seq_len(nrow(lab))) {
    same <- ev_norm(text$text[match(lab$text_id[i], text$text_id)]) %in% ev_norm(lab$text[i])
    if (same) next
    hit <- text$text_id[ev_norm(text$text) == ev_norm(lab$text[i])]
    if (length(hit) == 1) {
      problems <- c(problems, sprintf("%s: label text_id %d re-keyed to %d by text", id, lab$text_id[i], hit))
      lab$text_id[i] <- hit
    } else {
      problems <- c(problems, sprintf("%s: label text_id %d does not match the paper text; row dropped", id, lab$text_id[i]))
      lab$text_id[i] <- NA
    }
  }
  lab <- lab[!is.na(lab$text_id), ]

  cand <- unique(vapply(mod$table, function(r) as.integer(r$text_id %or% NA), 1L))
  if (anyNA(cand)) problems <- c(problems, sprintf("%s: candidate rows without text_id are not scored", id))
  cand <- cand[!is.na(cand)]

  ff <- file.path(run_dir, "findings", paste0(module, ".json"))
  fs <- if (file.exists(ff)) mc_read_json(ff) else list()
  val <- if (length(fs)) validated(run_dir, module, allow_validate)
  invalid_ids <- character(0); n_bad_quotes <- if (is.null(val) && length(fs)) NA_integer_ else 0L
  for (v in val) if (!isTRUE(v$valid)) {
    invalid_ids <- c(invalid_ids, v$finding_id)
    n_bad_quotes <- n_bad_quotes + sum(grepl("quote not found|cannot be verified", unlist(v$errors)))
  }
  f <- data.frame(
    finding_id = vapply(fs, function(x) x$finding_id %or% NA_character_, ""),
    text_id = vapply(fs, function(x) item_text_id(x$item_id %or% NA_character_), 1L),
    verdict = vapply(fs, function(x) x$agent$verdict %or% NA_character_, ""),
    sweep = vapply(fs, function(x) isTRUE(x$sweep) || grepl(":sweep:", x$finding_id %or% "", fixed = TRUE), NA),
    complete = vapply(fs, function(x) identical(x$coverage$status, "complete"), NA))
  f <- f[!f$finding_id %in% invalid_ids & !is.na(f$text_id), ] # invalid findings count as unadjudicated

  units <- data.frame(paper_id = id, text_id = sort(unique(c(lab$text_id, cand, f$text_id))))
  units <- merge(units, lab[, c("text_id", "extraction_label", "contextual_label", "ambiguous", "synthetic", "category")], all.x = TRUE)
  units$is_candidate <- units$text_id %in% cand
  pick <- function(t) { v <- f$verdict[f$text_id == t]; if (!length(v)) "none" else v[order(match(v, VERDICT_PRIORITY))][1] }
  units$verdict <- vapply(units$text_id, pick, "")
  units$sweep <- units$text_id %in% f$text_id[f$sweep] & !units$is_candidate
  units$complete <- units$text_id %in% f$text_id[f$complete]
  list(units = units, problems = problems, n_findings = length(fs), n_invalid_findings = length(invalid_ids),
       n_invalid_quotes = n_bad_quotes, n_sentences = nrow(text))
}

prf <- function(expected, observed) {
  tp <- sum(expected & observed); fp <- sum(!expected & observed); fn <- sum(expected & !observed)
  p <- ev_div(tp, tp + fp); r <- ev_div(tp, tp + fn)
  list(tp = tp, fp = fp, fn = fn, precision = p, recall = r, f1 = if (is.na(p + r) || p + r == 0) NA_real_ else 2 * p * r / (p + r))
}

metrics <- function(u, papers) {
  l <- u[!is.na(u$contextual_label), ]                       # labelled units
  amb <- l$ambiguous | l$contextual_label == "ambiguous"
  dec <- l[!amb & l$contextual_label %in% c("confirmed_issue", "not_an_issue"), ]   # decidable
  una <- l[!amb & l$contextual_label == "unavailable_evidence", ]
  issue <- dec$contextual_label == "confirmed_issue"
  positive <- dec$verdict == "confirmed_issue"
  module_ctx <- prf(issue, dec$is_candidate)
  final <- prf(issue, positive)
  cand <- u[u$is_candidate, ]
  cand_dec <- dec[dec$is_candidate, ]
  by_cat <- lapply(split(dec, dec$category), function(d) list(n = nrow(d), n_issue = sum(d$contextual_label == "confirmed_issue"),
    n_candidate = sum(d$is_candidate), n_confirmed = sum(d$verdict == "confirmed_issue"), n_abstained = sum(d$is_candidate & d$verdict %in% ABSTAIN)))
  list(
    data = list(n_papers = length(papers), n_labelled = nrow(l), n_synthetic = sum(l$synthetic), n_candidates = nrow(cand),
      n_unlabelled_candidates = sum(is.na(cand$contextual_label)), n_confirmed_issue = sum(issue), n_not_an_issue = sum(!issue),
      n_ambiguous = sum(amb), n_unavailable_evidence = nrow(una)),
    extraction = c(prf(l$extraction_label == "candidate", l$is_candidate), list(note = "generator vs extraction labels")),
    module_contextual = c(module_ctx, list(note = "generator alone vs contextual labels: the baseline the agent has to beat, and the recall ceiling without a sweep")),
    final = c(final, list(
      precision_gain = final$precision - module_ctx$precision,
      recall_loss = module_ctx$recall - final$recall,
      abstention_rate = ev_div(sum(cand_dec$verdict %in% ABSTAIN), nrow(cand_dec)),
      abstention_rate_all_candidates = ev_div(sum(cand$verdict %in% ABSTAIN), nrow(cand)),
      coverage_rate = ev_div(sum(!cand$verdict %in% c("none", "not_checked")), nrow(cand)),
      coverage_complete_rate = ev_div(sum(cand$complete), nrow(cand)))),
    sweep = list(n = sum(u$sweep), n_true_issue = sum(dec$sweep & issue & positive), n_false = sum(dec$sweep & !issue & positive),
      n_unlabelled = sum(u$sweep & is.na(u$contextual_label))),
    ambiguous = list(n = sum(amb), verdicts = as.list(table(l$verdict[amb])), note = "reported only; excluded from precision/recall"),
    unavailable_evidence = list(n = nrow(una), n_abstained = sum(una$verdict %in% ABSTAIN), n_confirmed_issue = sum(una$verdict == "confirmed_issue"),
      note = "expected verdict is insufficient_evidence; excluded from precision/recall"),
    by_category = by_cat)
}

score_set <- function(set_dir, lab, module, allow_validate) {
  dirs <- ev_run_dirs(set_dir)
  papers <- unique(lab$paper_id)
  missing <- setdiff(papers, names(dirs))
  res <- lapply(setdiff(papers, missing), function(id) score_paper(dirs[[id]], lab[lab$paper_id == id, ], module, allow_validate))
  ok <- Filter(function(r) is.null(r$could_not_check), res)
  if (!length(ok)) mc_fail("No scorable papers in ", set_dir)
  u <- do.call(rbind, lapply(ok, `[[`, "units"))
  m <- metrics(u, unique(u$paper_id))
  q <- vapply(ok, function(r) r$n_invalid_quotes, 1L)
  m$quotes <- list(n_findings = sum(vapply(ok, `[[`, 1L, "n_findings")), n_invalid_findings = sum(vapply(ok, `[[`, 1L, "n_invalid_findings")),
    n_invalid_quotes = if (anyNA(q)) NA_integer_ else sum(q), source = if (anyNA(q)) "findings_validated.json missing and mc_validate.R not run" else "findings_validated.json")
  m$run_set <- normalizePath(set_dir)
  m$problems <- I(c(unlist(lapply(ok, `[[`, "problems")), unlist(lapply(res, `[[`, "could_not_check")),
    if (length(missing)) paste("no run dir for:", paste(missing, collapse = ", "))))
  list(metrics = m, units = u)
}

get_path <- function(x, path) { for (k in strsplit(path, ".", fixed = TRUE)[[1]]) x <- x[[k]]; if (is.null(x)) NA_real_ else x }

check_criteria <- function(crit, runs, agreement) {
  lapply(crit, function(cr) {
    v <- if (startsWith(cr$metric, "agreement.")) {
      if (length(runs) < (cr$min_runs %or% 2)) NA_real_ else get_path(list(agreement = agreement), cr$metric)
    } else {
      vals <- vapply(runs, function(r) as.numeric(get_path(r, cr$metric)), 1)
      if (anyNA(vals)) NA_real_ else if (cr$op == ">=") min(vals) else max(vals)
    }
    pass <- if (is.na(v)) NA else if (cr$op == ">=") v >= cr$value else v <= cr$value
    list(id = cr$id, metric = cr$metric, op = cr$op, threshold = cr$value, value = v,
         result = if (is.na(pass)) "NOT_EVALUATED" else if (pass) "PASS" else "FAIL")
  })
}

fmt <- function(x) if (is.null(x) || is.na(x)) "NA" else if (x == round(x)) format(x) else formatC(x, digits = 3, format = "f")

markdown <- function(res) {
  r1 <- res$runs[[1]]
  col <- function(path) paste(vapply(res$runs, function(r) fmt(get_path(r, path)), ""), collapse = " / ")
  rows <- c("extraction.precision", "extraction.recall", "module_contextual.precision", "module_contextual.recall", "final.precision",
            "final.recall", "final.f1", "final.recall_loss", "final.abstention_rate", "final.coverage_rate", "quotes.n_invalid_quotes",
            "sweep.n", "sweep.n_true_issue", "ambiguous.n", "unavailable_evidence.n", "unavailable_evidence.n_confirmed_issue")
  c(sprintf("# Evaluation: %s (%s)", res$module, res$split %or% "all labelled papers"), "",
    sprintf("Labels: `%s`; %d papers, %d labelled sentences (%d synthetic), %d true issues, %d candidates. Runs: %d.",
            basename(res$labels), r1$data$n_papers, r1$data$n_labelled, r1$data$n_synthetic, r1$data$n_confirmed_issue, r1$data$n_candidates, length(res$runs)), "",
    "| metric | value per run |", "|---|---|", sprintf("| %s | %s |", rows, vapply(rows, col, "")), "",
    if (!is.null(res$agreement)) c(sprintf("Repeated-run agreement over %d units: pairwise %s, mean pairwise Cohen kappa %s, Fleiss kappa %s (binary issue/other: pairwise %s, Fleiss %s).",
      res$agreement$n_units, fmt(res$agreement$pairwise_agreement), fmt(res$agreement$pairwise_cohen_kappa), fmt(res$agreement$fleiss_kappa),
      fmt(res$agreement$binary$pairwise_agreement), fmt(res$agreement$binary$fleiss_kappa)), ""),
    sprintf("## Acceptance criteria (%s)", res$criteria_status), "",
    "| criterion | metric | required | worst run | result |", "|---|---|---|---|---|",
    vapply(res$criteria, function(cr) sprintf("| %s | %s | %s %s | %s | %s |", cr$id, cr$metric, cr$op, fmt(cr$threshold), fmt(cr$value), cr$result), ""), "",
    sprintf("**Overall: %s.** %s", res$overall, res$eligibility),
    if (length(res$problems)) c("", "## Problems", "", paste("-", res$problems)))
}

mc_main({
  args <- mc_args(list(module = "marginal", criteria = file.path(here, "criteria.json"), splits = file.path(here, "splits.json"),
                       out = file.path(here, "results")))
  mc_require(args, c("labels", "runs"))
  split <- if (is.character(args$split)) args$split
  lab <- ev_labels(args$labels, args$module, split, args$splits)
  sets <- strsplit(args$runs, ",", fixed = TRUE)[[1]]
  scored <- lapply(sets, score_set, lab = lab, module = args$module, allow_validate = !isTRUE(args$no_validate))
  runs <- lapply(scored, `[[`, "metrics")

  # agreement: candidate + sweep units present in every run set
  agreement <- NULL
  if (length(scored) >= 2) {
    key <- function(u) paste(u$paper_id, u$text_id)
    us <- lapply(scored, function(s) s$units[s$units$is_candidate | s$units$verdict != "none", ])
    papers <- Reduce(intersect, lapply(scored, function(s) unique(s$units$paper_id)))
    keys <- sort(unique(unlist(lapply(us, function(u) key(u)[u$paper_id %in% papers]))))
    m <- vapply(us, function(u) { v <- u$verdict[match(keys, key(u))]; ifelse(is.na(v), "none", v) }, character(length(keys)))
    m <- matrix(m, nrow = length(keys))
    agreement <- ev_agreement(m)
    agreement$binary <- ev_agreement(matrix(ifelse(m == "confirmed_issue", "issue", "other"), nrow = nrow(m)))[c("pairwise_agreement", "fleiss_kappa")]
    agreement$n_runs <- ncol(m)
    agreement$disagreements <- lapply(which(apply(m, 1, function(r) length(unique(r)) > 1)), function(i) list(unit = keys[i], verdicts = m[i, ]))
  }

  crit_all <- mc_read_json(args$criteria)
  crit <- crit_all$modules[[args$module]]$criteria
  if (is.null(crit)) mc_fail("No criteria for module ", args$module, " in ", args$criteria)
  checks <- check_criteria(crit, runs, agreement)
  results <- vapply(checks, `[[`, "", "result")
  synthetic <- runs[[1]]$data$n_synthetic > 0
  eligible <- identical(split, "heldout") && !synthetic
  res <- list(status = "ok", module = args$module, split = split, labels = normalizePath(args$labels), scored_at = mc_now(),
    criteria_file = normalizePath(args$criteria), criteria_version = crit_all$version, criteria_status = crit_all$status,
    runs = runs, agreement = agreement, criteria = checks,
    overall = if (all(results == "PASS")) "PASS" else if (any(results == "FAIL")) "FAIL" else "INCOMPLETE",
    eligible_for_acceptance = eligible,
    eligibility = if (eligible) "Held-out, non-synthetic: this result counts towards leaving shadow mode."
      else paste0("Does NOT count towards leaving shadow mode (", paste(c(if (!identical(split, "heldout")) "not the held-out split", if (synthetic) "synthetic fixtures included"), collapse = "; "), ")."),
    problems = I(unique(unlist(lapply(runs, `[[`, "problems")))))

  stem <- file.path(args$out, paste0("eval_", args$module, "_", split %or% "all"))
  mc_write_json(res, paste0(stem, ".json"))
  md <- markdown(res)
  dir.create(args$out, recursive = TRUE, showWarnings = FALSE)
  writeLines(md, paste0(stem, ".md"))
  mc_log(paste(md, collapse = "\n"))
  res$output <- list(json = paste0(stem, ".json"), markdown = paste0(stem, ".md"))
  mc_out(res)
  if (isTRUE(args$strict) && res$overall != "PASS") quit(status = 3, save = "no")
})
