<template>
  <div>
    <h2>班级错因分布（步骤级）</h2>
    <div class="filters">
      <select v-model="groupBy">
        <option value="error_type">按错因类型</option>
        <option value="question">按题目</option>
        <option value="step">按步骤</option>
      </select>
      <input v-model="subject" placeholder="学科（可空，如 数学）" />
      <input v-model.number="periodDays" type="number" min="1" max="180" placeholder="周期天数" />
      <input v-model="studentIds" placeholder="学生ID（逗号分隔，可空）" />
      <button @click="load">查询</button>
      <button class="ghost" :disabled="!data" @click="exportCsv">导出 CSV</button>
    </div>

    <div v-if="data" class="cards">
      <div class="card"><div class="num">{{ data.total }}</div><div class="lbl">错因记录</div></div>
      <div class="card"><div class="num">{{ data.scope.student_count }}</div><div class="lbl">涉及学生</div></div>
      <div class="card"><div class="num">{{ pct(data.attribution.deterministic_ratio) }}</div><div class="lbl">查表命中占比</div></div>
      <div class="card"><div class="num warn">{{ pct(data.attribution.unknown_ratio) }}</div><div class="lbl">待人工复核</div></div>
    </div>

    <div v-if="data" class="panel">
      <p class="scope">
        范围：{{ data.scope.period_days }} 天（{{ data.scope.from.slice(0, 10) }} ~ {{ data.scope.to.slice(0, 10) }}）
        <span v-if="data.scope.subject">· 学科 {{ data.scope.subject }}</span>
      </p>
      <table v-if="data.rows.length">
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
              <div class="track"><div class="fill" :style="{ width: (r.ratio * 100) + '%' }"></div></div>
              <span class="val">{{ pct(r.ratio) }}</span>
            </td>
            <td v-if="groupBy !== 'error_type'">{{ r.main_error_label }}</td>
            <td class="num-col">{{ r.student_count }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="hint">该范围内暂无错因记录。</p>
      <p v-if="data.rows.length && groupBy === 'error_type'" class="advice">
        教研提示：{{ data.rows[0].label }} 占比最高 —— {{ data.rows[0].advice }}
      </p>
    </div>
    <p v-else class="hint">填好条件点「查询」。班级范围默认取当前租户全部学生。</p>
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
h2 { font-size: 18px; margin: 0 0 12px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
input, select { padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
button.ghost { background: #fff; color: #409eff; border: 1px solid #409eff; }
button:disabled { opacity: .5; cursor: not-allowed; }
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
.card { background: #fff; border: 1px solid #ebeef5; border-radius: 8px; padding: 12px; text-align: center; }
.card .num { font-size: 22px; font-weight: 700; }
.card .num.warn { color: #e6a23c; }
.card .lbl { font-size: 12px; color: #909399; margin-top: 4px; }
.panel { background: #fff; border: 1px solid #ebeef5; border-radius: 8px; padding: 16px; }
.scope, .hint { font-size: 12px; color: #909399; margin: 0 0 10px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 6px; border-bottom: 1px solid #f2f6fc; text-align: left; }
th { color: #606266; font-weight: 600; }
.num-col { width: 70px; text-align: right; }
.bar-col { width: 220px; }
.track { display: inline-block; width: 150px; height: 8px; background: #eef2f7; border-radius: 4px; overflow: hidden; vertical-align: middle; }
.fill { height: 100%; background: #409eff; }
.val { font-size: 12px; color: #606266; margin-left: 8px; }
.advice { font-size: 13px; color: #606266; margin: 12px 0 0; background: #f4f8ff; padding: 10px; border-radius: 6px; }
</style>
