import time

from shmtu_auth.src.config.config_global import env_from_global
from shmtu_auth.src.core.credential_provider import resolve_credentials
from shmtu_auth.src.core.device_id import detect_device_mac, format_mac
from shmtu_auth.src.utils.env import get_env_str
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 自建凭据服务的本地缓存（内容是明文密码，已在 .gitignore 里排除）
DEFAULT_CREDENTIAL_CACHE = "./data/credentials.json"

_TRUTHY = ("1", "true", "yes", "on")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = get_env_str(name, "")
    if raw == "":
        return default
    return str(raw).strip().lower() in _TRUTHY


def get_credential_service_url() -> str:
    """自建凭据服务地址；留空表示不用这套机制。"""
    return get_env_str("SHMTU_AUTH_CREDENTIAL_URL", "")


def resolve_device_mac() -> str:
    """本机物理网卡 MAC（设备号），顺便把探测中的问题打进日志。"""
    identity = detect_device_mac(
        override=get_env_str("SHMTU_AUTH_DEVICE_MAC", ""),
        sysfs_dir=get_env_str("SHMTU_AUTH_SYSFS_NET_DIR", ""),
    )
    logger.info(
        "Device identity: %s (source=%s, iface=%s)"
        % (format_mac(identity.mac) or "(未取到)", identity.source or "-", identity.iface or "-")
    )
    if identity.warning:
        logger.warning(identity.warning)
    return identity.mac


# 凭据远端请求的刷新节流（秒）。
#
# monitor 每轮离线登录前都会调 get_user_list()，而 resolve_credentials
# 每次都是**真实的网络请求**（没有客户端 TTL 缓存）。不节流的话：
# 服务挂掉时每一轮（默认 10s）都要白等一次 (3+8)s 超时、刷一条告警、
# 写一次缓存文件。这里限制真实远端请求的频率，窗口内直接复用上次结果
# （成功与失败都算一次尝试——挂掉的服务同样不能每轮去敲）。
CREDENTIAL_REMOTE_REFRESH_INTERVAL = 300.0

# ts 用 -inf：见 get_query_string_requests 里对 time.monotonic() 的说明
_CREDENTIAL_REMOTE_CACHE: dict = {"ts": float("-inf"), "list": None}


def get_user_list_from_service():
    """向自建凭据服务按设备号换账号密码。

    没配 ``SHMTU_AUTH_CREDENTIAL_URL`` 时返回空列表（走本地配置，不算错误）。
    服务返回的 ``service`` / ``machine`` 写进全局配置，优先级高于 TOML 与环境变量。

    远端请求按 :data:`CREDENTIAL_REMOTE_REFRESH_INTERVAL` 节流；窗口内
    直接复用上次结果。取不到时回退本地缓存（在 resolve_credentials 内部），
    再不行返回空列表、由 get_user_list 落回本地配置。
    """
    url = get_credential_service_url()
    if not url:
        return []

    now = time.monotonic()
    if (
        _CREDENTIAL_REMOTE_CACHE["list"] is not None
        and now - _CREDENTIAL_REMOTE_CACHE["ts"] < CREDENTIAL_REMOTE_REFRESH_INTERVAL
    ):
        return _CREDENTIAL_REMOTE_CACHE["list"]

    mac = resolve_device_mac()
    bundle, note = resolve_credentials(
        url=url,
        mac=mac,
        token=get_env_str("SHMTU_AUTH_CREDENTIAL_TOKEN", ""),
        cache_path=get_env_str("SHMTU_AUTH_CREDENTIAL_CACHE", DEFAULT_CREDENTIAL_CACHE),
        verify=not _env_flag("SHMTU_AUTH_CREDENTIAL_INSECURE", False),
    )
    _CREDENTIAL_REMOTE_CACHE["ts"] = now

    if bundle is None:
        logger.warning(f"Credential service unusable: {note}")
        _CREDENTIAL_REMOTE_CACHE["list"] = []
        return []

    if note:
        logger.warning(note)
    logger.info(f"Loaded {len(bundle.users)} account(s) from credential {bundle.source}")

    if bundle.service:
        env_from_global["SHMTU_AUTH_PORTAL_SERVICE"] = bundle.service
        logger.info(f"Credential service pinned portal service: {bundle.service}")
    if bundle.machine:
        env_from_global["SHMTU_MACHINE_NAME"] = bundle.machine

    users = [(item["id"], item["password"], False) for item in bundle.users]
    _CREDENTIAL_REMOTE_CACHE["list"] = users
    return users


def get_user_num_list():
    user_list = get_env_str("SHMTU_AUTH_USER_LIST", "")
    if user_list == "":
        return []
    else:
        list_ori = user_list.split(";")
        new_list = []
        for item in list_ori:
            item = str(item).strip()
            if len(item) > 0:
                new_list.append(item)
        return new_list


def get_user_pwd(user_num):
    user_pwd = get_env_str("SHMTU_AUTH_USER_PWD_" + user_num, "", strip=False)
    return user_pwd


def get_user_list():
    """取认证用的账号列表：先问自建凭据服务，拿不到再回退本地配置。"""
    from shmtu_auth.src.utils.account_store import read_accounts
    saved = read_accounts()
    if saved is not None:
        return saved
    remote_list = get_user_list_from_service()
    if len(remote_list) > 0:
        return remote_list

    user_list = get_user_num_list()
    return_list = []
    for user_num in user_list:
        truly_user_num = user_num.strip()
        pwd = get_user_pwd(truly_user_num)
        if pwd != "":
            is_encrypt = len(get_env_str("SHMTU_AUTH_USER_PWD_ENCRYPT_" + user_num, "")) > 0
            return_list.append((truly_user_num, pwd, is_encrypt))

    return return_list


def convert_number_to_star(number: str) -> str:
    length = len(number)
    if length == 12:
        new_str = number[0:4] + "*****" + number[9:12]
        return new_str
    return "*" * length


def convert_password_to_star(password: str) -> str:
    return "*" * len(password)


if __name__ == "__main__":
    my_variable_value = get_env_str("SHMTU_AUTH_USER_LIST", "")
    print(my_variable_value)
    print(len(my_variable_value))
