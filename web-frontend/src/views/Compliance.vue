<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">合规审核</h2>

    <div class="tes-toolbar">
      <select v-model="status" @change="load">
        <option value="pending">待审核</option>
        <option value="approved">已通过</option>
        <option value="rejected">已驳回</option>
        <option value="">全部</option>
      </select>
      <button class="tes-btn-primary" @click="load">加载列表</button>
      <span v-if="loaded && !error" class="hint">共 {{ list.length }} 条</span>
    </div>

    <p v-if="error" class="err">{{ error }}</p>

    <ul v-if="list.length" class="list">
      <li v-for="c in list" :key="c.id" class="tes-card item">
        <div class="row">
          <span class="pill" :class="pillClass(c.status)">{{ statusLabel(c.status) }}</span>
          <b>{{ c.type }}</b>
          <span class="src">{{ sourceLabel(c.source) }}</span>
          <span class="time">{{ shortTime(c.created_at) }}</span>
        </div>
        <p class="content" :title="c.content">{{ c.content }}</p>
        <div v-if="c.status === 'pending'" class="ops">
          <button class="tes-btn-primary sm" :disabled="busying === c.id" @click="doReview(c, 'approved')">通过</button>
          <button class="ghost sm" :disabled="busying === c.id" @click="doReview(c, 'rejected')">驳回</button>
        </div>
        <p v-else-if="c.reviewed_by" class="reviewer">审核人 {{ c.reviewed_by }} · {{ shortTime(c.reviewed_at) }}</p>
      </li>
    </ul>
    <p v-else-if="loaded && !error" class="tes-empty">该状态下暂无审核内容。</p>
    <p v-else-if="!error" class="tes-empty">点击「加载列表」查看待审内容。</p>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listCompliance, reviewCompliance } from '../api/admin'

const list = ref([])
const status = ref('pending')
const error = ref('')
const loaded = ref(false)
const busying = ref(null)

const STATUS_LABEL = { pending: '待审核', approved: '已通过', rejected: '已驳回', reviewed: '已通过' }
const SOURCE_LABEL = { question: '教师录题', crawl: '采集入库', gateway: '网关内容安全', manual: '手工创建' }

function statusLabel(s) { return STATUS_LABEL[s] || s }
function sourceLabel(s) { return SOURCE_LABEL[s] || (s || '未知来源') }
function pillClass(s) { return s === 'pending' ? 'warn' : (s === 'rejected' ? 'bad' : 'info') }
function shortTime(t) { return t ? String(t).slice(0, 10) : '' }

function messageOf(err) {
  const code = err?.response?.status
  const detail = err?.response?.data?.detail
  if (code === 403) return '权限不足（403）：当前角色无权访问合规审核'
  if (code === 401) return '登录已失效，请重新登录'
  return `${code ? `（${code}）` : ''}${detail || err?.message || '未知错误'}`
}

async function load() {
  error.value = ''
  loaded.value = false
  try {
    list.value = await listCompliance(status.value || undefined)
  } catch (err) {
    // 关键修复：403/500 曾因未捕获变成 unhandled rejection，
    // 组件保持空态，页面显示「暂无待审核内容」——错误被伪装成没数据。
    list.value = []
    error.value = `加载失败：${messageOf(err)}`
  } finally {
    loaded.value = true
  }
}

async function doReview(item, next) {
  busying.value = item.id
  error.value = ''
  try {
    await reviewCompliance(item.id, next)
    await load()
  } catch (err) {
    error.value = `审核失败：${messageOf(err)}`
  } finally {
    busying.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.list { list-style: none; padding: 0; margin: 0; }
.item { display: block; margin-bottom: 10px; padding: 12px 14px; }
.row { display: flex; align-items: center; gap: 10px; }
.content {
  margin: 8px 0 0; font-size: 13px; line-height: 1.55; color: var(--ink-2, #444);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.src { font-size: 11px; color: var(--muted, #888); background: #f5f5f7; padding: 2px 8px; border-radius: 999px; }
.time { margin-left: auto; font-size: 11px; color: var(--muted, #888); }
.ops { display: flex; gap: 8px; margin-top: 10px; }
.reviewer { margin: 8px 0 0; font-size: 11px; color: var(--muted, #888); }
.hint { font-size: 12px; color: var(--muted, #888); }
.err { color: #c0392b; background: #fff1f0; border: 1px solid #ffd6d3; padding: 10px 12px; border-radius: 10px; font-size: 13px; }
.pill { flex-shrink: 0; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.pill.warn { background: #fff3da; color: #c77700; }
.pill.info { background: #eef4ff; color: var(--accent); }
.pill.bad { background: #ffeaea; color: #c0392b; }
button.sm { padding: 4px 14px; font-size: 12px; }
button.ghost { background: #fff; color: var(--brand-2, #d6336c); border: 1.5px solid var(--brand-2, #d6336c); border-radius: 8px; }
button:disabled { opacity: .5; cursor: not-allowed; }
</style>
