<template>
  <div class="practice">
    <h2>在线练习 · 过程采集</h2>
    <p class="tip">
      解答题请写出解题过程后提交，系统会定位<strong>首个出错步骤</strong>并给出错因类型；
      只写结论无法归因，会被要求补过程。
    </p>

    <div class="field">
      <label>题目</label>
      <textarea v-model="question" rows="3" placeholder="粘贴题干…" />
    </div>

    <div class="field">
      <label>我的作答（解答题请写出过程）</label>
      <textarea v-model="process" rows="8"
        placeholder="例如：由 A+B+C=π 且 A+B=3C，得 4C=π，所以 C=π/4…" />
      <span class="counter">{{ process.trim().length }} 字</span>
    </div>

    <div class="actions">
      <select v-model="subject">
        <option value="数学">数学</option>
        <option value="物理">物理</option>
      </select>
      <button :disabled="!question.trim() || loading" @click="submit">
        {{ loading ? '提交中…' : (process.trim() ? '提交作答并判分' : '只求讲解') }}
      </button>
      <label class="upload">
        拍照上传
        <input type="file" accept="image/*" @change="onImage" hidden />
      </label>
    </div>

    <p v-if="error" class="err">{{ error }}</p>

    <div v-if="result" class="cards">
      <div class="card">
        <h3>参考答案</h3>
        <p class="pre">{{ result.answer }}</p>
        <ol v-if="result.steps && result.steps.length">
          <li v-for="(s, i) in result.steps" :key="i">{{ s }}</li>
        </ol>
      </div>

      <div class="card" v-if="result.judgement">
        <h3>判分结果（步骤级归因）</h3>
        <p v-if="result.judgement.need_process" class="need">
          需要先写出解题过程才能判分：{{ result.judgement.comment }}
        </p>
        <template v-else>
          <p>
            <strong>结论：</strong>
            <span :class="result.judgement.correct ? 'ok' : 'bad'">
              {{ result.judgement.correct === true ? '正确'
                 : result.judgement.correct === false ? '错误' : '未判分' }}
            </span>
          </p>
          <p v-if="result.judgement.first_wrong_step">
            <strong>首个出错步骤：</strong>第 {{ result.judgement.first_wrong_step }} 步
          </p>
          <p v-if="result.judgement.error_type">
            <strong>错因类型：</strong>
            {{ label(result.judgement.error_type) }}（{{ result.judgement.error_type }}）
          </p>
          <p v-if="result.judgement.evidence"><strong>证据：</strong>{{ result.judgement.evidence }}</p>
          <p v-if="result.judgement.comment"><strong>点评：</strong>{{ result.judgement.comment }}</p>
          <p v-if="result.judgement.correction"><strong>纠正：</strong>{{ result.judgement.correction }}</p>
          <p class="src">归因来源：{{ result.judgement.attribution_source }}</p>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { solveWithProcess, solveImage } from '../api/agent'

const ERROR_TYPE_LABEL = {
  concept: '概念理解',
  formula: '公式误用',
  case: '分类讨论遗漏',
  calc: '计算运算',
  read: '审题漏条件',
  logic: '逻辑跳步',
  strategy: '方法策略',
  unknown: '待人工复核'
}
const label = (t) => ERROR_TYPE_LABEL[t] || t

const question = ref('')
const process = ref('')
const subject = ref('数学')
const loading = ref(false)
const error = ref('')
const result = ref(null)

async function submit() {
  loading.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await solveWithProcess({
      question: question.value,
      subject: subject.value,
      mode: 'explain',
      // 空则不传：后端返回 need_process=true 要求补过程，不判分
      student_answer: process.value.trim() || undefined
    })
  } catch (e) {
    error.value = '请求失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

async function onImage(e) {
  const file = e.target.files?.[0]
  if (!file) return
  loading.value = true
  error.value = ''
  try {
    const res = await solveImage(file)
    question.value = res.recognized_text || question.value
    result.value = res
  } catch (err) {
    error.value = '识别失败：' + (err.response?.data?.detail || err.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.practice { max-width: 900px; }
h2 { margin: 0 0 8px; }
.tip { color: #606266; font-size: 13px; margin: 0 0 16px; }
.field { margin-bottom: 12px; }
.field label { display: block; margin-bottom: 6px; font-weight: 600; font-size: 14px; }
textarea { width: 100%; padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; font-size: 14px; }
.counter { font-size: 12px; color: #909399; }
.actions { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
select, button, .upload { padding: 8px 14px; border-radius: 6px; font-size: 14px; }
button { background: #409eff; color: #fff; border: none; cursor: pointer; }
button:disabled { background: #c0c4cc; cursor: not-allowed; }
.upload { background: #67c23a; color: #fff; cursor: pointer; }
.err { color: #f56c6c; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.card { background: #fff; padding: 14px; border: 1px solid #ebeef5; border-radius: 8px; }
.card h3 { margin: 0 0 8px; font-size: 15px; }
.pre { white-space: pre-wrap; font-size: 14px; }
.ok { color: #67c23a; }
.bad { color: #f56c6c; }
.need { color: #e6a23c; }
.src { font-size: 12px; color: #909399; margin-top: 8px; }
</style>
