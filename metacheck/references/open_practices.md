# Rubric: open practices statements (`open_practices`)

Rubric version 1. Needs: `modules/open_practices.json`, `modules/all_urls.json`; feeds
`design_brief.json` (paper-level `data`, `code`, `materials`, `preregistration`) and
`repo_code.md`.

## Purpose

Establish, per artefact (data, analysis code, materials, preregistration), what the paper
actually says about availability, and later whether the claim is borne out by the linked
repository. The module finds sentences by intersecting keyword lists; it cannot tell "data
are at osf.io/abcde" from "data cannot be shared (see www.ethics.example)".

## What the module gives you

Candidate sentences. Columns: `text`, `text_id`, `header`, `section_type`, and logical
flags per sentence: `data`, `code`, `materials`, `prereg` (which artefact words matched),
`on_request` (matches `(on|by) (reasonable )?request`). `summary_table` has `data_open`,
`code_open`, `materials_open`, `prereg_open`, `on_request` and statement counts. Light:
green = data and code "open"; yellow = one of them; red = neither, or any on-request
sentence. Materials and preregistration never affect the light.

## Known failure modes

- Keyword lists are loose: code words include a bare `R` and `analy*`; availability words
  include `see`. Expect irrelevant sentences ("see Table 2 for the data analysis").
- "Data cannot be shared due to privacy" plus any URL in the sentence counts as open.
- A sentence offering a repository for some artefacts and "on request" for others counts as
  open; only on-request-*only* sentences are excluded from open.
- Third-party or secondary datasets ("we used the publicly available ESS data") count as
  the authors' open data.
- Statements spread over several sentences ("All materials are online. The link is ...")
  are split; statements without the keywords are missed (see `recall_sweep.md`).

## Procedure

1. Read all candidate sentences with their paragraphs (`--candidate-id`), plus any
   dedicated section: `mc_context.R --run-dir $RUN --sections`, then `--section-id <n>` for
   headers like "Data availability", "Open practices", "Transparency", "Author note".
2. Build an **artefact table** for the paper (per study if they differ): for each of data,
   code, materials, preregistration, one status from the vocabulary below, the link(s), and
   the evidence sentence(s). Work out which link hosts which artefact; one OSF project may
   host all. Separate the authors' own data from third-party sources.
3. Write findings. Candidates: one finding per candidate sentence (most are
   `not_applicable`/`not_an_availability_statement` or point to the artefact-level
   finding). Artefact level: one finding per artefact attached to the most informative
   candidate sentence (`<candidate_id>:c1`, `c2` ... when a sentence covers several
   artefacts); if no candidate sentence exists for an artefact use a sweep id
   (`<paper_id>:open_practices:sweep:data`, `item_id` `"data"`).
4. Record the links in `design_brief.json` and pass them to `repo_code.md` /
   `prereg_compare.md`. When those checks finish, revisit: a claimed-open artefact whose
   repository is empty, private or lacks that artefact becomes `claimed_open_not_verified`.

## Classification vocabulary (closed; artefact-level)

| classification | verdict | severity |
|---|---|---|
| `open_with_link` - persistent or repository link given for this artefact | `not_an_issue` | `none` |
| `claimed_open_not_verified` - statement says open, but the link is missing, dead, private, or the artefact was not found there (cite the fetch result) | `confirmed_issue` | `medium` |
| `restricted_with_justification` - legal/ethical/third-party restriction stated, ideally with an access route | `not_an_issue` | `none` (note if no access route is given) |
| `on_request_only` | `confirmed_issue` | `low` for materials; `medium` for data or code |
| `third_party_data` - data belong to someone else; access route cited | `not_an_issue` | `none` |
| `stated_not_available` - authors say it will not be shared, no reason | `confirmed_issue` | `medium` |
| `no_statement_found` - nothing about this artefact in checked content (searches recorded) | `confirmed_issue` | `low`-`medium` (data/code: `medium` for empirical papers) |
| `artefact_not_applicable` - no data/code by nature (theory paper, qualitative with stated reasons, no preregistration claimed) | `not_applicable` | `none` |
| `not_an_availability_statement` (sentence-level false positive) | `not_applicable` | `none` |
| `statement_ambiguous` | `insufficient_evidence` | `none` |

Not preregistering is not an issue by itself: for `prereg`, `no_statement_found` has
severity `none` and verdict `not_applicable`, unless the paper calls analyses
"confirmatory" or "preregistered" without giving a link (then `claimed_open_not_verified`).

## When to abstain

`statement_ambiguous` when a statement such as "materials are available online" has no
link in the parsed text (links are often lost in PDF conversion: check `all_urls` and the
title-page/footnote sections first), or when "supplementary materials" may or may not
include data.

## Worked examples (invented illustrations)

- **Not an issue.** "All data, analysis scripts and materials are available at
  https://osf.io/ab12c/." -> three artefact findings `open_with_link` (`c1`-`c3`), pending
  repository verification.
- **Confirmed.** "The data that support the findings are available from the corresponding
  author upon reasonable request." -> data: `on_request_only`, `medium`. Advice: "Requests
  to authors often go unanswered over time; if the data can be shared, a repository link
  would be more durable. If they cannot, it is worth stating the restriction."
- **Not an issue (module says open for the wrong reason).** "Because participants did not
  consent to data sharing, the data cannot be made public; the ethics protocol is at
  https://example.org/protocol." -> data: `restricted_with_justification`.
- **Not applicable.** "See Table 3 for the full analysis of the reaction-time data." ->
  `not_an_availability_statement`.
- **Abstention.** "Materials are available online." No URL in the sentence, paragraph or
  `all_urls` that plausibly hosts materials. -> `statement_ambiguous`, limitation "link may
  have been lost in PDF conversion".

## Advice phrasing

Say which artefact, what the paper says, and the smallest fix (add the link, state the
restriction and access route, name the licence). Do not moralise about open science;
restricted data with a clear reason is good practice.
