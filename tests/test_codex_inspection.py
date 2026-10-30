#!/usr/bin/env python3
"""Inspection diagnostics remain truthful without overriding structured verdicts."""
import json
import pathlib
import sys
import types
import contextlib
import io
import re
import shutil
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "runner")]
import reviewers
import review
import test_billing as tb


def run(code, output="", status="completed"):
    captured = {}
    events = [
        {"type": "item.completed", "item": {"type": "command_execution",
         "exit_code": code, "status": status, "aggregated_output": output}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "approve"}},
    ]
    def fake_sh(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stderr="",
                                     stdout="\n".join(map(json.dumps, events)))
    original = reviewers.sh
    try:
        reviewers.sh = fake_sh
        result = reviewers.run_codex("review", "/tmp", "gpt-6.1-sol", {
            "PATH": "/opt/reviewer-tools/bin:/usr/bin",
            "TMPDIR": "/private/reviewer tmp",
            "PRIVATE_SETTING": "must-not-reach-tools",
        })
    finally:
        reviewers.sh = original
    return result, captured["cmd"]


result, cmd = run(0)
assert result["returncode"] == 0 and result["text"] == "approve"
assert cmd[cmd.index("-s") + 1] == "read-only"
assert "shell_environment_policy.inherit=none" in cmd
assert 'shell_environment_policy.set.PATH="/opt/reviewer-tools/bin:/usr/bin"' in cmd
assert 'shell_environment_policy.set.TMPDIR="/private/reviewer tmp"' in cmd
assert not any("PRIVATE_SETTING" in arg or "must-not-reach-tools" in arg for arg in cmd)
assert result["tool_trace_meta"]["command_completed"] == 1
assert result["tool_trace_meta"]["successful_calls"] == 1
assert "inspection_error" not in result
assert not result["tool_trace_meta"]["inspection_unavailable"]

for code, output in [(127, "zsh:1: command not found: cat"),
                     (127, ["missing command"]),
                     (127, {"message": "missing command"}),
                     (1, "sandbox-exec: sandbox_apply: Operation not permitted"),
                     (None, "Failed to create unified exec process")]:
    result, _ = run(code, output, "failed")
    assert result["returncode"] == 0 and result["text"] == "approve"
    assert "inspection_error" not in result
    assert result["tool_trace_meta"]["successful_calls"] == 0
    assert result["tool_trace"][0]["ok"] is False
    assert result["tool_trace_meta"]["inspection_unavailable"]

result, _ = run(1, "", "failed")  # grep with no matches is still usable inspection
assert result["returncode"] == 0 and result["text"] == "approve"
assert "inspection_error" not in result
assert not result["tool_trace_meta"]["inspection_unavailable"]


def check_round(command_code, command_output, *, cli_code=0, terminal_failure=False,
                recover=False, verdict="approve"):
    """Exercise actual Codex parsing through the engine retry and publication plan."""
    d, rd, store = tb._workspace(["correctness"])
    calls = []
    sentinel = "PRIVATE-DIAGNOSTIC-SENTINEL"

    def fake_sh(cmd, **kwargs):
        marker = re.search(r"TAUCETI-VERDICT-\w+", kwargs["stdin_text"]).group(0)
        calls.append(cmd)
        failed = not (recover and len(calls) > 1)
        events = [
            {"type": "item.completed", "item": {"type": "command_execution",
             "exit_code": command_code, "status": "completed" if command_code == 0 else "failed",
             "command": sentinel, "aggregated_output": command_output}},
            {"type": "item.completed", "item": {"type": "agent_message",
             "text": marker + "\n" + json.dumps({"verdict": verdict,
                 "summary": "Filesystem inspection was unavailable because the sandbox failed to start.",
                 "findings": []})}},
        ]
        if terminal_failure and failed:
            events.append({"type": "turn.failed", "error": {"message": sentinel}})
        return types.SimpleNamespace(returncode=cli_code if failed else 0,
                                     stderr=sentinel if failed and cli_code else "",
                                     stdout="\n".join(map(json.dumps, events)))

    plan = d / "plan.json"
    try:
        with patch.object(reviewers, "sh", fake_sh), patch.object(
            review, "reviewer_env", return_value=({}, None)
        ), patch.object(review, "sweep_rev_homes"), contextlib.redirect_stderr(io.StringIO()):
            ledger = tb._run(store, rd, d / "diff.txt", mode="commit", rubrics=["correctness"],
                             extra=["--providers", "codex", "--codex-model", "gpt-6.1-sol",
                                    "--codex-effort", "high", "--post-plan-file", str(plan)])
        native_failure = bool(cli_code or terminal_failure)
        expected = "error" if native_failure and not recover else verdict
        assert ledger["prs"]["1"]["state"]["correctness"]["verdict"] == expected
        assert len(calls) == (2 if native_failure else 1)
        assert all('model_reasoning_effort="high"' in cmd for cmd in calls)
        record = json.loads((store / "reviews/1/1/correctness.json").read_text())
        assert all(a["reasoning_effort"] == "high" for a in record["attempts"])
        assert bool(json.loads(plan.read_text())["review_error"]) == (expected == "error")
        assert (record["verdict_obj"] is None) == (expected == "error")
        if command_code != 0:
            assert record["tool_trace"][0]["ok"] is False
            assert record["tool_trace_meta"]["successful_calls"] == 0
        if command_code == 127:
            assert record["tool_trace_meta"]["inspection_unavailable"]
        for path in store.rglob("*"):
            if path.is_file():
                assert sentinel not in path.read_text(), path
    finally:
        shutil.rmtree(d)


# Child command failures and diagnostic text are evidence, never a second verdict.
for verdict in ("approve", "request_changes", "block"):
    for code, output in ((1, "no grep match"), (127, "PRIVATE-DIAGNOSTIC-SENTINEL"),
                         (None, "Failed to create unified exec process PRIVATE-DIAGNOSTIC-SENTINEL")):
        check_round(code, output, verdict=verdict)

# Actual process failure and native turn.failed stay retryable, even beside valid verdict text.
for recover in (False, True):
    check_round(0, "", cli_code=127, recover=recover)
    check_round(0, "", terminal_failure=True, recover=recover)
print("codex inspection tests: ok")
