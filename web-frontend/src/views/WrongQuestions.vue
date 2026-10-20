<template>
  <div>
    <h2>错题本</h2>
    <input v-model="sid" placeholder="学生ID" />
    <button @click="load">查询</button>
    <ul v-if="list.length">
      <li v-for="w in list" :key="w.id">
        {{ w.question }} → 正确答案：{{ w.correct_answer }}（知识点：{{ w.knowledge_point }}）
      </li>
    </ul>
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
input { padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; margin-right: 8px; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
li { background: #fff; padding: 10px 12px; border: 1px solid #ebeef5; border-radius: 6px; margin-top: 8px; }
</style>
