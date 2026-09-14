#!/usr/bin/env python3
"""The isolated reviewer environment keeps login identity without inheriting personal config."""

import os
import pathlib
import sys
import tempfile
import sqlite3
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "runner"))
import reviewers  # noqa: E402


def test_reviewer_env_keeps_public_login_identity():
    old = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory() as home:
            os.environ.clear()
            os.environ.update(
                HOME=home,
                PATH="/usr/bin",
                LANG="C.UTF-8",
                USER="alice",
                LOGNAME="login-alice",
                PERSONAL_SETTING="must-not-leak",
            )
            env, isolated_home = reviewers.reviewer_env("claude", {"anthropic": "test-key"})
    finally:
        os.environ.clear()
        os.environ.update(old)

    assert env["USER"] == "alice"
    assert env["LOGNAME"] == "login-alice"
    assert "PERSONAL_SETTING" not in env
    assert set(env) == {"PATH", "HOME", "LANG", "CI", "USER", "LOGNAME", "ANTHROPIC_API_KEY"}
    reviewers.cleanup_rev_home(isolated_home)


def test_logname_is_a_user_fallback():
    old = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory() as home:
            os.environ.clear()
            os.environ.update(HOME=home, PATH="/usr/bin", LOGNAME="fallback-user")
            env, isolated_home = reviewers.reviewer_env("claude", {"anthropic": "test-key"})
    finally:
        os.environ.clear()
        os.environ.update(old)

    assert env["USER"] == "fallback-user"
    assert env["LOGNAME"] == "fallback-user"
    reviewers.cleanup_rev_home(isolated_home)


def _check_codex_subscription_home(setting, with_auth=True):
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        default_home = root / ".codex"
        selected_home = root / "selected profile"
        for source in (default_home, selected_home):
            source.mkdir()
            (source / "auth.json").write_text(source.name)
            (source / "config.toml").write_text("personal config")
            (source / "AGENTS.md").write_text("personal instructions")
        source_home = selected_home if setting else default_home
        if not with_auth:
            (source_home / "auth.json").unlink()
        environment = {"HOME": str(root), "PATH": "/usr/bin"}
        if setting is not None:
            environment["CODEX_HOME"] = str(selected_home) if setting else ""
        with patch.dict(os.environ, environment, clear=True), patch.object(
            reviewers, "REV_HOME_BASE", str(root / "reviewer-homes")
        ):
            env, isolated_home = reviewers.reviewer_env("codex", {}, subscription=True)
            try:
                isolated_codex = pathlib.Path(isolated_home) / ".codex"
                assert env["HOME"] == isolated_home
                if with_auth:
                    assert env["CODEX_HOME"] == str(isolated_codex)
                    assert (isolated_codex / "auth.json").read_text() == source_home.name
                    assert {path.name for path in isolated_codex.iterdir()} == {"auth.json"}
                    (isolated_codex / "auth.json").write_text("changed copy")
                    assert (source_home / "auth.json").read_text() == source_home.name
                else:
                    assert env["CODEX_HOME"] == str(source_home)
                    assert not (isolated_codex / "auth.json").exists()
                    assert (default_home / "auth.json").read_text() == default_home.name
            finally:
                reviewers.cleanup_rev_home(isolated_home)


def test_codex_subscription_honors_selected_home():
    _check_codex_subscription_home("selected")


def test_codex_subscription_uses_default_when_home_unset():
    _check_codex_subscription_home(None)


def test_codex_subscription_uses_default_when_home_empty():
    _check_codex_subscription_home("")


def test_codex_missing_selected_auth_does_not_use_default_account():
    _check_codex_subscription_home("selected", with_auth=False)


def test_codex_relative_home_fallback_keeps_parent_directory():
    original_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory).resolve()
        launch_directory = root / "launch"
        review_directory = root / "review"
        selected_home = launch_directory / "selected"
        decoy_home = review_directory / "selected"
        selected_home.mkdir(parents=True)
        decoy_home.mkdir(parents=True)
        (selected_home / "config.toml").write_text("selected config")
        (decoy_home / "config.toml").write_text("wrong config")
        isolated_home = None
        with patch.dict(os.environ, {"HOME": str(root), "CODEX_HOME": "selected"}, clear=True), patch.object(
            reviewers, "REV_HOME_BASE", str(root / "reviewer-homes")
        ):
            try:
                os.chdir(launch_directory)
                env, isolated_home = reviewers.reviewer_env("codex", {}, subscription=True)
                result = subprocess.run(
                    [sys.executable, "-c", "import os,pathlib; print((pathlib.Path(os.environ['CODEX_HOME']) / 'config.toml').read_text())"],
                    cwd=review_directory, env=env, capture_output=True, text=True, check=True,
                )
                assert result.stdout.strip() == "selected config"
                assert env["CODEX_HOME"] == str(selected_home)
            finally:
                os.chdir(original_directory)
                reviewers.cleanup_rev_home(isolated_home)


def test_codex_api_key_keeps_isolated_home():
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(os.environ, {"CODEX_HOME": directory}), patch.object(
            reviewers, "REV_HOME_BASE", str(pathlib.Path(directory) / "reviewer-homes")
        ):
            env, isolated_home = reviewers.reviewer_env("codex", {"openai": "test-key"})
            try:
                assert env["CODEX_HOME"] == str(pathlib.Path(isolated_home) / ".codex")
                assert env["OPENAI_API_KEY"] == "test-key"
                assert not (pathlib.Path(env["CODEX_HOME"]) / "auth.json").exists()
            finally:
                reviewers.cleanup_rev_home(isolated_home)


def test_kiro_subscription_copies_only_login_store():
    old = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory() as real_home:
            data = pathlib.Path(real_home) / ".local" / "share" / "kiro-cli"
            data.mkdir(parents=True)
            db = data / "data.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO auth_kv VALUES ('login', 'test')")
            os.environ.clear()
            os.environ.update(HOME=real_home, PATH="/usr/bin")
            env, isolated_home = reviewers.reviewer_env("kiro", {}, subscription=True)
            copied = pathlib.Path(env["XDG_DATA_HOME"]) / "kiro-cli" / "data.sqlite3"
            assert copied.is_file()
            with sqlite3.connect(copied) as conn:
                assert conn.execute("SELECT value FROM auth_kv WHERE key = 'login'").fetchone() == ("test",)
            assert env["KIRO_HOME"].startswith(isolated_home)
            assert "KIRO_API_KEY" not in env
            assert copied.stat().st_mode & 0o777 == 0o600
    finally:
        os.environ.clear()
        os.environ.update(old)
    reviewers.cleanup_rev_home(isolated_home)


def test_kiro_api_key_cannot_be_shadowed_by_browser_login():
    env, isolated_home = reviewers.reviewer_env(
        "kiro", {"kiro": "  ksk_test  "}, subscription=True
    )
    try:
        assert env["KIRO_API_KEY"] == "ksk_test"
        assert env["XDG_DATA_HOME"].startswith(isolated_home)
        assert not (pathlib.Path(env["XDG_DATA_HOME"]) / "kiro-cli" / "data.sqlite3").exists()
    finally:
        reviewers.cleanup_rev_home(isolated_home)


def test_kiro_macos_uses_native_private_data_path():
    old_env, old_platform = os.environ.copy(), reviewers.sys.platform
    isolated_home = None
    try:
        with tempfile.TemporaryDirectory() as real_home:
            data = pathlib.Path(real_home) / "Library" / "Application Support" / "kiro-cli"
            data.mkdir(parents=True)
            with sqlite3.connect(data / "data.sqlite3") as conn:
                conn.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO auth_kv VALUES ('login', 'mac')")
            os.environ.clear()
            os.environ.update(HOME=real_home, PATH="/usr/bin", XDG_DATA_HOME="/must/not/win")
            reviewers.sys.platform = "darwin"
            env, isolated_home = reviewers.reviewer_env("kiro", {}, subscription=True)
            copied = (
                pathlib.Path(isolated_home)
                / "Library"
                / "Application Support"
                / "kiro-cli"
                / "data.sqlite3"
            )
            assert copied.is_file()
            assert "XDG_DATA_HOME" not in env
            assert env["HOME"] == isolated_home
    finally:
        reviewers.sys.platform = old_platform
        os.environ.clear()
        os.environ.update(old_env)
        reviewers.cleanup_rev_home(isolated_home)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\nall {len(tests)} reviewer environment checks passed")
