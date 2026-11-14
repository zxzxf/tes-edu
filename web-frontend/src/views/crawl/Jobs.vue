<template>
  <div class="cw-page">
    <h2 class="cw-title">采集任务</h2>
    <p class="cw-hint">
      一次运行会真实从课程源抓取并解析：<code>bank</code>/<code>file</code> 走结构化解析，
      <code>url</code>/<code>web</code> 走网页 LLM 抽取（无题页面返回 0 题并写明原因，不编造）。
      P1 建 <code>auto_crawl</code> 草稿卷，P2 只进题库；入库一律 <code>draft</code>。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head"><b>新建任务（M3-8 / M4-4）</b><span class="cw-grow"></span>
        <button class="ghost" :disabled="busy" @click="loadAll">刷新</button>
      </div>
      <div class="cw-row">
        <label><span>任务名</span><input v-model.trim="form.name" placeholder="每月新题采集" /></label>
        <label>
          <span>课程源</span>
          <select v-model.number="form.source_id">
            <option :value="null">不绑定</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>
        <label>
          <span>入库路径</span>
          <select v-model="form.path">
            <option value="P1">P1 整卷入库</option>
            <option value="P2">P2 拆题入库</option>
          </select>
        </label>
        <label><span>cron（可空）</span><input v-model.trim="form.schedule" placeholder="0 3 * * *" /></label>
        <label><span>预算上限</span><input v-model.number="form.budget_limit" type="number" min="1" /></label>
        <button class="tes-btn-primary" :disabled="busy" @click="add">新建</button>
      </div>
      <div class="cw-table-wrap" v-if="rows.length">
      <table class="tes-table">
        <colgroup>
          <col class="c-task" /><col class="c-kind" /><col class="c-src" /><col class="c-path" />
          <col class="c-status" /><col class="c-budget" /><col class="c-query" /><col class="c-paper" /><col class="c-ops" />
        </colgroup>
        <thead>
          <tr>
            <th>任务</th><th>类型</th><th>课程源</th><th>路径</th><th>状态</th>
            <th>预算</th><th>检索约束</th><th>最近产出卷</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in rows" :key="j.id">
            <td><span class="cw-clip task" :title="j.name">{{ j.name }}</span></td>
            <td>
              <span class="tes-pill" :class="j.job_kind === 'discovery' ? 'info' : 'plain'">
                {{ j.job_kind === 'discovery' ? '自主发现' : '常规采集' }}
              </span>
            </td>
            <td><span class="cw-clip src" :title="sourceName(j.source_id)">{{ sourceName(j.source_id) }}</span></td>
            <td>{{ j.path }}</td>
            <td>
              <span class="tes-pill" :class="j.status === 'active' ? 'success' : 'warn'">
                {{ j.status === 'active' ? '定时中' : j.status === 'stopped' ? '预算停' : '已暂停' }}
              </span>
            </td>
            <td>{{ j.spent }} / {{ j.budget_limit }}</td>
            <td class="cw-query">
              <span class="cw-clip query" :title="j.query || '—'">{{ j.query || '—' }}</span>
            </td>
            <td>{{ j.last_paper_id ? '#' + j.last_paper_id : '—' }}</td>
            <td class="cw-ops">
              <button class="ghost" :disabled="busy" @click="openEdit(j)">编辑</button>
              <button class="ghost" :disabled="busy" @click="toggle(j)">{{ j.status === 'active' ? '暂停定时' : '启用定时' }}</button>
              <button class="ghost" :disabled="busy" @click="run(j)">执行一次</button>
              <button class="ghost" :disabled="busy" @click="retry(j)">重试</button>
              <button class="ghost cw-ghost-danger" :disabled="busy" @click="remove(j)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
      <p v-else class="tes-empty">还没有采集任务。</p>
    </section>

    <!-- 编辑弹窗 -->
    <div v-if="editOpen" class="cw-mask" @click.self="editOpen = false">
      <div class="cw-modal tes-card">
        <div class="cw-head"><b>编辑采集任务 #{{ editingId }}</b><span class="cw-grow"></span>
          <button class="ghost" :disabled="busy" @click="editOpen = false">取消</button>
        </div>
        <div class="cw-row cw-col">
          <label><span>任务名</span><input v-model.trim="editForm.name" /></label>
          <label>
            <span>课程源</span>
            <select v-model.number="editForm.source_id">
              <option :value="null">不绑定</option>
              <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </label>
          <label>
            <span>入库路径</span>
            <select v-model="editForm.path" :disabled="editForm.job_kind === 'discovery'">
              <option value="P1">P1 整卷入库</option>
              <option value="P2">P2 拆题入库</option>
            </select>
          </label>
          <label><span>cron（可空）</span><input v-model.trim="editForm.schedule" placeholder="0 3 * * *" /></label>
          <label><span>预算上限</span>
            <input v-model.number="editForm.budget_limit" type="number" min="1" />
            <small v-if="editForm.budget_limit != null && editForm.budget_limit < editSpent" class="warn-text">
              不能低于已花费 {{ editSpent }}
            </small>
          </label>
          <label v-if="editForm.job_kind === 'discovery'">
            <span>检索约束</span>
            <textarea v-model.trim="editForm.query" rows="2" placeholder="学科/年级/教材版本等自然语言检索约束" />
          </label>
        </div>
        <div class="cw-modal-foot">
          <button class="ghost" :disabled="busy" @click="editOpen = false">取消</button>
          <button class="tes-btn-primary" :disabled="busy || (editForm.budget_limit != null && editForm.budget_limit < editSpent)" @click="saveEdit">
            保存
          </button>
        </div>
      </div>
    </div>

    <div class="cw-links">
      <router-link class="cw-link" to="/course-intake/runs">查看运行日志 →</router-link>
      <router-link class="cw-link" to="/course-intake/sources">管理课程源</router-link>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useCrawlRequest } from '../../composables/useCrawlRequest'
import {
  createCrawlJob, deleteCrawlJob, listCourseSources, listCrawlJobs, retryCrawlJob,
  runCrawlJob, setCrawlJobStatus, updateCrawlJob,
} from '../../api/admin'

const { busy, error, msg, guard } = useCrawlRequest()
const rows = ref([])
const sources = ref([])
const form = reactive({ name: '', source_id: null, path: 'P2', schedule: '', budget_limit: 100 })

// 编辑弹窗状态
const editOpen = ref(false)
const editingId = ref(null)
const editSpent = ref(0)
const editForm = reactive({
  name: '', source_id: null, path: 'P2', schedule: '', budget_limit: 100, query: '',
})

function sourceName(id) {
  return sources.value.find((s) => s.id === id)?.name || '—'
}

// 长字段显示不全时统一省略号 + 鼠标悬停(title)显示全名，无需点击
async function loadAll() {
  await guard(async () => {
    sources.value = await listCourseSources()
    rows.value = await listCrawlJobs()
  })
}
async function add() {
  await guard(async () => {
    // 空 cron 必须归一成 null：后端对任何非空串都跑 parse_cron，传 '' 会 400
    await createCrawlJob({ ...form, schedule: (form.schedule || '').trim() || null })
    form.name = ''
    rows.value = await listCrawlJobs()
    msg.value = '采集任务已创建（cron 非法会被后端拒绝）'
  })
}
function openEdit(j) {
  editingId.value = j.id
  editSpent.value = j.spent || 0
  Object.assign(editForm, {
    name: j.name,
    source_id: j.source_id,
    path: j.path,
    schedule: j.schedule || '',
    budget_limit: j.budget_limit,
    query: j.query || '',
  })
  editOpen.value = true
}
async function saveEdit() {
  await guard(async () => {
    const payload = {
      name: editForm.name,
      source_id: editForm.source_id,
      path: editForm.path,
      schedule: (editForm.schedule || '').trim() || null,
      budget_limit: editForm.budget_limit,
    }
    if (editForm.job_kind === 'discovery') payload.query = editForm.query
    await updateCrawlJob(editingId.value, payload)
    editOpen.value = false
    rows.value = await listCrawlJobs()
    msg.value = `采集任务 #${editingId.value} 已更新`
  })
}
async function toggle(j) {
  await guard(async () => {
    const next = j.status === 'active' ? 'paused' : 'active'
    const out = await setCrawlJobStatus(j.id, next)
    rows.value = await listCrawlJobs()
    msg.value = next === 'active'
      ? `已启用定时${out.next_run_at ? `，下次 ${out.next_run_at}` : '（未设 cron，仅手动执行）'}`
      : '已暂停定时'
  })
}
async function run(j) {
  await guard(async () => {
    const out = await runCrawlJob(j.id)
    rows.value = await listCrawlJobs()
    if (out.last_run_status === 'failed' || out.last_run_status === 'stopped') {
      throw new Error(out.last_run_error || '采集任务未完成')
    }
    msg.value = out.job_kind === 'discovery'
      ? '已触发自主发现采集，请稍后在「运行日志」查看结果'
      : (out.last_paper_id
        ? `已执行：本次产出卷 #${out.last_paper_id}（草稿）`
        : '已触发执行，请稍后刷新查看结果')
  })
}
async function retry(j) {
  await guard(async () => {
    const out = await retryCrawlJob(j.id)
    rows.value = await listCrawlJobs()
    if (out.last_run_status === 'failed' || out.last_run_status === 'stopped') {
      throw new Error(out.last_run_error || '采集任务重试未完成')
    }
    msg.value = '已重试'
  })
}
async function remove(j) {
  if (!window.confirm(`确认删除采集任务「${j.name}」？该任务及其运行日志将被移除。`)) return
  await guard(async () => {
    await deleteCrawlJob(j.id)
    rows.value = await listCrawlJobs()
    msg.value = '采集任务已删除'
  })
}
onMounted(loadAll)
</script>

<style scoped>
.cw-table-wrap { overflow-x: auto; margin-top: 4px; -webkit-overflow-scrolling: touch; }
/* 自动布局 + 单列最小宽度 + 单元格单行(nowrap)，内容超宽时整体横向滚动，保证数据完整不截断 */
:deep(.tes-table) { table-layout: auto; min-width: 1272px; }
:deep(.tes-table thead th) { white-space: nowrap; }
:deep(.tes-table tbody td) { white-space: nowrap; }
:deep(.tes-table col.c-task) { width: 180px; }
:deep(.tes-table col.c-kind) { width: 96px; }
:deep(.tes-table col.c-src) { width: 220px; }
:deep(.tes-table col.c-path) { width: 72px; }
:deep(.tes-table col.c-status) { width: 96px; }
:deep(.tes-table col.c-budget) { width: 84px; }
:deep(.tes-table col.c-query) { width: 200px; }
:deep(.tes-table col.c-paper) { width: 104px; }
:deep(.tes-table col.c-ops) { width: 220px; }
.cw-query { color: #6b7280; font-size: 12px; vertical-align: top; }
/* 长文本统一：单行省略号 + 悬停(title)显示全名 */
.cw-clip { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cw-clip.task { max-width: 180px; }
.cw-clip.src { max-width: 220px; }
.cw-clip.query { max-width: 200px; }
.cw-ops { white-space: normal; }
.cw-ops .ghost { margin: 2px 4px 2px 0; }
.warn-text { color: #dc2626; display: block; }
.cw-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, .45);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.cw-modal { width: 520px; max-width: 92vw; background: #fff; }
.cw-modal .cw-col { flex-direction: column; align-items: stretch; }
.cw-modal .cw-col label { width: 100%; }
.cw-modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
:deep(.tes-pill.info) { background: #e0f2fe; color: #0369a1; }
:deep(.tes-pill.plain) { background: #f1f5f9; color: #475569; }
</style>
