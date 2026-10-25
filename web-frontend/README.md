# Web 管理后台前端（web-frontend）

Vue 3 + Vite 前端骨架，对应 `web-backend` 提供的后台 API。页面已按模块对接各接口，但后端当前为占位实现（内存数据），需先启动网关与后端才能联调。

## 技术栈

- **Vue 3** + **Vite**
- **vue-router 4**：路由 + 登录守卫（刷新后先拉授权再放行，见 T-AM7）
- **Pinia**：auth 登录态 + 授权菜单/按钮权限码（token 存 localStorage）
- **axios**：请求封装，自动附带 `Authorization: Bearer <token>`
- **vitest + @vue/test-utils**：前端单测（T-AM7 引入）

## 权限模型（T-AM7 起）

导航与按钮不再硬编码角色矩阵，以后端 `GET /menu/my`（`sys_role_menu` 聚合）为唯一权威：

- `src/api/menu.js`：拉授权菜单（扁平列表）；
- `src/utils/menu.js`：纯函数层——`buildMenuTree` 建树、`collectPerms` 聚合按钮码、`matchPerm` 镜像后端 `has_perm`（`*:*` / 精确 / 通配前缀）、`MENU_ROUTE_MAP` 后端 path → 前端路由映射（未建页面的菜单不出导航，避免死链）；
- `src/directives/permission.js`：`v-has-permission="'exam:generate'"` 按钮级指令（数组为任一命中；admin 恒放行；授权未就绪不误伤）；
- `src/stores/auth.js`：`menus/perms/menuTree` + `hasPerm()` + `hasRouteAccess()`，localStorage 缓存供刷新秒开；
- `src/router/index.js`：守卫在刷新后（有 token 无菜单）先 `await loadMenus()` 再放行，并按菜单映射做路由门禁（UX 层，后端 `require_role/require_perm` 才是安全边界）；
- 后端按钮码示例：`exam:generate`（组卷）、`grading:submit/review`（批改）、`system:menu:add/edit/delete`（菜单管理）、`system:role:auth`（角色授权），授权矩阵见 `web-backend/app/services/rbac_seed.py::ROLE_MENU_MAP`，与各 API 的 `require_perm` 对齐。

## 管理页（T-AM3 菜单管理 / T-AM4 角色管理）

「看」的业务页之外，「管」的两个系统页面（后端 API 就绪后于 2026-10-26 补建）：

- `src/api/rbac.js`：菜单 CRUD（`listMenus/createMenu/updateMenu/deleteMenu`）+ 角色（`listRoles`）+ 授权回显/保存（`getRoleMenus`/`updateRoleMenus`）；
- `src/utils/tree.js`：管理面树形纯函数——`buildTree`（保留 BUTTON，区别于导航向的 `menu.js`）、`flattenTree`（附 depth 供缩进）、`subtreeIds/findNode/checkState/toggleSubtree`（授权树三态复选框父子联动）、`withAncestors`（提交前补齐祖先链：`/menu/my` 按直接关联取数，父 DIR 不授权会被挂到导航根级）；
- `src/utils/errmsg.js`：axios 错误 → 展示文案（透传后端 FastAPI `detail`）；
- `src/views/SystemMenu.vue`：菜单树形表格（DIR/MENU/BUTTON 逐行）+ 新建/编辑对话框（编辑时锁 menu_id、父级下拉防环）+ 删除确认（有子节点后端 400 挡）；
- `src/views/SystemRole.vue`：角色列表 + 全量菜单授权树（含 BUTTON 权限码，三态复选框）；**授权保存是全量替换语义**，故打开页面前先 `GET /role/{id}/menus` 回显当前授权，否则空手提交会清空角色菜单。

## 单测

```bash
npm install
npm test           # vitest run（74 例：菜单/树形纯函数、错误文案、API 契约、指令、store、Layout 与两个管理页组件）
```

> 环境注意：WorkBuddy 沙箱的 brokered-FS 埋点会拦截 vitest 的临时缓存写入，
> 并发下 IPC 超时会误杀测试文件收集（表现为个别文件"消失"、EPERM）。
> 跑测试时加 `CODEBUDDY_BROKERED_FS_HOOK_ENABLED=0 npm test` 只关该埋点
> （不动沙箱本身），即可稳定全量收集。

## 模块页面

| 路由 | 页面 | 对应后端模块 |
|---|---|---|
| `/dashboard` | 概览 | — |
| `/knowledge` | 教研内容管理 | knowledge |
| `/analytics` | 学情看板 | analytics |
| `/wrong-questions` | 错题本 | wrong-questions |
| `/exam` | 自动组卷 | exam |
| `/grading` | 教师批改 | grading |
| `/reports` | 家长周报 | reports |
| `/compliance` | 合规审核 | compliance |
| `/system/menu` | 菜单管理（T-AM3） | rbac `/menu` |
| `/system/role` | 角色管理（T-AM4） | rbac `/role` |

## 启动

```bash
npm install
npm run dev        # http://localhost:5173
```

开发服务器通过 Vite 代理把 `/api` 转发到 Go 网关（http://localhost:8080），再由网关分流到 web-backend。

## 容器化（生产/演示）

`web-frontend/` 已提供多阶段 `Dockerfile` + `nginx.conf`。

**生产（137，T-E1 2026-10-24 上线）**：管理后台挂在 **`/admin/` 子路径**（经 edu-lb 剥前缀反代到 `edu-web-front` 容器）：

- `vite.config.js` `base: '/admin/'` + `router` `createWebHistory('/admin/')`——地址栏、资源引用、路由链接均带 `/admin/` 前缀；
- 页面 API 走 `/api/admin/*`（lb → 网关 → web-backend），**不经**前端容器；
- 访问入口：`http(s)://<域名或IP>/admin/`（`/admin` 自动 301 补尾斜杠）。

```bash
# 137 上重建（源码已同步到 /opt/tes-edu-web-front/）：
docker build -t edu-web-front:real /opt/tes-edu-web-front
# 换容器 + 刷新 lb 上游（容器重建后 IP 变化，必须 HUP，详见 deploy/README 关键约束 7）
cd /opt/tes-edu-gateway && docker compose up -d web-front && docker kill -s HUP edu-lb
```

本地开发仍可用 `npm run dev`（base 对 dev 同样生效，访问 `http://localhost:5173/admin/`）；`/api` 由 Vite 代理到本地网关。

## 接口约定

- 所有请求 `baseURL = /api/admin`，与后端 `main.py` 的路由前缀一致。
- **例外——登录必须走根路径 `POST /login`**（见 `src/api/admin.js`）：网关 `/api/admin/` 前缀路由是 RBAC + `require_tenant`，未携带 token 时会在网关层直接 401，根本到不了 web-backend 的签发逻辑；而网关 `/login` 路由 `mode` 为空不鉴权，直通后端签发 RS256 JWT（2026-10-24 线上修复）。
- 角色控制：路由 `meta.role` 已移除（T-AM7）——授权以后端菜单矩阵为唯一权威，守卫经 `auth.hasRouteAccess` 执行；细粒度到按钮级由后端 `require_perm` 强制。

## 演示账号

`admin@tes.edu / demo1234`（参见 web-backend 占位用户库）

## 说明

本骨架**未接入真实数据库**，页面数据来自后端内存占位。登录鉴权链路已打通：
1. 网关已放行 `/api/admin/login`（无需 token 即可登录）；
2. 后端 `auth.login` 用 `python-jose` 以 HS256 签发真实 JWT（载荷含 `uid` / `role`，与网关 `JWT_SECRET` 一致）；
3. 网关在鉴权通过后向 web-backend 注入 `X-User-Id` / `X-User-Role` 头，后端据此做 RBAC。

因此页面结构 + 登录后调接口已可联调；后续只需把内存占位换成真实 PostgreSQL/Kafka 即可上线。
