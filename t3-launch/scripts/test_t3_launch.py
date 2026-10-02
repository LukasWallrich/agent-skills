import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('t3_launch', Path(__file__).with_name('t3_launch.py'))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class FakeClient:
    def __init__(self, root):
        self.projects = [{'id': 'project', 'title': 'Scratch', 'workspaceRoot': str(root)}]
        self.threads = {}
        self.commands = []
        self.stored_mode_override = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def shell(self):
        return {'projects': self.projects, 'threads': list(self.threads.values())}

    def thread(self, thread_id):
        return copy.deepcopy(self.threads[thread_id])

    def dispatch(self, command):
        self.commands.append(command)
        if command['type'] == 'thread.create':
            self.threads[command['threadId']] = {
                'id': command['threadId'], 'projectId': command['projectId'],
                'title': command['title'], 'modelSelection': command['modelSelection'],
                'runtimeMode': self.stored_mode_override or command['runtimeMode'],
                'messages': [],
            }
        elif command['type'] == 'thread.turn.start':
            self.threads[command['threadId']]['messages'].append({
                'id': command['message']['messageId'], 'role': 'user',
            })


class AccessModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('Reply OK without using tools.')
        self.client = FakeClient(self.root)
        self.argv = ['t3_launch.py', 'launch', str(self.root), '--title', 'Access check',
                     '--prompt-file', str(self.prompt), '--receipt', str(self.root / 'receipt.json'),
                     '--model', 'gpt-6.1-sol']

    def run_cli(self, extra):
        output = io.StringIO()
        with patch('sys.argv', self.argv + extra), patch.object(launcher, 'Client', return_value=self.client), contextlib.redirect_stdout(output):
            launcher.main()
        return json.loads(output.getvalue())

    def test_omitting_mode_fails_before_server_access(self):
        with patch('sys.argv', self.argv), patch.object(launcher, 'Client') as client, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            launcher.main()
        self.assertEqual(error.exception.code, 2)
        client.assert_not_called()

    def test_parent_mode_reaches_thread_and_turn_and_replay_is_idempotent(self):
        for mode in ['full-access', 'approval-required', 'auto-accept-edits', 'auto']:
            with self.subTest(mode=mode):
                self.client = FakeClient(self.root)
                receipt = self.root / f'{mode}.json'
                extra = ['--runtime-mode', mode, '--receipt', str(receipt)]
                result = self.run_cli(extra)
                self.assertEqual(result['runtimeMode'], mode)
                self.assertEqual([c['runtimeMode'] for c in self.client.commands], [mode, mode])
                self.assertEqual(json.loads(receipt.read_text())['identity']['runtimeMode'], mode)
                replay = self.run_cli(extra)
                self.assertEqual(replay['userMessageCount'], 1)
                self.assertEqual(len(self.client.commands), 2)

    def test_server_mode_mismatch_prevents_sending_prompt(self):
        self.client.stored_mode_override = 'approval-required'
        with self.assertRaisesRegex(RuntimeError, 'access mode differs'):
            self.run_cli(['--runtime-mode', 'full-access'])
        self.assertEqual([c['type'] for c in self.client.commands], ['thread.create'])


if __name__ == '__main__':
    unittest.main()
