class ServiceType:
    # 有线「校园网」。门户在表单里会把它再编码一层，因此这里保留一次编码后的字面值，
    # requests 用 data= 提交时即可得到与浏览器一致的 %25E6%25A0... 字节。
    EDU = "%E6%A0%A1%E5%9B%AD%E7%BD%91"
    # 无线 i-SHMU，门户直接下发裸串，不做任何编码
    ISMU = "iSMU"
    China_Mobile = ""
    China_Unicom = ""


def get_default_query_string() -> str:
    return ""
