<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">异常队列</h2>
    <p class="cw-hint">
      入库三闸（去重 / 答案自洽 / 双源）挡下的题集中在这里，附<strong>命中哪道闸</strong>与<strong>具体原因</strong>。
      <b>放行</b> = 进入常规审核（draft）；<b>发布</b> = 确认无误直接进卷池（答案不合规会被拒）；
      <b>归档</b> = 确认重复或脏题，保留数据但移出队列（不删题，删除请走数据治理）。
      勾选题目可批量处置；<b>真题重复组</b>页只摆出候选、不自动改动已发布题，留哪条由人工逐组点选。
    </p>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card board">
      <div class="board-head">
        <b>队列总览</b>
        <span class="cw-grow"></span>
        <button class="ghost sm" :disabled="busy" @click="loadStats">刷新看板</button>
      </div>
      <div class="kpi-grid">
        <div class="kpi tes-card">
          <span class="kpi-label">隔离待处置</span>
          <b class="kpi-num bad">{{ quarantineTotal }}</b>
        </div>
        <div class="kpi tes-card">
          <span class="kpi-label">打标待人工</span>
          <b class="kpi-num warn">{{ flaggedTotal }}</b>
        </div>
        <div class="kpi tes-card">
          <span class="kpi-label">已归档</span>
          <b class="kpi-num">{{ archivedTotal }}</b>
        </div>
      </div>
      <p v-if="gateTags.length" class="queue-line">
        按闸：
        <span v-for="g in gateTags" :key="g.key" class="queue-tag">{{ g.label }} {{ g.n }}</span>
      </p>
      <p v-if="chainTags.length" class="queue-line">
        按来源：
        <span v-for="c in chainTags" :key="c.key" class="queue-tag">{{ c.label }} {{ c.n }}</span>
      </p>
    </section>

    <section class="tes-card">
      <div class="board-head tabs">
        <button
          v-for="t in TABS" :key="t.value"
          class="tab" :class="{ on: kind === t.value }"
          :disabled="busy" @click="switchKind(t.value)"
        >{{ t.label }}</button>
        <span class="cw-grow"></span>
        <template v-if="kind !== 'dupgroups'">
          <select v-model="gate" aria-label="按闸筛选" @change="load">
            <option value="">全部闸</option>
            <option v-for="g in gateOptions" :key="g.value" :value="g.value">{{ g.label }}</option>
          </select>
          <input v-model.trim="keyword" class="kw" placeholder="题干关键词" @keyup.enter="load" />
          <button class="tes-btn-primary sm" :disabled="busy" @click="load">查询</button>
        </template>
      </div>

      <div v-if="kind !== 'dupgroups' && selected.length" class="batch-bar">
        <span>已选 {{ selected.length }} 条</span>
        <template v-if="!batchAction">
          <button class="ghost sm" :disabled="busy" @click="askBatch('release')">批量放行</button>
          <button class="ghost sm" :disabled="busy" @click="askBatch('publish')">批量发布</button>
          <button class="ghost sm danger" :disabled="busy" @click="askBatch('archive')">批量归档</button>
          <button class="ghost sm" :disabled="busy" @click="selected = []">清空选择</button>
        </template>
        <template v-else>
          <span class="confirm-tip">{{ batchTip }}</span>
          <button class="tes-btn-primary sm" :disabled="busy" @click="doBatch">确认批量处置</button>
          <button class="ghost sm" :disabled="busy" @click="batchAction = ''">取消</button>
        </template>
      </div>

      <table v-if="kind === 'dupgroups' && groups.length" class="tes-table">
        <thead>
          <tr><th>重复组</th><th>组内题目（人工指定保留项）</th><th>操作</th></tr>
        </thead>
        <tbody v-for="g in groups" :key="g.group_key">
          <tr>
            <td class="q-cell">
              <div class="stem">{{ g.stem }}</div>
              <div class="meta">{{ g.subject }} · {{ g.year || '年份未知' }} · 同指纹 {{ g.size }} 条</div>
              <div class="meta dup">{{ g.group_key }}</div>
            </td>
            <td>
              <div v-for="m in g.members" :key="m.id" class="member">
                <b>#{{ m.id }}</b> {{ m.stem }}
                <span class="meta">（{{ m.source_tag || '—' }} · {{ m.status }}）</span>
              </div>
            </td>
            <td class="ops">
              <template v-if="groupConfirm.key === g.group_key">
                <span class="confirm-tip">
                  {{ groupConfirm.keepId ? `保留 #${groupConfirm.keepId}，其余归档（数据保留）？` : '确认这几条都不是重复？' }}
                </span>
                <button class="tes-btn-primary sm" :disabled="busy" @click="doGroupResolve(g.group_key)">确认</button>
                <button class="ghost sm" :disabled="busy" @click="cancelGroupConfirm">取消</button>
              </template>
              <template v-else>
                <button
                  v-for="m in g.members" :key="m.id"
                  class="ghost sm" :disabled="busy"
                  @click="askGroupConfirm(g.group_key, m.id)"
                >保留 #{{ m.id }}</button>
                <button class="ghost sm" :disabled="busy" @click="askGroupConfirm(g.group_key, null)">全部保留</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>

      <table v-else-if="kind !== 'dupgroups' && rows.length" class="tes-table">
        <thead>
          <tr>
            <th v-if="kind !== 'archived'"><input type="checkbox" aria-label="全选" :checked="allSelected" @change="toggleAll" /></th>
            <th>题目</th><th>命中</th><th>原因</th><th>来源</th>
            <th v-if="kind !== 'archived'">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td v-if="kind !== 'archived'">
              <input type="checkbox" :aria-label="`选择题目 ${r.id}`" :checked="selected.includes(r.id)" @change="toggle(r.id)" />
            </td>
            <td class="q-cell">
              <div class="stem" :title="r.stem">{{ r.stem }}</div>
              <div class="meta">
                #{{ r.id }} · {{ r.subject }} / {{ r.grade || '-' }} · {{ r.type }} · 答案
                <b>{{ r.answer || '（空）' }}</b>
              </div>
            </td>
            <td>
              <span class="pill" :class="gateClass(r.guard_gate)">
                {{ r.guard_gate_label || r.guard_gate || '—' }}
              </span>
            </td>
            <td class="reason" :title="r.guard_reason || ''">{{ r.guard_reason || '—' }}</td>
            <td>
              <span class="pill info">{{ chainLabel(r.chain) }}</span>
              <div class="meta">{{ r.source_tag || '—' }}</div>
              <div v-if="r.dup_of" class="meta dup">与 #{{ r.dup_of }} 重复</div>
            </td>
            <td v-if="kind !== 'archived'" class="ops">
              <template v-if="confirming.id === r.id">
                <span class="confirm-tip">{{ confirmTip }}</span>
                <button class="tes-btn-primary sm" :disabled="busy" @click="doResolve(r, confirming.action)">
                  确认
                </button>
                <button class="ghost sm" :disabled="busy" @click="cancelConfirm">取消</button>
              </template>
              <template v-else>
                <button class="ghost sm" :disabled="busy" @click="doResolve(r, 'release')">放行</button>
                <button
                  v-if="r.status === 'quarantine'"
                  class="ghost sm" :disabled="busy" @click="askConfirm(r, 'publish')"
                >发布</button>
                <button class="ghost sm danger" :disabled="busy" @click="askConfirm(r, 'archive')">归档</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="kind === 'dupgroups' && loaded && !error" class="tes-empty">当前没有待人工裁决的重复组。</p>
      <p v-else-if="loaded && !error" class="tes-empty">{{ emptyText }}</p>
      <p v-else-if="!error" class="tes-empty">点击「查询」查看队列。</p>
      <p v-if="loaded && !error" class="hint">共 {{ kind === 'dupgroups' ? groups.length : rows.length }} 条</p>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  getQuarantineStats, listQuarantineItems, resolveQuarantineItem,
  batchResolveQuarantineItems, listDuplicateGroups, resolveDuplicateGroup,
} from '../api/admin'

const TABS = [
  { value: 'quarantine', label: '隔离待处置' },
  { value: 'flagged', label: '打标待人工' },
  { value: 'archived', label: '已归档' },
  { value: 'dupgroups', label: '真题重复组' },
]

const CHAIN_LABEL = { crawl: '采集', teacher: '教师录题', import: '批量导入', other: '其他' }
// 与后端 GATE_LABEL 一致；stats 未返回时用这份兜底，避免筛选下拉空白
const GATE_FALLBACK = {
  dedup_exact: '内容哈希重复',
  dedup_text: '题干指纹重复',
  missing_answer: '答案缺失',
  answer_out_of_range: '选项越界',
  mechanical_mismatch: '代入不自洽',
  dual_source_conflict: '双源答案冲突',
  choice_no_second_source: '选择题无第二来源',
  numeric_no_reference: '无参照答案可比对',
  dedup_semantic: '语义近似重复（待人工裁决）',
}

const rows = ref([])
const groups = ref([])
const stats = ref(null)
const kind = ref('quarantine')
const gate = ref('')
const keyword = ref('')
const error = ref('')
const msg = ref('')
const busy = ref(false)
const loaded = ref(false)
const confirming = ref({ id: null, action: '' })
const selected = ref([])
const batchAction = ref('')
const groupConfirm = ref({ key: '', keepId: null })

const quarantineTotal = computed(() => stats.value?.quarantine_total ?? 0)
const flaggedTotal = computed(() => stats.value?.flagged_total ?? 0)
const archivedTotal = computed(() => stats.value?.archived_total ?? 0)

const gateLabels = computed(() => stats.value?.gate_labels || GATE_FALLBACK)

const gateTags = computed(() => Object.entries(stats.value?.by_gate || {})
  .map(([key, n]) => ({ key, n, label: gateLabels.value[key] || key })))

const chainTags = computed(() => Object.entries(stats.value?.by_chain || {})
  .map(([key, n]) => ({ key, n, label: CHAIN_LABEL[key] || key })))

// 筛选项取「看板里实际出现过的闸」；看板为空时给全量兜底，避免下拉只有「全部闸」
const gateOptions = computed(() => {
  const seen = Object.keys(stats.value?.by_gate || {})
  const keys = seen.length ? seen : Object.keys(gateLabels.value)
  return keys.map((k) => ({ value: k, label: gateLabels.value[k] || k }))
})

const emptyText = computed(() => {
  if (kind.value === 'quarantine') return '当前没有待处置的隔离题。'
  if (kind.value === 'flagged') return '当前没有仅打标待人工确认的题。'
  return '还没有归档的题。'
})

const confirmTip = computed(() => (
  confirming.value.action === 'publish' ? '确认发布进卷池？' : '确认归档移出队列？'
))

const allSelected = computed(() => (
  rows.value.length > 0 && rows.value.every((r) => selected.value.includes(r.id))
))

const batchTip = computed(() => ({
  release: '确认批量放行（进入常规审核）？',
  publish: '确认批量发布（答案不合规的会被跳过）？',
  archive: '确认批量归档（保留数据、移出队列）？',
}[batchAction.value] || ''))

function chainLabel(c) { return CHAIN_LABEL[c] || c || '—' }
function gateClass(g) {
  if (!g) return 'info'
  if (g.startsWith('dedup_')) return 'warn'
  if (g === 'dual_source_conflict' || g === 'dedup_exact') return 'bad'
  return 'info'
}

function messageOf(err) {
  const code = err?.response?.status
  const detail = err?.response?.data?.detail
  if (code === 403) return '权限不足（403）：当前角色无权处置异常队列'
  if (code === 401) return '登录已失效，请重新登录'
  if (code === 409) return `该题状态已变化（409）：${detail || '可能已被处置'}`
  return `${code ? `（${code}）` : ''}${detail || err?.message || '未知错误'}`
}

async function loadStats() {
  try {
    stats.value = await getQuarantineStats()
  } catch (err) {
    error.value = `看板加载失败：${messageOf(err)}`
  }
}

async function load() {
  busy.value = true
  error.value = ''
  loaded.value = false
  confirming.value = { id: null, action: '' }
  selected.value = []
  batchAction.value = ''
  try {
    const params = { kind: kind.value }
    if (gate.value) params.gate = gate.value
    if (keyword.value) params.q = keyword.value
    rows.value = await listQuarantineItems(params)
  } catch (err) {
    // 403/500 必须如实提示，不能伪装成「暂无」（合规页踩过的坑）
    rows.value = []
    error.value = `加载失败：${messageOf(err)}`
  } finally {
    loaded.value = true
    busy.value = false
  }
}

async function loadGroups() {
  busy.value = true
  error.value = ''
  loaded.value = false
  groups.value = []
  try {
    groups.value = await listDuplicateGroups()
  } catch (err) {
    error.value = `重复组加载失败：${messageOf(err)}`
  } finally {
    loaded.value = true
    busy.value = false
  }
}

function switchKind(value) {
  if (kind.value === value) return
  kind.value = value
  gate.value = ''
  selected.value = []
  batchAction.value = ''
  groupConfirm.value = { key: '', keepId: null }
  if (value === 'dupgroups') loadGroups()
  else load()
}

function toggle(id) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((x) => x !== id)
    : [...selected.value, id]
}

function toggleAll() {
  selected.value = allSelected.value ? [] : rows.value.map((r) => r.id)
}

function askBatch(action) {
  batchAction.value = action
}

async function doBatch() {
  const action = batchAction.value
  busy.value = true
  error.value = ''
  msg.value = ''
  try {
    const res = await batchResolveQuarantineItems(selected.value, action)
    const tail = {
      release: '已放行', publish: '已发布', archive: '已归档',
    }[action]
    const skipped = res?.skipped || []
    msg.value = `批量${tail} ${res?.succeeded ?? 0}/${res?.total ?? 0} 条` +
      (skipped.length ? `；${skipped.length} 条未处理（${skipped[0]?.detail || '状态已变化'}）` : '')
    selected.value = []
    batchAction.value = ''
    await Promise.all([load(), loadStats()])
  } catch (err) {
    error.value = `批量处置失败：${messageOf(err)}`
  } finally {
    busy.value = false
  }
}

function askGroupConfirm(key, keepId) {
  groupConfirm.value = { key, keepId }
}

function cancelGroupConfirm() {
  groupConfirm.value = { key: '', keepId: null }
}

async function doGroupResolve(groupKey) {
  const { keepId } = groupConfirm.value
  busy.value = true
  error.value = ''
  msg.value = ''
  try {
    const res = await resolveDuplicateGroup(groupKey, keepId ? 'keep_one' : 'keep_all', keepId)
    msg.value = keepId
      ? `已保留 #${keepId}，归档 ${(res?.archived_ids || []).length} 条（数据均已保留）`
      : '已确认该组全部保留，未改动任何题目'
    groupConfirm.value = { key: '', keepId: null }
    await loadGroups()
  } catch (err) {
    error.value = `重复组裁决失败：${messageOf(err)}`
  } finally {
    busy.value = false
  }
}

function askConfirm(row, action) {
  confirming.value = { id: row.id, action }
}

function cancelConfirm() {
  confirming.value = { id: null, action: '' }
}

async function doResolve(row, action) {
  busy.value = true
  error.value = ''
  msg.value = ''
  try {
    const res = await resolveQuarantineItem(row.id, action)
    const tail = action === 'archive'
      ? '已归档：数据保留，移出队列'
      : (action === 'publish' ? '已发布：进入卷池' : '已放行：进入常规审核（draft）')
    msg.value = `#${res?.id ?? row.id} ${tail}`
    confirming.value = { id: null, action: '' }
    await Promise.all([load(), loadStats()])
  } catch (err) {
    error.value = `处置失败：${messageOf(err)}`
  } finally {
    busy.value = false
  }
}

onMounted(() => { load(); loadStats() })
</script>

<style scoped>
.cw-hint { font-size: 12px; color: var(--muted, #888); line-height: 1.6; }
.board { margin-bottom: 14px; padding: 14px 16px; }
.board-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.cw-grow { flex: 1; }
.kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.kpi { padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.kpi-label { font-size: 11px; color: var(--muted, #888); }
.kpi-num { font-size: 22px; line-height: 1; color: var(--brand, #d6336c); }
.kpi-num.warn { color: #c77700; }
.kpi-num.bad { color: #c0392b; }
.queue-line { margin: 12px 0 0; font-size: 12px; color: var(--muted, #888); }
.queue-tag { display: inline-block; margin: 0 6px 4px 0; padding: 2px 8px; border-radius: 999px; background: #eef4ff; color: var(--accent); }
.tabs { gap: 6px; }
.tab { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px 12px; font-size: 13px; color: var(--muted, #888); cursor: pointer; }
.tab.on { background: #eef4ff; color: var(--accent); font-weight: 700; }
.kw { width: 150px; }
.q-cell { max-width: 420px; }
.stem { font-size: 13px; color: var(--ink, #222); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.meta { font-size: 11px; color: var(--muted, #888); margin-top: 2px; }
.meta.dup { color: #c77700; }
.reason { max-width: 260px; font-size: 12px; color: var(--ink-2, #444); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ops { white-space: nowrap; }
.batch-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 0 0 10px; padding: 8px 10px; border-radius: 8px; background: #f6f8fc; font-size: 12px; }
.member { font-size: 12px; color: var(--ink-2, #444); margin-bottom: 4px; }
.confirm-tip { font-size: 12px; color: #c77700; margin-right: 6px; }
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
