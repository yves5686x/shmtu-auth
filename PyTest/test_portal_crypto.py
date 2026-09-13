"""门户密码加密与验证码工具的单测。

加密期望值全部由门户真实的 ``security.js``（ohdave RSAUtils）在 Node 中跑出来，
用于保证纯 Python 实现与浏览器行为**逐字节一致**。
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
    result = encrypt_password("MyPass123", MAC, MODULUS, EXPONENT)
    assert result == (
        "2dc31a4b1341c4b4e0a96c1f361897923c865580dc607b8ad2dc53040fc2c365ff3fc10bab10ca5e68eae4aa3e5d53ff"
        "b9285c767d021b04d36e69ea7003580c03b88d3bd6b0f21cac3322357ab3d948b555661e8988b8b0d6c4e92bfa52a98"
        "f030b03897d4550b6f98b9c954841f0142def7668f64159b700bcc26fca8d1747"
    )
    assert len(result) == 256


def test_encrypt_password_with_default_mac():
    """mac 缺失时门户 JS 退化成 111111111，结果应与该向量一致。"""
    result = encrypt_password("x", DEFAULT_MAC, MODULUS, EXPONENT)
    assert result == (
        "58cd4e701024d7c3815d0b5023ffbaa26e30533f3e2be82cb336b06449b3fd703e637d3487f5fb3741a0772746766230"
        "933bca85198b0f1b8a6fdb0bbb11aa930fcf840743fe55fa5ce93a60465699470c9668347388b54234e5c9bfb4ae547"
        "540327315d1565719f677d9a907adf60afb75dc0a787dca79310483f1e924e42e"
    )


def test_encrypt_password_multiblock_separated_by_space():
    """超过 126 个码元时门户会切成多块，块之间用空格连接。"""
    result = encrypt_password("a" * 126, "111111111", MODULUS, EXPONENT)
    blocks = result.split(" ")
    assert len(blocks) == 2
    assert all(len(block) % 4 == 0 for block in blocks)
    assert result == (
        "18edbaf6ce3437e10a91b4071238aa3178e0d72f598c4afa8dc8489d261110dd28217c461d8d0ef5a50318360df64b0"
        "0b4eeb646929b25a03bea44ebf3386e0461b041d8d42edb06b3dffd29005c8b4c05babb429ec869a1b05c20164edf36"
        "828ef2d6c5e72ec19cebc138861dba5a01b3eeb1733ce1141bdbbdb0a2f2fbf544"
        " "
        "13707c58a0903af705815abd7737a34371a94551a19c5da4a024b0def0bbd5f3d65304ab35f7e82706b4045537e13c716"
        "e2a350b6d8ff930504bee0e7a554aa3e251684589618a4bffa7fc6f5c6b55bcf1a0774d746bfde84625d558b9a82e8718"
        "709dfd75d1e303c2261329a2e8e701e3c01ae47e0a1665f716f84fae0332d4"
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
