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


def get_user_list_from_service():
    """向自建凭据服务按设备号换账号密码。

    没配 ``SHMTU_AUTH_CREDENTIAL_URL`` 时返回空列表（走本地配置，不算错误）。
    服务返回的 ``service`` / ``machine`` 写进全局配置，优先级高于 TOML 与环境变量。
    """
    url = get_credential_service_url()
    if not url:
        return []

    mac = resolve_device_mac()
    bundle, note = resolve_credentials(
        url=url,
        mac=mac,
        token=get_env_str("SHMTU_AUTH_CREDENTIAL_TOKEN", ""),
        cache_path=get_env_str("SHMTU_AUTH_CREDENTIAL_CACHE", DEFAULT_CREDENTIAL_CACHE),
        verify=not _env_flag("SHMTU_AUTH_CREDENTIAL_INSECURE", False),
    )

    if bundle is None:
        logger.warning(f"Credential service unusable: {note}")
        return []

    if note:
        logger.warning(note)
    logger.info(f"Loaded {len(bundle.users)} account(s) from credential {bundle.source}")

    if bundle.service:
        env_from_global["SHMTU_AUTH_PORTAL_SERVICE"] = bundle.service
        logger.info(f"Credential service pinned portal service: {bundle.service}")
    if bundle.machine:
        env_from_global["SHMTU_MACHINE_NAME"] = bundle.machine

    return [(item["id"], item["password"], False) for item in bundle.users]


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
