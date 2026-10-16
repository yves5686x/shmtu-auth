import pytest

from shmtu_auth.src.utils import account_store, program_env_config
from shmtu_auth.src.web.server import create_app, load_admin, RuntimeState
from shmtu_auth.src.monitor import auth_status

PASSWORD = "test-management-password"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("SHMTU_AUTH_SETTINGS_PATH", str(tmp_path / "accounts.json"))
    monkeypatch.setenv("SHMTU_AUTH_WEB_PASSWORD", PASSWORD)
    monkeypatch.setattr(program_env_config, "get_user_list_from_service", lambda: [])
    monkeypatch.setattr(program_env_config, "get_user_num_list", lambda: [])
    state = RuntimeState()
    app = create_app(state)
    app.config["TESTING"] = True
    return app, state, tmp_path


def login(client):
    result = client.post("/api/login", json={"password": PASSWORD})
    assert result.status_code == 200
    return {"X-CSRF-Token": result.json["csrf"]}


def test_anonymous_access_is_rejected(web):
    app, _, _ = web
    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/api/accounts").status_code == 401
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/accounts", json={"users": []}).status_code == 401
    assert client.post("/api/login", json={"password": "bad"}).status_code == 401


def test_login_rate_limit(web):
    client = web[0].test_client()
    for _ in range(10):
        assert client.post("/api/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/login", json={"password": "bad"}).status_code == 429


def test_csrf_and_cross_origin_writes_are_rejected(web):
    client = web[0].test_client()
    headers = login(client)
    assert client.post("/api/accounts", json={"users": []}).status_code == 403
    assert client.post("/api/accounts", json={"users": []}, headers={**headers, "Origin": "https://other.invalid"}).status_code == 403
    assert client.post("/api/accounts", json=[], headers=headers).status_code == 400
    assert client.post("/api/accounts", data="{}", headers=headers).status_code == 415


def test_passwords_persist_exactly_and_never_return_to_browser(web):
    app, state, path = web
    client = app.test_client()
    headers = login(client)
    password = "  secret$with'quotes\\and空格  "
    result = client.post("/api/accounts", json={"users": [{"id": "123", "password": password}]}, headers=headers)
    assert result.status_code == 200
    assert state.wake.is_set()
    assert program_env_config.get_user_list() == [("123", password, False)]
    assert client.get("/api/accounts").json == {"users": [{"id": "123", "has_password": True}]}
    assert "secret" not in client.get("/api/status").get_data(as_text=True)
    assert (path / "accounts.json").stat().st_mode & 0o777 == 0o600
    # 重建应用后仍然可以用原管理密码登录，账号数据保留。
    other = create_app().test_client()
    login(other)
    assert other.get("/api/accounts").json["users"][0]["id"] == "123"
    assert client.post("/api/accounts", json={"users": [{"id": "123", "password": ""}]}, headers=headers).status_code == 200
    assert program_env_config.get_user_list()[0][1] == password


@pytest.mark.parametrize("users", [
    [{"id": "123", "password": ""}], [{"id": "x<script>", "password": "p"}],
    [{"id": "123", "password": "p\nq"}], [{"id": "123", "password": "p"}] * 2,
    [{"id": "123", "password": 123}], "not-a-list",
])
def test_invalid_settings_do_not_overwrite_config(web, users):
    client = web[0].test_client()
    headers = login(client)
    assert client.post("/api/accounts", json={"users": users}, headers=headers).status_code == 400
    assert account_store.read_accounts() is None


def test_removing_all_accounts_overrides_environment(web, monkeypatch):
    monkeypatch.setattr(program_env_config, "get_user_num_list", lambda: ["123"])
    monkeypatch.setattr(program_env_config, "get_user_pwd", lambda user: "env-password")
    assert program_env_config.get_user_list()
    client = web[0].test_client()
    headers = login(client)
    assert client.post("/api/accounts", json={"users": []}, headers=headers).status_code == 200
    assert program_env_config.get_user_list() == []


def test_bad_json_file_is_not_silently_ignored(web):
    (web[2] / "accounts.json").write_text("{broken")
    with pytest.raises(ValueError):
        program_env_config.get_user_list()


def test_keep_existing_encrypted_password_preserves_its_flag(web):
    account_store.save_accounts([{"id": "123", "password": ""}], [("123", "cipher", True)])
    assert program_env_config.get_user_list() == [("123", "cipher", True)]
    account_store.save_accounts([{"id": "123", "password": "new-raw"}], program_env_config.get_user_list())
    assert program_env_config.get_user_list() == [("123", "new-raw", False)]


def test_changing_admin_password_invalidates_old_sessions(web):
    app, _, path = web
    first, old = app.test_client(), app.test_client()
    headers = login(first)
    login(old)
    result = first.post("/api/password", json={"current": PASSWORD, "password": "replacement-password"}, headers=headers)
    assert result.status_code == 200
    assert old.get("/api/status").status_code == 401
    assert first.get("/api/status").status_code == 200
    assert app.test_client().post("/api/login", json={"password": PASSWORD}).status_code == 401
    reloaded = create_app().test_client()
    assert reloaded.post("/api/login", json={"password": "replacement-password"}).status_code == 200
    assert "replacement-password" not in (path / "web-admin.json").read_text()


def test_default_admin_password_only_applies_on_first_initialization(tmp_path, monkeypatch):
    monkeypatch.setenv("SHMTU_AUTH_SETTINGS_PATH", str(tmp_path / "accounts.json"))
    monkeypatch.delenv("SHMTU_AUTH_WEB_PASSWORD", raising=False)
    app = create_app()
    client = app.test_client()
    result = client.post("/api/login", json={"password": "Letsgo123!@#"})
    assert result.status_code == 200
    result = client.post("/api/password", json={"current": "Letsgo123!@#", "password": "custom-management-password"},
                         headers={"X-CSRF-Token": result.json["csrf"]})
    assert result.status_code == 200
    reloaded = create_app().test_client()
    assert reloaded.post("/api/login", json={"password": "Letsgo123!@#"}).status_code == 401
    assert reloaded.post("/api/login", json={"password": "custom-management-password"}).status_code == 200


def test_web_monitor_waits_until_accounts_are_added_and_honors_removal(web, monkeypatch):
    state = web[1]
    class StopTest(Exception):
        pass
    class FakeAuth:
        def login_by_list(self, users):
            assert users == [("123", "p", False)]
            return True
    stages = []
    class Wake:
        def wait(self, interval):
            stages.append(state.snapshot()["phase"])
            if len(stages) == 1:
                account_store.save_accounts([{"id": "123", "password": "p"}])
            elif len(stages) == 2:
                account_store.save_accounts([])
            else:
                raise StopTest
        def clear(self):
            pass
    monkeypatch.setattr(auth_status, "ShmtuNetAuth", FakeAuth)
    monkeypatch.setattr(auth_status, "check_is_connected", lambda: False)
    with pytest.raises(StopTest):
        auth_status.monitor_auth(wait_for_users=True, on_status=state.update, wake_event=Wake())
    assert stages == ["waiting", "online", "waiting"]
