---
name: deploy-html
description: Publish a standalone HTML report, plan, findings page, or mock to a public URL using Cloudflare Workers Static Assets, normally with the html-comments review layer. Use when the user explicitly asks to deploy, publish, put online, or give others a link. Use local or tailnet hosting for private previews; this skill is not for a full website. Honor an explicitly chosen hosting provider.
---

# Publish an HTML report

Default to **Cloudflare Workers Static Assets**. One persistent site serves reports at
`https://lukas-reports.<account-subdomain>.workers.dev/<slug>/`, with an index at `/`.
Static assets do not require Worker code. Keep Surge as an explicit fallback;
use GitHub Pages for a project's existing published site when appropriate.

```sh
~/.codex/skills/deploy-html/bin/deploy-doc.sh ./clean-report-folder --slug flora-screening-audit
```

Pass a clean directory containing `index.html` and its linked downloads/assets.
For a self-contained document, pass the HTML file instead. The source is unchanged.
Do not pass a project checkout: the whole supplied folder is uploaded. Hidden files
and symlinks are rejected. Check that the staging folder contains only intended
public material, including downloads. Link DOIs, issues, PRs, and other resolvable identifiers.

The helper injects the review layer, checks document and comment collisions,
claims the comment slug, deploys, and verifies the **exact new HTML bytes** over HTTPS.
Share the verified URL and check relevant linked downloads before finishing.

## Options

| Option | Effect |
|---|---|
| `--slug <slug>` | Descriptive report path and comment slug. Defaults to the filename. |
| `--no-comments` | Omit injection of the review layer. |
| `--dry-run` | Stage and validate offline; upload and claim nothing. |
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
the new report uses the same comment tab. The helper records that reservation locally
for future updates. Do not release a reservation or use a fresh comment slug merely
because the host changes. Keep the old link available unless removal was requested.

## Machine setup

Private configuration/state lives at `~/.config/deploy-html/`:

- `config.json`: optional `worker_name` (default `lukas-reports`), `credentials_file`;
  the helper records `base_url` after deployment.
- `cloudflare.env`: `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (mode 0600).
- `tools/`: locally installed Wrangler and its lockfile.
- `site/`: complete published asset tree; every deployment preserves sibling reports.
- `previous-site/`: previous asset tree; `deploy.lock` serializes local publications.

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

The local asset tree is authoritative: publish this shared site from this machine,
or transfer its complete state before changing machines. Do not edit the same Worker
outside this helper: a later upload replaces its full asset manifest. The previous
snapshot is retained for recovery; an upload that succeeds is saved locally even if
edge verification fails. A failed CLI deployment does not replace local site state.

Cloudflare documentation: [static assets](https://developers.cloudflare.com/workers/static-assets/),
[authorization](https://developers.cloudflare.com/workers/authorization/).
