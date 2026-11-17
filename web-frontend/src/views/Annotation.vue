<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">题库标注待校</h2>
    <p class="cw-hint">
      LLM 预标建议先进待校队列，人工确认后才写回题库。
      <b>通过</b> = 写回 <code>course_kb</code> 对应字段并留痕；<b>驳回</b> = 丢弃建议、不动题库。
    </p>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <!-- 产能看板：题库标注覆盖缺口 + 待校队列分布 -->
    <section class="tes-card board">
      <div class="board-head">
        <b>产能看板</b>
        <span class="cw-grow"></span>
        <button class="ghost sm" :disabled="busy" @click="loadStats">刷新看板</button>
      </div>
      <div v-if="stats" class="kpi-grid">
        <div class="kpi tes-card">
          <span class="kpi-label">已发布题</span>
          <b class="kpi-num">{{ stats.course_kb.published_total }}</b>
        </div>
        <div class="kpi tes-card">
          <span class="kpi-label">难度待补</span>
          <b class="kpi-num warn">{{ stats.course_kb.difficulty_gap }}</b>
        </div>
        <div class="kpi tes-card">
          <span class="kpi-label">年份待补</span>
          <b class="kpi-num warn">{{ stats.course_kb.year_gap }}</b>
        </div>
        <div class="kpi tes-card">
          <span class="kpi-label">卷别待补</span>
          <b class="kpi-num warn">{{ stats.course_kb.paper_type_gap }}</b>
        </div>
      </div>
      <p v-if="stats" class="queue-line">
        队列分布：
        <span v-for="(n, f) in queueFlat" :key="f" class="queue-tag">{{ fieldLabel(f) }} {{ n }}</span>
        <span v-if="!queueFlat.length">暂无建议</span>
      </p>
    </section>

    <!-- 待校队列 -->
    <section class="tes-card">
      <div class="board-head">
        <b>标注建议</b>
        <span class="cw-grow"></span>
        <select v-model="status" aria-label="审核状态" @change="load">
          <option value="pending">待校</option>
          <option value="approved">已通过</option>
          <option value="rejected">已驳回</option>
          <option value="">全部</option>
        </select>
        <select v-model="fieldName" aria-label="字段" @change="load">
          <option value="">全部字段</option>
          <option value="difficulty">难度</option>
          <option value="year">年份</option>
          <option value="paper_type">卷别</option>
        </select>
        <button class="tes-btn-primary sm" :disabled="busy" @click="load">加载</button>
      </div>

      <table v-if="rows.length" class="tes-table">
        <thead>
          <tr>
            <th>题目</th><th>字段</th><th>建议值</th><th>依据</th>
            <th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td class="q-cell">
              <div class="q-stem" :title="renderMathText(s.question_stem) || s.content_hash">
                <span v-html="renderMath(shortStem(s.question_stem) || shortHash(s.content_hash))"></span>
              </div>
              <div class="q-hash" :title="s.content_hash">{{ shortHash(s.content_hash) }}</div>
            </td>
            <td>{{ fieldLabel(s.field_name) }}</td>
            <td><b class="suggest" v-html="renderMath(s.suggested_value)"></b></td>
            <td class="reason" :title="renderMathText(s.reason || '')">{{ s.reason || '—' }}</td>
            <td>
              <span class="pill" :class="pillClass(s.status)">{{ statusLabel(s.status) }}</span>
              <span v-if="s.reviewed_by" class="reviewer"> by {{ s.reviewed_by }}</span>
            </td>
            <td>
              <template v-if="s.status === 'pending'">
                <button class="tes-btn-primary sm" :disabled="busy" @click="doReview(s, 'approved')">通过</button>
                <button class="ghost sm danger" :disabled="busy" @click="doReview(s, 'rejected')">驳回</button>
              </template>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="loaded && !error" class="tes-empty">该筛选条件下暂无标注建议。</p>
      <p v-else-if="!error" class="tes-empty">点击「加载」查看标注建议。</p>
      <p v-if="loaded && !error" class="hint">共 {{ rows.length }} 条</p>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { renderMath, renderMathText } from '../utils/math'
import {
  listAnnotationSuggestions, reviewAnnotationSuggestion, getAnnotationStats,
} from '../api/admin'

const rows = ref([])
const stats = ref(null)
const status = ref('pending')
const fieldName = ref('')
const error = ref('')
const msg = ref('')
const busy = ref(false)
const loaded = ref(false)

const FIELD_LABEL = { difficulty: '难度', year: '年份', paper_type: '卷别' }
const STATUS_LABEL = { pending: '待校', approved: '已通过', rejected: '已驳回' }

function fieldLabel(f) { return FIELD_LABEL[f] || f }
function statusLabel(s) { return STATUS_LABEL[s] || s }
function pillClass(s) { return s === 'pending' ? 'warn' : (s === 'rejected' ? 'bad' : 'info') }
function shortHash(h) { return h ? String(h).slice(0, 10) : '' }
// 题干摘要：压掉换行/多余空白，截前 60 字，悬停 title 看全文
function shortStem(stem) {
  if (!stem) return ''
  const oneLine = String(stem).replace(/\s+/g, ' ').trim()
  return oneLine.length > 60 ? `${oneLine.slice(0, 60)}…` : oneLine
}

// 队列分布扁平化：{ difficulty: {pending:3}, year:{approved:1} } → { '难度·待校':3, ... }
const queueFlat = computed(() => {
  const out = {}
  const queue = stats.value?.queue || {}
  for (const [field, byStatus] of Object.entries(queue)) {
    for (const [st, n] of Object.entries(byStatus)) {
      if (!n) continue
      out[`${fieldLabel(field)}·${statusLabel(st)}`] = n
    }
  }
  return out
})

function messageOf(err) {
  const code = err?.response?.status
  const detail = err?.response?.data?.detail
  if (code === 403) return '权限不足（403）：当前角色无权审核题库标注'
  if (code === 401) return '登录已失效，请重新登录'
  return `${code ? `（${code}）` : ''}${detail || err?.message || '未知错误'}`
}

async function loadStats() {
  try {
    stats.value = await getAnnotationStats()
  } catch (err) {
    error.value = `看板加载失败：${messageOf(err)}`
  }
}

async function load() {
  busy.value = true
  error.value = ''
  loaded.value = false
  try {
    const params = {}
    if (status.value) params.status = status.value
    if (fieldName.value) params.field_name = fieldName.value
    rows.value = await listAnnotationSuggestions(params)
  } catch (err) {
    // 403/500 必须如实提示，不能伪装成「暂无建议」（合规页踩过的坑）
    rows.value = []
    error.value = `加载失败：${messageOf(err)}`
  } finally {
    loaded.value = true
    busy.value = false
  }
}

async function doReview(item, decision) {
  busy.value = true
  error.value = ''
  msg.value = ''
  try {
    await reviewAnnotationSuggestion(item.id, decision)
    msg.value = decision === 'approved'
      ? `已通过：${fieldLabel(item.field_name)} → ${item.suggested_value}（写回题库）`
      : `已驳回：${fieldLabel(item.field_name)} 建议已丢弃，题库未改动`
    await Promise.all([load(), loadStats()])
  } catch (err) {
    error.value = `审核失败：${messageOf(err)}`
  } finally {
    busy.value = false
  }
}

onMounted(() => { load(); loadStats() })
</script>

<style scoped>
.cw-hint { font-size: 12px; color: var(--muted, #888); line-height: 1.6; }
.q-cell { max-width: 440px; min-width: 320px; }
.q-stem { font-size: 13px; color: var(--ink, #222); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.q-hash { font: 11px var(--font-mono, monospace); color: var(--muted, #888); margin-top: 3px; }
code { background: #f5f5f7; padding: 1px 5px; border-radius: 4px; font-size: 11px; }
.board { margin-bottom: 14px; padding: 14px 16px; }
.board-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.cw-grow { flex: 1; }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.kpi { padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.kpi-label { font-size: 11px; color: var(--muted, #888); }
.kpi-num { font-size: 22px; line-height: 1; color: var(--brand, #d6336c); }
.kpi-num.warn { color: #c77700; }
.queue-line { margin: 12px 0 0; font-size: 12px; color: var(--muted, #888); }
.queue-tag { display: inline-block; margin: 0 6px 4px 0; padding: 2px 8px; border-radius: 999px; background: #eef4ff; color: var(--accent); }
.suggest { color: var(--brand, #d6336c); }
.reason { max-width: 320px; font-size: 12px; color: var(--ink-2, #444); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.reviewer { font-size: 10px; color: var(--muted, #888); }
.muted { color: var(--muted, #888); }
.hint { font-size: 12px; color: var(--muted, #888); margin-top: 8px; }
.pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.pill.warn { background: #fff3da; color: #c77700; }
.pill.info { background: #eef4ff; color: var(--accent); }
.pill.bad { background: #ffeaea; color: #c0392b; }
.err { color: #c0392b; background: #fff1f0; border: 1px solid #ffd6d3; padding: 10px 12px; border-radius: 10px; font-size: 13px; }
.msg { color: #2f8f5b; background: #eefaf3; border: 1px solid #cdeedb; padding: 10px 12px; border-radius: 10px; font-size: 13px; }
button.sm { padding: 4px 12px; font-size: 12px; }
button.ghost { background: #fff; color: var(--brand-2, #d6336c); border: 1.5px solid var(--brand-2, #d6336c); border-radius: 8px; }
button.ghost.danger { color: #c0392b; border-color: #c0392b; }
button:disabled { opacity: .5; cursor: not-allowed; }
</style>
