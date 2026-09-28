#!/usr/bin/env Rscript
# Look up paper context by id. Every returned sentence carries its text_id.
#   mc_context.R --run-dir <dir> --text-id 16[,17] [--expand sentence|paragraph|section] [--plus n] [--minus n]
#   mc_context.R --run-dir <dir> --bib-id 3
#   mc_context.R --run-dir <dir> --candidate-id <paper_id>:<module>:<item_id>
#   mc_context.R --run-dir <dir> --search "regex" [--section-type results] [--case-sensitive]
#   mc_context.R --run-dir <dir> --sections
#   mc_context.R --run-dir <dir> --section-id n
source(file.path(dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])), "_common.R"))

int_ids <- function(x, flag) {
  ids <- suppressWarnings(as.integer(trimws(strsplit(as.character(x), ",")[[1]])))
  if (!length(ids) || anyNA(ids)) mc_fail("--", flag, " needs integer id(s), got: ", x)
  ids
}

sentences <- function(paper, rows) {
  tx <- paper$text[rows, c("text_id", "text"), drop = FALSE]
  rownames(tx) <- NULL
  tx
}

section_info <- function(paper, section_id) {
  s <- paper$section[match(section_id, paper$section$section_id), , drop = FALSE]
  list(section_id = section_id, header = s$header, section_type = s$section_type)
}

# one sentence with its paragraph, section and neighbours
text_context <- function(paper, id, expand = "paragraph", plus = 0, minus = 0) {
  tx <- paper$text
  i <- match(id, tx$text_id)
  if (is.na(i)) return(list(text_id = id, error = "text_id not found in paper"))
  row <- tx[i, , drop = FALSE]
  in_par <- which(tx$paragraph_id %in% row$paragraph_id & tx$section_id %in% row$section_id)
  in_sec <- which(tx$section_id %in% row$section_id)
  ctx <- switch(expand, sentence = i, paragraph = in_par, section = in_sec)
  # text_expand only does plus/minus for sentences within one paragraph, so neighbours are added here (within the section)
  ctx <- intersect(seq(min(ctx) - minus, max(ctx) + plus), in_sec)
  row$paper_id <- paper$paper_id
  out <- c(list(text_id = id, text = row$text), section_info(paper, row$section_id), list(
    paragraph_id = row$paragraph_id,
    page_number = row$page_number,
    expand = expand,
    expanded = if (plus + minus > 0) paste(tx$text[ctx], collapse = " ") else text_expand(row, paper, expand)$expanded,
    context = sentences(paper, ctx),
    previous = if (i > 1) sentences(paper, i - 1) else NULL,
    `next` = if (i < nrow(tx)) sentences(paper, i + 1) else NULL
  ))
  xr <- paper$xref[paper$xref$text_id %in% id, , drop = FALSE]
  if (nrow(xr)) out$xrefs <- xr
  urls <- paper$url[paper$url$text_id %in% id, , drop = FALSE]
  if (nrow(urls)) out$urls <- urls
  out
}

# reference entry plus every location citing it (bibr xrefs: xref_id == bib_id)
bib_context <- function(paper, id) {
  b <- paper$bib[paper$bib$bib_id %in% id, , drop = FALSE]
  if (!nrow(b)) return(list(bib_id = id, error = "bib_id not found in paper"))
  xr <- paper$xref[paper$xref$xref_type %in% "bibr" & paper$xref$xref_id %in% id, , drop = FALSE]
  ref_text <- if ("text_id" %in% names(b)) paper$text$text[match(b$text_id[1], paper$text$text_id)] else NA
  match <- if (is.data.frame(paper$bib_match)) paper$bib_match[paper$bib_match$bib_id %in% id, , drop = FALSE] else NULL
  list(
    bib_id = id, reference = b, reference_text = ref_text,
    bib_match = if (NROW(match)) match else NULL,
    n_citing = nrow(xr),
    citing = lapply(seq_len(nrow(xr)), \(j) c(list(citation = xr$contents[j]), text_context(paper, xr$text_id[j], "paragraph"))),
    note = if (!nrow(xr)) "No in-text citation is linked to this reference. Citation linking can be incomplete: search for the author name with --search before concluding it is uncited." else NULL
  )
}

mc_main({
  args <- mc_args(list(expand = "paragraph", plus = "0", minus = "0"))
  mc_require(args, "run_dir")
  modes <- intersect(c("text_id", "bib_id", "candidate_id", "search", "sections", "section_id"), names(args))
  if (length(modes) != 1) mc_fail("Give exactly one of --text-id, --bib-id, --candidate-id, --search, --sections, --section-id")
  if (!args$expand %in% c("sentence", "paragraph", "section")) mc_fail("--expand must be sentence, paragraph or section")
  plus <- int_ids(args$plus, "plus"); minus <- int_ids(args$minus, "minus")
  mc_load()
  paper <- mc_paper(args$run_dir)
  out <- list(status = "ok", paper_id = paper$paper_id, query = modes)

  if (modes == "text_id") {
    out$results <- lapply(int_ids(args$text_id, "text-id"), \(id) text_context(paper, id, args$expand, plus, minus))
  } else if (modes == "bib_id") {
    out$results <- lapply(int_ids(args$bib_id, "bib-id"), \(id) bib_context(paper, id))
  } else if (modes == "candidate_id") {
    parts <- strsplit(args$candidate_id, ":", fixed = TRUE)[[1]]
    if (length(parts) < 3) mc_fail("candidate_id must look like <paper_id>:<module>:<item_id>")
    module <- parts[length(parts) - 1]
    path <- file.path(args$run_dir, "modules", paste0(module, ".json"))
    if (!file.exists(path)) mc_fail("No module result at ", path)
    mod <- mc_read_json(path)
    hit <- Filter(\(r) identical(r$candidate_id, args$candidate_id), mod$table)
    if (!length(hit)) mc_fail("candidate_id not found in modules/", module, ".json")
    row <- hit[[1]]
    out$module <- module
    out$module_status <- mod$status
    out$row <- row
    # ref_* rows point at a reference; their text_id (if any) is the reference list entry
    if (startsWith(module, "ref_") && !is.null(row$bib_id)) out$bib <- bib_context(paper, as.integer(row$bib_id))
    if (!is.null(row$text_id)) out$text <- text_context(paper, as.integer(row$text_id), args$expand, plus, minus)
    if (is.null(out$bib) && is.null(out$text)) out$note <- "This row has no text_id or bib_id; use --search to locate it in the paper."
  } else if (modes == "search") {
    hits <- text_search(paper, args$search, return = "sentence", ignore.case = !isTRUE(args$case_sensitive), perl = TRUE)
    if (is.character(args$section_type)) hits <- hits[hits$section_type %in% strsplit(args$section_type, ",")[[1]], , drop = FALSE]
    out$pattern <- args$search
    out$n <- nrow(hits)
    out$results <- as.data.frame(hits[, intersect(c("text_id", "text", "section_id", "header", "section_type", "paragraph_id"), names(hits)), drop = FALSE])
    if (!nrow(hits)) out$section_types_in_paper <- unique(paper$section$section_type)
  } else if (modes == "sections") {
    s <- paper$section[, intersect(c("section_id", "header", "section_type", "parent_section_id"), names(paper$section)), drop = FALSE]
    ids <- split(paper$text$text_id, paper$text$section_id)[as.character(s$section_id)]
    s$n_sentences <- lengths(ids)
    s$text_id_min <- sapply(ids, \(x) if (length(x)) min(x) else NA)
    s$text_id_max <- sapply(ids, \(x) if (length(x)) max(x) else NA)
    rownames(s) <- NULL
    out$results <- s
  } else if (modes == "section_id") {
    out$results <- lapply(int_ids(args$section_id, "section-id"), \(id) {
      if (!id %in% paper$section$section_id) return(list(section_id = id, error = "section_id not found in paper"))
      rows <- which(paper$text$section_id %in% id)
      tx <- paper$text[rows, c("text_id", "paragraph_id", "text"), drop = FALSE]
      rownames(tx) <- NULL
      extras <- Filter(NROW, list(tables = paper$table[paper$table$section_id %in% id, intersect(c("table_id", "html"), names(paper$table)), drop = FALSE],
                                  figures = paper$figure[paper$figure$section_id %in% id, intersect(c("figure_id", "page_number"), names(paper$figure)), drop = FALSE]))
      c(section_info(paper, id), list(n_sentences = nrow(tx), sentences = tx), extras)
    })
  }
  mc_out(out)
})
