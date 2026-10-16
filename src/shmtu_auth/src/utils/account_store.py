"""网页账号设置：原子写入，明文密码只供认证使用，不回传浏览器。"""
import json
import os
from pathlib import Path
import re
import tempfile


def settings_path():
    return Path(os.environ.get("SHMTU_AUTH_SETTINGS_PATH", "./data/accounts.json"))


def write_private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_accounts():
    """None 表示未使用网页配置；空列表表示用户主动清空所有账号。"""
    path = settings_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    users = validate_accounts(data["users"])
    return [(user["id"], user["password"], raw.get("encrypted") is True)
            for user, raw in zip(users, data["users"])]


def validate_accounts(users, existing=()):
    if not isinstance(users, list) or len(users) > 50:
        raise ValueError("账号列表最多包含 50 个账号")
    passwords = {user: (password, encrypted) for user, password, encrypted in existing}
    result, seen = [], set()
    for item in users:
        if not isinstance(item, dict):
            raise ValueError("账号格式不正确")
        user, password = item.get("id"), item.get("password", "")
        if not isinstance(user, str) or not re.fullmatch(r"[0-9]{1,32}", user):
            raise ValueError("学号须为 1 至 32 位数字")
        if user in seen:
            raise ValueError("学号不能重复")
        if not isinstance(password, str):
            raise ValueError("密码格式不正确")
        encrypted = False
        if not password:
            password, encrypted = passwords.get(user, ("", False))
        if not password or len(password) > 512 or any(c in password for c in "\r\n\0"):
            raise ValueError("请填写密码，密码不能包含换行或空字符")
        seen.add(user)
        result.append({"id": user, "password": password, "encrypted": encrypted})
    return result


def save_accounts(users, existing=()):
    users = validate_accounts(users, existing)
    write_private_json(settings_path(), {"version": 1, "users": users})
