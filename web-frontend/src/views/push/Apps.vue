<template>
  <div class="cw-page">
    <h2 class="cw-title">接入应用</h2>
    <p class="cw-hint">
      平台自有应用（admin 代管）与第三方接入应用统一视图。第三方应用由其 portal 自助创建，
      此处只读监控 + 代发 token（联调/演示链路；生产业务走内部 emit）。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head"><b>应用列表</b><span class="cw-grow"></span>
        <button class="tes-btn-primary sm" @click="openCreate">+ 新建平台应用</button>
        <button class="ghost" :disabled="busy" @click="load">刷新</button>
      </div>
      <div class="cw-table-wrap" v-if="rows.length">
        <table class="tes-table">
          <thead>
            <tr><th>ID</th><th>名称</th><th>归属</th><th>渠道</th><th>频控</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="a in rows" :key="a.app_id">
              <td>{{ a.app_id }}</td>
              <td>{{ a.name }}</td>
              <td>
                <span v-if="a.owner_account_id" class="tes-pill info">第三方 {{ a.owner_email }}</span>
                <span v-else class="tes-pill plain">平台自有</span>
              </td>
              <td><code>{{ (a.allowed_channels || []).join(' + ') }}</code></td>
              <td>{{ a.rate_limit_per_min }}/分</td>
              <td><span class="tes-pill" :class="a.enabled ? 'success' : 'danger'">{{ a.enabled ? '启用' : '禁用' }}</span></td>
              <td class="cw-ops">
                <button class="ghost" @click="openTokens(a)">token</button>
                <button v-if="!a.owner_account_id" class="ghost" :disabled="busy" @click="openIssue(a)">签发 token</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="tes-empty">暂无应用。</p>
    </section>

    <!-- 新建平台应用 -->
    <div v-if="form" class="pv-mask" @click.self="form = null">
      <div class="pv-box">
        <div class="pv-head"><b>新建平台自有应用</b><span class="cw-grow"></span>
          <button class="ghost" @click="form = null">关闭</button>
        </div>
        <div class="rl-body">
          <label class="cw-col"><span>名称</span><input v-model.trim="form.name" maxlength="128" /></label>
          <label class="cw-col"><span>允许渠道</span>
            <span class="ck-row">
              <label><input type="checkbox" value="app_offline" v-model="form.channels" />app_offline</label>
              <label><input type="checkbox" value="wechat_oa" v-model="form.channels" />wechat_oa</label>
            </span>
          </label>
          <label class="cw-col"><span>频控（次/分）</span><input v-model.number="form.rate_limit_per_min" type="number" min="1" /></label>
          <button class="tes-btn-primary" :disabled="busy || !form.name || !form.channels.length" @click="create">创建</button>
        </div>
      </div>
    </div>

    <!-- 签发 token：档位四选一 + 明文一次性显示 -->
    <div v-if="issueFor" class="pv-mask" @click.self="issueFor = null">
      <div class="pv-box">
        <div class="pv-head"><b>签发推送 token — {{ issueFor.name }}</b><span class="cw-grow"></span>
          <button class="ghost" @click="closeIssue">关闭</button>
        </div>
        <div v-if="issued" class="rl-body">
          <p class="msg">⚠️ 明文仅此一次显示，请立即复制保存（旧 token 进入 7 天宽限并行）：</p>
          <code class="tk-plain">{{ issued.token }}</code>
          <p class="cw-hint">到期时间 {{ fmt(issued.expired_at) }}；接入方调用：
            <code>Authorization: Bearer &lt;token&gt;</code></p>
          <button class="tes-btn-primary" @click="copyIssued">{{ copied ? '已复制' : '复制 token' }}</button>
        </div>
        <div v-else class="rl-body">
          <label class="cw-col"><span>有效期档位</span>
            <select v-model.number="issueTier">
              <option :value="1">1 个月（联调试用）</option>
              <option :value="3">3 个月（短周期项目）</option>
              <option :value="6">6 个月（常规默认）</option>
              <option :value="12">12 个月（长期接入）</option>
            </select>
          </label>
          <label class="cw-col"><span>token 级 QPM（可选，留空沿用应用级）</span>
            <input v-model.number="issueRate" type="number" placeholder="如 30" />
          </label>
          <button class="tes-btn-primary" :disabled="busy" @click="issue">签发</button>
        </div>
      </div>
    </div>

    <!-- token 列表（前缀+状态，明文永不二次回显） -->
    <div v-if="tokenFor" class="pv-mask" @click.self="tokenFor = null">
      <div class="pv-box">
        <div class="pv-head"><b>{{ tokenFor.name }} 的 token</b><span class="cw-grow"></span>
          <button class="ghost" @click="tokenFor = null">关闭</button>
        </div>
        <div class="rl-body col">
          <table class="tes-table" v-if="tokens.length">
            <thead><tr><th>前缀</th><th>档位</th><th>状态</th><th>到期</th><th>最近使用</th><th></th></tr></thead>
            <tbody>
              <tr v-for="t in tokens" :key="t.token_id">
                <td class="mono">{{ t.token_prefix }}…</td>
                <td>{{ t.tier }} 月</td>
                <td><span class="tes-pill" :class="tPill(t.status)">{{ t.status }}</span></td>
                <td>{{ fmt(t.expired_at) }}</td>
                <td>{{ t.last_used_at ? fmt(t.last_used_at) : '从未' }}</td>
                <td>
                  <button v-if="t.status === 'active' || t.status === 'rotated'" class="ghost" :disabled="busy" @click="revoke(t)">吊销</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="tes-empty">该应用暂无 token。</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  listAllApps, createPlatformApp, issuePlatformToken,
  listAppTokens, revokePlatformToken,
} from '../../api/pushAdmin'

const rows = ref([])
const busy = ref(false)
const error = ref('')
const msg = ref('')

const form = ref(null)
const issueFor = ref(null)
const issueTier = ref(6)
const issueRate = ref(null)
const issued = ref(null)
const copied = ref(false)
const tokenFor = ref(null)
const tokens = ref([])

function fmt(iso) { return iso ? String(iso).replace('T', ' ').slice(0, 16) : '—' }
function brief(e) { return (e?.response?.data?.detail) || e?.message || '操作失败' }
function tPill(s) {
  if (s === 'active') return 'success'
  if (s === 'rotated') return 'info'
  return 'danger'
}

async function load() {
  error.value = ''
  busy.value = true
  try {
    rows.value = await listAllApps()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

function openCreate() {
  form.value = { name: '', channels: ['app_offline'], rate_limit_per_min: 60 }
}
async function create() {
  busy.value = true
  try {
    const r = await createPlatformApp({
      name: form.value.name,
      allowed_channels: form.value.channels,
      rate_limit_per_min: form.value.rate_limit_per_min,
    })
    msg.value = `平台应用 #${r.app_id} 已创建`
    form.value = null
    await load()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

function openIssue(a) {
  issueFor.value = a
  issueTier.value = 6
  issueRate.value = null
  issued.value = null
  copied.value = false
}
function closeIssue() {
  issueFor.value = null
  issued.value = null
}
async function issue() {
  busy.value = true
  try {
    issued.value = await issuePlatformToken(issueFor.value.app_id, issueTier.value, issueRate.value || null)
    await load()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}
async function copyIssued() {
  try {
    await navigator.clipboard.writeText(issued.value.token)
    copied.value = true
  } catch {
    msg.value = '复制失败，请手动选择文本复制'
  }
}

async function openTokens(a) {
  tokenFor.value = a
  tokens.value = []
  try {
    tokens.value = await listAppTokens(a.app_id)
  } catch (e) {
    error.value = brief(e)
  }
}
async function revoke(t) {
  if (!window.confirm(`吊销 ${t.token_prefix}…？接入方将立即 401。`)) return
  busy.value = true
  try {
    await revokePlatformToken(t.token_id)
    tokens.value = await listAppTokens(tokenFor.value.app_id)
    msg.value = 'token 已吊销（即时生效）'
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.cw-col { display: flex; flex-direction: column; gap: 4px; }
.rl-body { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; padding: 16px; }
.rl-body.col { flex-direction: column; align-items: stretch; }
.ck-row { display: flex; gap: 14px; }
.pv-mask { position: fixed; inset: 0; z-index: 50; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 16px; }
.pv-box { background: #fff; border-radius: 12px; width: min(680px, 94vw); max-height: 88vh; overflow: auto; box-shadow: 0 12px 40px rgba(0, 0, 0, .25); }
.pv-head { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid #eef2f7; }
.tk-plain { display: block; width: 100%; background: #0f172a; color: #7dd3fc; padding: 10px 12px; border-radius: 8px; font-size: 13.5px; word-break: break-all; }
.cw-table-wrap { overflow-x: auto; }
.cw-ops { white-space: nowrap; }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; }
.sm { padding: 4px 12px; font-size: 13px; }
</style>
