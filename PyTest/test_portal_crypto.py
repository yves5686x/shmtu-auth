"""门户密码加密与验证码工具的单测。

加密期望值全部由**门户真实的 JS** 在 Node 中跑出来，用于保证纯 Python 实现与
浏览器行为**逐字节一致**。oracle 脚本见 ``tools/portal_crypto_oracle.js``，
它加载两份真实文件：

* ``interface/index_files/js/security.js`` —— ohdave RSAUtils（填充算法）
* ``interface/index_files/js/AuthInterFace.js`` 里的 ``encryptedPassword`` ——
  **显式把明文反转**（``password.split("").reverse().join("")``）后再交给 RSAUtils

⚠️ 只拿 ``security.js`` 当 oracle 会漏掉那层反转：填充算法本身照样逐字节自洽，
于是「不反转」也能通过 —— 这正是 2026-09 那次「怎么改密码都报『用户不存在或者
密码错误!』」的根因。期望值必须用完整链路生成。
"""

import pytest

from shmtu_auth.src.core.captcha_solver import normalize_code
from shmtu_auth.src.core.eportal_protocol import is_valid_code_error
from shmtu_auth.src.core.portal_crypto import (
    DEFAULT_MAC,
    bi_high_index,
    chunk_size_of,
    encrypt_password,
)

# 来自门户 pageInfo 的真实 1024 位公钥
MODULUS = (
    "94dd2a8675fb779e6b9f7103698634cd400f27a154afa67af6166a43fc26417222a79506d34cacc7641946abda1785b7"
    "acf9910ad6a0978c91ec84d40b71d2891379af19ffb333e7517e390bd26ac312fe940c340466b4a5d4af1d65c3b5944"
    "078f96a1a51a5a53e4bc302818b7c9f63c4a1b07bd7d874cef1c3d4b2f5eb7871"
)
EXPONENT = "10001"
MAC = "67d1ff70d8b083fe77eff0367912afaa"


def test_bi_high_index_and_chunk_size():
    modulus = int(MODULUS, 16)
    assert modulus.bit_length() == 1024
    assert bi_high_index(modulus) == 63
    # 门户的 chunkSize = 2 * biHighIndex = 126，而不是 128
    assert chunk_size_of(modulus) == 126


def test_encrypt_password_matches_portal_js():
    """由 tools/portal_crypto_oracle.js 生成（含 AuthInterFace.js 的反转）。"""
    result = encrypt_password("MyPass123", MAC, MODULUS, EXPONENT)
    assert result == (
        "93d47e0e4058fc02b3c3bbcd79c9b1c32412c41ee9df30bf9593b735855ac821491138aa782622074c7741a0b52fe0f2"
        "4024a5011d10b276b71a3510563252e0ad8ddb516bdedab6dd3bc8fcf9dd4e993087b50347568bb1b74e8265b24537b4"
        "ad3fb80ee8f94f06fcd950dedc7cbd80f7c254e416410b98adc8158bcc40e2a6"
    )
    assert len(result) == 256


def test_encrypt_password_with_default_mac():
    """mac 缺失时门户 JS 退化成 111111111，结果应与该向量一致。"""
    result = encrypt_password("x", DEFAULT_MAC, MODULUS, EXPONENT)
    assert result == (
        "91658e6bce9fd60728d6a0cdbef0dfdca5753c56a76ba9b6fb662fdedb1c161448d1f4336e02228de244cdce5b25aa19"
        "cd5818a6474a97bcf198e7b922783c11c83551b17c56ac64c4431c06194f0817723c9f3feb208725852de913be51419f"
        "bd3cc67f1ab917846a9dadff8875034a4a82fe46455b2d19fdc4db8457ac58b5"
    )


def test_plaintext_is_reversed_before_rsa():
    """守护「明文先反转」这一步 —— 漏掉它加密结果结构上仍然合法，只是门户解不开。

    门户链路：``login_bch.js`` 拼 ``password + ">" + mac`` →
    ``AuthInterFace.js`` 的 ``encryptedPassword`` 里 ``split("").reverse().join("")``
    → ``RSAUtils.encryptedString``。RSAUtils 自己**不**反转，反转必须在调用侧完成。

    这个用例专门锁住「不反转」的旧结果：一旦有人把 ``units.reverse()`` 删掉，
    新向量仍会通过（因为它就是按反转生成的），但这里会立刻失败。
    """
    not_reversed = (
        "2dc31a4b1341c4b4e0a96c1f361897923c865580dc607b8ad2dc53040fc2c365ff3fc10bab10ca5e68eae4aa3e5d53ff"
        "b9285c767d021b04d36e69ea7003580c03b88d3bd6b0f21cac3322357ab3d948b555661e8988b8b0d6c4e92bfa52a98"
        "f030b03897d4550b6f98b9c954841f0142def7668f64159b700bcc26fca8d1747"
    )
    assert encrypt_password("MyPass123", MAC, MODULUS, EXPONENT) != not_reversed, (
        "不反转的旧密文又对上了：反转步骤被去掉，浏览器会判「用户不存在或者密码错误!」"
    )


def test_encrypt_password_multiblock_separated_by_space():
    """超过 126 个码元时门户会切成多块，块之间用空格连接。"""
    result = encrypt_password("a" * 126, "111111111", MODULUS, EXPONENT)
    blocks = result.split(" ")
    assert len(blocks) == 2
    assert all(len(block) % 4 == 0 for block in blocks)
    assert result == (
        "571b1d9c8fc46403db47c30a92b8bef75a5889a1a6e8465fc4d342bfbdec0ad7d58b9814371bfdc064f644cbdd68bc6d"
        "fe37b23b1e23343c4a9b41a2cc9cc88ed5e0d1413780c8298e3cb5751fc13cc09d5fbd6f14f02312d873f4ff421b966c"
        "06b5b54641bfb78e898dc806ad64939c55b75096e8518f1f3f280face47ffbb0"
        " "
        "10d78c8123805d0ffb353bbc0b2bbd19ba0036695dc3129e41778536fa3628dd298da046a2d6a2553a03ad27d5a378ea"
        "ac2b966de8e86bf7aa03f10cd2ee55cf76b65f890b11d17adb179226543149a552a99c40294c2c1d8eb66c22b5853281"
        "a014e6e6e8d14135741a596a12fd06be8427453b6759a53108f97959116098f4"
    )


def test_encrypt_password_requires_modulus():
    with pytest.raises(ValueError):
        encrypt_password("pwd", MAC, "", EXPONENT)


def test_normalize_code():
    assert normalize_code("1234") == "1234"
    # OCR 常见噪声：空白、换行、字母
    assert normalize_code("  1 2 3 4\n") == "1234"
    assert normalize_code("a1b2c3d4") == "1234"
    # 位数不对一律视为不可信，交给上层兜底
    assert normalize_code("123") is None
    assert normalize_code("12345") is None
    assert normalize_code("") is None
    assert normalize_code(None) is None


def test_is_valid_code_error():
    assert is_valid_code_error("验证码错误.")
    assert is_valid_code_error("验证码不能为空")
    assert is_valid_code_error("validCode is invalid")
    assert not is_valid_code_error("认证失败")
    assert not is_valid_code_error("")
    assert not is_valid_code_error(None)
