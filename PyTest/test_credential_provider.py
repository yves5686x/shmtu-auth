"""自建凭据服务客户端的单元测试。"""

import json
from pathlib import Path

from shmtu_auth.src.core import credential_provider as cp

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PROVIDER = REPO_ROOT / "src" / "shmtu_auth" / "src" / "core" / "credential_provider.py"
DOCKER_PROVIDER = REPO_ROOT / "docker_headless" / "app" / "credential_provider.py"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


def _patch_get(monkeypatch, response=None, error=None):
    def fake_get(url, **kwargs):
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(cp.requests, "get", fake_get)


def test_build_request_url():
    assert cp.build_request_url("https://s/v", "aa") == "https://s/v?mac=aa"
    assert cp.build_request_url("https://s/v?t=1", "aa") == "https://s/v?t=1&mac=aa"
    assert cp.build_request_url("https://s/{mac}/v", "aa") == "https://s/aa/v"
    assert cp.build_request_url("", "aa") == ""


def test_parse_payload_supports_both_shapes():
    bundle = cp.parse_payload({"users": [{"id": "1", "password": "p"}]}, "mac")
    assert bundle.users == [{"id": "1", "password": "p"}]
    assert bundle.ok and bundle.source == "remote" and bundle.mac == "mac"

    bundle = cp.parse_payload({"user": "2", "password": "q"})
    assert bundle.users == [{"id": "2", "password": "q"}]

    # 别名字段也认
    bundle = cp.parse_payload({"users": [{"userId": "3", "pwd": "r"}]})
    assert bundle.users == [{"id": "3", "password": "r"}]


def test_parse_payload_skips_incomplete_entries():
    bundle = cp.parse_payload({"users": [{"id": "", "password": "x"}, "junk", {"id": "4"}]})
    assert bundle.users == []
    assert not bundle.ok


def test_parse_payload_reads_optional_fields():
    bundle = cp.parse_payload(
        {"users": [{"id": "1", "password": "p"}], "service": "iSMU", "machine": "srv", "ttl": "60"}
    )
    assert (bundle.service, bundle.machine, bundle.ttl) == ("iSMU", "srv", 60)

    bundle = cp.parse_payload({"users": [{"id": "1", "password": "p"}], "ttl": "oops"})
    assert bundle.ttl == cp.DEFAULT_TTL


def test_fetch_credentials_success(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(payload={"users": [{"id": "1", "password": "p"}]}))
    bundle, error = cp.fetch_credentials("https://s/v", "00e01a0023a9", token="tk")
    assert bundle is not None and bundle.ok
    assert error == ""


def test_fetch_credentials_rejects_bad_input():
    bundle, error = cp.fetch_credentials("", "00e01a0023a9")
    assert bundle is None and "未配置" in error

    bundle, error = cp.fetch_credentials("https://s/v", "")
    assert bundle is None and "物理 MAC" in error


def test_fetch_credentials_handles_http_error(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(status_code=403))
    bundle, error = cp.fetch_credentials("https://s/v", "00e01a0023a9")
    assert bundle is None and "403" in error


def test_fetch_credentials_handles_network_error(monkeypatch):
    _patch_get(monkeypatch, error=OSError("connection refused"))
    bundle, error = cp.fetch_credentials("https://s/v", "00e01a0023a9")
    assert bundle is None and "connection refused" in error


def test_fetch_credentials_handles_invalid_json(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(json_error=True))
    bundle, error = cp.fetch_credentials("https://s/v", "00e01a0023a9")
    assert bundle is None and "JSON" in error


def test_fetch_credentials_reports_empty_users(monkeypatch):
    _patch_get(
        monkeypatch,
        response=_FakeResponse(payload={"users": [], "message": "设备未注册"}),
    )
    bundle, error = cp.fetch_credentials("https://s/v", "00e01a0023a9")
    assert bundle is None and "设备未注册" in error


def test_cache_round_trip(tmp_path):
    path = str(tmp_path / "data" / "credentials.json")
    original = cp.CredentialBundle(
        users=[{"id": "1", "password": "p"}],
        service="校园网",
        machine="srv",
        mac="00e01a0023a9",
        fetched_at=123.0,
        ttl=600,
    )
    assert cp.save_cache(path, original) is True

    loaded = cp.load_cache(path)
    assert loaded is not None
    assert loaded.users == original.users
    assert loaded.service == original.service
    assert loaded.mac == original.mac
    assert loaded.source == "cache"


def test_load_cache_tolerates_garbage(tmp_path):
    assert cp.load_cache(str(tmp_path / "missing.json")) is None

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cp.load_cache(str(bad)) is None

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"users": []}), encoding="utf-8")
    assert cp.load_cache(str(empty)) is None


def test_resolve_prefers_remote_and_writes_cache(monkeypatch, tmp_path):
    path = str(tmp_path / "credentials.json")
    _patch_get(monkeypatch, response=_FakeResponse(payload={"user": "1", "password": "p"}))

    bundle, note = cp.resolve_credentials("https://s/v", "00e01a0023a9", cache_path=path)
    assert bundle is not None
    assert bundle.source == "remote"
    assert note == ""
    assert cp.load_cache(path) is not None


def test_resolve_falls_back_to_cache(monkeypatch, tmp_path):
    path = str(tmp_path / "credentials.json")
    cp.save_cache(path, cp.CredentialBundle(users=[{"id": "9", "password": "cached"}]))

    _patch_get(monkeypatch, error=OSError("boom"))
    bundle, note = cp.resolve_credentials("https://s/v", "00e01a0023a9", cache_path=path)
    assert bundle is not None
    assert bundle.users == [{"id": "9", "password": "cached"}]
    assert "回退到本地缓存" in note


def test_resolve_without_url_is_silent():
    assert cp.resolve_credentials("", "00e01a0023a9") == (None, "")


def test_bundle_freshness():
    bundle = cp.CredentialBundle(users=[{"id": "1", "password": "p"}], fetched_at=100.0, ttl=10)
    assert bundle.is_fresh(now=105.0) is True
    assert bundle.is_fresh(now=111.0) is False
    assert cp.CredentialBundle(users=[{"id": "1", "password": "p"}]).is_fresh() is False


def test_docker_copy_is_identical():
    """docker_headless 是独立副本，两份必须逐字节一致（改一份就得改两份）。"""
    assert MAIN_PROVIDER.read_text(encoding="utf-8") == DOCKER_PROVIDER.read_text(encoding="utf-8")
