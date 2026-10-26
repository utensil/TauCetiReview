#!/usr/bin/env python3
"""A successful model response must not hide an unusable inspection shell."""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "runner"))
import reviewers


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
        result = reviewers.run_codex("review", "/tmp", "gpt-6.1-sol", {})
    finally:
        reviewers.sh = original
    return result, captured["cmd"]


result, cmd = run(0)
assert result["returncode"] == 0 and result["text"] == "approve"
assert cmd[cmd.index("-s") + 1] == "read-only"
assert "shell_environment_policy.inherit=none" in cmd
assert f'shell_environment_policy.set.PATH="{reviewers.CODEX_TOOL_PATH}"' in cmd
assert all(path.startswith("/") for path in reviewers.CODEX_TOOL_PATH.split(":"))
assert result["tool_trace_meta"]["command_completed"] == 1

for code, output in [(127, "zsh:1: command not found: cat"),
                     (1, "sandbox-exec: sandbox_apply: Operation not permitted"),
                     (None, "Failed to create unified exec process")]:
    result, _ = run(code, output, "failed")
    assert result["returncode"] != 0 and result["text"] == ""
    assert result["tool_trace_meta"]["inspection_unavailable"]

result, _ = run(1, "", "failed")  # grep with no matches is still usable inspection
assert result["returncode"] == 0 and result["text"] == "approve"
print("codex inspection tests: ok")
