<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">教师批改</h2>

    <div class="tes-card form">
      <label class="field">
        <span>学生 ID</span>
        <input v-model="sid" placeholder="student_001" />
      </label>
      <label class="field">
        <span>作答内容</span>
        <textarea v-model="answer" placeholder="粘贴学生作答…"></textarea>
      </label>
      <button class="tes-btn-primary" @click="submit">提交批改</button>
      <p v-if="result" class="ok">✅ 提交成功，作答 ID：{{ result.id }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { submitGrading } from '../api/admin'
const sid = ref('student_001')
const answer = ref('')
const result = ref(null)
async function submit() {
  result.value = await submitGrading({ student_id: sid.value, answer: answer.value })
}
</script>

<style scoped>
.form { max-width: 560px; }
.field { display: block; margin-bottom: 16px; }
.field span { display: block; font-size: 13px; font-weight: 600; margin-bottom: 7px; color: var(--ink-2); }
.ok { color: var(--success); font-weight: 600; margin: 4px 0 0; }
</style>
