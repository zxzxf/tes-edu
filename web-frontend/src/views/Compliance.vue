<template>
  <div>
    <h2>合规审核</h2>
    <button @click="load">加载待审</button>
    <ul v-if="list.length">
      <li v-for="c in list" :key="c.id">{{ c.type }}：{{ c.content }} [{{ c.status }}]</li>
    </ul>
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
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
li { background: #fff; padding: 10px 12px; border: 1px solid #ebeef5; border-radius: 6px; margin-top: 8px; list-style: none; }
</style>
