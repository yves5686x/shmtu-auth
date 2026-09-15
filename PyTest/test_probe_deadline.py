"""慢 DNS / 响应读取不能突破整轮预算，未完成任务不能无限增加。"""
import threading
import time
import socket

import pytest

from shmtu_auth.src.core import get_query_string_requests as probes
from shmtu_auth.src.monitor import auth_status


@pytest.mark.parametrize("stage", ["dns", "request", "body"])
def test_stalled_probe_returns_on_deadline_and_bounds_workers(monkeypatch, stage):
    gate = threading.Event()
    entered = threading.Event()
    finished = threading.Event()
    calls = []
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(probes, "_PROBE_SLOTS", slots)

    def stall():
        calls.append(stage)
        entered.set()
        gate.wait(3)

    class Response:
        encoding = "utf-8"
        status_code = 200
        url = "http://test.invalid"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_content(self, **kwargs):
            if stage == "body":
                stall()
            yield b"ok"

    class Session:
        def get(self, *args, **kwargs):
            if stage == "request":
                stall()
            return Response()
        def close(self):
            finished.set()

    def dns(*args):
        if stage == "dns":
            stall()
        return True

    monkeypatch.setattr(probes, "_dns_resolve_one", dns)
    monkeypatch.setattr(probes.requests, "Session", Session)
    try:
        start = time.monotonic()
        result = probes.probe_many(["http://test.invalid"], total_timeout=0.1)
        assert entered.is_set()
        assert time.monotonic() - start < 0.5
        assert result == [probes.ProbeResult("http://test.invalid")]
        # 上一轮仍阻塞时不再启动新任务，不会每隔几秒积累一个线程。
        for _ in range(5):
            assert probes.probe_many(["http://test.invalid"], total_timeout=0.02)[0].status == 0
        assert len(calls) == 1
    finally:
        gate.set()
        # 等到旧任务释放容量，确保结束后的正常轮次可以恢复。
        assert slots.acquire(timeout=2)
        slots.release()
    assert probes.probe_many(["http://test.invalid"], total_timeout=0.5)[0].status == 200


def test_cli_attempts_login_after_one_failed_probe(monkeypatch):
    calls = []
    users = [("123", "synthetic-password", False)]
    class StopTest(Exception):
        pass
    class Auth:
        def check_is_online(self):
            pytest.fail("CLI must not enter the three-attempt retry wrapper")
        def login_by_list(self, actual):
            assert actual == users
            calls.append("login")
            raise StopTest
    monkeypatch.setattr(auth_status, "ShmtuNetAuth", Auth)
    monkeypatch.setattr(auth_status, "check_is_connected", lambda: calls.append("probe") or False)
    monkeypatch.setattr(auth_status, "time_sleep", lambda seconds: pytest.fail("must login before sleeping"))
    with pytest.raises(StopTest):
        auth_status.monitor_auth(users)
    assert calls == ["probe", "login"]


def test_requests_dns_failure_does_not_retry_internally(monkeypatch):
    calls = []
    def fail_dns(*args, **kwargs):
        calls.append(1)
        raise socket.gaierror("synthetic DNS failure")
    monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
    # 使用真正的 Requests/urllib3 错误链；Max retries exceeded 也可能只请求一次。
    result = probes.probe_many(["http://test.invalid"], dns_precheck=False, total_timeout=0.5)
    assert result[0].status == 0
    assert calls == [1]
