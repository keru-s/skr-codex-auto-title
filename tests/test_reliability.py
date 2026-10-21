import fcntl
import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock

import test_background as background
import test_install as installation
import test_scopes as scopes

runtime = background.m
installer = installation.m


class RetryReliabilityTests(unittest.TestCase):
    setUp = background.BackgroundTests.setUp
    state = background.BackgroundTests.state

    def test_third_assessment_attempt_succeeds(self):
        original = self.rpc.assess
        calls = []
        def assess(packet, modules):
            calls.append(packet)
            if len(calls) < 3:
                raise TimeoutError('temporary model delay')
            return original(packet, modules)
        self.rpc.assess = assess
        self.naming.hook(self.event)
        self.assertEqual(len(calls), 3)
        self.assertTrue(self.state()['done'])
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 5])

    def test_three_failures_keep_task_without_success(self):
        self.rpc.fail_rename = True
        self.naming.hook(self.event)
        self.assertEqual(self.rpc.renames, 3)
        self.assertEqual(self.rpc.assessments, 1)
        self.assertNotIn('done', self.state())
        self.assertIn('pending', self.state())
        self.assertTrue(list(self.naming.jobs('test-session').glob('*.json')))
        records = [json.loads(line) for line in next((self.directory / 'logs').glob('*.jsonl')).read_text().splitlines()]
        errors = [r for r in records if r['status'] == 'error']
        self.assertEqual([r['attempt'] for r in errors], [1, 2, 3])
        self.assertEqual([r['retry_in_s'] for r in errors], [2, 5, None])
        self.assertEqual(errors[-1]['next_trigger'], 'next_stop')

    def test_invalid_result_is_not_retried(self):
        self.rpc.answer = {'completed': True, 'title': 'invalid', 'evidence': 'bad structure'}
        self.naming.hook(self.event)
        self.assertEqual(self.rpc.assessments, 1)
        self.sleep.assert_not_called()
        self.assertNotIn('done', self.state())

    def test_completed_without_output_is_invalid_and_not_retried(self):
        rpc = background.RealRpc.__new__(background.RealRpc)
        rpc.model, rpc.effort = 'gpt-6-luna', 'low'
        rpc.call = Mock(side_effect=[{'model': 'gpt-6-luna', 'thread': {'id': 'temporary', 'ephemeral': True}},
                                    {'turn': {'id': 'turn'}}])
        rpc.events = [{'method': 'turn/completed', 'params': {'threadId': 'temporary',
                       'turnId': 'turn', 'turn': {'id': 'turn', 'status': 'completed'}}}]
        self.rpc.assess = rpc.assess
        self.naming.hook(self.event)
        self.assertEqual(rpc.call.call_count, 2)
        self.sleep.assert_not_called()
        self.assertNotIn('done', self.state())

    def test_disable_during_backoff_cancels_retry(self):
        self.rpc.fail_rename = True
        self.sleep.side_effect = lambda seconds: runtime.atomic_json(self.directory / 'settings.json', dict(self.settings, enabled=False))
        self.naming.hook(self.event)
        self.assertEqual(self.rpc.renames, 1)
        self.assertEqual(self.rpc.assessments, 1)
        self.assertNotIn('done', self.state())

    def test_lost_rename_response_recovers_by_readback_in_same_trigger(self):
        original = self.rpc.call
        def call(method, params):
            result = original(method, params)
            if method == 'thread/name/set':
                raise TimeoutError('response lost')
            return result
        self.rpc.call = call
        self.naming.hook(self.event)
        self.assertTrue(self.state()['done'])
        self.assertEqual(self.rpc.renames, 1)
        self.assertEqual(self.rpc.assessments, 1)


class ProjectTermTests(unittest.TestCase):
    setUp = background.BackgroundTests.setUp
    global_naming = scopes.ScopeRuntimeTests.global_naming

    def test_global_terms_are_explicit_and_do_not_leak_to_siblings(self):
        naming = self.global_naming()
        installer.project_modules(naming.home, self.root, 'project-modules', '库存预占、订单履约')
        terms, source = naming.guidance_for(self.root / 'nested')
        self.assertEqual(terms, '库存预占、订单履约')
        self.assertEqual(source['kind'], 'project_modules')
        terms, source = naming.guidance_for(self.root.parent / 'unrelated')
        self.assertEqual(source['kind'], 'global_default')
        self.assertNotIn('库存预占', terms)
        installer.project_modules(naming.home, self.root, 'project-modules-clear')
        self.assertEqual(naming.guidance_for(self.root)[1]['kind'], 'global_default')


class InstallationReliabilityTests(unittest.TestCase):
    setUp = installation.InstallTests.setUp
    fake_probe = installation.InstallTests.fake_probe
    install = installation.InstallTests.install
    manifest = installation.InstallTests.manifest

    def test_uninstall_recovers_missing_markers_only_when_tables_match(self):
        self.install()
        manifest = self.manifest()
        path = self.home / 'config.toml'
        path.write_text(path.read_text().replace(manifest['trust_start'], '').replace(manifest['trust_end'], ''))
        installer.manage(self.root, 'uninstall', manifest)
        self.assertEqual(path.read_text().strip(), self.user_before.strip())

    def test_missing_markers_with_changed_owned_table_are_rejected(self):
        self.install()
        manifest = self.manifest()
        path = self.home / 'config.toml'
        path.write_text(path.read_text().replace(manifest['trust_start'], '').replace(manifest['trust_end'], '').replace('enabled = true', 'enabled = false'))
        before = path.read_bytes()
        with self.assertRaises(RuntimeError):
            installer.manage(self.root, 'uninstall', manifest)
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_manifest_layout_is_rejected_without_changes(self):
        self.install()
        manifest = self.manifest()
        manifest['version'] = 99
        before = (self.home / 'config.toml').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'layout'):
            installer.upgrade(self.root, 'unused', self.home, manifest)
        self.assertEqual((self.home / 'config.toml').read_bytes(), before)

    def test_upgrade_migrates_known_legacy_queue_to_shared_storage(self):
        self.install()
        old = self.root / installer.STATE / 'jobs/session-1' / (hashlib.sha256(b'turn-1').hexdigest() + '.json')
        manifest = self.manifest()
        manifest.pop('queue_storage')
        packet = {'first_user_request': '解释库存预占', 'recent_user_requests': [], 'final_response': '已经交付分析'}
        job = {'session_id': 'session-1', 'turn_id': 'turn-1', 'queued_at': 1.0,
               'project': str(self.root), 'transcript_path': str(self.root / 'transcript.jsonl'), 'packet': packet}
        runtime.atomic_json(old, job)
        installer.upgrade(self.root, 'unused', self.home, manifest)
        shared = installer.global_root(self.home) / 'coordination/jobs/session-1'
        self.assertEqual([json.loads(p.read_text()) for p in shared.glob('*.json')], [job])
        self.assertFalse(old.exists())

    def legacy_queue(self):
        manifest = self.manifest()
        manifest.pop('queue_storage')
        path = self.root / installer.STATE / 'jobs/session-1' / (hashlib.sha256(b'turn-1').hexdigest() + '.json')
        job = {'session_id': 'session-1', 'turn_id': 'turn-1', 'queued_at': 1.0,
               'project': str(self.root), 'transcript_path': str(self.root / 'transcript.jsonl'),
               'packet': {'first_user_request': '分析库存', 'recent_user_requests': [], 'final_response': '已交付'}}
        runtime.atomic_json(path, job)
        return manifest, path, job

    def test_active_worker_prevents_migration_without_partial_changes(self):
        self.install()
        manifest, path, _ = self.legacy_queue()
        worker = installer.global_root(self.home) / 'coordination/workers/session-1.lock'
        worker.parent.mkdir(parents=True, exist_ok=True)
        before = (self.home / 'config.toml').read_bytes()
        with worker.open('a') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'active naming worker'):
                installer.upgrade(self.root, 'unused', self.home, manifest)
        self.assertTrue(path.exists())
        self.assertEqual((self.home / 'config.toml').read_bytes(), before)
        self.assertFalse(list((installer.global_root(self.home) / 'coordination/jobs').glob('*/*.json')))

    def test_invalid_legacy_queue_is_preserved_without_conversion(self):
        self.install()
        manifest, path, job = self.legacy_queue()
        job['unknown'] = True
        runtime.atomic_json(path, job)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'unknown local queue'):
            installer.upgrade(self.root, 'unused', self.home, manifest)
        self.assertEqual(path.read_bytes(), before)

    def test_upgrade_recovers_one_missing_trust_marker(self):
        self.install()
        manifest = self.manifest()
        path = self.home / 'config.toml'
        path.write_text(path.read_text().replace(manifest['trust_end'], ''))
        result = installer.upgrade(self.root, 'unused', self.home, manifest)
        self.assertTrue(result['trust_recovered'])
        self.assertEqual(path.read_text().count(manifest['trust_start']), 1)
        self.assertEqual(path.read_text().count(manifest['trust_end']), 1)
