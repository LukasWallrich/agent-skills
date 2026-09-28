# Shared by mc_validate.R and mc_report.R: schema checks, quote verification, shadow lights.
# Needs _common.R sourced first and mc_skill_dir set to the skill root.

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

# ---- minimal JSON Schema interpreter (the subset used by schemas/*.json) ----
# used when `jsonvalidate` is not installed, so the schema file stays the single source

mc_json_type <- function(x) {
  if (is.null(x)) return("null")
  if (is.list(x)) return(if (length(x) && !is.null(names(x))) "object" else if (length(x)) "array" else c("array", "object"))
  if (is.logical(x)) return("boolean")
  if (is.character(x)) return("string")
  if (is.numeric(x)) return(if (x == round(x)) c("integer", "number") else "number")
  "unknown"
}

mc_schema_check <- function(x, schema, root = schema, path = "$") {
  err <- character(0)
  if (!is.null(schema[["$ref"]])) {
    ref <- root
    for (k in strsplit(sub("^#/", "", schema[["$ref"]]), "/")[[1]]) ref <- ref[[k]]
    return(mc_schema_check(x, ref, root, path))
  }
  ty <- mc_json_type(x)
  if (!is.null(schema$type) && !any(ty %in% unlist(schema$type)))
    return(sprintf("%s: expected %s, got %s", path, paste(unlist(schema$type), collapse = "|"), ty[1]))
  if ("const" %in% names(schema) && !identical(x, schema$const))
    err <- c(err, sprintf("%s: must be %s", path, jsonlite::toJSON(schema$const, auto_unbox = TRUE)))
  if (!is.null(schema$enum)) {
    ok <- any(vapply(schema$enum, function(e) identical(e, x), logical(1)))
    if (!ok) err <- c(err, sprintf("%s: must be one of %s", path, paste(unlist(schema$enum), collapse = ", ")))
  }
  if ("string" %in% ty) {
    if (!is.null(schema$minLength) && nchar(x) < schema$minLength) err <- c(err, sprintf("%s: must not be empty", path))
    if (!is.null(schema$pattern) && !grepl(schema$pattern, x, perl = TRUE))
      err <- c(err, sprintf("%s: '%s' does not match %s", path, x, schema$pattern))
  }
  if ("number" %in% ty && !is.null(schema$minimum) && x < schema$minimum) err <- c(err, sprintf("%s: must be >= %s", path, schema$minimum))
  if ("object" %in% ty) {
    err <- c(err, sprintf("%s: missing required field '%s'", path, setdiff(unlist(schema$required), names(x))))
    if (isFALSE(schema$additionalProperties))
      err <- c(err, sprintf("%s: unknown field '%s'", path, setdiff(names(x), names(schema$properties))))
    for (k in intersect(names(x), names(schema$properties)))
      err <- c(err, mc_schema_check(x[[k]], schema$properties[[k]], root, paste0(path, ".", k)))
  }
  if ("array" %in% ty) {
    if (!is.null(schema$minItems) && length(x) < schema$minItems) err <- c(err, sprintf("%s: needs at least %d item(s)", path, schema$minItems))
    if (!is.null(schema$items)) for (i in seq_along(x))
      err <- c(err, mc_schema_check(x[[i]], schema$items, root, sprintf("%s[%d]", path, i - 1)))
  }
  for (s in schema$allOf) err <- c(err, mc_schema_check(x, s, root, path))
  if (!is.null(schema$anyOf) && !any(vapply(schema$anyOf, function(s) !length(mc_schema_check(x, s, root, path)), logical(1))))
    err <- c(err, sprintf("%s: matches none of the allowed forms", path))
  if (!is.null(schema[["if"]]) && !length(mc_schema_check(x, schema[["if"]], root, path)) && !is.null(schema$then))
    err <- c(err, mc_schema_check(x, schema$then, root, path))
  err
}

# errors for one parsed JSON value against a schema file
mc_schema_errors <- function(x, schema_path) {
  if (requireNamespace("jsonvalidate", quietly = TRUE)) {
    json <- jsonlite::toJSON(x, auto_unbox = TRUE, null = "null", digits = NA)
    res <- tryCatch(jsonvalidate::json_validate(json, schema_path, engine = "ajv", verbose = TRUE), error = function(e) NULL)
    if (!is.null(res)) {
      if (isTRUE(res)) return(character(0))
      e <- attr(res, "errors")
      return(paste0("$", e$instancePath, ": ", e$message))
    }
  }
  mc_schema_check(x, mc_read_json(schema_path))
}

# ---- quote verification ----

# whitespace / unicode-quote / dash normalisation; nothing else is forgiven
mc_norm <- function(x) {
  x <- enc2utf8(as.character(x))
  x <- gsub("[‘’‚‛′`´]", "'", x)
  x <- gsub("[“”„‟″«»]", "\"", x)
  x <- gsub("[‐‑‒–—−]", "-", x)
  x <- gsub("­", "", x)
  trimws(gsub("[\\s   ]+", " ", x, perl = TRUE))
}

mc_text_id <- function(x) suppressWarnings(as.integer(sub("^t", "", as.character(x %||% NA))))

# quote must overlap the cited sentence; it may run into neighbouring sentences of the same paragraph
mc_quote_in_paper <- function(quote, text_id, text) {
  i <- match(text_id, text$text_id)
  if (is.na(i)) return(sprintf("text_id %s does not exist in the paper", text_id))
  q <- mc_norm(quote)
  if (grepl(q, mc_norm(text$text[i]), fixed = TRUE)) return(NULL)
  same <- which(text$section_id %in% text$section_id[i] & text$paragraph_id %in% text$paragraph_id[i])
  same <- same[order(text$text_id[same])]
  sent <- mc_norm(text$text[same])
  start <- cumsum(c(1, nchar(sent) + 1))[seq_along(sent)]
  hit <- regexpr(q, paste(sent, collapse = " "), fixed = TRUE)
  k <- match(i, same)
  if (hit > 0 && hit <= start[k] + nchar(sent[k]) - 1 && hit + nchar(q) - 1 >= start[k]) return(NULL)
  sprintf("quote not found verbatim in text_id %s (or spanning its neighbours in the same paragraph): \"%s\"", text_id, substr(quote, 1, 80))
}

mc_quote_in_file <- function(quote, path) {
  if (!file.exists(path)) return(sprintf("source file missing: %s", path))
  txt <- tryCatch(paste(readLines(path, warn = FALSE, encoding = "UTF-8"), collapse = "\n"), error = function(e) NULL)
  if (is.null(txt) || !validUTF8(txt)) return(sprintf("source file is not readable as text, quote cannot be verified: %s", basename(path)))
  if (grepl("\\.json$", path, ignore.case = TRUE)) { # quotes refer to decoded string values, not JSON escapes
    vals <- tryCatch(unlist(jsonlite::fromJSON(txt, simplifyVector = FALSE)), error = function(e) NULL)
    if (!is.null(vals)) txt <- paste(vals, collapse = "\n")
  }
  if (grepl(mc_norm(quote), mc_norm(txt), fixed = TRUE)) return(NULL)
  sprintf("quote not found verbatim in %s: \"%s\"", basename(path), substr(quote, 1, 80))
}

# ---- run-dir context ----

mc_run_context <- function(run_dir) {
  paper <- mc_paper(run_dir)
  idx_path <- file.path(run_dir, "sources", "index.json")
  idx <- if (file.exists(idx_path)) mc_read_json(idx_path) else list()
  if (!is.null(names(idx)) && !is.null(idx$sources)) idx <- idx$sources
  # mc_fetch.R keeps its budget as a pseudo-entry; it is not a source
  idx <- Filter(function(s) !identical(s$source_id, "_budget"), idx)
  names(idx) <- vapply(idx, function(s) as.character(s$source_id %||% ""), "")
  mods <- list()
  for (f in list.files(file.path(run_dir, "modules"), "\\.json$", full.names = TRUE))
    mods[[sub("\\.json$", "", basename(f))]] <- mc_read_json(f)
  list(run_dir = run_dir, paper = paper, paper_id = paper$paper_id, sources = idx, modules = mods)
}

mc_source_path <- function(ctx, source_id) {
  p <- ctx$sources[[source_id]]$path
  if (is.null(p)) return(NULL)
  cand <- c(p, file.path(ctx$run_dir, p), file.path(ctx$run_dir, "sources", p))
  (cand[file.exists(cand)])[1] %||% cand[2]
}

mc_source_ok <- function(ctx, id) {
  if (!is.character(id) || length(id) != 1) return(FALSE)
  if (id %in% c("paper", ctx$paper_id, names(ctx$sources))) return(TRUE)
  startsWith(id, "module:") && sub("^module:", "", id) %in% names(ctx$modules)
}

mc_candidates <- function(mod) vapply(mod$table %||% list(), function(r) as.character(r$candidate_id %||% NA), "")

# ---- one finding ----

mc_check_finding <- function(f, module, ctx, schema_path) {
  if (!is.list(f) || is.null(names(f))) return("finding is not a JSON object")
  err <- mc_schema_errors(f, schema_path)
  chr <- function(x) if (is.character(x) && length(x) == 1) x else ""
  fid <- chr(f$finding_id); item <- chr(f$item_id)
  if (!identical(f$module, module)) err <- c(err, sprintf("module '%s' does not match file findings/%s.json", chr(f$module), module))
  mod <- ctx$modules[[module]]
  if (is.null(mod)) err <- c(err, sprintf("no modules/%s.json: the module was not run", module))
  prefix <- paste0(ctx$paper_id, ":", module, ":")
  sweep <- isTRUE(f$sweep) || startsWith(fid, paste0(prefix, "sweep:"))
  if (sweep) {
    if (!isTRUE(f$sweep)) err <- c(err, "sweep finding_id needs \"sweep\": true")
    if (fid != paste0(prefix, "sweep:", item)) err <- c(err, sprintf("sweep finding_id must be %ssweep:%s", prefix, item))
    if (grepl("^t[0-9]+", item) && !mc_text_id(sub("\\..*$", "", item)) %in% ctx$paper$text$text_id)
      err <- c(err, sprintf("sweep item_id %s: no such text_id in the paper", item))
    if (grepl("^b[0-9]+", item) && !sub("^b([0-9]+).*", "\\1", item) %in% as.character(ctx$paper$bib$bib_id))
      err <- c(err, sprintf("sweep item_id %s: no such bib_id in the paper", item))
  } else if (!is.null(mod)) {
    cid <- sub(":c[0-9]+$", "", fid)
    if (!cid %in% mc_candidates(mod)) err <- c(err, sprintf("candidate_id '%s' is not in modules/%s.json", cid, module))
    else if (cid != paste0(prefix, item)) err <- c(err, sprintf("item_id '%s' does not match finding_id", item))
  }
  ids <- c(f$deterministic$source_id, unlist(f$coverage$checked_source_ids), vapply(f$agent$evidence %||% list(), function(e) chr(e$source_id), ""))
  for (id in unique(ids)) if (!mc_source_ok(ctx, id)) err <- c(err, sprintf("source_id '%s' is not 'paper', 'module:<m>' or listed in sources/index.json", id))

  n_verified <- 0
  for (e in f$agent$evidence %||% list()) {
    sid <- chr(e$source_id); q <- chr(e$quote)
    if (!nzchar(q) || !mc_source_ok(ctx, sid)) next
    bad <- if (sid %in% c("paper", ctx$paper_id)) {
      if (is.na(mc_text_id(e$text_id))) "paper evidence needs a text_id" else mc_quote_in_paper(q, mc_text_id(e$text_id), ctx$paper$text)
    } else if (startsWith(sid, "module:")) {
      mc_quote_in_file(q, file.path(ctx$run_dir, "modules", paste0(sub("^module:", "", sid), ".json")))
    } else {
      mc_quote_in_file(q, mc_source_path(ctx, sid) %||% "")
    }
    if (is.null(bad)) n_verified <- n_verified + 1 else err <- c(err, bad)
  }
  if (identical(f$agent$verdict, "confirmed_issue") && n_verified == 0 && !length(f$coverage$searches))
    err <- c(err, "confirmed_issue needs at least one verified quote, or coverage.searches for an absence claim")
  if (identical(f$agent$verdict, "confirmed_issue") && identical(f$severity, "none"))
    err <- c(err, "confirmed_issue needs a severity of high, medium or low")
  unique(err)
}

# ---- lights ----

mc_light_mapping <- function() mc_read_json(file.path(mc_skill_dir, "assets", "light_mapping.json"))

# rows: data.frame(verdict, severity, coverage) for valid findings + unadjudicated candidates
mc_shadow_light <- function(status, original, rows, mapping = mc_light_mapping()) {
  matches <- function(ms) {
    hit <- rep(FALSE, nrow(rows))
    for (m in ms) {
      h <- rep(TRUE, nrow(rows))
      for (k in names(m)) h <- h & rows[[k]] %in% unlist(m[[k]])
      hit <- hit | h
    }
    hit
  }
  holds <- function(w) {
    ok <- TRUE
    if (!is.null(w$module_status_not)) ok <- ok && !identical(status, w$module_status_not)
    if (!is.null(w$n_findings)) ok <- ok && nrow(rows) == w$n_findings
    if (!is.null(w$any)) ok <- ok && any(matches(w$any))
    if (!is.null(w$all)) ok <- ok && nrow(rows) > 0 && all(matches(w$all))
    ok
  }
  rule <- mapping$default
  for (r in mapping$rules) if (holds(r$when)) { rule <- r; break }
  light <- rule$light
  if (is.list(light)) light <- if (is.null(original) || is.na(original) || original %in% unlist(light$never)) light$fallback else original
  if (!light %in% unlist(mapping$lights)) light <- mapping$default$light
  list(light = light, rule = rule$id)
}

# ---- whole run ----

mc_validate_run <- function(run_dir, module = NULL) {
  ctx <- mc_run_context(run_dir)
  schema_path <- file.path(mc_skill_dir, "schemas", "finding.schema.json")
  mapping <- mc_light_mapping()
  files <- list.files(file.path(run_dir, "findings"), "\\.json$", full.names = TRUE)
  fmods <- sub("\\.json$", "", basename(files))
  mods <- union(names(ctx$modules), fmods)
  if (!is.null(module)) mods <- intersect(mods, module)

  findings <- list(); summaries <- list(); kept <- list()
  for (m in mods) {
    fs <- list(); file_error <- NULL
    if (m %in% fmods) {
      fs <- tryCatch(mc_read_json(files[match(m, fmods)]), error = function(e) { file_error <<- conditionMessage(e); list() })
      if (length(fs) && !is.null(names(fs))) { file_error <- "file must be a JSON array of findings"; fs <- list() }
    }
    ids <- vapply(fs, function(f) if (is.character(f$finding_id)) f$finding_id[1] else NA_character_, "")
    res <- lapply(seq_along(fs), function(i) {
      err <- mc_check_finding(fs[[i]], m, ctx, schema_path)
      if (!is.na(ids[i]) && sum(ids == ids[i], na.rm = TRUE) > 1) err <- c(err, "duplicate finding_id")
      list(finding_id = ids[i], module = m, valid = !length(err), errors = as.list(err))
    })
    valid <- vapply(res, function(r) r$valid, logical(1))
    ok <- fs[valid]
    mod <- ctx$modules[[m]]
    cands <- if (is.null(mod)) character(0) else mc_candidates(mod)
    covered <- sub(":c[0-9]+$", "", vapply(ok, function(f) f$finding_id, ""))
    unadj <- setdiff(cands, covered)
    # base extraction tables: rows are inputs to other checks, not flags to adjudicate
    if (m %in% unlist(mapping$adjudication_exempt)) unadj <- character(0)
    u <- mapping$unadjudicated
    rows <- data.frame(
      verdict = c(vapply(ok, function(f) f$agent$verdict, ""), rep(u$verdict, length(unadj))),
      severity = c(vapply(ok, function(f) f$severity, ""), rep(u$severity, length(unadj))),
      coverage = c(vapply(ok, function(f) f$coverage$status, ""), rep(u$coverage, length(unadj))))
    status <- mod$status %||% "not_run"
    original <- mod$traffic_light %||% NA_character_
    sl <- mc_shadow_light(status, original, rows, mapping)
    verdicts <- c("confirmed_issue", "not_an_issue", "insufficient_evidence", "not_checked", "not_applicable")
    summaries[[m]] <- list(
      module = m, status = status, n_candidates = length(cands), n_findings = length(fs),
      n_valid = sum(valid), n_invalid = sum(!valid), file_error = file_error,
      verdicts = as.list(table(factor(vapply(ok, function(f) f$agent$verdict, ""), verdicts))),
      unadjudicated = as.list(unadj), original_light = original,
      shadow_light = sl$light, shadow_rule = sl$rule, mapping_version = mapping$mapping_version)
    findings <- c(findings, res)
    kept[[m]] <- ok
  }
  n_invalid <- sum(!vapply(findings, function(r) r$valid, logical(1)))
  n_file_err <- sum(vapply(summaries, function(s) !is.null(s$file_error), logical(1)))
  out <- list(
    status = if (n_invalid + n_file_err == 0) "ok" else "invalid",
    run_dir = run_dir, validated_at = mc_now(),
    validator = if (requireNamespace("jsonvalidate", quietly = TRUE)) "jsonvalidate" else "builtin",
    schema_version = "1", mapping_version = mapping$mapping_version,
    n_findings = length(findings), n_invalid = n_invalid,
    n_unadjudicated = sum(vapply(summaries, function(s) length(s$unadjudicated), 1L)),
    note = paste("Validation checks structure, ids and that quotes exist verbatim in the cited source.",
                 "It does NOT check that the evidence supports the verdict; quote existence does not validate the interpretation.",
                 "Shadow lights are displayed next to the original module lights and never replace them.",
                 "Invalid findings are excluded from lights and the report; their candidates count as unadjudicated (not_checked)."),
    modules = unname(summaries), findings = findings)
  list(out = out, valid_findings = kept, ctx = ctx)
}
