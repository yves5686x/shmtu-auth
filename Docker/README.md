# Docker 部署指南

以下步骤面向 Ubuntu 有线宿主机，需要 Docker 和 Compose 2.24 或更新版本。
默认直接拉取 GitHub 构建好的镜像，账号在网页填写，不需要 `.env` 或本地编译。

## 最简单的启动方式

首次下载代码：

```bash
git clone -b feat/portal-captcha https://github.com/yves5686x/shmtu-auth.git
cd shmtu-auth/Docker
docker compose up -d
```

已经下载过代码时，在仓库根目录执行 `git pull origin feat/portal-captcha`，
再进入 `Docker` 目录执行 `docker compose up -d`。

浏览器打开 `http://宿主机IP:18888`，默认管理密码是 `Letsgo123!@#`。
登录后填写学号与校园网密码，点击「保存并应用」，程序自动开始认证。
没有账号时容器会保持运行，等待网页设置。

首次启动在本地没有镜像时会自动拉取。已有镜像时直接使用本地镜像，
避免尚未认证、无法访问镜像仓库时阻塞启动。更新镜像请执行：

```bash
docker compose pull
docker compose up -d
```

GHCR 首次创建的包可能是 Private。若拉取提示 `denied`，在 GitHub 包页面的
Package settings 中将 Visibility 改为 Public，或先登录 GHCR。

## 网页账号设置

- 网页管理密码与校园网密码不同，可在网页修改管理密码。
- 默认管理密码仅用于首次初始化；升级不会覆盖数据目录中已有的管理密码。
- 支持多个账号，按顺序尝试，任一账号成功后停止。
- 已有账号的密码留空可保留；填写新密码会覆盖原密码。
- 保存后自动生效，无需重启；删除全部账号会暂停认证。
- 网页配置优先于环境变量和凭据服务。要恢复原有来源，停容器后移除
  `data/accounts.json` 并重新启动。

网页通过 HTTP 在本机或可信局域网使用，不要直接暴露公网。
跨不可信网络请配 HTTPS 反向代理或使用 SSH 隧道。

## 数据与日志

当前 `Docker` 目录下的两个目录会挂载到容器：

| 宿主目录 | 容器目录 | 内容 |
| --- | --- | --- |
| `./data` | `/app/data` | 校园网账号、管理密码哈希、凭据缓存 |
| `./logs` | `/app/logs` | 认证日志 |

`docker compose down` 和更新镜像不会删除这两个目录，请保留并妥善备份 `data`。
从之前的 `docker run -v shmtu-auth-data:/app/data` 切换时，原账号在命名数据卷里；
若要保留，可将 Compose 的数据挂载改为 `shmtu-auth-data:/app/data`，
并在文件末尾声明 `volumes: {shmtu-auth-data: {external: true}}`。
从手动创建的同名容器切换到 Compose 时，需先停止并移除旧容器，保留数据。

## 可选 .env 配置

需要自定义时才复制 `.env.example` 为 `.env`。例如：

```dotenv
SHMTU_AUTH_WEB_PORT=18889
SHMTU_AUTH_TIME_INTERVAL=60
```

其他可选项：

| 变量 | 作用 |
| --- | --- |
| `SHMTU_AUTH_WEB=false` | 关闭网页，恢复 CLI，未配置账号时退出 |
| `SHMTU_AUTH_WEB_HOST=127.0.0.1` | 只允许宿主机访问网页 |
| `SHMTU_AUTH_WEB_PASSWORD` | 首次初始化时指定管理密码，至少 12 个字符 |
| `SHMTU_AUTH_IMAGE` | 指定镜像标签，默认 `ghcr.io/yves5686x/shmtu-auth:feat-portal-captcha` |
| `SHMTU_AUTH_CAPTCHA_OCR` | 是否识别验证码，默认开启 |
| `SHMTU_AUTH_CAPTCHA_MAX_RETRY` | 每轮验证码尝试上限，默认 6 |
| `SHMTU_AUTH_QUERY_STRING` | 手动指定认证页地址，绑定当前 IP，重连后可能失效 |

仍可使用环境变量配置账号：

```dotenv
SHMTU_AUTH_USER_LIST=202500000001;202500000002
SHMTU_AUTH_USER_PWD_202500000001='第一个密码'
SHMTU_AUTH_USER_PWD_202500000002='第二个密码'
```

`.env` 中的密码按 Compose 规则引用。配置过网页账号后，以网页为准。
改动 `.env` 后执行 `docker compose up -d`，单纯 `restart` 不会重新读取环境变量。
自建凭据服务配置仍支持，参考 `.env.example` 中的 `SHMTU_AUTH_CREDENTIAL_*` 说明。

## 运维

```bash
docker compose ps
docker compose logs -f --tail=100
docker compose restart
docker compose down
```

忘记管理密码时，停容器后移除 `data/web-admin.json`，重新启动会恢复默认管理密码
（或首次初始化环境变量指定的密码），校园网账号不受影响。

## 本地源码构建

默认 `docker-compose.yaml` 不再构建源码。本地开发可使用单独文件：

```bash
docker compose -f docker-compose.build.yaml up -d --build
docker compose -f docker-compose.build.yaml logs -f
```

这需要完整仓库，构建会读取 `src/shmtu_auth/`、`requirements.txt` 和 `Docker/Dockerfile`。
`docker-compose.image.yaml` 仍可使用，与默认文件均为拉取已编译镜像的配置。

## 离线部署

在能联网的机器上构建、导出：

```bash
docker compose -f docker-compose.build.yaml build
docker save shmtu-auth:local | gzip > shmtu-auth-image.tar.gz
```

将镜像压缩包和 `docker-compose.yaml` 拷到目标机器同一目录，执行：

```bash
docker load -i shmtu-auth-image.tar.gz
SHMTU_AUTH_IMAGE=shmtu-auth:local docker compose up -d --pull never
```

然后访问网页填写账号，无需 `.env`。

## 网络与自动构建

Compose 使用 `network_mode: host`，让认证请求使用 Ubuntu 宿主机网络。
不用设置端口映射，网页直接通过宿主机的 18888 端口访问。

推送 Docker 或主包代码到 GitHub 后，`Build and publish Docker image` 工作流
会构建并测试 amd64、arm64 镜像，通过后发布到 GHCR。
当前开发分支标签为 `feat-portal-captcha`，默认分支构建成功后更新 `latest`。
还提供 `sha-<提交短哈希>` 标签供固定版本使用。
