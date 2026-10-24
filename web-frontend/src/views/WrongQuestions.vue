<template>
  <div>
    <h2>错题本</h2>
    <div class="filters">
      <input v-model="sid" placeholder="学生ID" />
      <button @click="load">查询</button>
    </div>

    <div v-if="list.length" class="list">
      <div v-for="w in list" :key="w.id" class="item">
        <p class="q">{{ w.question }}</p>
        <p class="meta">
          正确答案：<b>{{ w.correct_answer }}</b>
          <span v-if="w.knowledge_point">· 知识点：{{ w.knowledge_point }}</span>
        </p>

        <!-- S5：步骤级归因 + 该步正确做法 -->
        <div v-if="w.error_type_label" class="attr">
          <span class="tag">错在第 {{ w.wrong_step_no || '—' }} 步</span>
          <span class="tag type">{{ w.error_type_label }}</span>
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
        <p v-if="w.advice" class="advice">建议：{{ w.advice }}</p>
      </div>
    </div>
    <p v-else class="hint">点「查询」查看该学生的错题（含步骤级错因与正确做法）。</p>
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
h2 { font-size: 18px; margin: 0 0 12px; }
.filters { display: flex; gap: 8px; margin-bottom: 12px; }
input { padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.item { background: #fff; padding: 12px 14px; border: 1px solid #ebeef5; border-radius: 8px; margin-bottom: 10px; }
.q { margin: 0 0 6px; font-size: 14px; color: #303133; }
.meta { margin: 0 0 8px; font-size: 13px; color: #606266; }
.attr { margin-bottom: 6px; }
.tag { background: #e0edff; color: #1d4ed8; border-radius: 6px; padding: 1px 6px; font-size: 11px; margin-right: 6px; }
.tag.type { background: #fee2e2; color: #b91c1c; }
.step { font-size: 12px; color: #909399; }
.corr { background: #f0f9eb; color: #3a6b35; padding: 8px 10px; border-radius: 6px; font-size: 13px; margin-bottom: 6px; }
.exp { font-size: 13px; color: #606266; margin-bottom: 6px; }
.kp-card { font-size: 13px; color: #606266; margin-bottom: 6px; }
.kp-card summary { cursor: pointer; color: #409eff; }
.advice { font-size: 12px; color: #909399; margin: 6px 0 0; }
.hint { font-size: 13px; color: #909399; }
</style>
