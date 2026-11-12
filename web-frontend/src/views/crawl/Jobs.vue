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
      <table v-if="rows.length" class="tes-table">
        <thead><tr><th>任务</th><th>课程源</th><th>路径</th><th>状态</th><th>预算</th><th>最近产出卷</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="j in rows" :key="j.id">
            <td>{{ j.name }}</td>
            <td>{{ sourceName(j.source_id) }}</td>
            <td>{{ j.path }}</td>
            <td>
              <span class="tes-pill" :class="j.status === 'active' ? 'success' : 'warn'">
                {{ j.status === 'active' ? '定时中' : j.status === 'stopped' ? '预算停' : '已暂停' }}
              </span>
            </td>
            <td>{{ j.spent }} / {{ j.budget_limit }}</td>
            <td>{{ j.last_paper_id ? '#' + j.last_paper_id : '—' }}</td>
            <td>
              <button class="ghost" :disabled="busy" @click="toggle(j)">{{ j.status === 'active' ? '暂停定时' : '启用定时' }}</button>
              <button class="ghost" :disabled="busy" @click="run(j)">执行一次</button>
              <button class="ghost" :disabled="busy" @click="retry(j)">重试</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="tes-empty">还没有采集任务。</p>
    </section>

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
  createCrawlJob, listCourseSources, listCrawlJobs, retryCrawlJob,
  runCrawlJob, setCrawlJobStatus,
} from '../../api/admin'

const { busy, error, msg, guard } = useCrawlRequest()
const rows = ref([])
const sources = ref([])
const form = reactive({ name: '', source_id: null, path: 'P2', schedule: '', budget_limit: 100 })

function sourceName(id) {
  return sources.value.find((s) => s.id === id)?.name || '—'
}
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
    msg.value = out.last_paper_id
      ? `已执行：本次产出卷 #${out.last_paper_id}（草稿）`
      : '已执行（P2 只入题库，未成卷）'
  })
}
async function retry(j) {
  await guard(async () => {
    await retryCrawlJob(j.id)
    rows.value = await listCrawlJobs()
    msg.value = '已重试'
  })
}
onMounted(loadAll)
</script>
