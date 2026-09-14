import logging
import os

from app.credential_provider import resolve_credentials
from app.device_id import detect_device_mac, format_mac

# 自建凭据服务的本地缓存（内容是明文密码）
DEFAULT_CREDENTIAL_CACHE = "./data/credentials.json"


def get_env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip()


def get_env_int(name: str, default: int) -> int:
    value = get_env_str(name, "")
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_bool(name: str, default: bool = False) -> bool:
    value = get_env_str(name, "")
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


DEFAULT_CHECK_INTERVAL = 60


def get_check_interval() -> int:
    """轮询间隔（秒）。

    主包侧这个配置叫 ``SHMTU_AUTH_TIME_INTERVAL``，Docker 历史上叫
    ``SHMTU_AUTH_CHECK_INTERVAL``。两个名字都认、优先新的那个，
    这样改名不会让老的 .env 静默失效（名字写错不报错，只会用默认值）。
    """
    for name in ("SHMTU_AUTH_TIME_INTERVAL", "SHMTU_AUTH_CHECK_INTERVAL"):
        if get_env_str(name, ""):
            return get_env_int(name, DEFAULT_CHECK_INTERVAL)
    return DEFAULT_CHECK_INTERVAL


def resolve_device_mac() -> str:
    """本机物理网卡 MAC（设备号）。

    容器要用 host 网络才能看到宿主物理网卡；否则得显式配置
    ``SHMTU_AUTH_DEVICE_MAC``。
    """
    identity = detect_device_mac(
        override=get_env_str("SHMTU_AUTH_DEVICE_MAC", ""),
        sysfs_dir=get_env_str("SHMTU_AUTH_SYSFS_NET_DIR", ""),
    )
    logging.info(
        "Device identity: %s (source=%s, iface=%s)",
        format_mac(identity.mac) or "(未取到)",
        identity.source or "-",
        identity.iface or "-",
    )
    if identity.warning:
        logging.warning(identity.warning)
    return identity.mac


def parse_user_list_from_service() -> list[tuple[str, str]]:
    """向自建凭据服务按设备号换账号密码；未配置地址时返回空列表。"""
    url = get_env_str("SHMTU_AUTH_CREDENTIAL_URL", "")
    if not url:
        return []

    bundle, note = resolve_credentials(
        url=url,
        mac=resolve_device_mac(),
        token=get_env_str("SHMTU_AUTH_CREDENTIAL_TOKEN", ""),
        cache_path=get_env_str("SHMTU_AUTH_CREDENTIAL_CACHE", DEFAULT_CREDENTIAL_CACHE),
        verify=not get_env_bool("SHMTU_AUTH_CREDENTIAL_INSECURE", False),
    )

    if bundle is None:
        logging.warning("Credential service unusable: %s", note)
        return []

    if note:
        logging.warning(note)
    logging.info("Loaded %s account(s) from credential %s", len(bundle.users), bundle.source)

    if bundle.service:
        os.environ["SHMTU_AUTH_PORTAL_SERVICE"] = bundle.service
        logging.info("Credential service pinned portal service: %s", bundle.service)
    if bundle.machine:
        os.environ["SHMTU_MACHINE_NAME"] = bundle.machine

    return [(item["id"], item["password"]) for item in bundle.users]


def parse_user_list() -> list[tuple[str, str]]:
    """取账号列表：先问自建凭据服务，拿不到再回退环境变量。"""
    remote_users = parse_user_list_from_service()
    if remote_users:
        return remote_users

    raw_users = get_env_str("SHMTU_AUTH_USER_LIST", "")
    if not raw_users:
        return []

    users: list[tuple[str, str]] = []
    for user_id in raw_users.split(";"):
        user_id = user_id.strip()
        if not user_id:
            continue

        password = get_env_str(f"SHMTU_AUTH_USER_PWD_{user_id}", "")
        if password:
            users.append((user_id, password))

    return users


def mask_user(user_id: str) -> str:
    if len(user_id) == 12:
        return f"{user_id[:4]}*****{user_id[-3:]}"
    return "*" * len(user_id)
