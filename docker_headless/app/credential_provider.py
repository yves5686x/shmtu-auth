"""从自建凭据服务按设备号取账号密码。

**服务端接口约定**（自己搭的服务按这个实现即可）：

请求::

    GET {SHMTU_AUTH_CREDENTIAL_URL}?mac=<12位小写物理MAC>
    Headers:
        X-Auth-Token: <SHMTU_AUTH_CREDENTIAL_TOKEN>   # 配置了 token 才带
        User-Agent: shmtu-auth/device-credential

地址里含 ``{mac}`` 占位符时直接替换、不再追加查询参数，
例如 ``https://host/device/{mac}/credential``。

响应 200 + JSON（``service`` / ``machine`` / ``ttl`` 都可选）::

    {
      "users": [
        {"id": "202540510004", "password": "xxxx"}
      ],
      "service": "校园网",
      "machine": "实验室服务器",
      "ttl": 3600
    }

单账号的简化写法也认：``{"user": "202540510004", "password": "xxxx"}``。

取不到时（非 200 / 非法 JSON / 没有可用账号）会自动**回退到本地缓存**，
缓存也没有才判定失败 —— 这样自建服务短暂挂掉不至于让设备断网。

安全提醒：密码是明文过网络的，务必走 HTTPS 并配 token，不要暴露到公网。
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 8
DEFAULT_TTL = 3600
DEFAULT_USER_AGENT = "shmtu-auth/device-credential"


@dataclass
class CredentialBundle:
    """一次凭据获取的结果。"""

    users: List[Dict[str, str]] = field(default_factory=list)
    service: str = ""
    machine: str = ""
    mac: str = ""
    fetched_at: float = 0.0
    ttl: int = DEFAULT_TTL
    source: str = ""  # remote / cache

    @property
    def ok(self) -> bool:
        return bool(self.users)

    def is_fresh(self, now: Optional[float] = None) -> bool:
        if not self.fetched_at or self.ttl <= 0:
            return False
        return (now if now is not None else time.time()) - self.fetched_at < self.ttl

    def to_dict(self) -> Dict:
        return {
            "users": self.users,
            "service": self.service,
            "machine": self.machine,
            "mac": self.mac,
            "fetched_at": self.fetched_at,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, data: Dict, source: str = "cache") -> "CredentialBundle":
        users: List[Dict[str, str]] = []
        for item in data.get("users") or []:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("id") or "").strip()
            password = str(item.get("password") or "").strip()
            if user_id and password:
                users.append({"id": user_id, "password": password})

        try:
            ttl = int(data.get("ttl") or DEFAULT_TTL)
        except (TypeError, ValueError):
            ttl = DEFAULT_TTL

        try:
            fetched_at = float(data.get("fetched_at") or 0)
        except (TypeError, ValueError):
            fetched_at = 0.0

        return cls(
            users=users,
            service=str(data.get("service") or "").strip(),
            machine=str(data.get("machine") or "").strip(),
            mac=str(data.get("mac") or "").strip(),
            fetched_at=fetched_at,
            ttl=ttl,
            source=source,
        )


def build_request_url(base_url: str, mac: str) -> str:
    """把设备号拼进请求地址：有 ``{mac}`` 占位符就替换，否则追加查询参数。"""
    base_url = (base_url or "").strip()
    if not base_url:
        return ""
    if "{mac}" in base_url:
        return base_url.replace("{mac}", mac)
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}mac={mac}"


def parse_payload(payload: Dict, mac: str = "") -> CredentialBundle:
    """解析服务端响应，兼容 ``users`` 数组与单账号两种写法。"""
    users: List[Dict[str, str]] = []

    raw_users = payload.get("users")
    if isinstance(raw_users, list):
        for item in raw_users:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("id") or item.get("userId") or item.get("user") or "").strip()
            password = str(item.get("password") or item.get("pwd") or "").strip()
            if user_id and password:
                users.append({"id": user_id, "password": password})

    if not users:
        user_id = str(payload.get("user") or payload.get("userId") or "").strip()
        password = str(payload.get("password") or payload.get("pwd") or "").strip()
        if user_id and password:
            users.append({"id": user_id, "password": password})

    try:
        ttl = int(payload.get("ttl") or DEFAULT_TTL)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL

    return CredentialBundle(
        users=users,
        service=str(payload.get("service") or "").strip(),
        machine=str(payload.get("machine") or "").strip(),
        mac=mac,
        fetched_at=time.time(),
        ttl=ttl,
        source="remote",
    )


def fetch_credentials(
    url: str,
    mac: str,
    token: str = "",
    user_agent: str = DEFAULT_USER_AGENT,
    verify: bool = True,
) -> Tuple[Optional[CredentialBundle], str]:
    """向自建服务请求凭据。返回 ``(结果, 错误说明)``，成功时错误说明为空串。"""
    if not url:
        return None, "未配置凭据服务地址"
    if not mac:
        return None, "未取到物理 MAC，无法向凭据服务标识本设备"

    request_url = build_request_url(url, mac)
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    if token:
        headers["X-Auth-Token"] = token

    try:
        res = requests.get(
            request_url,
            headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            verify=verify,
        )
    except Exception as e:
        return None, f"请求凭据服务失败: {e}"

    if res.status_code != 200:
        return None, f"凭据服务返回 HTTP {res.status_code}"

    try:
        payload = res.json()
    except Exception:
        return None, "凭据服务返回的不是合法 JSON"

    if not isinstance(payload, dict):
        return None, "凭据服务返回的 JSON 结构不对"

    bundle = parse_payload(payload, mac)
    if not bundle.ok:
        reason = str(payload.get("message") or payload.get("msg") or "").strip()
        suffix = f"：{reason}" if reason else "，该设备可能还没在服务端注册"
        return None, f"凭据服务没有返回可用账号{suffix}"
    return bundle, ""


def save_cache(path: str, bundle: CredentialBundle) -> bool:
    """把凭据写进本地缓存。注意内容是明文密码，权限收紧到 600。"""
    if not path:
        return False
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bundle.to_dict(), f, ensure_ascii=False, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return True
    except OSError:
        return False


def load_cache(path: str) -> Optional[CredentialBundle]:
    """读本地缓存；不存在或内容不可用时返回 None。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    bundle = CredentialBundle.from_dict(data)
    return bundle if bundle.ok else None


def resolve_credentials(
    url: str,
    mac: str,
    token: str = "",
    cache_path: str = "",
    user_agent: str = DEFAULT_USER_AGENT,
    verify: bool = True,
) -> Tuple[Optional[CredentialBundle], str]:
    """取凭据：先请求远端，失败回退本地缓存。返回 ``(结果, 说明)``。

    未配置 ``url`` 时返回 ``(None, "")`` —— 调用方据此走本地配置，不算错误。
    """
    if not url:
        return None, ""

    bundle, error = fetch_credentials(url, mac, token, user_agent, verify)
    if bundle is not None:
        if cache_path:
            save_cache(cache_path, bundle)
        return bundle, ""

    cached = load_cache(cache_path) if cache_path else None
    if cached is not None:
        return cached, f"{error}；已回退到本地缓存（{len(cached.users)} 个账号）"
    return None, error
