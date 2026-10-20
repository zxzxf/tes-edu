<template>
  <div>
    <h2>学情看板</h2>
    <input v-model="sid" placeholder="学生ID" />
    <button @click="load">查询</button>
    <div v-if="data" class="panel">
      <p>掌握度：{{ (data.mastery * 100).toFixed(0) }}%</p>
      <p>薄弱点：{{ data.weak_points.join('、') }}</p>
      <p>趋势：{{ data.trend.join(' → ') }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { getAnalytics } from '../api/admin'
const sid = ref('student_001')
const data = ref(null)
async function load() { data.value = await getAnalytics(sid.value) }
</script>

<style scoped>
input { padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; margin-right: 8px; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.panel { margin-top: 12px; background: #fff; padding: 16px; border: 1px solid #ebeef5; border-radius: 8px; }
</style>
