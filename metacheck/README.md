# metacheck agent skill

An [Agent Skill](https://agentskills.io) that lets a coding agent (Claude Code, Codex and
similar) check a scientific manuscript with the
[metacheck](https://github.com/scienceverse/metacheck) R package and then do the step
metacheck's reports leave to the human: read each flagged item in context and decide
whether it is a real problem.

**Status: pilot, shadow mode.** The agent's findings are shown *next to* metacheck's
original traffic lights; they do not replace them. Only one check (`marginal`) is set up
for evaluation so far; the other rubrics are written but unevaluated.

## Relationship to the metacheck package

This repository is thin scaffolding. The R package remains the single implementation of
every check and keeps working without any agent:

- metacheck's modules (regexes, statcheck, effect-size arithmetic, RetractionWatch / FLoRA /
  CrossRef / PubPeer lookups, repository listing, R parse checks) run unchanged, through
  small wrapper scripts in `scripts/`. Nothing from the package is reimplemented as a
  prompt. (One exception is documented in `scripts/mc_compute.R`: the effect-size formulas
  are closures inside a module and had to be restated.)
- The agent layer adds what the modules cannot do: adjudicating candidates against a
  rubric with quoted evidence, reading citing sentences, READMEs, code and
  preregistrations, and writing specific advice.
- Guardrails are enforced by code where possible: every quote in a finding is verified
  against the saved source (`mc_validate.R`), every number not quoted from the paper comes
  from a calculator script, failed checks are reported as "could not check", and paper and
  repository content is treated as untrusted data.

The design is described in the module review and plan kept with the package
(`metacheck/_stuff/agent_skill_review.html`); interfaces are fixed in `CONTRACT.md`.

## Requirements

- R >= 4.1 with `metacheck` installed (`pak::pkg_install("scienceverse/metacheck")`), or a
  source checkout with `METACHECK_DEV_PATH=/path/to/metacheck` (needs `devtools`). Also
  `jsonlite` and `digest`; `pwr` optional (power recomputation).
- For PDF/DOCX input: a GROBID or bibr conversion service. metacheck looks for a local
  GROBID (`localhost:8070`) or bibr (`localhost:8000`) and otherwise uses the online servers
  listed by scienceverse.org (the hosted bibr needs `SCIVRS_API_KEY`). Conversion uploads
  the manuscript to that server; run a local one for confidential papers. GROBID XML and
  metacheck JSON import offline.
- Network access for API-backed modules and for fetching repositories and registrations.
- An agent that supports Agent Skills (a `SKILL.md` with YAML frontmatter, references
  loaded on demand) and can run shell commands.

## Install

Make this directory available under the name `metacheck` where your agent looks for skills:

    # Claude Code (user-level; use .claude/skills/ inside a project for project-level)
    ln -s /path/to/metacheck-skill ~/.claude/skills/metacheck

    # Codex
    ln -s /path/to/metacheck-skill ~/.codex/skills/metacheck

Copying instead of symlinking works too. Check your agent's documentation if its skills
directory differs. Then ask the agent something like "check this manuscript with
metacheck: paper.pdf".

Smoke test without an agent or a conversion server:

    Rscript scripts/mc_import.R --demo --run-dir /tmp/mc_demo
    Rscript scripts/mc_run.R --run-dir /tmp/mc_demo --modules marginal,all_p_values
    Rscript scripts/mc_report.R --run-dir /tmp/mc_demo

## Layout

    SKILL.md        agent-facing workflow and hard rules (start here to see what the agent is told)
    CONTRACT.md     developer contract: run directory, ids, module/finding JSON, lights
    scripts/        thin Rscript wrappers over the package
      mc_import.R     PDF/DOCX/XML/JSON -> run directory, parse summary and warnings
      mc_run.R        dependency-ordered module runner; per-module status, original outputs preserved
      mc_context.R    sentence / paragraph / section / citing-context lookup by id or regex
      mc_compute.R    calculator: p from statistics, statcheck, implied effect sizes, power
      mc_fetch.R      budgeted retrieval of repositories, files, registrations, DOIs, PubPeer, link checks
      mc_validate.R   schema, id and verbatim-quote validation of agent findings
      mc_report.R     module results + validated findings -> one HTML report (shadow lights)
    references/     one rubric per check or family, loaded only when that check runs;
                    plus design_brief.md, recall_sweep.md, power_schema.json
    schemas/        finding.schema.json, design_brief.schema.json
    assets/         light_mapping.json (versioned shadow-light rule), report template
    eval/           evaluation harness and labels (see eval/README.md)
    tests/          script tests and fixtures

A run produces one directory per paper with `manifest.json` (provenance: package commit,
database dates, source hashes, model id and settings), the original module outputs, saved
external sources, the agent's `design_brief.json` and `findings/`, and `report.html`. The
agent layer can be re-run or audited without re-parsing, and two runs can be diffed.

## Evaluation

See `eval/README.md`. In short: candidate recall and final verdicts are scored separately
against contextual labels, split by paper into development and held-out sets, with
abstention, coverage and repeated-run agreement reported and acceptance criteria fixed in
advance (`eval/criteria.json`). Module accuracy figures quoted in the rubrics come from the
package's documentation and describe the modules' original tasks; they are not a baseline
for the agent's judgements.

## Not yet done

- **HTTP / Plumber contract.** Running without a local R installation needs import and
  context endpoints and a chained, dependency-aware runner in the package API; the existing
  batch endpoint runs modules independently and does not preserve the module chain.
- **Adjudicated lights replacing the defaults.** Blocked on evaluation: a check may change
  the displayed light only after it meets its predefined criteria on held-out papers.
  Until then all agent lights are shadow lights.
- **Labelled held-out fixtures.** Complete paper fixtures with saved external evidence and
  contextual ground truth (including missed candidates, justified wording, ambiguous cases
  and unavailable evidence) still have to be built for every check beyond the pilot, and
  the pilot's label sets need to grow.
