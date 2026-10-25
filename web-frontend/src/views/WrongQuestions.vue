<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">错题本</h2>

    <div class="tes-toolbar">
      <input v-model="sid" placeholder="学生ID" />
      <button class="tes-btn-primary" @click="load">查询</button>
    </div>

    <div v-if="list.length" class="list">
      <div v-for="w in list" :key="w.id" class="tes-card item">
        <p class="q">{{ w.question }}</p>
        <p class="meta">
          正确答案：<b>{{ w.correct_answer }}</b>
          <span v-if="w.knowledge_point">· 知识点：{{ w.knowledge_point }}</span>
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
    <p v-else class="tes-empty">点「查询」查看该学生的错题（含步骤级错因与正确做法）。</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { listWrongQuestions } from '../api/admin'
const sid = ref('student_001')
const list = ref([])
async function load() { list.value = await listWrongQuestions(sid.value) }
</script>

<style scoped>
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
