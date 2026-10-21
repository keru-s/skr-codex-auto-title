"""Opt-in real model/API test. Creates one disposable conversation; uses account quota."""
import argparse
import contextlib
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import time

ASSET = Path(__file__).resolve().parents[1] / "skills/skr-codex-auto-title/assets/first_completion.py"
spec = importlib.util.spec_from_file_location("live_runtime", ASSET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def deliver(rpc, thread, text):
    rpc.deadline = time.monotonic() + 120
    deadline = time.monotonic() + 90
    started = rpc.call("turn/start", {"threadId": thread, "model": rpc.model, "effort": rpc.effort,
                                     "input": [{"type": "text", "text": text}]})
    turn = started["turn"]["id"]
    output = None
    while True:
        event = rpc.events.pop(0) if rpc.events else rpc.receive(deadline)
        if "id" in event and "method" in event:
            raise RuntimeError("unexpected tool/approval request")
        params = event.get("params", {})
        if params.get("threadId") != thread:
            continue
        if event.get("method") == "item/completed":
            item = params.get("item", {})
            if item.get("type") not in ("userMessage", "agentMessage", "reasoning"):
                raise RuntimeError("unexpected business test tool")
            if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                output = item["text"]
        if event.get("method") == "turn/completed" and params["turn"]["id"] == turn:
            assert params["turn"]["status"] == "completed" and output
            return turn, output


@contextlib.contextmanager
def preserve_failure(root, report, session):
    try:
        yield
    except BaseException as error:
        directory = root / "runtime/skr-codex-auto-title"
        m.atomic_json(report, {"failed": True, "test_thread_id": session(), "error": str(error),
                              "logs": [p.read_text() for p in (directory / "logs").glob("*.jsonl")]})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--keep-test-thread", action="store_true", help="observer must archive it after native UI readback")
    args = parser.parse_args()
    rpc = m.Rpc(args.codex, str(args.codex_home))
    sid = None
    success = False
    try:
        with tempfile.TemporaryDirectory(prefix="codex-title-live-") as temporary, preserve_failure(Path(temporary), args.report, lambda: sid):
            root = Path(temporary).resolve()
            directory = root / "runtime/skr-codex-auto-title"
            m.atomic_json(directory / "settings.json", {"enabled": True, "activated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "excluded_session_ids": [], "module_guidance": "并发控制；项目配置；文档", "naming_model": "gpt-6-luna", "naming_reasoning_effort": "low"})
            m.atomic_json(root / "runtime/skr-codex-auto-title-install/backend.json", {"codex": args.codex, "codex_home": str(args.codex_home)})
            started = rpc.call("thread/start", {"model": "gpt-6-luna", "ephemeral": False, "allowProviderModelFallback": False,
                "cwd": str(root), "approvalPolicy": "never", "sandbox": "read-only",
                "baseInstructions": "你是临时验证助手。只按用户要求用文字答复，不调用工具，不修改文件。", "config": {"features.hooks": False, "web_search": "disabled"}})
            sid = started["thread"]["id"]
            print(json.dumps({"test_thread_id": sid}), flush=True)
            checks = []
            title_before = rpc.call("thread/read", {"threadId": sid, "includeTurns": False})["thread"].get("name")
            prompts = ["完整任务是解释我稍后指定的术语，目前术语缺失，请只追问术语名称，不自行选择。",
                       "请用三句话解释乐观锁，以商品标题和版本号举例。这就是本次完整任务，只交付解释。",
                       "请用两句话解释悲观锁。这就是下一个完整任务，只交付解释。"]
            frozen = None
            for index, prompt in enumerate(prompts):
                turn, output = deliver(rpc, sid, prompt)
                thread = rpc.call("thread/read", {"threadId": sid, "includeTurns": False})["thread"]
                transcript = thread.get("path") or started["thread"].get("path")
                assert transcript, "official transcript path missing"
                naming = m.Naming(root)
                naming.shared = root / 'test-coordination'
                began = time.monotonic()
                response = naming.hook({"hook_event_name": "Stop", "session_id": sid, "turn_id": turn,
                    "transcript_path": transcript, "cwd": str(root), "last_assistant_message": output})
                assert response == {}
                path = directory / (sid + ".json")
                state = json.loads(path.read_text())
                if not state.get("assessment"):
                    logs = [p.read_text() for p in (directory / "logs").glob("*.jsonl")]
                    m.atomic_json(args.report, {"failed": True, "test_thread_id": sid, "state": state, "logs": logs})
                    raise RuntimeError("Background assessment missing: " + "\n".join(logs))
                rpc.deadline = time.monotonic() + 120
                observed = rpc.call("thread/read", {"threadId": sid, "includeTurns": False})["thread"].get("name")
                if index == 0:
                    assert state["assessment"]["completed"] is False and not state.get("done")
                    assert observed == title_before
                elif index == 1:
                    assert state.get("done") and state["first_completed_turn"] == turn and observed == state["title"]
                    frozen = path.read_bytes()
                else:
                    assert frozen == path.read_bytes() and observed == state["title"]
                checks.append({"naming_elapsed_s": round(time.monotonic() - began, 3), "turn": turn, "response": output, "title": observed, "done": state.get("done", False)})
            evidence = [json.loads(p.read_text()) for p in (directory / "evidence" / sid).glob("*.json")]
            assert len([e for e in evidence if "inference" in e]) == 2
            m.atomic_json(args.report, {"test_thread_id": sid, "checks": checks, "evidence": evidence,
                "trigger": "manual invocation with real completed turn and official transcript", "native_async_stop_e2e": False})
            success = True
            print(json.dumps({"passed": True, "test_thread_id": sid, "title": state["title"]}), flush=True)
    finally:
        # Release this client's writer before a separate cleanup client archives the thread.
        rpc.close()
        if sid and (not args.keep_test_thread or not success):
            cleanup = m.Rpc(args.codex, str(args.codex_home))
            try:
                cleanup.call("thread/archive", {"threadId": sid})
            finally:
                cleanup.close()



if __name__ == "__main__":
    main()
