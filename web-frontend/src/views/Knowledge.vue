<template>
  <div>
    <h2>教研内容管理</h2>
    <button @click="load">加载知识点</button>
    <table v-if="list.length">
      <thead><tr><th>学科</th><th>年级</th><th>标题</th><th>标签</th></tr></thead>
      <tbody>
        <tr v-for="k in list" :key="k.id">
          <td>{{ k.subject }}</td><td>{{ k.grade }}</td><td>{{ k.title }}</td><td>{{ k.tags?.join('、') }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listKnowledge } from '../api/admin'
const list = ref([])
async function load() { list.value = await listKnowledge() }
onMounted(load)
</script>

<style scoped>
table { width: 100%; border-collapse: collapse; margin-top: 12px; background: #fff; }
th, td { border: 1px solid #ebeef5; padding: 8px 12px; text-align: left; }
button { padding: 8px 16px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
</style>
