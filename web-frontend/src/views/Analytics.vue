<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">学情看板</h2>

    <div class="tes-toolbar">
      <input v-model="sid" placeholder="学生ID" />
      <button class="tes-btn-primary" @click="load">查询</button>
    </div>

    <p v-if="error" class="err">{{ error }}</p>

    <div v-if="data" class="tes-card">
      <div class="big">{{ (data.mastery * 100).toFixed(0) }}<span>%</span></div>
      <div class="cap">综合掌握度</div>
      <div class="row"><span class="k">薄弱点</span><span class="v">{{ data.weak_points.join('、') }}</span></div>
      <div class="row"><span class="k">趋势</span><span class="v">{{ data.trend.join(' → ') }}</span></div>
    </div>
    <p v-else-if="!error" class="tes-empty">输入学生 ID 查询其学情画像。</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { getAnalytics, DEMO_STUDENT_ID } from '../api/admin'
const sid = ref(DEMO_STUDENT_ID)
const data = ref(null)
const error = ref('')

function messageOf(err) {
  const code = err?.response?.status
  if (code === 403) return '权限不足（403）：当前角色无权访问学情看板'
  if (code === 401) return '登录已失效，请重新登录'
  const detail = err?.response?.data?.detail
  return `${code ? `（${code}）` : ''}${detail || err?.message || '未知错误'}`
}

async function load() {
  error.value = ''
  try {
    data.value = await getAnalytics(sid.value)
  } catch (err) {
    // 403/500 曾因未捕获变 unhandled rejection，页面保持空态把权限错误伪装成没数据
    data.value = null
    error.value = `加载失败：${messageOf(err)}`
  }
}
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
.err { color: #c0392b; background: #fff1f0; border: 1px solid #ffd6d3; padding: 10px 12px; border-radius: 10px; font-size: 13px; }
</style>
