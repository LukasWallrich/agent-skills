#!/usr/bin/env python3
"""Control the running local T3 server; never edit its database directly."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class Client:
    def __init__(self, home, app):
        self.home = Path(home).expanduser().resolve()
        runtime = json.loads((self.home / 'userdata/server-runtime.json').read_text())
        self.origin = runtime['origin']
        parsed = urllib.parse.urlparse(self.origin)
        if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Only the running local loopback server is supported.')
        os.kill(runtime['pid'], 0)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.cli = [str(Path(app) / 'Contents/MacOS' / Path(app).stem),
                    str(Path(app) / 'Contents/Resources/app.asar/apps/server/dist/bin.mjs')]
        self.env = dict(os.environ, ELECTRON_RUN_AS_NODE='1')
        self.session = None

    def cli_run(self, args):
        result = subprocess.run(self.cli + args + ['--base-dir', str(self.home)],
                                env=self.env, capture_output=True, text=True, timeout=25)
        if result.returncode:
            # CLI output may contain authentication values; do not forward it.
            raise RuntimeError('Bundled T3 CLI failed; check app version and command support.')
        return result.stdout

    def __enter__(self):
        # Use the same supported auth flow as t3 project; ephemeral and revoked.
        self.session = json.loads(self.cli_run(['auth', 'session', 'issue',
                                               '--ttl', '5m', '--label', 't3-launch', '--json']))
        return self

    def __exit__(self, *_):
        if self.session:
            try:
                self.cli_run(['auth', 'session', 'revoke', self.session['sessionId']])
            except Exception:
                print('Warning: token revocation failed; the session expires within five minutes.', file=sys.stderr)

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.origin + path, data=data,
                                         headers={'Authorization': 'Bearer ' + self.session['token'],
                                                  'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                code = json.loads(error.read()).get('code', 'unknown')
            except Exception:
                code = 'unknown'
            raise RuntimeError(f'T3 HTTP {error.code} ({code}); no automatic retry.') from None

    def shell(self):
        return self.request('/api/orchestration/shell')

    def dispatch(self, command):
        return self.request('/api/orchestration/dispatch', command)

    def thread(self, thread_id):
        return self.request('/api/orchestration/threads/' + urllib.parse.quote(thread_id) + '?turnLimit=1')['thread']


def ensure_project(client, root, title):
    matches = [p for p in client.shell()['projects']
               if p.get('deletedAt') is None and Path(p['workspaceRoot']).resolve() == root]
    if len(matches) > 1:
        raise RuntimeError('Multiple active projects match this path; resolve the ambiguity first.')
    if matches:
        return matches[0], False
    project_id = uid()
    client.dispatch({'type': 'project.create', 'commandId': uid(), 'projectId': project_id,
                     'title': title or root.name, 'workspaceRoot': str(root), 'createdAt': now()})
    project = next((p for p in client.shell()['projects'] if p['id'] == project_id), None)
    if project is None:
        raise RuntimeError('Project create returned, but project is not visible. Inspect before retrying.')
    return project, True


def launch(client, args, root, project):
    prompt = Path(args.prompt_file).read_text()
    if not prompt.strip():
        raise ValueError('Prompt file is empty.')
    selection = {'instanceId': args.instance, 'model': args.model}
    if args.effort:
        selection['options'] = [{'id': 'reasoningEffort', 'value': args.effort}]
    identity = {'projectId': project['id'], 'workspaceRoot': str(root), 'title': args.title,
                'promptSha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'modelSelection': selection, 'runtimeMode': args.runtime_mode}
    receipt_path = Path(args.receipt).resolve()
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt['identity'] != identity:
            raise ValueError('Receipt belongs to a different request; use a new receipt path.')
    else:
        receipt = {'identity': identity, 'threadId': uid(), 'commandId': uid(), 'createCommandId': uid(),
                   'messageId': uid(), 'createdAt': now(), 'state': 'prepared'}
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve identifiers before the request, so an uncertain outcome can be reconciled.
        with receipt_path.open('x') as output:
            json.dump(receipt, output, indent=2)
    existing = next((t for t in client.shell()['threads'] if t['id'] == receipt['threadId']), None)
    if not existing:
        if receipt['state'] != 'prepared':
            raise RuntimeError('A dispatched request is not visible; inspect the receipt and server before retrying.')
        branch = subprocess.run(['git', '-C', str(root), 'branch', '--show-current'],
                                capture_output=True, text=True).stdout.strip() or None
        receipt['state'] = 'creating'
        receipt_path.write_text(json.dumps(receipt, indent=2))
        client.dispatch({'type': 'thread.create', 'commandId': receipt['createCommandId'],
                         'threadId': receipt['threadId'], 'projectId': project['id'],
                         'title': args.title, 'modelSelection': selection,
                         'runtimeMode': args.runtime_mode, 'interactionMode': 'default',
                         'branch': branch, 'worktreePath': None, 'createdAt': receipt['createdAt']})
        receipt['state'] = 'created'
        receipt_path.write_text(json.dumps(receipt, indent=2))
    detail = client.thread(receipt['threadId'])
    if detail['projectId'] != project['id'] or detail['modelSelection'] != selection:
        raise RuntimeError('Stored project or model differs from the request; inspect thread.')
    if not any(m['id'] == receipt['messageId'] for m in detail['messages']):
        if detail['messages'] or receipt['state'] not in ('creating', 'created'):
            raise RuntimeError('Unexpected thread state or uncertain dispatch; inspect before retrying.')
        command = {'type': 'thread.turn.start', 'commandId': receipt['commandId'],
                   'threadId': receipt['threadId'], 'createdAt': receipt['createdAt'],
                   'message': {'messageId': receipt['messageId'], 'role': 'user',
                               'text': prompt, 'attachments': []},
                   'modelSelection': selection, 'runtimeMode': args.runtime_mode,
                   'interactionMode': 'default'}
        receipt['state'] = 'starting'
        receipt_path.write_text(json.dumps(receipt, indent=2))
        client.dispatch(command)
        detail = client.thread(receipt['threadId'])
        if not any(m['id'] == receipt['messageId'] for m in detail['messages']):
            raise RuntimeError('Dispatch returned but message is not visible; inspect before retrying.')
    if detail['modelSelection'] != selection:
        raise RuntimeError('Stored model selection differs from the request; inspect thread.')
    receipt['state'] = 'verified'
    receipt_path.write_text(json.dumps(receipt, indent=2))
    turn = detail.get('latestTurn') or {}
    session = detail.get('session') or {}
    return {'threadId': receipt['threadId'], 'title': detail['title'],
            'modelSelection': detail['modelSelection'], 'messageVerified': True,
            'userMessageCount': sum(m['role'] == 'user' for m in detail['messages']),
            'turnState': turn.get('state'), 'sessionStatus': session.get('status'),
            'receipt': str(receipt_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', default=os.environ.get('T3CODE_HOME', str(Path.home() / '.t3')))
    parser.add_argument('--app', default='/Applications/T3 Code (Alpha).app')
    subs = parser.add_subparsers(dest='action', required=True)
    inspect_parser = subs.add_parser('inspect', help='Read project metadata or one thread summary')
    inspect_parser.add_argument('--thread-id')
    for action in ('add', 'launch'):
        sub = subs.add_parser(action)
        sub.add_argument('path')
        sub.add_argument('--project-title')
        if action == 'launch':
            sub.add_argument('--title', required=True)
            sub.add_argument('--prompt-file', required=True)
            sub.add_argument('--receipt', required=True)
            sub.add_argument('--model', required=True)
            sub.add_argument('--instance', default='codex')
            sub.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh', 'max', 'ultra'])
            sub.add_argument('--runtime-mode', choices=['approval-required', 'auto-accept-edits', 'auto', 'full-access'],
                             default='approval-required')
    args = parser.parse_args()
    root = None
    if args.action != 'inspect':
        root = Path(args.path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Project path must be an existing directory.')
        if args.action == 'launch':
            if not Path(args.prompt_file).read_text().strip():
                raise ValueError('Prompt file is empty.')
    with Client(args.home, args.app) as client:
        if args.action == 'inspect':
            if args.thread_id:
                detail = client.thread(args.thread_id)
                print(json.dumps({'threadId': detail['id'], 'title': detail['title'],
                                  'modelSelection': detail['modelSelection'],
                                  'latestTurn': detail.get('latestTurn'),
                                  'sessionStatus': (detail.get('session') or {}).get('status'),
                                  'lastError': (detail.get('session') or {}).get('lastError'),
                                  'userMessagesInLatestTurn': sum(m['role'] == 'user' for m in detail['messages'])}, indent=2))
                return
            shell = client.shell()
            print(json.dumps({'origin': client.origin,
                              'projects': [{k: p[k] for k in ('id', 'title', 'workspaceRoot')}
                                           for p in shell['projects'] if p.get('deletedAt') is None]}, indent=2))
        else:
            project, created = ensure_project(client, root, args.project_title)
            result = {'projectId': project['id'], 'projectTitle': project['title'],
                      'workspaceRoot': project['workspaceRoot'], 'projectCreated': created}
            if args.action == 'launch':
                result.update(launch(client, args, root, project))
            print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Avoid tracebacks containing request headers or captured CLI credential output.
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
