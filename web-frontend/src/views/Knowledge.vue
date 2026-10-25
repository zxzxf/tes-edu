<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">教研内容管理</h2>

    <div class="tes-toolbar">
      <button class="tes-btn-primary" @click="load">加载知识点</button>
    </div>

    <div v-if="list.length" class="tes-table-card">
      <table class="tes-table">
        <thead><tr><th>学科</th><th>年级</th><th>标题</th><th>标签</th></tr></thead>
        <tbody>
          <tr v-for="k in list" :key="k.id">
            <td>{{ k.subject }}</td><td>{{ k.grade }}</td><td>{{ k.title }}</td><td>{{ k.tags?.join('、') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="tes-empty">点击「加载知识点」查看教研内容库。</p>
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
/* 已接入全局设计系统 */
</style>
