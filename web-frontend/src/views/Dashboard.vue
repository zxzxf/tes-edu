<template>
  <div>
    <h2>概览</h2>
    <p>当前用户：{{ user?.username }}（{{ user?.role }}）</p>
    <div class="stats">
      <div class="stat"><div class="num">{{ stats.knowledge }}</div><div class="lbl">知识点</div></div>
      <div class="stat"><div class="num">{{ stats.wrong }}</div><div class="lbl">待关注错题</div></div>
      <div class="stat"><div class="num">{{ stats.pending }}</div><div class="lbl">待审内容</div></div>
    </div>
    <ul class="cards">
      <li>教研内容管理</li>
      <li>学情看板</li>
      <li>错题本</li>
      <li>自动组卷</li>
      <li>教师批改</li>
      <li>家长周报</li>
      <li>合规审核</li>
    </ul>
    <p class="muted">提示：当前后端为骨架占位，页面数据来自内存示例；接入 PostgreSQL 后展示真实数据。</p>
  </div>
</template>

<script setup>
import { reactive, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { listKnowledge, listWrongQuestions, listCompliance } from '../api/admin'

const auth = useAuthStore()
const user = auth.user
const stats = reactive({ knowledge: 0, wrong: 0, pending: 0 })

onMounted(async () => {
  try {
    const [k, w, c] = await Promise.all([
      listKnowledge(),
      listWrongQuestions('student_001'),
      listCompliance('pending'),
    ])
    stats.knowledge = k.length
    stats.wrong = w.length
    stats.pending = c.length
  } catch (e) {
    // 演示环境下忽略统计失败
  }
})
</script>

<style scoped>
.stats { display: flex; gap: 16px; margin: 12px 0; }
.stat { background: #ecf5ff; border: 1px solid #d9ecff; border-radius: 10px; padding: 16px 24px; text-align: center; }
.num { font-size: 28px; font-weight: 700; color: #409eff; }
.lbl { color: #606266; font-size: 13px; margin-top: 4px; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; padding: 0; list-style: none; }
.cards li { background: #fff; border: 1px solid #ebeef5; border-radius: 8px; padding: 16px 20px; min-width: 120px; }
.muted { color: #909399; font-size: 13px; }
</style>
