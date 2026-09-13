"""设备号（物理网卡 MAC）探测的单元测试。

Linux 分支用构造出来的假 sysfs 目录覆盖 —— 这样不必真进容器，
也能验证「host 网络能选对、非 host 网络会明确报错」这两条关键行为。
"""

from pathlib import Path

from shmtu_auth.src.core import device_id as did

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_DEVICE_ID = REPO_ROOT / "src" / "shmtu_auth" / "src" / "core" / "device_id.py"
DOCKER_DEVICE_ID = REPO_ROOT / "docker_headless" / "app" / "device_id.py"


def _make_net(tmp_path, entries):
    """按 ``{网卡名: (MAC, 是否物理设备)}`` 造一个假 sysfs 目录。"""
    net = tmp_path / "net"
    net.mkdir()
    for iface, (mac, physical) in entries.items():
        iface_dir = net / iface
        iface_dir.mkdir()
        (iface_dir / "address").write_text(mac + "\n", encoding="utf-8")
        if physical:
            # 真实物理设备才有 device 符号链接
            (iface_dir / "device").mkdir()
    return str(net)


def test_normalize_and_format_mac():
    assert did.normalize_mac("00:E0:1A:00:23:A9") == "00e01a0023a9"
    assert did.normalize_mac("00-e0-1a-00-23-a9") == "00e01a0023a9"
    assert did.normalize_mac("00e01a0023a9") == "00e01a0023a9"
    assert did.normalize_mac("") == ""
    # 剥掉非 hex 字符后长度不对的，一律判为非法，避免随机字符串凑出"看起来合法"的值
    assert did.normalize_mac("garbage!!") == ""
    assert did.normalize_mac("not-a-real-mac-1234abcd") == ""
    assert did.normalize_mac("00e01a0023a") == ""
    assert did.normalize_mac("00:e0:1a:00:23:a9:ff") == ""

    assert did.format_mac("00e01a0023a9") == "00:e0:1a:00:23:a9"
    assert did.format_mac("00e01a0023a9", "-") == "00-e0-1a-00-23-a9"
    assert did.format_mac("short") == "short"


def test_locally_administered_bit():
    # Docker veth 与 macOS 私有 Wi-Fi 地址都落在这里
    assert did.is_locally_administered("0242ac110002") is True
    assert did.is_locally_administered("2e0263f7b3c1") is True
    # 厂商固化地址
    assert did.is_locally_administered("00e01a0023a9") is False
    assert did.is_locally_administered("001f0578a431") is False
    assert did.is_locally_administered("bogus") is False


def test_host_network_container_sees_physical_nics(tmp_path):
    net = _make_net(
        tmp_path,
        {
            "lo": ("00:00:00:00:00:00", False),
            "eth0": ("00:e0:1a:00:23:a9", True),
            "ens18": ("00:1f:05:78:a4:31", True),
            "docker0": ("02:42:11:22:33:44", False),
            "veth1234": ("02:42:ac:11:00:02", False),
            "br-abc": ("02:42:99:88:77:66", False),
        },
    )

    # 虚拟网卡全部被排除，全零地址也被排除
    assert did.describe_candidates(net, "linux") == [
        "ens18=00:1f:05:78:a4:31",
        "eth0=00:e0:1a:00:23:a9",
    ]

    info = did.detect_device_mac(sysfs_dir=net, system="linux")
    assert info.mac == "001f0578a431"
    assert info.iface == "ens18"
    assert info.warning == ""


def test_container_without_host_network_fails_loudly(tmp_path):
    """非 host 网络时容器里只有 veth —— 必须报错而不是悄悄返回一个假 MAC。"""
    net = _make_net(
        tmp_path,
        {
            "eth0": ("02:42:ac:11:00:02", False),
            "veth1234": ("02:42:ac:11:00:03", False),
        },
    )

    assert did.describe_candidates(net, "linux") == []
    info = did.detect_device_mac(sysfs_dir=net, system="linux")
    assert info.mac == ""
    assert "network_mode: host" in info.warning


def test_configured_mac_wins(tmp_path):
    net = _make_net(tmp_path, {"eth0": ("00:e0:1a:00:23:a9", True)})

    info = did.detect_device_mac(override="00-1F-05-78-A4-31", sysfs_dir=net, system="linux")
    assert info.mac == "001f0578a431"
    assert info.source == "config"
    assert info.iface == ""
    assert info.warning == ""


def test_invalid_configured_mac_falls_back_with_warning(tmp_path):
    net = _make_net(tmp_path, {"eth0": ("00:e0:1a:00:23:a9", True)})

    info = did.detect_device_mac(override="not-a-mac", sysfs_dir=net, system="linux")
    assert info.mac == "00e01a0023a9"
    assert "不是合法 MAC" in info.warning


def test_configured_local_admin_mac_warns():
    info = did.detect_device_mac(override="02:42:ac:11:00:02", system="linux")
    assert info.mac == "0242ac110002"
    assert "本地管理位" in info.warning


def test_missing_sysfs_dir_is_not_fatal():
    info = did.detect_device_mac(sysfs_dir="/definitely/not/here", system="linux")
    assert info.mac == ""
    assert info.warning != ""


def test_get_physical_mac_shortcut(tmp_path):
    net = _make_net(tmp_path, {"eth0": ("00:e0:1a:00:23:a9", True)})
    assert did.get_physical_mac(sysfs_dir=net, system="linux") == "00e01a0023a9"
    assert did.get_physical_mac(sysfs_dir="/nope", system="linux") == ""


def test_native_detection_does_not_crash():
    """在真实平台上跑一次，只要求不抛异常（结果是环境相关的，不做断言）。"""
    info = did.detect_device_mac()
    assert isinstance(info.mac, str)
    assert len(info.mac) in (0, 12)


def test_docker_copy_is_identical():
    """docker_headless 是独立副本，两份必须逐字节一致（改一份就得改两份）。"""
    assert MAIN_DEVICE_ID.read_text(encoding="utf-8") == DOCKER_DEVICE_ID.read_text(encoding="utf-8")
