#!/usr/bin/env Rscript
# Render deterministic module results + validated agent findings to one self-contained HTML file.
#   Rscript scripts/mc_report.R --run-dir <dir> [--out report.html]
# Re-runs validation first (writes findings_validated.json); only valid findings are shown.
# Pilot = shadow mode: original lights are shown untouched next to the shadow light.

mc_skill_dir <- dirname(dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))))
source(file.path(mc_skill_dir, "scripts", "_common.R"))
source(file.path(mc_skill_dir, "scripts", "_validate.R"))

esc <- function(x) htmltools::htmlEscape(paste(as.character(x %||% ""), collapse = " "))
tag <- function(x) sprintf("<span class=\"tag\">%s</span>", esc(x))
light <- function(x) {
  x <- if (is.null(x) || is.na(x)) "na" else x
  lab <- c(red = "red", yellow = "yellow", green = "green", info = "info", na = "n/a", fail = "could not check")
  sprintf("<span class=\"light %s\">%s</span>", if (x %in% names(lab)) x else "na", esc(lab[x] %||% x))
}
anchor <- function(x) gsub("[^A-Za-z0-9_-]", "-", x)

# package report strings are quarto markdown: drop executable chunks (the table is rendered
# from the saved module table instead), turn callouts into <details>, then commonmark
md <- function(x) {
  x <- paste(unlist(x), collapse = "\n\n")
  if (!nzchar(trimws(x))) return("")
  x <- gsub("(?s)```\\{r[^}]*\\}.*?```", "", x, perl = TRUE)
  x <- gsub("(?m)^:::+ *\\{[^}]*title=\"([^\"]*)\"[^}]*\\} *$", "<details><summary>\\1</summary>\n", x, perl = TRUE)
  x <- gsub("(?m)^:::+ *\\{[^}]*\\} *$", "<details><summary>More</summary>\n", x, perl = TRUE)
  x <- gsub("(?m)^:::+ *$", "\n</details>\n", x, perl = TRUE)
  commonmark::markdown_html(x, extensions = TRUE)
}

html_table <- function(rows, max_rows = 200) {
  if (!length(rows)) return("")
  if (!is.null(names(rows))) rows <- list(rows)
  cols <- unique(unlist(lapply(rows, names)))
  cell <- function(v) if (is.null(v) || (length(v) == 1 && is.na(v))) "" else if (is.list(v)) as.character(jsonlite::toJSON(v, auto_unbox = TRUE)) else paste(v, collapse = ", ")
  vals <- lapply(cols, function(k) vapply(rows, function(r) cell(r[[k]]), ""))
  keep <- vapply(vals, function(v) any(nzchar(v)), logical(1)) & !cols %in% c("candidate_id", "paper_id", "formatted")
  cols <- cols[keep]; vals <- vals[keep]
  n <- min(length(rows), max_rows)
  body <- vapply(seq_len(n), function(i) paste0("<tr>", paste0("<td>", vapply(vals, function(v) esc(v[i]), ""), "</td>", collapse = ""), "</tr>"), "")
  paste0("<div class=\"tablewrap\"><table><thead><tr>", paste0("<th>", esc_each(cols), "</th>", collapse = ""), "</tr></thead><tbody>",
         paste(body, collapse = "\n"), "</tbody></table></div>",
         if (length(rows) > n) sprintf("<p class=\"muted\">Showing %d of %d rows; see modules/*.json for all.</p>", n, length(rows)) else "")
}
esc_each <- function(x) vapply(x, esc, "", USE.NAMES = FALSE)

# where a quote sits: section header from the paper (tool-derived) plus the agent's own note
where <- function(e, ctx) {
  sid <- e$source_id
  if (sid %in% c("paper", ctx$paper_id)) {
    tid <- mc_text_id(e$text_id)
    i <- match(tid, ctx$paper$text$text_id)
    hdr <- ctx$paper$section$header[match(ctx$paper$text$section_id[i], ctx$paper$section$section_id)]
    loc <- paste0("Paper, ", if (!is.na(hdr %||% NA) && !grepl("^\\[div", hdr)) paste0(hdr, ", "), "sentence t", tid)
  } else {
    s <- ctx$sources[[sid]]
    loc <- paste0(sid, if (!is.null(s$uri)) paste0(" (", s$uri, ")"))
  }
  paste0(esc(loc), if (nzchar(e$location %||% "")) paste0(" &middot; ", esc(e$location)))
}

finding_html <- function(f, ctx) {
  ev <- vapply(f$agent$evidence %||% list(), function(e)
    sprintf("<blockquote>&ldquo;%s&rdquo;<cite>%s</cite></blockquote>", esc(e$quote), where(e, ctx)), "")
  se <- vapply(f$coverage$searches %||% list(), function(s)
    sprintf("<li>searched <code>%s</code> in %s: %s hit(s)</li>", esc(s$query), esc(s$scope), esc(s$n_hits)), "")
  lim <- unlist(f$coverage$limitations)
  paste0(
    "<div class=\"finding\" id=\"", anchor(f$finding_id), "\">",
    "<div>", tag(gsub("_", " ", f$agent$verdict)),
    if (f$severity != "none") tag(paste("severity:", f$severity)), tag(paste("confidence:", f$agent$confidence)),
    if (f$coverage$status != "complete") tag(paste("coverage:", gsub("_", " ", f$coverage$status))),
    if (isTRUE(f$sweep)) tag("recall sweep"), if (!is.null(f$study_id)) tag(paste("study:", f$study_id)),
    " <span class=\"id\">", esc(f$finding_id), "</span></div>",
    "<p>", esc(f$agent$rationale), "</p>",
    paste(ev, collapse = ""),
    if (length(se)) paste0("<ul class=\"small\">", paste(se, collapse = ""), "</ul>"),
    if (length(lim)) paste0("<p class=\"small muted\">Limits: ", esc(paste(lim, collapse = "; ")), "</p>"),
    if (nzchar(f$advice %||% "")) paste0("<p><strong>Worth checking:</strong> ", esc(f$advice), "</p>"),
    if (nzchar(f$suggested_rewording %||% "")) paste0("<p class=\"small\"><strong>Possible rewording:</strong> ", esc(f$suggested_rewording), "</p>"),
    "</div>")
}

module_html <- function(mod, s, fs, ctx) {
  ok <- identical(mod$status, "ok")
  is_issue <- vapply(fs, function(f) f$agent$verdict != "not_an_issue", logical(1))
  shown <- fs[is_issue]; cleared <- fs[!is_issue]
  unadj <- unlist(s$unadjudicated)
  original <- if (ok) paste0(light(mod$traffic_light), " ", esc(mod$summary_text))
              else paste0(light("fail"), " Could not check (", esc(gsub("_", " ", mod$status)), ")",
                          if (!is.null(mod$error)) paste0(": ", esc(mod$error)), ". This is not a clean result.")
  counts <- unlist(s$verdicts); counts <- counts[counts > 0]
  shadow <- paste0(light(s$shadow_light), " ",
    if (!ok) "Nothing could be adjudicated."
    else if (!length(fs) && !length(unadj)) "No candidates to adjudicate; the original light stands."
    else paste0(paste(sprintf("%d %s", counts, gsub("_", " ", names(counts))), collapse = ", "),
                if (length(unadj)) sprintf("%s%d unadjudicated", if (length(counts)) ", " else "", length(unadj))),
    sprintf(" <span class=\"muted small\">(rule: %s)</span>", esc(s$shadow_rule)))
  report <- md(mod$report); tab <- html_table(mod$table)
  paste0(
    "<section class=\"module", if (!ok) " failed", "\" id=\"m-", anchor(mod$module), "\">",
    "<h3>", esc(mod$title %||% mod$module), " <span class=\"muted small\">", esc(mod$module), "</span></h3>",
    "<div class=\"cols\"><div class=\"col\"><span class=\"label\">Original module result</span>", original, "</div>",
    "<div class=\"col\"><span class=\"label\">Shadow light (adjudicated, pilot)</span>", shadow, "</div></div>",
    paste(vapply(shown, finding_html, "", ctx = ctx), collapse = ""),
    if (length(unadj)) paste0("<p class=\"small muted\">Not adjudicated (treated as not checked): ", esc(paste(sub("^.*:", "", unadj), collapse = ", ")), "</p>"),
    if (length(cleared)) paste0("<details><summary>", length(cleared), " flagged item(s) judged not an issue</summary>",
                                paste(vapply(cleared, finding_html, "", ctx = ctx), collapse = ""), "</details>"),
    if (nzchar(report) || nzchar(tab)) paste0("<details><summary>Original module report and table</summary>", report, tab, "</details>"),
    "</section>")
}

mc_main({
  args <- mc_require(mc_args(), "run_dir")
  run_dir <- normalizePath(args$run_dir, mustWork = TRUE)
  out_path <- if (is.character(args$out)) args$out else file.path(run_dir, "report.html")

  v <- mc_validate_run(run_dir)
  mc_write_json(v$out, file.path(run_dir, "findings_validated.json"))
  ctx <- v$ctx; res <- v$out
  man <- mc_manifest_update(run_dir, list(light_mapping_version = res$mapping_version, validated_at = res$validated_at, reported_at = mc_now()))
  sums <- stats::setNames(res$modules, vapply(res$modules, function(s) s$module, ""))
  all_f <- unlist(unname(v$valid_findings), recursive = FALSE) %||% list()

  # ---- priority list: confirmed issues by severity, then confidence ----
  conf <- Filter(function(f) f$agent$verdict == "confirmed_issue", all_f)
  lv <- c("high", "medium", "low", "none")
  conf <- conf[order(match(vapply(conf, function(f) f$severity, ""), lv), match(vapply(conf, function(f) f$agent$confidence, ""), lv),
                     vapply(conf, function(f) f$finding_id, ""))]
  priority <- if (!length(all_f)) {
    "<p>No validated agent findings in this run. The sections below show the deterministic module output only; flagged items have not been checked in context.</p>"
  } else if (!length(conf)) {
    "<p>No issues were confirmed among the adjudicated items. See the coverage limits before reading this as a clean result.</p>"
  } else paste0("<ol class=\"priority\">", paste(vapply(conf, function(f) {
    title <- ctx$modules[[f$module]]$title %||% f$module
    q <- if (length(f$agent$evidence)) f$agent$evidence[[1]]$quote
    sprintf("<li><a href=\"#%s\"><strong>%s</strong></a> %s%s<br>%s%s</li>", anchor(f$finding_id), esc(title),
            tag(paste("severity:", f$severity)), tag(paste("confidence:", f$agent$confidence)),
            esc(if (nzchar(f$advice %||% "")) f$advice else f$agent$rationale),
            if (!is.null(q)) sprintf("<br><span class=\"muted small\">&ldquo;%s&rdquo;</span>", esc(if (nchar(q) > 160) paste0(substr(q, 1, 160), "...") else q)) else "")
  }, ""), collapse = ""), "</ol>")

  # ---- coverage limits ----
  li <- character(0)
  for (m in ctx$modules) if (!identical(m$status, "ok"))
    li <- c(li, sprintf("<strong>%s</strong> could not be checked (%s)%s.", esc(m$title %||% m$module), esc(gsub("_", " ", m$status)),
                        if (!is.null(m$error)) paste0(": ", esc(m$error)) else ""))
  for (f in all_f) if (f$coverage$status != "complete")
    li <- c(li, sprintf("<a href=\"#%s\">%s</a>: coverage %s%s.", anchor(f$finding_id), esc(sub(paste0("^", ctx$paper_id, ":"), "", f$finding_id)),
                        esc(gsub("_", " ", f$coverage$status)), if (length(f$coverage$limitations)) paste0(" &ndash; ", esc(paste(unlist(f$coverage$limitations), collapse = "; "))) else ""))
  for (s in res$modules) {
    if (length(s$unadjudicated)) li <- c(li, sprintf("<strong>%s</strong>: %d flagged item(s) not adjudicated (%s).", esc(s$module), length(s$unadjudicated),
                                                       esc(paste(sub("^.*:", "", unlist(s$unadjudicated)), collapse = ", "))))
    if (!is.null(s$file_error)) li <- c(li, sprintf("<strong>%s</strong>: findings file unreadable (%s).", esc(s$module), esc(s$file_error)))
  }
  for (r in res$findings) if (!isTRUE(r$valid))
    li <- c(li, sprintf("Finding <code>%s</code> failed validation and is excluded: %s", esc(r$finding_id %||% "(no id)"), esc(paste(unlist(r$errors), collapse = "; "))))
  for (s in ctx$sources) if (!is.null(s$status) && !s$status %in% c("ok", "fetched", "cached"))
    li <- c(li, sprintf("Source <code>%s</code> (%s): %s.", esc(s$source_id), esc(s$uri), esc(s$status)))
  for (k in intersect(c("budget_limits", "coverage_limits"), names(man))) for (b in man[[k]])
    li <- c(li, esc(if (is.list(b)) jsonlite::toJSON(b, auto_unbox = TRUE) else b))
  imp <- file.path(run_dir, "import_summary.json")
  if (file.exists(imp)) for (w in unlist(mc_read_json(imp)[c("warnings", "parse_warnings")])) li <- c(li, paste0("Import: ", esc(w)))
  limits <- paste0("<div class=\"limits\">", if (length(li)) paste0("<ul>", paste0("<li>", li, "</li>", collapse = ""), "</ul>")
                   else "<p>No failed modules, partial checks or unadjudicated items were recorded. Checks that were never run are not listed here.</p>", "</div>")

  # ---- modules, in paper-section order ----
  sec <- c("general", "intro", "method", "results", "discussion", "reference")
  mods <- ctx$modules[order(match(vapply(ctx$modules, function(m) m$section %||% "", ""), sec, nomatch = 99), names(ctx$modules))]
  modules <- paste(vapply(mods, function(m) module_html(m, sums[[m$module]], v$valid_findings[[m$module]] %||% list(), ctx), ""), collapse = "\n")
  if (!length(mods)) modules <- "<p>No module results in this run directory.</p>"

  # ---- provenance ----
  kv <- function(x) if (is.list(x)) paste(sprintf("%s: %s", names(x), vapply(x, function(v) paste(unlist(v), collapse = ", "), "")), collapse = "; ") else paste(x, collapse = ", ")
  prov <- list(
    "Run" = man$run_id, "Paper" = paste0(man$paper_id %||% ctx$paper_id, if (!is.null(man$source$path)) paste0(" (", basename(man$source$path), ", sha256 ", man$source$sha256, ")")),
    "metacheck" = paste0(man$metacheck$version, if (!is.null(man$metacheck$commit)) paste0(" @ ", man$metacheck$commit)), "R" = man$r_version,
    "Model" = if (is.null(man$model_id)) "not recorded (no agent layer, or the agent did not write model_id)" else paste0(man$model_id, if (!is.null(man$settings)) paste0(" (", kv(man$settings), ")")),
    "Schema versions" = kv(man$schema_versions), "Light mapping" = paste0("version ", res$mapping_version),
    "Rubric versions" = if (!is.null(man$rubric_versions)) kv(man$rubric_versions),
    "Database dates" = if (length(man$databases)) kv(man$databases),
    "External sources" = if (length(ctx$sources)) paste(vapply(ctx$sources, function(s) sprintf("%s [%s, %s, sha256 %s]", s$source_id, s$uri %||% "", s$retrieved_at %||% "", s$sha256 %||% "none"), ""), collapse = " | "),
    "Validated / rendered" = paste(res$validated_at, "/", man$reported_at))
  prov <- Filter(Negate(is.null), prov)
  footer <- paste0("<footer><dl>", paste(sprintf("<dt>%s</dt><dd>%s</dd>", esc_each(names(prov)), esc_each(prov)), collapse = ""), "</dl></footer>")

  title <- ctx$paper$info$title %||% ctx$paper_id
  body <- paste0(
    "<h1>metacheck report</h1><p class=\"muted\">", esc(title), " &middot; <code>", esc(ctx$paper_id), "</code></p>",
    "<div class=\"banner\">These are automated prompts for the authors to check, not verdicts on the paper. Original module lights are shown unchanged; ",
    "the shadow light is a pilot, derived by fixed rules from the adjudicated findings. Quotes were verified to exist in the cited source; ",
    "whether they support the judgement was not machine-checked.</div>",
    "<h2>Worth checking first</h2>", priority,
    "<h2>Coverage limits</h2>", limits,
    "<h2>Checks</h2>", modules, footer)
  tpl <- paste(readLines(file.path(mc_skill_dir, "assets", "report_template.html"), warn = FALSE), collapse = "\n")
  html <- sub("{{body}}", body, sub("{{title}}", esc(paste("metacheck report:", ctx$paper_id)), tpl, fixed = TRUE), fixed = TRUE)
  dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)
  writeLines(html, out_path, useBytes = TRUE)

  mc_out(list(status = "ok", report = normalizePath(out_path), n_modules = length(mods), n_findings_shown = length(all_f),
              n_confirmed = length(conf), n_invalid_excluded = res$n_invalid, n_coverage_limits = length(li),
              lights = lapply(res$modules, function(s) s[c("module", "original_light", "shadow_light")])))
})
