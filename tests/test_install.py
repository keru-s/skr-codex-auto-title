import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] / 'skills/skr-codex-auto-title/scripts/install.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hook project ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project with spaces'
        self.home = Path(self.temp.name) / 'user config'
        self.root.mkdir(); self.home.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / '.codex').mkdir()
        self.hooks = {'description': 'existing hooks', 'custom': 'preserve', 'hooks': {
            'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'echo existing-start'}]}],
            'Stop': [{'hooks': [{'type': 'command', 'command': 'echo existing-stop'}]}]}}
        self.hooks_bytes = json.dumps(self.hooks, indent=4).encode()
        (self.root / '.codex/hooks.json').write_bytes(self.hooks_bytes)
        (self.root / '.codex/config.toml').write_text('model = "gpt-6.1-sol"\n')
        (self.root / '.gitignore').write_text('/build/\n')
        self.user_before = '# user settings\nmodel = "gpt-6.1-sol"\n'
        (self.home / 'config.toml').write_text(self.user_before)
        self.mock = patch.object(m, 'probe', side_effect=self.fake_probe)
        self.mock.start(); self.addCleanup(self.mock.stop)

    def fake_probe(self, root, binary, home):
        data = json.loads(m.hook_config(root, home).read_bytes())
        config = m.tomllib.loads((home / 'config.toml').read_text())
        state = config.get('hooks', {}).get('state', {})
        hooks = []
        for name, event in [('SessionStart', 'session_start'), ('Stop', 'stop')]:
            for index, group in enumerate(data['hooks'].get(name, [])):
                for hi, handler in enumerate(group['hooks']):
                    key = str(m.hook_config(root, home)) + ':' + event + ':' + str(index) + ':' + str(hi)
                    hooks.append({'sourcePath': str(m.hook_config(root, home)), 'command': handler['command'],
                                  'eventName': 'sessionStart' if name == 'SessionStart' else 'stop',
                                  'key': key, 'currentHash': 'sha256:' + 'a' * 64, 'enabled': True,
                                  'trustStatus': 'trusted' if state.get(key, {}).get('trusted_hash') == 'sha256:' + 'a'*64 else 'untrusted'})
        return {'cli_version': 'fake-only-for-state-tests', 'result': {'data': [{'hooks': hooks, 'errors': []}]}}

    def install(self):
        return m.install(self.root, 'unused', self.home, '订单履约、库存管理；来源 README', 'excluded-install')

    def manifest(self):
        return json.loads((self.root / m.ADMIN / 'installation.json').read_bytes())

    def test_should_support_terminal_install_and_explicit_model_without_chat_id(self):
        m.install(self.root, 'unused', self.home, '公共能力；来源本项目说明', model='explicit-model', effort='medium')
        settings = json.loads((self.root / m.STATE / 'settings.json').read_text())
        self.assertEqual(settings['excluded_session_ids'], [])
        self.assertEqual(settings['naming_model'], 'explicit-model')
        self.assertEqual(settings['naming_reasoning_effort'], 'medium')

    def test_should_merge_trust_and_preserve_existing_files(self):
        result = self.install()
        self.assertTrue(result['configuration_trusted'])
        data = json.loads((self.root / '.codex/hooks.json').read_bytes())
        for event in ['SessionStart', 'Stop']:
            self.assertEqual(data['hooks'][event][0], self.hooks['hooks'][event][0])
            self.assertEqual(len(data['hooks'][event]), 2)
        self.assertEqual(data['custom'], 'preserve')
        self.assertEqual((self.root / '.codex/config.toml').read_text(), 'model = "gpt-6.1-sol"\n')
        self.assertTrue((self.home / 'config.toml').read_text().startswith(self.user_before))
        for rel in [m.STATE + '/settings.json', m.ADMIN + '/installation.json']:
            self.assertEqual(subprocess.run(['git', 'check-ignore', '-q', rel], cwd=self.root).returncode, 0)
        for path in Path(result['backup']).glob('*.bak'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_should_keep_history_and_activation_on_disable_enable(self):
        self.install()
        state = self.root / m.STATE / 'a-real-chat.json'
        state.write_text('{"done": true, "title": "库存管理 | 分析扣减规则"}')
        before = state.read_bytes()
        settings_path = self.root / m.STATE / 'settings.json'
        activation = json.loads(settings_path.read_bytes())['activated_at']
        for action in ['disable', 'enable']:
            m.manage(self.root, action, self.manifest())
            settings = json.loads(settings_path.read_bytes())
            self.assertEqual(settings['enabled'], action == 'enable')
            self.assertEqual(settings['activated_at'], activation)
            self.assertEqual(state.read_bytes(), before)

    def test_should_uninstall_only_owned_handlers_and_trust(self):
        self.install()
        path = self.root / '.codex/hooks.json'
        data = json.loads(path.read_bytes())
        extra = {'hooks': [{'type': 'command', 'command': 'echo later-added'}]}
        data['hooks']['Stop'].append(extra)
        path.write_bytes(m.encoded(data))
        user = self.home / 'config.toml'
        user.write_text(user.read_text() + '\n[projects."later"]\ntrust_level = "trusted"\n')
        state = self.root / m.STATE / 'later-chat.json'
        state.write_text('{"done": true}')
        m.manage(self.root, 'uninstall', self.manifest())
        data = json.loads(path.read_bytes())
        self.assertEqual(data['hooks']['Stop'], self.hooks['hooks']['Stop'] + [extra])
        self.assertEqual(data['hooks']['SessionStart'], self.hooks['hooks']['SessionStart'])
        self.assertIn('[projects."later"]', user.read_text())
        self.assertNotIn('# BEGIN skr-codex-auto-title', user.read_text())
        self.assertEqual(state.read_text(), '{"done": true}')
        self.assertFalse((self.root / '.codex/hooks/first_completion.py').exists())

    def test_should_upgrade_with_backup_without_resetting_history(self):
        self.install()
        state = self.root / m.STATE / 'a-real-chat.json'
        state.write_text('{"done": true}')
        settings = self.root / m.STATE / 'settings.json'
        before = settings.read_bytes()
        hooks = (self.root / '.codex/hooks.json').read_bytes()
        original_script = (self.root / '.codex/hooks/first_completion.py').read_bytes()
        asset = Path(self.temp.name) / 'updated.py'
        asset.write_bytes(original_script + b'\n# upgraded test asset\n')
        with patch.object(m, 'ASSET', asset):
            result = m.upgrade(self.root, 'unused', self.home, self.manifest())
        self.assertTrue(result['configuration_trusted'])
        self.assertTrue(result['history_retained'])
        self.assertEqual(settings.read_bytes(), before)
        self.assertEqual(state.read_text(), '{"done": true}')
        self.assertEqual((self.root / '.codex/hooks.json').read_bytes(), hooks)
        self.assertEqual((self.root / '.codex/hooks/first_completion.py').read_bytes(), asset.read_bytes())
        self.assertTrue(any(p.read_bytes() == original_script for p in Path(result['backup']).glob('*.bak')))
        m.manage(self.root, 'uninstall', self.manifest())
        self.assertEqual((self.root / '.codex/hooks.json').read_bytes(), self.hooks_bytes)

    def test_should_refuse_upgrade_when_installed_script_changed(self):
        self.install()
        script = self.root / '.codex/hooks/first_completion.py'
        script.write_bytes(script.read_bytes() + b'\n# local change\n')
        before = script.read_bytes()
        with self.assertRaises(RuntimeError): m.upgrade(self.root, 'unused', self.home, self.manifest())
        self.assertEqual(script.read_bytes(), before)

    def test_should_roll_back_upgrade_when_verification_fails(self):
        self.install()
        script = self.root / '.codex/hooks/first_completion.py'
        before = script.read_bytes()
        manifest_before = (self.root / m.ADMIN / 'installation.json').read_bytes()
        user_before = (self.home / 'config.toml').read_bytes()
        asset = Path(self.temp.name) / 'updated.py'
        asset.write_bytes(before + b'\n# upgraded test asset\n')
        original_verify = m.verify
        count = 0
        def verify(*args):
            nonlocal count
            count += 1
            if count == 2: raise RuntimeError('simulated final verification failure')
            return original_verify(*args)
        with patch.object(m, 'ASSET', asset), patch.object(m, 'verify', side_effect=verify):
            with self.assertRaises(RuntimeError): m.upgrade(self.root, 'unused', self.home, self.manifest())
        self.assertEqual(script.read_bytes(), before)
        self.assertEqual((self.root / m.ADMIN / 'installation.json').read_bytes(), manifest_before)
        self.assertEqual((self.home / 'config.toml').read_bytes(), user_before)

    def test_should_restore_original_hooks_bytes_when_no_later_changes(self):
        self.install()
        m.manage(self.root, 'uninstall', self.manifest())
        self.assertEqual((self.root / '.codex/hooks.json').read_bytes(), self.hooks_bytes)

    def test_should_roll_back_when_final_trust_verification_fails(self):
        original_ignore = (self.root / '.gitignore').read_bytes()
        with patch.object(m, 'verify', side_effect=RuntimeError('trust rejected')):
            with self.assertRaisesRegex(RuntimeError, 'trust rejected'):
                self.install()
        self.assertEqual((self.home / 'config.toml').read_text(), self.user_before)
        self.assertEqual((self.root / '.codex/hooks.json').read_bytes(), self.hooks_bytes)
        self.assertTrue((self.root / '.gitignore').read_bytes().startswith(original_ignore))
        self.assertIn(m.ADMIN.encode(), (self.root / '.gitignore').read_bytes())
        self.assertFalse((self.root / m.STATE / 'settings.json').exists())

    def test_should_refuse_existing_installation_without_touching_history(self):
        self.install()
        before = (self.root / m.STATE / 'settings.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'conflict'):
            self.install()
        self.assertEqual((self.root / m.STATE / 'settings.json').read_bytes(), before)

    def test_should_refuse_uninstall_when_owned_script_or_trust_changed(self):
        self.install()
        script = self.root / '.codex/hooks/first_completion.py'
        script.write_bytes(script.read_bytes() + b'\n# edited\n')
        before = (self.home / 'config.toml').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'script changed'):
            m.manage(self.root, 'uninstall', self.manifest())
        self.assertEqual((self.home / 'config.toml').read_bytes(), before)

    def test_should_upgrade_legacy_sync_handlers_and_keep_every_existing_state_byte(self):
        self.install()
        path = self.root / '.codex/hooks.json'
        hooks = json.loads(path.read_text())
        own = hooks['hooks']['Stop'][1]['hooks'][0]
        own.pop('async')
        own['timeout'] = 10
        path.write_bytes(m.encoded(hooks))
        manifest = self.manifest()
        manifest.pop('backend_sha256')
        manifest.pop('skill_version')
        (self.root / m.ADMIN / 'backend.json').unlink()
        (self.root / m.ADMIN / 'installation.json').write_bytes(m.encoded(manifest))
        state = self.root / m.STATE / 'named-chat.json'
        before = b'{"done":true,"title":"existing title"}'
        state.write_bytes(before)
        settings_before = (self.root / m.STATE / 'settings.json').read_bytes()
        m.upgrade(self.root, 'unused', self.home, manifest)
        after = json.loads(path.read_text())
        self.assertEqual(after['hooks']['Stop'][0], self.hooks['hooks']['Stop'][0])
        self.assertTrue(after['hooks']['Stop'][1]['hooks'][0]['async'])
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual((self.root / m.STATE / 'settings.json').read_bytes(), settings_before)
        self.assertEqual(self.manifest()['skill_version'], '1.0.0')

    def test_should_upgrade_without_duplicate_trust_or_lost_state(self):
        self.install()
        manifest = self.manifest()
        manifest['skill_version'] = 'test-upgrade-base'
        (self.root / m.ADMIN / 'installation.json').write_bytes(m.encoded(manifest))
        state = self.root / m.STATE / 'already-named.json'
        state.write_bytes(b'{"done":true,"title":"existing title"}')
        before = state.read_bytes()
        settings = (self.root / m.STATE / 'settings.json').read_bytes()
        m.upgrade(self.root, 'unused', self.home, manifest)
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual((self.root / m.STATE / 'settings.json').read_bytes(), settings)
        self.assertEqual((self.home / 'config.toml').read_text().count(
            '# BEGIN skr-codex-auto-title '), 1)
        self.assertEqual(self.manifest()['skill_version'], '1.0.0')

    def test_upgrade_replaces_removable_interpreter_command(self):
        self.install()
        manifest = self.manifest()
        original = manifest['command']
        old_command = '/deleted/download/.venv/bin/python installed-hook.py hook'
        path = self.root / '.codex/hooks.json'
        hooks = json.loads(path.read_text())
        for groups in hooks['hooks'].values():
            for group in groups:
                for handler in group['hooks']:
                    if handler.get('command') == original:
                        handler['command'] = old_command
        path.write_bytes(m.encoded(hooks))
        manifest['command'] = old_command
        (self.root / m.ADMIN / 'installation.json').write_bytes(m.encoded(manifest))
        result = m.upgrade(self.root, 'unused', self.home, manifest)
        self.assertTrue(result['configuration_trusted'])
        self.assertEqual(self.manifest()['command'], m.runtime_command(self.root / '.codex/hooks/first_completion.py'))
        self.assertNotIn(old_command, path.read_text())
        m.manage(self.root, 'uninstall', self.manifest())
        self.assertEqual(path.read_bytes(), self.hooks_bytes)

    def test_should_refuse_modified_backend_config(self):
        self.install()
        path = self.root / m.ADMIN / 'backend.json'
        path.write_text('{"unknown":true}')
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'backend config changed'):
            m.upgrade(self.root, 'unused', self.home, self.manifest())
        self.assertEqual(path.read_bytes(), before)

    def test_should_remove_owned_backend_on_uninstall_and_retain_history(self):
        self.install()
        m.manage(self.root, 'uninstall', self.manifest())
        self.assertFalse((self.root / m.ADMIN / 'backend.json').exists())
        self.assertTrue((self.root / m.STATE / 'settings.json').exists())

    def test_should_rollback_async_handlers_and_backend_when_upgrade_fails(self):
        self.install()
        manifest = self.manifest()
        hooks = self.root / '.codex/hooks.json'
        backend = self.root / m.ADMIN / 'backend.json'
        before = (hooks.read_bytes(), backend.read_bytes())
        real_verify = m.verify
        count = 0
        def verify(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('final verify failed')
            return real_verify(*args)
        with patch.object(m, 'verify', side_effect=verify):
            with self.assertRaises(RuntimeError):
                m.upgrade(self.root, 'unused', self.home, manifest)
        self.assertEqual((hooks.read_bytes(), backend.read_bytes()), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
