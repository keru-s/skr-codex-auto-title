import concurrent.futures
import importlib.util
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import test_background as background
import test_install as installation

runtime = background.m
installer = installation.m
ROOT = Path(__file__).resolve().parents[1]


class RuntimeGuardTests(unittest.TestCase):
    setUp = background.BackgroundTests.setUp
    state = background.BackgroundTests.state

    def test_should_keep_failure_at_end_of_long_final_response(self):
        final = "Implementation details. " * 260 + "Still blocked; approval required."
        self.assertGreater(len(final), 5000)
        sample = self.naming.packet(dict(self.event, last_assistant_message=final))["final_response"]
        self.assertTrue(sample.endswith("Still blocked; approval required."))
        self.assertLessEqual(len(sample), 1600)

    def test_should_excerpt_oversized_context_before_inference(self):
        self.naming.hook(dict(self.event, last_assistant_message="x" * 33000))
        self.assertEqual(self.rpc.assessments, 1)
        self.assertEqual(self.rpc.renames, 1)

    def test_should_reject_missing_user_request(self):
        self.transcript.write_text(self.transcript.read_text().splitlines()[0] + "\n")
        with self.assertRaisesRegex(ValueError, "No user request"):
            self.naming.hook(self.event)
        self.assertEqual(self.rpc.assessments, 0)

    def test_should_ignore_subagent_old_session_and_unrelated_root(self):
        original = json.loads(self.transcript.read_text().splitlines()[0])
        for field, value in [("source", {"subagent": {"parent_thread_id": "parent"}}),
                             ("timestamp", "2020-01-01T00:00:00Z"), ("cwd", "/unrelated-project")]:
            row = json.loads(json.dumps(original))
            row["payload"][field] = value
            self.transcript.write_text(json.dumps(row) + "\n")
            self.assertEqual(self.naming.hook(self.event), {})
        self.assertEqual(self.rpc.assessments, 0)

    def test_should_preserve_done_after_resume_and_compaction(self):
        self.naming.hook(self.event)
        path = self.directory / "test-session.json"
        before = path.read_bytes()
        for source in ("resume", "compact"):
            self.naming.hook(dict(self.event, hook_event_name="SessionStart", source=source))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.rpc.renames, 1)

    def test_should_ignore_interrupt_and_subagent_stop(self):
        for event in ("Interrupt", "SubagentStop"):
            self.assertEqual(self.naming.hook(dict(self.event, hook_event_name=event)), {})
        self.assertEqual(self.rpc.assessments, 0)

    def test_should_collect_user_inputs_without_synthetic_context(self):
        rows = [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": t}]}}
                for t in ("# AGENTS.md instructions for test", "<environment_context>runtime</environment_context>", "Explain optimistic locking")]
        with self.transcript.open("a") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        self.assertEqual(runtime.user_requests(self.transcript), ["请解释幂等性", "Explain optimistic locking"])

    def test_should_emit_only_empty_json_on_runtime_failure(self):
        command = [sys.executable, str(ROOT / "skills/skr-codex-auto-title/assets/first_completion.py"), "hook", "--root", str(self.root)]
        result = subprocess.run(command, input="not-json", text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})

    def test_should_remove_legacy_control_entrypoint(self):
        self.assertFalse(hasattr(self.naming, "control"))


class PackagingTests(unittest.TestCase):
    def test_copied_skill_runs_without_repository(self):
        with tempfile.TemporaryDirectory(prefix='copied skill ') as folder:
            copied = Path(folder) / 'skr-codex-auto-title'
            shutil.copytree(ROOT / 'skills/skr-codex-auto-title', copied)
            for entry in (copied / 'scripts/install.py',):
                result = subprocess.run([sys.executable, str(entry), '--version'],
                                        cwd=folder, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'skr-codex-auto-title 1.0.0')
            result = subprocess.run([sys.executable, str(copied / 'assets/first_completion.py'),
                                     'hook', '--root', folder], input='not-json',
                                    cwd=folder, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})

    def test_installation_ownership_uses_project_name(self):
        start, end, block = installer.trust_block(Path('/example/project'), [])
        self.assertTrue(start.startswith('# BEGIN skr-codex-auto-title '))
        self.assertTrue(end.startswith('# END skr-codex-auto-title '))
        self.assertIn(start, block)

    def test_should_require_explicit_hook_trust_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(ROOT / "install.py"), "install", "--root", folder], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--trust-hooks", result.stderr)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_should_reject_missing_protocol_capability(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "unsupported CLI schema"):
                installer.validate_schema(Path(folder))

    def test_should_reject_schema_without_developer_instructions(self):
        fields = {"ThreadStartParams": ["ephemeral", "model", "allowProviderModelFallback", "baseInstructions", "cwd", "approvalPolicy", "sandbox", "config"],
                  "TurnStartParams": ["threadId", "input", "model", "effort", "outputSchema"],
                  "ThreadSetNameParams": ["threadId", "name"], "ThreadReadParams": ["threadId", "includeTurns"],
                  "HooksListParams": ["cwds"], "ModelListParams": ["cursor", "limit", "includeHidden"]}
        with tempfile.TemporaryDirectory() as folder:
            for name, props in fields.items():
                (Path(folder) / (name + ".json")).write_text(json.dumps({"properties": {key: {} for key in props}}))
            with self.assertRaisesRegex(RuntimeError, "ThreadStartParams"):
                installer.validate_schema(Path(folder))

    def test_should_serialize_different_projects_sharing_user_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            entered = threading.Event()
            attempted = threading.Event()
            def second_project():
                attempted.set()
                with installer.configuration_lock(home):
                    entered.set()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                with installer.configuration_lock(home):
                    task = pool.submit(second_project)
                    self.assertTrue(attempted.wait(2))
                    self.assertFalse(entered.wait(0.1))
                task.result(3)
                self.assertTrue(entered.is_set())

    def test_should_restore_file_mode_on_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "existing.txt"
            path.write_text("before")
            path.chmod(0o644)
            change = installer.Changes(root)
            change.set(path, b"after", 0o600)
            change.rollback()
            self.assertEqual(path.read_text(), "before")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
