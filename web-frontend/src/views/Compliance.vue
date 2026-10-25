<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">合规审核</h2>

    <div class="tes-toolbar">
      <button class="tes-btn-primary" @click="load">加载待审</button>
    </div>

    <ul v-if="list.length" class="list">
      <li v-for="c in list" :key="c.id" class="tes-card">
        <span class="pill" :class="c.status === 'pending' ? 'warn' : 'info'">{{ c.status }}</span>
        <b>{{ c.type }}</b>：{{ c.content }}
      </li>
    </ul>
    <p v-else class="tes-empty">暂无待审核内容。</p>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listCompliance } from '../api/admin'
const list = ref([])
async function load() { list.value = await listCompliance('pending') }
onMounted(load)
</script>

<style scoped>
.list { list-style: none; padding: 0; margin: 0; }
.list li { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.pill {
  flex-shrink: 0; padding: 2px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 700;
}
.pill.warn { background: #fff3da; color: #c77700; }
.pill.info { background: #eef4ff; color: var(--accent); }
</style>
