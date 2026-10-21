import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('background_hook', Path(__file__).resolve().parents[1] / 'skills/skr-codex-auto-title/assets/first_completion.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
RealRpc = m.Rpc


class FakeRpc:
    def __init__(self):
        self.title = '旧标题'
        self.answer = {'completed': True, 'title': '商品管控 | 解释幂等性', 'evidence': '已交付解释及例子'}
        self.assessments = 0
        self.renames = 0
        self.fail_rename = False
        self.mismatch = False
        self.gate = None
        self.disabled_callback = None

    def assess(self, packet, modules):
        self.assessments += 1
        if self.gate:
            self.gate[0].set()
            self.gate[1].wait(3)
        if self.disabled_callback:
            self.disabled_callback()
        return dict(self.answer), {'thread_id': 'ephemeral-luna', 'model': 'gpt-6-luna', 'effort': 'low', 'ephemeral': True}

    def call(self, method, params):
        if method == 'thread/read':
            return {'thread': {'name': '错误读回' if self.mismatch else self.title}}
        if method == 'thread/name/set':
            self.renames += 1
            if self.fail_rename:
                raise RuntimeError('official API unavailable')
            self.title = params['name']
            return {}
        raise AssertionError(method)

    def close(self):
        pass


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'runtime/skr-codex-auto-title'
        self.directory.mkdir(parents=True)
        self.settings = {'enabled': True, 'activated_at': '2026-10-09T00:00:00+00:00',
                         'excluded_session_ids': ['installation'], 'module_guidance': '商品管控、项目配置'}
        m.atomic_json(self.directory / 'settings.json', self.settings)
        m.atomic_json(self.root / 'runtime/skr-codex-auto-title-install/backend.json', {'codex': 'unused', 'codex_home': str(self.root)})
        self.transcript = self.root / 'test.jsonl'
        self.transcript.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'test-session',
             'source': 'vscode', 'cwd': str(self.root), 'timestamp': '2026-10-09T01:00:00Z'}}) + '\n')
        with self.transcript.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '请解释幂等性'}}) + '\n')
        self.event = {'session_id': 'test-session', 'transcript_path': str(self.transcript),
                      'cwd': str(self.root), 'hook_event_name': 'Stop', 'turn_id': 'turn-1',
                      'stop_hook_active': False, 'last_assistant_message': '已交付解释'}
        self.naming = m.Naming(self.root)
        self.rpc = FakeRpc()
        sleep = patch.object(m.time, 'sleep')
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        fake = patch.object(m, 'Rpc', return_value=self.rpc)
        fake.start()
        self.addCleanup(fake.stop)

    def state(self):
        return json.loads((self.directory / 'test-session.json').read_text())

    def test_should_name_without_any_prompt_or_main_model_continuation(self):
        self.assertEqual(self.naming.hook(self.event), {})
        self.assertTrue(self.state()['done'])
        self.assertEqual(self.state()['title'], self.rpc.title)
        self.assertEqual(self.rpc.assessments, 1)
        self.assertEqual(self.rpc.renames, 1)
        self.assertTrue((self.directory / 'evidence/test-session/rename-receipt.json').exists())

    def test_should_defer_question_then_name_first_actual_delivery_once(self):
        self.rpc.answer = {'completed': False, 'title': '', 'evidence': '只有追问'}
        self.assertEqual(self.naming.hook(self.event), {})
        self.assertNotIn('done', self.state())
        self.assertNotIn('pending', self.state())
        self.assertEqual(self.rpc.renames, 0)
        self.rpc.answer = {'completed': True, 'title': '商品管控 | 解释幂等性', 'evidence': '已交付解释'}
        self.naming.hook(dict(self.event, turn_id='turn-2'))
        before = (self.directory / 'test-session.json').read_bytes()
        self.naming.hook(dict(self.event, turn_id='turn-3'))
        self.assertEqual((self.directory / 'test-session.json').read_bytes(), before)
        self.assertEqual(self.rpc.assessments, 2)
        self.assertEqual(self.rpc.renames, 1)
        self.assertEqual(self.state()['first_completed_turn'], 'turn-2')

    def test_should_keep_pending_and_retry_without_another_luna_after_api_failure(self):
        self.rpc.fail_rename = True
        self.naming.hook(self.event)
        self.assertNotIn('done', self.state())
        pending = self.state()['pending']
        self.rpc.fail_rename = False
        self.naming.hook(dict(self.event, turn_id='turn-2'))
        self.assertTrue(self.state()['done'])
        self.assertEqual(self.state()['title'], pending['title'])
        self.assertEqual(self.state()['first_completed_turn'], 'turn-1')
        self.assertEqual(self.rpc.assessments, 1)

    def test_should_not_confirm_mismatched_readback_and_recover_already_applied_title(self):
        self.rpc.mismatch = True
        self.naming.hook(self.event)
        self.assertNotIn('done', self.state())
        self.assertIn('pending', self.state())
        self.rpc.mismatch = False
        self.naming.hook(self.event)
        self.assertTrue(self.state()['done'])
        self.assertEqual(self.rpc.renames, 3)
        self.assertEqual(self.rpc.assessments, 1)

    def test_should_serialize_duplicate_workers_and_rename_only_once(self):
        entered, release = threading.Event(), threading.Event()
        self.rpc.gate = (entered, release)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.naming.hook, self.event)
            self.assertTrue(entered.wait(3))
            second = pool.submit(m.Naming(self.root).hook, self.event)
            self.assertEqual(second.result(3), {})
            release.set()
            self.assertEqual(first.result(3), {})
        self.assertEqual(self.rpc.assessments, 1)
        self.assertEqual(self.rpc.renames, 1)
        self.assertTrue(self.state()['done'])

    def test_should_preserve_old_done_state_byte_for_byte(self):
        original = b'{"done":true,"title":"old custom title"}'
        (self.directory / 'test-session.json').write_bytes(original)
        self.assertEqual(self.naming.hook(self.event), {})
        self.assertEqual((self.directory / 'test-session.json').read_bytes(), original)
        self.assertEqual(self.rpc.assessments, 0)

    def test_should_honor_disable_during_luna_inference(self):
        def disable():
            m.atomic_json(self.directory / 'settings.json', dict(self.settings, enabled=False))
        self.rpc.disabled_callback = disable
        self.naming.hook(self.event)
        self.assertEqual(self.rpc.renames, 0)
        self.assertNotIn('done', self.state())

    def test_should_stop_quietly_on_invalid_model_result(self):
        self.rpc.answer = {'completed': True, 'title': '错误标题', 'evidence': '说明'}
        self.assertEqual(self.naming.hook(self.event), {})
        self.assertEqual(self.rpc.renames, 0)
        self.assertNotIn('done', self.state())
        self.assertTrue(list((self.directory / 'logs').glob('*.jsonl')))
        self.sleep.assert_not_called()

    def test_should_include_rpc_stage_and_elapsed_in_timeout(self):
        rpc = RealRpc.__new__(RealRpc)
        rpc.deadline = m.time.monotonic() - 1
        rpc.phase = 'assessment/turn-completed'
        rpc.phase_started = m.time.monotonic() - 25
        with self.assertRaisesRegex(TimeoutError, r'phase=assessment/turn-completed, elapsed_s=25'):
            rpc.receive(rpc.deadline)

    def test_should_retry_timeouts_with_backoff_then_complete(self):
        original = self.rpc.assess
        calls = []
        def assess(packet, modules):
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError('assessment/turn-completed')
            return original(packet, modules)
        self.rpc.assess = assess
        self.naming.hook(self.event)
        self.assertTrue(self.state()['done'])
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 5])
        errors = [json.loads(line) for line in next((self.directory / 'logs').glob('*.jsonl')).read_text().splitlines() if json.loads(line)['status'] == 'error']
        self.assertEqual([e['attempt'] for e in errors], [1, 2])

    def test_should_stop_after_three_failures_keep_job_and_pending(self):
        self.rpc.fail_rename = True
        self.naming.hook(self.event)
        self.assertEqual(self.rpc.renames, 3)
        self.assertEqual(self.rpc.assessments, 1)
        self.assertNotIn('done', self.state())
        self.assertIn('pending', self.state())
        self.assertEqual(len(list(self.naming.jobs('test-session').glob('*.json'))), 1)
        errors = [json.loads(line) for line in next((self.directory / 'logs').glob('*.jsonl')).read_text().splitlines()]
        self.assertEqual(errors[-1]['phase'], 'rename')
        self.assertIsNone(errors[-1]['retry_in_s'])

    def test_should_read_back_after_lost_rename_response_without_repeating_mutation(self):
        original = self.rpc.call
        def call(method, params):
            result = original(method, params)
            if method == 'thread/name/set':
                raise TimeoutError('response lost after mutation')
            return result
        self.rpc.call = call
        self.naming.hook(self.event)
        self.assertTrue(self.state()['done'])
        self.assertEqual(self.rpc.renames, 1)
        self.assertEqual(self.rpc.assessments, 1)

    def test_should_honor_disable_before_later_retry(self):
        self.rpc.fail_rename = True
        self.naming.hook(self.event)
        m.atomic_json(self.directory / 'settings.json', dict(self.settings, enabled=False))
        self.rpc.fail_rename = False
        m.Naming(self.root).hook(dict(self.event, turn_id='turn-2'))
        self.assertEqual(self.rpc.renames, 3)
        self.assertNotIn('done', self.state())

    def test_should_start_new_job_while_idle_worker_closes_connection(self):
        closing, release = threading.Event(), threading.Event()
        count = 0
        def close():
            nonlocal count
            count += 1
            if count == 1:
                closing.set()
                release.wait(3)
        self.rpc.close = close
        self.rpc.answer = {'completed': False, 'title': '', 'evidence': '只追问'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.naming.hook, self.event)
            self.assertTrue(closing.wait(3))
            self.rpc.answer = {'completed': True, 'title': '商品管控 | 解释幂等性', 'evidence': '已解释'}
            second = pool.submit(m.Naming(self.root).hook, dict(self.event, turn_id='turn-2'))
            try:
                self.assertEqual(second.result(3), {})
                self.assertTrue(self.state()['done'])
            finally:
                release.set()
            self.assertEqual(first.result(3), {})
        self.assertEqual(self.rpc.renames, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
