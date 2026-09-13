"""守护 ``docker_headless`` 与主包共用模块不漂移。

``docker_headless`` 是刻意拆出来的无 GUI 副本（不依赖 loguru / toml / PyQt），
所以 ``portal_crypto.py`` 被复制成了两份。密码加密算法一旦改动而只改一份，
线上表现就会静默不一致 —— 这个测试就是那道防线。
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PORTAL_CRYPTO = REPO_ROOT / "src/shmtu_auth/src/core/portal_crypto.py"
DOCKER_PORTAL_CRYPTO = REPO_ROOT / "docker_headless/app/portal_crypto.py"

# 来自门户 pageInfo 的真实 1024 位公钥（与 test_portal_crypto.py 保持一致）
MODULUS = (
    "94dd2a8675fb779e6b9f7103698634cd400f27a154afa67af6166a43fc26417222a79506d34cacc7641946abda1785b7"
    "acf9910ad6a0978c91ec84d40b71d2891379af19ffb333e7517e390bd26ac312fe940c340466b4a5d4af1d65c3b5944"
    "078f96a1a51a5a53e4bc302818b7c9f63c4a1b07bd7d874cef1c3d4b2f5eb7871"
)
EXPONENT = "10001"
MAC = "67d1ff70d8b083fe77eff0367912afaa"

# 覆盖跨块边界（chunkSize = 126）的一组长度
PASSWORD_VECTORS = ["x", "MyPass123", "a" * 125, "a" * 126, "a" * 127, "p" * 400]


def _read_normalized(path: Path) -> str:
    """按文本读取并统一换行符 —— 两份文件的换行风格允许不同。"""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _load_docker_portal_crypto():
    """独立加载 docker 那份（它只依赖 typing，不依赖 app 包）。"""
    spec = importlib.util.spec_from_file_location("docker_portal_crypto", DOCKER_PORTAL_CRYPTO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_both_files_exist():
    assert MAIN_PORTAL_CRYPTO.is_file(), f"缺少 {MAIN_PORTAL_CRYPTO}"
    assert DOCKER_PORTAL_CRYPTO.is_file(), f"缺少 {DOCKER_PORTAL_CRYPTO}"


def test_portal_crypto_source_is_identical():
    main_src = _read_normalized(MAIN_PORTAL_CRYPTO)
    docker_src = _read_normalized(DOCKER_PORTAL_CRYPTO)
    assert main_src == docker_src, (
        "两份 portal_crypto.py 内容不一致，请同步修改：\n"
        f"  主包   {MAIN_PORTAL_CRYPTO}\n"
        f"  Docker {DOCKER_PORTAL_CRYPTO}"
    )


def test_portal_crypto_outputs_match():
    """即使源码允许有细微差异，加密结果也必须逐字节一致。"""
    from shmtu_auth.src.core.portal_crypto import encrypt_password as main_encrypt

    docker = _load_docker_portal_crypto()

    for password in PASSWORD_VECTORS:
        main_result = main_encrypt(password, MAC, MODULUS, EXPONENT)
        docker_result = docker.encrypt_password(password, MAC, MODULUS, EXPONENT)
        assert main_result == docker_result, f"password={password!r} 加密结果不一致"


def test_docker_portal_crypto_defaults_match():
    from shmtu_auth.src.core import portal_crypto as main_module

    docker = _load_docker_portal_crypto()

    assert docker.DEFAULT_MAC == main_module.DEFAULT_MAC
    assert docker.ENCRYPTED_PASSWORD_MIN_LENGTH == main_module.ENCRYPTED_PASSWORD_MIN_LENGTH
