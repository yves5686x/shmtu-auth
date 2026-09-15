"""真实子进程覆盖 CLI 返回后解释器关闭线程池的回归。"""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("fail_probe", [False, True])
def test_cli_owns_monitor_lifetime(tmp_path, fail_probe):
    root = Path(__file__).resolve().parents[1]
    script = '''
import runpy
import time
from shmtu_auth.src.monitor import auth_status
from shmtu_auth.src.core import get_query_string_requests as probes

probes._dns_resolve_one = lambda host, timeout: True
auth_status.get_user_list = lambda: [("123", "test-password", False)]
rounds = 0

class FakeAuth:
    def check_is_online(self):
        # 给旧实现的主线程充分时间返回，触发解释器关闭。
        time.sleep(0.1)
        if FAIL_PROBE:
            raise RuntimeError("synthetic probe failure")
        assert probes.probe_dns(["first.test", "second.test"]) == {
            "first.test": True, "second.test": True,
        }
        return False

    def login_by_list(self, users):
        assert users == [("123", "test-password", False)]
        return True

def end_round(interval):
    global rounds
    rounds += 1
    print("ROUND_COMPLETED", flush=True)
    if rounds == 2:
        raise SystemExit(0)

auth_status.ShmtuNetAuth = FakeAuth
auth_status.time_sleep = end_round
runpy.run_module("shmtu_auth", run_name="__main__")
'''.replace("FAIL_PROBE", repr(fail_probe))
    env = {key: value for key, value in os.environ.items() if not key.startswith("SHMTU_")}
    env.update(PYTHONPATH=str(root / "src"), DOCKER_MODE="1")
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert "interpreter shutdown" not in result.stderr
    if fail_probe:
        assert result.returncode != 0, result.stderr
        assert "synthetic probe failure" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("ROUND_COMPLETED") == 2, result.stderr
