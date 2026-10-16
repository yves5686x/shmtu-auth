from collections import deque
from datetime import timedelta
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from shmtu_auth.src.utils.account_store import save_accounts, settings_path, write_private_json
from shmtu_auth.src.utils.program_env_config import get_user_list

DEFAULT_WEB_PASSWORD = "Letsgo123!@#"


class RuntimeState:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.value = {"phase": "waiting", "message": "等待配置账号"}

    def update(self, phase, message):
        with self.lock:
            self.value = {"phase": phase, "message": message, "updated_at": int(time.time())}

    def snapshot(self):
        with self.lock:
            return dict(self.value)


def load_admin():
    path = settings_path().parent / "web-admin.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    password = os.environ.get("SHMTU_AUTH_WEB_PASSWORD") or DEFAULT_WEB_PASSWORD
    if len(password) < 12:
        raise ValueError("网页管理密码至少需要 12 个字符")
    data = {"password_hash": generate_password_hash(password), "secret": secrets.token_hex(32)}
    write_private_json(path, data)
    if not os.environ.get("SHMTU_AUTH_WEB_PASSWORD"):
        print(f"网页初始管理密码（仅首次显示）: {password}", flush=True)
    return data


def create_app(state=None, admin=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    state = state or RuntimeState()
    admin = admin or load_admin()
    app.config.update(SECRET_KEY=admin["secret"], MAX_CONTENT_LENGTH=64 * 1024,
                      SESSION_COOKIE_NAME="shmtu_auth_admin",
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    failures, lock, save_lock = deque(maxlen=10), threading.Lock(), threading.Lock()

    @app.after_request
    def secure_headers(response):
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        })
        return response

    @app.before_request
    def protect_api():
        if request.method == "POST":
            if not request.is_json:
                abort(415)
            if not isinstance(request.get_json(), dict):
                abort(400)
            origin = request.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in ("http", "https") or parsed.netloc != request.host:
                    abort(403)
        if request.path.startswith("/api/") and request.path not in ("/api/login", "/api/session"):
            if not session.get("authenticated"):
                abort(401)
            if request.method == "POST" and not hmac.compare_digest(
                request.headers.get("X-CSRF-Token", "").encode(), session.get("csrf", "missing").encode()
            ):
                abort(403)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/session")
    def session_info():
        return jsonify(authenticated=bool(session.get("authenticated")), csrf=session.get("csrf", ""))

    @app.post("/api/login")
    def login():
        password = request.get_json().get("password", "")
        if not isinstance(password, str) or len(password) > 512:
            abort(400)
        # 限制密码哈希计算频率，也避免暴力尝试管理密码。
        with lock:
            now = time.monotonic()
            while failures and now - failures[0] >= 60:
                failures.popleft()
            if len(failures) >= 10:
                return jsonify(error="尝试过于频繁，请一分钟后再试"), 429
            failures.append(now)
        if not check_password_hash(admin["password_hash"], password):
            return jsonify(error="管理密码不正确"), 401
        session.clear()
        session.permanent = True
        session.update(authenticated=True, csrf=secrets.token_hex(24))
        return jsonify(csrf=session["csrf"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/password")
    def change_password():
        data = request.get_json()
        current, password = data.get("current", ""), data.get("password", "")
        if not isinstance(current, str) or not isinstance(password, str) or not 12 <= len(password) <= 512 or len(current) > 512:
            return jsonify(error="新管理密码需要 12 至 512 个字符"), 400
        if not check_password_hash(admin["password_hash"], current):
            return jsonify(error="当前管理密码不正确"), 400
        updated = {"password_hash": generate_password_hash(password), "secret": secrets.token_hex(32)}
        try:
            with save_lock:
                write_private_json(settings_path().parent / "web-admin.json", updated)
                admin.update(updated)
                app.secret_key = updated["secret"]
        except OSError:
            return jsonify(error="管理密码保存失败，请检查 data 目录权限"), 500
        session.clear()
        session.permanent = True
        session.update(authenticated=True, csrf=secrets.token_hex(24))
        return jsonify(csrf=session["csrf"])

    @app.get("/api/status")
    def status():
        return jsonify(state.snapshot())

    @app.get("/api/accounts")
    def accounts():
        # 从不把密码或其长度回传到浏览器。
        return jsonify(users=[{"id": user, "has_password": True} for user, _, _ in get_user_list()])

    @app.post("/api/accounts")
    def update_accounts():
        try:
            with save_lock:
                save_accounts(request.get_json().get("users"), get_user_list())
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except OSError:
            return jsonify(error="配置保存失败，请检查 data 目录权限"), 500
        state.wake.set()
        return jsonify(ok=True)

    return app


def start():
    from waitress import create_server
    from shmtu_auth.src.monitor import auth_status
    state = RuntimeState()
    app = create_app(state)
    host = os.environ.get("SHMTU_AUTH_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("SHMTU_AUTH_WEB_PORT", "18888"))
    server = create_server(app, host=host, port=port, threads=4,
                           max_request_body_size=64 * 1024, channel_timeout=30)
    worker = threading.Thread(target=server.run, name="account-web", daemon=True)
    worker.start()
    print(f"账号设置网页已启动：请访问 http://宿主机IP:{port}", flush=True)
    try:
        auth_status.monitor_auth(wait_for_users=True, on_status=state.update, wake_event=state.wake)
    finally:
        server.close()
        server.task_dispatcher.shutdown()
