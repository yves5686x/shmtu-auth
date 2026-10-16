# Docker 部署指南

服务器 / NAS 常驻运行的推荐方式。跑的是**主包 CLI**（与 GUI 共用认证核心，含验证码 OCR），
账号可通过浏览器设置，也支持原有 `.env` 配置。

## 最简单：浏览器设置账号

在 Ubuntu 宿主机执行：

```bash
docker run -d --name shmtu-auth --network host --restart unless-stopped \
  -v shmtu-auth-data:/app/data \
  ghcr.io/yves5686x/shmtu-auth:feat-portal-captcha
docker logs shmtu-auth
```

首次日志会显示一次「网页初始管理密码」。打开 `http://宿主机IP:8080`，用这个管理密码
登录，在网页填写学号与校园网密码，点击「保存并应用」。还没有账号时容器会等待设置。
保存后自动认证，不需要重启容器；网页也能查看当前状态、添加删除账号和修改管理密码。

网页管理密码与校园网密码不同。已有账号的密码留空可保留；删除全部账号后暂停认证。
网页配置优先于环境变量和凭据服务；需要恢复原有配置来源时，移除数据目录中的
`accounts.json` 并重启容器。不要删除数据卷，重建容器时继续挂载同一个卷。

网页通过 HTTP 在本机或可信局域网使用，不要直接暴露公网；跨不可信网络请配 HTTPS
反向代理或使用 SSH 隧道。端口冲突时添加 `-e SHMTU_AUTH_WEB_PORT=8081`。
可设置 `SHMTU_AUTH_WEB_HOST=127.0.0.1` 仅允许宿主机访问。

其他设置：

- `SHMTU_AUTH_WEB=false`：关闭网页，恢复原 CLI 启动方式，未配置账号时退出。
- `SHMTU_AUTH_WEB_PASSWORD`：首次初始化时指定管理密码，至少 12 个字符；已有管理配置时忽略。
- `SHMTU_AUTH_SETTINGS_PATH`：账号文件路径，默认 `/app/data/accounts.json`。
- 忘记管理密码：停容器后移除数据卷内 `web-admin.json`，重启会生成新管理密码，校园网账号保留。

使用 Compose 时可直接执行 `docker compose -f docker-compose.image.yaml up -d`，
账号在网页填写即可（需要 Compose 2.24 或更新版本，以支持可选 `.env`）。

## 目录结构

```
Docker/
├── Dockerfile            # 主包镜像（python:3.12-slim + OCR 依赖）
├── docker-compose.yaml   # 服务编排：host 网络 + 数据卷挂载
├── .env.example          # 配置模板（复制成 .env 后只改这一个文件）
└── README.md             # 本文档
```

## 快速开始

### 使用 GitHub 自动构建的镜像

推送 Docker 或主包代码到 GitHub 后，`Build and publish Docker image` 工作流会自动构建
并测试 Linux amd64 / arm64 镜像，通过后发布到 `ghcr.io/yves5686x/shmtu-auth`。
不需要设置 Docker Hub 或阿里云密码。也可在 Actions 页面手动运行工作流。

当前开发分支的标签为 `feat-portal-captcha`；默认分支构建成功后会更新 `latest`。
每次构建还提供 `sha-<提交短哈希>` 标签，方便固定版本。

```bash
cd Docker
cp .env.example .env
# 编辑 .env，填写账号密码
docker compose -f docker-compose.image.yaml pull
docker compose -f docker-compose.image.yaml up -d
docker compose -f docker-compose.image.yaml logs -f
```

以后升级只需重新执行 `pull` 和 `up -d`，无需在服务器上编译。
可在 `.env` 中设置 `SHMTU_AUTH_IMAGE=ghcr.io/yves5686x/shmtu-auth:latest` 切换标签。

GHCR 首次创建的包可能是 Private。要让服务器免登录拉取，在 GitHub 包页面的
Package settings 中将 Visibility 改为 Public；保留 Private 则需要先登录 GHCR。

### 从源码本地构建

```bash
cd Docker
cp .env.example .env
# 编辑 .env：至少填 SHMTU_AUTH_USER_LIST 和对应的密码行
docker compose up -d --build
docker compose logs -f          # 看到「Login success.」即认证成功
```

## .env 配置说明

| 变量 | 必填 | 说明 |
|------|:---:|------|
| `SHMTU_AUTH_USER_LIST` | ✅ | 学号，多个用分号分隔：`202500000000;202500000001` |
| `SHMTU_AUTH_USER_PWD_<学号>` | ✅ | 每个学号一行密码。**密码用单引号包裹**，避免 `$`、`#` 被 Compose 解释 |
| `SHMTU_AUTH_TIME_INTERVAL` | | 检测间隔（秒），默认 10 |
| `SHMTU_AUTH_PORTAL_SERVICE` | | 定死接入类型（`校园网` / `iSMU`）；不填则两种依次都试 |
| `SHMTU_AUTH_CAPTCHA_MAX_RETRY` | | 验证码识别重试次数，默认 6。无头环境**不建议调小** |
| `SHMTU_AUTH_QUERY_STRING` | | 手动指定 queryString 的终极兜底，见下文「排查」 |

### 自建凭据服务（可选，多机管理用）

不想把密码写在每台机器的 `.env` 里时，可以用自建凭据服务：容器拿本机物理网卡 MAC
当设备号去服务端换账号密码，配了就**优先于本地账号**。

```bash
SHMTU_AUTH_CREDENTIAL_URL=https://your-server/device/{mac}/credential
SHMTU_AUTH_CREDENTIAL_TOKEN=<服务端下发的 token>
```

> ⚠️ 密码明文过网络，服务端务必 HTTPS + token 校验，不要暴露公网。
> 接口约定见 `src/shmtu_auth/config/config.toml` 的 `[Credential]` 段。

## 为什么必须 host 网络

`docker-compose.yaml` 里写死了 `network_mode: host`，这不是可选项：

1. 门户认证依赖**看到宿主机的物理网卡**（设备号 / MAC），桥接网络下容器只有虚拟网卡，
   设备标识对不上；
2. captive portal 的劫持跳转发生在网关与宿主机之间，host 网络下容器才与宿主机共享
   网络栈，探测和登录请求的源 IP 才是正确的。

因此 **仅支持 Linux 宿主机**（host 网络是 Linux 的网络驱动；Windows/macOS 的 Docker
Desktop 不支持）。Windows 机器请用 GUI / exe 版本。

## 数据与日志

compose 已把两个目录挂载到宿主机（`Docker/` 下的相对路径）：

| 宿主目录 | 容器内 | 内容 |
|----------|--------|------|
| `./logs` | `/app/logs` | 运行日志（loguru） |
| `./data` | `/app/data` | 凭据服务缓存等运行数据 |

容器自身日志走 json-file 轮转（单文件 10MB × 3 份），不会撑爆磁盘。
`docker compose down` 不影响这两个目录；升级镜像后数据自动沿用。

## 常用运维命令

```bash
cd Docker
docker compose up -d --build   # 首次部署 / 改代码后重建
docker compose logs -f         # 跟踪日志（Ctrl+C 退出，容器继续跑）
docker compose logs --tail 100 # 看最近 100 行
docker compose restart         # 重启
docker compose down            # 停止并移除容器（数据保留）
docker compose pull && docker compose up -d --build   # 拉新代码后更新
```

改了 `.env` 后需要 `docker compose up -d` 让容器重新读取。

## 在另一台服务器上部署（从零开始）

前置要求只有两条：**Linux 服务器**（host 网络仅 Linux 可用）+ 装好 Docker。

### 方式 A：源码构建（推荐，服务器能上网时）

```bash
# 1) 装 Docker（官方一键脚本；装完当前用户可能需要重新登录以加入 docker 组）
curl -fsSL https://get.docker.com | sh

# 2) 把代码弄到服务器上，二选一：
git clone https://github.com/a645162/shmtu-auth.git      # 能访问 GitHub
# 或在开发机上打包整个仓库传过去（必须完整仓库，见下方说明）：
#   tar --exclude=.venv --exclude=.git -czf shmtu-auth-src.tar.gz shmtu-auth/
#   scp shmtu-auth-src.tar.gz user@server:~/
#   tar -xzf shmtu-auth-src.tar.gz && cd shmtu-auth

# 3) 配置并启动
cd shmtu-auth/Docker
cp .env.example .env
vi .env                       # 至少填 SHMTU_AUTH_USER_LIST 和密码行
docker compose up -d --build
docker compose logs -f        # 看到 Login success. 即成功
```

> ⚠️ 必须是**完整仓库**：compose 的 `build.context` 指向仓库根，镜像构建时要把
> `src/`、`requirements.txt` 等一起打包进镜像，只拷 `Docker/` 一个目录构建不了。
>
> 但也不是什么都拷——这些**都不需要**：
> - `.venv/`：本机开发用的虚拟环境。Docker 会按 `requirements.txt` 在镜像里
>   重新装依赖，服务器上用不到它，拷了白占几百 MB；
> - `Build/`：exe 打包产物；
> - `logs/`、`data/`、`config/`：本机运行时产物；
> - `__pycache__/`、`.git/`（git clone 自然没有）。
>
> 构建镜像的**最小集合**其实只有三样（对应 Dockerfile 里两行 COPY）：
>
> ```
> Docker/            # Dockerfile + docker-compose.yaml + .env.example
> src/shmtu_auth/    # 全部源码
> requirements.txt   # 依赖清单（镜像内按它装依赖）
> ```

### 方式 B：离线镜像（服务器构建慢 / 访问外网困难时）

在**能构建的机器**上（本机或 CI）先构建镜像并导出：

```bash
cd Docker
docker compose build
docker save shmtu-auth:local | gzip > shmtu-auth-image.tar.gz
```

把三样东西拷到目标服务器：`shmtu-auth-image.tar.gz`、`docker-compose.yaml`、`.env.example`，然后：

```bash
docker load -i shmtu-auth-image.tar.gz      # 导入镜像（约 200MB+）
cp .env.example .env && vi .env             # 填账号
docker compose up -d                        # 本地已有镜像，不会再触发构建
```

`docker-compose.yaml` 里保留的 `build:` 段在方式 B 下用不到，留着无妨。

## 排查指南

按顺序看 `docker compose logs`：

| 日志关键字 | 含义 | 处理 |
|-----------|------|------|
| `Login success.` | 认证成功 | 无需处理 |
| `Network is online` | 已联网 | 无需处理 |
| `No user information found.` | 没读到账号 | 检查 `.env` 的 USER_LIST / 密码行，改后 `up -d` |
| `Query String is Invalid` | 没抓到门户跳转 | 多为不在校园网内 / DNS 不通；见下 |
| `用户不存在或者密码错误` | 门户拒绝了提交 | 核对账号密码（浏览器手动登录验证）；或 service 不匹配 |
| `验证码识别失败` | OCR 没识别出来 | 靠重试机制；确认镜像没裁掉 OCR 依赖 |

**抓不到 queryString 时的兜底**：在宿主机浏览器打开任意 http 站点（如
`http://www.shmtu.edu.cn`），会被跳到认证页，把地址栏完整 URL 填进
`SHMTU_AUTH_QUERY_STRING` 后 `docker compose up -d`。
注意该值绑定本机当前 IP，换网络 / 重连后要重新粘贴。

容器内进一步排查：

```bash
# 探测全流程诊断（脚本在源码仓库根，host 网络下与容器同网络栈，宿主机跑即可）
python diagnose_portal.py
docker compose exec shmtu-auth env | grep SHMTU   # 确认环境变量已生效
```

## 与其他部署形态的关系

- **GUI / 单文件 exe**：本机有桌面的场景，见仓库根 [README](../README.md)；
- **GUI 一键导出多机 Docker 配置**：在账号列表页右键 →「导出Docker配置」，生成的每台
  服务器配置里，`build.context` 需指向该服务器上的源码仓库路径；
- Docker 版与 GUI 共用同一套认证核心（含 2026-09 新门户 eportal 协议），行为一致。
