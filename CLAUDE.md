# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

上海海事大学校园网自动认证工具 (SHMTU Campus Network Auto Authentication Tool). Automatically detects network authentication status and performs login when offline.

Dependencies are managed with **uv** (see `pyproject.toml`, `uv.lock`). Comments and log messages are largely in Chinese — match that style.

## Common Commands

```bash
# Install all dependencies (including dev)
uv sync                      # or: make dev-install

# Run the CLI monitor
uv run python start_cli.py   # or: make run; or: python -m shmtu_auth; console script: shmtu-auth

# Run the GUI (PySide6 + qfluentwidgets)
uv run python src/shmtu_auth/main_gui.py

# Run all tests
uv run pytest                # pytest.ini deliberately has NO testpaths — tests live all over the repo

# Run a single test file / test
uv run pytest PyTest/test_portal_detection.py
uv run pytest PyTest/test_portal_detection.py::test_name

# Lint
uv run flake8 src/           # config in .flake8
ruff check .                 # config in .ruff.toml

# Docker (headless deployment for servers/NAS; config via Docker/.env)
cd Docker && docker compose up --build -d
```

Important pins/constraints:
- `urllib3<2` is required (urllib3 2.x triggers a warning and is not compatible).
- `ddddocr` (OCR for portal captchas) is a main dependency; it pulls in onnxruntime.
- GUI deps (`PySide6`, `PySide6-Fluent-Widgets`) are an optional group: `uv sync --group gui`.

## Architecture

### Entry Points

1. **CLI** (main use case): `start_cli.py` / `python -m shmtu_auth` → `src/shmtu_auth/src/entry.py` → `monitor/auth_status.py:start_monitor_auth()` (runs the monitor loop in the **main thread** so the interpreter doesn't exit early).
2. **GUI**: `src/shmtu_auth/main_gui.py` → `gui/gui_main_application.py`. GUI deps optional.
3. **Docker**: `Docker/` runs the same CLI (host network mode — required so the MAC in the portal queryString matches). All config in `Docker/.env`.

### Authentication Flow (src/shmtu_auth/src/core/)

```
core.py              # ShmtuNetAuthCore - orchestrates the dual-portal strategy
eportal_protocol.py  # EPortalClient - eportal HTTP sequence (pageInfo/validcode/login)
portal_crypto.py     # RSA password encryption with MAC as part of the key material
captcha_solver.py    # Captcha OCR backends (ddddocr → pytesseract), 4-digit normalization
core_exp.py          # Connectivity check + portal queryString extraction (with TTL cache)
get_query_string_requests.py  # HTTP probes that trigger gateway redirect
shmtu_auth.py        # ShmtuNetAuth - iterates the configured user list
credential_provider.py  # Optional remote credential service (fetch accounts by device MAC)
device_id.py         # Device identity for the credential service
```

**Dual login strategy** (`core.py:login()`):
1. **Primary: eportal portal** (`ismu.shmtu.edu.cn:8443/eportal/`) via `EPortalClient`. The portal now **requires a graphical captcha**; full sequence: `index.jsp` (session) → `pageInfo` (captcha URL + RSA public key) → `validcode` (fetch image — server serves a **new image on every GET**) → OCR → `login` with `validcode` + RSA-encrypted password. The browser-like call order matters (`query_services`, `query_account_service` before login bind services to the session).
2. **Fallback: H3C portal** (`hwifi.shmtu.edu.cn/portalauth/login`, XSRF token + JSON POST) when eportal fails and the probe produced a portal URL.

**Captcha strategy**: OCR-first, human fallback via the `CaptchaProvider` callback (`(image: bytes) -> str | None`). OCR results that aren't exactly 4 digits are discarded as untrustworthy. Retries are capped by `SHMTU_AUTH_CAPTCHA_MAX_RETRY` (default 6 — high for headless environments that have no manual-input channel). Captcha-related failures are *not* worth switching portal/service for (`is_valid_code_error()` gates this).

**Service candidates**: the eportal `service` param (wired 校园网 vs wireless i-SHMU) is deliberately **both tried in order** rather than guessed — network-type detection is unreliable in containers/Docker. `SHMTU_AUTH_PORTAL_SERVICE` pins it to exactly one; the last-successful service is remembered in memory to reorder candidates.

**Connectivity confirmation**: when a portal response is ambiguous, `_confirm_login_success()` re-probes connectivity with `force=True` (bypasses the TTL cache — the cached pre-login "offline" verdict would make the check useless).

### Configuration Priority

All config reads go through `src/shmtu_auth/src/utils/env.py` (`get_env_str/int/time`). Resolution order:
1. Global config (`config/config_global.py`)
2. TOML file (`config.toml`, path via `-t` flag; also checked at `./config/config.toml`)
3. Environment variables

### Key Environment Variables

```bash
SHMTU_AUTH_USER_LIST=202540510004;202540510005  # Semicolon-separated user IDs
SHMTU_AUTH_USER_PWD_<学号>=password             # Password per user ID
SHMTU_AUTH_TIME_INTERVAL=60                     # Monitor check interval (seconds)
SHMTU_AUTH_CAPTCHA_MAX_RETRY=6                  # Captcha OCR retries per login attempt
SHMTU_AUTH_PORTAL_SERVICE=                      # Pin eportal service (empty = try both)
SHMTU_AUTH_USER_AGENT=                          # Override HTTP User-Agent
SHMTU_AUTH_CREDENTIAL_URL=                      # Remote credential service URL ({mac} placeholder)
SHMTU_AUTH_CREDENTIAL_TOKEN=                    # Auth token for the credential service
```

### Monitor Loop

`src/shmtu_auth/src/monitor/auth_status.py`: loops forever (interval `SHMTU_AUTH_TIME_INTERVAL`), checks connectivity, and on failure calls `ShmtuNetAuth.login_by_list()` for each configured user until one succeeds.

### GUI Notes (src/shmtu_auth/src/gui/)

Auth runs on a plain Python background thread (`feature/network_auth.py`), not a QThread. Cross-thread interactions use bridges (`common/captcha_bridge.py`, `common/credential_bridge.py`) with `BlockingQueuedConnection` so the core's synchronous callbacks (e.g. manual captcha input) can block the auth thread while the dialog runs on the GUI thread. Don't touch Qt widgets from the auth thread.

## Testing

- `pytest.ini` sets no `testpaths` on purpose — test files (`test_*.py` / `*_test.py`) are scattered across `PyTest/`, repo root, and even inside `src/` (e.g. `src/core/test_core_login_fallback.py`). Running bare `pytest` from the root picks them all up.
- `PyTest/SSL/` contains SSL/certificate handling experiments.
- Markers available: `slow`, `integration`, `unit`, `compatibility` (`--strict-markers` is on).
