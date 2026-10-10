---
name: deploy-html
description: Publish a standalone HTML report, plan, findings page, or mock to a public URL using Cloudflare Workers Static Assets, normally with the html-comments review layer. Use when the user explicitly asks to deploy, publish, put online, or give others a link. Use local or tailnet hosting for private previews; this skill is not for a full website. Honor an explicitly chosen hosting provider.
---

# Publish an HTML report

Default to **Cloudflare Workers Static Assets**. One persistent site serves reports at
`https://lukas-reports.<account-subdomain>.workers.dev/<slug>/`, with an index at `/`.
Static assets do not require Worker code. Any configured machine can publish: the
site's file tree lives in a private git repository, and a publish commits one report
to it, pushes, and deploys the pushed tree. Keep Surge as an explicit fallback;
use GitHub Pages for a project's existing published site when appropriate.

```sh
~/.codex/skills/deploy-html/bin/deploy-doc.sh ./clean-report-folder --slug flora-screening-audit
```

Pass a clean directory containing `index.html` and its linked downloads/assets.
For a self-contained document, pass the HTML file instead. The source is unchanged.
Do not pass a project checkout: the whole supplied folder is uploaded. Hidden files
and symlinks are rejected. Check that the staging folder contains only intended
public material, including downloads. Link DOIs, issues, PRs, and other resolvable identifiers.

Before publishing, include a distinct, subject-relevant emoji favicon in the
document's `<head>`. Choose an emoji that distinguishes this report from other
reports likely to be open alongside it, and retain it when updating the same report.
Use an SVG data URI so the favicon needs no separate asset, for example:

```html
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E🔬%3C/text%3E%3C/svg%3E">
```

Replace the example emoji with the report's chosen emoji and verify that the
published HTML retains the favicon link.

The helper injects the review layer, checks document and comment collisions,
claims the comment slug, deploys, and verifies the **exact new HTML bytes** over HTTPS.
Share the verified URL and check relevant linked downloads before finishing.

## Options

| Option | Effect |
|---|---|
| `--slug <slug>` | Descriptive report path and comment slug. Defaults to the filename. |
| `--no-comments` | Omit injection of the review layer. |
| `--dry-run` | Stage and validate against the local checkout; claim, push and upload nothing. |
| `--delete <slug>` | Remove a published report (no source argument). Its slug stays reserved and cannot be reused. |
| `--migrate-from <url>` | Move this same marked report from another host, retaining its comment slug and reservation. |
| `--state-dir <path>` | Use a separate managed site state/configuration. |
| `--provider surge` | Use the original Surge helper, supporting `--domain`, `--force`, and its original options. |

## Report identity and migration

One comment slug identifies one document, forever. Choose a content-specific slug;
`report`, `draft`, and `index` are poor defaults. Keep the same slug for the same
review round. Use a new slug for a separately reviewed revision.

The Cloudflare helper refuses a source carrying another slug or an unrecognized live
path. For migration, `--migrate-from` verifies that the old page carries the same
`hc-doc` marker. Its existing comment reservation remains associated with the old URL;
the new report uses the same comment tab. The helper records that reservation in the
site repository for future updates. Do not release a reservation or use a fresh comment slug merely
because the host changes. Keep the old link available unless removal was requested.

## Machine setup

Each publishing machine keeps private configuration at `~/.config/deploy-html/`:

- `config.json`: `site_repo` (git URL of the shared site tree, required), optional
  `worker_name` (default `lukas-reports`) and `credentials_file`; the helper records
  `base_url` after deployment.
- `cloudflare.env`: `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (mode 0600).
- `tools/`: locally installed Wrangler and its lockfile.
- `repo/`: a checkout of the site repository. It is a cache that every publish resets
  to the remote; `deploy.lock` serializes publications on one machine.

The site repository holds `site/<slug>/` for each report, `site/reports.json` (the
registry, including removed slugs) and the generated `site/index.html`. The machine
needs git credentials that can push to it.

Use **request-secret** to collect missing credentials privately. An account-scoped
token with `Workers Scripts: Edit` and `Account Settings: Read`, scoped to the
chosen account, and an enabled workers.dev subdomain are required. If the dashboard
uses granular roles, initial creation needs Workers product Admin; subsequent
deployments can use Editor. No zone permissions are needed for workers.dev hosting.
Install the CLI once, then use the installed version without downloading on each run:

```sh
npm install --prefix ~/.config/deploy-html/tools wrangler@4 --no-audit --no-fund
```

The review layer reads `~/.claude/html-comments.config.json`; the sibling
**html-comments** and **request-secret** skills must be installed. Use **review-comments**
to read feedback. The Surge fallback requires the Surge CLI.

The site repository is authoritative. Every upload replaces the Worker's full asset
manifest, so do not deploy to the same Worker outside this helper and do not edit
`site/` by hand. If another machine pushed first, the helper takes its tree and
re-applies the one report; after uploading it fetches again and uploads once more if
the repository moved, so the newest tree ends up live. The helper refuses to deploy a
tree in which a listed report has no page or a folder is not listed. If the upload
fails after the push, the change stays in the repository and goes live with the next
successful publish from any machine. Earlier versions of every report are in the
repository's history.

Cloudflare documentation: [static assets](https://developers.cloudflare.com/workers/static-assets/),
[authorization](https://developers.cloudflare.com/workers/authorization/).
