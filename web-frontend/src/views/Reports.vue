<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">家长周报</h2>

    <div class="tes-toolbar">
      <input v-model="sid" placeholder="学生ID" />
      <input v-model="week" placeholder="周次，如 2026-W42" />
      <input v-model.number="periodDays" type="number" min="1" max="60" placeholder="周期天数" />
      <button class="tes-btn-primary" @click="load">生成</button>
    </div>

    <div v-if="report">
      <div class="tes-card summary">{{ report.summary }}</div>
      <p class="weak">⚠️ 薄弱知识点：{{ report.weak_points.map(w => w.knowledge_point).join('、') || '无' }}</p>

      <!-- S5：步骤级薄弱点 -->
      <section class="tes-card panel">
        <h3>步骤级薄弱点</h3>
        <p v-if="sw.headline" class="headline">{{ sw.headline }}</p>
        <div v-if="sw.error_types.length">
          <div v-for="d in sw.error_types" :key="d.error_type" class="row">
            <span class="name">{{ d.label }}</span>
            <span class="tes-progress"><span class="fill" :style="{ width: (d.ratio * 100) + '%' }"></span></span>
            <span class="val">{{ d.count }} 次 · {{ pct(d.ratio) }}</span>
          </div>
        </div>
        <p v-else class="tes-empty">本周暂无可归因的错因记录。</p>

        <h4 v-if="sw.top_steps.length">反复出错的步骤</h4>
        <ul v-if="sw.top_steps.length" class="steps">
          <li v-for="s in sw.top_steps" :key="s.content_hash + '#' + s.step_no">
            <span class="tes-pill danger">{{ s.count }} 次</span>
            {{ s.step_label }}
            <span class="kp">{{ s.main_error_label }}</span>
            <span v-if="s.kp_name" class="kp">· {{ s.kp_name }}</span>
          </li>
        </ul>
        <p v-if="sw.advice.length" class="advice">💡 {{ sw.advice[0] }}</p>
      </section>

      <section class="tes-card panel">
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
.summary { font-size: 15px; line-height: 1.7; color: var(--ink); margin-bottom: 12px; }
.weak { color: #c77700; font-size: 13px; font-weight: 600; margin: 0 0 14px; }
.panel { margin-top: 16px; }
.panel h3 {
  font-size: 15px; margin: 0 0 12px; padding-left: 12px;
  border-left: 4px solid; border-image: var(--brand-grad) 1;
}
.panel h4 { font-size: 13px; margin: 16px 0 8px; color: var(--ink-2); }
.headline { font-size: 13px; color: var(--ink); margin: 0 0 12px; }
.row { display: grid; grid-template-columns: 120px 1fr 110px; align-items: center; gap: 10px; margin: 8px 0; font-size: 13px; }
.name { color: var(--ink-2); }
.val { text-align: right; color: var(--muted); font-size: 12px; }
.steps { list-style: none; padding: 0; margin: 0; font-size: 13px; }
.steps li { padding: 8px 0; border-bottom: 1px solid var(--line); display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.kp { color: var(--muted); font-size: 12px; }
.advice { background: var(--brand-grad-soft); padding: 10px 12px; border-radius: 12px; font-size: 13px; color: var(--brand-2); margin: 12px 0 0; }
.advice-list { margin: 0; padding-left: 20px; font-size: 13px; line-height: 1.9; color: var(--ink); }
</style>
