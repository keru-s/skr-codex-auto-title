#!/usr/bin/env python3
"""Install/manage global or project background naming hooks, preserving state."""
import argparse
import contextlib
import copy
import datetime as dt
try:
    import fcntl
except ImportError:
    raise SystemExit('This installer requires macOS or Linux (POSIX file locks).')
import hashlib
import importlib.util
import tempfile
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import uuid

ASSET = Path(__file__).resolve().parents[1] / 'assets/first_completion.py'
STATE = 'runtime/skr-codex-auto-title'
ADMIN = 'runtime/skr-codex-auto-title-install'
IGNORE = '\n# Codex first-completion naming state and private backups\n/' + STATE + '/\n/' + ADMIN + '/\n'


def runtime_module():
    spec = importlib.util.spec_from_file_location('title_runtime', ASSET)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


def validate_layout(manifest):
    required = {'version', 'skill_version', 'backend_sha256', 'root', 'user_config', 'command',
                'script_sha256', 'trust_start', 'trust_end', 'trust_block', 'trust_keys',
                'original_hooks', 'original_hooks_text', 'backup'}
    if (not isinstance(manifest, dict) or not required.issubset(manifest)
            or manifest['version'] != 1 or manifest['skill_version'] != '1.0.0'
            or manifest.get('queue_storage') not in (None, 'shared-v1')
            or not isinstance(manifest['trust_keys'], list) or len(manifest['trust_keys']) != 2
            or any(not isinstance(key, str) or not key for key in manifest['trust_keys'])
            or len(set(manifest['trust_keys'])) != 2
            or manifest['original_hooks'] is not None and not isinstance(manifest['original_hooks'], dict)
            or manifest['original_hooks_text'] is not None and not isinstance(manifest['original_hooks_text'], str)
            or any(not isinstance(manifest[key], str) for key in required - {'version', 'original_hooks', 'original_hooks_text', 'trust_keys'})):
        raise RuntimeError('unsupported installation layout; only published 1.0.0 layout 1 is supported')
    for key in ('backend_sha256', 'script_sha256'):
        if not re.fullmatch(r'[a-f0-9]{64}', manifest[key]):
            raise RuntimeError('invalid installation layout digest')


def without_owned_trust(text, manifest):
    """Recover missing comments only when the exact owned tables can be proved."""
    start, end = manifest['trust_start'], manifest['trust_end']
    if text.count(start) > 1 or text.count(end) > 1:
        raise RuntimeError('duplicate trust markers; inspect before changing config')
    if text.count(start) == text.count(end) == 1:
        begin, finish = text.index(start), text.index(end) + len(end) + 1
        if text[begin:finish] != manifest['trust_block']:
            raise RuntimeError('owned trust block changed; inspect before changing config')
        return text[:begin] + text[finish:], False
    actual = tomllib.loads(text)
    expected = tomllib.loads(manifest['trust_block']).get('hooks', {}).get('state', {})
    if set(expected) != set(manifest['trust_keys']) or len(expected) != 2:
        raise RuntimeError('cannot establish trust table ownership')
    state = actual.get('hooks', {}).get('state', {})
    if any(state.get(key) != value or set(value) != {'enabled', 'trusted_hash'} for key, value in expected.items()):
        raise RuntimeError('owned trust table content differs; no recovery performed')
    lines = text.splitlines(keepends=True)
    remove = set()
    for key in expected:
        header = '[hooks.state.' + json.dumps(key) + ']'
        matches = [i for i, line in enumerate(lines) if re.fullmatch(r'\s*' + re.escape(header) + r'\s*(?:#.*)?', line.rstrip('\r\n'))]
        if len(matches) != 1:
            raise RuntimeError('unknown owned trust table text layout; no recovery performed')
        index = matches[0]
        remove.add(index)
        for i in range(index + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith('['):
                break
            if not stripped or stripped.startswith('#'):
                continue
            if not re.match(r'^(?:trusted_hash|enabled)\s*=', stripped):
                raise RuntimeError('unknown owned trust table field layout')
            remove.add(i)
    remove.update(i for i, line in enumerate(lines) if line.strip() in (start, end))
    remaining = ''.join(line for i, line in enumerate(lines) if i not in remove)
    expected_remaining = copy.deepcopy(actual)
    for key in expected:
        del expected_remaining['hooks']['state'][key]
    def nonempty(value):
        if not isinstance(value, dict):
            return value
        return {key: cleaned for key, item in value.items() if (cleaned := nonempty(item)) != {}}
    if nonempty(tomllib.loads(remaining)) != nonempty(expected_remaining):
        raise RuntimeError('recovery would affect unrelated configuration')
    return remaining, True


def global_root(home):
    return home / 'skr-codex-auto-title'


def hook_config(root, home):
    return home / 'hooks.json' if root == global_root(home) else root / '.codex/hooks.json'


def project_policy(home, project, action):
    """Caller holds configuration_lock; worker mutation uses the same policy gate."""
    project = project.resolve()
    directory = global_root(home) / 'coordination'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.lock').open('a') as stream:
        os.chmod(stream.fileno(), 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        path = directory / 'projects.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        if action != 'project-status':
            data[str(project)] = action == 'project-enable'
            write(path, encoded(data), 0o600)
        matches = [(Path(key).resolve(), value) for key, value in data.items() if project.is_relative_to(Path(key).resolve())]
        selected = max(matches, key=lambda item: len(item[0].parts)) if matches else None
        return {'project': str(project), 'enabled': selected[1] if selected else True,
                'rule': str(selected[0]) if selected else None, 'action': action}


def project_modules(home, project, action, text=None):
    project = project.resolve()
    directory = global_root(home) / 'coordination'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.lock').open('a') as stream:
        os.chmod(stream.fileno(), 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        path = directory / 'modules.json'
        runtime = runtime_module()
        runtime.project_terms(home, project, '')  # Validate existing data before changing it.
        data = json.loads(path.read_text()) if path.exists() else {}
        if action == 'project-modules':
            if not isinstance(text, str) or not text.strip():
                raise ValueError('a non-empty UTF-8 modules file is required')
            data[str(project)] = text.strip()
        elif action == 'project-modules-clear':
            data.pop(str(project), None)
        else:
            raise ValueError('unsupported project modules action')
        write(path, encoded(data), 0o600)
        effective, source = runtime.project_terms(home, project, '')
        return {'action': action, 'project': str(project), 'module_source': source,
                'characters': len(effective), 'maximum_sent': runtime.MODULE_LIMIT}


def runtime_command(script):
    # Only the standard library is needed; a removable installer venv is unnecessary.
    interpreter = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    return shlex.quote(str(interpreter)) + ' ' + shlex.quote(str(script)) + ' hook'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return path.read_bytes() if path.exists() else None


def write(path, data, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, mode if mode is not None else (path.stat().st_mode & 0o777 if path.exists() else 0o600))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Changes:
    def __init__(self, root):
        self.backup = root / ADMIN / 'backups' / (dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        self.before = {}
        self.after = {}
        self.modes = {}

    def set(self, path, data, mode=None):
        if path not in self.before:
            self.before[path] = read(path)
            self.modes[path] = path.stat().st_mode & 0o777 if path.exists() else None
            self.backup.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.before[path] is not None:
                write(self.backup / (str(len(self.before)) + '.bak'), self.before[path], 0o600)
            manifest = [{'path': str(p), 'existed': b is not None, 'backup': str(i) + '.bak' if b is not None else None}
                        for i, (p, b) in enumerate(self.before.items(), 1)]
            write(self.backup / 'files.json', encoded(manifest), 0o600)
        if data is None:
            path.unlink(missing_ok=True)
        else:
            write(path, data, mode)
        self.after[path] = data

    def rollback(self, keep=()):
        conflicts = []
        for path in reversed(list(self.after)):
            if path in keep:
                continue
            if read(path) != self.after[path]:
                conflicts.append(str(path))
                continue
            original = self.before[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                write(path, original, self.modes[path])
        if conflicts:
            raise RuntimeError('rollback conflicts; inspect backups at ' + str(self.backup) + ': ' + ', '.join(conflicts))


@contextlib.contextmanager
def lock(root):
    directory = root / STATE
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def probe(root, binary, home):
    env = dict(os.environ, CODEX_HOME=str(home))
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    process = subprocess.Popen([binary, 'app-server', '--stdio'], cwd=root, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b''

    def send(packet):
        process.stdin.write(json.dumps(packet).encode() + b'\n')
        process.stdin.flush()

    def request(identifier, method, params):
        nonlocal buffer
        send({'id': identifier, 'method': method, 'params': params})
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                if not line.strip():
                    continue
                packet = json.loads(line)
                if packet.get('id') == identifier:
                    if 'error' in packet:
                        raise RuntimeError(method + ': ' + str(packet['error']))
                    return packet['result']
            if selector.select(min(1, max(0, deadline - time.monotonic()))):
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('configuration probe exited during ' + method)
                buffer += chunk
        raise TimeoutError(method)

    try:
        request(1, 'initialize', {'clientInfo': {'name': 'first-completion-config-probe', 'version': '1.0.0'},
                                  'capabilities': {'experimentalApi': True}})
        send({'method': 'initialized'})
        result = request(2, 'hooks/list', {'cwds': [str(root)]})
        if not isinstance(result.get('data'), list) or not result['data']:
            raise RuntimeError('unsupported hooks/list response')
        errors = [error for entry in result['data'] for error in entry.get('errors', [])]
        if errors:
            raise RuntimeError('existing hook configuration errors: ' + json.dumps(errors, ensure_ascii=False))
        return {'cli_version': version, 'result': result}
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def owned_hooks(probed, root, command, home):
    found = [h for entry in probed['result']['data'] for h in entry['hooks']
             if h.get('sourcePath') == str(hook_config(root, home)) and h.get('command') == command]
    if len(found) != 2 or {h['eventName'] for h in found} != {'sessionStart', 'stop'}:
        raise RuntimeError('expected two installed handlers in current hooks/list schema, found ' + str(len(found)))
    if any(not h.get('key') or not h.get('currentHash') for h in found):
        raise RuntimeError('missing real hook key/currentHash')
    return found


def trust_block(root, hooks):
    marker = 'skr-codex-auto-title ' + digest(str(root).encode())[:16]
    start, end = '# BEGIN ' + marker, '# END ' + marker
    text = start + '\n'
    for h in hooks:
        text += '[hooks.state.' + json.dumps(h['key']) + ']\ntrusted_hash = ' + json.dumps(h['currentHash']) + '\nenabled = true\n'
    return start, end, text + end + '\n'


def load_hooks(path):
    data = json.loads(path.read_bytes()) if path.exists() else {'hooks': {}}
    if not isinstance(data, dict) or not isinstance(data.get('hooks'), dict):
        raise ValueError('invalid hooks.json; inspect existing file')
    for event in ('SessionStart', 'Stop'):
        groups = data['hooks'].get(event, [])
        if not isinstance(groups, list) or any(not isinstance(g, dict) or not isinstance(g.get('hooks'), list) for g in groups):
            raise ValueError('unsupported existing hook groups for ' + event)
    return data


def backend_data(binary, home):
    executable = shutil.which(binary) or str(Path(binary).expanduser().resolve())
    return encoded({'version': '1.0.0', 'codex': executable, 'codex_home': str(home),
                    'inference': 'official-ephemeral-app-server', 'rename_api': 'thread/name/set'})


def verify(root, binary, home, manifest):
    validate_layout(manifest)
    if manifest.get('root') != str(root) or manifest.get('user_config') != str(home / 'config.toml'):
        raise RuntimeError('installation root or CODEX_HOME changed; inspect manifest')
    if digest((root / '.codex/hooks/first_completion.py').read_bytes()) != manifest['script_sha256']:
        raise RuntimeError('installed script changed; do not reuse stale trust')
    if manifest.get('backend_sha256'):
        if digest((root / ADMIN / 'backend.json').read_bytes()) != manifest['backend_sha256']:
            raise RuntimeError('owned backend config changed; inspect before upgrade')
        hooks = load_hooks(hook_config(root, home))
        stop = [h for g in hooks['hooks']['Stop'] for h in g['hooks'] if h.get('command') == manifest['command']]
        if len(stop) != 1 or stop[0].get('async') is not True:
            raise RuntimeError('background Stop must be async')
    found = owned_hooks(probe(root, binary, home), root, manifest['command'], home)
    if not all(h.get('enabled') and h.get('trustStatus') == 'trusted' for h in found):
        raise RuntimeError('installed hooks are not both enabled/trusted')
    return {'configuration_trusted': True, 'desktop_e2e_verified': False,
            'hooks': [{k: h[k] for k in ('key', 'eventName', 'enabled', 'trustStatus')} for h in found]}


def install(root, binary, home, modules, session=None, model='gpt-6-luna', effort='low'):
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if session is not None and (not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session) or session == 'settings'):
        raise ValueError('invalid excluded session ID')
    if not modules.strip():
        raise ValueError('project-derived module guidance is required')
    script = root / '.codex/hooks/first_completion.py'
    settings_path = root / STATE / 'settings.json'
    manifest_path = root / ADMIN / 'installation.json'
    for path in (script, settings_path, manifest_path):
        if path.exists():
            raise RuntimeError('existing installation/file conflict, preserve state and inspect: ' + str(path))
    probe(root, binary, home)
    user_path = home / 'config.toml'
    user_before = read(user_path) or b''
    tomllib.loads(user_before.decode())
    hooks_path = hook_config(root, home)
    hooks_before = read(hooks_path)
    hooks = load_hooks(hooks_path)
    command = runtime_command(script)
    for event in ('SessionStart', 'Stop'):
        handler = {'type': 'command', 'command': command, 'timeout': 10,
                   'statusMessage': '后台检查首次完成命名'}
        if event == 'Stop':
            handler.update({'async': True, 'timeout': runtime_module().HOOK_TIMEOUT})
        hooks['hooks'].setdefault(event, []).append({'hooks': [handler]})
    changes = Changes(root)
    try:
        ignore_path = root / '.gitignore'
        ignore = read(ignore_path) or b''
        changes.set(ignore_path, ignore + IGNORE.encode())
        git = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=root, capture_output=True, text=True)
        if git.returncode == 0:
            for relative in (STATE + '/settings.json', ADMIN + '/installation.json'):
                check = subprocess.run(['git', 'check-ignore', '-q', '--no-index', relative], cwd=root)
                if check.returncode != 0:
                    raise RuntimeError('runtime directory not ignored: ' + relative)
        changes.set(script, ASSET.read_bytes(), 0o644)
        backend = backend_data(binary, home)
        changes.set(root / ADMIN / 'backend.json', backend, 0o600)
        changes.set(hooks_path, encoded(hooks))
        settings = {'scope': 'global' if root == global_root(home) else 'project', 'enabled': True, 'activated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                    'excluded_session_ids': [session] if session else [], 'module_guidance': modules.strip(),
                    'naming_model': model, 'naming_reasoning_effort': effort}
        changes.set(settings_path, encoded(settings), 0o600)
        found = owned_hooks(probe(root, binary, home), root, command, home)
        start, end, block = trust_block(root, found)
        if start in user_before.decode() or end in user_before.decode():
            raise RuntimeError('existing trust marker; inspect before reinstall')
        if read(user_path) not in (None, user_before):
            raise RuntimeError('user config changed during install')
        user_new = user_before.decode() + '\n' + block
        tomllib.loads(user_new)
        changes.set(user_path, user_new.encode(), 0o600)
        manifest = {'version': 1, 'queue_storage': 'shared-v1', 'skill_version': '1.0.0', 'backend_sha256': digest(backend),
                    'root': str(root), 'user_config': str(user_path), 'command': command,
                    'script_sha256': digest(ASSET.read_bytes()), 'trust_start': start, 'trust_end': end,
                    'trust_block': block, 'trust_keys': [h['key'] for h in found],
                    'original_hooks': json.loads(hooks_before) if hooks_before is not None else None,
                    'original_hooks_text': hooks_before.decode() if hooks_before is not None else None,
                    'backup': str(changes.backup)}
        changes.set(manifest_path, encoded(manifest), 0o600)
        result = verify(root, binary, home, manifest)
        result.update(backup=str(changes.backup), settings=str(settings_path))
        return result
    except BaseException as error:
        # Private backups remain after failure, so their ignore rules must remain too.
        changes.rollback(keep=(root / '.gitignore',))
        raise RuntimeError('install failed; backups at ' + str(changes.backup) + ': ' + str(error)) from error


def upgrade(root, binary, home, manifest):
    verify(root, binary, home, manifest)
    user_path = home / 'config.toml'
    user_before = user_path.read_text()
    without_trust, recovered_trust = without_owned_trust(user_before, manifest)
    changes = Changes(root)
    try:
        changes.set(root / '.codex/hooks/first_completion.py', ASSET.read_bytes(), 0o644)
        backend_path = root / ADMIN / 'backend.json'
        if backend_path.exists() and not manifest.get('backend_sha256'):
            raise RuntimeError('unknown backend config; inspect before upgrade')
        backend = backend_data(binary, home)
        changes.set(backend_path, backend, 0o600)
        hooks_path = hook_config(root, home)
        hooks_before = read(hooks_path)
        hooks = load_hooks(hooks_path)
        command = runtime_command(root / '.codex/hooks/first_completion.py')
        for event in ('SessionStart', 'Stop'):
            for group in hooks['hooks'][event]:
                for handler in group['hooks']:
                    if handler.get('command') == manifest['command']:
                        handler['command'] = command
                        handler['statusMessage'] = '后台检查首次完成命名'
                        if event == 'Stop':
                            handler.update({'async': True, 'timeout': runtime_module().HOOK_TIMEOUT})
        if read(hooks_path) != hooks_before:
            raise RuntimeError('project hooks changed during upgrade')
        changes.set(hooks_path, encoded(hooks))
        found = owned_hooks(probe(root, binary, home), root, command, home)
        _, _, block = trust_block(root, found)
        if user_path.read_text() != user_before:
            raise RuntimeError('user config changed during upgrade')
        user_new = without_trust + ('' if without_trust.endswith('\n') else '\n') + block
        tomllib.loads(user_new)
        changes.set(user_path, user_new.encode(), 0o600)
        updated = dict(manifest, command=command, queue_storage='shared-v1', script_sha256=digest(ASSET.read_bytes()), trust_block=block,
                       skill_version='1.0.0', backend_sha256=digest(backend),
                       trust_keys=[h['key'] for h in found], upgrade_backup=str(changes.backup))
        changes.set(root / ADMIN / 'installation.json', encoded(updated), 0o600)
        result = verify(root, binary, home, updated)
        migrated = runtime_module().migrate_jobs(root, global_root(home) / 'coordination', changes, manifest)
        result.update(action='upgrade', history_retained=True, backup=str(changes.backup), migrated_jobs=migrated, trust_recovered=recovered_trust)
        return result
    except BaseException:
        changes.rollback()
        raise


def manage(root, action, manifest):
    validate_layout(manifest)
    home = Path(manifest['user_config']).parent
    settings_path = root / STATE / 'settings.json'
    settings = json.loads(settings_path.read_bytes())
    if manifest.get('root') != str(root):
        raise RuntimeError('installation root changed')
    changes = Changes(root)
    try:
        if action in ('enable', 'disable'):
            settings['enabled'] = action == 'enable'
            changes.set(settings_path, encoded(settings), 0o600)
            return {'action': action, 'history_retained': True}
        user_path = Path(manifest['user_config'])
        user = user_path.read_text()
        new_user, recovered_trust = without_owned_trust(user, manifest)
        tomllib.loads(new_user)
        script = root / '.codex/hooks/first_completion.py'
        if digest(script.read_bytes()) != manifest['script_sha256']:
            raise RuntimeError('owned hook script changed; inspect before uninstall')
        backend_path = root / ADMIN / 'backend.json'
        if manifest.get('backend_sha256') and digest(backend_path.read_bytes()) != manifest['backend_sha256']:
            raise RuntimeError('owned backend config changed; inspect before uninstall')
        hooks_path = hook_config(root, home)
        hooks = load_hooks(hooks_path)
        removed = 0
        for event in ('SessionStart', 'Stop'):
            groups = hooks['hooks'].get(event, [])
            for group in groups:
                old = group['hooks']
                group['hooks'] = [h for h in old if h.get('command') != manifest['command']]
                removed += len(old) - len(group['hooks'])
            hooks['hooks'][event] = [g for g in groups if g['hooks']]
            if not hooks['hooks'][event]:
                del hooks['hooks'][event]
        if removed != 2:
            raise RuntimeError('owned hook handlers changed; inspect before uninstall')
        original = manifest['original_hooks']
        if hooks == (original if original is not None else {'hooks': {}}):
            hooks_data = manifest['original_hooks_text'].encode() if original is not None else None
        else:
            hooks_data = encoded(hooks)
        if user_path.read_text() != user:
            raise RuntimeError('user config changed during uninstall')
        settings['enabled'] = False
        changes.set(settings_path, encoded(settings), 0o600)
        changes.set(hooks_path, hooks_data)
        changes.set(script, None)
        if manifest.get('backend_sha256'):
            changes.set(backend_path, None)
        changes.set(user_path, new_user.encode(), 0o600)
        migrated = runtime_module().migrate_jobs(root, global_root(home) / 'coordination', changes, manifest)
        manifest['queue_storage'] = 'shared-v1'
        manifest['uninstalled'] = True
        changes.set(root / ADMIN / 'installation.json', encoded(manifest), 0o600)
        return {'action': 'uninstall', 'history_retained': True, 'backup': str(changes.backup), 'migrated_jobs': migrated, 'trust_recovered': recovered_trust}
    except BaseException:
        changes.rollback()
        raise


@contextlib.contextmanager
def configuration_lock(home):
    """Serialize this installer's cross-project changes to a shared user config."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (home / '.first-completion-install.lock').open('a') as stream:
        os.chmod(stream.fileno(), 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def validate_schema(directory):
    required = {'ThreadStartParams': {'ephemeral', 'model', 'allowProviderModelFallback', 'baseInstructions', 'developerInstructions', 'cwd', 'approvalPolicy', 'sandbox', 'config'},
                'TurnStartParams': {'threadId', 'input', 'model', 'effort', 'outputSchema'},
                'ThreadSetNameParams': {'threadId', 'name'},
                'ThreadReadParams': {'threadId', 'includeTurns'},
                'HooksListParams': {'cwds'},
                'ModelListParams': {'cursor', 'limit', 'includeHidden'}}
    for name, fields in required.items():
        candidates = list(directory.rglob(name + '.json'))
        if len(candidates) != 1:
            raise RuntimeError('unsupported CLI schema: ' + name)
        properties = json.loads(candidates[0].read_text()).get('properties', {})
        if not fields.issubset(properties):
            raise RuntimeError('unsupported CLI fields: ' + name)
    return sorted(required)


def doctor(root, binary, home, model='gpt-6-luna', effort='low'):
    """Check real schema/config/model catalog without inference or renaming."""
    with tempfile.TemporaryDirectory(prefix='codex-title-schema-') as temporary:
        subprocess.run([binary, 'app-server', 'generate-json-schema', '--experimental', '--out', temporary],
                       check=True, capture_output=True, timeout=45)
        checked = validate_schema(Path(temporary))
    discovered = probe(root, binary, home)
    spec = importlib.util.spec_from_file_location('title_runtime', ASSET)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    rpc = runtime.Rpc(binary, str(home), model, effort)
    try:
        models, cursor, seen = [], None, set()
        while True:
            page = rpc.call('model/list', {'includeHidden': True, 'limit': 100, 'cursor': cursor})
            models.extend(page['data'])
            cursor = page.get('nextCursor')
            if not cursor:
                break
            if cursor in seen:
                raise RuntimeError('model catalog pagination repeated')
            seen.add(cursor)
        match = next((m for m in models if m.get('model') == model), None)
        if not match or effort not in [e['reasoningEffort'] for e in match.get('supportedReasoningEfforts', [])]:
            raise RuntimeError('requested model/effort absent from current account catalog: ' + model + '/' + effort)
    finally:
        rpc.close()
    return {'cli_version': discovered['cli_version'], 'schema_checks': checked,
            'model': model, 'effort': effort, 'configuration_readable': True,
            'inference_verified': False, 'desktop_e2e_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='skr-codex-auto-title 1.0.0')
    parser.add_argument('action', choices=('doctor', 'probe', 'install', 'verify', 'upgrade', 'enable', 'disable', 'uninstall', 'project-enable', 'project-disable', 'project-status', 'project-modules', 'project-modules-clear'))
    parser.add_argument('--scope', choices=('project', 'global'), default='project')
    parser.add_argument('--root', type=Path, help='target project; required for project scope and per-project switches')
    parser.add_argument('--codex', default=shutil.which('codex') or 'codex')
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    parser.add_argument('--modules-file', type=Path, help='UTF-8 project terminology; optional for direct installation')
    parser.add_argument('--exclude-session-id', help='installation chat ID; omit when installing from a terminal')
    parser.add_argument('--model', default='gpt-6-luna', help='explicit model; never silently falls back')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--trust-hooks', action='store_true', help='authorize writing trust for these reviewed hook commands')
    args = parser.parse_args()
    home = args.codex_home.expanduser().resolve()
    policy_action = args.action.startswith('project-')
    if (args.scope == 'project' or policy_action) and args.root is None:
        parser.error('--root is required for project scope and per-project switches')
    project = args.root.expanduser().resolve() if args.root else None
    if project is not None and not project.is_dir():
        parser.error('project root does not exist')
    if policy_action and args.scope != 'global':
        parser.error('per-project switches require --scope global')
    if args.scope == 'global' and project is not None and not policy_action:
        parser.error('--root is only used with global per-project switches')
    root = global_root(home) if args.scope == 'global' else project
    if args.action in ('install', 'upgrade') and not args.trust_hooks:
        parser.error('review the hook source, then supply --trust-hooks to authorize installing trusted commands')
    if args.scope == 'global' and args.action == 'install' and args.modules_file:
        parser.error('global install uses generic guidance; use project-modules --scope global --root <project> for project terms')
    try:
        if args.scope == 'global' and args.action == 'install':
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if policy_action:
            manifest_path = root / ADMIN / 'installation.json'
            if not manifest_path.exists():
                raise RuntimeError('install the global hook before managing project switches')
            validate_layout(json.loads(manifest_path.read_text()))
            with configuration_lock(home):
                if args.action in ('project-modules', 'project-modules-clear'):
                    text = args.modules_file.read_text(encoding='utf-8') if args.modules_file else None
                    result = project_modules(home, project, args.action, text)
                else:
                    result = project_policy(home, project, args.action)
                    if args.action == 'project-status':
                        _, result['module_source'] = runtime_module().project_terms(home, project, '')
        elif args.action in ('doctor', 'probe'):
            probe_root = root if root.is_dir() else Path.cwd()
            result = doctor(probe_root, args.codex, home, args.model, args.effort) if args.action == 'doctor' else probe(probe_root, args.codex, home)
        else:
            # One lock order for every mutation: shared config before project state.
            with configuration_lock(home), lock(root):
                if args.action == 'install':
                    doctor(root, args.codex, home, args.model, args.effort)
                    modules = args.modules_file.read_text(encoding='utf-8') if args.modules_file else (
                        '根据用户请求和实际成果提炼准确的主要模块；项目配置、公共能力、文档仅在与成果相符时使用。'
                        '这是通用回退，未读取项目业务文档，不假称已定制项目术语。')
                    result = install(root, args.codex, home, modules, args.exclude_session_id, args.model, args.effort)
                else:
                    manifest = json.loads((root / ADMIN / 'installation.json').read_bytes())
                    if manifest.get('uninstalled'):
                        raise RuntimeError('installation already uninstalled; retained state requires explicit migration; use disable for temporary stops')
                    if args.action == 'verify':
                        result = verify(root, args.codex, home, manifest)
                    elif args.action == 'upgrade':
                        settings = json.loads((root / STATE / 'settings.json').read_text())
                        doctor(root, args.codex, home, settings.get('naming_model', 'gpt-6-luna'), settings.get('naming_reasoning_effort', 'low'))
                        result = upgrade(root, args.codex, home, manifest)
                    else:
                        result = manage(root, args.action, manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
