"""H3C eportal 门户协议实现（无头版）。

与主包 ``shmtu_auth/src/core/eportal_protocol.py`` 保持一致，差异只有日志：

* 用标准库 ``logging`` 而不是 ``loguru``，保持本目录「不依赖 loguru / toml / PyQt」的定位。

门户（``https://ismu.shmtu.edu.cn:8443/eportal/``）的完整登录时序是：

1. ``GET  index.jsp?<queryString>``          建立会话，拿到 JSESSIONID
2. ``POST InterFace.do?method=pageInfo``     取 validCodeUrl / passwordEncrypt / RSA 公钥
3. ``GET  validcode?rnd=<随机数>``           下载验证码图片（会话绑定，一次性）
4. （本地识别验证码，见 captcha_solver）
5. ``POST InterFace.do?method=login``        带 validcode + RSA 密文密码

关键约束：

* 全流程必须复用**同一个 Session**，验证码与 JSESSIONID 绑定
* 验证码是**一次性**的：失败响应会带上新的 ``validCodeUrl``，必须重新取图识别
* ``service`` 由服务端下发（下拉框），有线取 ``校园网``、无线 i-SHMU 取 ``iSMU``
* RSA 公钥每次登录都要**重新获取**，不要写死
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote, urljoin, urlparse

import requests

LOGGER = logging.getLogger("shmtu_auth_headless.eportal")

# 超时配置（秒）
CONNECT_TIMEOUT = 3
READ_TIMEOUT = 8

DEFAULT_PORTAL_HOST = "https://ismu.shmtu.edu.cn:8443"
DEFAULT_PORTAL_BASE = f"{DEFAULT_PORTAL_HOST}/eportal/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
)

# 服务端在验证码错误时返回的 message
MSG_VALID_CODE_ERROR = "验证码错误"
# 验证码类错误的判定关键字（比对时统一转小写）
_VALID_CODE_HINTS = ("验证码", "validcode")

# 门户下拉框里的占位项，表示「走系统默认服务」，不是真实服务
PLACEHOLDER_SERVICE_PREFIX = "[-1-1]"


def encode_service_param(value: str) -> str:
    """把门户 serviceName 转成可以交给 ``requests`` 的 ``service`` 参数值。

    门户 JS 提交前对隐藏域 ``net_access_type.value`` 做了**两次**
    ``encodeURIComponent``（见 ``login_bch.js``）：::

        service = encodeURIComponent(encodeURIComponent(net_access_type.value))

    而我们用 ``requests`` 的 ``data=`` 提交时它还会再编码一次，所以这里只需要编码
    一次，线上字节就能和浏览器完全一致。

    :param value: 门户下发的原始服务名（如 ``校园网`` / ``iSMU``），或已编码的值
    :return: 可直接放进 payload 的 ``service`` 参数值
    """
    value = (value or "").strip()
    if not value:
        return ""
    if "%" in value:
        # 已经是编码过的形态（例如用户直接填了 %E6%A0%A1... ），原样使用
        return value
    return quote(value, safe="")


@dataclass
class PortalPageInfo:
    """``InterFace.do?method=pageInfo`` 的解析结果。"""

    valid_code_url: str = ""
    password_encrypt: bool = False
    public_key_exponent: str = ""
    public_key_modulus: str = ""
    is_service: bool = False
    raw: Dict = field(default_factory=dict)

    @property
    def need_valid_code(self) -> bool:
        """门户是否要求图形验证码（validCodeUrl 非空即要求）。"""
        return bool(self.valid_code_url.strip())


def _parse_page_info(payload: Dict) -> PortalPageInfo:
    return PortalPageInfo(
        valid_code_url=str(payload.get("validCodeUrl") or "").strip(),
        password_encrypt=str(payload.get("passwordEncrypt") or "").strip().lower() == "true",
        public_key_exponent=str(payload.get("publicKeyExponent") or "").strip(),
        public_key_modulus=str(payload.get("publicKeyModulus") or "").strip(),
        is_service=str(payload.get("isService") or "").strip().lower() == "true",
        raw=payload,
    )


def is_valid_code_error(message: str) -> bool:
    """判断服务端返回的 message 是否为「验证码」类错误（大小写不敏感）。"""
    text = str(message or "").lower()
    return any(hint in text for hint in _VALID_CODE_HINTS)


class EPortalClient:
    """eportal 门户客户端，封装一个 requests.Session。"""

    def __init__(
        self,
        session: requests.Session,
        base_url: str = DEFAULT_PORTAL_BASE,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.session = session
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.user_agent = user_agent

    # ------------------------------------------------------------------ 基础

    def _interface_url(self, method: str) -> str:
        return f"{self.base_url}InterFace.do?method={method}"

    def _browser_headers(self, referer: str = "") -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.base_url.rstrip("/"),
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def index_url(self, query_string: str) -> str:
        return f"{self.base_url}index.jsp?{(query_string or '').lstrip('?')}"

    def _absolute(self, url: str) -> str:
        url = (url or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("/"):
            parsed = urlparse(self.base_url)
            return f"{parsed.scheme}://{parsed.netloc}{url}"
        return urljoin(self.base_url, url.lstrip("./"))

    @staticmethod
    def _response_text(res: requests.Response) -> str:
        """按 UTF-8 优先解码响应体。

        门户多数接口（``pageInfo`` / ``login`` / ``getServices``）返回
        ``Content-Type: text/html``，**不带 charset**。requests 此时会退回
        ISO-8859-1，把 ``"验证码错误."`` 解成 ``"éªè¯ç éè¯¯."``，
        导致验证码错误识别、错误提示全部变成乱码。故这里强制 UTF-8 优先。
        """
        raw = res.content or b""
        if not raw:
            return ""
        for encoding in ("utf-8", res.apparent_encoding, res.encoding):
            if not encoding:
                continue
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")

    def _post_json(self, method: str, data: Dict, referer: str = "") -> Dict:
        url = self._interface_url(method)
        res = self.session.post(
            url,
            data=data,
            headers=self._browser_headers(referer),
            verify=False,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        text = self._response_text(res)
        try:
            return json.loads(text)
        except Exception:
            LOGGER.warning(
                "%s non-JSON response (status=%s): %s",
                method,
                res.status_code,
                text[:200],
            )
            return {}

    # ------------------------------------------------------------------ 流程

    def open_entry(self, query_string: str) -> bool:
        """第 1 步：打开 index.jsp，建立会话（拿 JSESSIONID）。"""
        url = self.index_url(query_string)
        try:
            res = self.session.get(
                url,
                headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,*/*"},
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=True,
            )
            LOGGER.debug("Portal entry opened: status=%s, url=%s", res.status_code, res.url)
            return res.status_code == 200
        except Exception as exc:
            LOGGER.warning("Failed to open portal entry: %s", exc)
            return False

    def query_page_info(self, query_string: str) -> PortalPageInfo:
        """第 2 步：查询页面配置（验证码开关、RSA 公钥、加密开关）。"""
        payload = self._post_json(
            "pageInfo",
            {"queryString": query_string},
            referer=self.index_url(query_string),
        )
        info = _parse_page_info(payload)
        LOGGER.info(
            "pageInfo: need_valid_code=%s, password_encrypt=%s, service=%s",
            info.need_valid_code,
            info.password_encrypt,
            info.raw.get("service"),
        )
        return info

    def fetch_valid_code(self, valid_code_url: str) -> Optional[bytes]:
        """第 3 步：下载验证码图片。"""
        url = self._absolute(valid_code_url)
        if not url:
            return None
        try:
            res = self.session.get(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": self.base_url,
                },
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            if res.status_code == 200 and res.content:
                return res.content
            LOGGER.warning("Failed to fetch validcode: status=%s", res.status_code)
        except Exception as exc:
            LOGGER.warning("Failed to fetch validcode: %s", exc)
        return None

    # ------------------------------------------------------------- service

    _SELECT_SERVICE_RE = re.compile(r"selectService\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'?(\d+)'?\s*\)")
    _NET_ACCESS_TYPE_RE = re.compile(
        r"name=\"net_access_type\"[^>]*?value='([^']*)'|id=\"net_access_type\"[^>]*?value='([^']*)'"
    )

    def query_services(self, query_string: str) -> List[Dict[str, str]]:
        """查询门户下发的可选服务列表（下拉框数据源）。

        门户把下拉项写在 ``serviceContent`` 的 HTML 片段里，形式为
        ``selectService('<serviceName>', '<展示名>', <序号>)`` ——
        ``serviceName`` 就是浏览器写进隐藏域 ``net_access_type`` 并最终提交的值
        （原始中文，提交前由 JS 编码）。这里解析成 ``[{value, name}]``。

        注意：本校门户 ``typeflag="true"``（后台统一配置），未登录时通常只下发
        「系统默认服务」占位项，真实服务要靠 ``query_account_service`` 拿。
        """
        url = f"{self._interface_url('getServices')}&queryString={query_string}"
        try:
            res = self.session.get(
                url,
                headers=self._browser_headers(referer=self.index_url(query_string)),
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            payload = json.loads(self._response_text(res))
        except Exception as exc:
            LOGGER.debug("getServices failed: %s", exc)
            return []

        services: List[Dict[str, str]] = []
        seen = set()

        for value, name, _index in self._SELECT_SERVICE_RE.findall(str(payload.get("serviceContent") or "")):
            if value and value not in seen:
                seen.add(value)
                services.append({"value": value, "name": name})

        for group in self._NET_ACCESS_TYPE_RE.findall(str(payload.get("defaultService") or "")):
            for value in group:
                if value and value not in seen:
                    seen.add(value)
                    services.append({"value": value, "name": value})

        service_json = str(payload.get("serviceJson") or "").strip()
        if service_json and service_json not in ("[]", "null"):
            try:
                for item in json.loads(service_json):
                    value = str(item.get("serviceName") or "").strip()
                    if value and value not in seen:
                        seen.add(value)
                        services.append({
                            "value": value,
                            "name": str(item.get("serviceShowName") or value),
                        })
            except Exception as exc:
                LOGGER.debug("serviceJson parse failed: %s", exc)

        LOGGER.info("Portal offered services: %s", services)
        return services

    def query_account_service(self, query_string: str, username: str) -> str:
        """查询账号当前应使用的服务名（``userV2.do?method=getServices``）。

        这就是页面上输入学号后门户自动选中下拉框的那次请求，**返回的是原始服务名**
        （如 ``校园网`` / ``iSMU``），会随 ``queryString`` 里的网络环境自动变化：

        * 有线（网口）环境的 queryString → ``校园网``
        * 无线 i-SHMU 环境的 queryString → ``iSMU``

        因此它是比下拉框列表更可靠的取值来源。返回值需要经过
        :func:`encode_service_param` 才能放进登录 payload。
        """
        if not username:
            return ""
        url = f"{self.base_url}userV2.do?method=getServices"
        try:
            res = self.session.post(
                url,
                data={"username": username, "search": "?" + (query_string or "").lstrip("?")},
                headers=self._browser_headers(referer=self.index_url(query_string)),
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            text = self._response_text(res).strip()
            if text and text.lower() != "null":
                LOGGER.info("Account bound service: %s", text)
                return text
        except Exception as exc:
            LOGGER.debug("userV2.getServices failed: %s", exc)
        return ""

    # --------------------------------------------------------------- login

    def submit_login(self, data: Dict) -> Dict:
        """第 5 步：提交登录。"""
        return self._post_json("login", data, referer=self.index_url(str(data.get("queryString", ""))))
