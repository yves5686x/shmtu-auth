"""为多台服务器生成 Docker 部署配置。

GUI 的「用户列表」里挑出有效账号，按固定数量切分给 N 台机器，每台生成一个
配置目录；目标机器准备好源码并设置 build.context 后，执行
``docker compose up -d --build``：

    <保存目录>/
    ├── README.md              部署说明
    ├── machine_1/
    │   ├── .env               这台机器分到的账号 + 运行参数
    │   └── docker-compose.yml
    └── machine_2/
        ├── .env
        └── docker-compose.yml

环境变量名全部对齐主包 CLI 实际读取的那套，
少一个都不会生效。

⚠️ ``.env`` 里是**明文密码**，生成后请自行妥善保管，别提交到版本库。
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 目录名格式，也是各机器的 SHMTU_MACHINE_NAME
MACHINE_DIR_FORMAT = "machine_{index}"

# 生成配置里默认的检测间隔（秒），用于主包 CLI
DEFAULT_CHECK_INTERVAL = 60


@dataclass
class GenerateResult:
    """生成结果，供界面提示用。"""

    ok: bool = False
    machine_count: int = 0
    user_count: int = 0
    skipped_machines: int = 0
    message: str = ""
    created_paths: List[str] = field(default_factory=list)


def find_repo_root(start_path: str = "") -> str:
    """向上找仓库根目录（以含 ``Docker/Dockerfile`` 为准）。

    生成的 docker-compose.yml 要把 build context 指回仓库，
    目标机器的源码路径可能不同，部署时需在 compose 中调整。
    """
    current = os.path.abspath(start_path or os.path.dirname(__file__))

    for _ in range(10):
        if os.path.isfile(os.path.join(current, "Docker", "Dockerfile")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return ""


def split_users(
    users: Sequence[Tuple[str, str]],
    machine_count: int,
    users_per_machine: int,
) -> List[List[Tuple[str, str]]]:
    """把账号按顺序切成若干份，一份对应一台机器。

    账号不够时后面的机器直接不生成（不会产出空的 machine_N 目录）。
    """
    if machine_count <= 0 or users_per_machine <= 0:
        return []

    chunks: List[List[Tuple[str, str]]] = []
    cursor = 0

    for _ in range(machine_count):
        chunk = list(users[cursor : cursor + users_per_machine])
        cursor += users_per_machine
        if not chunk:
            break
        chunks.append(chunk)

    return chunks


def _quote_env(value: str) -> str:
    """单引号避免插值；含反斜杠时用双引号转义，并以 $$ 保留美元符号。"""
    if "\\" in value:
        return json.dumps(value, ensure_ascii=False).replace("$", "$$")
    return "'" + value.replace("'", "\\'") + "'"


def _render_env(machine_name: str, users: Sequence[Tuple[str, str]]) -> str:
    """渲染一台机器的 .env。"""
    lines = [
        "# ==============================================================================",
        f"# {machine_name} —— 由 shmtu-auth 生成",
        "#",
        "# 容器用 host 网络（见 docker-compose.yml），因此看得到宿主机物理网卡，",
        "# 门户认证需要的设备标识才拿得对。",
        "#",
        "# ⚠️ 本文件含明文密码，请妥善保管，不要提交到版本库。",
        "# ==============================================================================",
        "",
        "# ---- 本机分配到的账号 ----",
        "# 多个学号用分号分隔；每个学号对应下面一行密码",
        "SHMTU_AUTH_USER_LIST=" + ";".join(user_id for user_id, _ in users),
    ]

    for user_id, password in users:
        lines.append(f"SHMTU_AUTH_USER_PWD_{user_id}={_quote_env(password)}")

    lines += [
        "",
        "# ---- 设备名（日志里用它区分是哪台机器）----",
        f"SHMTU_MACHINE_NAME={machine_name}",
        "",
        "# ---- 检测间隔（秒）----",
        "# 主包 CLI 的轮询间隔",
        f"SHMTU_AUTH_TIME_INTERVAL={DEFAULT_CHECK_INTERVAL}",
        "",
        "# ---- 可选：自建凭据服务（配了就优先用它，见 config.toml 的 [Credential] 段）----",
        "#SHMTU_AUTH_CREDENTIAL_URL=https://your-server/device/{mac}/credential",
        "#SHMTU_AUTH_CREDENTIAL_TOKEN=",
        "",
        "# ---- 可选：定死门户接入类型；不填则依次尝试「校园网」和「iSMU」----",
        "#SHMTU_AUTH_PORTAL_SERVICE=",
        "",
    ]

    return "\n".join(lines)


def _render_compose(repo_root: str) -> str:
    """渲染 docker-compose.yml。

    ``repo_root`` 为空（例如打包成 exe 后找不到源码目录）时退回 ``..``，
    并在 README 里提醒用户改成实际的仓库路径。
    """
    context = json.dumps(repo_root or "..", ensure_ascii=False).replace("$", "$$")

    return f"""services:
  shmtu-auth:
    build:
      context: {context}
      dockerfile: Docker/Dockerfile
    image: shmtu-auth:local
    container_name: shmtu-auth
    restart: unless-stopped
    # 必须用 host 网络：容器要看得到宿主物理网卡，设备号（物理 MAC）才取得对
    network_mode: host
    # 所有配置都写在同目录的 .env 里
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    # onnxruntime + opencv 常驻内存约 200MB，128M 会 OOM
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 128M
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
"""


def _render_readme(machine_names: Sequence[str], repo_root: str) -> str:
    """渲染总目录的部署说明。"""
    machine_list = "\n".join(f"| `{name}/` | 这台机器的账号与 compose 配置 |" for name in machine_names)

    repo_note = (
        f"当前机器上的仓库路径已写进各 compose 文件的 `build.context`：\n`{repo_root}`\n\n"
        "如果目标机器上没有这份源码，请把仓库拷过去，或改用已构建好的镜像。"
        if repo_root
        else "⚠️ 没能自动定位源码仓库，compose 里的 `build.context` 写的是 `..`，\n"
        "请按目标机器上的实际仓库路径修改。"
    )

    return f"""# shmtu-auth 多机部署配置

本目录由 shmtu-auth GUI 生成，共 {len(machine_names)} 台机器的配置。

## 目录结构

| 目录 | 说明 |
| --- | --- |
{machine_list}

## 部署步骤

在**每台目标机器**上分别执行：

```bash
cd {machine_names[0] if machine_names else "machine_1"}
docker compose up -d --build
```

查看日志确认认证状态：

```bash
docker compose logs -f
```

## 关于镜像构建

{repo_note}

## 注意事项

- 每个 `machine_N/.env` 里是**明文密码**，请限制目录权限，不要提交到版本库。
- 容器必须用 host 网络：只有这样才能读到宿主物理网卡，设备号（物理 MAC）才正确。
- 若该机器还配了自建凭据服务，取消 `.env` 里 `SHMTU_AUTH_CREDENTIAL_URL` 的注释即可，
  它会按物理 MAC 换账号，换不到才回退到 `.env` 里的本地账号。
"""


def generate_machine_configs(
    save_path: str,
    users: Sequence[Tuple[str, str]],
    machine_count: int = 1,
    users_per_machine: int = 3,
    repo_root: str = "",
    write_readme: bool = True,
) -> GenerateResult:
    """按机器生成整套 Docker 配置。

    :param save_path: 保存目录（不存在会自动创建）
    :param users: 可用的 (学号, 密码) 列表，按顺序分配
    :param machine_count: 要生成几台机器
    :param users_per_machine: 每台机器分几个账号
    :param repo_root: 源码仓库根目录，写进 compose 的 build context
    :param write_readme: 是否在总目录写一份部署说明
    """
    save_path = (save_path or "").strip()
    if not save_path:
        return GenerateResult(ok=False, message="保存目录不能为空")

    if not users:
        return GenerateResult(ok=False, message="没有可用的账号，请先在用户列表里添加有效账号")

    for user_id, password in users:
        if not re.fullmatch(r"[0-9]+", user_id) or not password or any(c in password for c in "\r\n\0"):
            return GenerateResult(ok=False, message="账号必须为数字，密码不能为空或包含换行及空字符")

    chunks = split_users(users, machine_count, users_per_machine)
    if not chunks:
        return GenerateResult(ok=False, message="机器数量或每台账号数量不合法")

    try:
        os.makedirs(save_path, exist_ok=True)
    except OSError as e:
        return GenerateResult(ok=False, message=f"无法创建目录：{e}")

    if not repo_root:
        repo_root = find_repo_root()

    machine_names: List[str] = []
    created: List[str] = []
    total_users = 0

    try:
        for i, chunk in enumerate(chunks, start=1):
            machine_name = MACHINE_DIR_FORMAT.format(index=i)
            machine_dir = os.path.join(save_path, machine_name)
            os.makedirs(machine_dir, exist_ok=True)

            env_path = os.path.join(machine_dir, ".env")
            descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as f:
                os.chmod(env_path, 0o600)
                f.write(_render_env(machine_name, chunk))

            with open(os.path.join(machine_dir, "docker-compose.yml"), "w", encoding="utf-8") as f:
                f.write(_render_compose(repo_root))

            machine_names.append(machine_name)
            created.append(machine_dir)
            total_users += len(chunk)

        if write_readme:
            with open(os.path.join(save_path, "README.md"), "w", encoding="utf-8") as f:
                f.write(_render_readme(machine_names, repo_root))

    except OSError as e:
        logger.error(f"Generate docker config failed: {e}")
        return GenerateResult(
            ok=False,
            machine_count=len(machine_names),
            user_count=total_users,
            message=f"写入文件失败：{e}",
            created_paths=created,
        )

    skipped = max(0, machine_count - len(chunks))
    logger.info(
        f"Generated docker config: {len(machine_names)} machine(s), "
        f"{total_users} user(s), skipped {skipped} machine(s)"
    )

    message = f"已生成 {len(machine_names)} 台机器的配置，共分配 {total_users} 个账号"
    if skipped:
        message += f"\n注意：账号不够，有 {skipped} 台机器未生成"

    return GenerateResult(
        ok=True,
        machine_count=len(machine_names),
        user_count=total_users,
        skipped_machines=skipped,
        message=message,
        created_paths=created,
    )
