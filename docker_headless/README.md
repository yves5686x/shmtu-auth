# SHMTU Auth Headless

这个目录是从原项目里拆出来的无 GUI 版本，目标就是在 Docker 里稳定跑认证轮询。

## 特点

- 不依赖 GUI
- 不依赖 `loguru`、`toml`、`PyQt`
- 只保留认证核心逻辑和环境变量配置
- 日志输出到标准输出，适合 Docker 查看
- 内置验证码 OCR，可全自动完成「带验证码 + RSA 加密密码」的新版门户登录

## 关于验证码（重要）

门户登录页已经改版，**每次登录都强制要求 4 位图形验证码**，并且：

- 验证码是**一次性**的，复用同一张图的识别结果会被服务端拒绝
- 密码不再是明文提交，而是用门户下发的 RSA 公钥加密后提交

无头环境没有弹窗，所以本目录走的是**纯 OCR 自动识别**：
`ddddocr`（首选）→ `pytesseract`（备选）。

`ddddocr` 依赖 `onnxruntime`，而 onnxruntime 只提供 glibc（manylinux）wheel，
因此基础镜像使用 `python:3.12-slim`（Debian），**不再使用 alpine**。
代价是镜像从 ~35MB 增长到 ~300MB+，换来的是开箱即用。

如果某些图片 OCR 识别不出来，程序会**换一张新图继续试**（服务端每取一次就换一张），
可用 `SHMTU_AUTH_CAPTCHA_MAX_RETRY` 控制单次登录内的重试次数，默认 6。
容器里没有弹窗兜底，所以这个值不建议调小。

## 快速开始

### Linux/macOS
```bash
./start.sh start    # 启动
./start.sh stop     # 停止
./start.sh logs     # 查看日志
./start.sh status   # 查看状态
```

### Windows (PowerShell)
```powershell
.\start.ps1 start    # 启动
.\start.ps1 stop     # 停止
.\start.ps1 logs     # 查看日志
.\start.ps1 status   # 查看状态
```

## 环境变量

编辑 `docker-compose.yml` 配置：

```yaml
environment:
  - SHMTU_AUTH_USER_LIST=your_student_id        # 学号（多个用;分隔）
  - SHMTU_AUTH_USER_PWD_your_student_id=your_password  # 密码
  - SHMTU_AUTH_CHECK_INTERVAL=60                # 检测间隔（秒）
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SHMTU_AUTH_USER_LIST` | 学号列表，多个用 `;` 分隔 | - |
| `SHMTU_AUTH_USER_PWD_<学号>` | 对应学号的密码 | - |
| `SHMTU_AUTH_CHECK_INTERVAL` | 轮询间隔秒数 | `60` |
| `SHMTU_AUTH_RUN_ONCE` | 只执行一次检查 | `false` |
| `SHMTU_AUTH_PROBE_URL` | 自定义探测URL | `http://1.1.1.1` |
| `SHMTU_AUTH_USER_AGENT` | 自定义UA | - |
| `SHMTU_AUTH_LOGIN_URL` | 门户地址（覆盖内置默认） | `https://ismu.shmtu.edu.cn:8443/eportal/` |
| `SHMTU_AUTH_PORTAL_SERVICE` | 强制指定 `service`，不填则依次尝试「校园网」和「iSMU」 | 依次尝试 |
| `SHMTU_AUTH_CAPTCHA_OCR` | 是否启用验证码 OCR | `true` |
| `SHMTU_AUTH_CAPTCHA_MAX_RETRY` | 单次登录内验证码重试次数 | `6` |

### service 参数是怎么定的

门户的 `service` 来自登录页的下拉框，由服务端下发，只有两个取值：

| 接入方式 | 提交值 |
|---|---|
| 有线（网口） | `%E6%A0%A1%E5%9B%AD%E7%BD%91`（即「校园网」） |
| 无线 i-SHMU | `iSMU` |

程序**不再根据当前网络类型二选一**，而是**两个都试**：先用第一个发一次登录，
失败就换第二个。原因是容器里的网络类型判断经常不准（拿到的可能是宿主机或
上一跳的类型），而选错的代价只是多一轮请求，让有线 / 无线 / 判断错误的场景
全部能自愈。

- 唯一例外：显式设置了 `SHMTU_AUTH_PORTAL_SERVICE` 就**只试它**，方便排查问题。
- 本进程上一次成功过的那个会被优先尝试（仅内存记忆，重启即失效），常驻的容器
  从第二轮开始就能一次命中。

> 提交值的字面量不能随便写：WLAN 认证里的 `service` 会被表单再编码一层，
> 所以有线的正确字面量是 `%E6%A0%A1%E5%9B%AD%E7%BD%91` 而不是「校园网」。

## 本地运行（不使用 Docker）

需要 Python 3.10+。

```bash
pip install -r requirements.txt
python -m app.main
```

## Docker 手动操作

```bash
# 构建并启动
docker compose up --build -d

# 查看日志
docker compose logs -f

# 停止
docker compose down

# 重启
docker compose restart
```

## 镜像信息

- 基础镜像: `python:3.12-slim`（Debian，glibc —— onnxruntime 需要）
- 构建方式: 多阶段构建
- 镜像大小: ~300MB（主要为 onnxruntime + opencv）
- 常驻内存: 约 200MB（compose 里限制为 512M）

## 目录结构

```
docker_headless/
├── app/
│   ├── __init__.py
│   ├── auth_core.py         # 认证主逻辑（门户主流程 + H3C 兜底）
│   ├── captcha_solver.py    # 验证码 OCR（ddddocr / pytesseract）
│   ├── config.py            # 环境变量读取
│   ├── eportal_protocol.py  # 门户协议（pageInfo / validcode / getServices / login）
│   ├── main.py              # 轮询入口
│   └── portal_crypto.py     # 门户 RSA 密码加密（纯 Python）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── start.sh
└── start.ps1
```

> `portal_crypto.py` 与主包 `src/shmtu_auth/src/core/portal_crypto.py` 保持**逐字节一致**，
> 由 `PyTest/test_portal_crypto_parity.py` 守护；改动任一份时两份都要同步。
