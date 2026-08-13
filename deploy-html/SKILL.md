---
name: deploy-html
description: Publish a standalone HTML document (report, plan, findings, mock) to a live public URL on surge.sh, normally with the html-comments review layer switched on. Use when the user wants a communication as HTML page deployed, published, put online, shared with reviewers, or given a link. Not used for a full website, or any HTML shipped for users.
---

# Deploy HTML — one command to a live, commentable URL

```sh
~/.claude/skills/deploy-html/bin/deploy-doc.sh report.html
```

That stages a copy of the file, injects the html-comments overlay, checks for collisions,
deploys to `https://report.surge.sh/`, verifies the page came back, and prints the URL plus
the teardown command. **The source file is never modified.**

## Writing the page

Link identifiers wherever they resolve — DOIs as `https://doi.org/…`, GitHub issues, PRs and
commit hashes, ticket ids, dataset accessions. A reader should be able to click through
rather than paste a string into a search box.

## Which host

**surge.sh for anything short-lived** — a draft under review, a findings page, a handover
note, a mock. One command, no repo, no commit, a subdomain per document, and
`surge teardown <domain>` when it has served its purpose.

**GitHub Pages for things that should last** — a project's own published site, or a page
worth keeping and indexing. For one-off pages that still want a permanent home, that is
`openclaw_projects` (one folder per page, plus an index card in the same commit).

Both are public-by-URL, as is the comment endpoint. Nothing confidential, no participant
data, no unpublished manuscript text goes to either.

## Options

| | |
|---|---|
| `--slug <slug>` | comment-tab slug; also the default subdomain. Defaults to the filename. |
| `--domain <host>` | deploy target, default `<slug>.surge.sh`. |
| `--no-comments` | deploy without the review layer. |
| `--dry-run` | print the plan, upload nothing. |
| `--force` | skip the collision checks. Needed the first time you redeploy over a page that predates this script. |

Pass a **directory** rather than a file when the page is not self-contained; it must contain
`index.html`. Given a lone file that references relative assets results in a warning.

## Choosing the slug

The slug names the Google Sheet tab that holds the page's comments (unless --no-comments is specified), so **one slug ↔ one
document, forever**. Defaults come from the filename, which can cause collissions: 
`report.html`, `draft.html` and `index.html` are not names, they are placeholders.
Derive the slug from the content instead (`zcurve-predictive-accuracy`,
`flora-screening-audit`), and state it to the user.

A revised version you want reviewed **separately** needs a new slug (`-v2`); comments do not
migrate. A revised version of the **same** review round keeps its slug — just re-run the
command, and the existing comments stay attached.

## What it refuses, and why not to force it

Three things are checked before anything uploads, because none of them can be undone:

1. **The domain already serves a different document.** Deploying would destroy it.
2. **The slug already has a comment tab elsewhere.** Two documents on one tab corrupts both
   sets of comments — each page tries to anchor the other's comments into text it does not
   contain. This check is `html-comments/bin/check-slug.sh`, the same script used when a slug
   is embedded by hand, so both routes apply one rule.
3. **The domain is live but carries no marker** (deployed before this script, or by
   something else). It cannot be shown to be the same document, so it is not assumed.

For 1 and 2 the answer is a fresh slug, not `--force`. For 3, look at the page and force it
if it is yours.

## Setup

Reads `~/.claude/html-comments.config.json` (endpoint + asset host) and needs the `surge`
CLI on PATH. Both should be on this machine. See the **html-comments** skill for the config and for
enabling the layer on Quarto or local `file://` pages, and **review-comments** for reading the
collected comments back and applying them to the source.
