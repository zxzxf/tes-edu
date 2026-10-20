<template>
  <div>
    <h2>教师批改</h2>
    <input v-model="sid" placeholder="学生ID" />
    <textarea v-model="answer" placeholder="作答内容"></textarea>
    <button @click="submit">提交批改</button>
    <p v-if="result" class="ok">提交成功，作答 ID：{{ result.id }}</p>
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
input, textarea { display: block; width: 100%; max-width: 480px; padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; margin-bottom: 8px; }
textarea { height: 100px; resize: vertical; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.ok { color: #67c23a; }
</style>
