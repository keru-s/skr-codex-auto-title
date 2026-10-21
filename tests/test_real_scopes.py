"""Real CLI checks for both scopes, in disposable projects and CODEX_HOME; no inference."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from test_real_install import m


def main(binary):
    with tempfile.TemporaryDirectory(prefix='title scopes ') as temporary:
        base = Path(temporary).resolve()
        home, project, other = (base / name for name in ('codex home', 'project one', 'project two'))
        for path in (home, project, other):
            path.mkdir()
        user = 'model = "gpt-6-luna"\n'
        for root in (project, other):
            user += '[projects.' + json.dumps(str(root)) + ']\ntrust_level = "trusted"\n'
        (home / 'config.toml').write_text(user)
        existing = m.encoded({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo retain-global'}]}]}})
        (home / 'hooks.json').write_bytes(existing)
        storage = m.global_root(home)
        storage.mkdir()
        with m.configuration_lock(home):
            for root in (storage, project):
                with m.lock(root):
                    assert m.install(root, binary, home, '实际成果模块')['configuration_trusted']
        result = m.probe(project, binary, home)
        hooks = [hook for entry in result['result']['data'] for hook in entry['hooks']]
        owned = [hook for hook in hooks if 'first_completion.py' in hook.get('command', '')]
        assert len(owned) == 4, owned
        assert all(hook['trustStatus'] == 'trusted' for hook in owned)
        installer = Path(__file__).resolve().parents[1] / 'install.py'
        for action, enabled in [('project-disable', False), ('project-status', False), ('project-enable', True)]:
            output = subprocess.check_output([sys.executable, str(installer), action, '--scope', 'global',
                                              '--root', str(project), '--codex-home', str(home)], text=True)
            assert json.loads(output)['enabled'] is enabled
        with m.configuration_lock(home):
            m.project_policy(home, project, 'project-disable')
            assert m.project_policy(home, other, 'project-status')['enabled']
            for root in (storage, project):
                with m.lock(root):
                    manifest = json.loads((root / m.ADMIN / 'installation.json').read_text())
                    assert m.upgrade(root, binary, home, manifest)['configuration_trusted']
                    for action in ('disable', 'enable'):
                        m.manage(root, action, manifest)
            assert not m.project_policy(home, project, 'project-status')['enabled']
            for root in (project, storage):
                with m.lock(root):
                    manifest = json.loads((root / m.ADMIN / 'installation.json').read_text())
                    m.manage(root, 'uninstall', manifest)
        assert (home / 'hooks.json').read_bytes() == existing
        assert (home / 'config.toml').read_text().strip() == user.strip()
        assert not (project / '.codex/hooks.json').exists()
        print(json.dumps({'cli_version': result['cli_version'], 'global_and_project_install': 'passed',
                          'per_project_cli_switches': 'passed', 'upgrade_retains_switches': True,
                          'uninstall_preserves_existing_configuration': True, 'desktop_e2e_verified': False}, indent=2))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'codex')
