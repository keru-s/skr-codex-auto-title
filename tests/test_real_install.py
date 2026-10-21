"""Real CLI config integration in temporary project and isolated CODEX_HOME; no chats."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] / 'skills/skr-codex-auto-title/scripts/install.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main(binary):
    with tempfile.TemporaryDirectory(prefix='codex hook migration ') as temporary:
        root, home = Path(temporary) / 'project with spaces', Path(temporary) / 'codex home'
        root.mkdir(); home.mkdir()
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        (root / '.codex').mkdir()
        # Trust only the disposable project so project hooks are actually loaded.
        original_config = 'model = "gpt-6-luna"\n[projects.' + json.dumps(str(root)) + ']\ntrust_level = "trusted"\n'
        (home / 'config.toml').write_text(original_config)
        original_hooks = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo existing', 'timeout': 5}]}]}}
        original_hooks_bytes = m.encoded(original_hooks)
        (root / '.codex/hooks.json').write_bytes(original_hooks_bytes)
        with m.lock(root):
            installed = m.install(root, binary, home, '订单履约、库存管理；来源临时项目说明', 'excluded-install')
            manifest = json.loads((root / m.ADMIN / 'installation.json').read_bytes())
            verified = m.verify(root, binary, home, manifest)
            assert installed['configuration_trusted'] and verified['configuration_trusted']
            for action in ('disable', 'enable'):
                m.manage(root, action, manifest)
                settings = json.loads((root / m.STATE / 'settings.json').read_bytes())
                assert settings['enabled'] == (action == 'enable')
            m.manage(root, 'uninstall', manifest)
        after = m.probe(root, binary, home)
        assert (root / '.codex/hooks.json').read_bytes() == original_hooks_bytes
        assert (home / 'config.toml').read_text().strip() == original_config.strip()
        assert not (root / '.codex/hooks/first_completion.py').exists()
        project_hooks = [h for entry in after['result']['data'] for h in entry['hooks']
                         if h.get('sourcePath') == str(root / '.codex/hooks.json')]
        assert len(project_hooks) == 1 and project_hooks[0]['command'] == 'echo existing'
        print(json.dumps({'real_cli_version': after['cli_version'], 'configuration_install_verify_uninstall': 'passed',
                          'existing_project_hook_retained': True, 'original_user_settings_retained': True,
                          'paths_with_spaces': 'passed', 'isolated_codex_home': True,
                          'desktop_e2e_verified': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'codex')
