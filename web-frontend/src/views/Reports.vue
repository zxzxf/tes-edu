<template>
  <div>
    <h2>家长周报</h2>
    <div class="filters">
      <input v-model="sid" placeholder="学生ID" />
      <input v-model="week" placeholder="周次，如 2026-W42" />
      <input v-model.number="periodDays" type="number" min="1" max="60" placeholder="周期天数" />
      <button @click="load">生成</button>
    </div>

    <div v-if="report">
      <p class="summary">{{ report.summary }}</p>
      <p class="weak">薄弱知识点：{{ report.weak_points.map(w => w.knowledge_point).join('、') || '无' }}</p>

      <!-- S5：步骤级薄弱点 -->
      <section class="panel">
        <h3>步骤级薄弱点</h3>
        <p v-if="sw.headline" class="headline">{{ sw.headline }}</p>
        <div v-if="sw.error_types.length">
          <div v-for="d in sw.error_types" :key="d.error_type" class="row">
            <span class="name">{{ d.label }}</span>
            <span class="track"><span class="fill" :style="{ width: (d.ratio * 100) + '%' }"></span></span>
            <span class="val">{{ d.count }} 次 · {{ pct(d.ratio) }}</span>
          </div>
        </div>
        <p v-else class="hint">本周暂无可归因的错因记录。</p>

        <h4 v-if="sw.top_steps.length">反复出错的步骤</h4>
        <ul v-if="sw.top_steps.length" class="steps">
          <li v-for="s in sw.top_steps" :key="s.content_hash + '#' + s.step_no">
            <span class="tag">{{ s.count }} 次</span>
            {{ s.step_label }}
            <span class="kp">{{ s.main_error_label }}</span>
            <span v-if="s.kp_name" class="kp">· {{ s.kp_name }}</span>
          </li>
        </ul>
        <p v-if="sw.advice.length" class="advice">建议：{{ sw.advice[0] }}</p>
      </section>

      <section class="panel">
        <h3>给家长的建议</h3>
        <ul class="advice-list">
          <li v-for="(s, i) in report.suggestions" :key="i">{{ s }}</li>
        </ul>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { getWeeklyReport } from '../api/admin'

const sid = ref('student_001')
const week = ref('2026-W42')
const periodDays = ref(7)
const report = ref(null)
const sw = computed(() => report.value?.step_weakness || { error_types: [], top_steps: [], advice: [], headline: '' })

function pct(v) {
  return typeof v === 'number' ? (v * 100).toFixed(0) + '%' : '—'
}

async function load() {
  report.value = await getWeeklyReport(sid.value, week.value, periodDays.value || 7)
}
</script>

<style scoped>
h2 { font-size: 18px; margin: 0 0 12px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
input { padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.summary { background: #fff; padding: 12px; border: 1px solid #ebeef5; border-radius: 6px; }
.weak { color: #e6a23c; }
.panel { background: #fff; padding: 14px 16px; border: 1px solid #ebeef5; border-radius: 8px; margin-top: 12px; }
.panel h3 { font-size: 14px; margin: 0 0 10px; border-left: 4px solid #409eff; padding-left: 8px; }
.panel h4 { font-size: 13px; margin: 14px 0 6px; color: #606266; }
.headline { font-size: 13px; color: #303133; margin: 0 0 10px; }
.row { display: grid; grid-template-columns: 120px 1fr 110px; align-items: center; gap: 8px; margin: 6px 0; font-size: 13px; }
.name { color: #606266; }
.track { background: #eef2f7; border-radius: 4px; height: 8px; overflow: hidden; }
.fill { display: block; height: 100%; background: #f56c6c; }
.val { text-align: right; color: #909399; font-size: 12px; }
.steps { list-style: none; padding: 0; margin: 0; font-size: 13px; }
.steps li { padding: 6px 0; border-bottom: 1px solid #f2f6fc; }
.tag { background: #fee2e2; color: #b91c1c; border-radius: 6px; padding: 1px 6px; font-size: 11px; margin-right: 6px; }
.kp { color: #909399; font-size: 12px; margin-left: 6px; }
.hint { font-size: 12px; color: #909399; margin: 6px 0 0; }
.advice { background: #f4f8ff; padding: 10px; border-radius: 6px; font-size: 13px; color: #606266; margin: 12px 0 0; }
.advice-list { margin: 0; padding-left: 20px; font-size: 13px; line-height: 1.8; color: #303133; }
</style>
