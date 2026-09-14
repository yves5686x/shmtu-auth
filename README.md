# 上海海事大学-校园网-自动认证

<!-- markdownlint-disable html -->

<div align="center" style="text-align: center; ">

<img

    src="Assets/Images/Logo/Logo128.png" 
    alt="Logo"

/>

<div class="badges">

<img

    src="https://img.shields.io/badge/Python-3.10%2B-brightgreen"
    alt=""

/>

[![License](https://img.shields.io/github/license/a645162/shmtu-auth?label=license&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiBmaWxsPSIjZmZmZmZmIj48cGF0aCBmaWxsLXJ1bGU9ImV2ZW5vZGQiIGQ9Ik0xMi43NSAyLjc1YS43NS43NSAwIDAwLTEuNSAwVjQuNUg5LjI3NmExLjc1IDEuNzUgMCAwMC0uOTg1LjMwM0w2LjU5NiA1Ljk1N0EuMjUuMjUgMCAwMTYuNDU1IDZIMi4zNTNhLjc1Ljc1IDAgMTAwIDEuNUgzLjkzTC41NjMgMTUuMThhLjc2Mi43NjIgMCAwMC4yMS44OGMuMDguMDY0LjE2MS4xMjUuMzA5LjIyMS4xODYuMTIxLjQ1Mi4yNzguNzkyLjQzMy42OC4zMTEgMS42NjIuNjIgMi44NzYuNjJhNi45MTkgNi45MTkgMCAwMDIuODc2LS42MmMuMzQtLjE1NS42MDYtLjMxMi43OTItLjQzMy4xNS0uMDk3LjIzLS4xNTguMzEtLjIyM2EuNzUuNzUgMCAwMC4yMDktLjg3OEw1LjU2OSA3LjVoLjg4NmMuMzUxIDAgLjY5NC0uMTA2Ljk4NC0uMzAzbDEuNjk2LTEuMTU0QS4yNS4yNSAwIDAxOS4yNzUgNmgxLjk3NXYxNC41SDYuNzYzYS43NS43NSAwIDAwMCAxLjVoMTAuNDc0YS43NS43NSAwIDAwMC0xLjVIMTIuNzVWNmgxLjk3NGMuMDUgMCAuMS4wMTUuMTQuMDQzbDEuNjk3IDEuMTU0Yy4yOS4xOTcuNjMzLjMwMy45ODQuMzAzaC44ODZsLTMuMzY4IDcuNjhhLjc1Ljc1IDAgMDAuMjMuODk2Yy4wMTIuMDA5IDAgMCAuMDAyIDBhMy4xNTQgMy4xNTQgMCAwMC4zMS4yMDZjLjE4NS4xMTIuNDUuMjU2Ljc5LjRhNy4zNDMgNy4zNDMgMCAwMDIuODU1LjU2OCA3LjM0MyA3LjM0MyAwIDAwMi44NTYtLjU2OWMuMzM4LS4xNDMuNjA0LS4yODcuNzktLjM5OWEzLjUgMy41IDAgMDAuMzEtLjIwNi43NS43NSAwIDAwLjIzLS44OTZMMjAuMDcgNy41aDEuNTc4YS43NS43NSAwIDAwMC0xLjVoLTQuMTAyYS4yNS4yNSAwIDAxLS4xNC0uMDQzbC0xLjY5Ny0xLjE1NGExLjc1IDEuNzUgMCAwMC0uOTg0LS4zMDNIMTIuNzVWMi43NXpNMi4xOTMgMTUuMTk4YTUuNDE4IDUuNDE4IDAgMDAyLjU1Ny42MzUgNS40MTggNS40MTggMCAwMDIuNTU3LS42MzVMNC43NSA5LjM2OGwtMi41NTcgNS44M3ptMTQuNTEtLjAyNGMuMDgyLjA0LjE3NC4wODMuMjc1LjEyNi41My4yMjMgMS4zMDUuNDUgMi4yNzIuNDVhNS44NDYgNS44NDYgMCAwMDIuNTQ3LS41NzZMMTkuMjUgOS4zNjdsLTIuNTQ3IDUuODA3eiI+PC9wYXRoPjwvc3ZnPgo=)](#license)

</div>

<div class="build-status">

</div>

</div>

![GUI](Assets/Images/QQ20240423002922.png)

上海海事大学校园网（H3C eportal 门户）自动认证工具。

浏览器上点「登录」做的事，它用脚本全流程复现：探测并抓取认证页 → 取图形验证码 →
OCR 识别 → RSA 加密密码 → 提交 → 轮询保持在线。适用于有线（网口）与无线（iSMU）接入。

> 在线文档：<https://a645162.github.io/shmtu-auth/>

## 支持平台

命令行、Docker、GUI 三种形态都同时支持 Windows / macOS / Linux。

**Python 3.10+**。`ddddocr` 依赖的 `onnxruntime` 不再提供更老版本的 wheel，
所以不要用 3.8 / 3.9。唯一的额外限制：**macOS x64 上装 GUI 需要 Python ≤ 3.11**（PySide6 限制）。

## Features

* [x] 自动认证 + 轮询保持在线
* [x] 自动识别图形验证码（OCR），识别不出时 GUI 会弹窗手输
* [x] 密码按门户 JS 的算法 RSA 加密提交（逐字节对齐，非标准填充）
* [x] 账号密码可从自建凭据服务获取（用物理网卡 MAC 当设备号）
* [x] Docker 无头部署
* [x] GUI：管理账号、查看日志、一键导出多机 Docker 配置
* [x] 完整日志与 WebHook 通知

## 快速开始

四条路，**按场景选一条就行**，彼此独立。

### 方式一：Docker（服务器 / 需要常驻，最省事）

```bash
cd docker_headless
cp .env.example .env      # 然后编辑 .env，填学号密码
./start.sh start          # Windows: .\start.ps1 start
```

`start.sh` / `start.ps1` 封装了 `docker compose` 的常用操作：

```bash
./start.sh start     # 构建并后台启动
./start.sh logs      # 跟日志
./start.sh status    # 看状态
./start.sh stop      # 停止
```

> ⚠️ **必须用 host 网络**（compose 里已配好）。容器要看到宿主机的物理网卡，
> 否则取不到设备号，也拿不到门户的设备标识。
>
> ⚠️ Docker 的配置**不用 `config.toml`**，改 `.env` 即可。注意有**几个变量名跟本地不一样**，
> 最容易踩的是轮询间隔：本地叫 `SHMTU_AUTH_TIME_INTERVAL`，Docker 叫 `SHMTU_AUTH_CHECK_INTERVAL`，
> 写混了不报错、只会静默用默认值。完整变量表见
> [docker_headless/README.md](docker_headless/README.md)。

### 方式二：命令行（pip / 源码）

```bash
# 1) 装依赖
pip install -r requirements.txt     # 或者在仓库根执行 pip install -e .

# 2) 把配置模板复制出来再改（这一步不能省，见下面「配置」）
cp src/shmtu_auth/config/config.toml ./config.toml
# 然后编辑 ./config.toml，填学号密码

# 3) 运行
python start_cli.py
```

装过 `pip install -e .` 之后，也可以直接用命令名：

```bash
shmtu-auth
```

命令行模式**没有弹窗兜底**，所以 `ddddocr` 是必需的（`requirements.txt` 已含）。
验证码识别失败会自动换一张新图重试。

### 方式三：GUI 桌面版

GUI 依赖（PySide6 / QFluentWidgets）不在默认安装里，要单独装：

```bash
pip install -e ".[gui]"
python -m shmtu_auth.main_gui
```

没有 `pip install -e .` 的话，加一句 `PYTHONPATH=src` 再跑上面第二条命令。

GUI 里可以增删账号、看日志、手动切换认证状态。账号数据存在
`data/user_list.pickle`，**不读** `config.toml` 的 `[User]` 段；但 `[Credential]`
（自建凭据服务）和 `[Portal]` 等段照样生效。

> 注意：`start_cli.py` 和 `shmtu-auth` 命令走的都是**命令行**模式，不会启动 GUI。

### 方式四：uv（开发 / 想改代码）

```bash
./setup_uv.sh          # Windows: .\setup_uv.ps1
uv run python start_cli.py

# 跑测试
uv run pytest PyTest/
```

### 方式五：预编译可执行文件

不想装 Python 的话，从 [Releases](https://github.com/a645162/shmtu-auth/releases)
下载对应平台的压缩包，解压后把 `config.toml` 放在可执行文件同目录再运行。
参数用法与命令行版一致（如 `-t /path/to/config.toml`）。

## 配置

### 配置文件放在哪（最容易踩的一步）

程序按下面的顺序找配置文件，路径都相对**当前工作目录**：

1. `-t` / `--toml` 显式指定的路径（给了就**只**读它）
2. `./config.toml`
3. `./config/config.toml`

都找不到就用环境变量和内置默认值。

> ⚠️ **仓库里那份 `src/shmtu_auth/config/config.toml` 只是模板，程序不会自动读它。**
> 直接改它、然后从仓库根跑 `python start_cli.py`，你会发现改动**完全不生效**
> —— 程序只会打印一行 `Toml config not found!`，然后全用默认值。
>
> 正确做法是先复制到工作目录：

```bash
cp src/shmtu_auth/config/config.toml ./config.toml
```

然后编辑 `./config.toml`。优先级是 **`config.toml` > 环境变量** ——
TOML 里写了值，同名环境变量就失效；想临时用环境变量覆盖，把那一行注释掉即可。

所有配置项的说明都写在模板文件的注释里，每一项都有。

### 本地账号（默认方式）

```toml
[User]
SHMTU_AUTH_USER_LIST = "202500000000;202500000001"   # 学号，多个用分号分隔
SHMTU_AUTH_USER_PWD_202500000000 = "密码1"
SHMTU_AUTH_USER_PWD_202500000001 = "密码2"
# 密码本身已是密文时才需要（值填 1）
#SHMTU_AUTH_USER_PWD_ENCRYPT_202500000000 = "1"
```

### 自建凭据服务（可选，多设备场景）

配好地址后，程序会用**本机物理网卡 MAC** 当设备号去换账号密码：
换到了就用服务端的（并自动带上门户接入类型），换不到自动回退到上面的本地账号。

`[Credential]` 段默认整段注释掉，留空即表示不使用。

```toml
[Credential]
SHMTU_AUTH_CREDENTIAL_URL = "https://your-server/device/{mac}/credential"
SHMTU_AUTH_CREDENTIAL_TOKEN = "你的令牌"
```

接口约定：`GET <地址>?mac=<12位小写MAC>`，响应

```json
{"users": [{"id": "202500000000", "password": "xxx"}], "service": "校园网", "machine": "实验室服务器"}
```

密码是明文过网络的，**必须 HTTPS + token，不要暴露公网**。

命令行、Docker、GUI 三条路都认这份配置：CLI 和 Docker 在启动/轮询时去换，
GUI 在每次点「开始认证」前换一次（拿不到就原样用界面里填的账号）。

> ⚠️ 这里的设备号要的是**物理网卡 MAC**（如 `00:e0:1a:00:23:a9`），
> 不是门户 URL 里那个加密过的 `mac` 串（如 `67d1ff70…`），两者完全不同。
> 容器里需要 `network_mode: host` 才能看到宿主的物理网卡。

### 可选配置项

* `SHMTU_MACHINE_NAME`: 服务器名称
* `SHMTU_AUTH_TIME_INTERVAL`: 认证状态检测时间间隔（秒，默认 10）
* `SHMTU_AUTH_PORTAL_SERVICE`: 门户接入类型，不填则依次尝试「校园网」（有线）和「iSMU」（无线）
* `SHMTU_AUTH_CAPTCHA_OCR`: 是否启用验证码自动识别（默认 true）
* `SHMTU_AUTH_CAPTCHA_MAX_RETRY`: 单次登录内验证码重试次数（默认 6）
* `SHMTU_AUTH_QUERY_STRING`: 手动指定 queryString（见下方排查一节）

<!-- - `SHMTU_AUTH_WEBHOOK_WEWORK` : 企业微信机器人WebHook -->
<!-- - `SHMTU_WEBHOOK_SLEEP_TIME_START` : WebHook免打扰-开始时间 -->
<!-- - `SHMTU_WEBHOOK_SLEEP_TIME_END` : WebHook免打扰-结束时间 -->

### 关于验证码

门户登录已强制 4 位图形验证码，且密码用 RSA 加密后提交。默认用 OCR 自动识别
（`ddddocr`），识别不出来就换一张新图重试。

能不能缺 `ddddocr`，取决于有没有界面兜底：

* **GUI**：没装也能用，OCR 识别不出来会弹窗请你手输
* **命令行 / Docker**：没有弹窗兜底，**必须装 `ddddocr`**，否则遇到验证码会直接登录失败

GUI 的依赖里已经带上它了（`pip install -e ".[gui]"`）；命令行请自行
`pip install ddddocr`。

## 认证流程

程序做的事和浏览器点「登录」完全一致，只是全程脚本化（2026-09 门户改版后的现状）：

```
① 探测 http 明文请求 → 被网关劫持到认证页，取出 queryString（含 mac）
② GET  index.jsp?<原始形态 queryString>      建立会话（绑定 wlanuserip / mac）
③ POST InterFace.do?method=pageInfo         取验证码地址 + RSA 公钥
④ GET  InterFace.do?method=getServices      绑定本会话可用服务
⑤ GET  validcode?rnd=…  → 识别 4 位数字      用 ddddocr；失败换新图重试
⑥ POST InterFace.do?method=login            带验证码 + RSA 密文密码
```

几个必须知道的点：

* **`mac` 由网关下发**：程序只把 queryString 原样透传，不读网卡 MAC、也不缓存上次的值
* **密码密文的明文是 `reverse(密码 + ">" + mac)`**，与门户 JS 逐字节一致
* **`service` 两个候选都试**（有线 `校园网` / 无线 `iSMU`），不按运行环境猜；
  可用 `SHMTU_AUTH_PORTAL_SERVICE` 显式指定
* 验证码**一次性**，失败自动换新图重试（`SHMTU_AUTH_CAPTCHA_MAX_RETRY`，默认 6 次）
* 认证状态由轮询监控（`SHMTU_AUTH_TIME_INTERVAL`，默认 10 秒）

> 想单独验证「账号密码能不能过」，用仓库根目录的独立脚本（不依赖 GUI，
> 复用主程序同一套登录逻辑）：
>
> ```bash
> python test_auth_cli.py            # 交互式输入学号密码，直接认证
> python test_auth_cli.py --manual   # 不用 OCR，每轮验证码弹图人工输入
> ```

## 认证不上怎么排查

先跑诊断脚本（只依赖 `requests`，可单独拷到出问题的机器上）：

```bash
python diagnose_portal.py
```

它会打出本机出口 IP、是否设了系统代理、**DNS 解析结果**、每个探测地址的状态码
与**响应体摘要**、抓到的 queryString，最后给出结论。全程并发，约 3 秒跑完。

**报「Query String is Invalid」时**

这句话是**我们自己的代码**说的，不是门户返回的，意思是没抓到 queryString，
跟账号密码无关。程序靠「网关劫持 http 明文请求」来拿它，常见原因：

* **机器设了代理** —— 请求走了代理就绕过了网关，劫持根本不会发生（头号原因）
* **DNS 不可用** —— 域名解析不出 IP 时**不可能**被劫持到认证页（劫持的前提就是
  先解析出 IP）。这时要查网卡 DNS 是否设成自动获取、是否连对了网络
* 探测地址恰好被网关放行
* 不在校园网内，或接了手机热点 / 其他 WiFi

**登录报「用户不存在或者密码错误！」**

门户把好几种情况都归到这一句话上，**先别急着改密码**。已知有两个与账号密码本身
无关的成因（都在 2026-09 门户改版后修过）：

* **queryString 里的 `mac` 没取到**。门户表单要求把 queryString 编码后提交，
  探测抓回来的多半是 `wlanuserip%3D...%26mac%3D...` 这种形态，直接 `parse_qs`
  一个字段都解析不出来。取不到 `mac` 时程序会拿门户默认值 `111111111` 参与密码
  加密，门户解不开，回的正是这句话。日志里会有 `queryString 中没有 mac 参数` 的告警。
* **密码加密的明文漏了反转**。门户 JS 的明文是 `reverse(password + ">" + mac)`，
  漏掉反转时门户解出来是反的密码，症状同样是这句话。

想单独验证「账号密码本身对不对」，用 `python test_auth_cli.py`；
`--manual` 可以把 OCR 这个变量摘出去，纯粹验证账号密码。

**「明明上不了网却显示已联网」**

这个误判已经修掉了，但了解一下原因有助于判断：某些网络里透明代理 / 缓存 /
DNS 劫持会对**所有 http 请求**返回 200，内容却根本不是你要的网站 —— 只凭状态码
判断就会被骗。诊断脚本会把响应体打印出来，内容空白或不是该网站，一眼就能看穿。

判据上要求「证明外网真的可达」：校园网只劫持 http、不碰 https，所以
**http 全 200 但 https 全部连不上**是一眼假的组合。

**手动指定 queryString（终极兜底）**

在本机浏览器打开一个 **http** 网址（不要 https），等它跳到认证页后，把地址栏
里那一整串 URL 粘给诊断脚本：

```bash
python diagnose_portal.py --url "<粘贴的完整URL>"
```

脚本会校验字段是否齐全（缺 `mac` 会导致门户解不开密码），并生成一行可直接用的
配置。然后写进 `config.toml` 的 `[Portal]` 段：

```toml
SHMTU_AUTH_QUERY_STRING = "<那一整串URL>"
```

配了它程序会跳过自动探测。注意 queryString 由网关现生成、**绑定本机当前 IP**，
换机器或重连网络后会失效，只适合临时救急或固定环境。

## 项目结构

```
.
├── start_cli.py                     # 命令行入口（从仓库根运行）
├── test_auth_cli.py                 # 独立认证测试脚本（不依赖 GUI，用于排查）
├── diagnose_portal.py               # 网络 / 门户探测诊断脚本
├── config.toml                      # ← 你的配置（从下面的模板复制，未入库）
├── src/shmtu_auth/
│   ├── config/config.toml           # 配置模板（含全部注释说明），不是运行时读取的那份
│   ├── main_start.py                # CLI 入口函数
│   ├── main_gui.py                  # GUI 入口
│   └── src/
│       ├── core/                    # 认证核心：门户协议、RSA 加密、验证码、queryString 探测
│       ├── gui/                     # GUI（PySide6 + QFluentWidgets）
│       ├── monitor/                 # 轮询与状态监测
│       └── utils/                   # 日志、环境变量、配置读取
├── docker_headless/                 # 独立无头版（Docker），不 import 主包
│   ├── app/                         # 与主包 core/ 部分文件保持逐字节一致（有测试守护）
│   └── .env.example                 # ← Docker 的唯一配置文件
├── Docker/                          # 上游的 Docker 构建（挂载 config.toml 跑主包 CLI）
├── PyTest/                          # 测试套件
└── Document/                        # VitePress 文档站源码
```

> `docker_headless/` 是**独立副本**，只依赖 `urllib3<2` + `requests` + 标准库，
> 不 import 主包。其中 `portal_crypto.py` / `device_id.py` / `credential_provider.py`
> 与主包同名文件保持**逐字节一致**，由 `PyTest/test_portal_crypto_parity.py` 等守护；
> 改动任一份时两份都要同步。

## 开发指南

推荐使用 `uv` 进行包管理，也可以使用 `Anaconda` / `Miniconda` 创建虚拟环境。

跑测试：

```bash
# uv
uv run pytest PyTest/

# 或者直接调用 pytest
PYTHONPATH=src pytest PyTest/ src/shmtu_auth/src/core/test_core_login_fallback.py
```

> `PyTest/test_204.py` 需要能访问 google.com，内网环境下会失败，与业务改动无关。

代码风格用 `flake8` + `black` + `isort`，`Makefile` 里有对应的快捷命令
（`make lint` / `make format` / `make test`）。

## License

本程序使用[GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html)协议开源。

GPLv3协议是我非常喜欢的一个协议，我的大部分程序均基于GPLv3协议开源。

此外，本程序使用到的QFluentWidgets库恰好也是基于GPLv3协议开源的。
