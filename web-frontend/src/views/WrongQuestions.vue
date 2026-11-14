<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">错题本</h2>

    <div class="tes-toolbar">
      <input v-model="sid" placeholder="学生ID" />
      <button class="tes-btn-primary" @click="load">查询</button>
      <span v-if="loaded && !error" class="hint">共 {{ list.length }} 条</span>
    </div>

    <p v-if="error" class="err">{{ error }}</p>

    <div v-if="list.length" class="list">
      <div v-for="w in list" :key="w.id" class="tes-card item">
        <p class="q">{{ w.question }}</p>
        <p class="meta">
          正确答案：<b>{{ w.correct_answer || '—' }}</b>
          <span v-if="w.knowledge_point">· 知识点：{{ w.knowledge_point }}</span>
          <span v-if="w.question_type">· 题型：{{ qtypeLabel(w.question_type) }}</span>
        </p>

        <!-- S5：步骤级归因 + 该步正确做法 -->
        <div v-if="w.error_type_label" class="attr">
          <span class="tes-pill danger">错在第 {{ w.wrong_step_no || '—' }} 步</span>
          <span class="tes-pill warn">{{ w.error_type_label }}</span>
          <span v-if="w.step_text" class="step">（{{ w.step_text }}）</span>
        </div>
        <div v-if="w.correction" class="corr">
          <b>{{ w.correction_matched ? '针对这个错因，这一步应该这么做：' : '这一步的参考做法：' }}</b>{{ w.correction }}
        </div>
        <div v-if="w.explanation" class="exp">{{ w.explanation }}</div>
        <details v-if="w.understanding || w.memory" class="kp-card">
          <summary>知识点卡{{ w.kp_name ? '：' + w.kp_name : '' }}</summary>
          <p v-if="w.understanding"><b>理解：</b>{{ w.understanding }}</p>
          <p v-if="w.memory"><b>记忆抓手：</b>{{ w.memory }}</p>
        </details>
        <p v-if="w.advice" class="advice">💡 建议：{{ w.advice }}</p>
      </div>
    </div>
    <p v-else-if="loaded && !error" class="tes-empty">该学生暂无错题。</p>
    <p v-else-if="!error" class="tes-empty">点「查询」查看该学生的错题（含步骤级错因与正确做法）。</p>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listWrongQuestions, DEMO_STUDENT_ID } from '../api/admin'

const sid = ref(DEMO_STUDENT_ID)
const list = ref([])
const error = ref('')
const loaded = ref(false)

const QTYPE_LABEL = { choice: '选择题', blank: '填空题', subjective: '解答题' }
function qtypeLabel(t) { return QTYPE_LABEL[t] || t }

function messageOf(err) {
  const code = err?.response?.status
  const detail = err?.response?.data?.detail
  if (code === 403) return '权限不足（403）：当前角色无权查看错题本'
  if (code === 401) return '登录已失效，请重新登录'
  return `加载失败${code ? `（${code}）` : ''}：${detail || err?.message || '未知错误'}`
}

async function load() {
  error.value = ''
  loaded.value = false
  try {
    list.value = await listWrongQuestions(sid.value.trim() || DEMO_STUDENT_ID)
  } catch (err) {
    // 关键修复：client.js 只兜 401，403/500 曾变成 unhandled rejection，
    // 组件保持空态，把权限不足 / 服务端异常伪装成「暂无数据」。
    list.value = []
    error.value = messageOf(err)
  } finally {
    loaded.value = true
  }
}

onMounted(load)
</script>

<style scoped>
.err { background: #fff1f0; color: #c0392b; padding: 10px 12px; border-radius: 12px; font-size: 13px; margin-bottom: 12px; }
.hint { font-size: 13px; color: var(--muted, #888); }
.item { margin-bottom: 14px; }
.q { margin: 0 0 6px; font-size: 15px; font-weight: 600; color: var(--ink); }
.meta { margin: 0 0 10px; font-size: 13px; color: var(--ink-2); }
.meta b { color: var(--success); }
.attr { margin-bottom: 8px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.step { font-size: 12px; color: var(--muted); }
.corr { background: #eafaf3; color: #1f8a5b; padding: 10px 12px; border-radius: 12px; font-size: 13px; margin-bottom: 8px; }
.corr b { color: #157a4c; }
.exp { font-size: 13px; color: var(--ink-2); margin-bottom: 8px; }
.kp-card { font-size: 13px; color: var(--ink-2); margin-bottom: 8px; }
.kp-card summary { cursor: pointer; color: var(--brand-2); font-weight: 600; }
.kp-card p { margin: 8px 0 0; }
.advice { font-size: 13px; color: var(--accent); margin: 8px 0 0; }
</style>
