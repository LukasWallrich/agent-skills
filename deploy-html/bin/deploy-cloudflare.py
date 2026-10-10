#!/usr/bin/env python3
"""Publish report folders to one assets-only Worker.

The site tree lives in a private git repository shared by every publishing machine.
A publish commits one report to it, pushes, and deploys the pushed tree.
"""
import argparse
import fcntl
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

SKILL = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path.home() / '.config/deploy-html'


def fetch(url: str, token: str = '') -> bytes:
    headers = {'User-Agent': 'deploy-html/1.0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def prepare(source: Path, target: Path, slug: str, comments: bool) -> None:
    if source.is_dir():
        for item in source.rglob('*'):
            if item.is_symlink() or any(p.startswith('.') for p in item.relative_to(source).parts):
                raise ValueError('Publish a clean staging directory without hidden files or symlinks.')
        shutil.copytree(source, target)
    else:
        target.mkdir()
        shutil.copyfile(source, target / 'index.html')
    page = target / 'index.html'
    content = page.read_text()
    existing = re.findall(r"(?:hc-doc:\s*|project:\s*['\"])([a-z0-9-]+)", content)
    if any(s != slug for s in existing):
        raise ValueError('Source carries a different document slug.')
    if comments and 'HC_CONFIG' not in content:
        cfg = json.loads((Path.home() / '.claude/html-comments.config.json').read_text())
        snippet = '<script>window.HC_CONFIG=' + json.dumps({'endpoint': cfg['endpoint'], 'project': slug}) + ';'
        snippet += 'var b=' + json.dumps(cfg['assetBase']) + ';var l=document.createElement("link");l.rel="stylesheet";l.href=b+"html-comments.css";document.head.appendChild(l);var s=document.createElement("script");s.src=b+"html-comments.js";document.head.appendChild(s);</script>'
        # Existing html-comments helpers recognize the readable project marker.
        snippet += "<!-- project: '" + slug + "' -->"
        pos = content.lower().rfind('</body>')
        content = content[:pos] + snippet + content[pos:] if pos >= 0 else content + snippet
    if 'hc-doc: ' + slug not in content:
        content += '\n<!-- hc-doc: ' + slug + ' -->\n'
    page.write_text(content)


INDEX_HEAD = '<!doctype html><html><head><meta charset="utf-8"><title>Reports</title><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 100 100%27%3E%3Ctext y=%27.9em%27 font-size=%2790%27%3E📚%3C/text%3E%3C/svg%3E"></head><body><h1>Reports</h1><ul>'


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('git ' + ' '.join(args) + ' failed:\n' + result.stderr.strip())
    return result.stdout.strip()


def sync_repo(state: Path, cfg: dict) -> Path:
    """Reset the local checkout to the shared tree. The checkout is a cache, never the authority."""
    if not cfg.get('site_repo'):
        raise ValueError('Set "site_repo" (git URL of the shared site tree) in ' + str(state / 'config.json'))
    repo = state / 'repo'
    if not (repo / '.git').exists():
        git(state, 'clone', '--quiet', cfg['site_repo'], str(repo))
    git(repo, 'fetch', '--quiet', 'origin')
    if git(repo, 'branch', '-r'):
        git(repo, 'checkout', '--quiet', '-B', 'main', 'origin/main')
        git(repo, 'clean', '-fdq')
    (repo / 'site').mkdir(exist_ok=True)
    return repo


def load_records(site: Path) -> dict:
    return json.loads((site / 'reports.json').read_text()) if (site / 'reports.json').exists() else {}


def check_tree(site: Path, records: dict) -> None:
    """Every listed report has its page, and every folder is a listed report."""
    live = {slug for slug, record in records.items() if not record.get('removed')}
    missing = sorted(slug for slug in live if not (site / slug / 'index.html').is_file())
    stray = sorted(item.name for item in site.iterdir() if item.is_dir() and item.name not in live)
    if missing or stray:
        raise ValueError(f'Site tree is inconsistent (missing: {missing}; unlisted: {stray}). Nothing was deployed.')


def write_site_files(site: Path, records: dict) -> None:
    (site / 'reports.json').write_text(json.dumps(records, indent=2) + '\n')
    live = sorted(slug for slug, record in records.items() if not record.get('removed'))
    cards = ''.join(f'<li><a href="/{html.escape(s)}/">{html.escape(s)}</a></li>' for s in live)
    (site / 'index.html').write_text(INDEX_HEAD + cards + '</ul></body></html>')


def credentials(state: Path, cfg: dict) -> dict:
    spec = importlib.util.spec_from_file_location('request_secret', SKILL.parent / 'request-secret/scripts/request_secret.py')
    secret = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(secret)
    env = dict(os.environ)
    path = Path(cfg.get('credentials_file', str(state / 'cloudflare.env')))
    if path.exists():
        env.update(secret.env_values(secret.read_private(path)))
    token, account = env.get('CLOUDFLARE_API_TOKEN'), env.get('CLOUDFLARE_ACCOUNT_ID')
    if not token or not account:
        raise ValueError('Cloudflare credentials missing. Use request-secret to save CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in ' + str(path))
    if not re.fullmatch('[a-fA-F0-9]{32}', account):
        raise ValueError('Cloudflare account ID must be 32 hexadecimal characters.')
    return env


def run_wrangler(state: Path, site: Path, worker: str, env: dict) -> None:
    # Pin the locally installed CLI; no downloads or surprise upgrades during deployment.
    cli = state / 'tools/node_modules/.bin/wrangler'
    if not cli.exists():
        raise ValueError('Install Wrangler: npm install --prefix ' + str(state / 'tools') + ' wrangler@4')
    with tempfile.TemporaryDirectory(prefix='wrangler-', dir=state) as tmp:
        config = Path(tmp) / 'wrangler.json'
        config.write_text(json.dumps({'name': worker, 'account_id': env['CLOUDFLARE_ACCOUNT_ID'], 'compatibility_date': '2026-10-07', 'workers_dev': True, 'assets': {'directory': str(site), 'not_found_handling': '404-page'}}))
        result = subprocess.run([str(cli), 'deploy', '--config', str(config)], env={**env, 'WRANGLER_SEND_METRICS': 'false'}, capture_output=True, text=True)
    if result.returncode:
        safe = result.stdout + result.stderr
        for value in (env['CLOUDFLARE_API_TOKEN'], env['CLOUDFLARE_ACCOUNT_ID']):
            safe = safe.replace(value, '[redacted]')
        raise ValueError('Cloudflare deploy failed:\n' + safe)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, nargs='?')
    parser.add_argument('--slug')
    parser.add_argument('--delete', metavar='SLUG', help='Remove a published report. Its slug stays reserved.')
    parser.add_argument('--no-comments', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Stage and validate against the local checkout; claim, push and upload nothing.')
    parser.add_argument('--migrate-from', help='Existing URL of this same report; preserve its comment reservation.')
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    if bool(args.source) == bool(args.delete):
        parser.error('Give a source to publish, or --delete SLUG.')
    if args.delete:
        slug, source = args.delete, None
    else:
        source = args.source.resolve()
        if not source.exists() or (source.is_dir() and not (source / 'index.html').is_file()):
            parser.error('Source must be an HTML file or a directory containing index.html.')
        if source.is_file() and source.suffix.lower() not in ('.html', '.htm'):
            parser.error('File source must be HTML.')
        slug = args.slug or re.sub('[^a-z0-9]+', '-', source.stem.lower()).strip('-')
    if not re.fullmatch('[a-z0-9][a-z0-9-]{2,89}', slug):
        parser.error('Use a descriptive --slug with 3–90 lowercase letters, digits, or hyphens.')
    state = args.state_dir.expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfgpath = state / 'config.json'
    cfg = json.loads(cfgpath.read_text()) if cfgpath.exists() else {}
    worker = cfg.get('worker_name', 'lukas-reports')
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', worker):
        parser.error('Invalid configured Worker name.')

    def apply(site: Path, reservation: str, url: str) -> dict:
        """Put this one change into the tree and return the updated registry."""
        records = load_records(site)
        record = records.get(slug, {})
        if record.get('removed'):
            raise ValueError('This slug belonged to a removed report and stays reserved; choose another slug.')
        if source is None:
            if slug not in records:
                raise ValueError('No published report has this slug.')
            shutil.rmtree(site / slug)
            records[slug] = {**record, 'removed': True}
        else:
            if (site / slug).exists():
                if slug not in records:
                    raise ValueError('Existing folder is not a managed report; choose another slug.')
                shutil.rmtree(site / slug)
            prepare(source, site / slug, slug, not args.no_comments)
            records[slug] = {'url': url, 'reservation_url': reservation or record.get('reservation_url') or url}
        write_site_files(site, records)
        check_tree(site, records)
        return records

    with (state / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.dry_run:
            with tempfile.TemporaryDirectory(prefix='stage-', dir=state) as tmp:
                site = Path(tmp) / 'site'
                current = state / 'repo/site'
                shutil.copytree(current, site) if current.exists() else site.mkdir()
                apply(site, '', 'dry-run')
                print(f'Dry run: {slug}/; {len(list(site.rglob("*")))} entries; siblings preserved; no claim, push or upload.')
            return
        env = credentials(state, cfg)
        token, account = env['CLOUDFLARE_API_TOKEN'], env['CLOUDFLARE_ACCOUNT_ID']
        subdomain = json.loads(fetch(f'https://api.cloudflare.com/client/v4/accounts/{account}/workers/subdomain', token))
        if not subdomain.get('success') or not subdomain.get('result', {}).get('subdomain'):
            raise ValueError('Enable an account workers.dev subdomain in the Cloudflare dashboard first.')
        base = f'https://{worker}.{subdomain["result"]["subdomain"]}.workers.dev'
        url = base + '/' + slug + '/'
        repo = sync_repo(state, cfg)
        site = repo / 'site'
        if not load_records(site):
            try:
                fetch(base + '/')
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
            else:
                raise ValueError('Worker already serves a site but the shared site repository is empty. Import the site tree first or choose a new worker_name.')
        reservation = args.migrate_from or ''
        if source is not None:
            if args.migrate_from and 'hc-doc: ' + slug not in fetch(args.migrate_from).decode():
                raise ValueError('Migration URL does not identify this report.')
            try:
                live = fetch(url).decode()
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
            else:
                if 'hc-doc: ' + slug not in live:
                    raise ValueError('Live path contains an unrecognized document; choose another slug.')
        # A rejected push means another machine published first: take its tree and re-apply this one change.
        for attempt in range(2):
            records = apply(site, reservation, url)
            if source is not None and not args.no_comments and attempt == 0:
                held = records[slug]['reservation_url']
                check = [str(SKILL.parent / 'html-comments/bin/check-slug.sh'), slug, '--allow-existing-at', held]
                if held == url:
                    check += ['--claim', url]
                subprocess.run(check, check=True)
            git(repo, 'add', '-A')
            if git(repo, 'status', '--porcelain'):
                git(repo, '-c', 'user.name=deploy-html', '-c', 'user.email=deploy-html@localhost', 'commit', '--quiet', '-m', ('Remove ' if source is None else 'Publish ') + slug)
            pushed = subprocess.run(['git', '-C', str(repo), 'push', '--quiet', 'origin', 'HEAD:main'], capture_output=True, text=True)
            if not pushed.returncode:
                break
            if attempt:
                raise ValueError('Could not push to the shared site repository:\n' + pushed.stderr.strip())
            sync_repo(state, cfg)
        # Deploy the pushed tree. If the repository moved on meanwhile, deploy again so the newest tree ends up live.
        for _ in range(3):
            deployed = git(repo, 'rev-parse', 'HEAD')
            try:
                run_wrangler(state, site, worker, env)
            except ValueError as error:
                raise ValueError(str(error) + '\nThe change is in the shared repository and goes live with the next successful deploy.')
            sync_repo(state, cfg)
            check_tree(site, load_records(site))
            if git(repo, 'rev-parse', 'HEAD') == deployed:
                break
        cfg['base_url'] = base
        cfgpath.write_text(json.dumps(cfg, indent=2) + '\n')
        if source is None:
            print('Removed: ' + url)
            return
        expected = hashlib.sha256((site / slug / 'index.html').read_bytes()).digest()
        for attempt in range(6):
            try:
                if hashlib.sha256(fetch(url)).digest() == expected:
                    print('Live: ' + url)
                    return
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(3)
        raise ValueError('Deployed, but fresh content could not be verified at ' + url)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
