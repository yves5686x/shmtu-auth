"""多机 Docker 配置生成器单测。

这里最怕的不是写不出文件，而是**写出来了但配置不生效**——
变量名跟 docker 后端对不上，界面却提示「生成成功」。
所以除了结构检查，还有一条专门核对变量名的用例。
"""

import pathlib

from shmtu_auth.src.gui.common.docker_config_generator import (
    find_repo_root,
    generate_machine_configs,
    split_users,
)

USERS = [
    ("202500000001", "pwd-1"),
    ("202500000002", "pwd-2"),
    ("202500000003", "pwd-3"),
    ("202500000004", "pwd-4"),
]


# --------------------------------------------------------------- split_users


def test_split_users_distributes_evenly():
    chunks = split_users(USERS, machine_count=2, users_per_machine=2)
    assert chunks == [
        [("202500000001", "pwd-1"), ("202500000002", "pwd-2")],
        [("202500000003", "pwd-3"), ("202500000004", "pwd-4")],
    ]


def test_split_users_stops_when_users_run_out():
    """账号不够时不产出空的 machine_N。"""
    chunks = split_users(USERS, machine_count=5, users_per_machine=2)
    assert len(chunks) == 2


def test_split_users_rejects_invalid_arguments():
    assert split_users(USERS, machine_count=0, users_per_machine=2) == []
    assert split_users(USERS, machine_count=2, users_per_machine=0) == []


# ---------------------------------------------------- generate_machine_configs


def test_generate_creates_per_machine_directories(tmp_path):
    result = generate_machine_configs(
        save_path=str(tmp_path),
        users=USERS,
        machine_count=2,
        users_per_machine=2,
        repo_root=str(tmp_path),
    )

    assert result.ok
    assert result.machine_count == 2
    assert result.user_count == 4

    for i in (1, 2):
        machine_dir = tmp_path / f"machine_{i}"
        assert (machine_dir / ".env").is_file()
        assert (machine_dir / "docker-compose.yml").is_file()


def test_generated_env_contains_expected_variables(tmp_path):
    generate_machine_configs(
        save_path=str(tmp_path),
        users=USERS[:2],
        machine_count=1,
        users_per_machine=2,
        repo_root=str(tmp_path),
    )

    content = (tmp_path / "machine_1" / ".env").read_text(encoding="utf-8")

    assert "SHMTU_AUTH_USER_LIST=202500000001;202500000002" in content
    assert "SHMTU_AUTH_USER_PWD_202500000001='pwd-1'" in content
    assert "SHMTU_AUTH_USER_PWD_202500000002='pwd-2'" in content
    assert "SHMTU_MACHINE_NAME=machine_1" in content
    assert "SHMTU_AUTH_TIME_INTERVAL=" in content


def test_generated_env_variables_are_actually_read_by_docker_backend(tmp_path):
    """生成的变量名必须在 docker 后端源码里真的被读取。

    这是最容易悄无声息出错的地方：名字差一个字母，配置就完全不起作用，
    而界面还照样提示「生成成功」。
    """
    repo_root = find_repo_root()
    assert repo_root, "找不到仓库根目录，测试环境异常"

    backend_src = ""
    for path in pathlib.Path(repo_root, "src", "shmtu_auth").rglob("*.py"):
        backend_src += path.read_text(encoding="utf-8")

    generate_machine_configs(
        save_path=str(tmp_path),
        users=USERS[:1],
        machine_count=1,
        users_per_machine=1,
        repo_root=repo_root,
    )
    env_text = (tmp_path / "machine_1" / ".env").read_text(encoding="utf-8")

    for name in ("SHMTU_AUTH_USER_LIST", "SHMTU_MACHINE_NAME", "SHMTU_AUTH_TIME_INTERVAL"):
        assert name in env_text, f"{name} 没有写进生成的 .env"
        assert name in backend_src, f"{name} 写进了 .env，但 docker 后端并不读它"


def test_generated_compose_uses_host_network_and_env_file(tmp_path):
    """host 网络是设备号正确的前提，env_file 是配置生效的前提，两个都不能丢。"""
    generate_machine_configs(
        save_path=str(tmp_path),
        users=USERS[:1],
        machine_count=1,
        users_per_machine=1,
        repo_root=str(tmp_path),
    )

    compose = (tmp_path / "machine_1" / "docker-compose.yml").read_text(encoding="utf-8")

    assert "network_mode: host" in compose
    assert "- .env" in compose


def test_generated_readme_mentions_every_machine(tmp_path):
    generate_machine_configs(
        save_path=str(tmp_path),
        users=USERS,
        machine_count=2,
        users_per_machine=2,
        repo_root=str(tmp_path),
    )

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    assert "machine_1" in readme
    assert "machine_2" in readme


def test_generate_reports_skipped_machines(tmp_path):
    """账号不够时要如实说明有几台没生成，不能假装全部成功。"""
    result = generate_machine_configs(
        save_path=str(tmp_path),
        users=USERS[:2],
        machine_count=4,
        users_per_machine=1,
        repo_root=str(tmp_path),
    )

    assert result.ok
    assert result.machine_count == 2
    assert result.skipped_machines == 2
    assert "未生成" in result.message


def test_generate_fails_without_users(tmp_path):
    result = generate_machine_configs(
        save_path=str(tmp_path),
        users=[],
        machine_count=2,
        users_per_machine=1,
    )

    assert not result.ok
    assert "账号" in result.message


def test_generate_fails_without_save_path():
    result = generate_machine_configs(save_path="", users=USERS, machine_count=1)
    assert not result.ok


def test_generate_creates_missing_save_path(tmp_path):
    target = tmp_path / "nested" / "deeper"

    result = generate_machine_configs(
        save_path=str(target),
        users=USERS[:1],
        machine_count=1,
        users_per_machine=1,
        repo_root=str(tmp_path),
    )

    assert result.ok
    assert (target / "machine_1" / ".env").is_file()
