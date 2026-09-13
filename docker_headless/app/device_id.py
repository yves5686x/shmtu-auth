"""设备身份：取本机**物理网卡 MAC**，作为设备号。

先分清两个同名不同义的东西，别混用：

* **本模块的物理 MAC** —— 给自建凭据服务标识「我是哪台设备」，取值由我们自己约定
* **门户 queryString 里的 mac** —— 网关下发的加密串，登录时原样透传即可

容器里的坑：Docker 的 ``eth0`` 是 veth 虚拟网卡，MAC 由 Docker 每次创建时随机分配，
和宿主物理网卡没有任何关系。所以按下面的优先级取值：

1. 显式配置 ``SHMTU_AUTH_DEVICE_MAC`` —— 跨平台最稳，容器里首选
2. 自动探测（容器用 ``network_mode: host`` 时，看到的就是宿主网卡，结果正确；
   非 host 网络时容器内没有物理网卡，会返回空串并给出提示）
3. 挂载宿主 sysfs：把宿主的 ``/sys/class/net`` 挂进容器，再把 ``sysfs_dir``
   指到挂载点，就能在非 host 网络下照样探测

各平台的判据：

* **Linux**：真实物理设备在 sysfs 里有 ``device`` 符号链接，
  ``veth`` / ``docker0`` / ``br-*`` / ``virbr*`` 都没有，天然被排除
* **macOS**：``networksetup -listallhardwareports`` 给出硬件端口 ↔ 设备的映射，
  能干净地排除 ``utun*`` / ``awdl*`` / ``anpi*`` 这些虚拟接口
* **Windows**：``Get-NetAdapter -Physical``

注意 ``uuid.getnode()`` 不可用：它只挑第一个有 MAC 的接口，在 macOS 上常拿到
``anpi*`` 之类虚拟口，在容器里拿到的是 veth。
"""

import os
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import List, Tuple

DEFAULT_SYSFS_NET_DIR = "/sys/class/net"

# 非十六进制字符全丢掉，用来把各种写法的 MAC 归一化
_MAC_STRIP_RE = re.compile(r"[^0-9a-fA-F]")

# 这些前缀的接口一定是虚拟 / 桥接的，两种平台都适用：
# Linux 侧 veth / docker0 / br-* / virbr*，macOS 侧 bridge0(雷雳桥) / utun* / awdl* / ap*
_SKIP_IFACE_PREFIXES = (
    "veth", "docker", "br-", "bridge", "virbr", "tun", "tap",
    "vmnet", "vboxnet", "utun", "awdl", "llw", "ap", "gif", "stf", "anpi", "vnic",
)

# MAC 第一个字节的 bit 1（值 0x02）是「本地管理位」：
# 置位说明不是厂商固化地址 —— Docker 的 02:42:…、macOS 私有 Wi-Fi 地址的 2e:… 都属此类
_LOCAL_ADMIN_BIT = 0x02


@dataclass
class DeviceIdentity:
    """设备号探测结果。"""

    mac: str = ""       # 12 位小写无分隔形式，空串表示没拿到
    iface: str = ""     # 命中的网卡名（配置来源时为空）
    source: str = ""    # config / sysfs:/sys/class/net / networksetup / get-netadapter
    warning: str = ""   # 需要提醒用户的问题（非致命）


def normalize_mac(value: str) -> str:
    """把任意写法的 MAC 统一成 12 位小写无分隔，例如 ``00:E0:1A:00:23:A9`` → ``00e01a0023a9``。

    剥掉非十六进制字符后**必须正好 12 位**，否则返回空串 ——
    否则一个随机字符串（比如 ``not-a-real-mac-1234abcd``）也能凑出看起来合法的值。
    """
    mac = _MAC_STRIP_RE.sub("", value or "").lower()
    return mac if len(mac) == 12 else ""


def format_mac(mac: str, sep: str = ":") -> str:
    """把 MAC 加上分隔符，便于日志里给人看；不是合法 MAC 时原样返回。"""
    normalized = normalize_mac(mac)
    if not normalized:
        return (mac or "").strip()
    return sep.join(normalized[i: i + 2] for i in range(0, 12, 2))


def is_locally_administered(mac: str) -> bool:
    """本地管理位是否为 1。

    置位意味着这不是厂商固化地址，拿来做设备标识可能漂移：
    Docker veth 的 ``02:42:…``、macOS「私有 Wi-Fi 地址」的 ``2e:…`` 都在这一类。
    """
    mac = normalize_mac(mac)
    if len(mac) != 12:
        return False
    try:
        return bool(int(mac[0:2], 16) & _LOCAL_ADMIN_BIT)
    except ValueError:
        return False


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _is_usable(raw: str) -> bool:
    """排除全零和长度不对的地址。"""
    mac = normalize_mac(raw)
    return len(mac) == 12 and set(mac) != {"0"}


def _skip_iface(name: str) -> bool:
    name = (name or "").strip().lower()
    if name in ("lo", "lo0"):
        return True
    return any(name.startswith(prefix) for prefix in _SKIP_IFACE_PREFIXES)


def _default_iface(system: str = "") -> str:
    """当前默认路由走哪个网卡 —— 优先选它，最贴近「本机对外的那张物理网卡」。"""
    if (system or platform.system()).lower() == "darwin":
        try:
            out = subprocess.run(
                ["route", "-n", "get", "default"],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            return ""
        match = re.search(r"interface:\s*(\S+)", out or "")
        return match.group(1) if match else ""

    # Linux：/proc/net/route 第二列是全零表示默认路由
    for line in _read_text("/proc/net/route").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "00000000":
            return parts[0]
    return ""


def _read_sysfs_candidates(net_dir: str) -> List[Tuple[str, str]]:
    """Linux：从 sysfs 读出真实物理网卡的 ``(网卡名, MAC)``。"""
    found: List[Tuple[str, str]] = []
    try:
        names = sorted(os.listdir(net_dir))
    except OSError:
        return found

    for name in names:
        if _skip_iface(name):
            continue
        iface_dir = os.path.join(net_dir, name)
        # 关键判据：只有真实物理设备才有 device 符号链接，veth / bridge 没有
        if not os.path.exists(os.path.join(iface_dir, "device")):
            continue
        mac = _read_text(os.path.join(iface_dir, "address"))
        if _is_usable(mac):
            found.append((name, normalize_mac(mac)))
    return found


def _read_macos_ether(device: str) -> str:
    try:
        out = subprocess.run(
            ["ifconfig", device], capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return ""
    match = re.search(r"\bether\s+([0-9a-fA-F:]{17})", out or "")
    return match.group(1) if match else ""


def _read_macos_candidates() -> List[Tuple[str, str]]:
    """macOS：靠 networksetup 的硬件端口列表把虚拟接口挡在外面。"""
    try:
        out = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        out = ""

    devices: List[str] = []
    port = ""
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:"):
            device = line.split(":", 1)[1].strip()
            # 只要以太网 / Wi-Fi 这类真实端口
            if device and not _skip_iface(device) and port:
                devices.append(device)

    found: List[Tuple[str, str]] = []
    for device in devices:
        mac = _read_macos_ether(device)
        if _is_usable(mac):
            found.append((device, normalize_mac(mac)))
    return found


def _read_windows_candidates() -> List[Tuple[str, str]]:
    """Windows：Get-NetAdapter -Physical 只列物理网卡。"""
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-NetAdapter -Physical | Select-Object -ExpandProperty MacAddress",
            ],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return []

    found: List[Tuple[str, str]] = []
    for index, line in enumerate((out or "").splitlines()):
        mac = normalize_mac(line)
        if _is_usable(mac):
            found.append((f"physical{index}", mac))
    return found


def get_candidates(sysfs_dir: str = "", system: str = "") -> List[Tuple[str, str]]:
    """列出本机所有候选物理网卡的 ``(网卡名, 12 位小写 MAC)``。

    :param system: 显式指定平台（``linux`` / ``darwin`` / ``windows``），
                   留空则自动判断。单测用它覆盖 Linux 分支。
    """
    system = (system or platform.system()).lower()
    if system == "darwin":
        return _read_macos_candidates()
    if system == "windows":
        return _read_windows_candidates()
    return _read_sysfs_candidates(sysfs_dir or DEFAULT_SYSFS_NET_DIR)


def describe_candidates(sysfs_dir: str = "", system: str = "") -> List[str]:
    """给排查日志用的候选列表，形如 ``["eth0=00:e0:1a:00:23:a9"]``。"""
    return [
        f"{iface}={format_mac(mac)}"
        for iface, mac in get_candidates(sysfs_dir, system)
    ]


def _pick(candidates: List[Tuple[str, str]], prefer: str = "") -> Tuple[str, str]:
    """挑一个：优先默认路由那张，其次非本地管理位地址，最后实在没有再退让。"""
    for iface, mac in candidates:
        if iface == prefer and not is_locally_administered(mac):
            return iface, mac
    for iface, mac in candidates:
        if not is_locally_administered(mac):
            return iface, mac
    for iface, mac in candidates:
        if iface == prefer:
            return iface, mac
    return candidates[0]


def detect_device_mac(
    override: str = "",
    sysfs_dir: str = "",
    system: str = "",
) -> DeviceIdentity:
    """探测设备号，返回结果 + 来源 + 需要提醒的问题。

    :param override: 配置里的 ``SHMTU_AUTH_DEVICE_MAC``，任意写法都行
    :param sysfs_dir: 读 sysfs 的目录，默认 ``/sys/class/net``；
                      非 host 网络的容器可指向挂载进来的宿主同名目录
    :param system: 显式指定平台（留空自动判断），单测用
    """
    configured = normalize_mac(override)
    if len(configured) == 12:
        warning = ""
        if is_locally_administered(configured):
            warning = (
                "配置的设备号本地管理位置位（02:/06:/2e: 开头），通常是虚拟网卡"
                "或系统随机化地址，建议换成网卡上固化的物理 MAC"
            )
        return DeviceIdentity(mac=configured, source="config", warning=warning)

    problems: List[str] = []
    if (override or "").strip():
        problems.append(f"配置的设备号 {override!r} 不是合法 MAC，已忽略并改为自动探测")

    system = (system or platform.system()).lower()
    net_dir = sysfs_dir or DEFAULT_SYSFS_NET_DIR

    if system == "darwin":
        candidates, source = _read_macos_candidates(), "networksetup"
    elif system == "windows":
        candidates, source = _read_windows_candidates(), "get-netadapter"
    else:
        candidates, source = _read_sysfs_candidates(net_dir), f"sysfs:{net_dir}"

    if not candidates:
        problems.append(
            "未找到物理网卡。容器请用 network_mode: host，"
            "或直接配置 SHMTU_AUTH_DEVICE_MAC"
        )
        return DeviceIdentity(source=source, warning="；".join(problems))

    iface, mac = _pick(candidates, _default_iface(system))
    if is_locally_administered(mac):
        problems.append(
            f"{iface} 的 MAC 是本地管理地址（虚拟网卡或系统随机化地址），"
            "建议用 SHMTU_AUTH_DEVICE_MAC 显式指定物理 MAC"
        )

    return DeviceIdentity(mac=mac, iface=iface, source=source, warning="；".join(problems))


def get_physical_mac(override: str = "", sysfs_dir: str = "", system: str = "") -> str:
    """只要 MAC 的简化入口：拿不到返回空串。"""
    return detect_device_mac(override, sysfs_dir, system).mac
