<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">自动组卷</h2>

    <div class="tes-toolbar">
      <input v-model="title" placeholder="试卷标题" />
      <input v-model="kps" placeholder="知识点，逗号分隔" />
      <button class="tes-btn-primary" @click="gen">生成</button>
    </div>

    <ul v-if="paper" class="qs">
      <li v-for="(q, i) in paper.questions" :key="i" class="tes-card">
        <span class="idx">{{ i + 1 }}</span>{{ q }}
      </li>
    </ul>
    <p v-else class="tes-empty">填写标题与知识点，点「生成」自动组卷。</p>
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
.qs { list-style: none; padding: 0; margin: 0; }
.qs li { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 12px; line-height: 1.6; }
.idx {
  flex-shrink: 0; width: 26px; height: 26px; border-radius: 8px;
  display: grid; place-items: center; font-size: 13px; font-weight: 800;
  background: var(--brand-grad-soft); color: var(--brand-2);
}
</style>
