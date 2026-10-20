<template>
  <div>
    <h2>自动组卷</h2>
    <input v-model="title" placeholder="试卷标题" />
    <input v-model="kps" placeholder="知识点，逗号分隔" />
    <button @click="gen">生成</button>
    <ul v-if="paper" class="qs">
      <li v-for="(q, i) in paper.questions" :key="i">{{ q }}</li>
    </ul>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { generateExam } from '../api/admin'
const title = ref('周测卷')
const kps = ref('二次函数,被动语态')
const paper = ref(null)
async function gen() {
  paper.value = await generateExam({
    title: title.value,
    knowledge_points: kps.value.split(',').map(s => s.trim()).filter(Boolean),
    difficulty: 3
  })
}
</script>

<style scoped>
input { padding: 8px; border: 1px solid #dcdfe6; border-radius: 6px; margin-right: 8px; margin-bottom: 8px; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.qs { list-style: none; padding: 0; }
.qs li { background: #fff; padding: 10px 12px; border: 1px solid #ebeef5; border-radius: 6px; margin-top: 8px; }
</style>
