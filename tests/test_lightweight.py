import json
import concurrent.futures
import importlib.util
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import unittest
import venv

import test_background as background

m = background.m


class LightweightTests(unittest.TestCase):
    setUp = background.BackgroundTests.setUp
    state = background.BackgroundTests.state

    def test_queued_next_stop_runs_after_inflight_timeout_without_third_stop(self):
        entered, release = threading.Event(), threading.Event()
        original = self.rpc.assess
        calls = []
        def assess(packet, modules):
            calls.append(packet['final_response'])
            if len(calls) == 1:
                entered.set()
                release.wait(3)
                raise TimeoutError('first turn timed out')
            return original(packet, modules)
        self.rpc.assess = assess
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.naming.hook, self.event)
            self.assertTrue(entered.wait(3))
            second = pool.submit(m.Naming(self.root).hook, dict(self.event, turn_id='turn-2', last_assistant_message='最新结果已交付'))
            self.assertEqual(second.result(3), {})
            release.set()
            self.assertEqual(first.result(3), {})
        self.assertTrue(self.state()['done'])
        self.assertEqual(calls, ['已交付解释', '最新结果已交付'])
        self.assertEqual(self.rpc.renames, 1)
        self.sleep.assert_not_called()

    def test_large_first_request_no_longer_blocks_naming(self):
        rows = self.transcript.read_text().splitlines()
        row = json.loads(rows[1])
        row['payload']['message'] = '请分析库存扣减。' + '说明' * 18000 + '最后给出结论。'
        rows[1] = json.dumps(row)
        self.transcript.write_text('\n'.join(rows) + '\n')
        packet = self.naming.packet(self.event)
        self.assertLess(len(json.dumps(packet, ensure_ascii=False)), 4000)
        self.assertEqual(packet['recent_user_requests'], [])
        self.naming.hook(self.event)
        self.assertTrue(self.state()['done'])

    def test_reply_excerpt_preserves_end_and_middle_caution(self):
        response = '已经完成分析。' + 'a' * 6000 + '\n数据库变更尚未执行，需要用户确认。\n' + 'b' * 6000 + '\nStill blocked; approval required.'
        packet = self.naming.packet(dict(self.event, last_assistant_message=response))
        self.assertTrue(packet['final_response'].endswith('Still blocked; approval required.'))
        self.assertTrue(any('数据库变更尚未执行' in item for item in packet['caution_excerpts']))
        self.assertLessEqual(len(packet['final_response']), 1600)

    def test_only_latest_nonduplicate_request_is_sent(self):
        with self.transcript.open('a') as stream:
            for text in ('第二个问题', '最新的需求'):
                stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': text}}) + '\n')
        packet = self.naming.packet(self.event)
        self.assertEqual(packet['first_user_request'], '请解释幂等性')
        self.assertEqual(packet['recent_user_requests'], ['最新的需求'])

    def test_pending_rename_does_not_need_new_conversation_packet(self):
        self.rpc.fail_rename = True
        self.naming.hook(self.event)
        self.rpc.fail_rename = False
        # A missing user request must not prevent retrying a title already generated.
        self.transcript.write_text(self.transcript.read_text().splitlines()[0] + '\n')
        self.naming.hook(dict(self.event, turn_id='turn-2', last_assistant_message='x' * 100000))
        self.assertTrue(self.state()['done'])
        self.assertEqual(self.rpc.assessments, 1)

    def test_model_timeout_attempts_once_then_next_stop_recovers(self):
        original = self.rpc.assess
        attempts = []
        def timeout(packet, modules):
            attempts.append(packet)
            raise TimeoutError('assessment timeout')
        self.rpc.assess = timeout
        self.naming.hook(self.event)
        self.assertEqual(len(attempts), 1)
        self.sleep.assert_not_called()
        self.assertFalse(self.state().get('done', False))
        self.rpc.assess = original
        self.naming.hook(dict(self.event, turn_id='turn-2'))
        self.assertTrue(self.state()['done'])

    def test_module_guidance_is_bounded(self):
        self.naming.settings['module_guidance'] = '模块' * 10000
        received = []
        original = self.rpc.assess
        def assess(packet, modules):
            received.append(modules)
            return original(packet, modules)
        self.rpc.assess = assess
        self.naming.hook(self.event)
        self.assertLessEqual(len(received[0]), 400)

    def test_newer_stop_uses_newer_reply_after_timeout(self):
        original = self.rpc.assess
        self.rpc.assess = lambda *args: (_ for _ in ()).throw(TimeoutError('timeout'))
        self.naming.hook(self.event)
        received = []
        def assess(packet, modules):
            received.append(packet)
            return original(packet, modules)
        self.rpc.assess = assess
        self.naming.hook(dict(self.event, turn_id='turn-2', last_assistant_message='已完成最新修复。'))
        self.assertEqual(received[0]['final_response'], '已完成最新修复。')


class InterpreterPortabilityTests(unittest.TestCase):
    def test_command_still_runs_after_downloading_environment_is_removed(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='portable naming ') as temporary:
            root = Path(temporary)
            download = root / 'download'
            skill = download / 'skill'
            shutil.copytree(repository / 'skills/skr-codex-auto-title', skill)
            environment = download / '.venv'
            venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
            installed = root / 'project/.codex/hooks/first_completion.py'
            installed.parent.mkdir(parents=True)
            shutil.copy2(skill / 'assets/first_completion.py', installed)
            code = ('import importlib.util,sys; '
                    's=importlib.util.spec_from_file_location("installer",sys.argv[1]); '
                    'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                    'print(m.runtime_command(sys.argv[2]))')
            output = subprocess.check_output([str(environment / 'bin/python'), '-c', code,
                                              str(skill / 'scripts/install.py'), str(installed)], text=True)
            command = shlex.split(output.strip())
            self.assertFalse(Path(command[0]).is_relative_to(download))
            shutil.rmtree(download)
            result = subprocess.run(command, input='not-json', text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
