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

## 配置（只需要改一个文件）

```bash
cp .env.example .env    # 然后编辑 .env
```

所有配置都写在 `docker_headless/.env` 里，`docker-compose.yml` 通过 `env_file` 读它。
（早先是把变量直接写进 compose，现已迁出，避免同一份配置散在多处。）

### 账号密码从哪来（二选一）

**方式一：本地填写**（默认）

```env
SHMTU_AUTH_USER_LIST=202500000000                     # 多个学号用 ; 分隔
SHMTU_AUTH_USER_PWD_202500000000=你的密码
```

**方式二：自建凭据服务**（多设备场景推荐）

配好地址后，容器会拿**本机物理网卡 MAC** 当设备号去换账号密码；
换到了就用服务端的，换不到自动回退到本地的 `SHMTU_AUTH_USER_LIST`。

```env
SHMTU_AUTH_CREDENTIAL_URL=https://your-server/device/{mac}/credential
SHMTU_AUTH_CREDENTIAL_TOKEN=你的令牌
```

接口约定（自己搭的服务按这个实现）：`GET <地址>?mac=<12位小写MAC>`，返回

```json
{
  "users": [{"id": "202500000000", "password": "xxx"}],
  "service": "校园网",
  "machine": "实验室服务器",
  "ttl": 3600
}
```

拿到之后：`service` 会定死门户接入类型（省掉「两个都试」那轮），`machine` 写进日志。
密码是明文过网络的，**务必 HTTPS + token，不要暴露到公网**。

### 设备号（物理 MAC）是怎么取的

先分清两个都叫 mac 但不是一回事的东西：

| | 是什么 | 用在哪 |
|---|---|---|
| **物理网卡 MAC** | 网卡上固化的地址，如 `00:e0:1a:00:23:a9` | 自建凭据服务的设备标识 |
| **门户 queryString 里的 mac** | 网关下发的加密串，如 `67d1ff70…` | 门户登录时原样透传 |

物理 MAC 的取值优先级：

1. `SHMTU_AUTH_DEVICE_MAC` 显式配置
2. 自动探测：读 sysfs，只认带 `device` 符号链接的网卡 ——
   `veth` / `docker0` / `br-*` 这些虚拟接口天然被排除

**容器必须用 `network_mode: host`**（compose 里已配好），
这样容器看到的就是宿主机的物理网卡。非 host 网络下容器里只剩 veth，
程序会**明确报错**让你配置 `SHMTU_AUTH_DEVICE_MAC`，而不是悄悄编一个假值。

同理，门户 `queryString` 里读不到 `mac` 时也会显式告警 ——
那个值参与密码加密，缺了会导致门户解不开密码，只报一句含糊的「认证失败」。

### 全部配置项

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SHMTU_AUTH_USER_LIST` | 学号列表，多个用 `;` 分隔 | - |
| `SHMTU_AUTH_USER_PWD_<学号>` | 对应学号的密码 | - |
| `SHMTU_AUTH_CREDENTIAL_URL` | 自建凭据服务地址，留空则不用 | - |
| `SHMTU_AUTH_CREDENTIAL_TOKEN` | 凭据服务鉴权令牌（`X-Auth-Token`） | - |
| `SHMTU_AUTH_CREDENTIAL_CACHE` | 凭据本地缓存路径 | `./data/credentials.json` |
| `SHMTU_AUTH_CREDENTIAL_INSECURE` | 关闭 TLS 校验（自签名证书时用） | `false` |
| `SHMTU_AUTH_DEVICE_MAC` | 手动指定设备号（物理 MAC） | 自动探测 |
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
│   ├── auth_core.py            # 认证主逻辑（门户主流程 + H3C 兜底）
│   ├── captcha_solver.py       # 验证码 OCR（ddddocr / pytesseract）
│   ├── config.py               # 环境变量读取 + 账号列表（含凭据服务）
│   ├── credential_provider.py  # 自建凭据服务客户端
│   ├── device_id.py            # 物理网卡 MAC 探测（设备号）
│   ├── eportal_protocol.py     # 门户协议（pageInfo / validcode / getServices / login）
│   ├── main.py                 # 轮询入口
│   └── portal_crypto.py        # 门户 RSA 密码加密（纯 Python）
├── .env.example                # ← 复制成 .env，唯一需要编辑的配置
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── start.sh
└── start.ps1
```

> `portal_crypto.py`、`device_id.py`、`credential_provider.py` 与主包
> `src/shmtu_auth/src/core/` 下的同名文件保持**逐字节一致**，分别由
> `PyTest/test_portal_crypto_parity.py`、`PyTest/test_device_id.py`、
> `PyTest/test_credential_provider.py` 守护；改动任一份时两份都要同步。
