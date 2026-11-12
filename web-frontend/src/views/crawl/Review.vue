<template>
  <div class="cw-page">
    <h2 class="cw-title">知识点审核与发布</h2>
    <p class="cw-hint">
      采集/解析产生的知识点一律 <code>draft</code>，未审核不进学生端图谱。
      审核通过后写入 <code>kb_knowledge_points</code>；可随时退回重新编辑。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head">
        <b>知识点候选（M5-3 / M5-4）</b>
        <span class="cw-grow"></span>
        <select v-model="status" aria-label="知识点状态" @change="load">
          <option value="draft">待审核</option>
          <option value="published">已发布</option>
        </select>
        <button class="ghost" :disabled="busy" @click="load">刷新</button>
      </div>
      <p class="cw-hint">当前视图：<b>{{ status === 'draft' ? '待审核（draft）' : '已发布（published）' }}</b></p>
      <table v-if="rows.length" class="tes-table">
        <thead><tr><th>code</th><th>名称</th><th>父节点</th><th>来源</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="k in rows" :key="k.code">
            <td class="cw-mono">{{ k.code }}</td>
            <td>{{ k.name }}</td>
            <td class="cw-mono">{{ k.parent_code || '—' }}</td>
            <td>{{ k.source }}</td>
            <td>
              <button v-if="k.status === 'draft'" class="ghost" :disabled="busy" @click="review(k, 'published')">通过</button>
              <button v-else class="ghost" :disabled="busy" @click="review(k, 'draft')">退回</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="tes-empty">没有知识点候选。</p>
    </section>

    <div class="cw-links">
      <router-link class="cw-link" to="/course-intake/parse">从教材解析导入 →</router-link>
      <router-link class="cw-link" to="/course-intake/workbench">返回工作台</router-link>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useCrawlRequest } from '../../composables/useCrawlRequest'
import { listKpNodes, reviewKpNode } from '../../api/admin'

const { busy, error, msg, guard } = useCrawlRequest()
const rows = ref([])
const status = ref('draft')

async function load() {
  await guard(async () => { rows.value = await listKpNodes(status.value) })
}
async function review(k, next) {
  await guard(async () => {
    await reviewKpNode(k.code, next)
    rows.value = await listKpNodes(status.value)
    msg.value = next === 'published' ? `已发布 ${k.name}` : `已退回 ${k.name} 为 draft`
  })
}
onMounted(load)
</script>
