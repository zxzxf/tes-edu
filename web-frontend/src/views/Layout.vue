<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo">Tes Edu</div>
      <nav>
        <router-link to="/dashboard">概览</router-link>
        <router-link to="/knowledge">教研内容管理</router-link>
        <router-link to="/analytics">学情看板</router-link>
        <router-link to="/error-distribution">班级错因分布</router-link>
        <router-link to="/wrong-questions">错题本</router-link>
        <router-link to="/exam">自动组卷</router-link>
        <router-link to="/grading">教师批改</router-link>
        <router-link to="/reports">家长周报</router-link>
        <router-link to="/compliance">合规审核</router-link>
        <router-link to="/practice">在线练习（过程采集）</router-link>
      </nav>
    </aside>
    <div class="main">
      <header class="topbar">
        <span>{{ currentTitle }}</span>
        <span class="user">{{ user?.username }}（{{ user?.role }}）<button @click="onLogout">退出</button></span>
      </header>
      <section class="content">
        <router-view />
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const user = auth.user
const currentTitle = computed(() => route.meta.title || 'Tes Edu')

function onLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout { display: flex; height: 100vh; }
.sidebar { width: 200px; background: #1f2d3d; color: #fff; padding: 16px; }
.logo { font-weight: 700; margin-bottom: 20px; }
.sidebar nav { display: flex; flex-direction: column; gap: 4px; }
.sidebar a { color: #c0c4cc; padding: 8px 12px; border-radius: 6px; text-decoration: none; }
.sidebar a.router-link-active { background: #409eff; color: #fff; }
.main { flex: 1; display: flex; flex-direction: column; }
.topbar { height: 56px; background: #fff; border-bottom: 1px solid #ebeef5; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; }
.user button { margin-left: 8px; padding: 4px 10px; border: none; background: #f56c6c; color: #fff; border-radius: 4px; cursor: pointer; }
.content { padding: 20px; overflow: auto; background: #f5f7fa; flex: 1; }
</style>
