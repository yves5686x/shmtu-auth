# 上海海事大学-校园网-自动认证

<!-- markdownlint-disable html -->

<div align="center" style="text-align: center; ">

<img 

    src="Assets/Images/Logo/Logo128.png" 
    alt="Logo"

/>

<div class="badges">

<!-- 

![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-brightgreen)

 -->
<img

    src="https://img.shields.io/badge/Python-3.8%2B-brightgreen"
    alt=""

/>

[![License](https://img.shields.io/github/license/a645162/shmtu-auth?label=license&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgd2lkdGg9IjI0IiBoZWlnaHQ9IjI0IiBmaWxsPSIjZmZmZmZmIj48cGF0aCBmaWxsLXJ1bGU9ImV2ZW5vZGQiIGQ9Ik0xMi43NSAyLjc1YS43NS43NSAwIDAwLTEuNSAwVjQuNUg5LjI3NmExLjc1IDEuNzUgMCAwMC0uOTg1LjMwM0w2LjU5NiA1Ljk1N0EuMjUuMjUgMCAwMTYuNDU1IDZIMi4zNTNhLjc1Ljc1IDAgMTAwIDEuNUgzLjkzTC41NjMgMTUuMThhLjc2Mi43NjIgMCAwMC4yMS44OGMuMDguMDY0LjE2MS4xMjUuMzA5LjIyMS4xODYuMTIxLjQ1Mi4yNzguNzkyLjQzMy42OC4zMTEgMS42NjIuNjIgMi44NzYuNjJhNi45MTkgNi45MTkgMCAwMDIuODc2LS42MmMuMzQtLjE1NS42MDYtLjMxMi43OTItLjQzMy4xNS0uMDk3LjIzLS4xNTguMzEtLjIyM2EuNzUuNzUgMCAwMC4yMDktLjg3OEw1LjU2OSA3LjVoLjg4NmMuMzUxIDAgLjY5NC0uMTA2Ljk4NC0uMzAzbDEuNjk2LTEuMTU0QS4yNS4yNSAwIDAxOS4yNzUgNmgxLjk3NXYxNC41SDYuNzYzYS43NS43NSAwIDAwMCAxLjVoMTAuNDc0YS43NS43NSAwIDAwMC0xLjVIMTIuNzVWNmgxLjk3NGMuMDUgMCAuMS4wMTUuMTQuMDQzbDEuNjk3IDEuMTU0Yy4yOS4xOTcuNjMzLjMwMy45ODQuMzAzaC44ODZsLTMuMzY4IDcuNjhhLjc1Ljc1IDAgMDAuMjMuODk2Yy4wMTIuMDA5IDAgMCAuMDAyIDBhMy4xNTQgMy4xNTQgMCAwMC4zMS4yMDZjLjE4NS4xMTIuNDUuMjU2Ljc5LjRhNy4zNDMgNy4zNDMgMCAwMDIuODU1LjU2OCA3LjM0MyA3LjM0MyAwIDAwMi44NTYtLjU2OWMuMzM4LS4xNDMuNjA0LS4yODcuNzktLjM5OWEzLjUgMy41IDAgMDAuMzEtLjIwNi43NS43NSAwIDAwLjIzLS44OTZMMjAuMDcgNy41aDEuNTc4YS43NS43NSAwIDAwMC0xLjVoLTQuMTAyYS4yNS4yNSAwIDAxLS4xNC0uMDQzbC0xLjY5Ny0xLjE1NGExLjc1IDEuNzUgMCAwMC0uOTg0LS4zMDNIMTIuNzVWMi43NXpNMi4xOTMgMTUuMTk4YTUuNDE4IDUuNDE4IDAgMDAyLjU1Ny42MzUgNS40MTggNS40MTggMCAwMDIuNTU3LS42MzVMNC43NSA5LjM2OGwtMi41NTcgNS44M3ptMTQuNTEtLjAyNGMuMDgyLjA0LjE3NC4wODMuMjc1LjEyNi41My4yMjMgMS4zMDUuNDUgMi4yNzIuNDVhNS44NDYgNS44NDYgMCAwMDIuNTQ3LS41NzZMMTkuMjUgOS4zNjdsLTIuNTQ3IDUuODA3eiI+PC9wYXRoPjwvc3ZnPgo=)](#license)

</div>

<div class="build-status">

</div>

</div>

![GUI](Assets/Images/QQ20240423002922.png)

## 使用文档

请参考
[使用文档](https://a645162.github.io/shmtu-auth/)

## 支持平台

* Windows命令行(exe、pip)
* macOS命令行(二进制文件、pip)
* Linux命令行(pip)
* Docker镜像

GUI Coming Soon...

## Features

* [x] 自动认证
* [x] 程序记录日志

## 使用方法

### 使用 UV 包管理器 (推荐)

本项目现已支持 [uv](https://docs.astral.sh/uv/) 包管理器，提供更快的依赖安装和管理体验。

#### 快速开始

**Windows (PowerShell):**

```powershell
# 自动安装 uv 并初始化项目
.\setup_uv.ps1

# 运行程序
uv run python start_cli.py
```

**Linux/macOS (Bash):**

```bash
# 自动安装 uv 并初始化项目
./setup_uv.sh

# 运行程序
uv run python start_cli.py
```

详细的 uv 使用指南请参考 [UV_GUIDE.md](UV_GUIDE.md)。

### 直接使用二进制可执行文件

### Docker(推荐在服务器中使用这种方式)

[https://hub.docker.com/r/a645162/shmtu-auth](https://hub.docker.com/r/a645162/shmtu-auth)

```bash
docker pull registry.cn-shanghai.aliyuncs.com/a645162/shmtu-auth:latest
```

### 直接运行Python源代码(请手动安装依赖库)

#### Windows

```powershell
.\start.ps1
```

#### Linux

```bash
chmod +x start.sh
./start.sh
```

## 配置

**本地运行只有一个地方要改：`src/shmtu_auth/config/config.toml`。**

优先级是 `config.toml` > 环境变量 —— TOML 里写了值，同名环境变量就失效，
所以想临时用环境变量覆盖，把那一行注释掉即可。

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

> ⚠️ 这里的设备号要的是**物理网卡 MAC**（如 `00:e0:1a:00:23:a9`），
> 不是门户 URL 里那个加密过的 `mac` 串（如 `67d1ff70…`），两者完全不同。
> 容器里需要 `network_mode: host` 才能看到宿主的物理网卡。

### Docker 部署

不用 `config.toml`，改 `docker_headless/.env`（从 `.env.example` 复制一份），
变量名与上面完全一致。

> 完整的配置项说明都写在 `config.toml` 的注释里，每一项都有。

**可选配置项：**

* `SHMTU_MACHINE_NAME`: 服务器名称
* `SHMTU_AUTH_TIME_INTERVAL`: 认证状态检测时间间隔
<!-- - `SHMTU_AUTH_WEBHOOK_WEWORK` : 企业微信机器人WebHook -->
<!-- - `SHMTU_WEBHOOK_SLEEP_TIME_START` : WebHook免打扰-开始时间 -->
<!-- - `SHMTU_WEBHOOK_SLEEP_TIME_END` : WebHook免打扰-结束时间 -->

## 开发指南

推荐使用 `uv` 进行包管理，也可以使用 `Anaconda` 或 `Miniconda` 创建虚拟环境。
推荐使用学生认证的 `Jetbrains PyCharm Professional` 进行开发。

因为许多开发步骤已经在 `PyCharm` 中配置好，因此推荐使用 `PyCharm` 进行开发。

## GUI说明

Windows下AMD显卡显示Mica云母特效会有问题，因此全局关闭了Mica云母特效。

macOS x64下Python版本必须小于等于3.11，否则无法安装PySide6。

## License

本程序使用[GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html)协议开源。

GPLv3协议是我非常喜欢的一个协议，我的大部分程序均基于GPLv3协议开源。

此外，本程序使用到的QFluentWidgets库恰好也是基于GPLv3协议开源的。
