<template>
  <div class="cw-page">
    <h2 class="cw-title">消息查询</h2>
    <p class="cw-hint">
      全量消息投递记录（第三方消息此处仅展示<b>标题与状态</b>，正文走库内留痕不进列表接口）。
      <code>dead</code>/<code>expired</code> 可重发：重置 pending 再走一遍分发。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head">
        <b>筛选</b><span class="cw-grow"></span>
        <label class="cw-filter">应用
          <select v-model.number="f.app_id"><option :value="null">全部</option>
            <option v-for="a in apps" :key="a.app_id" :value="a.app_id">{{ a.name }}(#{{ a.app_id }})</option>
          </select>
        </label>
        <label class="cw-filter">主题<input v-model.trim="f.topic_code" placeholder="如 grade" /></label>
        <label class="cw-filter">接收人<input v-model.trim="f.receiver" placeholder="receiver" /></label>
        <label class="cw-filter">状态
          <select v-model="f.status"><option value="">全部</option>
            <option v-for="s in ['pending','sent','partial','expired','dead']" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <button class="tes-btn-primary sm" @click="reload">查询</button>
      </div>

      <div class="cw-table-wrap" v-if="rows.length">
        <table class="tes-table">
          <thead>
            <tr><th>msg_id</th><th>应用</th><th>主题</th><th>接收人</th><th>标题</th><th>状态</th><th>有效期</th><th>创建时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in rows" :key="m.msg_id">
              <td class="mono">#{{ m.msg_id.slice(0, 8) }}</td>
              <td>{{ appName(m.app_id) }}</td>
              <td><code>{{ m.topic_code }}</code></td>
              <td class="mono">{{ short(m.receiver) }}</td>
              <td><span class="cw-clip" :title="m.title">{{ m.title }}</span></td>
              <td><span class="tes-pill" :class="pill(m.status)">{{ m.status }}</span></td>
              <td>{{ m.expire_at ? fmt(m.expire_at) : '长期' }}</td>
              <td>{{ fmt(m.created_at) }}</td>
              <td class="cw-ops">
                <button
                  v-if="m.status === 'dead' || m.status === 'expired'"
                  class="ghost" :disabled="busy" @click="resend(m)"
                >重发</button>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="pg-bar">
          <button class="ghost" :disabled="page <= 1" @click="go(page - 1)">上一页</button>
          <span>第 {{ page }} 页 / 共 {{ total }} 条</span>
          <button class="ghost" :disabled="page * size >= total" @click="go(page + 1)">下一页</button>
        </div>
      </div>
      <p v-else class="tes-empty">无符合条件的消息。</p>
    </section>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { listAllApps, listPushMessages, resendPushMessage } from '../../api/pushAdmin'

const rows = ref([])
const apps = ref([])
const total = ref(0)
const page = ref(1)
const size = 20
const f = reactive({ app_id: null, topic_code: '', receiver: '', status: '' })
const busy = ref(false)
const error = ref('')
const msg = ref('')

function pill(s) {
  if (s === 'sent') return 'success'
  if (s === 'dead' || s === 'expired') return 'danger'
  if (s === 'partial') return 'info'
  return 'plain'
}
function fmt(iso) { return iso ? String(iso).replace('T', ' ').slice(0, 16) : '—' }
function short(s) { return s && s.length > 14 ? `${s.slice(0, 8)}…${s.slice(-4)}` : s || '—' }
function appName(id) { return apps.value.find(a => a.app_id === id)?.name || `#${id}` }
function brief(e) { return (e?.response?.data?.detail) || e?.message || '操作失败' }

async function reload() {
  error.value = ''
  busy.value = true
  try {
    const params = { page: page.value, size }
    if (f.app_id) params.app_id = f.app_id
    if (f.topic_code) params.topic_code = f.topic_code
    if (f.receiver) params.receiver = f.receiver
    if (f.status) params.status = f.status
    const data = await listPushMessages(params)
    rows.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}
function go(p) { page.value = p; reload() }

async function resend(m) {
  busy.value = true
  try {
    const r = await resendPushMessage(m.msg_id)
    msg.value = `已重发 ${short(m.msg_id)} → ${r.status}`
    await reload()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try { apps.value = await listAllApps() } catch { /* 列表失败不拦消息查询 */ }
  await reload()
})
</script>

<style scoped>
.cw-filter { display: inline-flex; align-items: center; gap: 6px; }
.cw-table-wrap { overflow-x: auto; }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; }
.cw-clip { display: block; max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cw-ops { white-space: nowrap; }
.pg-bar { display: flex; align-items: center; gap: 12px; justify-content: flex-end; margin-top: 10px; font-size: 13px; color: var(--muted); }
.sm { padding: 4px 12px; font-size: 13px; }
</style>
