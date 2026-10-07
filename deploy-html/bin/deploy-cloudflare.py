#!/usr/bin/env python3
"""Publish report folders to one assets-only Worker, preserving sibling reports."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--slug')
    parser.add_argument('--no-comments', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Stage and validate offline; do not claim or upload.')
    parser.add_argument('--migrate-from', help='Existing URL of this same report; preserve its comment reservation.')
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
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
    cfg = json.loads(cfgpath.read_text()) if cfgpath.exists() else {'worker_name': 'lukas-reports'}
    worker = cfg['worker_name']
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', worker):
        parser.error('Invalid configured Worker name.')
    root = state / 'site'
    with (state / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = json.loads((root / 'reports.json').read_text()) if (root / 'reports.json').exists() else {}
        if args.migrate_from:
            old = fetch(args.migrate_from).decode()
            if 'hc-doc: ' + slug not in old:
                raise ValueError('Migration URL does not identify this report.')
        reservation = args.migrate_from or records.get(slug, {}).get('reservation_url')
        with tempfile.TemporaryDirectory(prefix='stage-', dir=state) as tmp:
            stage = Path(tmp) / 'site'
            if root.exists():
                shutil.copytree(root, stage)
            else:
                stage.mkdir()
            destination = stage / slug
            if destination.exists():
                if slug not in records:
                    raise ValueError('Existing folder is not a managed report; choose another slug.')
                shutil.rmtree(destination)
            prepare(source, destination, slug, not args.no_comments)
            if args.dry_run:
                print(f'Dry run: {slug}/; {len(list(stage.rglob("*")))} entries; siblings preserved; no claim or upload.')
                return
            secret_path = SKILL.parent / 'request-secret/scripts/request_secret.py'
            spec = importlib.util.spec_from_file_location('request_secret', secret_path)
            secret = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(secret)
            env = dict(os.environ)
            credentials = Path(cfg.get('credentials_file', str(state / 'cloudflare.env')))
            if credentials.exists():
                env.update(secret.env_values(secret.read_private(credentials)))
            token, account = env.get('CLOUDFLARE_API_TOKEN'), env.get('CLOUDFLARE_ACCOUNT_ID')
            if not token or not account:
                raise ValueError('Cloudflare credentials missing. Use request-secret to save CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in ' + str(credentials))
            if not re.fullmatch('[a-fA-F0-9]{32}', account):
                raise ValueError('Cloudflare account ID must be 32 hexadecimal characters.')
            subdomain = json.loads(fetch(f'https://api.cloudflare.com/client/v4/accounts/{account}/workers/subdomain', token))
            if not subdomain.get('success') or not subdomain.get('result', {}).get('subdomain'):
                raise ValueError('Enable an account workers.dev subdomain in the Cloudflare dashboard first.')
            base = f'https://{worker}.{subdomain["result"]["subdomain"]}.workers.dev'
            url = base + '/' + slug + '/'
            if not root.exists():
                try:
                    fetch(base + '/')
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                else:
                    raise ValueError('Worker already serves a site but its local state is missing. Restore site state or choose a new worker_name.')
            try:
                live = fetch(url).decode()
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
            else:
                if 'hc-doc: ' + slug not in live:
                    raise ValueError('Live path contains an unrecognized document; choose another slug.')
            if not args.no_comments:
                check = [str(SKILL.parent / 'html-comments/bin/check-slug.sh'), slug, '--allow-existing-at', reservation or url]
                if not reservation:
                    check += ['--claim', url]
                subprocess.run(check, check=True)
            records[slug] = {'url': url, 'reservation_url': reservation or url}
            (stage / 'reports.json').write_text(json.dumps(records, indent=2) + '\n')
            cards = ''.join(f'<li><a href="/{html.escape(s)}/">{html.escape(s)}</a></li>' for s in sorted(records))
            (stage / 'index.html').write_text('<!doctype html><html><head><meta charset="utf-8"><title>Reports</title></head><body><h1>Reports</h1><ul>' + cards + '</ul></body></html>')
            wrangler = Path(tmp) / 'wrangler.json'
            wrangler.write_text(json.dumps({'name': worker, 'account_id': account, 'compatibility_date': '2026-10-07', 'workers_dev': True, 'assets': {'directory': str(stage), 'not_found_handling': '404-page'}}))
            env['WRANGLER_SEND_METRICS'] = 'false'
            # Pin the locally installed CLI; no downloads or surprise upgrades during deployment.
            cli = state / 'tools/node_modules/.bin/wrangler'
            if not cli.exists():
                raise ValueError('Install Wrangler: npm install --prefix ' + str(state / 'tools') + ' wrangler@4')
            result = subprocess.run([str(cli), 'deploy', '--config', str(wrangler)], env=env, capture_output=True, text=True)
            if result.returncode:
                safe = result.stdout + result.stderr
                for value in (token, account):
                    safe = safe.replace(value, '[redacted]')
                raise ValueError('Cloudflare deploy failed:\n' + safe)
            # Persist the deployed tree even if edge verification fails, so a future publish retains it.
            backup = state / 'previous-site'
            if backup.exists():
                shutil.rmtree(backup)
            if root.exists():
                root.rename(backup)
            stage.rename(root)
            cfg['base_url'] = base
            cfgpath.write_text(json.dumps(cfg, indent=2) + '\n')
            expected = hashlib.sha256((root / slug / 'index.html').read_bytes()).digest()
            for attempt in range(6):
                try:
                    if hashlib.sha256(fetch(url)).digest() == expected:
                        print('Live: ' + url)
                        return
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(3)
            raise ValueError('Uploaded and saved locally, but fresh content could not be verified at ' + url)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
