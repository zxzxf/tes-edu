<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">学情看板</h2>

    <div class="tes-toolbar">
      <input v-model="sid" placeholder="学生ID" />
      <button class="tes-btn-primary" @click="load">查询</button>
    </div>

    <div v-if="data" class="tes-card">
      <div class="big">{{ (data.mastery * 100).toFixed(0) }}<span>%</span></div>
      <div class="cap">综合掌握度</div>
      <div class="row"><span class="k">薄弱点</span><span class="v">{{ data.weak_points.join('、') }}</span></div>
      <div class="row"><span class="k">趋势</span><span class="v">{{ data.trend.join(' → ') }}</span></div>
    </div>
    <p v-else class="tes-empty">输入学生 ID 查询其学情画像。</p>
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
.big {
  font-size: 40px; font-weight: 800; line-height: 1;
  background: var(--brand-grad);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.big span { font-size: 20px; margin-left: 2px; }
.cap { color: var(--muted); font-size: 13px; margin: 4px 0 14px; }
.row { display: flex; gap: 12px; padding: 8px 0; border-top: 1px solid var(--line); }
.k { color: var(--ink-2); font-weight: 600; min-width: 64px; }
.v { color: var(--ink); }
</style>
