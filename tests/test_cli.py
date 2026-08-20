#!/usr/bin/env python3
"""Focused tests for the user-facing tauceti-review CLI."""
import json
import os
import pathlib
import sys
import tempfile
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "runner"))
import cli  # noqa: E402


def test_explicit_codex_profile_reaches_inner_engine():
    assert cli.codex_review_args("gpt-5.6-sol", "high") == [
        "--codex-model", "gpt-5.6-sol", "--codex-effort", "high"
    ]
    assert cli.codex_review_args("", "") == []


def test_pr_ref_oids_uses_old_gh_compatible_rest_fields():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return types.SimpleNamespace(
            stdout=json.dumps({"head": {"sha": "head-sha"}, "base": {"sha": "base-sha"}})
        )

    original = cli.run
    cli.run = fake_run
    try:
        assert cli.pr_ref_oids("owner/repo", 42) == ("head-sha", "base-sha")
    finally:
        cli.run = original

    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[:2] == ["gh", "api"] and cmd[2].endswith("/pulls/42"), calls
    assert kwargs.get("capture") is True


def test_pr_ref_lookup_does_not_require_new_pr_view_field():
    source = pathlib.Path(cli.__file__).read_text()
    assert '"headRefOid,baseRefOid"' not in source


def test_exact_configured_engine_source_precedes_installed_tree():
    original_engine_at = cli.engine_at
    old_env = os.environ.copy()
    calls = []
    try:
        os.environ["TAUCETI_REVIEW_ENGINE_REPO"] = "owner/fork"
        os.environ["TAUCETI_REVIEW_ENGINE_REF"] = "a" * 40
        os.environ.pop("TAUCETI_REVIEW_DIR", None)

        def fake_engine_at(sha, repo=cli.REVIEW_REPO):
            calls.append((sha, repo))
            return pathlib.Path("/exact-engine")

        cli.engine_at = fake_engine_at
        assert cli.resolve_repo_dir(None) == pathlib.Path("/exact-engine")
    finally:
        cli.engine_at = original_engine_at
        os.environ.clear()
        os.environ.update(old_env)

    assert calls == [("a" * 40, "owner/fork")]


def test_explicit_checkout_precedes_configured_engine_source():
    old_env = os.environ.copy()
    try:
        os.environ["TAUCETI_REVIEW_ENGINE_REPO"] = "owner/fork"
        os.environ["TAUCETI_REVIEW_ENGINE_REF"] = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            checkout = pathlib.Path(directory)
            (checkout / "rubrics").mkdir()
            (checkout / "runner").mkdir()
            (checkout / "runner" / "review.py").touch()
            assert cli.resolve_repo_dir(checkout) == checkout.resolve()
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def test_configured_engine_source_precedes_ambient_checkout():
    original_engine_at = cli.engine_at
    old_env = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory() as directory:
            checkout = pathlib.Path(directory)
            (checkout / "rubrics").mkdir()
            (checkout / "runner").mkdir()
            (checkout / "runner" / "review.py").touch()
            os.environ["TAUCETI_REVIEW_DIR"] = str(checkout)
            os.environ["TAUCETI_REVIEW_ENGINE_REPO"] = "owner/fork"
            os.environ["TAUCETI_REVIEW_ENGINE_REF"] = "e" * 40
            cli.engine_at = lambda sha, repo=cli.REVIEW_REPO: pathlib.Path(
                f"/exact/{repo}/{sha}"
            )
            assert cli.resolve_repo_dir(None) == pathlib.Path(
                f"/exact/owner/fork/{'e' * 40}"
            )
    finally:
        cli.engine_at = original_engine_at
        os.environ.clear()
        os.environ.update(old_env)


def test_configured_engine_source_fails_closed_on_partial_or_moving_ref():
    old_env = os.environ.copy()
    cases = [
        {"TAUCETI_REVIEW_ENGINE_REPO": "owner/fork"},
        {"TAUCETI_REVIEW_ENGINE_REF": "c" * 40},
        {
            "TAUCETI_REVIEW_ENGINE_REPO": "owner/fork",
            "TAUCETI_REVIEW_ENGINE_REF": "dev",
        },
    ]
    try:
        for values in cases:
            os.environ.pop("TAUCETI_REVIEW_ENGINE_REPO", None)
            os.environ.pop("TAUCETI_REVIEW_ENGINE_REF", None)
            os.environ.update(values)
            try:
                cli.resolve_repo_dir(None)
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError(f"unsafe configured engine source accepted: {values}")
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def test_engine_cache_key_includes_repository_and_commit():
    original_cache = cli.CACHE_DIR
    original_run = cli.run
    calls = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            cli.CACHE_DIR = pathlib.Path(directory)

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if cmd[:2] == ["git", "init"]:
                    root = pathlib.Path(cmd[-1])
                    (root / "rubrics").mkdir()
                    (root / "runner").mkdir()
                    (root / "runner" / "review.py").touch()
                if cmd[-2:] == ["rev-parse", "HEAD"]:
                    return types.SimpleNamespace(stdout="d" * 40 + "\n", returncode=0)
                return types.SimpleNamespace(stdout="", stderr="", returncode=0)

            cli.run = fake_run
            result = cli.engine_at("d" * 40, "owner/fork")
            assert result == pathlib.Path(directory) / "engines" / "owner__fork" / ("d" * 40)
    finally:
        cli.CACHE_DIR = original_cache
        cli.run = original_run

    assert any("https://github.com/owner/fork" in command for command in calls)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\nall {len(tests)} CLI checks passed")
