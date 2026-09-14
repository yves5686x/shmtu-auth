"""H3C eportal 门户密码加密（纯 Python，不引入第三方依赖）。

门户页面的 ``security.js`` 使用 David Shapiro（ohdave）的 RSAUtils 库，
其 ``encryptedString`` **不是**标准的 PKCS#1 v1.5 填充，算法实际是：

1. 把字符串按 UTF-16 码元展开成数组 ``a[]``（``charCodeAt`` 语义）
2. 在末尾补 ``0``，直到 ``len(a)`` 是 ``chunkSize`` 的整数倍
3. 每 ``chunkSize`` 个码元打包成 ``chunkSize / 2`` 个 **16 位小端字**，拼成大整数 ``m``
4. ``c = m ** e mod n``
5. 输出十六进制：每个 16 位字固定 4 位，高位字在前，且省略前导的全零字

其中 ``chunkSize = 2 * biHighIndex(modulus)``。
对 1024 位模数而言 ``biHighIndex = 63``，因此 ``chunkSize = 126``（而不是 128）。
长字符串会被切成多个块，块之间用 **空格** 连接。

加密的**明文**是 ``reverse(password + ">" + mac)``，``mac`` 取自门户 URL 的 ``mac``
参数，缺失时门户 JS 会退化成 ``"111111111"``。

⚠️ 那个 **reverse 极易漏掉**，因为 ``security.js`` 的 ``RSAUtils.encryptedString``
本身并不反转，反转发生在调用它的包装函数里：

* ``login_bch.js``： ``password = encryptedPassword(password + ">" + macString)``
* ``AuthInterFace.js``： ::

      function encryptedPassword(password){
          var passwordEncode = password.split("").reverse().join("");
          ...
          RSAUtils.encryptedString(key, passwordEncode);
      }

（``index.jsp`` 内**没有**同名定义，所以生效的一定是 ``AuthInterFace.js`` 这一份，
反转必然发生。门户 JS 原注释也写着「这里需要把字符串进行反转，不然加密的结果是错的」。）

只拿 ``security.js`` 当 oracle 验证「填充算法」，会得到「不反转」也能逐字节自洽的
错误结论 —— 那正是这个 bug 曾经潜伏下来的原因。生成期望值时必须带上这层包装，
见 ``tools/portal_crypto_oracle.js``。

非 ASCII 输入在原 JS 库中会因 ``charCodeAt`` 超过 16 位导致内部 BigInt 溢出，
结果不再可用，此处不做兼容。
"""

from typing import List

# 门户 JS 中 mac 缺失时的兜底值
DEFAULT_MAC = "111111111"

# 门户 JS 中 `if(password.length > 150) encrypt = "true"` 的阈值
ENCRYPTED_PASSWORD_MIN_LENGTH = 150


def _to_code_units(text: str) -> List[int]:
    """按 UTF-16 码元展开字符串，等价于 JS 的 ``charCodeAt`` 序列。"""
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i: i + 2], "little") for i in range(0, len(raw), 2)]


def bi_high_index(modulus: int) -> int:
    """等价于 RSAUtils.biHighIndex：返回最高非零 16 位字的索引。"""
    if modulus == 0:
        return 0
    return (modulus.bit_length() - 1) // 16


def chunk_size_of(modulus: int) -> int:
    """等价于 RSAKeyPair 的 ``chunkSize = 2 * biHighIndex(modulus)``。"""
    return 2 * bi_high_index(modulus)


def encrypt_password(
    password: str,
    mac: str = DEFAULT_MAC,
    public_key_modulus: str = "",
    public_key_exponent: str = "10001",
) -> str:
    """按门户 JS 的算法加密密码。

    :param password: 明文密码
    :param mac: 门户 URL 中的 mac 参数（缺失时应传 DEFAULT_MAC）
    :param public_key_modulus: pageInfo 返回的 publicKeyModulus（十六进制字符串）
    :param public_key_exponent: pageInfo 返回的 publicKeyExponent（十六进制字符串）
    :return: 十六进制密文，多块之间以空格分隔
    :raises ValueError: 公钥缺失或非法
    """
    modulus_hex = (public_key_modulus or "").strip()
    exponent_hex = (public_key_exponent or "").strip() or "10001"
    if not modulus_hex:
        raise ValueError("publicKeyModulus is empty; cannot encrypt password")

    modulus = int(modulus_hex, 16)
    exponent = int(exponent_hex, 16)

    chunk_size = chunk_size_of(modulus)
    if chunk_size <= 0:
        raise ValueError("invalid publicKeyModulus: chunkSize must be positive")
    if chunk_size % 2 != 0:
        raise ValueError("invalid publicKeyModulus: chunkSize must be even")

    # 明文 = reverse(password + ">" + mac)。
    #
    # login_bch.js 先把两者拼起来再调 encryptedPassword()，而 AuthInterFace.js 的
    # encryptedPassword 会先 `password.split("").reverse().join("")` 才送进
    # RSAUtils.encryptedString。RSAUtils 本身不反转，所以这一步必须由我们补上。
    #
    # 反转的是 JS 的 **UTF-16 码元序列**（split("")），不是 Unicode 码点，
    # 所以先展开成码元再整体倒序，非 BMP 字符也能对上。
    units = _to_code_units(password + ">" + (mac or DEFAULT_MAC))
    units.reverse()

    # JS: while (a.length % chunkSize != 0) a[i++] = 0;
    remainder = len(units) % chunk_size
    if remainder != 0:
        units.extend([0] * (chunk_size - remainder))

    blocks: List[str] = []
    half = chunk_size // 2
    for start in range(0, len(units), chunk_size):
        block = units[start: start + chunk_size]

        # 等价于 block.digits[j] = a[k++] | (a[k++] << 8)，digits 低位在前
        value = 0
        for j in range(half - 1, -1, -1):
            value = (value << 16) | (block[j * 2] | (block[j * 2 + 1] << 8))

        hex_text = format(pow(value, exponent, modulus), "x")
        # 等价于 biToHex：每个 16 位字占 4 位，因此补齐到 4 的整数倍
        hex_text = hex_text.zfill(((len(hex_text) + 3) // 4) * 4)
        blocks.append(hex_text)

    return " ".join(blocks)
