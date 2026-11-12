<template>
  <div class="cw-page">
    <h2 class="cw-title">运行日志与回滚</h2>
    <p class="cw-hint">
      每次「执行一次」/定时调度都会落一条 <code>crawl_run</code>。失败原因、去重数、超预算停止均可追溯；
      一键回滚只撤本次 <code>draft</code> / <code>auto_crawl</code>，不动教师已审核的题目。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <div class="cw-stats">
      <div class="cw-stat"><div class="cw-stat-label">成功率</div><div class="cw-stat-value success">{{ pct(metrics?.success_rate) }}%</div></div>
      <div class="cw-stat"><div class="cw-stat-label">去重率</div><div class="cw-stat-value">{{ pct(metrics?.dedup_rate) }}%</div></div>
      <div class="cw-stat"><div class="cw-stat-label">累计运行</div><div class="cw-stat-value">{{ metrics?.runs ?? 0 }}</div></div>
      <div class="cw-stat"><div class="cw-stat-label">失败</div><div class="cw-stat-value" :class="{ danger: metrics?.failed }">{{ metrics?.failed ?? 0 }}</div></div>
      <div class="cw-stat"><div class="cw-stat-label">超预算停</div><div class="cw-stat-value">{{ metrics?.stopped ?? 0 }}</div></div>
    </div>

    <section class="tes-card">
      <div class="cw-head"><b>最近运行（M5-6 可观测）</b><span class="cw-grow"></span>
        <button class="ghost" :disabled="busy" @click="load">刷新</button>
      </div>
      <table v-if="recent.length" class="tes-table">
        <thead><tr><th>#</th><th>任务</th><th>状态 / 原因</th><th>入库 / 重复 / 失败</th><th>产出卷</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in recent" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.job_id }}</td>
            <td>
              <span class="tes-pill" :class="r.status === 'ok' ? 'success' : 'warn'">{{ r.status }}</span>
              <span v-if="r.error" class="cw-err" :title="r.error">{{ r.error }}</span>
            </td>
            <td>{{ r.inserted }} / {{ r.duplicated }} / {{ r.failed }}</td>
            <td>{{ r.paper_id ? '#' + r.paper_id : '—' }}</td>
            <td>
              <button v-if="canManage && r.status !== 'running'" class="ghost cw-ghost-danger" :disabled="busy" @click="rollback(r)">回退本次</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="tes-empty">暂无运行记录。</p>
    </section>

    <div class="cw-links">
      <router-link class="cw-link" to="/course-intake/jobs">去执行采集任务 →</router-link>
      <router-link class="cw-link" to="/course-intake/workbench">返回工作台</router-link>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useCrawlRequest } from '../../composables/useCrawlRequest'
import { getCrawlMetrics, rollbackCrawlRun } from '../../api/admin'

const { busy, error, msg, guard } = useCrawlRequest()
const metrics = ref(null)
const auth = useAuthStore()
const canManage = computed(() => ['admin', 'school'].includes(auth.user?.role))
const recent = computed(() => metrics.value?.recent || [])

function pct(v) {
  return typeof v === 'number' ? (v * 100).toFixed(1) : '0.0'
}
async function load() {
  await guard(async () => { metrics.value = await getCrawlMetrics() })
}
async function rollback(r) {
  await guard(async () => {
    const out = await rollbackCrawlRun(r.id)
    metrics.value = await getCrawlMetrics()
    msg.value = `已回退：题 ${out.removed_questions} 道、卷 ${out.removed_papers} 张（已审核的保留）`
  })
}
onMounted(load)
</script>
