<template>
  <div>
    <h2>家长周报</h2>
    <input v-model="sid" placeholder="学生ID" />
    <input v-model="week" placeholder="周次，如 2026-W42" />
    <button @click="load">生成</button>
    <p v-if="report" class="summary">{{ report.summary }}</p>
    <p v-if="report" class="weak">薄弱点：{{ report.weak_points.join('、') }}</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { getWeeklyReport } from '../api/admin'
const sid = ref('student_001')
const week = ref('2026-W42')
const report = ref(null)
async function load() { report.value = await getWeeklyReport(sid.value, week.value) }
</script>

<style scoped>
input { padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; margin-right: 8px; margin-bottom: 8px; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.summary { background: #fff; padding: 12px; border: 1px solid #ebeef5; border-radius: 6px; }
.weak { color: #e6a23c; }
</style>
