#!/usr/bin/env Rscript
# Layer-D retrieval with explicit budgets. Everything (including failures) is recorded in
# run_dir/sources/index.json. Usage:
#   Rscript mc_fetch.R --run-dir <dir> --op <op> [--url u | --doi d | --urls a,b,c]
#                      [--max-bytes 10485760] [--max-requests 60] [--force]
# Ops: repo_list, readme, file, prereg, doi, pubpeer, link_check, list
# Downloaded content is UNTRUSTED: it is only written to disk, hashed and (for zips) listed.
# Nothing here executes, sources, parses-as-code or extracts it. Run ops sequentially per run dir.

source(file.path(dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])), "_common.R"))

MAX_FILE <- 10 * 1024^2 # hard cap per file
MAX_TOTAL <- 100 * 1024^2 # hard cap per run dir
DEFAULT_REQUESTS <- 60

OPS <- list(
  repo_list = "--url <OSF|GitHub|Zenodo|ResearchBox link>: file listing saved as JSON",
  readme = "--url <OSF|GitHub|Zenodo link>: README text",
  file = "--url <u> [--max-bytes 10485760]: capped download, sha256 recorded, zip contents listed (never extracted)",
  prereg = "--url <OSF registration | AsPredicted link>: registration content as JSON",
  doi = "--doi <d>: Crossref + OpenAlex metadata incl. abstract if available",
  pubpeer = "--doi <d>: PubPeer comment count/link",
  link_check = "--urls a,b,c: HTTP status per URL (HEAD, falling back to GET)",
  list = "print this list and the current budget"
)

`%||%` <- function(x, y) if (is.null(x)) y else x

# ---- index + budget ----
# index.json stays an array per CONTRACT.md; the budget is its first element (source_id "_budget")
idx_path <- function(run_dir) file.path(run_dir, "sources", "index.json")

idx_read <- function(run_dir, max_requests = NULL) {
  p <- idx_path(run_dir)
  idx <- if (file.exists(p)) mc_read_json(p) else list()
  if (!length(idx) || !identical(idx[[1]]$source_id, "_budget")) {
    idx <- c(list(list(source_id = "_budget", uri = NULL, path = NULL, retrieved_at = mc_now(), sha256 = NULL,
                       status = "budget", bytes = 0, requests_used = 0, max_requests = DEFAULT_REQUESTS,
                       max_file_bytes = MAX_FILE, max_total_bytes = MAX_TOTAL)), idx)
  }
  if (!is.null(max_requests)) idx[[1]]$max_requests <- as.numeric(max_requests)
  idx
}

idx_ids <- function(idx) vapply(idx, \(e) e$source_id %||% "", "")

# short, stable id from op + target
make_id <- function(op, target) {
  s <- tolower(target)
  s <- sub("^[a-z]+://", "", s)
  s <- sub("^(www|api)\\.", "", s)
  s <- sub("^(github|zenodo|researchbox|aspredicted|osf)\\.(com|org|io)", "\\1", s)
  s <- gsub("[^a-z0-9]+", "_", s)
  s <- gsub("^_|_$", "", s)
  if (nchar(s) > 60) s <- paste0(substr(s, 1, 53), "_", substr(digest::digest(target, algo = "sha256"), 1, 6))
  paste(op, s, sep = "_")
}

# a repeated attempt (after a failure, or with --force) gets .2, .3 ...: nothing is dropped
unique_id <- function(idx, id) {
  ids <- idx_ids(idx)
  n <- 1; out <- id
  while (out %in% ids) { n <- n + 1; out <- paste0(id, ".", n) }
  out
}

# append an entry, update the budget, write, print the entry and exit
finish <- function(run_dir, idx, entry, requests = 1) {
  idx[[1]]$requests_used <- idx[[1]]$requests_used + requests
  idx[[1]]$bytes <- idx[[1]]$bytes + (entry$bytes %||% 0)
  idx[[1]]$retrieved_at <- mc_now()
  idx <- c(idx, list(entry))
  mc_write_json(idx, idx_path(run_dir))
  mc_out(c(entry, list(budget = idx[[1]][c("requests_used", "max_requests", "bytes", "max_total_bytes")])))
}

entry <- function(id, op, uri, status, path = NULL, error = NULL, summary = NULL, ...) {
  full <- if (!is.null(path)) file.path(RUN_DIR, path)
  list(source_id = id, op = op, uri = uri, path = path, retrieved_at = mc_now(),
       sha256 = if (!is.null(full)) mc_sha256(full), status = status,
       bytes = if (!is.null(full)) file.size(full) else 0, error = error, summary = summary, ...)
}

save_json <- function(id, x) {
  rel <- file.path("sources", paste0(id, ".json"))
  mc_write_json(x, file.path(RUN_DIR, rel))
  rel
}

save_text <- function(id, txt, ext = "txt") {
  rel <- file.path("sources", paste0(id, ".", ext))
  dir.create(dirname(file.path(RUN_DIR, rel)), recursive = TRUE, showWarnings = FALSE)
  writeLines(enc2utf8(txt), file.path(RUN_DIR, rel), useBytes = TRUE)
  rel
}

host_of <- function(url) {
  h <- tolower(sub("^[a-z]+://([^/]+).*", "\\1", url, ignore.case = TRUE))
  if (grepl("github\\.com$|githubusercontent\\.com$", h)) return("github")
  if (grepl("osf\\.io$", h)) return("osf")
  if (grepl("zenodo", url, ignore.case = TRUE)) return("zenodo")
  if (grepl("researchbox\\.org$", h)) return("researchbox")
  if (grepl("aspredicted\\.org$", h)) return("aspredicted")
  "other"
}

# call a package function quietly; warnings are kept (the package signals many failures that way)
quiet <- function(expr) {
  warns <- character(0)
  val <- withCallingHandlers(
    suppressMessages(expr),
    warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") })
  list(value = val, warnings = warns)
}

# ---- listings (shared by repo_list and readme) ----
# returns list(fn, files = data.frame(name, path, size, download_url), meta) or stops with the failure reason
repo_listing <- function(url) {
  host <- host_of(url)
  if (host == "github") {
    r <- quiet(github_files(url, recursive = TRUE))
    if (is.null(r$value)) stop("github_files() returned NULL: repo not found, private, or API failure. ", paste(r$warnings, collapse = "; "))
    f <- r$value
    f <- f[f$type != "dir", , drop = FALSE]
    list(fn = "metacheck::github_files(recursive = TRUE)", files = f[, intersect(c("name", "path", "size", "download_url", "type"), names(f))],
         meta = list(repo = f$clean_repo[1]))
  } else if (host == "osf") {
    r <- quiet(osf_info(url, recursive = TRUE))
    x <- r$value
    bad <- c("error", "unfound", "private", "invalid", "too many requests", "unknown")
    if (is.null(x) || !nrow(x) || x$osf_type[1] %in% bad) {
      stop("osf_info() could not retrieve the node (osf_type = ", x$osf_type[1] %||% "NULL", "). ", paste(r$warnings, collapse = "; "))
    }
    f <- x[x$osf_type == "files" & x$kind %in% "file", , drop = FALSE]
    list(fn = "metacheck::osf_info(recursive = TRUE)", files = f[, intersect(c("name", "path", "size", "download_url", "filetype", "osf_id", "parent"), names(f))],
         meta = as.list(x[1, intersect(c("osf_id", "name", "description", "osf_type", "category", "public", "registration", "project"), names(x))]),
         warnings = r$warnings)
  } else if (host == "zenodo") {
    r <- quiet(zenodo_info(url))
    x <- r$value
    if (is.null(x) || !nrow(x) || is.null(x$files) || is.na(x$title[1] %||% NA)) stop("zenodo_info() returned no record. ", paste(r$warnings, collapse = "; "))
    fl <- x$files[[1]]
    f <- data.frame(name = vapply(fl, \(z) z$key %||% NA_character_, ""), size = vapply(fl, \(z) as.numeric(z$size %||% NA), 0),
                    checksum = vapply(fl, \(z) z$checksum %||% NA_character_, ""),
                    download_url = vapply(fl, \(z) z$links$self %||% NA_character_, ""))
    f$path <- f$name
    list(fn = "metacheck::zenodo_info()", files = f,
         meta = as.list(x[1, intersect(c("zenodo_id", "title", "doi", "description", "publication_date", "resource_type", "license"), names(x))]))
  } else if (host == "researchbox") {
    r <- quiet(rbox_info(url))
    x <- r$value
    if (is.null(x) || !nrow(x) || !is.null(x$error) && !is.na(x$error[1])) stop("rbox_info() failed: ", x$error[1] %||% "NULL", ". ", paste(r$warnings, collapse = "; "))
    f <- x$files[[1]]
    list(fn = "metacheck::rbox_info()", files = f,
         meta = as.list(x[1, intersect(c("rb_url", "RB_target", "RB_license", "RB_public", "RB_authors", "RB_abstract"), names(x))]),
         note = "ResearchBox exposes file names only (no sizes or per-file URLs)")
  } else {
    stop("repo listing supports OSF, GitHub, Zenodo and ResearchBox links only")
  }
}

# ---- capped download; returns list(path, status, error, http_status) ----
capped_download <- function(url, dest, cap) {
  if (!grepl("^https?://", url, ignore.case = TRUE)) stop("Only http(s) URLs can be downloaded")
  con <- file(dest, "wb")
  got <- 0; over <- FALSE
  h <- curl::new_handle(followlocation = TRUE, maxredirs = 5, timeout = 120, useragent = "metacheck-skill (scienceverse/metacheck)",
                        protocols_str = "http,https", redir_protocols_str = "http,https")
  res <- tryCatch(
    curl::curl_fetch_stream(url, function(x) {
      got <<- got + length(x)
      if (got > cap) { over <<- TRUE; stop("cap") }
      writeBin(x, con)
    }, handle = h),
    error = function(e) e)
  close(con)
  if (over) {
    unlink(dest)
    return(list(status = "budget_exceeded", error = sprintf("file exceeds the %.0f byte cap; download aborted and partial file deleted", cap)))
  }
  if (inherits(res, "error")) {
    unlink(dest)
    return(list(status = "failed", error = conditionMessage(res)))
  }
  if (res$status_code >= 400) {
    unlink(dest)
    return(list(status = "failed", error = paste("HTTP", res$status_code), http_status = res$status_code))
  }
  list(status = "ok", http_status = res$status_code, final_url = res$url,
       content_type = curl::parse_headers_list(res$headers)[["content-type"]])
}

file_ext <- function(url) {
  b <- basename(sub("/+$", "", sub("[?#].*$", "", sub("^[A-Za-z]+://[^/]+", "", url))))
  if (grepl("\\.[A-Za-z0-9]{1,8}$", b)) tolower(sub(".*\\.", "", b)) else "bin"
}

# ---- ops ----
op_repo_list <- function(a, id) {
  l <- repo_listing(a$url)
  rel <- save_json(id, list(uri = a$url, retrieved_at = mc_now(), retrieved_with = l$fn, meta = l$meta,
                            n_files = nrow(l$files), files = l$files, note = l$note, warnings = l$warnings))
  entry(id, "repo_list", a$url, "ok", rel, retrieved_with = l$fn,
        summary = list(n_files = nrow(l$files), total_listed_bytes = if ("size" %in% names(l$files)) sum(l$files$size, na.rm = TRUE),
                       first_files = utils::head(l$files$path %||% l$files$name, 15), note = l$note))
}

op_readme <- function(a, id, cap) {
  if (host_of(a$url) == "github") {
    txt <- quiet(github_readme(a$url))$value
    # github_readme() returns "" for a missing README, a missing repo and an API failure alike
    if (!nzchar(txt %||% "")) stop("github_readme() returned an empty string: no README, repo not found, or API failure (the package does not distinguish these)")
    rel <- save_text(id, txt, "md")
    return(entry(id, "readme", a$url, "ok", rel, retrieved_with = "metacheck::github_readme()",
                 summary = list(n_chars = nchar(txt), first_line = trimws(strsplit(txt, "\n")[[1]][1]))))
  }
  l <- repo_listing(a$url)
  nm <- l$files$name %||% character(0)
  hit <- which(grepl("^read[ _-]?me", basename(nm), ignore.case = TRUE))
  if (!length(hit)) {
    return(entry(id, "readme", a$url, "not_found", retrieved_with = l$fn,
                 summary = list(n_files_listed = length(nm), note = "listing succeeded; no file named README* among the listed files")))
  }
  f <- l$files[hit[order(nchar(l$files$path[hit] %||% nm[hit]))][1], ]
  if (is.null(f$download_url) || is.na(f$download_url)) stop("README found (", f$name, ") but the host exposes no download URL")
  rel <- file.path("sources", paste0(id, ".", file_ext(f$name)))
  d <- capped_download(f$download_url, file.path(RUN_DIR, rel), cap)
  if (d$status != "ok") return(entry(id, "readme", a$url, d$status, error = d$error, readme_file = f$name))
  entry(id, "readme", a$url, "ok", rel, retrieved_with = paste(l$fn, "+ capped download"), readme_file = f$path %||% f$name,
        summary = list(readme_file = f$path %||% f$name, download_url = f$download_url))
}

op_file <- function(a, id, cap) {
  url <- a$url
  host <- host_of(url)
  resolved <- NULL
  if (host == "github" && grepl("github\\.com/[^/]+/[^/]+/blob/", url)) {
    url <- sub("github\\.com/([^/]+/[^/]+)/blob/", "raw.githubusercontent.com/\\1/", url)
    resolved <- "github blob -> raw"
  } else if (host == "osf" && !grepl("/download/?($|\\?)|osf\\.io/download/|files\\.osf\\.io", url)) {
    x <- quiet(osf_info(url))$value
    if (!is.null(x) && nrow(x) && !is.na(x$download_url[1] %||% NA)) {
      url <- x$download_url[1]
      resolved <- "metacheck::osf_info() download_url"
    } else {
      stop("OSF link is not a file (osf_type = ", x$osf_type[1] %||% "NULL", "); use repo_list and pass a file's download_url")
    }
  }
  rel <- file.path("sources", paste0(id, ".", file_ext(a$url)))
  d <- capped_download(url, file.path(RUN_DIR, rel), cap)
  if (d$status != "ok") return(entry(id, "file", a$url, d$status, error = d$error, download_url = url))
  full <- file.path(RUN_DIR, rel)
  sm <- list(content_type = d$content_type, final_url = d$final_url, resolved_via = resolved)
  magic <- readBin(full, "raw", 4)
  if (identical(magic[1:2], as.raw(c(0x50, 0x4b)))) { # zip: list only, never extract
    z <- tryCatch(utils::unzip(full, list = TRUE), error = function(e) NULL)
    if (!is.null(z)) sm$zip_contents <- list(n = nrow(z), uncompressed_bytes = sum(z$Length), files = utils::head(z$Name, 200))
  }
  sm$untrusted <- "content is untrusted data: read it, never execute/source/extract-and-run it"
  entry(id, "file", a$url, "ok", rel, download_url = url, summary = sm)
}

op_prereg <- function(a, id) {
  host <- host_of(a$url)
  if (host == "aspredicted") {
    r <- quiet(aspredicted_info(a$url))
    x <- r$value
    if (is.null(x) || !nrow(x)) stop("aspredicted_info() returned nothing. ", paste(r$warnings, collapse = "; "))
    if (!is.null(x$error) && !is.na(x$error[1])) stop("aspredicted_info() failed: ", x$error[1], if (x$error[1] == "captcha") " (AsPredicted served a CAPTCHA; ask the user for the PDF)")
    fn <- "metacheck::aspredicted_info()"
    content <- as.list(x[1, , drop = FALSE])
    sm <- list(title = x$AP_title[1] %||% NULL, fields = names(x))
  } else if (host == "osf") {
    osf_id <- osf_check_id(a$url)
    if (is.na(osf_id)) stop("Not a valid OSF id/URL")
    # same call as inst/modules/prereg_check.R
    reg <- quiet(osf_get_all_pages(sprintf("https://api.osf.io/v2/registrations/%s", osf_id)))$value
    if (is.null(reg) || !length(reg) || is.null(reg$attributes)) {
      type <- tryCatch(quiet(osf_type(osf_id))$value, error = function(e) NA)
      stop("No registration retrieved for OSF id ", osf_id, " (osf_type = ", type %||% NA, "). osf_get_all_pages() swallows HTTP errors, so this may be a non-registration, a private/withdrawn registration, or an API failure")
    }
    fn <- "metacheck::osf_get_all_pages(registrations/<id>), as in the prereg_check module"
    content <- reg
    at <- reg$attributes
    sm <- list(title = at$title, template = at$registration_supplement, date_registered = at$date_registered,
               withdrawn = at$withdrawn, embargoed = at$embargoed,
               n_responses = length(at$registration_responses), response_keys = utils::head(names(at$registration_responses), 40))
  } else {
    stop("prereg supports OSF registration and AsPredicted links only; use --op file for anything else")
  }
  rel <- save_json(id, list(uri = a$url, retrieved_at = mc_now(), retrieved_with = fn, content = content))
  entry(id, "prereg", a$url, "ok", rel, retrieved_with = fn, summary = sm)
}

op_doi <- function(a, id) {
  doi <- doi_clean(a$doi)
  cr <- tryCatch(quiet(crossref_doi(doi))$value, error = function(e) data.frame(DOI = doi, error = conditionMessage(e)))
  oa <- tryCatch(quiet(openalex_doi(doi))$value[[1]], error = function(e) list(DOI = doi, error = conditionMessage(e)))
  cr_err <- if (!nrow(cr)) "empty result" else if (!is.null(cr$error) && !is.na(cr$error[1])) as.character(cr$error[1])
  oa_err <- oa$error
  status <- if (is.null(cr_err) && is.null(oa_err)) "ok" else if (is.null(cr_err) || is.null(oa_err)) "partial" else "failed"
  err <- if (status != "ok") paste(c(if (!is.null(cr_err)) paste("crossref:", cr_err), if (!is.null(oa_err)) paste("openalex:", oa_err)), collapse = "; ")
  if (status == "failed") stop(err)
  oa_keep <- oa[intersect(c("id", "doi", "title", "publication_year", "publication_date", "type", "cited_by_count", "is_retracted",
                            "open_access", "primary_location", "authorships", "abstract"), names(oa))]
  rel <- save_json(id, list(doi = doi, retrieved_at = mc_now(), crossref = if (is.null(cr_err)) cr, openalex = if (is.null(oa_err)) oa_keep,
                            errors = list(crossref = cr_err, openalex = oa_err)))
  abstract <- oa$abstract %||% (if (is.null(cr_err) && !is.na(cr$abstract[1] %||% NA)) cr$abstract[1])
  entry(id, "doi", paste0("https://doi.org/", doi), status, rel, error = err,
        retrieved_with = "metacheck::crossref_doi() + metacheck::openalex_doi()",
        summary = list(title = (if (is.null(cr_err)) cr$title[1]) %||% oa$title, year = (if (is.null(cr_err)) cr$year[1]) %||% oa$publication_year,
                       container = if (is.null(cr_err)) cr$`container-title`[1], is_retracted_openalex = oa$is_retracted,
                       has_abstract = !is.null(abstract), abstract_chars = if (!is.null(abstract)) nchar(abstract)))
}

op_pubpeer <- function(a, id) {
  doi <- doi_clean(a$doi)
  x <- quiet(pubpeer_comments(doi))$value
  if (is.null(x)) stop("pubpeer_comments() returned NULL: the PubPeer request failed")
  rel <- save_json(id, list(doi = doi, retrieved_at = mc_now(), retrieved_with = "metacheck::pubpeer_comments()", result = x))
  entry(id, "pubpeer", paste0("https://pubpeer.com/search?q=", doi), "ok", rel, retrieved_with = "metacheck::pubpeer_comments()",
        summary = list(total_comments = x$total_comments[1], url = x$url[1], users = x$users[1],
                       note = "the package exposes comment count, thread URL and commenter names only, not comment text"))
}

check_one <- function(u) {
  if (!grepl("^https?://", u, ignore.case = TRUE)) return(list(url = u, ok = NA, http_status = NA, error = "not an http(s) URL"))
  go <- function(nobody) {
    h <- curl::new_handle(nobody = nobody, followlocation = TRUE, maxredirs = 5, timeout = 20,
                          useragent = "Mozilla/5.0 (compatible; metacheck-skill link check)")
    if (!nobody) curl::handle_setopt(h, range = "0-1023")
    curl::curl_fetch_memory(u, handle = h)
  }
  r <- tryCatch(go(TRUE), error = function(e) e)
  method <- "HEAD"
  if (inherits(r, "error") || r$status_code >= 400) { # many servers reject HEAD
    r2 <- tryCatch(go(FALSE), error = function(e) e)
    method <- "GET"
    if (!inherits(r2, "error") || inherits(r, "error")) r <- r2
  }
  if (inherits(r, "error")) return(list(url = u, ok = NA, http_status = NA, method = method, error = conditionMessage(r)))
  st <- r$status_code
  list(url = u, ok = st < 400, http_status = st, method = method, final_url = r$url,
       error = if (st %in% c(401, 403, 429)) "blocked or rate-limited: link state unknown, not necessarily dead" else NULL)
}

op_link_check <- function(a, id, urls) {
  res <- lapply(urls, check_one)
  n_ok <- sum(vapply(res, \(r) isTRUE(r$ok), NA))
  n_unknown <- sum(vapply(res, \(r) is.na(r$ok) || r$http_status %in% c(401, 403, 429), NA))
  rel <- save_json(id, list(retrieved_at = mc_now(), results = res))
  status <- if (n_unknown == length(res)) "failed" else if (n_unknown > 0) "partial" else "ok"
  entry(id, "link_check", paste(urls, collapse = ","), status, rel,
        error = if (n_unknown) paste(n_unknown, "URL(s) could not be checked (network error or blocked); these are 'could not check', not dead links"),
        summary = list(n = length(res), n_ok = n_ok, n_http_error = length(res) - n_ok - n_unknown, n_could_not_check = n_unknown, results = res))
}

# ---- main ----
mc_main({
  a <- mc_args()
  mc_require(a, "op")
  if (a$op == "list" && is.null(a$run_dir)) {
    mc_out(list(status = "ok", op = "list", ops = OPS))
    quit(status = 0, save = "no")
  }
  mc_require(a, "run_dir")
  RUN_DIR <- normalizePath(a$run_dir, mustWork = FALSE)
  if (!dir.exists(RUN_DIR)) mc_fail("Run directory does not exist: ", RUN_DIR)
  dir.create(file.path(RUN_DIR, "sources"), showWarnings = FALSE)
  idx <- idx_read(RUN_DIR, a$max_requests)
  bud <- idx[[1]]
  if (a$op == "list") {
    mc_out(list(status = "ok", op = "list", ops = OPS, budget = bud, source_ids = setdiff(idx_ids(idx), "_budget")))
    quit(status = 0, save = "no")
  }
  if (!a$op %in% names(OPS)) mc_fail("Unknown --op '", a$op, "'; use --op list")

  target_key <- switch(a$op, doi = , pubpeer = "doi", link_check = "urls", "url")
  mc_require(a, target_key)
  target <- a[[target_key]]
  urls <- if (a$op == "link_check") unique(trimws(strsplit(target, ",")[[1]])) else NULL
  n_req <- max(1, length(urls))
  base_id <- make_id(a$op, if (a$op == "link_check" && length(urls) > 1) paste0(length(urls), "urls_", substr(digest::digest(sort(urls), algo = "sha256"), 1, 8)) else target)

  # idempotent: an earlier successful fetch of the same target is returned without spending budget
  done <- Filter(\(e) sub("\\.[0-9]+$", "", e$source_id) == base_id && e$status %in% c("ok", "partial"), idx)
  if (length(done) && !isTRUE(a$force)) {
    mc_out(c(done[[length(done)]], list(cached = TRUE, note = "already retrieved in this run dir; pass --force to fetch again")))
    quit(status = 0, save = "no")
  }
  id <- unique_id(idx, base_id)

  cap <- min(as.numeric(a$max_bytes %||% MAX_FILE), MAX_FILE)
  if (is.na(cap) || cap <= 0) mc_fail("--max-bytes must be a positive number")
  if (bud$requests_used + n_req > bud$max_requests) {
    e <- entry(id, a$op, target, "budget_exceeded",
               error = sprintf("request budget exhausted (%d used + %d needed > %d); not fetched = could not check", bud$requests_used, n_req, bud$max_requests))
    finish(RUN_DIR, idx, e, requests = 0)
    quit(status = 0, save = "no")
  }
  if (a$op %in% c("file", "readme")) {
    cap <- min(cap, MAX_TOTAL - bud$bytes)
    if (cap <= 0) {
      e <- entry(id, a$op, target, "budget_exceeded", error = sprintf("total download budget of %.0f bytes for this run dir is used up", MAX_TOTAL))
      finish(RUN_DIR, idx, e, requests = 0)
      quit(status = 0, save = "no")
    }
  }

  mc_load()
  mc_log("mc_fetch: ", a$op, " ", target)
  e <- tryCatch(
    switch(a$op,
           repo_list = op_repo_list(a, id), readme = op_readme(a, id, cap), file = op_file(a, id, cap),
           prereg = op_prereg(a, id), doi = op_doi(a, id), pubpeer = op_pubpeer(a, id),
           link_check = op_link_check(a, id, urls)),
    error = function(err) entry(id, a$op, target, "failed", error = conditionMessage(err)))
  if (e$status %in% c("failed", "budget_exceeded")) e$interpretation <- "could not check (NOT 'nothing found')"
  finish(RUN_DIR, idx, e, requests = n_req)
})
