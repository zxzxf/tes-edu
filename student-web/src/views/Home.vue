<template>
  <div class="home">
    <header class="topbar">
      <div class="brand"><span class="mark">T</span> Tes Edu · 学生端</div>
      <div class="user">
        <span class="greet">你好，{{ auth.displayName }} 👋</span>
        <button class="logout" @click="onLogout">退出</button>
      </div>
    </header>

    <main class="content">
      <h1 class="welcome">我的学习舱</h1>
      <p class="sub">基座已就绪，更多能力将在此陆续点亮。</p>

      <section class="grid">
        <article class="card link" v-if="feature.isOn('project')" @click="goProjects">
          <div class="ic">📁</div>
          <h3>我的项目</h3>
          <p>自学 / 专题工作区</p>
          <span class="tag">进入 →</span>
        </article>
        <article class="card">
          <div class="ic">🧠</div>
          <h3>知识图谱</h3>
          <p>掌握度与薄弱点可视化（T-B5）</p>
          <span class="tag">敬请期待</span>
        </article>
        <article class="card">
          <div class="ic">❌</div>
          <h3>错题本</h3>
          <p>归因级错题复盘</p>
          <span class="tag">敬请期待</span>
        </article>
        <article class="card">
          <div class="ic">🎯</div>
          <h3>精准练习</h3>
          <p>过程采集与即时反馈</p>
          <span class="tag">敬请期待</span>
        </article>
      </section>
    </main>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useFeatureStore } from '../stores/features'

const auth = useAuthStore()
const router = useRouter()
const feature = useFeatureStore()

function onLogout() {
  auth.logout()
  router.push('/login')
}
function goProjects() {
  router.push('/projects')
}
</script>

<style scoped>
.home { min-height: 100vh; background: var(--bg); }
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 28px; background: var(--surface);
  box-shadow: 0 2px 12px rgba(255, 61, 129, .06);
}
.brand { display: flex; align-items: center; gap: 8px; font-weight: 800; }
.brand .mark {
  width: 30px; height: 30px; border-radius: 8px;
  background: var(--brand-grad); color: #fff;
  display: grid; place-items: center; font-weight: 900;
}
.user { display: flex; align-items: center; gap: 14px; }
.greet { color: var(--ink-2); font-size: 14px; }
.logout {
  border: 1.5px solid var(--line-strong); background: #fff; color: var(--ink-2);
  border-radius: 10px; padding: 7px 14px; cursor: pointer; font-weight: 600;
}
.logout:hover { border-color: var(--brand-2); color: var(--brand-2); }

.content { max-width: 980px; margin: 0 auto; padding: 40px 24px; }
.welcome { font-size: 30px; font-weight: 900; }
.sub { color: var(--muted); margin: 8px 0 28px; }

.grid {
  display: grid; gap: 18px;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
}
.card {
  background: var(--surface); border-radius: var(--r-lg);
  padding: 22px; box-shadow: 0 8px 24px rgba(255, 61, 129, .08);
  transition: transform .15s, box-shadow .15s;
}
.card:hover { transform: translateY(-3px); box-shadow: 0 14px 32px rgba(255, 61, 129, .14); }
.card.link { cursor: pointer; }
.ic {
  width: 46px; height: 46px; border-radius: 12px;
  background: var(--brand-grad-soft); display: grid; place-items: center;
  font-size: 22px; margin-bottom: 14px;
}
.card h3 { font-size: 17px; }
.card p { color: var(--muted); font-size: 13px; margin: 6px 0 12px; line-height: 1.5; }
.tag {
  display: inline-block; font-size: 12px; font-weight: 700;
  color: var(--brand-2); background: var(--brand-grad-soft);
  border-radius: 999px; padding: 4px 12px;
}
</style>
