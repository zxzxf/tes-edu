# Cloudflare Tunnel 部署指南（无域名 / 固定域名 HTTPS）

> 适用场景：一台云服务器（如 `124.221.253.137`），**不想做 ICP 备案、不想开防火墙入站端口、也没有（或暂时没有）域名**，也想用 `https://` 加密对外提供服务。
> 本文档基于 2026-10-21 在 `124.221.253.137`（CentOS 7，Docker）上的真实部署经验整理，可直接照抄复用到其他机器/域名。

---

## 0. 两种用法（先选一个）

| 用途 | 方式 | 地址 | 是否需要域名 | 重启后是否变 |
|---|---|---|---|---|
| 临时演示 / 先跑通 | **Quick Tunnel（快速隧道）** | `https://xxxx.trycloudflare.com` | ❌ 不需要 | ⚠️ 会变 |
| 固定地址长期用 | **Named Tunnel（命名隧道）+ 域名** | `https://你的域名` | ✅ 需要 | ✅ 不变 |

本指南主线路线 = **命名隧道 + 已购域名**（固定地址）。如果只是临时验证，先看附录 A 的 Quick Tunnel。

---

## 1. 整体架构（流量怎么走的）

```
用户浏览器
   │  https（由 Cloudflare 边缘节点签发证书，TLS 终止在 Cloudflare）
   ▼
Cloudflare 边缘节点（全球 Anycast，隐藏你的源站 IP）
   │  加密隧道（outbound，服务器主动连出去，无需开放任何入站端口）
   ▼
服务器上的 cloudflared 容器  ──►  本机服务（如网关 http://localhost:80）
```

关键点：**服务器只出不让进**，防火墙 80/443 全关也没关系；域名 DNS 只解析到 Cloudflare，源站 IP 不暴露。

---

## 2. 前置条件

- 一个 Cloudflare **免费**账号（注册无需信用卡）。
- 一台装了 Docker 的服务器，且能主动访问外网（隧道是出站连接）。
- 本机服务已经在服务器上监听某个端口（本文以网关 `http://localhost:80` 为例）。
- （固定域名方案）一个已购买的域名，且能在注册商处修改 NS（Nameserver）。

---

## 3. 部署步骤（命名隧道 + 固定域名）

### Step 1 — 域名加进 Cloudflare

1. 登录 <https://dash.cloudflare.com> → 「添加站点 / Add a site」→ 输入你的域名（如 `sd12sd.icu`）。
2. 选 **Free** 套餐 → 继续。
3. Cloudflare 会扫一遍现有解析记录（可跳过）。
4. 页面会给你**两个 Cloudflare 分配的 NS**，形如：
   ```
   lakas.ns.cloudflare.com
   sima.ns.cloudflare.com
   ```
   **记下来**，下一步要用。（同一 Cloudflare 账号下所有域名共用这一对 NS。）

### Step 2 — 在域名注册商处把 NS 改成 Cloudflare

> ⚠️ **改 NS 不在 Cloudflare 里改，在你“买域名的平台”改。**

以**腾讯云**为例（其他平台思路一致）：
1. 打开 <https://console.cloud.tencent.com/domain> → 域名列表 → 找到你的域名 → **管理**。
2. 切到 **「基本信息」** 标签 → 找到 **「DNS 服务器」** 一栏 → 点 **「修改 DNS 服务器」**。
3. 选 **「使用非腾讯云 DNS」**（不要选“使用 DNSPod 推荐”）。
4. 把 Step 1 记下的两个 `*.ns.cloudflare.com` 填进去 → **提交**。

> 常见迷路点（本次踩过的坑）：
> - 别进轻量服务器控制台的「域名解析记录」页 —— 那是管 A 记录指向哪台机器的，不是改 NS。
> - 别进域名详情里的「自定义 DNS Host」标签 —— 那是给“用自己的 ns1.你的域名”做胶水记录用的，用不上。

### Step 3 — 等 Cloudflare 状态变 Active

- 回到 Cloudflare 控制台 → 你的域名 → **概述**，状态会从 `Pending Nameserver update` 变成 **Active**（“您的域现在受 Cloudflare 保护”）。
- 想加速：点页面上的 **「立即检查名称服务器 / Check nameservers」** 按钮。
- 时效：注册局推送通常几分钟~1 小时（`.icu` 这类新顶级域很快），最长 24~48h。
- 服务器侧可在 NS 切换后这样确认（直查注册局权威，不受公共缓存影响）：
  ```bash
  dig NS 你的域名 @a.nic.icu          # 返回应为 lakas/sima.ns.cloudflare.com
  ```

### Step 4 — 创建命名隧道，拿到 token

1. 打开 <https://one.dash.cloudflare.com> → **Networks → Tunnels → Create a tunnel**。
2. 起名（如 `tes-edu`）→ 选 **Cloudflared** → 进入后页面会显示 `docker run ... --token <TOKEN>`。
3. 复制那串 `<TOKEN>`（很长的一段 JWT，形如 `eyJhIjoi...`）。

> 如果不想用控制台，也可以 CLI 创建（需本地有 `cloudflared`）：
> ```bash
> cloudflared tunnel login
> cloudflared tunnel create tes-edu
> cloudflared tunnel token tes-edu      # 打印 token
> ```

### Step 5 — 服务器部署 cloudflared（docker-compose）

在服务器 `/opt/cloudflared/docker-compose.yml`：

```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    network_mode: host          # 必须 host，否则 localhost:80 指不到宿主机服务
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token <TOKEN>   # 换成 Step 4 的 token
```

启动：
```bash
mkdir -p /opt/cloudflared
# 把上面的 yaml 写进 /opt/cloudflared/docker-compose.yml
cd /opt/cloudflared && docker compose up -d
docker ps --filter name=cloudflared     # 看到 Up 即正常
```

> `network_mode: host` 是必须的：隧道容器要访问宿主机上的 `localhost:80`，桥接网络下 `localhost` 指的是容器自己。

### Step 6 — 在隧道加 Public Hostname（把域名绑进来）

1. Cloudflare 控制台 → 你的域名 → 左侧 **Zero Trust**（或 `one.dash.cloudflare.com`）。
2. **Networks → Tunnels → `tes-edu`** → 顶部 **「路由 / Public Hostname」** → **添加路由**。
3. 选 **「已发布的应用程序 / Public Hostname」**，按表填：

   | 字段 | 填什么 |
   |---|---|
   | 子域名 Subdomain | **留空**（根域名直接对外） |
   | 域 Domain | 选你的域名（如 `sd12sd.icu`） |
   | 路径 Path | 留空 |
   | 类型 Type | **HTTP**（不是 HTTPS） |
   | URL | **`http://localhost:80`** ← 必须带 `http://` |

4. 保存。

> ⚠️ **URL 务必写 `http://localhost:80`**：你的服务在服务器本机是明文 HTTP，加密由 Cloudflare 隧道外层负责；若误写成 `https://` 会出现 502。

### Step 7 — 🔴 关键坑：删掉冲突的旧 A 记录

如果域名之前在注册商/DNSPod 里建过指向服务器 IP 的 A 记录（如 `A ｜ sd12sd.icu ｜ 124.221.253.137`），它会和隧道要创建的 CNAME **冲突**，导致：

- Cloudflare 无法自动建 CNAME；
- 外网访问 `https://你的域名` 返回 **522**（Cloudflare 边缘直连你服务器 443，但 443 没人监听，超时）。

**修复**：
1. 打开 <https://dash.cloudflare.com> → 你的域名 → 左侧 **DNS → 记录**。
2. 删除所有指向你服务器 IP 的 **A 记录**（根域名 + `www` 都删）。
3. 回到隧道路由，**编辑 `你的域名` 那条路由 → 直接重新保存**（触发 Cloudflare 重建 CNAME 指向 `xxxx.cfargotunnel.com`）。
4. DNS 记录页应出现新的一行 `CNAME ｜ 你的域名 ｜ xxxx.cfargotunnel.com`。

### Step 8 — 验证 HTTPS

在**服务器上**直连验证（避免本地代理干扰）：
```bash
dig +short 你的域名                 # 应返回 Cloudflare 边缘 IP（如 104.21.x / 172.67.x）
curl -sI https://你的域名/          # 响应头含 Server: cloudflare / CF-RAY 即走通隧道
curl -s https://你的域名/healthz    # 应返回 200（视你网关实际路径）
```

或直接浏览器打开 `https://你的域名`，看到小锁即成功。

### Step 9 — 停掉临时隧道（如用过 Quick Tunnel）

```bash
docker stop cf-quick && docker rm cf-quick
docker ps --filter name=cloudflared   # 仅保留正式命名隧道
```

---

## 4. 可复用片段速查

**docker-compose（命名隧道）**
```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    network_mode: host
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token <TOKEN>
```

**服务器端验证三连**
```bash
docker ps --filter name=cloudflared
dig +short 你的域名
curl -s -o /dev/null -w "HTTP %{http_code}\n" https://你的域名/healthz
```

**cloudflared 日志排错**
```bash
docker logs cloudflared --tail 20
```

---

## 5. 排错表

| 现象 | 原因 | 解决 |
|---|---|---|
| 控制台一直 `Pending Nameserver update` | 注册商 NS 没改 / 改错 | 回 Step 2 确认填的是两个 `*.ns.cloudflare.com`，不是 DNSPod 的 |
| 访问返回 **522** | 旧 A 记录与隧道 CNAME 冲突，流量没走隧道 | 回 Step 7 删旧 A 记录 + 重存路由 |
| 隧道连上但访问 **502** | Public Hostname 的 URL 写成 `https://localhost:80` | 改成 `http://localhost:80`（明文，隧道负责加密） |
| 网关 404 | 服务根路径没挂 handler（如只有 `/healthz`） | 正常，确认业务路径；404 不代表链路坏（响应头有 `Server: cloudflare` 即通） |
| 换端口后不通 | 服务没监听 / `network_mode` 非 host | 确认服务在 `localhost:PORT` 可达，compose 用 `host` 模式 |
| `docker: command not found` | 服务器没装 Docker | 先安装 Docker 与 docker-compose 插件 |

---

## 6. 安全与备案说明

- **源站 IP 隐藏**：DNS 只指向 Cloudflare，服务器无需开 80/443 入站，降低被扫描爆破面。
- **无需 ICP 备案**：流量经 Cloudflare 边缘终止，域名不解析到国内服务器，常规无备案风险；如需国内节点直连再单独评估。
- **建议后续加固**（与本文无关但值得做）：
  - 服务器 `sshd` 关闭密码登录（仅密钥）；
  - 业务配置里的 `JWT_SECRET` 改为随机值，勿用 `change-me-in-prod`；
  - 隧道 token 属敏感凭证，compose 文件勿公开提交。

---

## 附录 A：Quick Tunnel（临时、免域名）

只想先跑通、不想买域名时：
```bash
docker run -d --name cf-quick --restart unless-stopped \
  cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://localhost:80
docker logs cf-quick | grep -o 'https://[a-z-]*\.trycloudflare\.com'
```
输出里的 `https://xxxx.trycloudflare.com` 即为临时地址。**注意：容器重启后地址会变**，正式环境请用上文命名隧道。

---

## 附录 B：本次实际部署留痕（2026-10-21）

- 服务器：`124.221.253.137`（CentOS 7，Docker），网关监听宿主机 `:80`。
- 域名：`sd12sd.icu`（腾讯云购买，注册商 NS 改为 `lakas/sima.ns.cloudflare.com`）。
- 隧道：命名隧道 `tes-edu`，compose 部署于 `/opt/cloudflared/docker-compose.yml`。
- 绑定：`sd12sd.icu` → `http://localhost:80`。
- 踩坑：旧 A 记录导致 522 → 删 A 记录 + 重存路由后解决。
- 结果：`https://sd12sd.icu/healthz` 返回 200；`cf-quick` 已停。
