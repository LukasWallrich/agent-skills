#!/usr/bin/env Rscript
# Import a paper into a run directory.
#   mc_import.R --file <pdf|docx|xml|json> --run-dir <dir> [--crossref-lookup]   (pdf/docx: add bib_match, needed by ref_accuracy)
#   mc_import.R --demo --run-dir <dir>
source(file.path(dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])), "_common.R"))

# heuristic signs that the parse (not the paper) is broken
parse_warnings <- function(paper, sections) {
  w <- character(0)
  types <- sections$section_type
  body <- paper$text[!paper$text$section_id %in% sections$section_id[types %in% "references"], ]
  n_words <- sum(lengths(strsplit(body$text, "\\s+")))
  n_bib <- nrow(paper$bib)
  n_bibr <- sum(paper$xref$xref_type %in% "bibr")
  title <- paper$info$title
  if (!length(title) || is.na(title[1]) || !nzchar(trimws(title[1]))) w <- c(w, "Empty title")
  if (nrow(body) == 0) w <- c(w, "No body text was extracted")
  if (nrow(body) > 0 && nrow(body) < 20) w <- c(w, sprintf("Very short text (%d sentences); the file may be truncated or image-only", nrow(body)))
  if (!"method" %in% types) w <- c(w, "No method section detected")
  if (!"results" %in% types) w <- c(w, "No results section detected (results may be classified under another section type; check --sections)")
  if (all(is.na(types) | types == "")) w <- c(w, "No section types were assigned")
  if (n_bib == 0 && n_words > 1000) w <- c(w, "No references extracted")
  if (n_bib > 0 && n_words / n_bib > 1000) w <- c(w, sprintf("Few references for the text length (%d refs, ~%d words)", n_bib, n_words))
  if (n_bib > 0 && n_bibr == 0) w <- c(w, "No in-text citations (bibr xrefs) although references were extracted; citing contexts are unavailable")
  if (n_bib > 0 && n_bibr > 0 && n_bibr < n_bib / 2) w <- c(w, sprintf("Fewer in-text citations (%d) than half the references (%d); citation linking is probably incomplete", n_bibr, n_bib))
  if (n_bibr > 0 && any(is.na(paper$xref$xref_id[paper$xref$xref_type %in% "bibr"]))) w <- c(w, "Some in-text citations are not linked to a reference (xref_id is NA)")
  if (n_bib > 0 && NROW(paper$bib_match) == 0) w <- c(w, "No bib_match table (Crossref lookups): ref_accuracy cannot run; re-import a pdf/docx with --crossref-lookup")
  if (anyDuplicated(paper$text$text_id)) w <- c(w, "Duplicate text_ids")
  w
}

db_dates <- function() {
  get_date <- function(f) tryCatch(as.character(f()), error = function(e) NULL)
  list(retractionwatch = get_date(rw_date), FLoRA = get_date(FLoRA_date))
}

git_commit <- function(path) {
  if (!nzchar(path) || !nzchar(Sys.which("git"))) return(NULL)
  out <- suppressWarnings(tryCatch(system2("git", c("-C", shQuote(path.expand(path)), "rev-parse", "HEAD"), stdout = TRUE, stderr = FALSE), error = function(e) NULL))
  if (length(out) == 1 && grepl("^[0-9a-f]{40}$", out)) out else NULL
}

mc_main({
  args <- mc_args()
  mc_require(args, "run_dir")
  demo <- isTRUE(args$demo)
  if (!demo && !is.character(args$file)) mc_fail("Provide --file <path> or --demo")
  if (!demo && !file.exists(args$file)) mc_fail("File not found: ", args$file)
  mc_load()

  run_dir <- args$run_dir
  dir.create(file.path(run_dir, "modules"), recursive = TRUE, showWarnings = FALSE)
  dir.create(file.path(run_dir, "sources"), showWarnings = FALSE)
  run_dir <- normalizePath(run_dir)

  src <- if (demo) demofile("json") else normalizePath(args$file)
  ext <- tolower(tools::file_ext(src))
  converted <- NULL
  if (ext %in% c("pdf", "docx", "doc")) {
    # needs a local or online GROBID/bibr server; surface its error as is
    mc_log("Converting ", basename(src), " (needs GROBID or bibr)...")
    conv_dir <- file.path(run_dir, "sources", "converted")
    dir.create(conv_dir, showWarnings = FALSE)
    converted <- tryCatch(convert(src, save_path = conv_dir, crossref_lookup = isTRUE(args$crossref_lookup)),
      error = function(e) mc_fail("Conversion of ", basename(src), " failed: ", conditionMessage(e)))
    converted <- grep("\\.json$", unlist(converted), value = TRUE)
    if (!length(converted) || !file.exists(converted[1])) mc_fail("Conversion of ", basename(src), " produced no JSON file")
    paper <- read(converted[1])
  } else if (ext %in% c("json", "xml")) {
    paper <- read(src)
  } else {
    mc_fail("Unsupported file type: .", ext, " (expected pdf, docx, xml or json)")
  }
  # read() logs and swallows parse errors, returning an empty paperlist
  if (!.is_paper(paper)) mc_fail("metacheck::read() could not parse ", basename(src), " into a single paper")

  saveRDS(paper, file.path(run_dir, "paper.rds"))
  paper_write(paper, "paper", run_dir)

  n_sent <- table(paper$text$section_id)
  rng <- function(id, f) { x <- paper$text$text_id[paper$text$section_id %in% id]; if (length(x)) f(x) else NA }
  sections <- paper$section[, intersect(c("section_id", "header", "section_type", "parent_section_id"), names(paper$section)), drop = FALSE]
  sections$n_sentences <- as.integer(n_sent[as.character(sections$section_id)])
  sections$n_sentences[is.na(sections$n_sentences)] <- 0L
  sections$text_id_min <- sapply(sections$section_id, rng, min)
  sections$text_id_max <- sapply(sections$section_id, rng, max)

  summary <- list(
    status = "ok",
    paper_id = paper$paper_id,
    title = paper$info$title[1],
    doi = paper$info$doi[1],
    input_format = paper$info$input_format[1],
    counts = list(
      sentences = nrow(paper$text), paragraphs = length(unique(paper$text$paragraph_id)),
      sections = nrow(paper$section), refs = nrow(paper$bib), xrefs = nrow(paper$xref),
      xrefs_bibr = sum(paper$xref$xref_type %in% "bibr"), bib_matches = NROW(paper$bib_match),
      urls = nrow(paper$url), tables = nrow(paper$table), figures = nrow(paper$figure),
      equations = NROW(paper$eq), authors = nrow(paper$author)
    ),
    sections = sections,
    parse_warnings = I(parse_warnings(paper, sections))
  )
  mc_write_json(summary, file.path(run_dir, "import_summary.json"))

  dev <- Sys.getenv("METACHECK_DEV_PATH", "")
  mc_manifest_update(run_dir, list(
    run_id = paste0(paper$paper_id, "_", format(Sys.time(), "%Y%m%dT%H%M%SZ", tz = "UTC")),
    paper_id = paper$paper_id,
    source = list(path = src, sha256 = mc_sha256(src), demo = demo, converted_json = converted[1]),
    metacheck = list(version = as.character(utils::packageVersion("metacheck")), commit = git_commit(dev), dev_path = if (nzchar(dev)) path.expand(dev) else NULL),
    r_version = R.version.string,
    databases = db_dates(),
    schema_versions = list(manifest = "1", module_result = "1", finding = "1"),
    created_at = mc_now()
  ))
  mc_out(summary)
})
