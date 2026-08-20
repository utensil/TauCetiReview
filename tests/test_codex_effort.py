#!/usr/bin/env python3
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runner"))

import reviewers  # noqa: E402


captured = {}


def fake_sh(cmd, **kwargs):
    captured["cmd"] = cmd
    captured["env"] = kwargs["env"]
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


real_sh = reviewers.sh
try:
    reviewers.sh = fake_sh
    result = reviewers.run_codex(
        "review",
        "/tmp",
        "gpt-5.6-sol",
        {
            "PATH": "/usr/bin",
            "TAUCETI_INTERNAL_CODEX_REVIEW_EFFORT": "high",
        },
    )
    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="high"' in captured["cmd"]
    assert "TAUCETI_INTERNAL_CODEX_REVIEW_EFFORT" not in captured["env"]
    assert result["reasoning_effort"] == "high"

    captured.clear()
    result = reviewers.run_codex("review", "/tmp", "gpt-5.6-sol", {"PATH": "/usr/bin"})
    assert not any("model_reasoning_effort" in arg for arg in captured["cmd"])
    assert "reasoning_effort" not in result
finally:
    reviewers.sh = real_sh

print("codex effort tests: ok")
