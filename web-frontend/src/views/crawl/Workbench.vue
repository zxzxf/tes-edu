<template>
  <div class="cw-page">
    <h2 class="cw-title">采集工作台</h2>
    <p class="cw-hint">
      课程源 → 采集任务 → 真实抓取/解析 → 去重入库 <code>draft</code> → 知识点审核发布。
      这里汇总 M5 全链路的运行态势与待办，数据来自 <code>/course/metrics</code> 等既有接口。
    </p>
    <p v-if="error" class="err">{{ error }}</p>

    <div class="cw-stats">
      <div class="cw-stat"><div class="cw-stat-label">累计运行</div><div class="cw-stat-value">{{ metrics?.runs ?? 0 }}</div></div>
      <div class="cw-stat"><div class="cw-stat-label">近期入库题目</div><div class="cw-stat-value">{{ insertedTotal }}</div></div>
      <div class="cw-stat"><div class="cw-stat-label">去重率</div><div class="cw-stat-value">{{ pct(metrics?.dedup_rate) }}%</div></div>
      <div class="cw-stat"><div class="cw-stat-label">失败运行</div><div class="cw-stat-value" :class="{ danger: metrics?.failed }">{{ metrics?.failed ?? 0 }}</div></div>
    </div>

    <div class="cw-links">
      <router-link class="cw-link" to="/course-intake/sources">课程源管理</router-link>
      <router-link class="cw-link" to="/course-intake/jobs">采集任务</router-link>
      <router-link class="cw-link" to="/course-intake/parse">解析预览</router-link>
      <router-link class="cw-link" to="/course-intake/review">知识点审核</router-link>
      <router-link class="cw-link" to="/course-intake/runs">运行日志</router-link>
      <button class="ghost" :disabled="busy" @click="load">刷新</button>
    </div>

    <div class="cw-grid-2">
      <section class="tes-card">
        <div class="cw-head"><b>我的待办</b><span class="cw-grow"></span><span class="cw-badge">{{ todos.length }} 项</span></div>
        <div v-if="todos.length" class="cw-list">
          <div v-for="t in todos" :key="t.text">
            · <router-link :to="t.to">{{ t.text }}</router-link>
          </div>
        </div>
        <p v-else class="tes-empty">暂无待办，采集链路运行正常。</p>
      </section>

      <section class="tes-card">
        <div class="cw-head"><b>最近运行</b><span class="cw-grow"></span>
          <router-link class="cw-badge" to="/course-intake/runs">查看全部</router-link>
        </div>
        <div v-if="recent.length" class="cw-list">
          <div v-for="r in recent" :key="r.id">
            · 任务 #{{ r.job_id }}
            <span class="tes-pill" :class="r.status === 'ok' ? 'success' : 'warn'">{{ r.status }}</span>
            入库 {{ r.inserted }} / 重复 {{ r.duplicated }}
            <span v-if="r.error" class="cw-err" :title="r.error">{{ r.error }}</span>
          </div>
        </div>
        <p v-else class="tes-empty">暂无运行记录。</p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useCrawlRequest } from '../../composables/useCrawlRequest'
import { getCrawlMetrics, listCourseSources, listKpNodes } from '../../api/admin'

const { busy, error, guard, errText } = useCrawlRequest()
const metrics = ref(null)
const draftKp = ref(0)
const sources = ref([])

const recent = computed(() => (metrics.value?.recent || []).slice(0, 5))
const insertedTotal = computed(() =>
  (metrics.value?.recent || []).reduce((n, r) => n + (r.inserted || 0), 0)
)
const pausedSources = computed(() => sources.value.filter((s) => s.status !== 'active').length)
const failedRuns = computed(() => (metrics.value?.recent || []).filter((r) => r.status === 'failed').length)
const todos = computed(() => {
  const out = []
  if (draftKp.value) out.push({ text: `知识点待审核 ${draftKp.value} 条`, to: '/course-intake/review' })
  if (failedRuns.value) out.push({ text: `采集失败待处理 ${failedRuns.value} 次`, to: '/course-intake/runs' })
  if (pausedSources.value) out.push({ text: `暂停中的课程源 ${pausedSources.value} 个`, to: '/course-intake/sources' })
  return out
})

function pct(v) {
  return typeof v === 'number' ? (v * 100).toFixed(1) : '0.0'
}

async function load() {
  await guard(async () => {
    // 单个接口失败不应让整个工作台空白：分别取值，再把失败原因汇总到顶部
    const [m, kp, src] = await Promise.allSettled([
      getCrawlMetrics(), listKpNodes('draft'), listCourseSources(),
    ])
    if (m.status === 'fulfilled') metrics.value = m.value
    if (kp.status === 'fulfilled') draftKp.value = (kp.value || []).length
    if (src.status === 'fulfilled') sources.value = src.value || []
    const bad = [m, kp, src].filter((r) => r.status === 'rejected')
    if (bad.length) error.value = `${bad.length} 项数据加载失败：${errText(bad[0].reason)}`
  })
}
onMounted(load)
</script>
