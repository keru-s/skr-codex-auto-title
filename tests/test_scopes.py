import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_background as background
import test_install as installation

m = installation.m
runtime = background.m


class GlobalInstallTests(unittest.TestCase):
    setUp = installation.InstallTests.setUp
    fake_probe = installation.InstallTests.fake_probe

    def install_global(self):
        root = m.global_root(self.home)
        root.mkdir()
        self.original = m.encoded({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo global-existing'}]}]}})
        (self.home / 'hooks.json').write_bytes(self.original)
        result = m.install(root, 'unused', self.home, '根据实际成果提炼模块')
        return root, result

    def test_global_install_upgrade_and_uninstall_preserve_unrelated_files(self):
        local_hooks = (self.root / '.codex/hooks.json').read_bytes()
        root, result = self.install_global()
        self.assertTrue(result['configuration_trusted'])
        self.assertEqual(json.loads((root / m.STATE / 'settings.json').read_text())['scope'], 'global')
        manifest = json.loads((root / m.ADMIN / 'installation.json').read_text())
        state = root / m.STATE / 'named.json'
        state.write_text('{"done":true}')
        m.upgrade(root, 'unused', self.home, manifest)
        self.assertEqual(state.read_text(), '{"done":true}')
        manifest = json.loads((root / m.ADMIN / 'installation.json').read_text())
        for action in ('disable', 'enable'):
            m.manage(root, action, manifest)
            self.assertEqual(json.loads((root / m.STATE / 'settings.json').read_text())['enabled'], action == 'enable')
        m.manage(root, 'uninstall', manifest)
        self.assertFalse(json.loads((root / m.STATE / 'settings.json').read_text())['enabled'])
        self.assertEqual((self.home / 'hooks.json').read_bytes(), self.original)
        self.assertEqual((self.root / '.codex/hooks.json').read_bytes(), local_hooks)
        self.assertEqual((self.home / 'config.toml').read_text().strip(), self.user_before.strip())

    def test_project_can_be_reenabled_after_global_uninstall(self):
        root, _ = self.install_global()
        m.project_policy(self.home, self.root, 'project-disable')
        manifest = json.loads((root / m.ADMIN / 'installation.json').read_text())
        m.manage(root, 'uninstall', manifest)
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'install.py'),
                   'project-enable', '--scope', 'global', '--root', str(self.root), '--codex-home', str(self.home)]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['enabled'])

    def test_per_project_switches_survive_global_upgrade(self):
        root, _ = self.install_global()
        child = self.root / 'nested'
        child.mkdir()
        with m.configuration_lock(self.home):
            self.assertFalse(m.project_policy(self.home, self.root, 'project-disable')['enabled'])
            self.assertFalse(m.project_policy(self.home, child, 'project-status')['enabled'])
            self.assertTrue(m.project_policy(self.home, child, 'project-enable')['enabled'])
        manifest = json.loads((root / m.ADMIN / 'installation.json').read_text())
        m.upgrade(root, 'unused', self.home, manifest)
        self.assertFalse(m.project_policy(self.home, self.root, 'project-status')['enabled'])
        self.assertTrue(m.project_policy(self.home, child, 'project-status')['enabled'])

    def test_global_and_local_installations_have_distinct_hooks(self):
        global_root, _ = self.install_global()
        m.install(self.root, 'unused', self.home, '项目术语')
        self.assertNotEqual(m.hook_config(global_root, self.home), m.hook_config(self.root, self.home))
        for root in (global_root, self.root):
            manifest = json.loads((root / m.ADMIN / 'installation.json').read_text())
            self.assertTrue(m.verify(root, 'unused', self.home, manifest)['configuration_trusted'])


class ScopeRuntimeTests(unittest.TestCase):
    setUp = background.BackgroundTests.setUp
    state = background.BackgroundTests.state

    def global_naming(self):
        root = self.root / 'global-storage'
        runtime.atomic_json(root / m.STATE / 'settings.json', dict(self.settings, scope='global'))
        runtime.atomic_json(root / m.ADMIN / 'backend.json', self.naming.backend)
        return runtime.Naming(root)

    def test_global_names_unrelated_project(self):
        naming = self.global_naming()
        naming.hook(self.event)
        self.assertEqual(self.rpc.renames, 1)
        self.assertTrue(json.loads((naming.directory / 'test-session.json').read_text())['done'])

    def test_project_disabled_then_enabled_without_affecting_other_projects(self):
        naming = self.global_naming()
        policy = naming.shared / 'projects.json'
        runtime.atomic_json(policy, {str(self.root): False})
        naming.hook(self.event)
        self.assertEqual(self.rpc.assessments, 0)
        other = self.root.parent / 'another-project'
        self.assertTrue(naming.project_allowed(other))
        runtime.atomic_json(policy, {str(self.root): True})
        naming.hook(self.event)
        self.assertEqual(self.rpc.renames, 1)

    def test_global_defers_to_project_install_even_when_project_disabled(self):
        naming = self.global_naming()
        runtime.atomic_json(self.root / m.ADMIN / 'installation.json', {'uninstalled': False})
        naming.hook(self.event)
        self.assertEqual(self.rpc.assessments, 0)
        runtime.atomic_json(self.directory / 'settings.json', dict(self.settings, enabled=False))
        runtime.Naming(self.root).hook(self.event)
        self.assertEqual(self.rpc.assessments, 0)
        runtime.atomic_json(self.root / m.ADMIN / 'installation.json', {'uninstalled': True})
        naming.hook(self.event)
        self.assertEqual(self.rpc.renames, 1)

    def test_completed_global_session_is_not_renamed_by_project_install(self):
        self.global_naming().hook(self.event)
        self.naming.hook(dict(self.event, turn_id='later'))
        self.assertEqual(self.rpc.assessments, 1)
        self.assertEqual(self.rpc.renames, 1)
        self.assertTrue(self.state()['done'])

    def test_pending_title_survives_switch_from_global_to_project(self):
        self.rpc.fail_rename = True
        self.global_naming().hook(self.event)
        self.assertEqual(self.rpc.assessments, 1)
        attempts = self.rpc.renames
        self.rpc.fail_rename = False
        self.rpc.answer['title'] = '项目配置 | 生成不同标题'
        self.naming.hook(self.event)
        self.assertEqual(self.rpc.assessments, 1)
        self.assertEqual(self.rpc.renames, attempts + 1)
        self.assertEqual(self.state()['title'], '商品管控 | 解释幂等性')

    def test_later_project_activation_continues_pending_global_session(self):
        self.rpc.fail_rename = True
        naming = self.global_naming()
        naming.hook(self.event)
        self.rpc.fail_rename = False
        runtime.atomic_json(self.directory / 'settings.json', dict(self.settings, activated_at='2026-10-10T00:00:00+00:00'))
        runtime.atomic_json(self.root / m.ADMIN / 'installation.json', {'uninstalled': False})
        naming.hook(dict(self.event, turn_id='later-global'))
        runtime.Naming(self.root).hook(dict(self.event, turn_id='later-project'))
        self.assertEqual(self.rpc.assessments, 1)
        self.assertTrue(self.state()['done'])

    def test_later_project_activation_continues_unfinished_global_session(self):
        naming = self.global_naming()
        self.rpc.answer = {'completed': False, 'title': '', 'evidence': '只有追问'}
        naming.hook(self.event)
        runtime.atomic_json(self.directory / 'settings.json', dict(self.settings, activated_at='2026-10-10T00:00:00+00:00'))
        runtime.atomic_json(self.root / m.ADMIN / 'installation.json', {'uninstalled': False})
        self.rpc.answer = {'completed': True, 'title': '商品管控 | 解释幂等性', 'evidence': '已交付'}
        runtime.Naming(self.root).hook(dict(self.event, turn_id='later-project'))
        self.assertTrue(self.state()['done'])

    def test_later_global_activation_continues_pending_project_session(self):
        self.rpc.fail_rename = True
        self.naming.hook(self.event)
        self.rpc.fail_rename = False
        naming = self.global_naming()
        runtime.atomic_json(naming.directory / 'settings.json', dict(self.settings, scope='global', activated_at='2026-10-10T00:00:00+00:00'))
        runtime.atomic_json(self.root / m.ADMIN / 'installation.json', {'uninstalled': True})
        naming = runtime.Naming(naming.root)
        naming.hook(dict(self.event, turn_id='later-global'))
        self.assertEqual(self.rpc.assessments, 1)
        self.assertTrue(json.loads((naming.directory / 'test-session.json').read_text())['done'])

    def test_invalid_session_cannot_create_shared_registration(self):
        lines = self.transcript.read_text().splitlines()
        row = json.loads(lines[0])
        row['payload']['id'] = '../escape'
        lines[0] = json.dumps(row)
        self.transcript.write_text('\n'.join(lines) + '\n')
        self.assertEqual(self.naming.hook(dict(self.event, session_id='../escape')), {})
        self.assertFalse(self.naming.shared.exists())
        self.assertEqual(self.rpc.assessments, 0)

    def test_project_switch_is_rechecked_after_model_assessment(self):
        naming = self.global_naming()
        self.rpc.disabled_callback = lambda: runtime.atomic_json(naming.shared / 'projects.json', {str(self.root): False})
        naming.hook(self.event)
        self.assertEqual(self.rpc.assessments, 1)
        self.assertEqual(self.rpc.renames, 0)

    def test_project_switch_applies_to_local_install_too(self):
        runtime.atomic_json(self.naming.shared / 'projects.json', {str(self.root): False})
        self.naming.hook(self.event)
        self.assertEqual(self.rpc.assessments, 0)

    def test_nested_project_install_owns_its_subtree(self):
        nested = self.root / 'nested'
        nested.mkdir()
        runtime.atomic_json(nested / m.ADMIN / 'installation.json', {'uninstalled': False})
        self.assertFalse(self.naming.project_allowed(nested))


class ScopeCliTests(unittest.TestCase):
    def test_global_does_not_require_project_root_but_requires_trust(self):
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'install.py')]
        result = subprocess.run(command + ['install', '--scope', 'global'], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--trust-hooks', result.stderr)
        self.assertNotIn('--root is required', result.stderr)
        result = subprocess.run(command + ['project-disable', '--scope', 'global'], text=True, capture_output=True)
        self.assertIn('--root is required', result.stderr)
