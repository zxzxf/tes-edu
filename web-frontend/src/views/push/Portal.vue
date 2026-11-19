<template>
  <!-- 第三方推送 portal：独立会话域（portal 令牌），不进主站 Layout 壳子。
       未登录=登录/注册卡；已登录=自有侧栏五视图（账号/应用/token/主题/消息+接入帮助）。 -->
  <div v-if="!session" class="pt-gate">
    <div class="pt-gate-card">
      <div class="pt-logo"><span class="pt-logo-mark">P</span><b>推送中心 · 开发者门户</b></div>
      <p class="pt-sub">第三方接入应用自助注册、推送 token 管理与用量查询</p>

      <div class="pt-tabs">
        <button :class="{ on: tab === 'login' }" @click="tab = 'login'">登录</button>
        <button :class="{ on: tab === 'reg' }" @click="tab = 'reg'">注册</button>
      </div>

      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="notice" class="msg">{{ notice }}</p>

      <form v-if="tab === 'login'" @submit.prevent="doLogin">
        <label>邮箱<input v-model.trim="loginForm.email" type="email" required /></label>
        <label>密码<input v-model="loginForm.password" type="password" required /></label>
        <button class="tes-btn-primary full" :disabled="busy" type="submit">登录</button>
      </form>
      <form v-else @submit.prevent="doRegister">
        <label>邮箱<input v-model.trim="regForm.email" type="email" required /></label>
        <label>机构名称<input v-model.trim="regForm.org_name" required /></label>
        <label>密码（≥8 位）<input v-model="regForm.password" type="password" minlength="8" required /></label>
        <label>联系电话（可选）<input v-model.trim="regForm.phone" /></label>
        <button class="tes-btn-primary full" :disabled="busy" type="submit">提交注册（待平台审批）</button>
      </form>
    </div>
  </div>

  <div v-else class="pt-shell">
    <header class="pt-head">
      <div class="pt-logo"><span class="pt-logo-mark">P</span><b>推送中心 · {{ me.org_name }}</b></div>
      <span class="pt-roles">{{ (me.roles || []).join('/') || '未授权' }}</span>
      <span class="cw-grow"></span>
      <span class="pt-mail">{{ me.email }}</span>
      <button class="ghost" @click="logout">退出</button>
    </header>

    <div class="pt-body">
      <nav class="pt-side">
        <button v-for="v in views" :key="v.k" class="pt-side-item" :class="{ on: view === v.k }" @click="switchView(v.k)">
          {{ v.label }}
        </button>
      </nav>

      <main class="pt-main">
        <p v-if="error2" class="err">{{ error2 }}</p>
        <p v-if="msg2" class="msg">{{ msg2 }}</p>

        <!-- 账号概览 -->
        <section v-if="view === 'me'" class="tes-card">
          <h3>我的账号</h3>
          <table class="tes-table">
            <tbody>
              <tr><td>账号 ID</td><td>{{ me.account_id }}</td></tr>
              <tr><td>邮箱</td><td>{{ me.email }}</td></tr>
              <tr><td>机构</td><td>{{ me.org_name }}</td></tr>
              <tr><td>状态</td><td><span class="tes-pill" :class="me.status === 'active' ? 'success' : 'info'">{{ me.status }}</span></td></tr>
              <tr><td>角色</td><td>{{ (me.roles || []).join(', ') || '（待 admin 授予 developer/operator/viewer）' }}</td></tr>
            </tbody>
          </table>
          <p v-if="me.status === 'pending'" class="cw-hint">注册已提交，等待平台管理员审批后应用/token 等写操作才会放行。</p>
        </section>

        <!-- 应用 -->
        <section v-else-if="view === 'apps'" class="tes-card">
          <div class="cw-head"><b>接入应用</b><span class="cw-grow"></span>
            <button v-if="isDev" class="tes-btn-primary sm" @click="appForm = { name: '', channels: ['app_offline'], rate_limit_per_min: 60 }">+ 新建应用</button>
          </div>
          <table class="tes-table" v-if="apps.length">
            <thead><tr><th>ID</th><th>名称</th><th>渠道</th><th>频控</th><th>状态</th><th v-if="isDev">操作</th></tr></thead>
            <tbody>
              <tr v-for="a in apps" :key="a.app_id">
                <td>{{ a.app_id }}</td><td>{{ a.name }}</td>
                <td><code>{{ (a.allowed_channels || []).join(' + ') }}</code></td>
                <td>{{ a.rate_limit_per_min }}/分</td>
                <td><span class="tes-pill" :class="a.enabled ? 'success' : 'danger'">{{ a.enabled ? '启用' : '禁用' }}</span></td>
                <td v-if="isDev"><button class="ghost" :disabled="busy" @click="disableApp(a)">禁用</button></td>
              </tr>
            </tbody>
          </table>
          <p v-else class="tes-empty">暂无应用。</p>
          <div v-if="appForm" class="pt-inline-form">
            <label>名称<input v-model.trim="appForm.name" maxlength="128" /></label>
            <label><input type="checkbox" value="app_offline" v-model="appForm.channels" />app_offline</label>
            <label><input type="checkbox" value="wechat_oa" v-model="appForm.channels" />wechat_oa</label>
            <label>频控<input v-model.number="appForm.rate_limit_per_min" type="number" min="1" /></label>
            <button class="tes-btn-primary sm" :disabled="busy || !appForm.name || !appForm.channels.length" @click="createApp">创建</button>
            <button class="ghost sm" @click="appForm = null">取消</button>
          </div>
        </section>

        <!-- token -->
        <section v-else-if="view === 'tokens'" class="tes-card">
          <div class="cw-head"><b>推送 token</b>
            <select v-model.number="curApp" @change="loadTokens">
              <option v-for="a in apps" :key="a.app_id" :value="a.app_id">{{ a.name }}</option>
            </select>
            <span class="cw-grow"></span>
            <button v-if="isDev" class="tes-btn-primary sm" :disabled="!curApp" @click="issueForm = { tier: 6, rate: null }">+ 签发/换发</button>
          </div>
          <p class="cw-hint">续期=换发：签发新 token 后旧 token 进入 <b>7 天宽限</b>并行可用，随后立即失效。</p>
          <table class="tes-table" v-if="tokens.length">
            <thead><tr><th>前缀</th><th>档位</th><th>状态</th><th>签发</th><th>到期</th><th>宽限至</th><th>最近使用</th><th v-if="isDev"></th></tr></thead>
            <tbody>
              <tr v-for="t in tokens" :key="t.token_id">
                <td class="mono">{{ t.token_prefix }}…</td>
                <td>{{ t.tier }} 月</td>
                <td><span class="tes-pill" :class="t.status === 'active' ? 'success' : t.status === 'rotated' ? 'info' : 'danger'">{{ t.status }}</span></td>
                <td>{{ fmt(t.issued_at) }}</td><td>{{ fmt(t.expired_at) }}</td>
                <td>{{ t.grace_until ? fmt(t.grace_until) : '—' }}</td>
                <td>{{ t.last_used_at ? fmt(t.last_used_at) : '从未' }}</td>
                <td v-if="isDev">
                  <button v-if="t.status !== 'revoked'" class="ghost" :disabled="busy" @click="revokeTok(t)">吊销</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="tes-empty">该应用暂无 token。</p>
          <div v-if="issued" class="pt-issued">
            <p>⚠️ 明文仅此一次显示，请立即复制：</p>
            <code>{{ issued.token }}</code>
            <button class="tes-btn-primary sm" @click="copyText(issued.token)">{{ copied ? '已复制' : '复制' }}</button>
          </div>
          <div v-else-if="issueForm" class="pt-inline-form">
            <label>档位
              <select v-model.number="issueForm.tier">
                <option :value="1">1 个月</option><option :value="3">3 个月</option>
                <option :value="6">6 个月</option><option :value="12">12 个月</option>
              </select>
            </label>
            <label>QPM（可选）<input v-model.number="issueForm.rate" type="number" /></label>
            <button class="tes-btn-primary sm" :disabled="busy" @click="issueTok">签发</button>
            <button class="ghost sm" @click="issueForm = null">取消</button>
          </div>
        </section>

        <!-- 主题 -->
        <section v-else-if="view === 'topics'" class="tes-card">
          <div class="cw-head"><b>推送主题</b>
            <select v-model.number="curApp2" @change="loadTopics">
              <option v-for="a in apps" :key="a.app_id" :value="a.app_id">{{ a.name }}</option>
            </select>
            <span class="cw-grow"></span>
            <button v-if="isDevOp" class="tes-btn-primary sm" :disabled="!curApp2" @click="topicForm = emptyTopic()">+ 注册主题</button>
          </div>
          <table class="tes-table" v-if="topics.length">
            <thead><tr><th>code</th><th>名称</th><th>默认渠道</th><th>日频控</th><th>状态</th><th v-if="isDevOp">操作</th></tr></thead>
            <tbody>
              <tr v-for="t in topics" :key="t.topic_id">
                <td><code>{{ t.topic_code }}</code></td><td>{{ t.name }}</td>
                <td><code>{{ (t.default_channels || []).join(' + ') }}</code></td>
                <td>{{ t.rate_limit_per_day }}</td>
                <td><span class="tes-pill" :class="t.enabled ? 'success' : 'danger'">{{ t.enabled ? '启用' : '停用' }}</span></td>
                <td v-if="isDevOp"><button class="ghost" :disabled="busy" @click="disableTopic(t)">停用</button></td>
              </tr>
            </tbody>
          </table>
          <p v-else class="tes-empty">暂无主题（send 必须带已注册主题，未注册主题会被 422 拒发）。</p>
          <div v-if="topicForm" class="pt-inline-form wrap">
            <label>topic_code<input v-model.trim="topicForm.topic_code" placeholder="^[a-z][a-z0-9_]{1,63}$" /></label>
            <label>名称<input v-model.trim="topicForm.name" /></label>
            <label><input type="checkbox" value="app_offline" v-model="topicForm.channels" />app_offline</label>
            <label><input type="checkbox" value="wechat_oa" v-model="topicForm.channels" />wechat_oa</label>
            <label>默认TTL秒<input v-model.number="topicForm.default_ttl" type="number" placeholder="空=7天" /></label>
            <label>日频控<input v-model.number="topicForm.rate_limit_per_day" type="number" /></label>
            <button class="tes-btn-primary sm" :disabled="busy || !topicForm.topic_code || !topicForm.name || !topicForm.channels.length" @click="createTopic">注册</button>
            <button class="ghost sm" @click="topicForm = null">取消</button>
          </div>
        </section>

        <!-- 消息 -->
        <section v-else-if="view === 'messages'" class="tes-card">
          <div class="cw-head"><b>消息记录</b>
            <select v-model.number="curApp3" @change="loadMsgs">
              <option v-for="a in apps" :key="a.app_id" :value="a.app_id">{{ a.name }}</option>
            </select>
            <label class="cw-filter">状态
              <select v-model="msgStatus" @change="loadMsgs"><option value="">全部</option>
                <option v-for="s in ['pending','sent','partial','expired','dead']" :key="s" :value="s">{{ s }}</option>
              </select>
            </label>
          </div>
          <table class="tes-table" v-if="msgs.length">
            <thead><tr><th>msg_id</th><th>主题</th><th>接收人</th><th>标题</th><th>状态</th><th>时间</th></tr></thead>
            <tbody>
              <tr v-for="m in msgs" :key="m.msg_id">
                <td class="mono">#{{ m.msg_id.slice(0, 8) }}</td>
                <td><code>{{ m.topic_code }}</code></td>
                <td class="mono">{{ short(m.receiver) }}</td>
                <td>{{ m.title }}</td>
                <td><span class="tes-pill" :class="mPill(m.status)">{{ m.status }}</span></td>
                <td>{{ fmt(m.created_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="tes-empty">暂无消息。</p>
          <div class="pg-bar"><span>共 {{ msgTotal }} 条（最近 {{ msgs.length }} 条）</span></div>
        </section>

        <!-- 接入帮助 -->
        <section v-else class="tes-card">
          <h3>接入指南（3 行代码）</h3>
          <p class="cw-hint">在「推送 token」页签发后，接入方服务器直接调用（有效期到期自动 401，续期即换发新 token）：</p>
          <pre class="pt-code">curl -X POST {{ origin }}/api/push/open/send \
  -H "Authorization: Bearer pth-你的token" \
  -H "Content-Type: application/json" \
  -d '{
    "topic_code": "homework",
    "receiver": "终端用户ID（app_offline）或 pushplus 令牌（wechat_oa）",
    "title": "标题",
    "body": "内容",
    "ttl_seconds": 86400
  }'

# 自查 token 剩余有效期：
curl {{ origin }}/api/push/open/token-info -H "Authorization: Bearer pth-你的token"</pre>
          <table class="tes-table">
            <thead><tr><th>返回码</th><th>含义</th></tr></thead>
            <tbody>
              <tr><td><code>200 accepted</code></td><td>已受理（异步分发，看消息记录页确认送达）</td></tr>
              <tr><td><code>200 duplicate</code></td><td>msg_id 重复（幂等吞掉，不会重发）</td></tr>
              <tr><td><code>401 TOKEN_EXPIRED</code></td><td>token 过期，续期换发</td></tr>
              <tr><td><code>422 TOPIC_NOT_REGISTERED</code></td><td>主题未注册，先到主题页注册</td></tr>
              <tr><td><code>429</code></td><td>超过 token/应用/主题任一层频控</td></tr>
            </tbody>
          </table>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  portalRegister, portalLogin, portalMe,
  portalListApps, portalCreateApp, portalDisableApp,
  portalIssueToken, portalListTokens, portalRevokeToken,
  portalListTopics, portalCreateTopic, portalDisableTopic,
  portalListMessages,
} from '../../api/pushPortal'

const LS = 'push_portal_session'
const session = ref(localStorage.getItem(LS) || '')
const tab = ref('login')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const error2 = ref('')
const msg2 = ref('')
const me = ref({})
const view = ref('me')
const origin = location.origin

const views = [
  { k: 'me', label: '我的账号' },
  { k: 'apps', label: '接入应用' },
  { k: 'tokens', label: '推送 token' },
  { k: 'topics', label: '推送主题' },
  { k: 'messages', label: '消息记录' },
  { k: 'help', label: '接入指南' },
]

const isDev = computed(() => (me.value.roles || []).includes('developer'))
const isDevOp = computed(() => (me.value.roles || []).some((r) => r === 'developer' || r === 'operator'))

const apps = ref([])
const appForm = ref(null)
const tokens = ref([])
const curApp = ref(null)
const issueForm = ref(null)
const issued = ref(null)
const copied = ref(false)
const topics = ref([])
const curApp2 = ref(null)
const topicForm = ref(null)
const msgs = ref([])
const msgTotal = ref(0)
const curApp3 = ref(null)
const msgStatus = ref('')

const loginForm = reactive({ email: '', password: '' })
const regForm = reactive({ email: '', password: '', org_name: '', phone: '' })

function brief(e) {
  const d = e?.response?.data?.detail
  if (d && typeof d === 'object') return d.message || JSON.stringify(d)
  return d || e?.message || '操作失败'
}
function fmt(iso) { return iso ? String(iso).replace('T', ' ').slice(0, 16) : '—' }
function short(s) { return s && s.length > 14 ? `${s.slice(0, 8)}…${s.slice(-4)}` : s || '—' }
function mPill(s) {
  if (s === 'sent') return 'success'
  if (s === 'dead' || s === 'expired') return 'danger'
  if (s === 'partial') return 'info'
  return 'plain'
}
function emptyTopic() { return { topic_code: '', name: '', channels: ['app_offline'], default_ttl: null, rate_limit_per_day: 10000 } }
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); copied.value = true } catch { msg2.value = '复制失败，请手动选择复制' }
}

async function doLogin() {
  busy.value = true; error.value = ''
  try {
    const r = await portalLogin(loginForm.email, loginForm.password)
    localStorage.setItem(LS, r.access_token)
    session.value = r.access_token
    await boot()
  } catch (e) {
    error.value = brief(e)
  } finally { busy.value = false }
}

async function doRegister() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const r = await portalRegister({ ...regForm })
    notice.value = r.message || '注册成功，等待审批'
    tab.value = 'login'
  } catch (e) {
    error.value = brief(e)
  } finally { busy.value = false }
}

function logout() {
  localStorage.removeItem(LS)
  session.value = ''
  me.value = {}
}

async function boot() {
  error2.value = ''
  try {
    me.value = await portalMe()
    apps.value = await portalListApps()
    if (apps.value.length) {
      curApp.value = curApp.value || apps.value[0].app_id
      curApp2.value = curApp2.value || apps.value[0].app_id
      curApp3.value = curApp3.value || apps.value[0].app_id
    }
    await refreshView()
  } catch (e) {
    error2.value = brief(e)
  }
}

async function refreshView() {
  if (view.value === 'tokens' && curApp.value) await loadTokens()
  else if (view.value === 'topics' && curApp2.value) await loadTopics()
  else if (view.value === 'messages' && curApp3.value) await loadMsgs()
}
function switchView(k) { view.value = k; error2.value = ''; msg2.value = ''; refreshView() }

async function createApp() {
  busy.value = true
  try {
    const r = await portalCreateApp({ name: appForm.value.name, allowed_channels: appForm.value.channels, rate_limit_per_min: appForm.value.rate_limit_per_min })
    msg2.value = `应用 #${r.app_id} 已创建`
    appForm.value = null
    await boot()
  } catch (e) { error2.value = brief(e) } finally { busy.value = false }
}
async function disableApp(a) {
  if (!window.confirm(`禁用应用「${a.name}」？其 token 将即时全部失效。`)) return
  busy.value = true
  try { await portalDisableApp(a.app_id); await boot() } catch (e) { error2.value = brief(e) } finally { busy.value = false }
}

async function loadTokens() {
  try { tokens.value = curApp.value ? await portalListTokens(curApp.value) : [] } catch (e) { error2.value = brief(e) }
}
async function issueTok() {
  busy.value = true
  try {
    issued.value = await portalIssueToken(curApp.value, issueForm.value.tier, issueForm.value.rate || null)
    copied.value = false
    issueForm.value = null
    await loadTokens()
  } catch (e) { error2.value = brief(e) } finally { busy.value = false }
}
async function revokeTok(t) {
  if (!window.confirm(`吊销 ${t.token_prefix}…？接入方立即 401。`)) return
  busy.value = true
  try { await portalRevokeToken(t.token_id); await loadTokens() } catch (e) { error2.value = brief(e) } finally { busy.value = false }
}

async function loadTopics() {
  try { topics.value = curApp2.value ? await portalListTopics(curApp2.value) : [] } catch (e) { error2.value = brief(e) }
}
async function createTopic() {
  busy.value = true
  try {
    const f = topicForm.value
    await portalCreateTopic(curApp2.value, {
      topic_code: f.topic_code, name: f.name, default_channels: f.channels,
      default_ttl: f.default_ttl || null, rate_limit_per_day: f.rate_limit_per_day, force_channels: [],
    })
    msg2.value = `主题 ${f.topic_code} 已注册`
    topicForm.value = null
    await loadTopics()
  } catch (e) { error2.value = brief(e) } finally { busy.value = false }
}
async function disableTopic(t) {
  busy.value = true
  try { await portalDisableTopic(t.topic_id); await loadTopics() } catch (e) { error2.value = brief(e) } finally { busy.value = false }
}

async function loadMsgs() {
  try {
    if (!curApp3.value) { msgs.value = []; return }
    const params = { page: 1, size: 50 }
    if (msgStatus.value) params.status = msgStatus.value
    const r = await portalListMessages(curApp3.value, params)
    msgs.value = r.items || []
    msgTotal.value = r.total || 0
  } catch (e) { error2.value = brief(e) }
}

onMounted(async () => { if (session.value) await boot() })
</script>

<style scoped>
.pt-gate { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #fdf4fb, #fff6f2); }
.pt-gate-card { width: min(420px, 92vw); background: #fff; border: 1px solid var(--line); border-radius: var(--r-lg); box-shadow: var(--shadow); padding: 30px 28px; }
.pt-logo { display: flex; align-items: center; gap: 10px; font-size: 17px; }
.pt-logo-mark { width: 30px; height: 30px; border-radius: 9px; background: var(--brand-grad); color: #fff; display: inline-flex; align-items: center; justify-content: center; font-weight: 800; }
.pt-sub { color: var(--muted); font-size: 13px; margin: 8px 0 16px; }
.pt-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.pt-tabs button { flex: 1; padding: 8px 0; border-radius: var(--r-sm); border: 1.5px solid var(--line-strong); background: #fff; cursor: pointer; }
.pt-tabs button.on { border-color: var(--brand-2); color: var(--brand-2); background: var(--brand-grad-soft); font-weight: 700; }
.pt-gate-card form { display: flex; flex-direction: column; gap: 10px; }
.full { width: 100%; margin-top: 4px; }

.pt-shell { min-height: 100vh; display: flex; flex-direction: column; }
.pt-head { display: flex; align-items: center; gap: 14px; padding: 0 22px; height: 58px; background: #fff; border-bottom: 1px solid var(--line); }
.pt-roles, .pt-mail { font-size: 13px; color: var(--muted); }
.pt-body { flex: 1; display: flex; }
.pt-side { width: 170px; background: #fff; border-right: 1px solid var(--line); padding: 14px 10px; display: flex; flex-direction: column; gap: 4px; }
.pt-side-item { text-align: left; border: 0; background: none; padding: 9px 12px; border-radius: var(--r-sm); cursor: pointer; font-size: 14px; color: var(--ink-2); }
.pt-side-item.on { background: var(--brand-grad-soft); color: var(--brand-2); font-weight: 700; }
.pt-main { flex: 1; padding: 18px 22px; overflow: auto; }
.pt-inline-form { display: flex; gap: 10px; align-items: flex-end; margin-top: 12px; flex-wrap: wrap; }
.pt-inline-form label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.pt-inline-form.wrap label { min-width: 120px; }
.pt-issued { margin-top: 12px; }
.pt-issued code { display: block; background: #0f172a; color: #7dd3fc; padding: 10px 12px; border-radius: 8px; word-break: break-all; margin: 6px 0; }
.pt-code { background: #0f172a; color: #e2e8f0; padding: 14px; border-radius: 10px; overflow: auto; font-size: 12.5px; line-height: 1.6; }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; }
.sm { padding: 4px 12px; font-size: 13px; }
.pg-bar { text-align: right; font-size: 13px; color: var(--muted); margin-top: 8px; }
.cw-filter { display: inline-flex; align-items: center; gap: 6px; }
</style>
