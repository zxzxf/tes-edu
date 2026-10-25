<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">班级错因分布<span class="sub">（步骤级）</span></h2>

    <div class="tes-toolbar">
      <select v-model="groupBy">
        <option value="error_type">按错因类型</option>
        <option value="question">按题目</option>
        <option value="step">按步骤</option>
      </select>
      <input v-model="subject" placeholder="学科（可空，如 数学）" />
      <input v-model.number="periodDays" type="number" min="1" max="180" placeholder="周期天数" />
      <input v-model="studentIds" placeholder="学生ID（逗号分隔，可空）" />
      <button class="tes-btn-primary" @click="load">查询</button>
      <button class="ghost" :disabled="!data" @click="exportCsv">导出 CSV</button>
    </div>

    <div v-if="data">
      <div class="tes-grid cols-4 cards">
        <div class="tes-stat"><div class="num">{{ data.total }}</div><div class="lbl">错因记录</div></div>
        <div class="tes-stat"><div class="num">{{ data.scope.student_count }}</div><div class="lbl">涉及学生</div></div>
        <div class="tes-stat"><div class="num">{{ pct(data.attribution.deterministic_ratio) }}</div><div class="lbl">查表命中占比</div></div>
        <div class="tes-stat"><div class="num warn">{{ pct(data.attribution.unknown_ratio) }}</div><div class="lbl">待人工复核</div></div>
      </div>

      <div class="tes-card panel">
        <p class="scope">
          范围：{{ data.scope.period_days }} 天（{{ data.scope.from.slice(0, 10) }} ~ {{ data.scope.to.slice(0, 10) }}）
          <span v-if="data.scope.subject">· 学科 {{ data.scope.subject }}</span>
        </p>

        <div v-if="data.rows.length" class="tes-table-card">
          <table class="tes-table">
            <thead>
              <tr>
                <th>{{ groupBy === 'error_type' ? '错因类型' : (groupBy === 'question' ? '题目' : '步骤') }}</th>
                <th class="num-col">次数</th>
                <th class="bar-col">占比</th>
                <th v-if="groupBy !== 'error_type'">主要错因</th>
                <th class="num-col">涉及学生</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in data.rows" :key="r.key">
                <td>{{ r.label }}</td>
                <td class="num-col">{{ r.count }}</td>
                <td class="bar-col">
                  <span class="tes-progress"><span class="fill" :style="{ width: (r.ratio * 100) + '%' }"></span></span>
                  <span class="val">{{ pct(r.ratio) }}</span>
                </td>
                <td v-if="groupBy !== 'error_type'">{{ r.main_error_label }}</td>
                <td class="num-col">{{ r.student_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="tes-empty">该范围内暂无错因记录。</p>

        <p v-if="data.rows.length && groupBy === 'error_type'" class="advice">
          📌 教研提示：{{ data.rows[0].label }} 占比最高 —— {{ data.rows[0].advice }}
        </p>
      </div>
    </div>
    <p v-else class="tes-empty">填好条件点「查询」。班级范围默认取当前租户全部学生。</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { getErrorDistribution, downloadErrorDistributionCsv } from '../api/admin'

const groupBy = ref('error_type')
const subject = ref('')
const periodDays = ref(30)
const studentIds = ref('')
const data = ref(null)

function pct(v) {
  return typeof v === 'number' ? (v * 100).toFixed(1) + '%' : '—'
}

async function load() {
  const params = {
    group_by: groupBy.value,
    period_days: periodDays.value || 30
  }
  if (subject.value) params.subject = subject.value
  if (studentIds.value) params.student_ids = studentIds.value
  data.value = await getErrorDistribution(params)
}

async function exportCsv() {
  const params = { group_by: groupBy.value, period_days: periodDays.value || 30 }
  if (subject.value) params.subject = subject.value
  if (studentIds.value) params.student_ids = studentIds.value
  await downloadErrorDistributionCsv(params, `错因分布_按${groupBy.value}.csv`)
}
</script>

<style scoped>
.sub { font-size: 14px; font-weight: 600; color: var(--muted); }
.cards { margin-bottom: 16px; }
.num.warn { color: var(--warning); }
.panel { margin-top: 16px; }
.scope { font-size: 12px; color: var(--muted); margin: 0 0 12px; }
.num-col { width: 70px; text-align: right; }
.bar-col { width: 220px; }
.val { font-size: 12px; color: var(--ink-2); margin-left: 8px; }
.advice { font-size: 13px; color: var(--brand-2); margin: 14px 0 0; background: var(--brand-grad-soft); padding: 10px 12px; border-radius: 12px; }
button.ghost { background: #fff; color: var(--brand-2); border: 1.5px solid var(--brand-2); }
button.ghost:hover { background: #fff0f5; }
button:disabled { opacity: .5; cursor: not-allowed; }
</style>
