"""Docker 配置经过真实 Compose 解析后，账号密码必须完整到达主包。"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from shmtu_auth.src.config.config_global import env_from_global
from shmtu_auth.src.config.config_toml import env_from_toml
from shmtu_auth.src.core import credential_provider
from shmtu_auth.src.gui.common.docker_config_generator import generate_machine_configs
from shmtu_auth.src.monitor import auth_status
from shmtu_auth.src.utils.program_env_config import get_user_list


@pytest.fixture(autouse=True)
def clean_account_config(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SHMTU_"):
            monkeypatch.delenv(key)
    for config in (env_from_global, env_from_toml):
        for key in list(config):
            monkeypatch.delitem(config, key)


PASSWORDS = ["normal-password", "a$UNDEFINED", "a${UNDEFINED}", "a # comment",
             "a'b\"c", " leading and trailing ", "中文密码", "slash\\", "a\\'b",
             "a\\$UNDEFINED", "a\\\\b", "tab\there"]


@pytest.mark.parametrize("password", PASSWORDS)
def test_local_and_remote_passwords_preserve_literal_value(monkeypatch, password):
    monkeypatch.setenv("SHMTU_AUTH_USER_LIST", "202500000001")
    monkeypatch.setenv("SHMTU_AUTH_USER_PWD_202500000001", password)
    assert get_user_list() == [("202500000001", password, False)]
    bundle = credential_provider.CredentialBundle.from_dict(
        {"users": [{"id": "202500000001", "password": password}]}
    )
    assert bundle.users[0]["password"] == password


def test_missing_accounts_exit_before_monitor_or_network(monkeypatch):
    monkeypatch.setattr(auth_status.threading, "Thread", lambda **kw: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as result:
        auth_status.start_monitor_auth()
    assert result.value.code == 1


def test_container_layout_starts_cli_and_rejects_empty_accounts(tmp_path):
    """复刻 Dockerfile 的目录布局，真实执行 CLI，检查入口、日志目录及退出码。"""
    repo = Path(__file__).resolve().parents[1]
    package = tmp_path / "src/shmtu_auth"
    shutil.copytree(repo / "src/shmtu_auth", package,
                    ignore=shutil.ignore_patterns("__pycache__", "config.toml", "logs", "data"))
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "src"), "DOCKER_MODE": "1"}
    result = subprocess.run([sys.executable, "-m", "shmtu_auth"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert "No valid accounts configured" in result.stderr
    assert (tmp_path / "logs").is_dir()
    assert "202300000000" not in result.stdout + result.stderr


def test_users_without_passwords_are_not_attempted(monkeypatch):
    monkeypatch.setenv("SHMTU_AUTH_USER_LIST", "202500000001;202500000002")
    monkeypatch.setenv("SHMTU_AUTH_USER_PWD_202500000002", "valid")
    assert get_user_list() == [("202500000002", "valid", False)]


@pytest.mark.parametrize("user,password", [("bad\nNAME", "p"), ("123", ""), ("123", "p\nOTHER=x"), ("123", "p\0")])
def test_invalid_account_export_writes_nothing(tmp_path, user, password):
    assert not generate_machine_configs(str(tmp_path), [(user, password)]).ok
    assert list(tmp_path.iterdir()) == []


def test_export_targets_main_image_and_persists_data(tmp_path):
    assert generate_machine_configs(str(tmp_path), [("123", "p")], repo_root='/source/path: with spaces').ok
    machine = tmp_path / "machine_1"
    service = yaml.safe_load((machine / "docker-compose.yml").read_text())["services"]["shmtu-auth"]
    assert service["build"] == {"context": '/source/path: with spaces', "dockerfile": "Docker/Dockerfile"}
    assert "./logs:/app/logs" in service["volumes"]
    assert "./data:/app/data" in service["volumes"]
    if os.name != "nt":
        assert (machine / ".env").stat().st_mode & 0o777 == 0o600


def test_compose_password_round_trip_to_main_package(tmp_path, monkeypatch):
    binary = os.environ.get("COMPOSE_BINARY")
    command = [binary] if binary else [shutil.which("docker") or "docker", "compose"]
    if not binary and not shutil.which("docker"):
        pytest.skip("Install Docker Compose or set COMPOSE_BINARY for the integration test")
    users = [(str(202500000001 + i), password) for i, password in enumerate(PASSWORDS)]
    assert generate_machine_configs(str(tmp_path), users, users_per_machine=len(users)).ok
    result = subprocess.run(command + ["-f", str(tmp_path / "machine_1/docker-compose.yml"),
                                      "config", "--format", "json"],
                            capture_output=True, text=True, check=True, timeout=30)
    environment = json.loads(result.stdout)["services"]["shmtu-auth"]["environment"]
    # config 的可重用 JSON 会把 $ 序列化成 $$；--environment 返回解析后的字面值。
    resolved = subprocess.run(command + ["-f", str(tmp_path / "machine_1/docker-compose.yml"),
                                        "config", "--environment"],
                              capture_output=True, text=True, check=True, timeout=30)
    values = dict(line.split("=", 1) for line in resolved.stdout.splitlines() if "=" in line)
    for key in environment:
        assert environment[key] == values[key].replace("$", "$$")
        monkeypatch.setenv(key, values[key])
    # Do not include passwords in assertion diagnostics.
    actual = get_user_list()
    assert len(actual) == len(users)
    assert all(actual[i] == (user, password, False) for i, (user, password) in enumerate(users))
