"""Exercise the full-tree upload boundary without Cloudflare calls."""
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


class PublishTests(unittest.TestCase):
    def exercise(self, failure=False):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            state = base / 'state'
            root = state / 'site'
            (root / 'older-report').mkdir(parents=True)
            (root / 'older-report/index.html').write_text('prior report')
            (root / 'reports.json').write_text(json.dumps({'older-report': {'url': 'old'}}))
            cli = state / 'tools/node_modules/.bin/wrangler'
            cli.parent.mkdir(parents=True)
            cli.touch()
            source = base / 'report.html'
            source.write_text('<body>new report</body>')
            uploaded = {}

            def run(command, **kwargs):
                config = json.loads(Path(command[-1]).read_text())
                assets = Path(config['assets']['directory'])
                uploaded['prior'] = (assets / 'older-report/index.html').read_text()
                uploaded['new'] = (assets / 'new-report/index.html').read_bytes()
                return subprocess.CompletedProcess(command, int(failure), '', 'mock failed' if failure else '')

            def fetch(url, token=''):
                if url.endswith('/workers/subdomain'):
                    return b'{"success":true,"result":{"subdomain":"test"}}'
                if 'new' not in uploaded:
                    raise urllib.error.HTTPError(url, 404, 'absent', None, None)
                return uploaded['new']

            argv = ['deploy', str(source), '--state-dir', str(state), '--slug', 'new-report', '--no-comments']
            with patch.object(sys, 'argv', argv), patch.dict(os.environ, {'CLOUDFLARE_API_TOKEN': 'dummy', 'CLOUDFLARE_ACCOUNT_ID': 'a'*32}), patch.object(deploy, 'fetch', fetch), patch.object(deploy.subprocess, 'run', run):
                if failure:
                    with self.assertRaisesRegex(ValueError, 'deploy failed'):
                        deploy.main()
                    self.assertFalse((root / 'new-report').exists())
                else:
                    deploy.main()
                    self.assertIn('new-report', json.loads((root / 'reports.json').read_text()))
                self.assertEqual((root / 'older-report/index.html').read_text(), 'prior report')
                self.assertEqual(uploaded['prior'], 'prior report')
                self.assertEqual(source.read_text(), '<body>new report</body>')

    def test_preserves_siblings(self):
        self.exercise()

    def test_failed_upload_keeps_state(self):
        self.exercise(True)

    def test_rejects_other_document_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'source'
            source.mkdir()
            (source / 'index.html').write_text('<!-- hc-doc: other-report -->')
            with self.assertRaisesRegex(ValueError, 'different document'):
                deploy.prepare(source, base / 'target', 'new-report', False)
            (source / 'secret').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                deploy.prepare(source, base / 'other', 'new-report', False)


if __name__ == '__main__':
    unittest.main()
