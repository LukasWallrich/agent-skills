"""Exercise publishing against a local git remote, without Cloudflare calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location('deploy', Path(__file__).resolve().parents[1] / 'bin/deploy-cloudflare.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

ENV = {'CLOUDFLARE_API_TOKEN': 'dummy', 'CLOUDFLARE_ACCOUNT_ID': 'a' * 32}


class Site:
    """A bare remote holding one report, plus helpers to publish to it from separate machines."""

    def __init__(self, base: Path):
        self.base = base
        self.remote = base / 'remote.git'
        subprocess.run(['git', 'init', '--quiet', '--bare', '-b', 'main', str(self.remote)], check=True)
        seed = base / 'seed'
        subprocess.run(['git', 'clone', '--quiet', str(self.remote), str(seed)], check=True, capture_output=True)
        (seed / 'site/older-report').mkdir(parents=True)
        (seed / 'site/older-report/index.html').write_text('prior report')
        (seed / 'site/reports.json').write_text(json.dumps({'older-report': {'url': 'old', 'reservation_url': 'old'}}))
        deploy.git(seed, 'add', '-A')
        deploy.git(seed, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--quiet', '-m', 'seed')
        deploy.git(seed, 'push', '--quiet', 'origin', 'HEAD:main')
        self.uploads = []      # the file tree of every Cloudflare upload, in order
        self.before_upload = None

    def machine(self, name: str) -> Path:
        state = self.base / name
        state.mkdir()
        (state / 'config.json').write_text(json.dumps({'site_repo': str(self.remote)}))
        return state

    def tree(self) -> dict:
        check = self.base / f'check-{len(list(self.base.iterdir()))}'
        subprocess.run(['git', 'clone', '--quiet', str(self.remote), str(check)], check=True, capture_output=True)
        return {str(p.relative_to(check / 'site')): p.read_text() for p in (check / 'site').rglob('*') if p.is_file()}

    def run(self, state: Path, *argv: str, fail: bool = False) -> None:
        def wrangler(state_, site, worker, env):
            if self.before_upload:
                hook, self.before_upload = self.before_upload, None
                hook()
            if fail:
                raise ValueError('Cloudflare deploy failed:\nmock')
            self.uploads.append({str(p.relative_to(site)): p.read_bytes() for p in site.rglob('*') if p.is_file()})

        def fetch(url, token=''):
            if url.endswith('/workers/subdomain'):
                return b'{"success":true,"result":{"subdomain":"test"}}'
            path = url.split('.workers.dev/')[1] + 'index.html'
            if not self.uploads or path not in self.uploads[-1]:
                raise urllib.error.HTTPError(url, 404, 'absent', None, None)
            return self.uploads[-1][path]

        with patch.object(sys, 'argv', ['deploy', *argv, '--state-dir', str(state)]), patch.dict(os.environ, ENV), \
                patch.object(deploy, 'fetch', fetch), patch.object(deploy, 'run_wrangler', wrangler):
            deploy.main()


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.site = Site(self.base)
        self.source = self.base / 'report.html'
        self.source.write_text('<body>new report</body>')

    def tearDown(self):
        self.temp.cleanup()

    def test_publish_keeps_siblings_and_pushes(self):
        self.site.run(self.site.machine('mac'), str(self.source), '--slug', 'new-report', '--no-comments')
        tree = self.site.tree()
        self.assertEqual(tree['older-report/index.html'], 'prior report')
        self.assertIn('new report', tree['new-report/index.html'])
        self.assertEqual(self.site.uploads[-1]['older-report/index.html'], b'prior report')
        self.assertEqual(self.source.read_text(), '<body>new report</body>')

    def test_two_machines_publish_independently(self):
        mac, box = self.site.machine('mac'), self.site.machine('box')
        other = self.base / 'other.html'
        other.write_text('<body>box report</body>')
        self.site.run(mac, str(self.source), '--slug', 'new-report', '--no-comments')
        # The box has never seen the Mac's report; its publish must keep it.
        self.site.run(box, str(other), '--slug', 'box-report', '--no-comments')
        self.assertEqual(set(self.site.uploads[-1]) >= {'new-report/index.html', 'box-report/index.html', 'older-report/index.html'}, True)

    def test_redeploys_when_the_repository_moved_during_upload(self):
        mac, box = self.site.machine('mac'), self.site.machine('box')
        other = self.base / 'other.html'
        other.write_text('<body>box report</body>')
        # While the Mac uploads, the box publishes. The Mac must end with the newest tree live.
        self.site.before_upload = lambda: self.site.run(box, str(other), '--slug', 'box-report', '--no-comments')
        self.site.run(mac, str(self.source), '--slug', 'new-report', '--no-comments')
        self.assertIn('box-report/index.html', self.site.uploads[-1])
        self.assertIn('new-report/index.html', self.site.uploads[-1])

    def test_failed_upload_leaves_the_change_in_the_repository(self):
        with self.assertRaisesRegex(ValueError, 'next successful deploy'):
            self.site.run(self.site.machine('mac'), str(self.source), '--slug', 'new-report', '--no-comments', fail=True)
        self.assertIn('new-report/index.html', self.site.tree())
        self.assertEqual(self.site.uploads, [])

    def test_delete_leaves_a_reserved_slug(self):
        mac = self.site.machine('mac')
        self.site.run(mac, '--delete', 'older-report')
        tree = self.site.tree()
        self.assertNotIn('older-report/index.html', tree)
        self.assertTrue(json.loads(tree['reports.json'])['older-report']['removed'])
        self.assertNotIn('older-report', tree['index.html'])
        with self.assertRaisesRegex(ValueError, 'stays reserved'):
            self.site.run(mac, str(self.source), '--slug', 'older-report', '--no-comments')

    def test_refuses_an_inconsistent_tree(self):
        site = self.base / 'broken'
        (site / 'unlisted').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "missing: \\['gone'\\]; unlisted: \\['unlisted'\\]"):
            deploy.check_tree(site, {'gone': {'url': 'u'}, 'removed-one': {'removed': True}})

    def test_rejects_other_document_and_symlinks(self):
        source = self.base / 'source'
        source.mkdir()
        (source / 'index.html').write_text('<!-- hc-doc: other-report -->')
        with self.assertRaisesRegex(ValueError, 'different document'):
            deploy.prepare(source, self.base / 'target', 'new-report', False)
        (source / 'secret').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            deploy.prepare(source, self.base / 'other', 'new-report', False)


if __name__ == '__main__':
    unittest.main()
