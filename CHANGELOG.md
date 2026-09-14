# CHANGELOG

## 2.2.0

适配 2026-09 门户改版：认证页迁到 `https://ismu.shmtu.edu.cn:8443/eportal/`，
并**强制 4 位图形验证码 + RSA 加密密码**。

**核心功能**

- 重写门户登录链路为与浏览器逐字节对齐的五步流程：
  `index.jsp`（建会话）→ `pageInfo`（验证码地址 + RSA 公钥）→ `validcode`（取图）
  → OCR/人工识别 → `login`；全程复用同一个 `Session`（验证码绑 JSESSIONID）
- 密码加密实现（`portal_crypto.py`）与门户 `security.js` + `AuthInterFace.js`
  逐字节一致，含 ohdave RSAUtils 自定义填充与「明文先反转」两步
- 验证码默认用 `ddddocr` 识别，识别失败自动换新图重试（`SHMTU_AUTH_CAPTCHA_MAX_RETRY`）；
  GUI 无 OCR 时弹窗人工输入

**修复**

- **密码加密明文漏了反转** —— 门户明文是 `reverse(password + ">" + mac)`，
  漏掉时门户解出反的密码，恒定报「用户不存在或者密码错误！」
- **queryString 已编码导致取不到 `mac`** —— 探测抓回的是 `wlanuserip%3D...%26mac%3D...`
  形态，直接 `parse_qs` 解析不出字段；现在两种形态依次试
- **`index.jsp` 会话入口用错 queryString 形态** —— 服务端从该 URL 解析 `wlanuserip`/`mac`
  并绑定会话，必须用未编码的原始形态
- **漏掉登录前的 `InterFace.do?method=getServices`** —— 浏览器在 `pageInfo` 之后调用它，
  服务端据此绑定本会话可用服务
- **认不出 2026-09 改版后的门户地址** —— 判据停在旧的 `hwifi.shmtu.edu.cn`，
  现在按域名 / 路径 / 通用劫持特征三层判断
- **把代理错误页、透明代理伪造的 200 当成「已联网」** —— 改为只认 2xx +
  响应体特征，并要求 https 通道交叉验证；探测 `Session` 关闭 `trust_env` 绕过系统代理
- **DNS 解析无超时导致探测卡住** —— `socket.getaddrinfo` 不受 requests 超时约束，
  Windows 上实测等 48 秒；改为 daemon 线程 + 超时，并在探测前做 DNS 预检
- **GUI「添加用户」在空列表时编辑区禁用、无法新建** —— 修掉空状态死锁
- **认证前重复跑一遍连通性探测** —— `login()` 内 `test_net()` 之后 `get_query_string()`
  内部又跑一遍，改为复用结果；连通性结论另加短 TTL 缓存（`SHMTU_AUTH_CONNECT_CACHE_TTL`）

**新增**

- `diagnose_portal.py`：独立诊断脚本（只依赖 `requests`），打印出口 IP / 代理 / DNS /
  各探测地址状态码与响应体摘要 / 抓到的 queryString；支持 `--url` 解析手工粘贴的认证页 URL
- `test_auth_cli.py`：不依赖 GUI 的独立认证测试脚本，验证码可人工输入以隔离 OCR 因素
- `Tools/portal_crypto_oracle.js`：加载门户真实 JS（`security.js` + `AuthInterFace.js`）
  生成加密期望值，供单测逐字节比对
- 配置项 `SHMTU_AUTH_QUERY_STRING`：手动指定 queryString，跳过自动探测（终极兜底）
- GUI 接入自建凭据服务，并支持导出 Docker 配置
- 新增 `docker_headless/README.md` 与主 README 的排查章节

## 1.4.0

正式支持GUI
