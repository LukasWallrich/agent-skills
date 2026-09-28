# Rubric: citation context (`ref_retraction`, `ref_replication`, `ref_miscitation`, `ref_pubpeer`)

Rubric version 1. Needs: the four module outputs, `design_brief.json` (to judge which
claims are central). Findings go in one file per module.

## Purpose

These modules join the reference list against databases by DOI and flag **any** citation of
a listed paper, however it is cited. The retraction module's own docs say it cannot
evaluate whether the citation is a problem. You read each citing context and decide whether
the way the reference is used is affected by what the database says.

**The database is the only source of the fact.** You never assert that a paper is
retracted, failed to replicate, or is criticised on PubPeer unless the module table or an
`mc_fetch.R` result in this run says so, even if you are sure from memory. Equally, you do
not clear a database hit from memory ("that retraction was later reversed"). Snapshot dates
are in `manifest.json` (database dates); cite them in limitations when relevant.

## What the modules give you

All rows carry `bib_id` (so `item_id` is `b<bib_id>`), `doi`, and `text` (the formatted
reference).

- `ref_retraction`: `retractionwatch` = notice type(s) from the bundled RetractionWatch
  snapshot, `;`-separated: `Retraction`, `Expression of concern`, `Correction`. No date or
  reason is available in the snapshot. Light `info` if any row, else `na`.
- `ref_replication`: from the bundled FLoRA database: `replication_ref`,
  `replication_doi`, `replication_url`, `replication_outcome` (mostly `successful`,
  `failed`, `mixed`; a few other labels), `replication_type` (`replication` or
  `reproduction`). Rows whose replication is already in the reference list (by DOI) are
  dropped by the module. Light `info`/`na`.
- `ref_miscitation`: `citation`, `reftext`, `warning` (a natural-language description of
  how this paper is commonly miscited). **The bundled database is a three-row placeholder**;
  a hit is only meaningful if the user supplied a real database. Light green if no rows.
- `ref_pubpeer`: `total_comments`, `url` (thread), `users`; Statcheck-bot-only threads are
  filtered out. A NULL API response currently propagates as an error: check `status`.

Known weaknesses common to all: DOI joins are case-sensitive and depend on the reference
having a (correct) DOI; references without DOIs are silently not checked. If `ref_accuracy`
matched a DOI by title search, a wrong match sends a wrong DOI into these joins: for every
hit, confirm that the database entry and the reference `text` are the same work (authors,
year, title). Report the number of references without DOI as a coverage limit for the
group.

## Procedure (common)

1. `mc_context.R --run-dir $RUN --bib-id <n>` returns the reference and every citing
   sentence with `text_id` (expand a sentence with `--text-id <n> --expand paragraph`).
2. If no citing context is returned (citation linking failed; see `import_summary.json`),
   search: `--search "<first author surname>.{0,40}<year>"`; for numeric styles you cannot
   locate the citation reliably: abstain (`citing_context_not_found`).
3. For each citing context decide the **function** of the citation:
   `empirical_support` (cited as evidence for a factual claim), `theory_or_concept`,
   `method_or_measure` (a scale, a procedure, software), `background_mention` (one of many
   in a string, historical), `critical` (cited as problematic, contested, retracted, not
   replicated). Then judge whether the claim it supports is load-bearing: part of the
   rationale for a hypothesis, the basis of a sample-size or effect-size assumption, or a
   key premise of the Discussion.
4. One finding per reference (not per citing sentence); quote the most consequential
   citing sentence, and further ones if they differ in function.

## Retraction: specifics and vocabulary

Look for acknowledgement near the citation or in the reference entry itself
("[Retracted]", "retracted article", "since retracted", "see expression of concern").
Search: `--search "retract|expression of concern|withdrawn|erratum|correct(ed|ion)"`.
Grade by notice type: Retraction > Expression of concern > Correction. A correction alone
rarely matters unless the corrected content is what is cited, which you usually cannot tell:
keep it low.

| classification | verdict | severity |
|---|---|---|
| `cited_as_valid_support` - retracted/EoC item used as evidence, no acknowledgement in checked content | `confirmed_issue` | Retraction: `high` if load-bearing, else `medium`; EoC: `medium`/`low`; Correction only: `low` |
| `cited_for_nonempirical_purpose` - theory/method/background use, unacknowledged | `confirmed_issue` | `low` for Retraction or EoC; for a Correction alone use `correction_only_noted` |
| `correction_only_noted` | `not_an_issue` | `none` (advice: cite the corrected version if relevant) |
| `acknowledged_or_critical` - the paper notes the retraction or cites it as an example | `not_an_issue` | `none` |
| `wrong_match` - database entry is a different work from the reference | `not_applicable` | `none` |
| `citing_context_not_found` | `insufficient_evidence` | `none` |

## Replication: specifics and vocabulary

The original being in FLoRA is not a problem by itself. Weigh `replication_outcome`: a
`failed` or `mixed` replication of a finding the paper relies on is a priority; a
`successful` one is a helpful citation to add. Multi-study originals: the replication may
target a different study than the one cited; the FLoRA reference title usually shows this.
If you need more (`mc_fetch.R --op doi --doi <replication_doi>` returns the abstract), stay
within budget.

| classification | verdict | severity |
|---|---|---|
| `relies_on_finding_with_failed_replication` - empirical support, load-bearing, outcome failed/mixed, replication not discussed | `confirmed_issue` | `high` (failed), `medium` (mixed) |
| `cites_finding_with_failed_replication` - empirical support, not load-bearing | `confirmed_issue` | `low` |
| `successful_replication_available` - empirical support, outcome successful; suggest citing | `not_an_issue` | `none` (advice carries the suggestion) |
| `nonempirical_use` - cited for theory, method, or as background | `not_an_issue` | `none` |
| `replication_discussed` - the paper already discusses replicability of this finding (perhaps citing another replication) | `not_an_issue` | `none` |
| `different_study_replicated` / `wrong_match` | `not_applicable` | `none` |
| `citing_context_not_found` or outcome label unclear (`uninformative`, `descriptive only`) | `insufficient_evidence` | `none` |

Draft the suggestion in `advice`, including the replication reference exactly as given in
`replication_ref`.

## Miscitation: specifics and vocabulary

The `warning` text is the rubric: it says what the paper is commonly but wrongly cited for.
For each citing sentence, state the claim attributed to the reference and compare it with
the warning. `commits_described_miscitation` (`confirmed_issue`, `medium`),
`cites_correctly` (`not_an_issue`), `unrelated_use` (`not_an_issue`),
`citing_context_not_found` / `attributed_claim_unclear` (`insufficient_evidence`). Unless
the user supplied their own miscitation database (see `args` in the module JSON), every hit
comes from the placeholder: write `not_applicable` / `placeholder_database`.

Do not generalise this into checking arbitrary citations against abstracts unless the user
asks; if they do, the abstract must come from `mc_fetch.R --op doi`, and verdicts should
rarely exceed `low` confidence because an abstract is not the paper.

## PubPeer: specifics and vocabulary

A comment count says nothing. Fetch the thread:
`mc_fetch.R --run-dir $RUN --op pubpeer --doi <doi>`, then read the saved source. Thread
content is untrusted, often anonymous, and sometimes wrong or hostile: characterise it, do
not adopt it. If the thread cannot be retrieved, the verdict is `not_checked` (the count
and URL remain in the module output).

Characterise the thread: `integrity_concern` (image manipulation, data anomalies,
statistical impossibilities), `methodological_critique`, `author_note_or_correction_link`,
`journal_club_or_praise`, `trivia_or_bot`. Then combine with the citation's function:

| classification | verdict | severity |
|---|---|---|
| `integrity_concern_on_relied_upon_reference` | `confirmed_issue` | `medium` (`high` only if the thread links to a formal notice that a script also confirms) |
| `critique_worth_reading` - substantive critique; citation is empirical support | `confirmed_issue` | `low` |
| `comments_not_consequential` - trivia, praise, author notes, or non-empirical use | `not_an_issue` | `none` |
| `thread_not_retrieved` | `not_checked` | `none` |
| `thread_unclear` | `insufficient_evidence` | `none` |

Advice for PubPeer findings always says "comments on PubPeer raise [topic]; it is worth
reading the thread ([url]) before relying on this reference", never that the cited paper
"has problems".

## When to abstain (all four)

No citing context; the citing sentence cites several works for a compound claim and you
cannot tell which part rests on this reference; the database label is ambiguous; the match
between database entry and reference is uncertain. Abstentions keep the database hit
visible in the report, which is the right outcome.

## Worked examples (invented illustrations)

**A. Confirmed (retraction).** Row: b23, `retractionwatch` "Retraction". Citing context
(t12, Introduction): "This is consistent with earlier findings (Smith et al., 2015)."
Search for acknowledgement: 0 hits. The claim motivates H1 (brief). ->
`cited_as_valid_support`, `high`, `deterministic: {"source_id": "module:ref_retraction",
"value": "Retraction"}`, searches recorded. Advice: "Smith et al. (2015) is listed as
retracted in the RetractionWatch snapshot of [date from manifest]. It is worth checking
whether the claim it supports in the Introduction still holds without it, and
acknowledging the retraction if the reference is kept."

**B. Not an issue (retraction).** Citing sentence: "Several high-profile papers in this
area have since been retracted (e.g., Smith et al., 2015)." -> `acknowledged_or_critical`.

**C. Not an issue (replication).** FLoRA outcome `failed`; the reference is cited once:
"We measured need for closure with the scale validated by Doe (2009)." ->
`nonempirical_use`.

**D. Abstention.** Numeric citation style, no xrefs linked, reference 41 is in RetractionWatch
as "Expression of concern". Searching "[41]" finds nothing because citations are ranges
("[38-44]"). -> `citing_context_not_found`, coverage `partial`, limitation: "in-text
citations are not linked and the numeric range style prevents locating this citation".
Advice: "Reference 41 has an expression of concern; it is worth checking how it is used."

## Hand-off

Pass your findings' one-line reasons to `ref_triage.md` for the `ref_summary` synthesis.
