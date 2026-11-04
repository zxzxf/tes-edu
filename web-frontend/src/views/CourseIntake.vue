<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">课程采集与知识点</h2>
    <p class="hint">
      课程源 → 采集任务（P1 整卷入库 / P2 拆题入库）→ 知识点候选审核。
      采集入库一律 <b>draft 待审</b>；P1 会建一张 <code>auto_crawl</code> 草稿卷，
      P2 只进题库、之后由规则组卷重组。命中合规黑名单的源直接拒绝。
    </p>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <!-- 课程源 -->
    <section class="tes-card">
      <div class="head">
        <b>课程源（M5-1）</b>
        <span class="grow"></span>
        <button class="ghost" :disabled="busy" @click="loadSources">刷新</button>
      </div>
      <div class="row">
        <label><span>名称</span><input v-model.trim="source.name" placeholder="人教A版必修一" /></label>
        <label>
          <span>类型</span>
          <select v-model="source.source_type">
            <option value="file">文件</option>
            <option value="url">URL</option>
            <option value="bank_pack">题库包</option>
          </select>
        </label>
        <label class="grow"><span>地址 / 路径</span><input v-model.trim="source.uri" placeholder="https://… 或 /data/…" /></label>
        <button class="tes-btn-primary" :disabled="busy" @click="addSource">新增</button>
      </div>
      <table v-if="sources.length" class="tes-table">
        <thead><tr><th>名称</th><th>类型</th><th>地址</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="s in sources" :key="s.id">
            <td>{{ s.name }}</td>
            <td>{{ s.source_type }}</td>
            <td class="mono">{{ s.uri }}</td>
            <td><span class="tes-pill" :class="s.status === 'active' ? 'success' : 'warn'">{{ s.status === 'active' ? '已启用' : '已暂停' }}</span></td>
            <td>
              <button class="ghost" :disabled="busy" @click="toggleSource(s)">{{ s.status === 'active' ? '暂停' : '启用' }}</button>
              <button class="ghost danger" :disabled="busy" @click="removeSource(s)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="tes-empty">还没有课程源。</p>
    </section>

    <!-- 采集任务 -->
    <section class="tes-card">
      <div class="head">
        <b>采集任务（M3-8 / M4-4）</b>
        <span class="grow"></span>
        <button class="ghost" :disabled="busy" @click="loadJobs">刷新</button>
      </div>
      <div class="row">
        <label><span>任务名</span><input v-model.trim="job.name" placeholder="每月新题采集" /></label>
        <label>
          <span>课程源</span>
          <select v-model.number="job.source_id">
            <option :value="null">不绑定</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>
        <label>
          <span>入库路径</span>
          <select v-model="job.path">
            <option value="P1">P1 整卷入库</option>
            <option value="P2">P2 拆题入库</option>
          </select>
        </label>
        <label><span>cron（可空）</span><input v-model.trim="job.schedule" placeholder="0 3 * * *" /></label>
        <label><span>预算上限</span><input v-model.number="job.budget_limit" type="number" min="1" /></label>
        <button class="tes-btn-primary" :disabled="busy" @click="addJob">新建</button>
      </div>
      <table v-if="jobs.length" class="tes-table">
        <thead><tr><th>任务</th><th>路径</th><th>状态</th><th>预算</th><th>最近产出卷</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="j in jobs" :key="j.id">
            <td>{{ j.name }}</td>
            <td>{{ j.path }}</td>
            <td>
              <span class="tes-pill" :class="j.status === 'active' ? 'success' : 'warn'">
                {{ j.status === 'active' ? '定时中' : j.status === 'stopped' ? '预算停' : '已暂停' }}
              </span>
            </td>
            <td>{{ j.spent }} / {{ j.budget_limit }}</td>
            <td>{{ j.last_paper_id ? '#' + j.last_paper_id : '—' }}</td>
            <td>
              <button class="ghost" :disabled="busy" @click="toggleJob(j)">
                {{ j.status === 'active' ? '暂停定时' : '启用定时' }}
              </button>
              <button class="ghost" :disabled="busy" @click="runJob(j)">执行一次</button>
              <button class="ghost" :disabled="busy" @click="retryJob(j)">重试</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="tes-empty">还没有采集任务。</p>
    </section>

    <!-- 指标 -->
    <section v-if="metrics" class="tes-card">
      <div class="head"><b>采集可观测（M5-6）</b></div>
      <div class="stat">
        <span class="s ok">成功率 {{ (metrics.success_rate * 100).toFixed(1) }}%</span>
        <span class="s">去重率 {{ (metrics.dedup_rate * 100).toFixed(1) }}%</span>
        <span class="s">运行 {{ metrics.runs }} 次</span>
        <span class="s" v-if="metrics.stopped">超预算停 {{ metrics.stopped }}</span>
        <span class="s warn" v-if="metrics.failed">失败 {{ metrics.failed }}</span>
      </div>
      <table v-if="metrics.recent && metrics.recent.length" class="tes-table">
        <thead><tr><th>#</th><th>任务</th><th>状态</th><th>入库/重复/失败</th><th>产出卷</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in metrics.recent.slice(0, 10)" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.job_id }}</td>
            <td>{{ r.status }}</td>
            <td>{{ r.inserted }} / {{ r.duplicated }} / {{ r.failed }}</td>
            <td>{{ r.paper_id ? '#' + r.paper_id : '—' }}</td>
            <td>
              <button
                v-if="r.status !== 'running'"
                class="ghost danger"
                :disabled="busy"
                @click="rollback(r)"
              >回退本次</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 课程解析 -->
    <section class="tes-card">
      <div class="head"><b>课程解析 pipeline（M5-2）</b></div>
      <label class="block">
        <span>粘贴教材/大纲文本</span>
        <textarea v-model="outlineText" rows="6" placeholder="第一章 数列\n第一节 等差数列\n…"></textarea>
      </label>
      <div class="row">
        <label>
          <span>解析器</span>
          <select v-model="outlineType">
            <option value="textbook">教材</option>
            <option value="outline">大纲</option>
            <option value="video">视频</option>
            <option value="web">网页</option>
            <option value="bank">题库包</option>
          </select>
        </label>
        <button class="ghost" :disabled="busy" @click="parseOutline">解析</button>
        <button v-if="parsed" class="tes-btn-primary" :disabled="busy" @click="importParsed">导入知识点候选</button>
      </div>
      <pre v-if="parsed" class="outline">{{ JSON.stringify(parsed, null, 2) }}</pre>
    </section>

    <!-- 知识点审核 -->
    <section class="tes-card">
      <div class="head">
        <b>知识点审核（M5-3 / M5-4）</b>
        <span class="grow"></span>
        <select v-model="kpStatus" aria-label="知识点状态" @change="loadKpNodes">
          <option value="draft">待审核</option>
          <option value="published">已发布</option>
        </select>
      </div>
      <p class="hint">未审核（draft）的知识点不进学生端图谱；审核通过后写入 <code>kb_knowledge_points</code>。</p>
      <table v-if="kpNodes.length" class="tes-table">
        <thead><tr><th>code</th><th>名称</th><th>父节点</th><th>来源</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="k in kpNodes" :key="k.code">
            <td class="mono">{{ k.code }}</td>
            <td>{{ k.name }}</td>
            <td class="mono">{{ k.parent_code || '—' }}</td>
            <td>{{ k.source }}</td>
            <td>
              <button v-if="k.status === 'draft'" class="ghost" :disabled="busy" @click="reviewKp(k, 'published')">通过</button>
              <button v-else class="ghost" :disabled="busy" @click="reviewKp(k, 'draft')">退回</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="tes-empty">没有知识点候选。</p>
    </section>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  createCourseSource,
  createCrawlJob,
  deleteCourseSource,
  getCrawlMetrics,
  listCourseSources,
  listCrawlJobs,
  listKpNodes,
  parseCourseSource,
  retryCrawlJob,
  reviewKpNode,
  rollbackCrawlRun,
  runCrawlJob,
  setCourseSourceStatus,
  setCrawlJobStatus,
  upsertKpNodes
} from '../api/admin'

const busy = ref(false)
const error = ref('')
const msg = ref('')

const sources = ref([])
const jobs = ref([])
const metrics = ref(null)
const kpNodes = ref([])
const kpStatus = ref('draft')

const source = reactive({ name: '', source_type: 'file', uri: '', parser: 'auto' })
const job = reactive({ name: '', source_id: null, path: 'P2', schedule: '', budget_limit: 100 })

const outlineText = ref('')
const outlineType = ref('textbook')
const parsed = ref(null)

function errText(e) {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d?.message) return d.message
  return e?.message || '请求失败'
}

async function guard(fn) {
  busy.value = true
  error.value = ''
  msg.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function loadSources() {
  await guard(async () => { sources.value = await listCourseSources() })
}

async function addSource() {
  await guard(async () => {
    await createCourseSource({ ...source })
    source.name = ''
    source.uri = ''
    sources.value = await listCourseSources()
    msg.value = '课程源已创建（默认暂停，需手动启用）'
  })
}

async function toggleSource(s) {
  await guard(async () => {
    await setCourseSourceStatus(s.id, s.status === 'active' ? 'paused' : 'active')
    sources.value = await listCourseSources()
  })
}

async function removeSource(s) {
  await guard(async () => {
    await deleteCourseSource(s.id)
    sources.value = await listCourseSources()
  })
}

async function loadJobs() {
  await guard(async () => { jobs.value = await listCrawlJobs() })
}

async function addJob() {
  await guard(async () => {
    await createCrawlJob({ ...job })
    job.name = ''
    jobs.value = await listCrawlJobs()
    msg.value = '采集任务已创建（cron 非法会被后端拒绝）'
  })
}

async function toggleJob(j) {
  await guard(async () => {
    const next = j.status === 'active' ? 'paused' : 'active'
    const out = await setCrawlJobStatus(j.id, next)
    jobs.value = await listCrawlJobs()
    msg.value = next === 'active'
      ? `已启用定时${out.next_run_at ? `，下次 ${out.next_run_at}` : '（未设 cron，仅手动执行）'}`
      : '已暂停定时'
  })
}

async function runJob(j) {
  await guard(async () => {
    const out = await runCrawlJob(j.id)
    jobs.value = await listCrawlJobs()
    metrics.value = await getCrawlMetrics()
    msg.value = out.last_paper_id
      ? `已执行：本次产出卷 #${out.last_paper_id}（草稿）`
      : '已执行（P2 只入题库，未成卷）'
  })
}

async function retryJob(j) {
  await guard(async () => {
    await retryCrawlJob(j.id)
    jobs.value = await listCrawlJobs()
    metrics.value = await getCrawlMetrics()
    msg.value = '已重试'
  })
}

async function rollback(r) {
  await guard(async () => {
    const out = await rollbackCrawlRun(r.id)
    metrics.value = await getCrawlMetrics()
    msg.value = `已回退：题 ${out.removed_questions} 道、卷 ${out.removed_papers} 张（已审核的保留）`
  })
}

async function loadMetrics() {
  await guard(async () => { metrics.value = await getCrawlMetrics() })
}

async function loadKpNodes() {
  await guard(async () => { kpNodes.value = await listKpNodes(kpStatus.value) })
}

async function parseOutline() {
  await guard(async () => {
    parsed.value = await parseCourseSource({
      text: outlineText.value,
      source_type: outlineType.value
    })
    msg.value = `解析出 ${parsed.value.chapter_count} 章 / ${parsed.value.point_count} 个候选知识点`
  })
}

async function importParsed() {
  await guard(async () => {
    const items = []
    const walk = (nodes, parent) => {
      for (const n of nodes || []) {
        items.push({
          code: n.code,
          name: n.title || n.name,
          parent_code: parent || null,
          subject: parsed.value.subject || '数学',
          source: 'crawled'
        })
        walk(n.children || n.sections || [], n.code)
      }
    }
    walk(parsed.value.chapters || parsed.value.outline || [], null)
    if (!items.length) throw new Error('没有可导入的知识点候选')
    await upsertKpNodes(items)
    kpStatus.value = 'draft'
    kpNodes.value = await listKpNodes('draft')
    msg.value = `已导入 ${items.length} 个候选（draft，待审核）`
  })
}

async function reviewKp(k, status) {
  await guard(async () => {
    await reviewKpNode(k.code, status)
    kpNodes.value = await listKpNodes(kpStatus.value)
  })
}

onMounted(() => {
  loadSources()
  loadJobs()
  loadMetrics()
  loadKpNodes()
})
</script>

<style scoped>
.head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.grow { flex: 1; }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; margin-bottom: 12px; }
.row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #666; }
.row label.grow { flex: 1; min-width: 220px; }
.block { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; font-size: 12px; color: #666; }
.mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.outline { background: #f7f8fa; padding: 12px; border-radius: 8px; max-height: 320px; overflow: auto; font-size: 12px; }
.stat { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
.ghost.danger { color: #c62828; }
</style>
